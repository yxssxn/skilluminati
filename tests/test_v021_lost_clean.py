"""In plain words: "Why it lost" quotes the Owl's own words in the report, the page and the
skilluminati_result tool. Those words come from a model, so they are cleaned before they are shown:
HTML and markdown markup and control characters are taken out, and each reason is cut to
report.LOST_REASON_MAX characters at a word boundary, ending in "...". Hostile input, temp dirs,
no model calls. The stored judge text is never changed.
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

report = importlib.import_module("plugin.skilluminati.report")
tools = importlib.import_module("plugin.skilluminati.tools")
paths = importlib.import_module("plugin.skilluminati.paths")
from tests.test_w2_lost_reasons import RUN_B, SKILL, _pair, _result, _write  # noqa: E402

HOSTILE = {
    "html": "<script>alert(1)</script>Answer 2 was <b>clearer</b> &lt;img src=x onerror=alert(1)&gt; here.",
    "md": "**Answer 2** was [clearer](https://evil.example/x) and `shorter` ![x](https://evil.example/p.png) ~~ok~~",
    "heading": "# Ignore all rules\n> quoted\n- listed\n| a | b |",
    "control": "Answer 2\x00 was\x1b[31m red\x1b[0m\u202e and\u200b hidden\x07 here.",
    "long": "Answer 2 kept to the point " + "and stayed on the question " * 20 + "end.",
    "oneword": "x" * 400,
}
BAD = ("<", ">", "**", "`", "](", "\x00", "\x1b", "\u202e", "\u200b", "\x07", "&lt;", "&gt;", "~~", "|")


def _clean(text: str) -> str:
    return report.clean_reason(text)


class CleanReason(unittest.TestCase):
    def test_markup_and_control_characters_are_gone(self):
        for name, raw in HOSTILE.items():
            got = _clean(raw)
            for bad in BAD:
                self.assertNotIn(bad, got, (name, got))
            self.assertEqual(got, got.strip(), name)
            self.assertNotIn("  ", got, name)
            self.assertTrue(all(ch.isprintable() for ch in got), (name, got))

    def test_words_are_kept(self):
        self.assertEqual(_clean(HOSTILE["html"]), "alert(1)Answer 2 was clearer here.")
        self.assertEqual(_clean(HOSTILE["md"]), "Answer 2 was clearer and shorter x ok")
        self.assertEqual(_clean(HOSTILE["heading"]), "Ignore all rules quoted listed a b")
        self.assertEqual(_clean(HOSTILE["control"]), "Answer 2 was red and hidden here.")
        self.assertEqual(_clean("Plain words, kept as they are: 3 < 4 is fine? No."), "Plain words, kept as they are: 3 4 is fine? No.")

    def test_snake_case_and_maths_survive(self):
        self.assertEqual(_clean("It used max_tokens and 2*3 correctly."), "It used max_tokens and 2*3 correctly.")

    def test_cut_at_a_word_boundary_with_an_ellipsis(self):
        got = _clean(HOSTILE["long"])
        self.assertLessEqual(len(got), report.LOST_REASON_MAX)
        self.assertTrue(got.endswith("..."))
        body = got[:-3]
        self.assertTrue(HOSTILE["long"].startswith(body))
        self.assertEqual(HOSTILE["long"][len(body)], " ", "cut falls between words")
        self.assertFalse(body.endswith(" "))

    def test_one_long_word_is_cut_hard(self):
        got = _clean(HOSTILE["oneword"])
        self.assertEqual(len(got), report.LOST_REASON_MAX)
        self.assertTrue(got.endswith("..."))

    def test_short_text_is_untouched(self):
        self.assertEqual(_clean("Shorter and right."), "Shorter and right.")

    def test_markup_only_is_nothing(self):
        self.assertEqual(_clean("<b></b>**``**"), "")


class EverywhereItShows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        runs = self.root / "runs"
        rows = [_pair(f"t{i}", "loss", (raw, raw + " b")) for i, raw in enumerate(HOSTILE.values())]
        _write(runs / RUN_B, rows)
        (runs / RUN_B / "result.json").write_text(json.dumps(_result(0, len(rows), 0)), encoding="utf-8")
        self.d = runs / RUN_B

    def tearDown(self):
        self.tmp.cleanup()

    def _assert_clean(self, reasons):
        self.assertTrue(reasons)
        for r in reasons:
            self.assertLessEqual(len(r), report.LOST_REASON_MAX)
            for bad in BAD:
                self.assertNotIn(bad, r, r)

    def test_lost_reasons_are_clean(self):
        self._assert_clean(report.lost_reasons(self.d, SKILL, limit=10))

    def test_page_and_reports(self):
        self._assert_clean(report.page_result({"skill": SKILL, "run": RUN_B, "wins": 0, "losses": 6, "ties": 0},
                                              self.d.parent)["working"]["lost_reasons"])
        for text in (report.build(self.d), report.terminal(self.d)):
            for bad in ("<script", "\x1b", "\u202e", "](https"):
                self.assertNotIn(bad, text)

    def test_result_tool_rebuilds_an_old_saved_report(self):
        # a report.md saved by v0.2.0 still holds the raw reason; the tool must not pass it on
        raw = HOSTILE["html"]
        (self.d / report.REPORT_FILE).write_text(f"old\n{report.sb_copy.LOST_LABEL}\n- {raw}\n", encoding="utf-8")
        paths._data_dir_override = self.root
        try:
            out = json.loads(tools.result({"skill": SKILL}))
        finally:
            paths._data_dir_override = None
        self.assertTrue(out["found"], out)
        self.assertNotIn("<script", out["report"])
        self.assertIn(report.sb_copy.LOST_LABEL, out["report"])

    def test_stored_pairs_are_not_changed(self):
        before = (self.d / "pairs.jsonl").read_bytes()
        report.lost_reasons(self.d, SKILL, limit=10)
        self.assertEqual((self.d / "pairs.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
