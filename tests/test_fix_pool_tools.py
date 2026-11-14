"""In plain words: a pooled result keeps the tool-driven flag (F05) and the same-model judge flag
(F07) (fix batch M3). Each pooled run stores its own tested-use, tool-driven and empty-answer
counts, the pool sums them, and the pooled recommendation is built with those counts and the judge.
Stdlib only, temp dirs, no model calls.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

evidence = importlib.import_module("plugin.skilluminati.evidence")
rec = importlib.import_module("plugin.skilluminati.recommend")
copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")
llm = importlib.import_module("plugin.skilluminati.llm")
paths = importlib.import_module("plugin.skilluminati.paths")
report = importlib.import_module("plugin.skilluminati.report")
run = importlib.import_module("plugin.skilluminati.run")

MODEL = "claude-opus-5-5[1m]"
OTHER = "gpt-6"
SHA = "a" * 64


def _pair(i, outcome="win", tool_driven=False, empty=False, hashed=True):
    arms = {"with": {"ok": not empty, "error_type": llm.EMPTY_ANSWER if empty else None},
            "without": {"ok": True, "error_type": None}}
    p = {"cell": "s", "outcome": "error" if empty else outcome, "tool_driven": tool_driven, "arms": arms}
    if hashed:
        p["task_hash"] = f"{i:012x}"
    return p


def _kanban_pairs(hashed_errors=True):
    """12 tested uses: 9 judged (4 went straight to tools) and 3 empty answers."""
    return ([_pair(i, "win", tool_driven=i < 4) for i in range(8)] + [_pair(8, "loss")]
            + [_pair(20 + i, empty=True, hashed=hashed_errors) for i in range(3)])


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": self.tmp.name, "HOME": self.tmp.name})
        self.env.start()
        self.data = mock.patch.object(paths, "_data_dir_override", Path(self.tmp.name) / "plugin-data")
        self.data.start()
        evidence.clear_cache()

    def tearDown(self):
        self.data.stop()
        self.env.stop()
        self.tmp.cleanup()

    def summary(self, pairs, run_id="r1"):
        return {"skill": "s", "skill_sha256": SHA, "pairs": pairs, "stamp": {"date": "2026-11-14"},
                "tools": rec.tool_driven_counts(pairs), "run": run_id}


class RunCountsStored(_Home):
    def test_each_pool_run_stores_its_tool_counts(self):
        s = self.summary(_kanban_pairs())
        evidence.pool_summary(s, model=MODEL, judge=OTHER, run="r1")
        entry = json.loads(evidence.pool_path("s").read_text())["pools"][0]["runs"][0]
        self.assertEqual(entry["tools"], {"tested": 12, "tool_driven": 4, "empty_answer": 3, "count": 7})

    def test_kanban_style_run_pooled_once_keeps_the_flag(self):
        # the run's own counts win even when its error pairs carry no task hash (so they are not pooled)
        s = self.summary(_kanban_pairs(hashed_errors=False))
        self.assertEqual(s["tools"]["count"], 7)
        pool = evidence.pool_summary(s, model=MODEL, judge=OTHER, run="r1")
        self.assertEqual((pool["tools"]["tested"], pool["tools"]["count"]), (12, 7))
        self.assertTrue(pool["tools"]["flag"])
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in pool["recommendation"]["flags"]])
        self.assertNotEqual(pool["recommendation"]["confidence"]["level"], rec.STRONG)

    def test_counts_sum_across_runs(self):
        evidence.pool_summary(self.summary(_kanban_pairs()), model=MODEL, judge=OTHER, run="r1")
        clean = [_pair(i) for i in range(12)]
        pool = evidence.pool_summary(self.summary(clean, "r2"), model=MODEL, judge=OTHER, run="r2")
        self.assertEqual((pool["tools"]["tested"], pool["tools"]["tool_driven"], pool["tools"]["empty_answer"],
                          pool["tools"]["count"]), (24, 4, 3, 7))
        self.assertFalse(pool["tools"]["flag"])  # 7 of 24

    def test_same_run_twice_is_counted_once(self):
        s = self.summary(_kanban_pairs())
        evidence.pool_summary(s, model=MODEL, judge=OTHER, run="r1")
        pool = evidence.pool_summary(s, model=MODEL, judge=OTHER, run="r1")
        self.assertEqual(pool["tools"]["tested"], 12)

    def test_pool_runs_saved_without_counts_fall_back_to_pair_marks(self):
        p = evidence.pool_path("s")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "skill": "s", "pools": [
            {"skill_sha256": SHA, "model": MODEL, "judge": OTHER, "runs": [
                {"run": "old", "date": "2026-11-13", "pairs": [
                    {"task_hash": f"{i:012x}", "outcome": "win", "tool_driven": i < 6, "empty_answer": False}
                    for i in range(6)]}]}]}))
        pool = evidence.pool_summary(self.summary([_pair(i) for i in range(6)], "r2"), model=MODEL,
                                     judge=OTHER, run="r2")
        self.assertEqual((pool["tools"]["tested"], pool["tools"]["count"]), (12, 6))
        self.assertTrue(pool["tools"]["flag"])

    def test_same_model_judge_flag_survives_pooling(self):
        for judge in (MODEL, "anthropic/claude-opus-5-5", "claude-opus-5-5-20261001"):
            with self.subTest(judge=judge):
                pool = evidence.pool_summary(self.summary(_kanban_pairs()), model=MODEL, judge=judge, run="r1")
                self.assertIn(rec.FLAG_SAME_JUDGE, [f["key"] for f in pool["recommendation"]["flags"]])
                evidence.pool_path("s").unlink()
        pool = evidence.pool_summary(self.summary(_kanban_pairs()), model=MODEL, judge=OTHER, run="r1")
        self.assertNotIn(rec.FLAG_SAME_JUDGE, [f["key"] for f in pool["recommendation"]["flags"]])


class FirstRunEndToEnd(_Home):
    """A first run that is tool-driven stores the held strength in the row, chat line and report."""

    def _finish(self, pairs):
        from tests.test_f05_tool_driven import _summarise
        s = _summarise(pairs)
        s["skill_sha256"] = SHA
        s["run"] = "20261114-000000-verdict-s"
        pool = evidence.pool_summary(s, model=MODEL, judge=OTHER, run=s["run"])
        s["pool"] = pool
        folder = Path(self.tmp.name) / "plugin-data" / "runs" / s["run"]
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps({"per_skill": {"s": s}, "subject_model": MODEL,
                                                        "judge_model": OTHER}), encoding="utf-8")
        run.store_verdict(dict(evidence.effective(s)), model=MODEL, judge=OTHER)
        rows = json.loads(paths.verdicts_path().read_text())["runs"]
        return folder, rows[0]

    def test_row_chat_line_and_report_hold_plausible(self):
        from tests.test_f05_tool_driven import _full_pair
        pairs = ([_full_pair(i, "win", tool_driven=i < 14) for i in range(20)]
                 + [_full_pair(20 + i, "loss") for i in range(3)])
        folder, row = self._finish(pairs)
        self.assertEqual(row["recommendation"]["confidence"]["text"], "Plausible")
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in row["recommendation"]["flags"]])
        line = run.result_line(folder, "s")
        self.assertIn("(Plausible)", line)
        self.assertEqual(line.count(copy.FLAG_TOOL_DRIVEN), 1)
        text = report.terminal(folder)
        self.assertIn("s: Enlightened (Plausible).", text)
        self.assertIn(copy.FLAG_TOOL_DRIVEN, text)


if __name__ == "__main__":
    unittest.main()
