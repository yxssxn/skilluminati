"""In plain words: when the model being tested also judges its own answers, the result should say so.
These checks (V6, same-model part) raise the flag when subject and judge are the same model (a
trailing "[1m]" style suffix ignored), and when no judge was set (it falls back to the active
model), and keep it off for a judge from another model. The flag shows in the recommendation, the
report and the dashboard row.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests import test_run as R  # noqa: E402,F401  (puts plugin/ on sys.path)
from tests.test_ux_v2 import verdict_result  # noqa: E402

from skilluminati import copy as sb_copy, recommend as rec, report, verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("skilluminati_plugin_api_f07", ROOT / "plugin" / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

OPUS, OPUS_1M, GPT = "claude-opus-5-5", "claude-opus-5-5[1m]", "gpt-5.5"
EARNS = verdict.EARNS_ITS_KEEP


def keys(r):
    return [f["key"] for f in r["flags"]]


class SameJudgeRuleTests(unittest.TestCase):
    def test_same_model_raises_it(self):
        self.assertTrue(rec.same_judge(OPUS, OPUS))
        self.assertTrue(rec.same_judge(OPUS_1M, OPUS))
        self.assertTrue(rec.same_judge(" Claude-Opus-5-5 ", OPUS_1M))

    def test_different_models_do_not(self):
        self.assertFalse(rec.same_judge(OPUS, GPT))
        self.assertFalse(rec.same_judge(OPUS_1M, "claude-sonnet-5-5"))

    def test_missing_judge_falls_back_to_the_active_model_so_counts_as_same(self):
        self.assertTrue(rec.same_judge(OPUS, None))
        self.assertTrue(rec.same_judge(OPUS, ""))
        self.assertTrue(rec.same_judge(None, None))


class RecommendationFlagTests(unittest.TestCase):
    def build(self, **kw):
        return rec.recommend(EARNS, wins=8, losses=1, ties=3, verdict_model=OPUS, active_model=OPUS, **kw)

    def test_flag_in_the_flags_list(self):
        r = self.build(judge_model=OPUS_1M)
        self.assertIn(rec.FLAG_SAME_JUDGE, keys(r))
        text = next(f["text"] for f in r["flags"] if f["key"] == rec.FLAG_SAME_JUDGE)
        self.assertEqual(text, sb_copy.FLAG_SAME_JUDGE)
        self.assertIn("judge.provider", text)
        self.assertIn("judge.model", text)
        self.assertIn(sb_copy.FLAG_SAME_JUDGE, rec.format_text(r))

    def test_missing_judge_raises_it(self):
        self.assertIn(rec.FLAG_SAME_JUDGE, keys(self.build(judge_model=None)))

    def test_other_maker_does_not(self):
        self.assertNotIn(rec.FLAG_SAME_JUDGE, keys(self.build(judge_model=GPT)))

    def test_callers_that_do_not_say_get_no_flag(self):
        self.assertNotIn(rec.FLAG_SAME_JUDGE, keys(self.build()))

    def test_from_run_reads_the_stored_judge(self):
        row = {"skill": "s", "verdict": "Keep", "wins": 8, "losses": 1, "ties": 3, "model": OPUS_1M}
        self.assertIn(rec.FLAG_SAME_JUDGE, keys(rec.from_run({**row, "judge": OPUS})))
        self.assertIn(rec.FLAG_SAME_JUDGE, keys(rec.from_run(row)))
        self.assertNotIn(rec.FLAG_SAME_JUDGE, keys(rec.from_run({**row, "judge": GPT})))

    def test_clean_sets_it_from_the_row(self):
        stored = self.build(judge_model=GPT)
        self.assertIn(rec.FLAG_SAME_JUDGE, keys(rec.clean(stored, row={"model": OPUS, "judge": OPUS_1M})))
        flagged = self.build(judge_model=OPUS)
        self.assertNotIn(rec.FLAG_SAME_JUDGE, keys(rec.clean(flagged, row={"model": OPUS, "judge": GPT})))
        self.assertEqual(keys(rec.clean(flagged)), keys(flagged))  # no row: left as stored


class ReportTests(unittest.TestCase):
    def facts(self, subject, judge):
        res = verdict_result(label=EARNS, wins=8, losses=1, ties=3, n_next=None)
        res["subject_model"], res["judge_model"] = subject, judge
        return report._verdict_facts(res, None, "unslop")

    def test_report_shows_it_for_the_same_model(self):
        md = report.verdict_markdown(self.facts(OPUS_1M, OPUS))
        self.assertIn(sb_copy.FLAG_SAME_JUDGE + ".", md)

    def test_report_shows_it_once(self):
        res = verdict_result(label=EARNS, wins=8, losses=1, ties=3, n_next=None)
        res["subject_model"] = res["judge_model"] = OPUS
        res["per_skill"]["unslop"]["recommendation"]["flags"].append(
            {"key": rec.FLAG_SAME_JUDGE, "text": sb_copy.FLAG_SAME_JUDGE})
        md = report.verdict_markdown(report._verdict_facts(res, None, "unslop"))
        self.assertEqual(md.count(sb_copy.FLAG_SAME_JUDGE), 1)

    def test_report_leaves_it_off_for_another_maker(self):
        md = report.verdict_markdown(self.facts(OPUS, GPT))
        self.assertNotIn(sb_copy.FLAG_SAME_JUDGE, md)


class DashboardTests(unittest.TestCase):
    def home(self, tmp, judge):
        d = Path(tmp) / "plugin-data" / "skilluminati"
        d.mkdir(parents=True)
        row = {"skill": "unslop", "verdict": "Keep", "user_label": EARNS, "model": OPUS_1M, "date": "2026-11-12",
               "wins": 8, "losses": 1, "ties": 3, "low": 0.6, "high": 0.98}
        if judge is not ...:
            row["judge"] = judge
        (d / "verdicts.json").write_text(json.dumps({"schema": 1, "runs": [row]}))
        return Path(tmp)

    def test_page_row_and_verdicts_route(self):
        for judge, want in ((OPUS, True), (GPT, False), (..., True)):
            with tempfile.TemporaryDirectory() as tmp:
                home = self.home(tmp, judge)
                [row] = api.page_results(home)
                self.assertIs(row["same_judge"], want, judge)
                [run] = api.read_verdicts(home)["runs"]
                self.assertEqual(rec.FLAG_SAME_JUDGE in keys(run["recommendation"]), want, judge)

    def test_page_copy_and_js(self):
        page = sb_copy.page_copy()
        self.assertTrue(page["same_judge_tag"])
        self.assertEqual(page["same_judge_tip"], sb_copy.FLAG_SAME_JUDGE + ".")
        js = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("r.same_judge", js)
        self.assertIn("c.same_judge_tag", js)
        self.assertIn("c.same_judge_tip", js)


def _yaml_blocks(text):
    return [b.split("\n", 1)[1] for b in text.split("```")[1::2] if b.startswith("yaml\n")]


def _nested(block):
    """A tiny reader for the README's plain nested YAML maps (two-space indent, ``key: value``)."""
    root, stack = {}, [(-1, None)]
    stack[0] = (-1, root)
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        value = value.split(" #")[0].strip()
        if value:
            parent[key] = value
        else:
            parent[key] = {}
            stack.append((indent, parent[key]))
    return root


class ReadmeTests(unittest.TestCase):
    def test_readme_names_the_settings(self):
        text = (ROOT / "plugin" / "README.md").read_text(encoding="utf-8")
        self.assertIn("another maker", text)

    def test_readme_shows_the_nested_yaml_hermes_reads(self):
        # S11: Hermes splits the setting key on dots and walks nested maps under
        # plugins.entries.<id>.settings, so a flat "judge.provider:" key is never read.
        text = (ROOT / "plugin" / "README.md").read_text(encoding="utf-8")
        blocks = _yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("judge.provider:", text)
        settings = _nested(blocks[0])["plugins"]["entries"]["skilluminati"]["settings"]
        self.assertEqual(set(settings["judge"]), {"provider", "model"})
        self.assertEqual(set(settings["harm_judge"]), {"provider", "model"})


if __name__ == "__main__":
    unittest.main()
