"""In plain words: when the row's counts are pooled over more than one run, the task-by-task list
below them (one run's outcomes) is labelled "Latest run only" (fix batch S8). Stdlib only, no model
calls.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

copy = importlib.import_module("plugin.skilluminati.copy")
report = importlib.import_module("plugin.skilluminati.report")
JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")

RUN = "20261114-000000-verdict-s"


def _row(pool_runs):
    return {"skill": "s", "user_label": "Earns its keep", "wins": 6, "losses": 4, "ties": 2, "model": "m1",
            "date": "2026-11-14", "run": RUN, "pool_tasks": 12, "pool_runs": pool_runs}


class LatestRunOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = Path(self.tmp.name)
        d = self.runs / RUN
        d.mkdir()
        pairs = [{"cell": "s", "task_hash": f"{i:012x}", "outcome": "win"} for i in range(12)]
        (d / "result.json").write_text(json.dumps({"per_skill": {"s": {"skill": "s", "pairs": pairs}}}),
                                       encoding="utf-8")

    def test_pooled_row_marks_its_task_list(self):
        r = report.page_result(_row(3), self.runs)
        self.assertEqual(len(r["working"]["tasks"]), 12)
        self.assertTrue(r["working"]["latest_only"])

    def test_one_run_or_no_pool_is_not_marked(self):
        for runs in (1, None):
            r = report.page_result(_row(runs), self.runs)
            self.assertFalse(r["working"]["latest_only"], runs)

    def test_wording_and_page(self):
        self.assertEqual(copy.page_copy()["working_tasks_latest_only"], "Latest run only")
        start = JS.index("function Working(")
        body = JS[start:JS.index("\nfunction ", start + 1)]
        self.assertIn("w.latest_only", body)
        self.assertIn("c.working_tasks_latest_only", body)


if __name__ == "__main__":
    unittest.main()
