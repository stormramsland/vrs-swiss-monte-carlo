"""
Python port of the per-match VRS update (glicko.js + ranking_context.js from Valve's
counter-strike_regional_standings repository).

How VRS works in this repo:
  1. Every roster gets a *seed* rating between 400 and 2000 from its factors
     (Bounty Offered, Bounty Collected, Opponent Network, LAN Wins) - see team.js.
  2. All matches in the 6-month window are then replayed oldest -> newest through a
     Glicko update with a FIXED rating deviation of 75 (which makes it Elo-like).
     Each match is weighted by its "information content" = the Age Weight.

This module implements step 2, i.e. "given both teams' ratings before the match,
what are their ratings after it?".

Note: an official standings update recomputes everything from scratch (seeds can move
when new results change a team's factors, and old matches fade out of the window), so
this tells you the head-to-head adjustment for one match, not the next published
standings exactly.

Usage as a library:
    from cs2_vrs_swiss.vrs_match import update_ratings
    new_winner, new_loser = update_ratings(1900.0, 1850.0)

Usage from the command line:
    python -m cs2_vrs_swiss.vrs_match 1900 1850
    python -m cs2_vrs_swiss.vrs_match 1900 1850 --age-weight 0.8
    python -m cs2_vrs_swiss.vrs_match 1900 1850 --match-date 2026-09-05 --window-end 2026-11-02
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

# rank delta of 400 points = ~90% chance of victory (glicko.js: Q)
Q = math.log(10) / 400
# constant for RD decay over time (unused when RD is fixed, kept for completeness)
C = 34.6
ONE_OVER_PI_SQUARED = 1 / (math.pi * math.pi)

# ranking.js: glicko.setFixedRD(75)  -> "glicko -> elo"
VRS_FIXED_RD = 75.0

# data_loader.js
DATA_WINDOW_SECONDS = 6 * 30 * 24 * 3600   # 6 months (180 days)
GRACE_PERIOD_SECONDS = 30 * 24 * 3600      # last month counts at full weight
TIME_DECAY_FACTOR = 1.0                    # ranking_context.js default (linear)


def remap_value_clamped(val, in_start, in_end, out_start, out_end):
    """util/remap_value_clamped.js"""
    interp = 0.5 if in_start == in_end else (val - in_start) / (in_end - in_start)
    clamped = max(min(interp, 1.0), 0.0)
    return clamped * out_end + (1 - clamped) * out_start


# --------------------------------------------------------------------------------------
# Glicko (glicko.js)
# --------------------------------------------------------------------------------------
@dataclass
class GlickoTeam:
    rank: float
    rd: float
    _adj_rank: float = field(default=0.0, repr=False)
    _adj_rd_sq: float = field(default=0.0, repr=False)

    def decay_rd(self, glicko: "Glicko", t: float = 1.0) -> None:
        self.rd = glicko.clamp_rd(math.sqrt(self.rd * self.rd + C * C * t))

    def reset_pending_matches(self) -> None:
        self._adj_rank = 0.0
        self._adj_rd_sq = 0.0

    def add_pending_match(self, other: "GlickoTeam", score: float, info: float = 1.0) -> None:
        r, ro, rdo = self.rank, other.rank, other.rd

        g = 1 / math.sqrt(1 + (3 * Q * Q * rdo * rdo * ONE_OVER_PI_SQUARED))
        ev = 1 / (1 + math.pow(10, g * (r - ro) / (-400)))

        self._adj_rd_sq += g * g * ev * (1 - ev) * info * info
        self._adj_rank += g * (score - ev) * info

    def apply_pending_matches(self, glicko: "Glicko") -> None:
        rd = self.rd
        adjusted_rd_sq = 1 / (1 / (rd * rd) + Q * Q * self._adj_rd_sq)

        self.rank += Q * adjusted_rd_sq * self._adj_rank
        self.rd = glicko.clamp_rd(math.sqrt(adjusted_rd_sq))

        self.reset_pending_matches()


class Glicko:
    def __init__(self) -> None:
        self.starting_rank = 1500.0
        self.starting_rd = 350.0
        self.max_rd = 350.0
        self.min_rd = 35.0

    def set_fixed_rd(self, val: float) -> "Glicko":
        """Converts glicko to elo by ignoring the RD calculation."""
        self.starting_rd = self.max_rd = self.min_rd = val
        return self

    def clamp_rd(self, rd: float) -> float:
        return max(self.min_rd, min(self.max_rd, rd))

    def new_team(self, rank: float | None = None, rd: float | None = None) -> GlickoTeam:
        return GlickoTeam(
            rank=self.starting_rank if rank is None else rank,
            rd=self.starting_rd if rd is None else rd,
        )

    def incremental_match(self, winner: GlickoTeam, loser: GlickoTeam, information_content: float = 1.0) -> None:
        winner.add_pending_match(loser, 1.0, information_content)
        loser.add_pending_match(winner, 0.0, information_content)

    def finalize_matches(self, teams) -> None:
        for team in teams:
            team.apply_pending_matches(self)

    def single_match(self, winner: GlickoTeam, loser: GlickoTeam, information_content: float = 1.0) -> None:
        self.incremental_match(winner, loser, information_content)
        self.finalize_matches([winner, loser])


# --------------------------------------------------------------------------------------
# Age weight / information content (ranking_context.js + data_loader.js)
# --------------------------------------------------------------------------------------
def age_weight(match_time: float, window_end: float,
               data_window: float = DATA_WINDOW_SECONDS,
               grace_period: float = GRACE_PERIOD_SECONDS,
               time_decay_factor: float = TIME_DECAY_FACTOR) -> float:
    """
    Information content of a match, given unix timestamps.

    window_end is the time of the most recent match in the dataset (the standings date).
    Matches older than 6 months get 0, matches in the last month get 1, linear in between.
    """
    window_start = window_end - data_window
    modifier_end = max(window_start, window_end - grace_period)
    clamp = remap_value_clamped(match_time, window_start, modifier_end, 0.0, 1.0)
    return math.pow(clamp, time_decay_factor)


def expected_win_probability(rating_a: float, rating_b: float, rd_b: float = VRS_FIXED_RD) -> float:
    """Expected chance that A beats B (same formula used inside the update)."""
    g = 1 / math.sqrt(1 + (3 * Q * Q * rd_b * rd_b * ONE_OVER_PI_SQUARED))
    return 1 / (1 + math.pow(10, g * (rating_a - rating_b) / (-400)))


# --------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------
# Valve's evaluation of the model (expected vs observed win rate, in the readme of
# counter-strike_regional_standings) found a slope of 0.7986: the model is overconfident,
# favourites win less often than it predicts. Shrinking each probability towards 50% by
# that slope corrects for it while keeping P(A beats B) + P(B beats A) = 1.
CALIBRATION_SLOPE = 0.8

# The same check split by series format (HLTV 1-star matches, 2024-2026,
# https://github.com/stormramsland/vrs-win-rate-analysis) finds the model more overconfident
# than Valve's single slope suggests, most of all in best-of-one:
#   BO1 0.4717, BO3 0.6089  (BO5 0.86 from only 78 matches, too few to use).
# The Swiss simulation uses these per format by default.
CALIBRATION_BO1 = 0.47
CALIBRATION_BO3 = 0.61


def calibrate(p: float, slope: float = CALIBRATION_SLOPE) -> float:
    """50% + slope * (p - 50%). slope 1 = no change, 0 = every match a coin flip."""
    if not 0 <= slope <= 1:
        raise ValueError(f"calibration slope must be between 0 and 1, got {slope}")
    return 0.5 + slope * (p - 0.5)


def calibrated_win_probability(rating_a: float, rating_b: float,
                               slope: float = CALIBRATION_SLOPE) -> float:
    """Chance that A beats B, corrected for the model's overconfidence."""
    return calibrate(expected_win_probability(rating_a, rating_b), slope)


# --------------------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------------------
def update_ratings(winner_rating: float, loser_rating: float,
                   information_content: float = 1.0) -> tuple[float, float]:
    """
    Return (new_winner_rating, new_loser_rating) after one match.

    information_content is the match's Age Weight (1.0 for a match played within the
    last month of the window, which is the case for any match that "just ended").
    """
    glicko = Glicko().set_fixed_rd(VRS_FIXED_RD)
    winner = glicko.new_team(winner_rating)
    loser = glicko.new_team(loser_rating)
    glicko.single_match(winner, loser, information_content)
    return winner.rank, loser.rank


def _parse_date(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def main() -> None:
    p = argparse.ArgumentParser(description="Compute VRS ratings after a match.")
    p.add_argument("winner_rating", type=float, help="Winner's rating before the match")
    p.add_argument("loser_rating", type=float, help="Loser's rating before the match")
    p.add_argument("--age-weight", type=float, default=None,
                   help="Information content / Age Weight (0-1). Default 1.0")
    p.add_argument("--match-date", help="Match date YYYY-MM-DD (used with --window-end)")
    p.add_argument("--window-end", help="Standings date YYYY-MM-DD (used with --match-date)")
    args = p.parse_args()

    info = 1.0
    if args.age_weight is not None:
        info = args.age_weight
    elif args.match_date and args.window_end:
        info = age_weight(_parse_date(args.match_date), _parse_date(args.window_end))

    p_win = expected_win_probability(args.winner_rating, args.loser_rating)
    new_w, new_l = update_ratings(args.winner_rating, args.loser_rating, info)

    print(f"Age weight:            {info:.3f}")
    print(f"Winner expected win %: {p_win * 100:.1f}%")
    print(f"Winner: {args.winner_rating:8.1f} -> {new_w:8.1f}  ({new_w - args.winner_rating:+.2f})")
    print(f"Loser:  {args.loser_rating:8.1f} -> {new_l:8.1f}  ({new_l - args.loser_rating:+.2f})")


if __name__ == "__main__":
    main()
