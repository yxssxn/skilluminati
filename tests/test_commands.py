"""Unit tests for the /skilluminati command wiring and verdict labels. Stdlib only, no network.

Run from the project root:
    python -m pytest tests/test_commands.py -v
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

plugin = importlib.import_module("plugin")
commands = importlib.import_module("plugin.skilluminati.commands")
cost = importlib.import_module("plugin.skilluminati.cost")


class FakeCtx:
    """Records register_command / register_tool calls; exposes no model."""

    def __init__(self, model=None):
        self.commands = []
        self.tools = []
        if model is not None:
            self.model = model

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands.append({"name": name, "handler": handler,
                              "description": description, "args_hint": args_hint})

    def register_tool(self, **kw):
        self.tools.append(kw)


FAKE_RESULT = {
    "label": "estimate", "method": cost.METHOD, "model": None, "price_per_mtok_input": None,
    "price_source": None, "cost": "unknown",
    "totals": {"skill_count": 2, "disabled_count": 0, "index_tokens_per_turn": 30,
               "load_tokens_all": 300, "index_cost_per_turn": None, "index_cost_per_100_turns": None},
    "skills": [
        {"name": "alpha", "index_tokens": 20, "load_tokens": 200,
         "index_cost_per_turn": None, "load_cost": None},
        {"name": "bravo", "index_tokens": 10, "load_tokens": 100,
         "index_cost_per_turn": None, "load_cost": None},
    ],
    "notes": [],
}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        plugin.register(self.ctx)
        self.by_name = {c["name"]: c for c in self.ctx.commands}

    def tearDown(self):
        commands.bind(None)

    def test_one_command_no_alias(self):
        self.assertEqual(set(self.by_name), {"skilluminati"})
        self.assertFalse(hasattr(commands, "handle_bench"))

    def test_descriptions_and_hint(self):
        main = self.by_name["skilluminati"]
        self.assertEqual(main["description"],
                         "Test whether a skill still earns its keep on your current model (Skilluminati)")
        self.assertEqual(main["args_hint"],
                         "<skill> | last | stop | selftest")

    def test_tool_still_registered(self):
        self.assertEqual([t["name"] for t in self.ctx.tools], ["skilluminati_estimate", "skilluminati_result"])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": self.tmp.name})
        self.env.start()
        commands.bind(FakeCtx())
        self.estimate_calls = []

        def fake_estimate(**kw):
            self.estimate_calls.append(kw)
            r = dict(FAKE_RESULT)
            r["model"] = kw.get("model")
            return r

        self.patches = [mock.patch.object(cost, "estimate", side_effect=fake_estimate)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        commands.bind(None)
        self.env.stop()
        self.tmp.cleanup()

    def h(self, args):
        return commands.handle_skilluminati(args)

    def test_help_text(self):
        for args in ("", "help", "--help", None):
            out = self.h(args)
            self.assertIn("skilluminati", out.lower())
            self.assertNotIn("benchmark", out.lower())
            self.assertNotIn("/bench", out)
            self.assertIn("selftest", out)
            self.assertIn("Skilluminati page", out)
            self.assertIn("stop", out)
            self.assertNotIn("not wired", out)
            self.assertNotIn("\u2014", out)

    def test_estimate_named_model(self):
        out = self.h("estimate claude-opus-5-5")
        self.assertEqual(self.estimate_calls[-1]["model"], "claude-opus-5-5")
        self.assertIn("Cost applies to: claude-opus-5-5", out)
        self.assertIn("Skilluminati cost view: model claude-opus-5-5", out)
        self.assertIn("alpha", out)
        self.assertNotIn(commands.ALL_HEADER, out)

    def test_estimate_unknown_model(self):
        out = self.h("estimate")
        self.assertIsNone(self.estimate_calls[-1]["model"])
        self.assertIn("Cost applies to: unknown model", out)
        self.assertIn("model unknown model", out)

    def test_estimate_uses_ctx_model(self):
        commands.bind(FakeCtx(model="claude-opus-5-5"))
        out = self.h("estimate")
        self.assertIn("Cost applies to: claude-opus-5-5", out)

    def test_all_header_and_every_skill(self):
        with mock.patch.object(cost, "format_table", wraps=cost.format_table) as ft:
            out = self.h("--all claude-opus-5-5")
            self.assertGreaterEqual(ft.call_args.kwargs.get("limit", 0), 2)
        self.assertTrue(out.startswith(commands.ALL_HEADER))
        self.assertIn("Cost applies to: claude-opus-5-5", out)
        self.assertIn("alpha", out)
        self.assertIn("bravo", out)

    def test_all_unknown_model(self):
        out = self.h("--all")
        self.assertIn(commands.ALL_HEADER, out)
        self.assertIn("unknown model", out)

    def test_estimate_failure_never_raises(self):
        with mock.patch.object(cost, "format_table", side_effect=RuntimeError("boom")):
            out = self.h("--all")
        self.assertIn("failed", out)
        self.assertIn("boom", out)

    def test_skill_without_history_says_so_plainly(self):
        commands.bind(SimpleNamespace(session_surface=lambda: "cli"))
        out = self.h("some-skill")
        self.assertIn("Not enough history to test 'some-skill'", out)
        self.assertIn("no session history was found", out)
        for word in ("Earns its keep", "Gets in the way", "Model does this already"):
            self.assertNotIn(word, out)

    def test_selftest_without_session_shows_plan_and_runs_nothing(self):
        out = self.h("selftest")
        self.assertIn("Self-test (quick)", out)
        self.assertIn("108 calls", out)
        self.assertIn("Nothing was run", out)

    def test_unknown_option(self):
        out = self.h("--nope")
        self.assertIn("Unknown option", out)

    def test_no_em_dashes(self):
        for args in ("help", "estimate x", "--all", "skill-x", "selftest", "--nope"):
            self.assertNotIn("\u2014", self.h(args))


class LabelTests(unittest.TestCase):
    def test_user_label(self):
        ul = commands.user_label
        self.assertEqual(ul("Keep", None), "Earns its keep")
        self.assertEqual(ul("Keep", "positive"), "Earns its keep")
        self.assertEqual(ul("Unsure", None), "Not sure yet")
        self.assertEqual(ul("Drop", "none"), "Model does this already")
        self.assertEqual(ul("Drop", "negative"), "Gets in the way")
        self.assertEqual(ul("Drop", None), "Model does this already")
        self.assertEqual(ul("Drop"), "Model does this already")
        self.assertEqual(ul("garbage", None), "Not sure yet")

    def test_stale_label(self):
        sl = commands.stale_label
        self.assertIsNone(sl("claude-opus-5-5", "claude-opus-5-5"))
        self.assertIsNone(sl(None, "claude-opus-5-5"))
        self.assertIsNone(sl("claude-opus-5-5", None))
        self.assertIsNone(sl("", "x"))
        self.assertIsNone(sl("unknown", "claude-opus-5-5"))
        self.assertEqual(sl("claude-haiku-5-5", "claude-opus-5-5"), "stale, re-test on claude-opus-5-5")


if __name__ == "__main__":
    unittest.main()
