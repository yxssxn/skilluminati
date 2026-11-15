"""In plain words: the tool-driven sentence lived in copy.py three times (the flag, the row tag tip
and the working line). The page now reads one page key for both places, and the text it shows is
the same sentence as before. Reads plugin.js as text, no model calls.
"""

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
JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")

#: The sentence the page showed in v0.2.0, in both places.
V020_TEXT = "Most tested uses went straight to tools in real use, or came back empty here. This test reads answers only."


def _key_after(marker: str) -> str:
    """The ``c.<key>`` the page reads on the line that tests ``marker``."""
    line = next(l for l in JS.splitlines() if marker in l)
    m = re.search(re.escape(marker) + r"\)?\s*(?:\?\s*jsx\(RowTag, \{ tag: c\.tool_driven_tag, tip: |lines\.push\(str\()c\.(\w+)", line)
    assert m is not None, line
    return m.group(1)


class ToolDrivenCopyOnce(unittest.TestCase):
    def test_page_copy_holds_the_sentence_once(self):
        page = copy.page_copy()
        keys = sorted(k for k, v in page.items() if v == copy.FLAG_TOOL_DRIVEN)
        self.assertEqual(keys, ["tool_driven_tip"])
        self.assertFalse(hasattr(copy, "WORKING_TOOL_DRIVEN"))

    def test_page_text_is_unchanged(self):
        page = copy.page_copy()
        working = _key_after("if (w.tool_driven")
        tip = _key_after("(r.working || {}).tool_driven")
        self.assertEqual(working, tip)
        self.assertEqual(page[working], V020_TEXT)
        self.assertEqual(page[tip], V020_TEXT)
        self.assertEqual(copy.FLAG_TOOL_DRIVEN, V020_TEXT)


if __name__ == "__main__":
    unittest.main()
