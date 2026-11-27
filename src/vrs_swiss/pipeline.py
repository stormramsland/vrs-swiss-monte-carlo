"""
One command from VRS points and odds to bets:

    1. checks the input files (same team names everywhere, valid round 1, valid odds)
    2. runs the Monte Carlo (100,000 simulations)  -> output/montecarlo_results.csv, montecarlo_runs.csv
    3. finds bets whose EV beats the house edge and sizes them with joint Kelly
                                                   -> output/bets.csv

Inputs (data/):
    vrs_points.csv   team,points
    round1.csv       team1,team2
    odds.csv         team,qualify,not_qualify

Usage (from the project root):
    vrs-swiss                                  # 50€ bankroll, 5€ max bet, half Kelly
    vrs-swiss --bankroll 25
    vrs-swiss --skip-sim                       # odds changed only: reuse the last simulation
    vrs-swiss --fraction 1 --seed 1            # full Kelly, repeatable
    vrs-swiss --bo3 all                        # every match best-of-three (e.g. a Stage 3)
(`python -m vrs_swiss` does the same as `vrs-swiss`.)
"""

from __future__ import annotations

import argparse
import os

from .bets import report, save_bets
from .kelly import load_odds, to_decimal_odds
from .montecarlo import run as run_montecarlo
from .swiss import add_calibration_args, calibration_from_args, load_pairs, load_points


def check_inputs(points_csv: str, round1_csv: str, odds_csv: str) -> None:
    """Stop early with a clear message instead of failing halfway through."""
    problems = []
    for f in (points_csv, round1_csv, odds_csv):
        if not os.path.exists(f):
            problems.append(f"missing file: {f}")
    if problems:
        raise SystemExit("\n".join(problems))

    points = load_points(points_csv)
    teams = set(points)
    if len(points) % 2:
        problems.append(f"{os.path.basename(points_csv)} has an odd number of teams ({len(points)})")

    round1 = load_pairs(round1_csv)
    used = [t for pair in round1 for t in pair]
    for t in sorted(set(used) - teams):
        problems.append(f"round1.csv: '{t}' is not in vrs_points.csv")
    for t in sorted(teams - set(used)):
        problems.append(f"round1.csv: '{t}' has no round-1 match")
    for t in sorted({t for t in used if used.count(t) > 1}):
        problems.append(f"round1.csv: '{t}' plays more than once")

    odds = load_odds(odds_csv)
    for t in sorted(set(odds) - teams):
        problems.append(f"odds.csv: '{t}' is not in vrs_points.csv (check the spelling)")
    for t, markets in odds.items():
        for m, o in markets.items():
            try:
                to_decimal_odds(o)
            except ValueError as e:
                problems.append(f"odds.csv: {t} {m}: {e}")
    missing = sorted(teams - set(odds))

    if problems:
        raise SystemExit("Input problems:\n  " + "\n  ".join(problems))
    if missing:
        print(f"Note: no odds for {', '.join(missing)}; they're simulated but not bet on.\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vrs-swiss",
                                 description="Simulate the Swiss stage and find value bets.")
    ap.add_argument("--bankroll", type=float, default=50.0, help="Bankroll in € (default 50)")
    ap.add_argument("--max-bet", type=float, default=5.0, help="Max stake per bet in € (default 5)")
    ap.add_argument("--fraction", type=float, default=0.5,
                    help="Share of Kelly to bet (default 0.5 = half Kelly; 1.0 = full Kelly)")
    ap.add_argument("-n", "--sims", type=int, default=100000, help="Simulations (default 100000)")
    ap.add_argument("--seed", type=int, default=None, help="Random seed, for repeatable results")
    ap.add_argument("--skip-sim", action="store_true",
                    help="Reuse the last simulation in the output folder instead of simulating again")
    ap.add_argument("--quiet", action="store_true", help="Don't print the Monte Carlo table")
    add_calibration_args(ap)
    ap.add_argument("--data-dir", default="data", help="Folder with the input CSVs (default: data)")
    ap.add_argument("--output-dir", default="output", help="Folder for generated CSVs (default: output)")
    args = ap.parse_args(argv)
    if not 0 < args.fraction <= 1:
        ap.error("--fraction must be in (0, 1]")
    calibration = calibration_from_args(ap, args)

    points_csv = os.path.join(args.data_dir, "vrs_points.csv")
    round1_csv = os.path.join(args.data_dir, "round1.csv")
    odds_csv = os.path.join(args.data_dir, "odds.csv")
    results_csv = os.path.join(args.output_dir, "montecarlo_results.csv")
    runs_csv = os.path.join(args.output_dir, "montecarlo_runs.csv")
    bets_csv = os.path.join(args.output_dir, "bets.csv")

    check_inputs(points_csv, round1_csv, odds_csv)
    os.makedirs(args.output_dir, exist_ok=True)

    print("## 1. Monte Carlo\n")
    if args.skip_sim:
        if not (os.path.exists(results_csv) and os.path.exists(runs_csv)):
            raise SystemExit("--skip-sim: no previous simulation found; run without it first")
        print("Skipped; using the last simulation.\n")
    else:
        run_montecarlo(points_csv, round1_csv, args.sims, args.seed,
                       results_csv=results_csv, runs_csv=runs_csv, show_table=not args.quiet,
                       calibration=calibration)
        print()

    print("## 2. Bets\n")
    placed = report(odds_csv, runs_csv, results_csv, args.bankroll, args.max_bet, args.fraction)
    save_bets(placed, bets_csv)


if __name__ == "__main__":
    main()
