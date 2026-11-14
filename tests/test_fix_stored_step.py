"""In plain words: a stored result below Probable never shows a step a fresh result would not give
(fix batch S1). For a no-difference or hindrance headline below Probable, any stored next step except
"run more tasks" (Consider trimming included) shows as "Ask again", in the dashboard API and in the
report. The row below has the shape of a real stored unslop row (5 / 2 / 5, Heavy, Plausible,
"Consider trimming"); numbers only. Stdlib only, temp dirs, no model calls.
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

rec = importlib.import_module("plugin.skilluminati.recommend")
copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")
report = importlib.import_module("plugin.skilluminati.report")

HEAVY = {"load_tokens": 6012, "median_tokens": 2434.5, "ratio": 2.4695, "over_tokens": 3578, "over_pct": 147,
         "flag": True, "text": "Heavy: +3,578 tokens per load (147% over the median skill)"}


def _stored_rec(step_key="trim", step_text="Consider trimming", level="Moderate", label=verdict.MODEL_DOES_THIS_ALREADY):
    head = {"label": label, "themed": "Dead weight", "plain": "The model does this anyway. It only costs tokens.",
            "text": "Dead weight. The model does this anyway. It only costs tokens.", "tone": "muted"}
    return {"headline": head,
            "confidence": {"level": level, "text": copy.CONFIDENCE_WORDS[level],
                           "why": "7 decisive tasks, win rate between 36% and 92%"},
            "flags": [{"key": "heavy", "text": HEAVY["text"]}, {"key": "few_tasks", "text": "Few tasks"},
                      {"key": "judges_split", "text": copy.FLAG_JUDGES_SPLIT}],
            "next_step": {"key": step_key, "text": step_text, "n": None}, "note": copy.NEXT_NOTE,
            "lean": {"verdict": "no_difference", "strength": "likely", "wins": 5, "losses": 2, "ties": 5, "n": 12},
            "numbers": {"n_decisive": 7, "wins": 5, "losses": 2, "ties": 5, "low": 0.3589, "high": 0.9178,
                        "tasks_to_settle": 14}}


ROW = {"skill": "s", "verdict": "Unsure", "effect": "none", "user_label": verdict.MODEL_DOES_THIS_ALREADY,
       "model": "m1", "judge": "j1", "date": "2026-11-12", "wins": 5, "losses": 2, "ties": 5,
       "low": 0.3589, "high": 0.9178, "tasks_to_settle": 14, "heavy": HEAVY}


class StoredStepIsHonest(unittest.TestCase):
    def test_live_unslop_row_shape_shows_ask_again_in_the_api(self):
        out = rec.clean(_stored_rec(), row=ROW)
        self.assertEqual(out["next_step"]["key"], rec.STEP_ASK_AGAIN)
        self.assertEqual(out["next_step"]["text"], copy.NEXT_ASK_AGAIN)
        self.assertEqual(out["headline"]["themed"], copy.UNCLEAR_WORD)

    def test_matches_a_fresh_result_with_the_same_numbers(self):
        fresh = rec.recommend(verdict.MODEL_DOES_THIS_ALREADY, wins=5, losses=2, ties=5, low=0.3589, high=0.9178,
                              tasks_to_settle=14, heavy=HEAVY, lean=verdict.lean(5, 2, 5))
        self.assertEqual(rec.clean(_stored_rec(), row=ROW)["next_step"]["key"], fresh["next_step"]["key"])

    def test_every_step_but_run_more_is_remapped_below_probable(self):
        for key, text in (("trim", copy.NEXT_TRIM), ("archive", copy.NEXT_ARCHIVE), ("keep", copy.NEXT_KEEP)):
            for label in (verdict.MODEL_DOES_THIS_ALREADY, verdict.GETS_IN_THE_WAY):
                with self.subTest(key=key, label=label):
                    out = rec.clean(_stored_rec(key, text, label=label), row=ROW)
                    self.assertEqual(out["next_step"]["key"], rec.STEP_ASK_AGAIN)
        more = rec.clean(_stored_rec("run_more", "Run 14 more tasks", level="Weak"), row=ROW)
        self.assertEqual(more["next_step"]["key"], rec.STEP_RUN_MORE)

    def test_probable_keeps_its_step(self):
        out = rec.clean(_stored_rec(level="Strong"), row=ROW)
        self.assertEqual(out["next_step"]["key"], "trim")

    def test_enlightened_plausible_keeps_its_step(self):
        out = rec.clean(_stored_rec("keep", copy.NEXT_KEEP, label=verdict.EARNS_ITS_KEEP), row=ROW)
        self.assertEqual(out["next_step"]["key"], rec.STEP_KEEP)

    def test_report_shows_ask_again(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "20261112-000000-verdict-s"
            folder.mkdir()
            summary = {"skill": "s", "user_label": verdict.MODEL_DOES_THIS_ALREADY, "effect": "none",
                       "combined": {"wins": 5, "losses": 2, "ties": 5}, "heavy": HEAVY,
                       "recommendation": _stored_rec(), "stamp": {"model": "m1", "judge": "j1"}}
            (folder / "result.json").write_text(json.dumps({"per_skill": {"s": summary}, "subject_model": "m1",
                                                            "judge_model": "j1"}), encoding="utf-8")
            text = report.terminal(folder)
        self.assertIn("Next: ask again.", text)
        self.assertNotIn(copy.NEXT_TRIM, text)


if __name__ == "__main__":
    unittest.main()
