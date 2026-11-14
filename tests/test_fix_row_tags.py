"""In plain words: the Desktop row shows Stale, Skill changed and Tool-driven as small tags beside the
verdict, the way it shows "Self-judged" (fix batch S7), so a row never reads as a clean
"Enlightened (Probable)" while those flags sit only in the expanded panel. Text-level checks on the
page wording and plugin.js, as lane B did. Stdlib only, no model calls.
"""

from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

copy = importlib.import_module("plugin.skilluminati.copy")
JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = JS.index(f"function {name}(")
    nxt = JS.find("\nfunction ", start + 1)
    return JS[start:nxt if nxt != -1 else len(JS)]


class RowTags(unittest.TestCase):
    def test_page_copy_has_a_tag_and_tip_for_each(self):
        page = copy.page_copy()
        self.assertEqual(page["stale_tag"], "Stale")
        self.assertEqual(page["skill_changed_tag"], "Skill changed")
        self.assertEqual(page["tool_driven_tag"], "Tool-driven")
        for k in ("stale_tip", "skill_changed_tip", "tool_driven_tip"):
            self.assertTrue(page[k], k)
        self.assertEqual(page["tool_driven_tip"], copy.FLAG_TOOL_DRIVEN)
        self.assertTrue(page["skill_changed_tip"].startswith(copy.FLAG_SKILL_CHANGED))

    def test_the_row_cell_renders_each_tag_beside_the_verdict(self):
        cell = _function("ResultCell")
        for flag, tag in (("r.stale", "c.stale_tag"), ("r.skill_changed", "c.skill_changed_tag"),
                          ("(r.working || {}).tool_driven", "c.tool_driven_tag")):
            self.assertIn(flag, cell)
            self.assertIn(tag, cell)
        self.assertIn("r.same_judge", cell)  # lane B's tag is still there
        line = next(x for x in cell.splitlines() if "VerdictWord" in x)
        for tag in ("c.stale_tag", "c.skill_changed_tag", "c.tool_driven_tag", "SameJudge"):
            self.assertIn(tag, line, "on the verdict line")

    def test_tags_share_the_self_judged_look(self):
        tag = _function("RowTag")
        same = _function("SameJudge")
        m = re.search(r"style: (\{[^}]*\})", same)
        assert m is not None
        self.assertIn(m.group(1), tag)

    def test_stale_hover_names_the_models_when_known(self):
        self.assertIn("staleTip(r, c)", _function("ResultCell"))
        tip = _function("staleTip")
        self.assertIn("r.show_flags", tip)
        self.assertIn("c.stale_tip", tip)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_plugin_js_parses(self):
        out = subprocess.run(["node", "--check", str(ROOT / "plugin" / "desktop" / "plugin.js")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
