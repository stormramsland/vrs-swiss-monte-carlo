import io
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout

from cs2_vrs_swiss.bets import Bet, joint_kelly
from cs2_vrs_swiss.kelly import kelly, to_decimal_odds
from cs2_vrs_swiss.montecarlo import simulate
from cs2_vrs_swiss.pipeline import check_inputs
from cs2_vrs_swiss.swiss import SwissStage
from cs2_vrs_swiss.vrs_match import age_weight, update_ratings

TEAMS = {f"T{i}": 1600 - 25 * i for i in range(16)}
ROUND1 = [(f"T{i}", f"T{i + 8}") for i in range(8)]


class TestVrsMatch(unittest.TestCase):
    def test_even_match_is_zero_sum(self):
        w, l = update_ratings(1500, 1500)
        self.assertAlmostEqual(w - 1500, 1500 - l)
        self.assertAlmostEqual(w - 1500, 15.08, places=2)

    def test_upset_moves_more_than_expected_win(self):
        upset = update_ratings(1400, 1600)[0] - 1400
        expected = update_ratings(1600, 1400)[0] - 1600
        self.assertGreater(upset, expected)

    def test_age_weight(self):
        end = 1_000_000_000
        day = 24 * 3600
        self.assertEqual(age_weight(end, end), 1.0)               # last month: full weight
        self.assertEqual(age_weight(end - 181 * day, end), 0.0)   # older than 6 months
        self.assertAlmostEqual(age_weight(end - 105 * day, end), 0.5)


class TestSwiss(unittest.TestCase):
    def test_stage_rules(self):
        for seed in range(200):
            stage = SwissStage(TEAMS, random.Random(seed))
            stage.set_round1(ROUND1)
            stage.simulate_to_end()
            pairs = [frozenset((m.team1, m.team2)) for m in stage.matches]
            self.assertEqual(len(pairs), len(set(pairs)), "rematch")
            self.assertEqual(len(stage.matches), 33)
            self.assertEqual(sum(t.wins == 3 for t in stage.teams.values()), 8)
            for m in stage.matches:   # opponents always share a record
                self.assertEqual(m.group.count("-"), 1)

    def test_round1_must_use_every_team(self):
        stage = SwissStage(TEAMS)
        with self.assertRaises(ValueError):
            stage.set_round1(ROUND1[:-1])


class TestMonteCarlo(unittest.TestCase):
    def test_eight_qualify_every_run(self):
        records, after = simulate(TEAMS, ROUND1, 300, seed=1)
        for i in range(300):
            self.assertEqual(sum(after[t][i][0] for t in TEAMS), 8)
        self.assertEqual(sum(c for r in records.values() for c in r.values()), 300 * 16)


class TestKelly(unittest.TestCase):
    def test_formula(self):
        self.assertAlmostEqual(kelly(0.6, 2.0).kelly_fraction, 0.2)
        self.assertEqual(kelly(0.3, 2.0).kelly_fraction, 0.0)
        self.assertAlmostEqual(kelly(0.6, 2.0, fraction=0.5).stake(100), 10)

    def test_odds_formats(self):
        self.assertEqual(to_decimal_odds("+150"), 2.5)
        self.assertEqual(to_decimal_odds("-200"), 1.5)
        self.assertEqual(to_decimal_odds("3/2"), 2.5)
        with self.assertRaises(ValueError):
            to_decimal_odds("1.0")


class TestJointKelly(unittest.TestCase):
    def test_single_bet_matches_kelly_formula(self):
        teams = ["A", "B"]
        patterns = {(1, 0): 60, (0, 1): 40}          # A qualifies 60% of the time
        bet = Bet("A", "Qualify", 2.0, 0.6, 0.0)
        stake = joint_kelly([bet], teams, patterns, bankroll=100, max_bet=1e9)[0]
        self.assertAlmostEqual(stake, 20, places=4)

    def test_cap_and_opposite_bets(self):
        teams = ["A", "B"]
        patterns = {(1, 0): 60, (0, 1): 40}
        bets = [Bet("A", "Qualify", 2.0, 0.6, 0.0), Bet("B", "Not qualify", 2.0, 0.6, 0.0)]
        stakes = joint_kelly(bets, teams, patterns, bankroll=100, max_bet=5)
        self.assertTrue(all(s <= 5 + 1e-9 for s in stakes))


class TestPipelineChecks(unittest.TestCase):
    def test_bad_team_name_stops_early(self):
        with tempfile.TemporaryDirectory() as d:
            def write(name, text):
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            write("p.csv", "team,points\nA,1500\nB,1400\n")
            write("r.csv", "team1,team2\nA,B\n")
            write("o.csv", "team,qualify,not_qualify\nA,1.5,2.5\nX,2.0,1.8\n")
            with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()):
                check_inputs(*(os.path.join(d, n) for n in ("p.csv", "r.csv", "o.csv")))
            self.assertIn("'X'", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
