"""Unit tests for skilluminati.stats. Pure Python, no Hermes, no network.

Loads ``stats.py`` by file path so it works whether or not ``skilluminati/__init__.py``
exists or is importable.

Run: python -m pytest tests/test_stats.py -v
(pytest also collects these when installed.)
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATS_PATH = REPO / "plugin" / "skilluminati" / "stats.py"


def _load():
    spec = importlib.util.spec_from_file_location("skilluminati_stats", STATS_PATH)
    assert spec is not None and spec.loader is not None, STATS_PATH
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve the module if annotations are
    # ever stringified.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stats = _load()


class WilsonTests(unittest.TestCase):
    def test_five_of_five_lower_bound(self):
        low, high = stats.wilson(5, 5)
        self.assertAlmostEqual(low, 0.566, places=3)
        self.assertEqual(high, 1.0)

    def test_four_of_five_lower_bound(self):
        low, _ = stats.wilson(4, 5)
        self.assertAlmostEqual(low, 0.376, places=3)

    def test_zero_of_five_upper_bound(self):
        low, high = stats.wilson(0, 5)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.434, places=3)

    def test_eighteen_of_twenty_five_lower_bound(self):
        # BRIEF quotes 0.504 (continuity-corrected); the plain Wilson bound is 0.524.
        # Both are above 0.5, which is what the verdict depends on.
        low, _ = stats.wilson(18, 25)
        self.assertGreater(low, 0.504)
        self.assertAlmostEqual(low, 0.524, places=3)

    def test_zero_trials_is_uninformative(self):
        self.assertEqual(stats.wilson(0, 0), (0.0, 1.0))

    def test_bounds_are_clamped_and_ordered(self):
        for wins, n in [(0, 1), (1, 1), (3, 7), (50, 100), (99, 100)]:
            low, high = stats.wilson(wins, n)
            self.assertGreaterEqual(low, 0.0)
            self.assertLessEqual(high, 1.0)
            self.assertLessEqual(low, high)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            stats.wilson(6, 5)
        with self.assertRaises(ValueError):
            stats.wilson(-1, 5)


class VerdictTests(unittest.TestCase):
    def test_five_of_five_is_keep(self):
        v = stats.verdict(5, 0)
        self.assertEqual(v.verdict, stats.KEEP)
        self.assertAlmostEqual(v.low, 0.566, places=3)
        self.assertEqual(v.n_decisive, 5)
        self.assertEqual(v.tasks_to_settle, 0)

    def test_four_of_five_is_unsure(self):
        v = stats.verdict(4, 1)
        self.assertEqual(v.verdict, stats.UNSURE)
        self.assertAlmostEqual(v.low, 0.376, places=3)
        self.assertIsInstance(v.tasks_to_settle, int)
        self.assertGreater(v.tasks_to_settle, 0)

    def test_zero_of_five_is_drop(self):
        v = stats.verdict(0, 5)
        self.assertEqual(v.verdict, stats.DROP)
        self.assertAlmostEqual(v.high, 0.434, places=3)
        self.assertEqual(v.tasks_to_settle, 0)

    def test_eighteen_of_twenty_five_is_keep(self):
        v = stats.verdict(18, 7)
        self.assertEqual(v.verdict, stats.KEEP)
        self.assertGreater(v.low, 0.504)
        self.assertEqual(v.tasks_to_settle, 0)

    def test_ties_are_ignored(self):
        with_ties = stats.verdict(5, 0, ties=40)
        without = stats.verdict(5, 0)
        self.assertEqual(with_ties.verdict, stats.KEEP)
        self.assertEqual(with_ties.n_decisive, 5)
        self.assertEqual(with_ties.ties, 40)
        self.assertEqual((with_ties.low, with_ties.high), (without.low, without.high))
        self.assertEqual(with_ties.tasks_to_settle, without.tasks_to_settle)

    def test_all_ties_is_unsure(self):
        v = stats.verdict(0, 0, ties=10)
        self.assertEqual(v.verdict, stats.UNSURE)
        self.assertEqual(v.n_decisive, 0)
        self.assertIsNone(v.rate)
        self.assertIsNone(v.tasks_to_settle)

    def test_exact_half_rate_never_settles(self):
        v = stats.verdict(10, 10)
        self.assertEqual(v.verdict, stats.UNSURE)
        self.assertIsNone(v.tasks_to_settle)

    def test_tasks_to_settle_projects_at_observed_rate(self):
        # 4/5 = 0.8: the smallest extra count where the projected interval clears 0.5.
        # At 0.8 the bound is 0.490 over 10 tasks and 0.504 over 11, so 6 extra.
        extra = stats.tasks_to_settle(4, 5)
        self.assertEqual(extra, 6)
        low_at, _ = stats._wilson_rate(0.8, 5 + extra, stats.Z_95)
        low_before, _ = stats._wilson_rate(0.8, 5 + extra - 1, stats.Z_95)
        self.assertGreater(low_at, 0.5)
        self.assertLessEqual(low_before, 0.5)
        # Integer check: 8/10 is still Unsure.
        self.assertEqual(stats.verdict(8, 2).verdict, stats.UNSURE)

    def test_tasks_to_settle_for_a_real_seventy_percent_skill(self):
        # BRIEF: a real 70% skill needs about 25 to 50 decisive tasks.
        extra = stats.tasks_to_settle(7, 10)
        self.assertIsNotNone(extra)
        self.assertTrue(15 <= 10 + extra <= 50, 10 + extra)

    def test_tasks_to_settle_symmetric_for_losing_skill(self):
        self.assertEqual(stats.tasks_to_settle(1, 5), stats.tasks_to_settle(4, 5))

    def test_rejects_negative_counts(self):
        with self.assertRaises(ValueError):
            stats.verdict(-1, 2)


class PositionBiasTests(unittest.TestCase):
    def test_flag_triggers_above_threshold(self):
        pb = stats.position_bias(61, 100)
        self.assertAlmostEqual(pb.share, 0.61)
        self.assertTrue(pb.flagged)

    def test_flag_clear_at_threshold(self):
        # "60% or lower" passes (BRIEF Step 2 pass criteria).
        pb = stats.position_bias(60, 100)
        self.assertAlmostEqual(pb.share, 0.60)
        self.assertFalse(pb.flagged)

    def test_flag_clear_below_threshold(self):
        pb = stats.position_bias(5, 10)
        self.assertEqual(pb.share, 0.5)
        self.assertFalse(pb.flagged)

    def test_no_judgements(self):
        pb = stats.position_bias(0, 0)
        self.assertIsNone(pb.share)
        self.assertFalse(pb.flagged)

    def test_threshold_constant_matches_brief(self):
        self.assertEqual(stats.POSITION_BIAS_MAX, 0.60)


class AANoiseTests(unittest.TestCase):
    def test_balanced_run_counts(self):
        aa = stats.aa_noise(["win", "loss"] * 10 + ["tie"] * 4)
        self.assertEqual((aa.wins, aa.losses, aa.ties, aa.n), (10, 10, 4, 24))
        self.assertEqual(aa.n_decisive, 20)
        self.assertEqual(aa.rate, 0.5)
        self.assertAlmostEqual(aa.tie_share, 4 / 24)
        self.assertLess(aa.low, 0.5)
        self.assertGreater(aa.high, 0.5)

    def test_no_noisy_flag(self):
        # F11: the direction flag could not fire on a handful of pairs; firm picks (n_decisive) replace it.
        aa = stats.aa_noise(["win"] * 18 + ["loss"] * 2)
        self.assertEqual((aa.rate, aa.n_decisive), (0.9, 20))
        self.assertFalse(hasattr(aa, "noisy"))

    def test_aliases_and_booleans(self):
        aa = stats.aa_noise(["A", "b", "first", "second", True, False, None, "tie", "T"])
        self.assertEqual((aa.wins, aa.losses, aa.ties), (3, 3, 3))

    def test_empty_run(self):
        aa = stats.aa_noise([])
        self.assertEqual(aa.n, 0)
        self.assertIsNone(aa.rate)
        self.assertIsNone(aa.tie_share)
        self.assertEqual(aa.n_decisive, 0)

    def test_rejects_unknown_token(self):
        with self.assertRaises(ValueError):
            stats.aa_noise(["maybe"])


class ConstantsTests(unittest.TestCase):
    def test_thresholds_are_module_level(self):
        self.assertEqual(stats.Z_95, 1.96)
        self.assertEqual(stats.WIN_RATE_NULL, 0.5)
        self.assertEqual(stats.POSITION_BIAS_MAX, 0.60)
        self.assertIsInstance(stats.SETTLE_SEARCH_CAP, int)


if __name__ == "__main__":
    unittest.main()
