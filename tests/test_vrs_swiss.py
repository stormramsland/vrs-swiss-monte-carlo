import argparse
import io
import os
import random
import tempfile
import unittest
import unittest.mock
from collections import Counter
from contextlib import redirect_stdout

from vrs_swiss.bets import Bet, joint_kelly
from vrs_swiss.kelly import kelly, to_decimal_odds
from vrs_swiss.montecarlo import simulate
from vrs_swiss.pipeline import check_inputs
from vrs_swiss.swiss import Calibration, SwissStage, add_calibration_args, calibration_from_args
from vrs_swiss.vrs_match import (age_weight, calibrate, calibrated_win_probability,
                                     expected_win_probability, update_ratings)

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


class TestCalibration(unittest.TestCase):
    def test_shrinks_towards_half(self):
        self.assertAlmostEqual(calibrate(0.5), 0.5)
        self.assertAlmostEqual(calibrate(0.9), 0.82)
        self.assertAlmostEqual(calibrate(1.0), 0.9)
        self.assertAlmostEqual(calibrate(0.9, 1.0), 0.9)     # slope 1 = raw model
        self.assertAlmostEqual(calibrate(0.9, 0.0), 0.5)     # slope 0 = coin flip

    def test_both_sides_sum_to_one(self):
        for a, b in [(1750, 1320), (1500, 1500), (1400, 1600)]:
            self.assertAlmostEqual(calibrated_win_probability(a, b) + calibrated_win_probability(b, a), 1)
            self.assertLess(abs(calibrated_win_probability(a, b) - 0.5),
                            abs(expected_win_probability(a, b) - 0.5) + 1e-12)

    def test_invalid_slope(self):
        with self.assertRaises(ValueError):
            calibrate(0.7, 1.5)

    def test_simulation_uses_it(self):
        # big favourite vs big underdog: calibrated sims should produce more upsets
        teams = {"Fav": 1900, "Dog": 1300}
        def upsets(cal):
            rng, n = random.Random(1), 0
            for _ in range(4000):
                s = SwissStage(teams, rng, update_points=False, calibration=cal)
                s.set_round1([("Fav", "Dog")])
                s.simulate_result(s.matches[0])
                n += s.matches[0].winner == "Dog"
            return n / 4000
        raw = 1 - expected_win_probability(1900, 1300)
        self.assertAlmostEqual(upsets(1.0), raw, delta=0.02)
        self.assertAlmostEqual(upsets(0.8), 1 - calibrate(1 - raw), delta=0.02)


class TestPerFormatCalibration(unittest.TestCase):
    def play(self, calibration):
        stage = SwissStage(TEAMS, random.Random(3), calibration=calibration)
        stage.set_round1(ROUND1)
        stage.simulate_to_end()
        return stage

    def test_deciders_are_bo3(self):
        # Major Stage 1/2: advancement and elimination matches are BO3, the rest BO1
        stage = self.play(Calibration())
        for m in stage.matches:
            w, l = map(int, m.group.split("-"))
            self.assertEqual(m.best_of, 3 if 2 in (w, l) else 1, m.group)
        counts = Counter(m.best_of for m in stage.matches)
        self.assertEqual(counts, {1: 20, 3: 13})   # 0-0, 1-0, 0-1, 1-1 are BO1

    def test_all_and_none(self):
        self.assertTrue(all(m.best_of == 3 for m in self.play(Calibration(bo3_matches="all")).matches))
        self.assertTrue(all(m.best_of == 1 for m in self.play(Calibration(bo3_matches="none")).matches))

    def test_slope_follows_format(self):
        teams = {"Fav": 1900, "Dog": 1300}
        raw = expected_win_probability(1900, 1300)
        for rule, expected in [("none", calibrate(raw, 0.3)), ("all", calibrate(raw, 0.9))]:
            s = SwissStage(teams, random.Random(1), update_points=False,
                           calibration=Calibration(bo1=0.3, bo3=0.9, bo3_matches=rule))
            s.set_round1([("Fav", "Dog")])
            self.assertAlmostEqual(s.simulate_result(s.matches[0]), expected)

    def test_defaults_and_single_number(self):
        self.assertEqual((Calibration().bo1, Calibration().bo3), (0.47, 0.61))
        self.assertEqual(SwissStage(TEAMS, calibration=0.8).calibration, Calibration(0.8, 0.8))
        self.assertEqual(SwissStage(TEAMS).calibration, Calibration())

    def test_invalid(self):
        with self.assertRaises(ValueError):
            Calibration(bo1=1.5)
        with self.assertRaises(ValueError):
            Calibration(bo3_matches="some")

    def test_command_line_options(self):
        p = argparse.ArgumentParser()
        add_calibration_args(p)
        parse = lambda *a: calibration_from_args(p, p.parse_args(list(a)))
        self.assertEqual(parse(), Calibration(0.47, 0.61, "deciders"))
        self.assertEqual(parse("--calibration", "1"), Calibration(1, 1))          # overrides both
        self.assertEqual(parse("--calibration-bo1", "0.5", "--bo3", "all"), Calibration(0.5, 0.61, "all"))
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            parse("--calibration-bo3", "2")


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
