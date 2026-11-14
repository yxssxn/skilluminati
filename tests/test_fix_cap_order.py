"""In plain words: the tool-driven cap (C's hold at Plausible) is applied before the shown headline
and next step are chosen (B's rules), so a tool-driven Probable "no difference" never shows
"Dead weight" or "Consider archiving": it shows "No clear difference yet" and "Ask again" (fix batch
S12). Stdlib only, no model calls.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

rec = importlib.import_module("plugin.skilluminati.recommend")
copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")
evidence = importlib.import_module("plugin.skilluminati.evidence")

SAME = verdict.MODEL_DOES_THIS_ALREADY
HURTS = verdict.GETS_IN_THE_WAY
TOOLS = {"tested": 12, "tool_driven": 9, "empty_answer": 0, "count": 9, "flag": True}
CLEAN = {"tested": 12, "tool_driven": 0, "empty_answer": 0, "count": 0, "flag": False}
HEAVY = {"flag": True, "text": "Heavy: +3,000 tokens per load (120% over the median skill)", "over_tokens": 3000}


def _lean(w, l, t):
    out = verdict.lean(w, l, t)
    assert out["strength"] == verdict.STRONG, out
    return out


class CapBeforeShownWords(unittest.TestCase):
    def _r(self, label, lean, tools, heavy=None):
        w, l, t = lean["wins"], lean["losses"], lean["ties"]
        return rec.recommend(label, wins=w, losses=l, ties=t, low=0.1, high=0.9, lean=lean, tools=tools,
                             heavy=heavy, tasks_to_settle=None)

    def test_tool_driven_probable_no_difference(self):
        lean = _lean(1, 1, 22)
        self.assertEqual(lean["verdict"], verdict.NO_DIFFERENCE)
        clean = self._r(SAME, lean, CLEAN)
        self.assertEqual(clean["confidence"]["text"], "Probable")
        self.assertEqual(clean["headline"]["themed"], "Dead weight")
        self.assertEqual(clean["next_step"]["key"], rec.STEP_ARCHIVE)
        for heavy in (None, HEAVY):
            r = self._r(SAME, lean, TOOLS, heavy)
            self.assertEqual(r["confidence"]["text"], "Plausible")
            self.assertEqual(r["headline"]["themed"], copy.UNCLEAR_WORD)
            self.assertEqual(r["headline"]["themed"], "No clear difference yet")
            self.assertEqual(r["next_step"]["text"], "Ask again")
            self.assertEqual(r["next_step"]["key"], rec.STEP_ASK_AGAIN)
            self.assertEqual(r["lean"]["strength"], verdict.LIKELY)
            self.assertEqual(r["lean"]["capped_from"], verdict.STRONG)

    def test_tool_driven_probable_hindrance_is_not_archived(self):
        lean = _lean(1, 20, 1)
        self.assertEqual(lean["verdict"], verdict.HURTS)
        self.assertEqual(self._r(HURTS, lean, CLEAN)["next_step"]["key"], rec.STEP_ARCHIVE)
        self.assertEqual(self._r(HURTS, lean, TOOLS)["next_step"]["key"], rec.STEP_ASK_AGAIN)

    def test_lean_free_path_is_capped_first_too(self):
        r = rec.recommend(SAME, wins=20, losses=20, ties=0, low=0.45, high=0.55, tools=TOOLS)
        self.assertNotEqual(r["confidence"]["level"], rec.STRONG)
        self.assertEqual(r["headline"]["themed"], copy.UNCLEAR_WORD)
        self.assertEqual(r["next_step"]["key"], rec.STEP_ASK_AGAIN)

    def test_pooled_result_is_capped_first_too(self):
        counts = {"wins": 1, "losses": 1, "ties": 22, "n_tasks": 24, "n_runs": 2, "low": 0.1, "high": 0.9,
                  "tools": TOOLS}
        block = evidence.pooled_block(counts, model="m1", judge="j1")
        r = block["recommendation"]
        self.assertEqual(r["headline"]["themed"], copy.UNCLEAR_WORD)
        self.assertEqual(r["next_step"]["key"], rec.STEP_ASK_AGAIN)


if __name__ == "__main__":
    unittest.main()
