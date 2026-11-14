"""W4: the Desktop page data and the page rules.

In plain words: these checks make sure the Skilluminati page can always be drawn. Old and new saved
results both turn into a plain verdict, a test that is running shows its progress, the skill list
is ordered by use, and the page code stays inside the Desktop plugin rules.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT / "plugin" / "dashboard"))

from skilluminati import copy, report  # noqa: E402
import plugin_api as api  # noqa: E402

JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")


class VerdictKeyTests(unittest.TestCase):
    def test_old_words_map_to_new_keys(self):
        cases = {"Keep": "helps", "Illuminated": "helps", "Cast out": "hurts", "Obscures": "hurts",
                 "Unsure": "not_enough", "Still watching": "not_enough", "Drop": "hurts"}
        for word, key in cases.items():
            self.assertEqual(report.verdict_key({"verdict": word}), key, word)

    def test_new_keys_pass_through_and_user_label_wins_over_internal(self):
        for key in report.VERDICT_KEYS:
            self.assertEqual(report.verdict_key({"verdict_key": key}), key)
        self.assertEqual(report.verdict_key({"verdict": "Unsure", "user_label": "Model does this already"}),
                         "no_difference")

    def test_unknown_or_junk_is_not_enough_and_never_raises(self):
        for row in ({}, {"verdict": 3}, {"verdict": None, "recommendation": "x"}, {"verdict": "???"}):
            self.assertEqual(report.verdict_key(row), "not_enough")

    def test_strength(self):
        self.assertEqual(report.verdict_strength({"verdict": "helps", "strength": "strong"}), "strong")
        self.assertEqual(report.verdict_strength({"verdict": "Keep", "recommendation": {"confidence": {"level": "Strong"}}}),
                         "strong")
        self.assertEqual(report.verdict_strength({"verdict": "Keep"}), "likely")
        self.assertIsNone(report.verdict_strength({"verdict": "Unsure", "strength": "strong"}))

    def test_every_key_has_page_words(self):
        for key in report.VERDICT_KEYS:
            for table in (copy.VERDICT_WORDS, copy.VERDICT_TONES, copy.VERDICT_MEANING):
                self.assertTrue(table[key].strip(), key)


class PageResultTests(unittest.TestCase):
    def test_old_row_without_run_folder(self):
        r = report.page_result({"skill": "unslop", "verdict": "Unsure", "wins": 4, "losses": 2, "ties": 3,
                                "low": 0.3, "high": 0.9032, "model": "m1", "date": "2026-11-11"})
        self.assertEqual((r["key"], r["n"], r["low"], r["high"], r["strength"]), ("not_enough", 9, 30, 90, None))
        self.assertEqual(r["working"]["tasks"], [])
        line = copy.RESULT_LINE.format(**r)
        self.assertEqual(line, "Better 4 · Same 3 · Worse 2")

    def test_row_without_skill_is_skipped(self):
        self.assertIsNone(report.page_result({"verdict": "Keep"}))

    def test_working_reads_the_run_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            d = runs / "20261111-221020-verdict-unslop"
            d.mkdir()
            pairs = [{"outcome": "win", "judge": {"agreed": True}}, {"outcome": "loss", "judge": {"agreed": False}},
                     {"outcome": "tie"}, {"outcome": "weird"}]
            (d / "result.json").write_text(json.dumps({
                "judge_model": "j1",
                "per_skill": {"unslop": {"pairs": pairs, "combined": {"wins": 1, "losses": 1, "ties": 1}}},
                "aa": {"wins": 2, "losses": 4, "ties": 3},
                "position_bias": {"flagged": True, "share_of_decisive": 0.63},
                "totals": {"calls": 72, "total_tokens": 846705}}))
            r = report.page_result({"skill": "unslop", "verdict_key": "helps", "wins": 1, "losses": 1, "ties": 1},
                                   runs)
            other = report.page_result({"skill": "unslop", "verdict": "Keep", "wins": 9, "losses": 0, "ties": 0}, runs)
        self.assertIsNone(other["working"]["run"], "a run with a different score is not this verdict's working")
        w = r["working"]
        self.assertEqual(w["tasks"], ["win", "loss", "tie", "error"])
        self.assertEqual(w["order"], {"flips": 1, "n": 4})
        self.assertEqual((w["bias_pct"], w["calls"], w["tokens"], w["judge"]), (63, 72, 846705, "j1"))
        self.assertEqual(w["noise"], {"firm": 6, "n": 9, "weak": True,
                                      "lines": [copy.SAME_VS_SAME.format(firm=6, n=9, pct=67), copy.SAME_VS_SAME_WEAK]})
        self.assertEqual(w["run"], "20261111-221020-verdict-unslop")


class RunningTests(unittest.TestCase):
    def test_progress_from_plan_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            done = runs / "20261111-100000-verdict-old"
            done.mkdir()
            (done / "result.json").write_text("{}")
            d = runs / "20261111-230000-verdict-github"
            d.mkdir()
            (d / "plan.json").write_text(json.dumps({"per_skill": {"github": {"tasks": 9}}, "aa": {"tasks": 9}}))
            (d / "progress.log").write_text("[skill] github\n  github strict ab: win\n  github strict cd: tie\n")
            (d / "calls.jsonl").write_text("{}\n")
            got = report.running(runs)
            self.assertEqual(got, {"run": d.name, "skill": "github", "done": 2, "total": 18})
            self.assertIsNone(report.running(runs, now=time.time() + 3600), "a stale folder is not running")

    def test_no_plan_yet_means_unknown_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "20261111-230000-verdict-github"
            d.mkdir()
            (d / "calls.jsonl").write_text("{}\n")
            got = report.running(Path(tmp))
            self.assertEqual((got["done"], got["total"]), (None, None))

    def test_a_start_that_failed_is_not_running(self):
        """Live test 12 Nov: a run that failed before its first call showed as running for 20 min."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "20261112-022743-verdict-systematic-debugging").mkdir()
            self.assertIsNone(report.running(Path(tmp)))

    def test_live_file_wins_and_matches_the_popup(self):
        """Live test 12 Nov: page said 0 of 16 while the popup said 11 of 16. Both read one source now."""
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            runs.mkdir()
            (Path(tmp) / "live.json").write_text(json.dumps({
                "skill": "systematic-debugging", "session_id": "s1", "done": 11, "total": 16,
                "line": "The Owl is weighing the evidence. 11 of 16 compared.", "updated": time.time()}))
            got = report.running(runs)
            self.assertEqual((got["skill"], got["done"], got["total"], got["session_id"]),
                             ("systematic-debugging", 11, 16, "s1"))
            self.assertIn("11 of 16", got["line"])
            self.assertIsNone(report.running(runs, now=time.time() + 120), "an old live file means the run is gone")

    def test_missing_runs_dir(self):
        self.assertIsNone(report.running(Path("/nonexistent/skilluminati/runs")))


class PagePayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        skills = self.home / "skills"
        for name in ("alpha", "beta", "gamma"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: does {name}\n---\nBody {name}\n")
        (skills / ".usage.json").write_text(json.dumps({"beta": {"use_count": 9}, "gamma": {"use_count": 2},
                                                         "junk": "x"}))
        data = self.home / "plugin-data" / "skilluminati"
        data.mkdir(parents=True)
        (data / "verdicts.json").write_text(json.dumps({"schema": 1, "runs": [
            {"skill": "beta", "verdict": "Keep", "wins": 9, "losses": 1, "ties": 2, "date": "2026-11-10"},
            {"skill": "beta", "verdict_key": "hurts", "strength": "strong", "wins": 1, "losses": 9, "ties": 2,
             "date": "2026-11-11"},
            {"skill": "gamma", "verdict": "Obscures", "date": "2026-11-09"},
            "junk", {"verdict": "Keep"}], "aa": [{"model": None, "wins": 1}]}))
        api._skills_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_payload(self):
        out = api.read_page(self.home)
        self.assertEqual([s["name"] for s in out["skills"]], ["beta", "gamma", "alpha"], "most used first")
        self.assertEqual([s["uses"] for s in out["skills"]], [9, 2, 0])
        self.assertTrue(all(s["prompt_tokens"] > 0 for s in out["skills"]))
        self.assertEqual([(r["skill"], r["key"]) for r in out["results"]], [("beta", "hurts"), ("gamma", "hurts")])
        self.assertIsNone(out["running"])
        self.assertEqual(out["copy"]["page_empty"], copy.PAGE_EMPTY)
        json.dumps(out)

    def test_empty_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = api.read_page(Path(tmp))
        self.assertEqual((out["skills"], out["results"], out["running"]), ([], [], None))

    def test_corrupt_verdicts_is_empty_results(self):
        (self.home / "plugin-data" / "skilluminati" / "verdicts.json").write_text("{nope")
        self.assertEqual(api.read_page(self.home)["results"], [])


class PageRuleTests(unittest.TestCase):
    def test_imports_only_sdk_and_jsx_runtime(self):
        imports = [l for l in JS.splitlines() if l.startswith("import ")]
        self.assertEqual(len(imports), 2)
        self.assertIn("from '@hermes/plugin-sdk'", imports[0])
        self.assertIn("from 'react/jsx-runtime'", imports[1])

    def test_stays_inside_the_sdk(self):
        for bad in ("document.", "window.", "eval(", "new Function", ".prototype", "globalThis"):
            self.assertNotIn(bad, JS, bad)

    def test_book_icon_in_the_sidebar(self):
        self.assertIn("codicon: 'book'", JS)

    def test_no_jargon_or_hard_coded_verdict_words(self):
        for word in ("A/A", "Wilson", "placebo", "Helps", "Hurts", "Not enough", "decisive"):
            self.assertNotIn(word, JS, word)
        self.assertNotIn("\u2014", JS)

    def test_test_button_starts_in_a_new_chat_or_copies(self):
        self.assertIn("host.newChat()", JS)
        self.assertIn("host.composer.submit('new', command)", JS)
        self.assertIn("c.test_copied", JS)

    def test_one_table_one_test_button(self):
        self.assertEqual(JS.count("jsx(TestButton"), 1, "cards and table merged: one Test button per row")
        self.assertIn("function tableRows", JS)
        self.assertNotIn("c.results_title", JS)

    def test_summary_and_strength_words_cover_every_key(self):
        for key in report.VERDICT_KEYS:
            self.assertEqual(len(copy.SUMMARY_PARTS[key]), 2, key)
        self.assertEqual(set(copy.STRENGTH_WORDS), set(copy.STRENGTH_TIPS))
        self.assertIn("with it and without it", copy.PAGE_WHAT, "the header sentence says what the test does")

    def test_copy_has_every_key_the_page_reads(self):
        page = copy.page_copy()
        import re
        for key in set(re.findall(r"\bc\.([a-z_]+)", JS)):
            self.assertIn(key, page, key)


if __name__ == "__main__":
    unittest.main()
