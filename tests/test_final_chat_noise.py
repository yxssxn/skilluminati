"""In plain words: the chat report's same-vs-same line agrees with the page (review-final S6).

For a run saved before same-vs-same pooling, the chat noise line uses the stored pooled row for that
model and judge in ``verdicts.json``, as the page does, not the run's own few pairs.

Stdlib only, temp dirs, no model calls. Synthetic shapes: a pooled noise row of 37 firm picks in 59
pairs and an old run with 4 pairs of its own; no task text, answers or skill names.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_final_chat_report import JUDGE, MODEL, _helps_lean, _Home, _skill, copy, paths, report  # noqa: E402


class NoiseLineMatchesPage(_Home):
    OLD_AA = {"wins": 1, "losses": 0, "ties": 3, "model": MODEL,
              "pairs": [{"outcome": o} for o in ("win", "tie", "tie", "tie")]}

    def store(self, aa_rows):
        p = paths.verdicts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "runs": [], "aa": aa_rows}), encoding="utf-8")

    def test_old_run_uses_stored_pooled_row(self):
        self.store([{"model": MODEL, "judge": JUDGE, "wins": 1, "losses": 0, "ties": 3, "firm": 37, "pooled": 59}])
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        text = report.terminal(folder)
        self.assertIn("37 of 59", text)
        self.assertNotIn(copy.SAME_VS_SAME_FEW.format(n=4), text)

    def test_saved_report_is_replaced_by_the_pooled_line(self):
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        report.save(folder)  # saved before the pool existed: says "not enough pairs (4)"
        self.store([{"model": MODEL, "judge": JUDGE, "firm": 37, "pooled": 59}])
        self.assertIn("37 of 59", report.saved_or_built(folder))

    def test_model_ids_are_normalised(self):
        self.store([{"model": "claude-opus-5-5", "judge": "anthropic/claude-opus-5-5", "firm": 37, "pooled": 59}])
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        self.assertIn("37 of 59", report.terminal(folder))

    def test_same_judge_row_wins_over_another_judge(self):
        self.store([{"model": MODEL, "judge": JUDGE, "firm": 37, "pooled": 59},
                    {"model": MODEL, "judge": "gpt-6", "firm": 2, "pooled": 20}])
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        self.assertIn("37 of 59", report.terminal(folder))

    def test_other_model_row_is_not_used(self):
        self.store([{"model": "gpt-6", "judge": JUDGE, "firm": 37, "pooled": 59}])
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        text = report.terminal(folder)
        self.assertNotIn("37 of 59", text)
        self.assertIn(copy.SAME_VS_SAME_FEW.format(n=4), text)

    def test_no_store_keeps_the_runs_own_pairs(self):
        folder = self.run_folder(_skill(_helps_lean()), aa=self.OLD_AA)
        self.assertIn(copy.SAME_VS_SAME_FEW.format(n=4), report.terminal(folder))


if __name__ == "__main__":
    unittest.main()
