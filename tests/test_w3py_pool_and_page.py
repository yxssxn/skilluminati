"""W3PY CODE-M1, CODE unknown-model pooling, PAGE-M2 usable count, CODE page noise rows.
Temp HERMES_HOME only; no model calls."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
for p in (REPO / "plugin", REPO / "plugin" / "dashboard"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from skilluminati import evidence, paths  # noqa: E402

SHA = "a" * 64


def _pairs(outcomes):
    return [{"task_hash": f"t{i}", "outcome": o, "cell": "s"} for i, o in enumerate(outcomes)]


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        paths._data_dir_override = None
        self.env.stop()
        self.tmp.cleanup()


class CorruptPoolTests(Home):
    def test_pooled_counts_skips_bad_entries(self):
        runs = [{"pairs": ["x", None, 3, {"task_hash": "t1", "outcome": "win"}]},
                {"pairs": {"task_hash": "t2", "outcome": "loss"}},  # pairs stored as a dict
                "not a run", None,
                {"pairs": [{"task_hash": "t3", "outcome": "tie"}]}]
        got = evidence.pooled_counts(runs)
        self.assertEqual((got["wins"], got["losses"], got["ties"], got["n_tasks"]), (1, 0, 1, 2))

    def test_a_corrupt_pool_file_does_not_stop_later_pooling(self):
        path = evidence.pool_path("s")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": evidence.POOL_SCHEMA, "skill": "s", "pools": [
            {"skill_sha256": SHA, "model": "m1", "judge": None,
             "runs": [{"run": "old", "date": None, "pairs": ["broken", {"task_hash": "t9", "outcome": "win"}]},
                      {"run": "old2", "date": None, "pairs": "nope"}]}]}), encoding="utf-8")
        got = evidence.add_run("s", skill_sha256=SHA, model="m1", judge=None, run="new", date=None,
                               pairs=_pairs(["win", "loss"]))
        self.assertIsNotNone(got)
        self.assertEqual(got["n_runs"], 3)
        self.assertEqual(got["n_tasks"], 3)  # t0, t1 from the new run plus t9 from the old one


class UnknownModelTests(Home):
    def test_unknown_models_are_not_pooled(self):
        for m in ("unknown", "active model"):
            self.assertIsNone(evidence.add_run("s", skill_sha256=SHA, model=m, judge=None, run=f"r-{m}",
                                               date=None, pairs=_pairs(["win"])))
        self.assertFalse(evidence.pool_path("s").exists())

    def test_known_models_still_pool_across_id_forms(self):
        evidence.add_run("s", skill_sha256=SHA, model="claude-opus-5-5[1m]", judge=None, run="r1", date=None,
                         pairs=_pairs(["win"]))
        got = evidence.add_run("s", skill_sha256=SHA, model="anthropic/claude-opus-5-5", judge=None, run="r2",
                               date=None, pairs=_pairs(["win", "win"]))
        self.assertEqual(got["n_runs"], 2)


class PageTests(Home):
    def _store(self, data):
        d = self.home / "plugin-data" / "skilluminati"
        d.mkdir(parents=True, exist_ok=True)
        (d / "verdicts.json").write_text(json.dumps(data), encoding="utf-8")

    def _row(self, **kw):
        return {"skill": "s", "verdict": "Enlightened", "model": "claude-opus-5-5", "judge": "claude-opus-5-5",
                "date": "2026-11-14", "wins": 6, "losses": 0, "ties": 0,
                "counts": {"uses": 603, "eligible": 40, "tested": 12}, **kw}

    def test_page_row_carries_usable(self):
        import plugin_api as api
        self._store({"runs": [self._row()]})
        [row] = api.page_results(self.home)
        self.assertEqual((row["uses"], row["usable"], row["tested"]), (603, 40, 12))

    def test_usable_is_none_when_unknown(self):
        import plugin_api as api
        self._store({"runs": [self._row(counts={"uses": 5, "tested": 3})]})
        [row] = api.page_results(self.home)
        self.assertIsNone(row["usable"])

    def test_page_noise_matches_ids_like_the_report(self):
        import plugin_api as api
        aa = {"model": "anthropic/claude-opus-5-5[1m]", "judge": "claude-opus-5-5-20261101",
              "pooled": 20, "firm": 5, "wins": 3, "losses": 2, "ties": 15}
        self._store({"runs": [self._row()], "aa": [aa]})
        [row] = api.page_results(self.home)
        self.assertEqual((row["working"]["noise"]["firm"], row["working"]["noise"]["n"]), (5, 20))


if __name__ == "__main__":
    unittest.main()
