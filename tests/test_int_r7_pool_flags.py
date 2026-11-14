"""In plain words: a pooled result (F01) keeps the tool-driven flag (F05, ruling R7) and the
same-model judge flag (F07, ruling R4), and each shows once.

The pool keeps each pair's ``tool_driven`` and whether an arm came back empty, so the pooled result
applies the same rule as a single run: a tested use counts when its real session went straight to
tools or an answer came back empty, and the flag shows at half or more of the pooled uses. Errors
still never count toward the verdict. Stdlib only, temp dirs, no model calls.
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
report = importlib.import_module("plugin.skilluminati.report")
run = importlib.import_module("plugin.skilluminati.run")
paths = importlib.import_module("plugin.skilluminati.paths")

MODEL = "claude-opus-5-5"
OTHER = "gpt-6"


def _pair(i, outcome="win", tool_driven=False, empty=False):
    arms = {"with": {"ok": not empty, "error_type": llm.EMPTY_ANSWER if empty else None},
            "without": {"ok": True, "error_type": None}}
    return {"cell": "s", "task_hash": f"{i:012x}", "outcome": "error" if empty else outcome,
            "tool_driven": tool_driven, "arms": arms}


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"HERMES_HOME": self.tmp.name, "HOME": self.tmp.name}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.data = mock.patch.object(paths, "_data_dir_override", Path(self.tmp.name) / "plugin-data")
        self.data.start()
        evidence.clear_cache()

    def tearDown(self):
        self.data.stop()
        self.env.stop()
        self.tmp.cleanup()

    def add(self, run_id, pairs, judge=OTHER):
        return evidence.add_run("s", skill_sha256="a" * 64, model=MODEL, judge=judge, run=run_id,
                                date="2026-11-14", pairs=pairs)


class PoolKeepsToolData(_Home):
    def test_each_pooled_pair_keeps_tool_driven_and_nothing_else_new(self):
        self.add("r1", [_pair(1, tool_driven=True), _pair(2), _pair(3, empty=True)])
        pool = json.loads(evidence.pool_path("s").read_text())
        pairs = pool["pools"][0]["runs"][0]["pairs"]
        for p in pairs:
            self.assertLessEqual(set(p), {"task_hash", "outcome", "tool_driven", "empty_answer"})
        by = {p["task_hash"]: p for p in pairs}
        self.assertTrue(by[f"{1:012x}"]["tool_driven"])
        self.assertFalse(by[f"{2:012x}"]["tool_driven"])
        self.assertTrue(by[f"{3:012x}"]["empty_answer"])

    def test_errors_still_never_count_toward_the_verdict(self):
        c = self.add("r1", [_pair(1), _pair(2, empty=True), _pair(3, outcome="loss")])
        self.assertEqual((c["n_tasks"], c["wins"], c["losses"], c["ties"]), (2, 1, 1, 0))

    def test_pooled_tools_use_the_same_rule_as_one_run(self):
        one = [_pair(i, tool_driven=i < 4) for i in range(6)] + [_pair(6, empty=True)]
        c = self.add("r1", one)
        self.assertEqual(c["tools"], rec.tool_driven_counts(one))
        self.assertTrue(c["tools"]["flag"])  # 4 tool-driven + 1 empty of 7

    def test_flag_follows_all_pooled_samples(self):
        self.assertTrue(self.add("r1", [_pair(i, tool_driven=True) for i in range(6)])["tools"]["flag"])
        two = self.add("r2", [_pair(i) for i in range(6)])
        self.assertEqual((two["tools"]["tested"], two["tools"]["count"]), (12, 6))
        self.assertTrue(two["tools"]["flag"])  # half
        three = self.add("r3", [_pair(i) for i in range(6)])
        self.assertFalse(three["tools"]["flag"])  # 6 of 18

    def test_pool_rows_saved_before_r7_still_load(self):
        p = evidence.pool_path("s")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "skill": "s", "pools": [
            {"skill_sha256": "a" * 64, "model": MODEL, "judge": OTHER,
             "runs": [{"run": "old", "date": "2026-11-12",
                       "pairs": [{"task_hash": f"{i:012x}", "outcome": "win"} for i in range(6)]}]}]}))
        c = self.add("r1", [_pair(i, tool_driven=True) for i in range(6)])
        self.assertEqual((c["n_runs"], c["tools"]["tested"], c["tools"]["count"]), (2, 12, 6))


class PooledResultShowsFlags(_Home):
    def _summary(self, pairs, judge=OTHER):
        s = {"skill": "s", "skill_sha256": "a" * 64, "pairs": pairs, "stamp": {"date": "2026-11-14"}}
        pool = evidence.pool_summary(s, model=MODEL, judge=judge, run="r1")
        return pool, evidence.effective({**s, "pool": pool})

    def test_pooled_recommendation_raises_the_tool_flag_and_holds_strength(self):
        pool, eff = self._summary([_pair(i, tool_driven=True) for i in range(12)])
        keys = [f["key"] for f in pool["recommendation"]["flags"]]
        self.assertEqual(keys.count(rec.FLAG_TOOLS), 1)
        self.assertEqual(pool["lean"]["strength"], verdict.LIKELY)
        self.assertEqual(pool["lean"].get("capped_from"), verdict.STRONG)
        self.assertEqual(pool["lean"]["verdict"], verdict.lean(12, 0, 0)["verdict"])  # verdict unchanged
        self.assertEqual(pool["recommendation"]["confidence"]["text"], copy.CONFIDENCE_WORDS[rec.MODERATE])
        self.assertTrue(eff["tools"]["flag"])

    def test_pooled_recommendation_without_tool_use_is_unflagged(self):
        pool, eff = self._summary([_pair(i) for i in range(12)])
        self.assertNotIn(rec.FLAG_TOOLS, [f["key"] for f in pool["recommendation"]["flags"]])
        self.assertEqual(pool["lean"]["strength"], verdict.STRONG)
        self.assertFalse(eff["tools"]["flag"])

    def test_pooled_recommendation_keeps_the_same_model_judge_flag(self):
        same, _ = self._summary([_pair(i) for i in range(6)], judge=MODEL)
        self.assertIn(rec.FLAG_SAME_JUDGE, [f["key"] for f in same["recommendation"]["flags"]])

    def test_pooled_recommendation_from_another_maker_has_no_same_model_flag(self):
        other, _ = self._summary([_pair(i) for i in range(6)], judge=OTHER)
        self.assertNotIn(rec.FLAG_SAME_JUDGE, [f["key"] for f in other["recommendation"]["flags"]])


class FlagsShowOnce(_Home):
    """R4: the same-model text never sits in the chat line; the report lists it once."""

    def _folder(self, pairs, judge):
        s = {"skill": "s", "skill_sha256": "a" * 64, "pairs": pairs, "stamp": {"date": "2026-11-14"},
             "combined": {"wins": 0, "losses": 0, "ties": 0}}
        s["pool"] = evidence.pool_summary(s, model=MODEL, judge=judge, run="r1")
        folder = Path(self.tmp.name) / "run"
        folder.mkdir(exist_ok=True)
        (folder / "result.json").write_text(json.dumps({
            "subject_model": MODEL, "judge_model": judge, "per_skill": {"s": s}}), encoding="utf-8")
        return folder

    def test_chat_line_has_the_tool_flag_once_and_no_same_model_text(self):
        folder = self._folder([_pair(i, tool_driven=True) for i in range(12)], judge=MODEL)
        line = run.result_line(folder, "s")
        self.assertEqual(line.count(copy.FLAG_TOOL_DRIVEN), 1)
        self.assertNotIn("Same-model judge", line)
        self.assertIn("(Plausible)", line)


if __name__ == "__main__":
    unittest.main()
