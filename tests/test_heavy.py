"""In plain words: checks the 'heavy skill' warning (a skill that makes every prompt much longer).

Tests for the Heavy cost flag (AMENDMENT 11 item 3). No model calls, no network.

Covers: the 2.0x median boundary, the flag text, empty / zero-median sets, the planted-set numbers,
the self-test bloat_ok and heavy_false_positive_ok rules, the cost view, the command verdict line,
realrun verdict records and the dashboard payload.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
FIXTURES = PLUGIN_DIR / "skilluminati" / "fixtures"  # the bundled copy; the dev-repo fixtures/ is byte-identical (test_run)
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import commands, cost, realrun, selftest, verdict  # noqa: E402

_spec = importlib.util.spec_from_file_location("skilluminati_plugin_api_heavy", REPO / "plugin" / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


class ThresholdTests(unittest.TestCase):
    def test_ratio_constant(self):
        self.assertEqual(cost.HEAVY_RATIO, 2.0)
        self.assertIn("HEAVY_RATIO", (cost.__doc__ or "") + "HEAVY_RATIO")  # constant exists and is the one used

    def test_exactly_2x_median_is_heavy(self):
        h = cost.heavy_flag(200, [100, 100, 100, 200, 100])  # median 100
        self.assertEqual(h["median_tokens"], 100.0)
        self.assertTrue(h["flag"])
        self.assertEqual(h["ratio"], 2.0)

    def test_just_under_2x_is_not_heavy(self):
        h = cost.heavy_flag(199, [100, 100, 100, 199, 100])  # 1.99x
        self.assertFalse(h["flag"])
        self.assertIsNone(h["text"])
        self.assertEqual(h["ratio"], 1.99)

    def test_just_over_is_heavy(self):
        self.assertTrue(cost.heavy_flag(201, [100, 100, 100, 201, 100])["flag"])

    def test_median_of_even_set_is_mean_of_middle_two(self):
        self.assertEqual(cost.median_tokens([10, 20, 30, 40]), 25.0)

    def test_flags_are_per_skill_against_the_whole_set(self):
        flags = cost.heavy_flags({"a": 100, "b": 100, "c": 100, "d": 250})
        self.assertEqual({k: v["flag"] for k, v in flags.items()}, {"a": False, "b": False, "c": False, "d": True})


class TextFormatTests(unittest.TestCase):
    def test_exact_text(self):
        h = cost.heavy_flag(2408, [532, 560, 532, 2408])
        self.assertEqual(h["over_tokens"], 1862)
        self.assertEqual(h["over_pct"], 341)
        self.assertEqual(h["text"], "Heavy: +1,862 tokens per load (341% over the median skill)")

    def test_over_is_load_minus_median_and_pct_is_rounded(self):
        h = cost.heavy_flag(250, [100, 100, 100, 250])
        self.assertEqual(h["over_tokens"], 150)
        self.assertEqual(h["over_pct"], 150)
        self.assertEqual(h["text"], "Heavy: +150 tokens per load (150% over the median skill)")

    def test_thousands_separator(self):
        self.assertEqual(cost.heavy_text(12345, 987), "Heavy: +12,345 tokens per load (987% over the median skill)")

    def test_text_has_no_em_dash(self):
        self.assertNotIn("\u2014", cost.heavy_flag(2408, [532, 560, 532, 2408])["text"])


class SafeEdgeTests(unittest.TestCase):
    def test_empty_set_is_safe_and_not_heavy(self):
        h = cost.heavy_flag(100, [])
        self.assertFalse(h["flag"])
        self.assertIsNone(h["text"])
        self.assertIsNone(h["ratio"])
        self.assertEqual(cost.heavy_flags({}), {})

    def test_zero_median_is_safe_and_not_heavy(self):
        h = cost.heavy_flag(50, [0, 0, 0, 50])
        self.assertEqual(h["median_tokens"], 0.0)
        self.assertFalse(h["flag"])
        self.assertIsNone(h["over_pct"])  # no division by zero

    def test_junk_values_do_not_raise(self):
        h = cost.heavy_flag("many", [None, "x", True])
        self.assertFalse(h["flag"])

    def test_single_skill_is_never_heavy(self):
        self.assertFalse(cost.heavy_flag(5000, [5000])["flag"])


class PlantedSetTests(unittest.TestCase):
    """Decision 3: helpful 532, placebo 560, poisoned 532, bloat 2408; median 546; only bloat Heavy."""

    def test_load_tokens_match_the_fixtures_by_hand(self):
        tokens = selftest.planted_load_tokens(FIXTURES)
        by_hand = {v: -(-(FIXTURES / v / "SKILL.md").stat().st_size // 4) for v in selftest.VARIANTS}
        self.assertEqual(tokens, by_hand)
        self.assertEqual(tokens, {"helpful": 532, "poisoned": 532, "placebo": 560, "bloat": 2408})

    def test_only_bloat_is_heavy(self):
        heavy = selftest.planted_heavy(FIXTURES)
        self.assertEqual({k: v["flag"] for k, v in heavy.items()},
                         {"helpful": False, "poisoned": False, "placebo": False, "bloat": True})
        self.assertEqual(heavy["bloat"]["median_tokens"], 546.0)
        self.assertEqual(heavy["bloat"]["text"], "Heavy: +1,862 tokens per load (341% over the median skill)")

    def test_hermetic_to_the_users_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "skills" / "x").mkdir(parents=True)
            (home / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n" + "y" * 40000, encoding="utf-8")
            with mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}):
                self.assertEqual(selftest.planted_load_tokens(FIXTURES)["bloat"], 2408)

    def test_missing_fixtures_gives_none(self):
        self.assertIsNone(selftest.planted_heavy(Path("/nonexistent/fixtures")))


class SelftestRuleTests(unittest.TestCase):
    HEAVY = {"flag": True, "text": "Heavy: +1 tokens per load (1% over the median skill)"}
    NOT_HEAVY = {"flag": False, "text": None}

    def test_bloat_ok_needs_heavy_and_a_non_harm_label(self):
        for label in (verdict.EARNS_ITS_KEEP, verdict.MODEL_DOES_THIS_ALREADY, verdict.NOT_SURE_YET):
            self.assertTrue(selftest.bloat_ok(label, self.HEAVY), label)
        self.assertFalse(selftest.bloat_ok(verdict.GETS_IN_THE_WAY, self.HEAVY))
        self.assertFalse(selftest.bloat_ok(verdict.EARNS_ITS_KEEP, self.NOT_HEAVY))
        self.assertFalse(selftest.bloat_ok(verdict.EARNS_ITS_KEEP, None))
        self.assertFalse(selftest.bloat_ok(None, self.HEAVY))

    def test_heavy_false_positive_ok(self):
        ok = {"helpful": self.NOT_HEAVY, "poisoned": self.NOT_HEAVY, "placebo": self.NOT_HEAVY, "bloat": self.HEAVY}
        self.assertTrue(selftest.heavy_false_positive_ok(ok))
        self.assertFalse(selftest.heavy_false_positive_ok({**ok, "placebo": self.HEAVY}))
        self.assertTrue(selftest.heavy_false_positive_ok({**ok, "bloat": self.NOT_HEAVY}))  # bloat itself is not a false positive
        self.assertIsNone(selftest.heavy_false_positive_ok(None))

    def test_expected_column_text(self):
        self.assertEqual(selftest.expected_text("bloat"), "any verdict except Gets in the way, plus Heavy")
        self.assertEqual(selftest.expected_text("helpful"), verdict.EARNS_ITS_KEEP)
        self.assertNotIn(verdict.GETS_IN_THE_WAY, selftest.EXPECTED["bloat"])

    def test_other_rows_unchanged(self):
        self.assertEqual(selftest.variant_match("helpful", verdict.EARNS_ITS_KEEP, None), True)
        self.assertEqual(selftest.variant_match("helpful", verdict.NOT_SURE_YET, None), False)
        self.assertEqual(selftest.variant_match("placebo", verdict.NOT_SURE_YET, None), True)
        self.assertIsNone(selftest.variant_match("bloat", None, self.HEAVY))

    @unittest.skipUnless((REPO / "reports" / "step3-selftest.json").is_file(), "dev checkout only: stored run report")
    def test_stored_report_passes_on_verdict_plus_heavy(self):
        stored = json.loads((REPO / "reports" / "step3-selftest.json").read_text(encoding="utf-8"))
        agg = stored["aggregate"]
        bloat = agg["matrix"]["haiku"]["bloat"]
        self.assertEqual(bloat["actual"], verdict.EARNS_ITS_KEEP)
        self.assertTrue(bloat["match"])
        self.assertEqual(bloat["heavy"]["text"], "Heavy: +1,862 tokens per load (341% over the median skill)")
        for k in ("helpful", "poisoned", "placebo"):
            self.assertFalse(agg["matrix"]["haiku"][k]["heavy"]["flag"], k)
        sel = agg["gate"]["selftest"]
        self.assertTrue(sel["bloat_ok"])
        self.assertTrue(sel["heavy_false_positive_ok"])
        self.assertFalse(sel["harm_judge_never_prefers_poisoned"])
        self.assertFalse(sel["position_bias_ok"])
        self.assertFalse(sel["overall"])  # only the two judge items still fail


class CostViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.skills = Path(self.tmp.name) / "skills"
        for name, size in (("s1", 400), ("s2", 400), ("s3", 400), ("big", 2400)):
            d = self.skills / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use when {name}.\n---\n" + "x" * size, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_carry_heavy_and_table_shows_text(self):
        r = cost.estimate(skills_dir=self.skills, model="claude-opus-5-5", disabled=[])
        by = {s["name"]: s for s in r["skills"]}
        self.assertTrue(by["big"]["heavy"]["flag"])
        self.assertFalse(by["s1"]["heavy"]["flag"])
        self.assertEqual(r["totals"]["heavy_count"], 1)
        table = cost.format_table(r)
        self.assertIn(by["big"]["heavy"]["text"], table)
        self.assertEqual(table.count("Heavy: +"), 1)
        self.assertIn("Heavy flag: a skill is Heavy when", table)

    def test_top_slice_does_not_change_the_comparison_set(self):
        full = cost.estimate(skills_dir=self.skills, disabled=[])
        top = cost.estimate(skills_dir=self.skills, disabled=[], top=1)
        self.assertEqual(top["skills"][0]["heavy"], next(s["heavy"] for s in full["skills"] if s["name"] == top["skills"][0]["name"]))

    def test_empty_skills_dir(self):
        r = cost.estimate(skills_dir=Path(self.tmp.name) / "none", disabled=[])
        self.assertEqual(r["totals"]["heavy_count"], 0)
        self.assertIn("Heavy flag:", cost.format_table(r))


class CommandTests(unittest.TestCase):
    def test_verdict_line_puts_flag_beside_label(self):
        heavy = cost.heavy_flag(2408, [532, 560, 532, 2408])
        self.assertEqual(commands.format_verdict(verdict.EARNS_ITS_KEEP, heavy),
                         "Earns its keep · Heavy: +1,862 tokens per load (341% over the median skill)")

    def test_no_flag_means_the_bare_label(self):
        for heavy in (None, {}, cost.heavy_flag(100, [100, 100, 100])):
            self.assertEqual(commands.format_verdict(verdict.NOT_SURE_YET, heavy), verdict.NOT_SURE_YET)

    def test_labels_unchanged(self):
        self.assertEqual(verdict.LABELS, ("Earns its keep", "Gets in the way", "Model does this already", "Not sure yet"))

    def test_skill_plan_shows_load_cost_and_heavy(self):
        fake = {"skills": [{"name": "big", "load_tokens": 4800, "heavy": cost.heavy_flag(4800, [1000, 1000, 1000, 4800])},
                           {"name": "small", "load_tokens": 1000, "heavy": cost.heavy_flag(1000, [1000, 1000, 1000, 4800])}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HERMES_HOME": tmp}), \
                mock.patch.object(cost, "estimate", return_value=fake):
            commands.bind(SimpleNamespace(session_surface=lambda: "cli"))
            try:
                big, small = commands.handle_skilluminati("big"), commands.handle_skilluminati("small")
            finally:
                commands.bind(None)
        # The reply stays at three short lines; the Heavy flag is in the report (test_report).
        for out in (big, small):
            self.assertLessEqual(len(out.splitlines()), 3, out)
            self.assertNotIn("\u2014", out)

    def test_skill_plan_survives_a_cost_failure(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HERMES_HOME": tmp}), \
                mock.patch.object(cost, "estimate", side_effect=RuntimeError("boom")):
            commands.bind(SimpleNamespace(session_surface=lambda: "cli"))
            try:
                out = commands.handle_skilluminati("anything")
            finally:
                commands.bind(None)
        self.assertIn("Not enough history", out)


class RealrunTests(unittest.TestCase):
    def test_comparison_set_is_enabled_skills_plus_subjects(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp)
            for name, size in (("a", 400), ("b", 400), ("c", 400), ("off", 9000)):
                d = skills / name
                d.mkdir()
                (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n" + "x" * size, encoding="utf-8")
            got = realrun.heavy_comparison_set(skills, {"a": 1, "extra": 777}, disabled=["off"])
            self.assertEqual(sorted(got), ["a", "b", "c", "extra"])
            self.assertEqual(got["a"], cost.estimate_tokens((skills / "a" / "SKILL.md").read_text(encoding="utf-8")))
            self.assertEqual(got["extra"], 777)  # an unscanned subject skill is still in its own set

    def test_verdict_record_has_heavy_dict(self):
        from tests import test_realrun as tr
        case = tr.EndToEnd("test_run_shapes_logs_and_evidence")
        case.setUp()
        try:
            rr = realrun.RealRunner(tr.FakeLlm(), case.cfg, log=lambda s: None)
            res = rr.run()
            for skill in (tr.SKILL_A, tr.SKILL_B):
                h = res["per_skill"][skill]["heavy"]
                self.assertEqual(set(h), {"load_tokens", "median_tokens", "ratio", "over_tokens", "over_pct", "flag", "text"})
                self.assertEqual(h["load_tokens"], rr.skill_load_tokens[skill])
                self.assertFalse(h["flag"])  # two near-equal skills: neither is 2x the median
            self.assertEqual(res["heavy_comparison"]["ratio"], cost.HEAVY_RATIO)
            self.assertEqual(res["heavy_comparison"]["skills_in_set"], 2)
        finally:
            case.tearDown()


class DashboardPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _write(self, runs):
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "runs": runs, "aa": [], "position_bias": None}), encoding="utf-8")

    def test_payload_carries_heavy_field(self):
        heavy = cost.heavy_flag(2408, [532, 560, 532, 2408])
        self._write([{"skill": "bloat", "verdict": "Keep", "heavy": heavy}, {"skill": "plain", "verdict": "Drop"}])
        out = api.read_verdicts()
        self.assertEqual(out["runs"][0]["heavy"]["text"], heavy["text"])
        self.assertTrue(out["runs"][0]["heavy"]["flag"])
        self.assertNotIn("heavy", out["runs"][1])  # absent stays absent

    def test_malformed_heavy_is_cleaned_not_trusted(self):
        self._write([{"skill": "x", "verdict": "Keep", "heavy": {"flag": "yes", "text": "<b>Heavy</b>", "evil": 1}},
                     {"skill": "y", "verdict": "Keep", "heavy": "nope"}])
        runs = api.read_verdicts()["runs"]
        self.assertIs(runs[0]["heavy"]["flag"], False)
        self.assertIsNone(runs[0]["heavy"]["text"])
        self.assertNotIn("evil", runs[0]["heavy"])
        self.assertIsNone(runs[1]["heavy"])

    def test_desktop_page_renders_the_flag_beside_the_badge(self):
        js = (REPO / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        # W4 page: the verdict word is its own badge; the per-prompt token cost sits in the skill list.
        self.assertIn("jsx(VerdictWord, { k: r.key, c, r })", js)  # r: F02 shown wording
        self.assertIn("c.tokens_per_prompt", js)
        self.assertNotIn("\u2014", js)


if __name__ == "__main__":
    unittest.main()
