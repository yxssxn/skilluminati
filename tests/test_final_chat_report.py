"""In plain words: the chat report (``/skilluminati last`` and the result tool) agrees with the page.

S1: a lean result shows its strength word ("Plausible") in chat, not only when the tool-driven cap
fired, and a "No clear difference yet" result shows no strength word.
Stdlib only, temp dirs, no model calls. The shapes are synthetic: an 8 / 1 / 2 lean (Better / Same /
Worse); no task text, answers or skill names.
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

paths = importlib.import_module("plugin.skilluminati.paths")
report = importlib.import_module("plugin.skilluminati.report")
copy = importlib.import_module("plugin.skilluminati.copy")
evidence = importlib.import_module("plugin.skilluminati.evidence")

MODEL = "claude-opus-5-5[1m]"
JUDGE = "claude-opus-5-5[1m]"


def _rec(label, themed, plain, level, lean):
    return {"headline": {"label": label, "themed": themed, "plain": plain},
            "confidence": {"level": level, "text": copy.CONFIDENCE_WORDS.get(level, level)},
            "lean": lean, "flags": [], "next_step": {"key": "keep", "text": "Keep it."}, "numbers": {}}


def _helps_lean(capped=False):
    lean = {"verdict": "helps", "strength": "likely", "wins": 8, "losses": 2, "ties": 1, "n": 11}
    if capped:
        lean["capped_from"] = "strong"
    return _rec("Earns its keep", "Enlightened", "Answers are clearly better with it. Keep it.",
                "Moderate", lean)


def _skill(rec, wins=8, losses=2, ties=1):
    return {"recommendation": rec, "combined": {"wins": wins, "losses": losses, "ties": ties},
            "n_tasks": wins + losses + ties, "stamp": {"model": MODEL, "judge": JUDGE}}


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

    def run_folder(self, skill_block, aa=None, name="20261112-000000-verdict-s"):
        folder = Path(self.tmp.name) / "plugin-data" / "runs" / name
        folder.mkdir(parents=True)
        result = {"per_skill": {"s": skill_block}, "subject_model": MODEL, "judge_model": JUDGE}
        if aa is not None:
            result["aa"] = aa
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return folder


class StrengthWordInChat(_Home):
    def test_lean_result_says_plausible(self):
        folder = self.run_folder(_skill(_helps_lean()))
        text = report.terminal(folder)
        self.assertIn("s: Enlightened (Plausible).", text)

    def test_lean_result_says_plausible_in_markdown(self):
        folder = self.run_folder(_skill(_helps_lean()))
        self.assertIn("**s: Enlightened (Plausible).**", report.build(folder, live=True))

    def test_capped_result_says_plausible_once(self):
        folder = self.run_folder(_skill(_helps_lean(capped=True)))
        text = report.terminal(folder)
        self.assertIn("s: Enlightened (Plausible).", text)
        self.assertEqual(text.count("(Plausible)"), 1)

    def test_strong_result_says_probable(self):
        lean = {"verdict": "helps", "strength": "strong", "wins": 7, "losses": 1, "ties": 1, "n": 9}
        rec = _rec("Earns its keep", "Enlightened", "Answers are clearly better with it. Keep it.", "Strong", lean)
        folder = self.run_folder(_skill(rec, 7, 1, 1))
        self.assertIn("s: Enlightened (Probable).", report.terminal(folder))

    def test_no_clear_difference_has_no_strength_word(self):
        lean = {"verdict": "no_difference", "strength": "likely", "wins": 5, "losses": 2, "ties": 5, "n": 12}
        rec = _rec("Model does this already", "Dead weight", "The model does this anyway.", "Moderate", lean)
        folder = self.run_folder(_skill(rec, 5, 2, 5))
        head = report.terminal(folder).splitlines()[0]
        self.assertTrue(head.startswith(f"s: {copy.UNCLEAR_WORD}."), head)
        self.assertNotIn("(Plausible)", head)
        self.assertNotIn("(Probable)", head)


if __name__ == "__main__":
    unittest.main()
