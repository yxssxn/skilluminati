"""Unit tests for skilluminati.cost (zero-history cost view). Stdlib only, no Hermes, no network.

Fixtures are made-up skills in a temp HERMES_HOME; the real ~/.hermes is never read.

Run: python -m pytest tests/test_cost.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import commands, cost, tools  # noqa: E402

SKILLS = {
    # folder name differs from frontmatter name
    "folder-a": "---\nname: alpha-widget\ndescription: \"Use when polishing widgets: alpha, beta.\"\n---\n# Alpha\nbody\n",
    # folded description, long enough to be clipped at 60 chars
    "cat/bravo": (
        "---\nname: bravo-gadget\ndescription: >\n  Use when assembling gadgets from many parts and\n"
        "  checking every screw twice before shipping the gadget.\nversion: 1\n---\n# Bravo\n" + "x" * 400 + "\n"
    ),
    # disabled
    "cat/charlie": "---\nname: charlie-gizmo\ndescription: 'Gizmo helper'\n---\nshort\n",
    # hidden dir: must be skipped
    ".archive/delta": "---\nname: delta-hidden\ndescription: hidden\n---\n",
}

CONFIG = "model:\n  default: x\nskills:\n  external_dirs: []\n  disabled:\n    - charlie-gizmo\nother:\n  disabled:\n    - alpha-widget\n"


class CostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.skills = self.home / "skills"
        for rel, text in SKILLS.items():
            d = self.skills / rel
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(text, encoding="utf-8")
        (self.home / "config.yaml").write_text(CONFIG, encoding="utf-8")
        self._old = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self.home)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old
        self.tmp.cleanup()

    def test_scan_uses_frontmatter_name_and_skips_hidden(self):
        names = sorted(s["name"] for s in cost.scan_skills(self.skills))
        self.assertEqual(names, ["alpha-widget", "bravo-gadget", "charlie-gizmo"])

    def test_folded_description(self):
        by = {s["name"]: s for s in cost.scan_skills(self.skills)}
        self.assertEqual(by["bravo-gadget"]["description"],
                         "Use when assembling gadgets from many parts and checking every screw twice before shipping the gadget.")
        self.assertEqual(by["charlie-gizmo"]["description"], "Gizmo helper")
        line = cost.index_line("bravo-gadget", by["bravo-gadget"]["description"])
        self.assertTrue(line.startswith("    - bravo-gadget: Use when"))
        self.assertTrue(line.endswith("..."))
        self.assertLessEqual(len(cost.clip_description(by["bravo-gadget"]["description"])), 60)

    def test_read_disabled_only_under_skills(self):
        names, note = cost.read_disabled()
        self.assertEqual(names, ["charlie-gizmo"])
        self.assertIsNone(note)

    def test_estimate_counts_order_disabled(self):
        r = cost.estimate(model="claude-opus-5-5")
        self.assertEqual(r["label"], "estimate")
        names = [x["name"] for x in r["skills"]]
        self.assertEqual(names, ["bravo-gadget", "alpha-widget"])  # index tokens desc
        self.assertNotIn("charlie-gizmo", names)
        t = r["totals"]
        self.assertEqual(t["skill_count"], 2)
        self.assertEqual(t["disabled_count"], 1)
        self.assertEqual(t["index_tokens_per_turn"], sum(x["index_tokens"] for x in r["skills"]))
        alpha = r["skills"][1]
        expected = -(-len("    - alpha-widget: Use when polishing widgets: alpha, beta.\n") // 4)
        self.assertEqual(alpha["index_tokens"], expected)
        self.assertEqual(alpha["load_tokens"], -(-len(SKILLS["folder-a"]) // 4))
        self.assertAlmostEqual(alpha["index_cost_per_turn"], expected * 4.0 / 1e6)
        self.assertAlmostEqual(t["index_cost_per_100_turns"], t["index_cost_per_turn"] * 100, places=8)
        self.assertGreater(r["skills"][0]["load_tokens"], 100)

    def test_explicit_disabled_and_top(self):
        r = cost.estimate(skills_dir=self.skills, disabled=["bravo-gadget"], top=1)
        self.assertEqual([x["name"] for x in r["skills"]], ["alpha-widget"])
        self.assertEqual(r["totals"]["skill_count"], 2)

    def test_unknown_model_cost_none(self):
        self.assertIsNone(cost.price_per_mtok("made-up-model-9"))
        self.assertEqual(cost.price_per_mtok("claude-opus-5-5[1m]"), 4.0)
        r = cost.estimate(model="made-up-model-9")
        self.assertEqual(r["cost"], "unknown")
        self.assertIsNone(r["totals"]["index_cost_per_turn"])
        self.assertTrue(all(x["index_cost_per_turn"] is None and x["load_cost"] is None for x in r["skills"]))
        self.assertIn("unknown", cost.format_table(r))

    def test_format_table_header(self):
        out = cost.format_table(cost.estimate(model="claude-opus-5-5"))
        first = out.splitlines()[0]
        self.assertIn("claude-opus-5-5", first)
        self.assertIn("estimate (chars/4, list price)", first)
        self.assertLess(out.index("bravo-gadget"), out.index("alpha-widget"))
        self.assertNotIn("charlie-gizmo", out)

    def test_tool_returns_json(self):
        data = json.loads(tools.estimate({"model": "claude-opus-5-5", "top": 1}))
        self.assertTrue(data["success"])
        self.assertEqual(data["label"], "estimate")
        self.assertEqual(len(data["skills"]), 1)
        self.assertTrue(json.loads(tools.estimate(None))["success"])
        self.assertTrue(json.loads(tools.estimate({"top": "lots"}))["success"])

    def test_estimate_command(self):
        out = commands.handle_skilluminati("estimate claude-opus-5-5")
        self.assertIn("estimate (chars/4, list price)", out)
        self.assertEqual(commands.handle_skilluminati("help"), commands.USAGE)

    def test_missing_dir_and_bad_config(self):
        (self.home / "config.yaml").write_text("skills:\n  disabled: charlie-gizmo\n", encoding="utf-8")
        names, note = cost.read_disabled()
        self.assertEqual(names, [])
        self.assertTrue(note)
        r = cost.estimate(skills_dir=self.home / "nope", model="claude-opus-5-5", disabled=[])
        self.assertEqual(r["totals"]["skill_count"], 0)


if __name__ == "__main__":
    unittest.main()
