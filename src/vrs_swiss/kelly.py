"""
Kelly criterion: what fraction of a bankroll to stake on a bet, given your
probability of winning and the bookmaker's odds.

    b  = decimal odds - 1          (net profit per 1 unit staked)
    f* = (b*p - (1-p)) / b  =  p - (1-p)/b

If f* <= 0 the bet has no edge and Kelly says don't bet.
Full Kelly is very aggressive when p is uncertain; a fraction of it (e.g. 0.25 = quarter
Kelly) is commonly used instead. Use --fraction for that.

Single bet:
    python -m vrs_swiss.kelly 0.853 1.25                      # p = 85.3%, decimal odds 1.25
    python -m vrs_swiss.kelly 0.40 +150 --bankroll 1000       # American odds
    python -m vrs_swiss.kelly 0.40 3/2 --fraction 0.25        # fractional odds, quarter Kelly

Every team at once, using the qualification chances from montecarlo.py:
    python -m vrs_swiss.kelly --results output/montecarlo_results.csv --odds data/odds.csv --bankroll 1000
odds.csv has columns team,qualify,not_qualify (odds in any format above; either can be
empty). Both bets are checked for every team. Teams without odds are skipped.

Note: Kelly stakes are computed per bet independently. Several bets on the same stage are
correlated (only 8 teams can qualify), so the combined stake is not exactly optimal; using
a fraction of Kelly helps keep that in check.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from fractions import Fraction


def to_decimal_odds(odds: str | float) -> float:
    """Convert decimal (2.5), American (+150 / -200) or fractional (3/2) odds to decimal."""
    if isinstance(odds, (int, float)):
        d = float(odds)
    else:
        s = odds.strip()
        if "/" in s:
            d = 1 + float(Fraction(s))
        elif s.startswith(("+", "-")):
            a = float(s)
            if abs(a) < 100:
                raise ValueError(f"American odds must be <= -100 or >= +100, got {s}")
            d = 1 + (a / 100 if a > 0 else 100 / -a)
        else:
            d = float(s)
    if d <= 1:
        raise ValueError(f"decimal odds must be greater than 1, got {d}")
    return d


@dataclass
class KellyResult:
    probability: float
    decimal_odds: float
    implied_probability: float   # 1 / odds (includes the bookmaker's margin)
    edge: float                  # expected profit per 1 unit staked = p*odds - 1
    kelly_fraction: float        # full Kelly, never below 0
    stake_fraction: float        # kelly_fraction * fraction

    def stake(self, bankroll: float) -> float:
        return bankroll * self.stake_fraction


def kelly(probability: float, odds: str | float, fraction: float = 1.0) -> KellyResult:
    """
    probability: your chance of winning the bet (0-1)
    odds:        bookmaker odds (decimal, American or fractional)
    fraction:    share of full Kelly to bet (1 = full, 0.5 = half, 0.25 = quarter)
    """
    if not 0 <= probability <= 1:
        raise ValueError(f"probability must be between 0 and 1, got {probability}")
    if not 0 < fraction <= 1:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    d = to_decimal_odds(odds)
    b = d - 1
    f = max(0.0, probability - (1 - probability) / b)
    return KellyResult(
        probability=probability,
        decimal_odds=d,
        implied_probability=1 / d,
        edge=probability * d - 1,
        kelly_fraction=f,
        stake_fraction=f * fraction,
    )


# ---- CLI ---------------------------------------------------------------------------
def _load_probabilities(path: str) -> dict[str, float]:
    with open(path, newline="") as f:
        return {r["Team"]: float(r["Qualify %"]) / 100 for r in csv.DictReader(f)}


def load_odds(path: str) -> dict[str, dict[str, str]]:
    """
    Returns {team: {"Qualify": odds, "Not qualify": odds}}.
    Columns: team, qualify, not_qualify (either odds column may be empty).
    A single 'odds' column is also accepted and treated as 'qualify'.
    """
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            markets = {}
            q = (r.get("qualify") or r.get("odds") or "").strip()
            nq = (r.get("not_qualify") or "").strip()
            if q:
                markets["Qualify"] = q
            if nq:
                markets["Not qualify"] = nq
            if markets:
                out[r["team"].strip()] = markets
    return out


def _print_single(r: KellyResult, bankroll: float | None) -> None:
    print(f"Win probability:      {r.probability * 100:.1f}%")
    print(f"Decimal odds:         {r.decimal_odds:.3f}")
    print(f"Implied probability:  {r.implied_probability * 100:.1f}%")
    print(f"Edge (EV per 1 unit): {r.edge * 100:+.1f}%")
    print(f"Full Kelly:           {r.kelly_fraction * 100:.2f}% of bankroll")
    print(f"Stake:                {r.stake_fraction * 100:.2f}% of bankroll"
          + (f" = {r.stake(bankroll):.2f}" if bankroll is not None else ""))
    if r.kelly_fraction == 0:
        print("No edge at these odds: Kelly says don't bet.")


def main() -> None:
    p = argparse.ArgumentParser(description="Kelly criterion stake calculator.")
    p.add_argument("probability", nargs="?", type=float, help="Win probability, 0-1 (single bet)")
    p.add_argument("odds", nargs="?", help="Odds: decimal 2.5, American +150/-200, or fractional 3/2")
    p.add_argument("--fraction", type=float, default=1.0, help="Share of full Kelly to bet (default 1.0)")
    p.add_argument("--bankroll", type=float, default=None, help="Bankroll, to show stakes in money")
    p.add_argument("--results", help="montecarlo_results.csv (uses its 'Qualify %%' column)")
    p.add_argument("--odds", dest="odds_csv", help="CSV with team,qualify,not_qualify odds")
    args = p.parse_args()

    if args.results or args.odds_csv:
        if not (args.results and args.odds_csv):
            p.error("--results and --odds must be used together")
        probs = _load_probabilities(args.results)
        odds = load_odds(args.odds_csv)
        unknown = sorted(set(odds) - set(probs))
        if unknown:
            p.error(f"teams in odds file not found in results: {', '.join(unknown)}")

        money = args.bankroll is not None
        print("| Team | Bet | Model % | Odds | Implied % | Edge | Full Kelly | Stake %"
              + (" | Stake |" if money else " |"))
        print("|---|---|---|---|---|---|---|---" + ("|---|" if money else "|"))
        total = 0.0
        for team, prob in sorted(probs.items(), key=lambda kv: -kv[1]):
            for market, o in odds.get(team, {}).items():
                p_win = prob if market == "Qualify" else 1 - prob
                r = kelly(p_win, o, args.fraction)
                total += r.stake_fraction
                print(f"| {team} | {market} | {p_win * 100:.1f} | {r.decimal_odds:.2f}"
                      f" | {r.implied_probability * 100:.1f} | {r.edge * 100:+.1f}%"
                      f" | {r.kelly_fraction * 100:.2f}% | {r.stake_fraction * 100:.2f}%"
                      + (f" | {r.stake(args.bankroll):.2f} |" if money else " |"))
        print(f"\nTotal stake: {total * 100:.2f}% of bankroll"
              + (f" = {total * args.bankroll:.2f}" if money else ""))
        return

    if args.probability is None or args.odds is None:
        p.error("give a probability and odds, or use --results with --odds")
    _print_single(kelly(args.probability, args.odds, args.fraction), args.bankroll)


if __name__ == "__main__":
    main()
