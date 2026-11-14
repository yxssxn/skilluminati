"""In plain words: checks the advice Skilluminati gives after a test, and that the page shows it plainly.

recommend.py: confidence boundaries, each flag on and off, each next step, wiring into the command,
realrun record, dashboard payload and Desktop page, and no write path to a skills directory.
Stdlib only, no network. Samples at the bottom print one recommendation per headline."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

rec = importlib.import_module("plugin.skilluminati.recommend")
copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")
stats = importlib.import_module("plugin.skilluminati.stats")
cost = importlib.import_module("plugin.skilluminati.cost")
commands = importlib.import_module("plugin.skilluminati.commands")

_spec = importlib.util.spec_from_file_location("skilluminati_plugin_api_rec", ROOT / "plugin" / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

EARNS, ALREADY = verdict.EARNS_ITS_KEEP, verdict.MODEL_DOES_THIS_ALREADY
WAY, NOVICE = verdict.GETS_IN_THE_WAY, verdict.NOT_SURE_YET
HEAVY = cost.heavy_flag(2408, [532, 560, 532, 2408])
NOT_HEAVY = cost.heavy_flag(532, [532, 560, 532, 2408])


def conf(label, n, low, high, **kw):
    return rec.confidence(label, n, low, high, **kw)


def sample_recommendations():
    """One recommendation per headline, from fixed fixtures (no model calls)."""
    lo, hi = stats.wilson(18, 20)
    out = {EARNS: rec.recommend(EARNS, wins=18, losses=2, ties=3, low=lo, high=hi, heavy=HEAVY, tasks_to_settle=0,
                                verdict_model="claude-haiku-5-5", active_model="claude-haiku-5-5")}
    lo, hi = stats.wilson(11, 24)
    out[ALREADY] = rec.recommend(ALREADY, wins=11, losses=13, ties=6, low=lo, high=hi, heavy=NOT_HEAVY,
                                 verdict_model="claude-haiku-5-5", active_model="claude-haiku-5-5",
                                 job_first_share=0.52)
    lo, hi = stats.wilson(1, 9)
    out[WAY] = rec.recommend(WAY, wins=1, losses=8, ties=1, low=lo, high=hi, heavy=NOT_HEAVY, scripted_harm=True,
                             verdict_model="claude-haiku-5-5", active_model="claude-sonnet-5-5",
                             harm_flip_share=0.10)
    lo, hi = stats.wilson(3, 6)
    out[NOVICE] = rec.recommend(NOVICE, wins=3, losses=3, ties=1, low=lo, high=hi, tasks_to_settle=None,
                                verdict_model="claude-haiku-5-5", active_model="claude-haiku-5-5",
                                job_first_share=0.75)
    return out


class ConfidenceBoundaryTests(unittest.TestCase):
    def test_19_vs_20_tasks_for_strong(self):
        self.assertEqual(conf(EARNS, 19, 0.70, 0.99), rec.MODERATE)
        self.assertEqual(conf(EARNS, 20, 0.70, 0.99), rec.STRONG)

    def test_margin_exactly_0_10_off_half_is_strong(self):
        self.assertEqual(conf(EARNS, 20, 0.60, 0.95), rec.STRONG)   # low - 0.5 = 0.10
        self.assertEqual(conf(EARNS, 20, 0.5999, 0.95), rec.MODERATE)  # just under
        self.assertEqual(conf(WAY, 20, 0.05, 0.40), rec.STRONG)     # 0.5 - high = 0.10
        self.assertEqual(conf(WAY, 20, 0.05, 0.4001), rec.MODERATE)

    def test_moderate_needs_10_tasks_and_an_interval_off_half(self):
        self.assertEqual(conf(EARNS, 9, 0.60, 0.99), rec.WEAK)
        self.assertEqual(conf(EARNS, 10, 0.55, 0.99), rec.MODERATE)
        self.assertEqual(conf(EARNS, 10, 0.50, 0.99), rec.WEAK)  # touches 0.5: not excluded
        self.assertEqual(conf(WAY, 10, 0.01, 0.45), rec.MODERATE)

    def test_neutral_labels_use_interval_width(self):
        self.assertEqual(conf(ALREADY, 20, 0.35, 0.65), rec.STRONG)    # width 0.30
        self.assertEqual(conf(ALREADY, 20, 0.34, 0.65), rec.MODERATE)  # width 0.31, <= 0.45
        self.assertEqual(conf(NOVICE, 20, 0.30, 0.75), rec.MODERATE)   # width 0.45
        self.assertEqual(conf(NOVICE, 20, 0.29, 0.75), rec.WEAK)       # width 0.46
        self.assertEqual(conf(ALREADY, 19, 0.35, 0.65), rec.MODERATE)  # 19 tasks cannot be Strong

    def test_no_interval_is_weak(self):
        self.assertEqual(conf(NOVICE, 0, None, None), rec.WEAK)
        self.assertEqual(conf(EARNS, 30, None, None), rec.WEAK)

    def test_scripted_harm_is_at_least_moderate(self):
        self.assertEqual(conf(WAY, 4, 0.0, 0.6), rec.WEAK)
        self.assertEqual(conf(WAY, 4, 0.0, 0.6, scripted_harm=True), rec.MODERATE)
        self.assertEqual(conf(WAY, 25, 0.0, 0.2, scripted_harm=True), rec.STRONG)  # never lowered
        self.assertEqual(conf(EARNS, 4, 0.0, 0.6, scripted_harm=True), rec.WEAK)   # only for Gets in the way

    def test_real_wilson_intervals(self):
        low, high = stats.wilson(15, 20)
        self.assertEqual(conf(EARNS, 20, low, high), rec.MODERATE)  # 15/20 low ~0.53: excludes 0.5, margin < 0.10
        low, high = stats.wilson(19, 20)
        self.assertEqual(conf(EARNS, 20, low, high), rec.STRONG)


class FlagTests(unittest.TestCase):
    def flags(self, **over):
        base = dict(n_decisive=20, heavy=None, verdict_model="m1", active_model="m1",
                    harm_flip_share=None, job_first_share=None)
        base.update(over)
        return {f["key"]: f["text"] for f in rec.build_flags(**base)}

    def test_no_flags_on_a_clean_result(self):
        self.assertEqual(self.flags(), {})
        self.assertEqual(self.flags(heavy=NOT_HEAVY), {})

    def test_heavy_uses_the_cost_text(self):
        f = self.flags(heavy=HEAVY)
        self.assertEqual(f[rec.FLAG_HEAVY], "Heavy: +1,862 tokens per load (341% over the median skill)")

    def test_few_tasks_below_10(self):
        self.assertIn(rec.FLAG_FEW, self.flags(n_decisive=9))
        self.assertNotIn(rec.FLAG_FEW, self.flags(n_decisive=10))
        self.assertIn("9", self.flags(n_decisive=9)[rec.FLAG_FEW])

    def test_stale_reuses_commands_stale_label(self):
        self.assertIn(rec.FLAG_STALE, self.flags(verdict_model="claude-opus-5-5", active_model="claude-sonnet-5-5"))
        self.assertNotIn(rec.FLAG_STALE, self.flags(verdict_model="same", active_model="SAME"))
        self.assertNotIn(rec.FLAG_STALE, self.flags(verdict_model=None, active_model="x"))
        self.assertNotIn(rec.FLAG_STALE, self.flags(verdict_model="x", active_model="unknown model"))

    def test_judges_split_harm_flip(self):
        self.assertNotIn(rec.FLAG_SPLIT, self.flags(harm_flip_share=verdict.HARM_FLIP_MAX))   # at the line: ok
        self.assertIn(rec.FLAG_SPLIT, self.flags(harm_flip_share=verdict.HARM_FLIP_MAX + 0.01))

    def test_judges_split_job_first_share_band(self):
        for share in (0.40, 0.50, 0.60):
            self.assertNotIn(rec.FLAG_SPLIT, self.flags(job_first_share=share), share)
        for share in (0.39, 0.61, 0.0, 1.0):
            self.assertIn(rec.FLAG_SPLIT, self.flags(job_first_share=share), share)

    def test_unmeasured_is_not_a_split(self):
        self.assertFalse(rec.judges_split(None, None))

    def test_fixed_order(self):
        out = rec.build_flags(n_decisive=3, heavy=HEAVY, verdict_model="a", active_model="b",
                              harm_flip_share=0.9, job_first_share=0.9)
        self.assertEqual([f["key"] for f in out], [rec.FLAG_HEAVY, rec.FLAG_FEW, rec.FLAG_STALE, rec.FLAG_SPLIT])


class NextStepTests(unittest.TestCase):
    def step(self, label, level, heavy, settle=None):
        return rec.next_step(label, level, heavy, settle)

    def test_keep(self):
        s = self.step(EARNS, rec.STRONG, False)
        self.assertEqual((s["key"], s["text"]), (rec.STEP_KEEP, "Keep"))

    def test_consider_trimming(self):
        for label, level in ((EARNS, rec.MODERATE), (ALREADY, rec.STRONG)):
            s = self.step(label, level, True)
            self.assertEqual((s["key"], s["text"]), (rec.STEP_TRIM, "Consider trimming"), label)
        # F02: a Plausible "no difference" asks again first, Heavy or not.
        self.assertEqual(self.step(ALREADY, rec.MODERATE, True)["key"], rec.STEP_ASK_AGAIN)

    def test_consider_archiving(self):
        self.assertEqual(self.step(WAY, rec.STRONG, False)["text"], "Consider archiving")
        self.assertEqual(self.step(WAY, rec.STRONG, True)["key"], rec.STEP_ARCHIVE)  # harm beats Heavy
        self.assertEqual(self.step(ALREADY, rec.STRONG, False)["text"], "Consider archiving")
        # F02: archiving only after a Probable (Strong) result.
        self.assertEqual(self.step(WAY, rec.MODERATE, True)["key"], rec.STEP_ASK_AGAIN)

    def test_run_more_uses_the_settle_estimate(self):
        s = self.step(NOVICE, rec.WEAK, False, 30)
        self.assertEqual((s["key"], s["n"], s["text"]), (rec.STEP_RUN_MORE, 30, "Run about 30 more tasks"))
        self.assertEqual(self.step(NOVICE, rec.WEAK, False, 1)["text"], "Run about 1 more task", "singular for one")

    def test_run_more_defaults_to_10(self):
        for settle in (None, 0, -3, True):
            self.assertEqual(self.step(NOVICE, rec.WEAK, False, settle)["n"], 10, settle)

    def test_weak_confidence_asks_for_more_tasks_whatever_the_headline(self):
        self.assertEqual(self.step(EARNS, rec.WEAK, False, 12)["text"], "Run about 12 more tasks")
        self.assertEqual(self.step(WAY, rec.WEAK, True)["key"], rec.STEP_RUN_MORE)

    def test_novice_always_runs_more(self):
        self.assertEqual(self.step(NOVICE, rec.STRONG, True)["key"], rec.STEP_RUN_MORE)


class RecommendObjectTests(unittest.TestCase):
    def test_four_sample_recommendations(self):
        """One fixture per headline. ``print_samples`` shows them; here each is checked end to end."""
        for name, r in sample_recommendations().items():
            self.assertEqual(r["headline"]["label"], name)
            self.assertIn(r["confidence"]["level"], rec.CONFIDENCE_LEVELS)
            self.assertTrue(r["next_step"]["text"])
            if name == ALREADY and r["confidence"]["level"] != rec.STRONG:  # F02: not Dead weight on a lean
                self.assertEqual(r["headline"]["themed"], copy.UNCLEAR_WORD)
            else:
                self.assertEqual(r["headline"]["text"], copy.headline(name)[2])

    def test_shape(self):
        low, high = stats.wilson(18, 20)
        r = rec.recommend(EARNS, wins=18, losses=2, ties=3, low=low, high=high, heavy=HEAVY,
                          tasks_to_settle=0, verdict_model="a", active_model="a")
        self.assertEqual(r["headline"]["text"], "Enlightened. Answers are clearly better with it. Keep it.")
        self.assertEqual(r["confidence"]["level"], rec.STRONG)
        self.assertEqual(r["next_step"]["text"], "Consider trimming")
        self.assertEqual([f["key"] for f in r["flags"]], [rec.FLAG_HEAVY])
        self.assertEqual(r["numbers"]["n_decisive"], 20)
        self.assertEqual(r["numbers"]["ties"], 3)
        json.dumps(r)  # plain data

    def test_unknown_label_raises(self):
        with self.assertRaises(ValueError):
            rec.recommend("Keep")

    def test_from_cell_scripted_harm(self):
        pairs = [verdict.PairEvidence(pair_id=f"p{i}", job="loss", scripted_harm=True, harm_plain=False) for i in range(6)]
        cell = verdict.cell_verdict(pairs)
        r = rec.from_cell(cell)
        self.assertEqual(r["headline"]["label"], WAY)
        self.assertGreaterEqual(["Weak", "Moderate", "Strong"].index(r["confidence"]["level"]), 1)
        self.assertTrue(r["numbers"]["scripted_harm"])
        self.assertEqual(rec.from_cell(cell.to_dict())["headline"]["text"], r["headline"]["text"])

    def test_from_run_labels(self):
        base = {"skill": "s", "model": "m", "wins": 9, "losses": 1, "ties": 0, "low": 0.6, "high": 0.97}
        self.assertEqual(rec.from_run({**base, "verdict": "Keep"})["headline"]["label"], EARNS)
        self.assertEqual(rec.from_run({**base, "verdict": "Unsure"})["headline"]["label"], NOVICE)
        self.assertEqual(rec.from_run({**base, "verdict": "Drop", "effect": "negative"})["headline"]["label"], WAY)
        self.assertEqual(rec.from_run({**base, "verdict": "Drop", "effect": "none"})["headline"]["label"], ALREADY)
        self.assertEqual(rec.from_run({**base, "user_label": ALREADY})["headline"]["label"], ALREADY)
        self.assertIsNone(rec.from_run({**base, "verdict": "Drop"}))  # a bare Drop is ambiguous: not guessed
        self.assertIsNone(rec.from_run({**base, "verdict": "outside replay scope"}))
        self.assertIsNone(rec.from_run({"verdict": "Keep"}))

    def test_format_text_has_all_four_parts(self):
        low, high = stats.wilson(18, 20)
        text = rec.format_text(rec.recommend(EARNS, wins=18, losses=2, low=low, high=high, heavy=HEAVY))
        self.assertIn("Enlightened. Answers are clearly better with it. Keep it.", text)
        self.assertIn("Confidence: Probable", text)
        self.assertIn("Flags: Heavy: +1,862", text)
        self.assertIn("Next step: Consider trimming", text)
        self.assertNotIn("\u2014", text)

    def test_format_text_says_none_when_no_flags(self):
        low, high = stats.wilson(18, 20)
        text = rec.format_text(rec.recommend(EARNS, wins=18, losses=2, low=low, high=high))
        self.assertIn("Flags: none", text)
        self.assertIn("Next step: Keep", text)


class WiringTests(unittest.TestCase):
    def test_command_format_recommendation(self):
        r = rec.recommend(EARNS, wins=18, losses=2, low=0.7, high=0.97)
        self.assertEqual(commands.format_recommendation(r), rec.format_text(r))
        self.assertEqual(commands.format_recommendation(None), "")
        self.assertEqual(commands.format_recommendation({"broken": 1}), "")  # never raises

    def test_help_text_lists_the_themed_headlines(self):
        out = commands.handle_skilluminati("help")
        self.assertIn("/skilluminati selftest", out)  # W1: short help, verdict words are on the page
        self.assertIn("Skilluminati page", out)

    def test_realrun_summary_carries_a_recommendation(self):
        realrun = importlib.import_module("plugin.skilluminati.realrun")
        shell = realrun.RealRunner.__new__(realrun.RealRunner)
        shell.skill_sha256 = {}  # set by __init__
        shell.skill_paths = {"s": "/x"}
        shell.skill_redaction = {"s": {}}
        shell.purposes = {"s": "purpose"}
        shell.skill_load_tokens = {"s": 2408}
        shell.heavy_set = {"a": 532, "b": 560, "c": 532, "s": 2408}
        shell.guard_notes = {}
        shell.cfg = mock.Mock(subject_model="m1", judge_model="j", judge_mode="x", workers=1)

        def pair(i, outcome):
            return {"outcome": outcome, "mode": "strict", "task_hash": f"h{i}", "cell": "s", "skill": "s",
                    "judge": {"first_picks": 1, "decisive_judgements": 2, "parse_failures": 0, "call_failures": 0,
                              "agreed": True, "orders": [{"raw_winner": "a"}, {"raw_winner": "b"}]}, "arms": {}}
        pairs = [pair(i, "win") for i in range(12)]
        calls = [{"role": "subject", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "latency_s": 1.0,
                  "cache_read_tokens": 0, "model": "m1", "ok": True, "error_type": None}]
        with mock.patch.object(realrun.sb_runner, "totals", return_value={"minutes": 1.0}):
            out = realrun.RealRunner.summarise_skill(shell, "s", pairs, 1.0, calls)
        self.assertEqual(out["user_label"], EARNS)  # old fields kept
        self.assertIn("heavy", out)
        r = out["recommendation"]
        self.assertEqual(r["headline"]["text"], "Enlightened. Answers are clearly better with it. Keep it.")
        self.assertEqual(r["next_step"]["text"], "Consider trimming")
        self.assertEqual(r["numbers"]["job_first_share"], 0.5)


class DashboardPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _write(self, runs, aa=None):
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "runs": runs, "aa": aa or [], "position_bias": None}), encoding="utf-8")

    def test_payload_carries_copy_and_recommendation(self):
        self._write([{"skill": "a", "model": "m", "verdict": "Keep", "wins": 18, "losses": 2, "ties": 1,
                      "low": 0.7, "high": 0.97, "tasks_to_settle": 0}])
        out = api.read_verdicts()
        r = out["runs"][0]["recommendation"]
        self.assertEqual(r["headline"]["text"], "Enlightened. Answers are clearly better with it. Keep it.")
        self.assertEqual(r["headline"]["tone"], "success")
        self.assertEqual(r["next_step"]["text"], "Keep")
        self.assertEqual(out["copy"]["judge"], "the Owl")
        self.assertEqual(out["copy"]["ui"]["next_step"], "Next step")
        self.assertIn("Minerva", out["copy"]["concept"]["quote"])

    def test_stored_recommendation_is_whitelisted(self):
        stored = rec.recommend(NOVICE, wins=5, losses=5, low=0.24, high=0.76)
        stored["evil"] = "<script>"
        stored["headline"]["extra"] = 1
        self._write([{"skill": "a", "verdict": "Unsure", "wins": 5, "losses": 5, "recommendation": stored}])
        r = api.read_verdicts()["runs"][0]["recommendation"]
        self.assertNotIn("evil", r)
        self.assertNotIn("extra", r["headline"])
        self.assertEqual(r["headline"]["text"], "Unproven. Too few uses to tell yet.")

    def test_malformed_and_unlabelled_rows_get_none_not_a_guess(self):
        self._write([{"skill": "a", "verdict": "Drop", "wins": 1, "losses": 9, "recommendation": "nope"},
                     {"skill": "b", "verdict": "outside replay scope"}])
        runs = api.read_verdicts()["runs"]
        self.assertIsNone(runs[0]["recommendation"])
        self.assertIsNone(runs[1]["recommendation"])

    def test_empty_and_error_payloads_still_carry_copy(self):
        self.assertEqual(api.read_verdicts()["copy"]["judge"], "the Owl")
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{bad", encoding="utf-8")
        out = api.read_verdicts()
        self.assertIn("error", out)
        self.assertIn("copy", out)


class DesktopPageTests(unittest.TestCase):
    JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")

    def test_page_renders_the_four_parts_from_the_payload(self):
        # W4 page: verdict word, one plain line, the working behind a disclosure; words from the payload.
        for needle in ("function VerdictWord", "c.legend_tips", "c.verdict_meaning", "c.show_working",
                       "function Working", "d.copy"):
            self.assertIn(needle, self.JS)

    def test_flags_are_chips(self):
        self.assertIn("c.verdict_tones", self.JS)
        self.assertIn("TONES.has(tone)", self.JS)

    def test_no_owl_strings_and_no_em_dash_in_the_page(self):
        for word in ("Illuminated", "Obscures", "Still watching", "Already known", "Earns its keep", "the Owl"):
            self.assertNotIn(word, self.JS)
        self.assertNotIn("\u2014", self.JS)

    def test_page_only_reads(self):
        self.assertNotIn("method: 'POST'", self.JS)
        self.assertNotIn("PUT", self.JS)


class NoSkillsWriteTests(unittest.TestCase):
    """The tool advises; it never deletes or edits a skill. No write call may appear in these modules."""

    WRITE = re.compile(r"\.write_text\(|\.write_bytes\(|open\([^)]*['\"][wax]\+?b?['\"]|\bos\.remove\(|\bos\.unlink\(|\.unlink\(|"
                       r"\bshutil\.(rmtree|move|copy\w*)\(|\.rmdir\(|\.rename\(|\.replace\(\s*Path|\bos\.rename\(|\.mkdir\(|\.touch\(")

    def test_no_write_calls_in_recommend_or_copy(self):
        for name in ("recommend.py", "copy.py"):
            text = (ROOT / "plugin" / "skilluminati" / name).read_text(encoding="utf-8")
            code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
            self.assertIsNone(self.WRITE.search(code), name)

    def test_no_file_io_imports(self):
        for name in ("recommend.py", "copy.py"):
            text = (ROOT / "plugin" / "skilluminati" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"^\s*(import|from)\s+(shutil|os|pathlib|subprocess|tempfile)\b", name)

    def test_the_dashboard_api_stays_read_only(self):
        text = (ROOT / "plugin" / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
        self.assertIsNone(self.WRITE.search(text))


if __name__ == "__main__":
    unittest.main()
