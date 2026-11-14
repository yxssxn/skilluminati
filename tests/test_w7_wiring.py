"""W7: the four speed-run parts wired together.

In plain words: these checks make sure a real run uses your latest uses with the chat as it stood,
says how many it found, gives Opus room to answer, and that the page gets the lean verdict, its
strength, the counts and the run id under fixed names.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
sys.path.insert(0, str(ROOT / "plugin" / "dashboard"))

from skilluminati import llm as sb_llm, realrun, report, run  # noqa: E402
import plugin_api as api  # noqa: E402

LEAN_ROW = {"skill": "unslop", "verdict": "Unsure", "user_label": "Not sure yet", "wins": 8, "losses": 1, "ties": 3,
            "date": "2026-11-12", "run": "20261112-001000-verdict-unslop",
            "counts": {"uses": 939, "eligible": 587, "tested": 12},
            "recommendation": {"lean": {"verdict": "helps", "strength": "likely"}}}


class PageKeysTests(unittest.TestCase):
    def test_lean_wins_over_old_words_and_counts_and_run_come_through(self):
        r = report.page_result(LEAN_ROW)
        self.assertEqual((r["key"], r["strength"]), ("helps", "likely"))
        self.assertEqual((r["uses"], r["eligible"], r["tested"]), (939, 587, 12))

    def test_run_folder_is_found_by_its_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / LEAN_ROW["run"]
            d.mkdir()
            (d / "result.json").write_text(json.dumps({"per_skill": {"unslop": {"combined": {"wins": 0}}}}))
            r = report.page_result(LEAN_ROW, Path(tmp))
            self.assertEqual(r["run"], LEAN_ROW["run"])

    def test_api_keeps_run_and_counts(self):
        out = api._clean_run({**LEAN_ROW, "counts": {**LEAN_ROW["counts"], "secret": "x"}})
        self.assertEqual(out["run"], LEAN_ROW["run"])
        self.assertEqual(out["counts"], {"uses": 939, "eligible": 587, "tested": 12})


class RunWiringTests(unittest.TestCase):
    def test_plan_line_reports_real_counts(self):
        pl = {"skill": "unslop", "source": "history", "tasks": [{}] * 12, "minutes": 9,
              "counts": {"eligible": 586}}
        self.assertEqual(run.plan_line(pl), "Bringing unslop before the Owl: testing its last 12 uses, with and without. "
                                            "About 9 min.")

    def test_progress_keeps_the_plan_line_until_the_first_comparison(self):
        plan = "Bringing unslop before the Owl: testing its last 12 uses, with and without. About 9 min."
        self.assertEqual(run.popup_progress(3, 64, plan), plan)
        self.assertEqual(run.popup_progress(8, 64, plan), "The Owl is weighing the evidence. 2 of 16 compared.")

    def test_minutes_come_from_calls_times_seconds(self):
        per = run.SECONDS_PER_CALL["verdict"] + run.CALL_PAUSE_S
        self.assertEqual(run.minutes_estimate(60, "verdict", 1), -(-int(60 * per) // 60))
        self.assertEqual(run.minutes_estimate(60, "verdict", 1, seconds_per_call=59.5), 60)

    def test_answers_have_room_and_thinking_does_not_count_as_runaway(self):
        self.assertGreaterEqual(realrun.RealRunConfig.subject_max_tokens, 4000)
        rec = SimpleNamespace(ok=True, text="a normal answer", output_tokens=3 * 6000)
        self.assertIsNone(sb_llm.answer_problem(rec, 6000))


if __name__ == "__main__":
    unittest.main()
