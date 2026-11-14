"""W1 checks: /skilluminati <skill> starts at once, shows progress, posts the verdict line,
asks the model one question at a time, and falls back to a built-in question set.
Fake model and fake chat only: no network, a temp HERMES_HOME."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest import mock

from tests import test_run as R  # noqa: E402  (FakeCtx, HomeCase, the fake history; puts plugin/ on sys.path)
from skilluminati import commands, run  # noqa: E402


class LiveCtx(R.FakeCtx):
    """A Desktop chat: posted messages are shown and can be edited."""

    def post_message(self, text):
        self.posted.append(text)
        return SimpleNamespace(ok=True, error=None, edit=lambda t: self.posted.append(t))


class CountingLlm(R.JudgeAlwaysWith):
    """Records the most calls that were ever in flight at the same time."""

    def __init__(self):
        super().__init__()
        self.now = 0
        self.most = 0
        self.lock = threading.Lock()

    def complete(self, messages, **kw):
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        try:
            time.sleep(0.002)
            return super().complete(messages, **kw)
        finally:
            with self.lock:
                self.now -= 1


class StartTests(R.HomeCase):
    def setUp(self):
        super().setUp()
        d = self.home / "skills" / "notes" / R.SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(R.SKILL_MD, encoding="utf-8")

    def tearDown(self):
        run.wait_active(10)
        super().tearDown()

    def test_starts_at_once_then_posts_the_result_line(self):
        R._db(self.home / "state.db", 6)
        llm = CountingLlm()
        ctx = LiveCtx(llm, surface="local", home=self.home)
        reading = threading.Event()
        slow_find = run.find_tasks

        def find_slowly(*a, **kw):  # reading a big history takes minutes; the command must not wait
            reading.wait(5)
            return slow_find(*a, **kw)

        with mock.patch.object(run, "find_tasks", find_slowly), mock.patch.object(run, "announce", return_value=True):
            t0 = time.monotonic()
            out = self.h(ctx, R.SKILL)
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(ctx.asked, [], "no Yes/No question")
            self.assertTrue(out.startswith(f"Bringing {R.SKILL} before the Owl: finding its latest uses"), out)
            self.assertIn("/skilluminati stop", out)
            self.assertEqual(len(out.splitlines()), 1)
            reading.set()
            self.assertTrue(run.wait_active(30))
        self.assertEqual(llm.most, 1, "one model call at a time")
        last = ctx.posted[-1]
        # F01: the line ends with how much evidence the verdict rests on
        self.assertEqual(last, f"{R.SKILL}: Enlightened (Probable). Better 6 · Same 0 · Worse 0. 6 tasks, 1 run.")

    def test_progress_is_posted_while_it_runs(self):
        R._db(self.home / "state.db", 6)
        ctx = LiveCtx(R.JudgeAlwaysWith(), surface="local", home=self.home)
        with mock.patch.object(run, "PROGRESS_EVERY_S", 0.01), mock.patch.object(run, "CALL_PAUSE_S", 0.01), \
                mock.patch.object(run, "announce", return_value=True):
            self.h(ctx, R.SKILL)
            self.assertTrue(run.wait_active(30))
        self.assertTrue(any(" of 10 compared." in p for p in ctx.posted), ctx.posted)
        from skilluminati import paths as sb_paths
        self.assertFalse((sb_paths.data_dir() / run.LIVE_FILE).exists(), "live.json is gone once the run ends")

    def test_built_in_questions_when_history_is_short(self):
        R._db(self.home / "state.db", 2)
        pack = [dict(t) for t in run.find_tasks(R.SKILL)[0]] * 4  # 8 tasks
        with mock.patch.object(run, "pack_tasks", lambda skill: pack if skill == R.SKILL else None):
            pl = run.verdict_plan(R.FakeCtx(R.JudgeAlwaysWith(), home=self.home), R.SKILL)
            self.assertEqual(pl["source"], "pack")
            self.assertEqual(len(pl["tasks"]), 8)
            pl = run.verdict_plan(R.FakeCtx(R.JudgeAlwaysWith(), home=self.home), R.SKILL, source="history")
            self.assertEqual(pl["source"], "history")

    def test_help_lists_the_three_commands(self):
        out = commands.handle_skilluminati("")
        for line in ("/skilluminati <skill>", "/skilluminati stop", "/skilluminati selftest", "Skilluminati page"):
            self.assertIn(line, out)


if __name__ == "__main__":
    import unittest
    unittest.main()


class NoPopupCtx(R.FakeCtx):
    """A Desktop chat whose popup cannot be posted (the live 12 Nov failure): post_message is slow
    and refused, the way it is when the gateway does not answer."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.post_threads = []

    def post_message(self, text):
        self.post_threads.append(threading.current_thread().name)
        time.sleep(0.2)
        return SimpleNamespace(ok=False, error="timeout", edit=lambda t: None)


class DesktopNeverWaitsTests(R.HomeCase):
    """Live test 12 Nov: on Desktop the command waited on the popup, then ran the whole test inside
    the command, and the window froze. On Desktop and the TUI it must answer at once, every time."""

    def setUp(self):
        super().setUp()
        d = self.home / "skills" / "notes" / R.SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(R.SKILL_MD, encoding="utf-8")

    def tearDown(self):
        run.wait_active(10)
        super().tearDown()

    def test_desktop_answers_at_once_even_when_the_popup_fails(self):
        R._db(self.home / "state.db", 6)
        for surf in ("desktop", "tui"):
            ctx = NoPopupCtx(R.JudgeAlwaysWith(), surface=surf, home=self.home)
            commands.bind(ctx)
            t0 = time.monotonic()
            out = commands.handle_skilluminati(R.SKILL, session_id="s1")
            took = time.monotonic() - t0
            self.assertLess(took, 0.15, f"{surf}: the command waited {took:.2f}s")
            self.assertNotIn("MainThread", ctx.post_threads, f"{surf}: popup posted inside the command")
            self.assertTrue(out.strip(), surf)
            run.wait_active(10)


class LiveLineTests(R.HomeCase):
    """The author, 12 Nov: the popup sat on 'n of m compared' for a minute at a time. It now says what the
    model is doing this second, with a counter, and stays one line under 120 characters."""

    def test_now_line_names_each_kind_of_call(self):
        from skilluminati import llm as sb_llm
        g = sb_llm.CommandGuard(None, cap=64)
        self.assertEqual(run.now_line(g), "")
        for label, want in (("cell ab12 with", "with the skill"), ("cell ab12 without", "without it"),
                            ("cell ab12 judge order1", "judging"), ("aa ab12 without_a", "noise check")):
            g.now, g.now_since = label, time.monotonic() - 42
            line = run.popup_progress(60, 64, now=run.now_line(g))
            self.assertIn(want, line)
            self.assertIn("(42s)", line)
            self.assertLess(len(line), 120, line)
            self.assertNotIn("\n", line)
