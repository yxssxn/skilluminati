"""In plain words: the plugin ships one small skill so the agent knows Skilluminati exists when a
user asks "is this skill worth keeping?". These checks make sure it is registered through
Hermes's public ``ctx.register_skill``, points at the two read-only tools, never tells the agent
to start a test itself, and that an older Hermes without ``register_skill`` still loads the plugin.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "plugin" / "skills" / "skilluminati" / "SKILL.md"


class FakeCtx:
    def __init__(self, with_skills: bool = True) -> None:
        self.commands, self.tools, self.skills = [], [], []
        if with_skills:
            self.register_skill = lambda name, path, description="": self.skills.append((name, Path(path), description))
        self.llm = None

    def register_command(self, name, **kw):
        self.commands.append(name)

    def register_tool(self, name, **kw):
        self.tools.append(name)


def load_plugin():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import plugin as plugin_pkg  # the plugin folder is the package Hermes loads
    return importlib.reload(plugin_pkg)


class BundledSkill(unittest.TestCase):
    def test_skill_file_says_the_right_things(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: skilluminati\n"))
        for word in ("skilluminati_result", "skilluminati_estimate", "exactly as returned", "Never start one yourself"):
            self.assertIn(word, text)
        desc = text.split("description:", 1)[1].split("\n", 1)[0].strip().strip('"')
        self.assertLessEqual(len(desc), 80, desc)

    def test_register_adds_the_skill(self) -> None:
        ctx = FakeCtx()
        load_plugin().register(ctx)
        self.assertEqual([s[0] for s in ctx.skills], ["skilluminati"])
        name, path, desc = ctx.skills[0]
        self.assertEqual(path.resolve(), SKILL_MD.resolve())
        self.assertIn("worth keeping", desc)
        self.assertEqual(sorted(ctx.tools), ["skilluminati_estimate", "skilluminati_result"])

    def test_old_hermes_without_register_skill_still_loads(self) -> None:
        ctx = FakeCtx(with_skills=False)
        load_plugin().register(ctx)
        self.assertEqual(ctx.commands, ["skilluminati"])
        self.assertEqual(len(ctx.tools), 2)

    def test_args_hint_lists_last(self) -> None:
        self.assertIn("last", load_plugin().ARGS_HINT)


if __name__ == "__main__":
    unittest.main()
