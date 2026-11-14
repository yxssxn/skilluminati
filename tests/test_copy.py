"""copy.py completeness: every internal label maps, every string is non-empty and plain, and no owl
string is hard-coded outside copy.py. Stdlib only, no network."""

from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")

EM_DASH = "\u2014"


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _strings(v)


class CopyCompletenessTests(unittest.TestCase):
    def test_every_internal_label_maps(self):
        self.assertEqual(set(copy.VERDICTS), set(verdict.LABELS))
        self.assertEqual(set(copy.VERDICT_TONE), set(verdict.LABELS))

    def test_the_four_pairs_are_as_decided(self):
        self.assertEqual(copy.headline(verdict.EARNS_ITS_KEEP)[2], "Enlightened. Answers are clearly better with it. Keep it.")
        self.assertEqual(copy.headline(verdict.MODEL_DOES_THIS_ALREADY)[2], "Dead weight. The model does this anyway. It only costs tokens.")
        self.assertEqual(copy.headline(verdict.GETS_IN_THE_WAY)[2], "Hindrance. Answers are worse with it. Fix or remove.")
        self.assertEqual(copy.headline(verdict.NOT_SURE_YET)[2], "Unproven. Too few uses to tell yet.")
        self.assertIsNone(copy.headline("nonsense"))

    def test_themed_word_is_always_paired_with_a_plain_label(self):
        for label, (themed, plain) in copy.VERDICTS.items():
            self.assertTrue(themed.strip() and plain.strip(), label)
            self.assertNotEqual(themed.lower(), plain.lower())
            self.assertRegex(copy.headline(label)[2], r"^[^.]+\. .+")
        for kind, (themed, plain) in copy.TRIAL.items():
            self.assertTrue(themed.strip() and plain.strip(), kind)
        self.assertEqual(copy.trial_name("helpful"), "the Adept (helps)")
        self.assertEqual(copy.trial_name("placebo"), "the Idler (does nothing)")
        self.assertEqual(copy.trial_name("poisoned"), "the Impostor (sabotages)")
        self.assertEqual(copy.trial_name("bloat"), "the Glutton (bloats)")
        self.assertEqual(set(copy.TRIAL), {"helpful", "placebo", "poisoned", "bloat"})

    def test_names_and_concept(self):
        self.assertEqual(copy.JUDGE_NAME, "the Owl")
        self.assertEqual(copy.RUN_VERB, "bring a skill to trial")
        self.assertEqual(copy.CONCEPT_PLAIN, "The Owl judges a skill on work it has already done.")
        self.assertIn("owl of Minerva", copy.CONCEPT_QUOTE)
        self.assertFalse(hasattr(copy, "CONCEPT_STATUS"))
        self.assertFalse(hasattr(copy, "DRAFT"))

    def test_every_string_is_non_empty(self):
        names = [n for n in dir(copy) if n.isupper()]
        self.assertTrue(names)
        for n in names:
            value = getattr(copy, n)
            if isinstance(value, bool):
                continue
            strings = list(_strings(value))
            self.assertTrue(strings, n)
            for s in strings:
                self.assertTrue(s.strip(), f"{n} holds an empty string")
        for s in _strings(copy.payload()):
            self.assertTrue(s.strip())

    def test_no_em_dash_in_any_string(self):
        for n in dir(copy):
            if n.isupper():
                for s in _strings(getattr(copy, n)):
                    self.assertNotIn(EM_DASH, s, n)
        for s in _strings(copy.payload()):
            self.assertNotIn(EM_DASH, s)
        self.assertNotIn(EM_DASH, (ROOT / "plugin" / "skilluminati" / "copy.py").read_text(encoding="utf-8"))

    def test_no_draft_text_ships(self):
        text = (ROOT / "plugin" / "skilluminati" / "copy.py").read_text(encoding="utf-8")
        self.assertNotIn("DRAFT", text)
        self.assertNotIn("to approve", text)

    def test_usage_lists_every_headline(self):
        commands = importlib.import_module("plugin.skilluminati.commands")
        # W1: the help is the 3 commands and a pointer to the page; the verdict words live on the page.
        for line in ("/skilluminati <skill>", "/skilluminati stop", "/skilluminati selftest", "Skilluminati page"):
            self.assertIn(line, commands.USAGE)


class NoHardCodedOwlStrings(unittest.TestCase):
    """The acceptance grep, as a test: themed words live in copy.py only."""

    PATTERN = re.compile(r"Illuminated|Obscures|Still watching|Already known")

    def test_themed_words_only_in_copy_py(self):
        offenders = []
        for path in (ROOT / "plugin").rglob("*"):
            if path.suffix not in (".py", ".js") or "__pycache__" in path.parts:
                continue
            if path == ROOT / "plugin" / "skilluminati" / "copy.py":
                continue
            if self.PATTERN.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
