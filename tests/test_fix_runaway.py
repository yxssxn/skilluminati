"""FIX-BRIEF (11 Nov): the self-test runaway. No-tools note on every subject call, the runaway and empty
answer guard, a command that returns at once and runs in the background, /skilluminati stop, and the
real model id on the plan. Fake LLM and fake ctx only: no network, no Hermes imports."""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import test_run as R  # noqa: E402  (FakeCtx, ActiveLlm, HomeCase; puts plugin/ on sys.path)
from skilluminati import commands, llm as sb_llm, realrun, replay, run, selftest  # noqa: E402
from tests import test_selftest as T  # noqa: E402


def _subjects(llm):
    """(messages, kw) of every subject call the fake saw (judge calls carry a rubric as system prompt)."""
    return [(m, kw) for m, kw in llm.calls if m[0]["content"] not in (T.RUBRIC_JOB, T.RUBRIC_HARM)]


class RunawayLlm(R.ActiveLlm):
    """Answers subject call number ``at`` with a fake ``skill_view`` loop of ``chars`` characters."""

    def __init__(self, at=1, text=None, out_tokens=128_000, **kw):
        super().__init__(**kw)
        self.at, self.text, self.out_tokens, self.n_subject = at, text, out_tokens, 0

    def complete(self, messages, **kw):
        r = super().complete(messages, **kw)
        if messages[0]["content"] not in (T.RUBRIC_JOB, T.RUBRIC_HARM):
            self.n_subject += 1
            if self.n_subject == self.at:
                r.text = self.text if self.text is not None else 'skill_view(name="ticket-ids")\n' * 7000
                r.usage = SimpleNamespace(input_tokens=10, output_tokens=self.out_tokens, total_tokens=self.out_tokens,
                                          cache_read_tokens=0)
        return r


class ReplayNoteTests(R.HomeCase):
    def test_every_subject_call_carries_the_no_tools_note(self):
        llm = R.ActiveLlm()
        self.h(R.FakeCtx(llm, surface="cli", home=self.home), "selftest --yes")
        subs = _subjects(llm)
        self.assertEqual(len(subs), 36, "quick: 18 pairs x 2 subject calls")
        for m, _ in subs:
            self.assertTrue(m[0]["content"].endswith(replay.REPLAY_NOTE), "note on both arms, every task")
        self.assertIs(realrun.REPLAY_NOTE, replay.REPLAY_NOTE, "one text for real runs and the self-test")

    def test_aa_arms_both_get_the_note(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            st = selftest.SelfTest(T.FakeLlm(), fixtures_dir=T.FIXTURES, out_dir=Path(tmp), run="a1",
                                   task_ids=[T.TASKS[0]["id"]], log=lambda s: None, sleep=lambda s: None)
            llm = st.llm
            st.pair(T.TASKS[0], selftest.AA)
            subs = _subjects(llm)
            self.assertEqual(len(subs), 2)
            self.assertTrue(all(m[0]["content"].endswith(replay.REPLAY_NOTE) for m, _ in subs))


class AnswerCheckTests(unittest.TestCase):
    def rec(self, text, out=10):
        return sb_llm.CallRecord(ok=True, text=text, output_tokens=out, latency_s=820.0)

    def test_boundaries_match_the_real_run_rule(self):
        f = sb_llm.RUNAWAY_TOKEN_FACTOR  # hidden thinking counts as output, so the factor is loose
        self.assertIsNone(sb_llm.answer_problem(self.rec("x", f * 600), 600), "exactly the factor is allowed")
        self.assertEqual(sb_llm.answer_problem(self.rec("x", f * 600 + 1), 600), sb_llm.RUNAWAY)
        self.assertIsNone(sb_llm.answer_problem(self.rec("x" * 20_000), 600), "exactly 20,000 chars is allowed")
        self.assertEqual(sb_llm.answer_problem(self.rec("x" * 20_001), 600), sb_llm.RUNAWAY)
        self.assertEqual(sb_llm.answer_problem(self.rec("", 68), 600), sb_llm.EMPTY_ANSWER)
        self.assertEqual(sb_llm.answer_problem(self.rec("  \n", 50), 600), sb_llm.EMPTY_ANSWER)

    def test_no_stop_sequence_is_invented(self):
        sent = []
        sb_llm.complete(SimpleNamespace(complete=lambda m, **kw: sent.append(kw) or SimpleNamespace(text="a")), [])
        self.assertNotIn("stop", sent[0])
        self.assertNotIn("stop_sequences", sent[0])


class RunawayStopsTheRunTests(R.HomeCase):
    def test_runaway_stops_the_run_and_is_never_judged(self):
        llm = RunawayLlm(at=2)
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), "selftest --yes")
        judged = [m for m, _ in llm.calls if m[0]["content"] in (T.RUBRIC_JOB, T.RUBRIC_HARM)]
        self.assertEqual(judged, [], "the pair with the runaway is never judged")
        self.assertEqual(len(llm.calls), 2, "the run stops on the first runaway: no further call")
        self.assertIn("Stopped early: one answer ran far too long.", out)
        self.assertNotIn("RunawayAnswer", out, "the reply gives the plain reason only")
        err = self.stop_error()  # the full error stays in result.json (Round 3 item 2)
        self.assertIn("task t01_id_long_impact (helpful, without)", err)
        self.assertIn("128,000 output tokens", err)
        self.assertIn("s; cap 600 tokens", err, "the stored error says how long the call took")
        self.assertIn("not sent to the judges", err)

    def stop_error(self) -> str:
        runs = sorted((Path(self.home) / "plugin-data" / "skilluminati" / "runs").iterdir())
        res = json.loads((runs[-1] / "result.json").read_text())
        stop = res.get("stopped_early") or (res.get("run") or {}).get("stopped_early") or {}
        return f"{stop.get('reason')}: {stop.get('error')}"

    def test_runaway_by_characters_alone(self):
        llm = RunawayLlm(at=1, text="y" * 20_001, out_tokens=300)
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), "selftest --yes")
        self.assertIn("Stopped early: one answer ran far too long.", out)
        self.assertIn("RunawayAnswer", self.stop_error())
        self.assertEqual(len(llm.calls), 1)

    def test_runaway_text_is_not_logged_in_full(self):
        llm = RunawayLlm(at=1)
        self.h(R.FakeCtx(llm, surface="cli", home=self.home), "selftest --yes")
        run_dir = next((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        prompts = (run_dir / "prompts-quick.jsonl").read_text(encoding="utf-8")
        self.assertLess(len(prompts), 60_000)
        calls = (run_dir / "calls-quick.jsonl").read_text(encoding="utf-8")
        self.assertIn('"ok": false', calls)
        self.assertIn('"error_type": "RunawayAnswer"', calls)

    def test_empty_answer_counts_as_failed_not_ok(self):
        llm = RunawayLlm(at=1, text="", out_tokens=68)
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), "selftest --yes")
        run_dir = next((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        import json
        rows = [json.loads(l) for l in (run_dir / "calls-quick.jsonl").read_text().splitlines()]
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["error_type"], sb_llm.EMPTY_ANSWER)
        pairs = [json.loads(l) for l in (run_dir / "pairs-quick.jsonl").read_text().splitlines()]
        self.assertEqual(pairs[0]["outcome"], "error", "an empty answer is never judged as a real answer")
        self.assertNotIn("RunawayAnswer", out, "an empty answer fails the pair, it does not stop the run")
        self.assertEqual(len(llm.calls), 108 - 4, "that pair's two judge calls are not made")

    def test_real_run_subject_uses_the_same_guard(self):
        import threading as th
        rr = realrun.RealRunner.__new__(realrun.RealRunner)
        rr.cfg = SimpleNamespace(subject_model=None, subject_max_tokens=600, runaway_max_chars=sb_llm.RUNAWAY_MAX_CHARS, subject_temperature=None,
                                 provider=None, timeout_s=120.0)
        rr.names, rr._lock, rr.calls = [], th.Lock(), []
        rr.budget = SimpleNamespace(reserve=lambda: 1)
        rr.calls_path, rr.raw_path = self.home / "calls.jsonl", self.home / "raw.jsonl"
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

        def answering(text, out):
            return SimpleNamespace(complete=lambda m, **kw: SimpleNamespace(
                text=text, model="m", provider="p",
                usage=SimpleNamespace(input_tokens=1, output_tokens=out, total_tokens=out, cache_read_tokens=0)))
        rr.llm = answering("", 40)
        rec = rr._subject(msgs, {"skill": "s", "arm": "with", "task_hash": "h1", "role": "subject"}, "lbl")
        self.assertFalse(rec.ok)
        self.assertEqual(rec.error_type, sb_llm.EMPTY_ANSWER)
        rr.llm = answering("z" * 30_000, 40)
        with self.assertRaises(sb_llm.RunawayAnswer):
            rr._subject(msgs, {"skill": "s", "arm": "with", "task_hash": "h2", "role": "subject"}, "lbl")
        self.assertIn('"error_type": "RunawayAnswer"', rr.calls_path.read_text())


class BlockingLlm(R.ActiveLlm):
    """Every call blocks until ``release`` is set (a slow model call)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.release = threading.Event()
        self.entered = threading.Event()

    def complete(self, messages, **kw):
        self.entered.set()
        self.release.wait(10)
        return super().complete(messages, **kw)


class LiveCtx(R.FakeCtx):
    """A session that can take posted messages (Desktop): ``post_message`` answers ok."""

    def post_message(self, text):
        self.posted.append(text)
        return SimpleNamespace(ok=True, error=None, edit=lambda t: self.posted.append(t))


class BackgroundTests(R.HomeCase):
    def tearDown(self):
        run.stop_active("test teardown")
        run.wait_active(5)
        super().tearDown()

    def test_command_returns_at_once_while_a_model_call_blocks(self):
        llm = BlockingLlm()
        ctx = LiveCtx(llm, surface="local", home=self.home)
        t0 = time.monotonic()
        out = self.h(ctx, "selftest")
        took = time.monotonic() - t0
        self.assertLess(took, 1.0, f"returned in {took:.2f}s")
        self.assertTrue(out.startswith("The Owl's trial started"), out)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertTrue(llm.entered.wait(5), "the job is running in the background")
        llm.release.set()
        self.assertTrue(run.wait_active(20))
        self.assertTrue(any("as expected" in p for p in ctx.posted), "the end line posts into the popup")
        self.assertTrue(any("of 18 compared" in p for p in ctx.posted), "progress posts too")
        for p in ctx.posted:
            self.assertNotIn("\n", p, "every popup is one line")
            self.assertLess(len(p), 120, p)

    def test_stop_ends_a_run(self):
        llm = BlockingLlm()
        ctx = LiveCtx(llm, surface="local", home=self.home)
        self.h(ctx, "selftest --yes")
        self.assertTrue(llm.entered.wait(5))
        t0 = time.monotonic()
        out = self.h(ctx, "stop")
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertIn("Stopping", out)
        llm.release.set()
        self.assertTrue(run.wait_active(20))
        self.assertLessEqual(len(llm.calls), 2, "no new call starts after stop")
        self.assertTrue(any("stopped after" in p for p in ctx.posted), ctx.posted)
        runs = list((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        report = (runs[0] / "report.md").read_text(encoding="utf-8")
        self.assertIn("Stopped early: you ran /skilluminati stop.", report)

    def test_stop_with_nothing_running(self):
        out = self.h(LiveCtx(R.ActiveLlm(), surface="local", home=self.home), "stop")
        self.assertIn("No Skilluminati run is going", out)

    def test_second_start_is_refused_while_one_runs(self):
        llm = BlockingLlm()
        ctx = LiveCtx(llm, surface="local", home=self.home)
        self.h(ctx, "selftest --yes")
        self.assertTrue(llm.entered.wait(5))
        out = self.h(ctx, "selftest --yes")
        self.assertIn("already going", out)
        self.assertIn("/skilluminati stop", out)
        llm.release.set()

    def test_no_chat_to_post_into_runs_in_the_foreground(self):
        class NoPost(R.FakeCtx):
            def post_message(self, text):
                return SimpleNamespace(ok=False, error="gateway_unavailable", edit=lambda t: None)
        llm = R.ActiveLlm()
        out = self.h(NoPost(llm, surface="cli", home=self.home), "selftest --yes")
        self.assertIn("3 of 3 as expected", out, "hermes chat -q: no live chat, so it runs and returns")
        self.assertEqual(len(llm.calls), 108)


class ModelOnPlanTests(R.HomeCase):
    def test_plan_names_the_real_model_id(self):
        with mock.patch.object(run, "active_model_id", return_value="claude-opus-5-5[1m]"):
            out = self.h(R.FakeCtx(R.ActiveLlm(), surface="telegram"), "selftest")
        self.assertIn("on claude-opus-5-5[1m]:", out)
        self.assertEqual(out.count("claude-opus-5-5[1m]"), 1, "the model is named once when every role shares it")

    def test_unknown_model_says_so(self):
        with mock.patch.object(run, "active_model_id", return_value=None):
            out = self.h(R.FakeCtx(R.ActiveLlm(), surface="telegram"), "selftest")
        self.assertIn("on your active model:", out)


if __name__ == "__main__":
    unittest.main()
