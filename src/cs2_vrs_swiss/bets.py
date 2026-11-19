"""
Find +EV "qualify / doesn't qualify" bets and size them with Kelly, using the
Monte Carlo output.

Inputs (all made by / for montecarlo.py):
    data/odds.csv                  team,qualify,not_qualify   (decimal, American or fractional odds)
    output/montecarlo_results.csv  summary table (used as a cross-check)
    output/montecarlo_runs.csv     one row per run, 1 = team qualified, 0 = eliminated

1. Value bets
   For every team and both sides, the model probability p comes from the runs.
       EV per 1€ staked  = p * odds - 1
       house edge (team) = 1/qualify_odds + 1/not_qualify_odds - 1   (the bookmaker margin)
   A bet is kept only if its EV is above that team's house edge. A bet with a smaller
   edge than the margin is too easily explained by the model simply being a bit wrong.

2. Kelly sizing (joint)
   All selected bets are on the same stage, so they're linked (exactly 8 teams qualify).
   Instead of sizing each bet on its own, the stakes are chosen to maximise the average
   log bankroll over the simulated runs, i.e. Kelly for all bets at once, subject to:
       - each stake <= max bet (default 5€)
       - you can never lose more than the bankroll
   --fraction scales the result (0.5 = half Kelly). Stakes are rounded down to cents.

Usage (from the project root):
    python -m cs2_vrs_swiss.bets
    python -m cs2_vrs_swiss.bets --bankroll 50 --max-bet 5 --fraction 0.5
    python -m cs2_vrs_swiss.bets --odds data/odds.csv --runs output/montecarlo_runs.csv --results output/montecarlo_results.csv --save output/bets.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from collections import Counter
from dataclasses import dataclass

from .kelly import kelly, load_odds, to_decimal_odds



@dataclass
class Bet:
    team: str
    market: str           # "Qualify" or "Not qualify"
    odds: float           # decimal
    p: float              # model probability this bet wins
    house_edge: float     # bookmaker margin on this team (both sides)

    @property
    def ev(self) -> float:
        return self.p * self.odds - 1

    @property
    def implied(self) -> float:
        return 1 / self.odds

    @property
    def is_value(self) -> bool:
        return self.ev > self.house_edge

    def wins(self, qualified: bool) -> bool:
        return qualified if self.market == "Qualify" else not qualified

    def label(self) -> str:
        return f"{self.team} – {'qualifies' if self.market == 'Qualify' else 'does not qualify'}"


# ---- loading -----------------------------------------------------------------------
def load_runs(path: str) -> tuple[list[str], Counter]:
    """Returns (team names, Counter{tuple of 0/1 per team: number of runs})."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        teams = header[1:]
        patterns = Counter(tuple(int(x) for x in row[1:]) for row in reader if row)
    return teams, patterns


def load_results(path: str) -> dict[str, float]:
    with open(path, newline="") as f:
        return {r["Team"]: float(r["Qualify %"]) / 100 for r in csv.DictReader(f)}


def build_bets(odds: dict[str, dict[str, str]], teams: list[str], patterns: Counter) -> list[Bet]:
    n = sum(patterns.values())
    unknown = sorted(set(odds) - set(teams))
    if unknown:
        raise SystemExit(f"teams in odds file not found in runs file: {', '.join(unknown)}")
    bets = []
    for team, markets in odds.items():
        i = teams.index(team)
        p_q = sum(c for pat, c in patterns.items() if pat[i]) / n
        dec = {m: to_decimal_odds(o) for m, o in markets.items()}
        # house edge needs both sides; with only one side we can't measure it, use 0
        margin = (1 / dec["Qualify"] + 1 / dec["Not qualify"] - 1) if len(dec) == 2 else 0.0
        for m, d in dec.items():
            bets.append(Bet(team, m, d, p_q if m == "Qualify" else 1 - p_q, margin))
    return bets


# ---- joint Kelly -------------------------------------------------------------------
def joint_kelly(bets: list[Bet], teams: list[str], patterns: Counter, bankroll: float,
                max_bet: float, fraction: float = 1.0, iters: int = 200) -> list[float]:
    """
    Stakes (in money) maximising E[log(bankroll after all bets settle)] over the
    simulated runs. Coordinate ascent; the objective is concave so this converges
    to the optimum. With fraction < 1, solves full Kelly with cap max_bet/fraction
    and scales down, so every stake still ends up <= max_bet.
    """
    if not bets:
        return []
    cap = max_bet / fraction
    total = sum(patterns.values())
    idx = [teams.index(b.team) for b in bets]
    pats = list(patterns.items())
    weights = [c / total for _, c in pats]
    # r[i][w]: profit per 1 unit staked on bet i in outcome w
    r = [[(b.odds - 1) if b.wins(bool(pat[idx[i]])) else -1.0 for pat, _ in pats]
         for i, b in enumerate(bets)]

    stakes = [0.0] * len(bets)
    wealth = [bankroll] * len(pats)

    def deriv(i: int, s: float) -> float:
        ri = r[i]
        return sum(w * ri[k] / (wealth[k] + (s - stakes[i]) * ri[k]) for k, w in enumerate(weights))

    for _ in range(iters):
        moved = 0.0
        for i in range(len(bets)):
            # largest stake keeping wealth > 0 in every outcome (others fixed)
            ri = r[i]
            limit = min((wealth[k] - stakes[i] * ri[k]) for k in range(len(pats)) if ri[k] < 0)
            hi = min(cap, limit * (1 - 1e-9))
            lo = 0.0
            if deriv(i, lo) <= 0:
                new = 0.0
            elif deriv(i, hi) >= 0:
                new = hi
            else:
                for _ in range(60):
                    mid = (lo + hi) / 2
                    if deriv(i, mid) > 0:
                        lo = mid
                    else:
                        hi = mid
                new = (lo + hi) / 2
            delta = new - stakes[i]
            if delta:
                for k in range(len(pats)):
                    wealth[k] += delta * ri[k]
                stakes[i] = new
                moved = max(moved, abs(delta))
        if moved < 1e-7:
            break
    return [s * fraction for s in stakes]


def portfolio_stats(bets: list[Bet], stakes: list[float], teams: list[str], patterns: Counter,
                    bankroll: float) -> dict[str, float]:
    total = sum(patterns.values())
    idx = [teams.index(b.team) for b in bets]
    dist = []
    for pat, c in patterns.items():
        profit = sum(s * ((b.odds - 1) if b.wins(bool(pat[i])) else -1)
                     for b, s, i in zip(bets, stakes, idx))
        dist.append((profit, c))
    dist.sort()
    mean = sum(p * c for p, c in dist) / total

    def pct(q: float) -> float:
        acc = 0
        for p, c in dist:
            acc += c
            if acc >= q * total:
                return p
        return dist[-1][0]

    return {
        "staked": sum(stakes),
        "expected_profit": mean,
        "p_profit": sum(c for p, c in dist if p > 1e-9) / total,
        "p_loss": sum(c for p, c in dist if p < -1e-9) / total,
        "worst": dist[0][0],
        "best": dist[-1][0],
        "p05": pct(0.05),
        "median": pct(0.5),
        "p95": pct(0.95),
        "log_growth": sum(c * math.log((bankroll + p) / bankroll) for p, c in dist) / total,
    }


# ---- CLI ---------------------------------------------------------------------------
def report(odds: str, runs: str, results: str | None = None, bankroll: float = 50.0,
           max_bet: float = 5.0, fraction: float = 1.0) -> list[tuple[Bet, float]]:
    """Print value bets and joint-Kelly stakes; returns [(bet, stake), ...] for stakes > 0."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    teams, patterns = load_runs(runs)
    n = sum(patterns.values())
    bets = build_bets(load_odds(odds), teams, patterns)

    # cross-check: runs and results table should come from the same simulation
    if results and os.path.exists(results):
        res = load_results(results)
        diffs = [abs(b.p - res[b.team]) for b in bets if b.market == "Qualify" and b.team in res]
        if diffs and max(diffs) > 0.002:
            print(f"WARNING: runs and results files disagree by up to {max(diffs) * 100:.1f} points;"
                  " they may come from different simulations.\n")

    bets.sort(key=lambda b: b.ev - b.house_edge, reverse=True)
    print(f"{n} simulated runs, {len(bets)} bets on offer\n")
    print("### All bets with positive EV\n")
    print("| Bet | Odds | Model % | Implied % | EV | House edge | Value bet? |")
    print("|---|---|---|---|---|---|---|")
    for b in bets:
        if b.ev > 0:
            print(f"| {b.label()} | {b.odds:.2f} | {b.p * 100:.1f} | {b.implied * 100:.1f}"
                  f" | {b.ev * 100:+.1f}% | {b.house_edge * 100:.1f}% | {'Yes' if b.is_value else 'No'} |")

    value = [b for b in bets if b.is_value]
    if not value:
        print("\nNo bet has an EV above the house edge. Nothing to bet.")
        return []

    stakes = joint_kelly(value, teams, patterns, bankroll, max_bet, fraction)
    stakes = [math.floor(s * 100) / 100 for s in stakes]       # round down to cents
    single = [min(max_bet, kelly(b.p, b.odds, fraction).stake(bankroll)) for b in value]

    frac_txt = {1: "full Kelly", 0.5: "half Kelly"}.get(fraction, f"{fraction:g} × Kelly")
    print(f"\n### Stakes ({frac_txt}, bankroll {bankroll:.2f}€, max {max_bet:.2f}€ per bet)\n")
    print("| Bet | Odds | EV | Stake if sized alone | Stake (joint Kelly) | Returns if it wins |")
    print("|---|---|---|---|---|---|")
    for b, s1, s in zip(value, single, stakes):
        print(f"| {b.label()} | {b.odds:.2f} | {b.ev * 100:+.1f}% | {s1:.2f}€ | **{s:.2f}€** | {s * b.odds:.2f}€ |")

    placed = [(b, s) for b, s in zip(value, stakes) if s > 0]
    if not placed:
        print("\nJoint Kelly puts nothing on these bets.")
        return []
    st = portfolio_stats([b for b, _ in placed], [s for _, s in placed], teams, patterns, bankroll)
    print(f"\n### If you place these bets (over the {n} simulated runs)\n")
    print(f"- Total staked: {st['staked']:.2f}€ of {bankroll:.2f}€")
    print(f"- Expected profit: {st['expected_profit']:+.2f}€ ({st['expected_profit'] / st['staked'] * 100:+.1f}% of stake)")
    print(f"- Chance of ending in profit: {st['p_profit'] * 100:.1f}%, in loss: {st['p_loss'] * 100:.1f}%")
    print(f"- Median result: {st['median']:+.2f}€; 90% of runs between {st['p05']:+.2f}€ and {st['p95']:+.2f}€")
    print(f"- Worst case: {st['worst']:+.2f}€, best case: {st['best']:+.2f}€")
    print(f"- Expected bankroll growth (log): {st['log_growth'] * 100:+.2f}%")

    return placed


def save_bets(placed: list[tuple[Bet, float]], path: str) -> None:
    """Save the bets to place as CSV: team,bet,odds,model_pct,ev_pct,stake,returns_if_win."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["team", "bet", "odds", "model_pct", "ev_pct", "stake", "returns_if_win"])
        for b, s in placed:
            w.writerow([b.team, b.market, f"{b.odds:.2f}", f"{b.p * 100:.1f}", f"{b.ev * 100:.1f}",
                        f"{s:.2f}", f"{s * b.odds:.2f}"])
    print(f"\nSaved bets to {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Find +EV bets and size them with joint Kelly.")
    ap.add_argument("--odds", default=os.path.join("data", "odds.csv"))
    ap.add_argument("--runs", default=os.path.join("output", "montecarlo_runs.csv"))
    ap.add_argument("--results", default=os.path.join("output", "montecarlo_results.csv"))
    ap.add_argument("--bankroll", type=float, default=50.0, help="Bankroll in € (default 50)")
    ap.add_argument("--max-bet", type=float, default=5.0, help="Max stake per bet in € (default 5)")
    ap.add_argument("--fraction", type=float, default=0.5,
                    help="Share of Kelly to bet (default 0.5 = half Kelly; 1.0 = full Kelly)")
    ap.add_argument("--save", default=None, help="Also save the bets to place to this CSV")
    args = ap.parse_args()
    if not 0 < args.fraction <= 1:
        ap.error("--fraction must be in (0, 1]")

    placed = report(args.odds, args.runs, args.results, args.bankroll, args.max_bet, args.fraction)
    if args.save:
        save_bets(placed, args.save)


if __name__ == "__main__":
    main()
