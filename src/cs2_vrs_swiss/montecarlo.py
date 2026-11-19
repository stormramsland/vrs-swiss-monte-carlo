"""
Monte Carlo simulation of a Swiss stage: how often does each team qualify?

Each simulation plays the whole stage with swiss.py: round 1 from the matchups file,
later rounds drawn by record with no rematches, and winners picked with VRS win chances.

Usage:
    python -m cs2_vrs_swiss.montecarlo data/vrs_points.csv data/round1.csv
    python -m cs2_vrs_swiss.montecarlo data/vrs_points.csv data/round1.csv -n 10000 --seed 1
    python -m cs2_vrs_swiss.montecarlo data/vrs_points.csv data/round1.csv --static-points --csv results.csv

The results table is printed and saved as CSV (default: output/montecarlo_results.csv;
change it with --csv).

Which teams qualified in each run is saved to output/montecarlo_runs.csv (change it with
--runs-csv): one row per run, one column per team, 1 = qualified, 0 = eliminated.
Every row has exactly 8 ones. Use it for anything that needs the joint outcomes,
e.g. sizing several bets on the same stage together.

Swap in another points file and matchups file to simulate a different event.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from collections import Counter

from .swiss import LOSSES_TO_ELIMINATE, WINS_TO_ADVANCE, SwissStage, load_pairs, load_points

RECORDS = [(3, 0), (3, 1), (3, 2), (2, 3), (1, 3), (0, 3)]


def simulate(points: dict[str, float], round1: list[tuple[str, str]], n: int,
             seed: int | None = None, update_points: bool = True
             ) -> tuple[dict[str, Counter], dict[str, list[tuple[bool, float]]]]:
    """
    Run n stages. Returns ({team: Counter of final (wins, losses)},
                           {team: [(qualified, VRS points after the stage), ...] one per simulation}).
    """
    rng = random.Random(seed)
    records = {t: Counter() for t in points}
    points_after: dict[str, list[tuple[bool, float]]] = {t: [] for t in points}
    for _ in range(n):
        stage = SwissStage(points, rng, update_points=update_points)
        stage.set_round1(round1)
        stage.simulate_to_end()
        for t in stage.teams.values():
            records[t.name][t.record] += 1
            points_after[t.name].append((t.wins == WINS_TO_ADVANCE, t.points))
    return records, points_after


def _mean(xs: list[float]) -> str:
    return f"{sum(xs) / len(xs):.0f}" if xs else "-"


def _percentile(sorted_xs: list[float], q: float) -> float:
    return sorted_xs[min(len(sorted_xs) - 1, int(q * len(sorted_xs)))]


def run(points_csv: str, round1_csv: str, sims: int = 100000, seed: int | None = None,
        static_points: bool = False, results_csv: str | None = None, runs_csv: str | None = None,
        show_table: bool = True) -> tuple[str, str]:
    """
    Simulate the stage, print the table and save both CSVs.
    Returns (results_csv path, runs_csv path). Default paths: output/ in the current folder.
    """
    points = load_points(points_csv)
    round1 = load_pairs(round1_csv)
    records, points_after = simulate(points, round1, sims, seed, not static_points)
    n = sims

    def pct(team: str, rec: tuple[int, int]) -> float:
        return 100 * records[team][rec] / n

    def qualify(team: str) -> float:
        return 100 * sum(c for (w, _), c in records[team].items() if w == WINS_TO_ADVANCE) / n

    order = sorted(points, key=lambda t: (-qualify(t), -points[t]))
    headers = (["Team", "VRS before", "VRS after if qualified", "VRS after if eliminated",
                "VRS after (80% range)", "Qualify %"]
               + [f"{w}-{l} %" for w, l in RECORDS])
    rows = []
    for t in order:
        after = points_after[t]
        all_after = sorted(p for _, p in after)
        rows.append([t, f"{points[t]:.0f}",
                     _mean([p for q, p in after if q]),
                     _mean([p for q, p in after if not q]),
                     f"{_percentile(all_after, 0.1):.0f}-{_percentile(all_after, 0.9):.0f}",
                     f"{qualify(t):.1f}"]
                    + [f"{pct(t, r):.1f}" for r in RECORDS])

    if show_table:
        print(f"{n} simulations{' (static points)' if static_points else ''}")
        if static_points:
            print("VRS after equals VRS before, since --static-points is on")
        else:
            print("VRS after = points once the stage is over; averaged over the simulations where the"
                  " team qualified / was eliminated. 80% range = 10th to 90th percentile.")
        print()
        print("| " + " | ".join(headers) + " |")
        print("|" + "---|" * len(headers))
        for row in rows:
            print("| " + " | ".join(row) + " |")
        print()

    folder = "output"
    out = results_csv or os.path.join(folder, "montecarlo_results.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    print(f"Saved results to {out}")

    # One row per run: 1 = team qualified in that run, 0 = eliminated.
    runs_out = runs_csv or os.path.join(folder, "montecarlo_runs.csv")
    os.makedirs(os.path.dirname(os.path.abspath(runs_out)), exist_ok=True)
    teams = list(points)
    with open(runs_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run"] + teams)
        for i in range(n):
            w.writerow([i + 1] + [1 if points_after[t][i][0] else 0 for t in teams])
    print(f"Saved per-run qualifiers to {runs_out}")
    return out, runs_out


def main() -> None:
    p = argparse.ArgumentParser(description="Monte Carlo qualification odds for a Swiss stage.")
    p.add_argument("points_csv", help="CSV with team,points")
    p.add_argument("round1_csv", help="CSV with team1,team2 (round-1 matchups)")
    p.add_argument("-n", "--sims", type=int, default=100000, help="Number of simulations (default 100000)")
    p.add_argument("--seed", type=int, default=None, help="Random seed, for repeatable results")
    p.add_argument("--static-points", action="store_true",
                   help="Don't update VRS points between matches inside a simulation")
    p.add_argument("--csv", default=None,
                   help="Where to save the results table as CSV "
                        "(default: output/montecarlo_results.csv)")
    p.add_argument("--runs-csv", default=None,
                   help="Where to save which teams qualified in each run "
                        "(default: output/montecarlo_runs.csv)")
    args = p.parse_args()
    run(args.points_csv, args.round1_csv, args.sims, args.seed, args.static_points,
        args.csv, args.runs_csv)


if __name__ == "__main__":
    main()
