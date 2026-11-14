"""Tier 1 (F1): the model-calling commands inside the plugin. Fake LLM and fake ctx only: no network,
no Hermes imports, a temp HERMES_HOME so nothing touches the real state.db or plugin-data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import commands, llm as sb_llm, paths, realrun, run, selftest  # noqa: E402
from tests import test_selftest as T  # noqa: E402  (fake subject + judges keyed on the planted fixtures)

ACTIVE = "claude-haiku-5-5"


def _result(text, model=ACTIVE):
    return SimpleNamespace(text=text, provider="active", model=model,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12, cache_read_tokens=0))


class ActiveLlm(T.FakeLlm):
    """The self-test fake, answering as the user's active model when no model is pinned."""

    def complete(self, messages, **kw):
        self.requested = getattr(self, "requested", []) + [dict(kw)]
        kw = dict(kw)
        if kw.get("model") is None:
            kw["model"] = ACTIVE
        return super().complete(messages, **kw)


class FakeCtx:
    def __init__(self, llm=None, surface="cli", answer=None, settings=None, home=None):
        self.llm = llm
        self._surface = surface
        self._answer = answer
        self.asked = []
        self.posted = []
        self.settings = settings or {}
        self.state = SimpleNamespace(data_dir=Path(home) / "plugin-data" / "skilluminati") if home else None

    def session_surface(self):
        return self._surface

    def ask_choice(self, question, choices):
        self.asked.append((question, choices))
        return self._answer

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def post_message(self, text):
        self.posted.append(text)
        return SimpleNamespace(edit=lambda t: self.posted.append(t))


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()
        os.environ.pop("HERMES_CRON_SESSION", None)
        self.no_pause = mock.patch.object(run, "CALL_PAUSE_S", 0)
        self.no_pause.start()

    def tearDown(self):
        self.no_pause.stop()
        commands.bind(None)
        paths._data_dir_override = None
        self.env.stop()
        self.tmp.cleanup()

    def h(self, ctx, args):
        commands.bind(ctx)
        return commands.handle_skilluminati(args)


# ── the guard ────────────────────────────────────────────────────────────────

class GuardTests(unittest.TestCase):
    def test_cap_stops_and_flag_halts_later_calls(self):
        inner = mock.Mock()
        inner.complete.return_value = _result("x")
        g = sb_llm.CommandGuard(inner, cap=2)
        g.complete([], model=None)
        g.complete([], model=None)
        with self.assertRaises(sb_llm.CapExceeded):
            g.complete([], model=None)
        with self.assertRaises(sb_llm.StopRequested):
            g.complete([], model=None)
        self.assertEqual(inner.complete.call_count, 2)

    def test_usage_limit_sets_the_stop_flag(self):
        inner = mock.Mock()
        inner.complete.side_effect = RuntimeError("HTTP 429 too many requests")
        g = sb_llm.CommandGuard(inner, cap=10)
        with self.assertRaises(sb_llm.UsageLimit):
            g.complete([])
        with self.assertRaises(sb_llm.StopRequested):
            g.complete([])
        self.assertEqual(inner.complete.call_count, 1)

    def test_other_errors_do_not_stop(self):
        inner = mock.Mock()
        inner.complete.side_effect = [ValueError("bad json"), _result("ok")]
        g = sb_llm.CommandGuard(inner, cap=10)
        rec = sb_llm.complete(g, [])
        self.assertFalse(rec.ok)
        self.assertTrue(sb_llm.complete(g, []).ok)

    def test_pinned_mismatch_stops(self):
        inner = mock.Mock()
        inner.complete.return_value = _result("x", model="claude-opus-5-5")
        g = sb_llm.CommandGuard(inner, cap=10)
        with self.assertRaises(sb_llm.ModelMismatch):
            sb_llm.complete(g, [], model="claude-sonnet-5-5")
        self.assertTrue(g.stop.is_set())

    def test_active_model_change_mid_run_stops(self):
        inner = mock.Mock()
        inner.complete.side_effect = [_result("a", model="m1"), _result("b", model="m2")]
        g = sb_llm.CommandGuard(inner, cap=10)
        g.complete([])
        self.assertEqual(g.active_model, "m1")
        with self.assertRaises(sb_llm.ModelMismatch):
            g.complete([])

    def test_threads_share_one_budget(self):
        inner = mock.Mock()
        inner.complete.return_value = _result("x")
        g = sb_llm.CommandGuard(inner, cap=50)
        errors = []

        def work():
            for _ in range(20):
                try:
                    g.complete([])
                except sb_llm.RunStop as exc:
                    errors.append(exc)
        ts = [threading.Thread(target=work) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(inner.complete.call_count, 50)
        self.assertEqual(g.used, 50)


# ── where a run may start, and the confirm step ──────────────────────────────

class GateTests(HomeCase):
    def test_messaging_and_no_session_never_call(self):
        for surface in ("telegram", "discord", ""):
            llm = ActiveLlm()
            out = self.h(FakeCtx(llm, surface=surface), "selftest")
            self.assertIn("Nothing was run", out)
            self.assertIn("local session", out)
            self.assertEqual(llm.calls, [])

    def test_local_gateway_surface_is_a_local_session(self):
        # Hermes Desktop and the TUI report "local" for turns served by the local gateway.
        llm = ActiveLlm()
        ctx = FakeCtx(llm, surface="local", answer=None)
        out = self.h(ctx, "selftest")
        self.assertNotIn("Nothing was run", out)
        self.assertEqual(ctx.asked, [], "W1: no Yes/No question, the run starts at once")
        self.assertTrue(llm.calls)

    def test_cron_never_calls_even_with_yes(self):
        llm = ActiveLlm()
        with mock.patch.dict(os.environ, {"HERMES_CRON_SESSION": "1"}):
            out = self.h(FakeCtx(llm, surface="cli"), "selftest --yes")
        self.assertIn("cron", out)
        self.assertEqual(llm.calls, [])

    def test_plan_names_models_and_tokens(self):
        out = self.h(FakeCtx(ActiveLlm(), surface="telegram"), "selftest")
        self.assertIn("108 calls", out)
        self.assertIn("About 270,000 tokens and 9 minutes.", out)
        self.assertIn("on your active model:", out)
        self.assertNotIn("judge", out, "the judge is the same model, so it is not named again")
        self.assertIn("3 planted skills", out)
        self.assertLessEqual(len(out.splitlines()), 3, out)

    def test_judges_are_configurable(self):
        ctx = FakeCtx(ActiveLlm(), surface="telegram",
                      settings={"judge.model": "claude-sonnet-5-5", "judge.provider": "p1"})
        out = self.h(ctx, "selftest")
        self.assertIn("judge claude-sonnet-5-5", out)
        self.assertIn("harm judge claude-sonnet-5-5", out, "harm judge defaults to the judge")

    def test_full_is_behind_a_flag_and_respects_max_calls(self):
        out = self.h(FakeCtx(ActiveLlm(), surface="telegram"), "selftest --full")
        self.assertIn("360 calls", out)
        out = self.h(FakeCtx(ActiveLlm(), surface="cli", settings={"max_calls": 300}), "selftest --full --yes")
        self.assertIn("above max_calls (300)", out)


# ── selftest end to end ──────────────────────────────────────────────────────

class SelftestTests(HomeCase):
    def test_quick_runs_on_the_active_model_under_one_budget(self):
        llm = ActiveLlm(harm_fn=lambda a1, a2: (False, False))
        ctx = FakeCtx(llm, surface="cli", home=self.home)
        out = self.h(ctx, "selftest --yes")
        self.assertEqual(len(llm.calls), 108)
        self.assertTrue(all(kw["model"] is None and kw["provider"] is None for kw in llm.requested),
                        "no hard-coded provider or model by default")
        self.assertIn("108 calls", out)
        self.assertIn("the Adept (helps)", out)
        self.assertIn("the Impostor (sabotages)", out)
        self.assertIn("the Idler (does nothing)", out)
        self.assertIn("3 of 3 as expected", out)
        runs = list((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual(stat.S_IMODE(runs[0].stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((runs[0] / "prompts-quick.jsonl").stat().st_mode), 0o600)

    def test_usage_limit_halts_the_selftest(self):
        class Limited(ActiveLlm):
            n = 0

            def complete(self, messages, **kw):
                Limited.n += 1
                if Limited.n == 5:
                    raise RuntimeError("usage limit reached")
                return super().complete(messages, **kw)
        llm = Limited()
        out = self.h(FakeCtx(llm, surface="cli"), "selftest --yes")
        self.assertIn("Stopped early: your provider's usage limit was reached. This is a partial result.", out)
        self.assertEqual(len(llm.calls), 4)

    def test_model_swap_halts_the_selftest(self):
        class Swap(ActiveLlm):
            def complete(self, messages, **kw):
                r = super().complete(messages, **kw)
                if len(self.calls) == 3:
                    r.model = "some-fallback-model"
                return r
        llm = Swap()
        out = self.h(FakeCtx(llm, surface="cli"), "selftest --yes")
        self.assertIn("Stopped early: an answer came from a different model than planned.", out)
        self.assertEqual(len(llm.calls), 3)

    def test_fixtures_bundled_inside_the_plugin(self):
        self.assertEqual(selftest.DEFAULT_FIXTURES, paths.FIXTURES_DIR)
        self.assertTrue(str(paths.FIXTURES_DIR).startswith(str(PLUGIN_DIR)))
        for name in ("tasks.jsonl", "checks.py", "rubric_purpose.md", "rubric_harm_v2.md",
                     "helpful/SKILL.md", "poisoned/SKILL.md", "placebo/SKILL.md", "bloat/SKILL.md"):
            self.assertTrue((paths.FIXTURES_DIR / name).is_file(), name)

    @unittest.skipUnless((REPO / "fixtures").is_dir(), "dev checkout only: the root fixtures/ copy is not shipped")
    def test_bundled_fixtures_match_the_dev_copy(self):
        for name in ("tasks.jsonl", "checks.py", "rubric_purpose.md", "rubric_purpose.sha256",
                     "rubric_harm_v2.md", "rubric_harm_v2.sha256", "helpful/SKILL.md"):
            self.assertEqual((paths.FIXTURES_DIR / name).read_bytes(), (REPO / "fixtures" / name).read_bytes(), name)


class NoParentsTests(unittest.TestCase):
    def test_no_module_resolves_above_the_plugin(self):
        for py in (PLUGIN_DIR / "skilluminati").glob("*.py"):
            text = py.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"parents\[[2-9]\]", text.replace("no ``parents[2]``", "")), py.name)


# ── a verdict end to end ─────────────────────────────────────────────────────

SKILL = "tidy-notes"
SKILL_MD = "---\nname: tidy-notes\ndescription: Use when tidying meeting notes into actions.\n---\nAlways list owners first.\n"


def _db(path: Path, n: int) -> None:
    from tests.test_mine import MESSAGES_DDL, PROMPTS_DDL, SESSIONS_DDL, _Builder
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")
    for i in range(n):
        b = _Builder(conn, f"s{i}", system_prompt="You are a helpful agent.")
        # Requests must differ: a run skips near-identical ones (mine.latest_tasks).
        b.user(f"Tidy these notes: {hashlib.sha256(str(i).encode()).hexdigest()}").call("skill_view", name=SKILL).assistant("Done.")
    conn.commit()
    conn.close()


class JudgeAlwaysWith:
    """Subject: only the WITH arm (it has the skill text) says "Owners first". Judge (system prompt =
    the purpose rubric): prefers the answer that says it, in either order."""

    RUBRIC = (paths.FIXTURES_DIR / "rubric_purpose.md").read_text(encoding="utf-8")

    def __init__(self):
        self.calls = []

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if messages[0]["content"] == self.RUBRIC:
            a1 = messages[1]["content"].split("ANSWER 1:\n", 1)[1]
            winner = "1" if a1.lstrip().startswith("Owners first") else "2"
            return _result(json.dumps({"winner": winner, "reason": "lists owners"}))
        with_skill = "Always list owners first." in json.dumps(messages)
        return _result("Owners first: Sam. Actions follow." if with_skill else "Actions: do things.")


class VerdictTests(HomeCase):
    def _skills(self):
        d = self.home / "skills" / "notes" / SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    def test_not_enough_history_says_so_and_calls_nothing(self):
        self._skills()
        _db(self.home / "state.db", 2)
        llm = JudgeAlwaysWith()
        out = self.h(FakeCtx(llm, surface="cli"), f"{SKILL} --yes")
        self.assertIn(f"Not enough history to test '{SKILL}'", out)
        self.assertIn("2 of 6 tasks needed", out)  # F04: the gate is verdict.MIN_TESTS
        self.assertIn("No calls were made", out)
        self.assertEqual(llm.calls, [])

    def test_verdict_runs_and_is_stored_for_the_desktop_page(self):
        self._skills()
        _db(self.home / "state.db", 6)
        llm = JudgeAlwaysWith()
        ctx = FakeCtx(llm, surface="cli", home=self.home)
        out = self.h(ctx, f"{SKILL} --yes")
        self.assertIn(f"{SKILL}: Enlightened (Probable). Answers are clearly better with it.", out)
        self.assertIn(f"Model: {ACTIVE}", out)
        self.assertTrue(all(kw["model"] is None and kw["provider"] is None for _, kw in llm.calls))
        # 6 tasks + 4 A/A (AA_LIMIT) = 10 pairs x 4 calls
        self.assertEqual(len(llm.calls), 40)
        data = json.loads((self.home / "plugin-data" / "skilluminati" / "verdicts.json").read_text())
        row = data["runs"][0]
        self.assertEqual((row["skill"], row["model"], row["wins"]), (SKILL, ACTIVE, 6))
        # relative skill path only: no home directory in any prompt
        self.assertFalse(any(str(self.home) in json.dumps(m) for m, _ in llm.calls))



if __name__ == "__main__":
    unittest.main()
