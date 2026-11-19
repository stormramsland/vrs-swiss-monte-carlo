"""
16-team Swiss stage (3 wins advance, 3 losses eliminated), following these rules:

  Round 1: pairings are given (seeded from the Valve Global Standings).
  Round 2: groups by record: 1-0 ("High"), 0-1 ("Low").
  Round 3: 2-0 ("High"), 1-1 ("Mid"), 0-2 ("Low"). High winners advance, Low losers are out.
  Round 4: 2-1 ("High"), 1-2 ("Low"). High winners advance, Low losers are out.
  Round 5: 2-2. Winners advance, losers are out.
  In every round after the first, each team is drawn an opponent from its own group
  that it has not played yet.

Results can be entered by hand (SwissStage.record_result) or simulated with VRS
win probabilities, updating ratings after every match (vrs_match.update_ratings).

Command line (simulates the whole stage):
    python -m cs2_vrs_swiss.swiss data/vrs_points.csv
    python -m cs2_vrs_swiss.swiss data/vrs_points.csv --round1 data/round1.csv --seed 7
round1.csv has two columns (team1,team2), one row per match. Without it,
the hard-coded ROUND1 matchups below are used.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field

from .vrs_match import expected_win_probability, update_ratings

WINS_TO_ADVANCE = 3
LOSSES_TO_ELIMINATE = 3


@dataclass
class Team:
    name: str
    points: float
    wins: int = 0
    losses: int = 0
    opponents: set[str] = field(default_factory=set)

    @property
    def record(self) -> tuple[int, int]:
        return (self.wins, self.losses)

    @property
    def active(self) -> bool:
        return self.wins < WINS_TO_ADVANCE and self.losses < LOSSES_TO_ELIMINATE


@dataclass
class Match:
    round: int
    group: str
    team1: str
    team2: str
    winner: str | None = None


class SwissStage:
    def __init__(self, teams: dict[str, float], rng: random.Random | None = None,
                 update_points: bool = True):
        self.teams = {name: Team(name, pts) for name, pts in teams.items()}
        self.rng = rng or random.Random()
        # True: VRS points change after every match (later win chances use them).
        # False: every match uses the points the teams started the stage with.
        self.update_points = update_points
        self.round = 0
        self.matches: list[Match] = []

    # ---- pairing -------------------------------------------------------------------
    def set_round1(self, pairs: list[tuple[str, str]]) -> list[Match]:
        if self.round != 0:
            raise ValueError("round 1 already set")
        used = [t for p in pairs for t in p]
        if sorted(used) != sorted(self.teams) :
            raise ValueError("round 1 must use every team exactly once")
        self.round = 1
        new = [Match(1, "0-0", a, b) for a, b in pairs]
        self.matches += new
        return new

    def groups(self) -> dict[tuple[int, int], list[Team]]:
        """Active teams grouped by record, best record first."""
        g: dict[tuple[int, int], list[Team]] = {}
        for t in self.teams.values():
            if t.active:
                g.setdefault(t.record, []).append(t)
        return dict(sorted(g.items(), key=lambda kv: (-kv[0][0], kv[0][1])))

    def _draw(self, pool: list[Team]) -> list[tuple[Team, Team]]:
        """Random draw within a group, never repeating a matchup (backtracks if a draw dead-ends)."""
        if not pool:
            return []
        first, rest = pool[0], pool[1:]
        candidates = [t for t in rest if t.name not in first.opponents]
        self.rng.shuffle(candidates)
        for opp in candidates:
            remaining = [t for t in rest if t is not opp]
            sub = self._draw(remaining)
            if sub is not None:
                return [(first, opp)] + sub
        return None

    def pair_next_round(self) -> list[Match]:
        if self.round == 0:
            raise ValueError("set round 1 first (set_round1)")
        if any(m.winner is None for m in self.matches):
            raise ValueError("record all results of the current round first")
        groups = self.groups()
        if not groups:
            return []
        self.round += 1
        new: list[Match] = []
        for (w, l), members in groups.items():
            if len(members) % 2:
                raise ValueError(f"odd number of teams at {w}-{l}")
            pool = members[:]
            self.rng.shuffle(pool)
            pairs = self._draw(pool)
            if pairs is None:
                raise ValueError(f"no valid draw without rematches at {w}-{l}")
            new += [Match(self.round, f"{w}-{l}", a.name, b.name) for a, b in pairs]
        self.matches += new
        return new

    # ---- results -------------------------------------------------------------------
    def record_result(self, match: Match, winner: str) -> None:
        if winner not in (match.team1, match.team2):
            raise ValueError(f"{winner} did not play in {match.team1} vs {match.team2}")
        loser = match.team2 if winner == match.team1 else match.team1
        w, l = self.teams[winner], self.teams[loser]
        match.winner = winner
        w.wins += 1
        l.losses += 1
        w.opponents.add(loser)
        l.opponents.add(winner)
        if self.update_points:
            w.points, l.points = update_ratings(w.points, l.points)

    def simulate_result(self, match: Match) -> float:
        """Pick a winner using VRS expected win probability; returns team1's win chance."""
        p1 = expected_win_probability(self.teams[match.team1].points, self.teams[match.team2].points)
        self.record_result(match, match.team1 if self.rng.random() < p1 else match.team2)
        return p1

    def simulate_to_end(self) -> None:
        """Simulate every remaining match (round 1 must already be set)."""
        while True:
            for m in self.matches:
                if m.winner is None:
                    self.simulate_result(m)
            if not self.pair_next_round():
                return

    @property
    def finished(self) -> bool:
        return self.round > 0 and not self.groups() and all(m.winner for m in self.matches)


# ---- CLI ---------------------------------------------------------------------------
def load_points(path: str) -> dict[str, float]:
    with open(path, newline="") as f:
        return {row["team"]: float(row["points"]) for row in csv.DictReader(f)}


def load_pairs(path: str) -> list[tuple[str, str]]:
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if rows and rows[0][0].lower() == "team1":
        rows = rows[1:]
    return [(a.strip(), b.strip()) for a, b, *_ in rows]


# Fixed round-1 matchups. These do NOT change if VRS points change.
ROUND1 = [
    ("PVISION", "DENDELE CS"),
    ("Team Liquid", "9z Team"),
    ("100 Thieves", "Imperial Esports"),
    ("HEROIC", "Wildcard"),
    ("Ninjas in Pyjamas", "FlyQuest"),
    ("FaZe Clan", "BC.Game"),
    ("Eternal Fire", "Chinggis Warriors"),
    ("M80", "Lynn Vision Gaming"),
]


def main() -> None:
    p = argparse.ArgumentParser(description="Simulate a Swiss stage using VRS points.")
    p.add_argument("points_csv")
    p.add_argument("--round1", help="CSV of round-1 pairings (team1,team2)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for draws/results")
    args = p.parse_args()

    points = load_points(args.points_csv)
    stage = SwissStage(points, random.Random(args.seed))
    stage.set_round1(load_pairs(args.round1) if args.round1 else ROUND1)

    while True:
        current = [m for m in stage.matches if m.round == stage.round]
        print(f"\n### Round {stage.round}\n")
        print("| Group | Match | Win % (team 1) | Winner |")
        print("|---|---|---|---|")
        for m in current:
            p1 = stage.simulate_result(m)
            print(f"| {m.group} | {m.team1} vs {m.team2} | {p1 * 100:.0f}% | {m.winner} |")
        if not stage.pair_next_round():
            break

    print("\n### Final standings\n")
    print("| Team | Record | Result | VRS before | VRS after |")
    print("|---|---|---|---|---|")
    for t in sorted(stage.teams.values(), key=lambda t: (-t.wins, t.losses, -t.points)):
        res = "Advanced" if t.wins == WINS_TO_ADVANCE else "Eliminated"
        print(f"| {t.name} | {t.wins}-{t.losses} | {res} | {points[t.name]:.0f} | {t.points:.0f} |")


if __name__ == "__main__":
    main()
