"""In plain words: checks that a real skill test runs end to end with a fake model, keeps private
text private and adds up to the right answer.

Unit tests for ``skilluminati.realrun`` (Step 8). Mock LLM only: no network, no Hermes imports,
no real tasks. Synthetic tasks and a synthetic skills tree are built in a temp dir."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
FIXTURES = PLUGIN_DIR / "skilluminati" / "fixtures"  # the bundled copy; the dev-repo fixtures/ is byte-identical (test_run)
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import realrun, runner, stats  # noqa: E402

# Pinned routes for these tests only; the plugin's RealRunConfig defaults to the active model (None).
SUB_PROVIDER, SUBJECT_MODEL, JUDGE_MODEL = "test-provider", "claude-opus-5-5", "claude-haiku-5-5"

PLANTED = "Quokkaberry"  # the planted deny-list name
SKILL_A, SKILL_B = "alpha-skill", "beta-skill"
SYSTEM = (
    "You are an assistant.\n\n<available_skills>\n"
    f"  - {SKILL_A}: Use when alpha things happen.\n"
    f"  - {SKILL_B}: Use when beta things happen.\n"
    "  - git: Git workflows.\n</available_skills>\n"
)


def _skill_md(name: str, desc: str) -> str:
    return f"---\nname: {name}\ndescription: \"{desc}\"\n---\n\n# {name}\n\nAlways answer with the word PLATYPUS first, then the rest.\n"


def _task(skill: str, i: int, user: str = "do the thing") -> dict:
    return {"skill": skill, "kept": True, "session_id": f"s{skill}{i}", "load_message_id": i,
            "system_prompt": SYSTEM, "user_turn": f"{user} {i}", "mode": "x"}


def _frozen(skill: str, i: int) -> dict:
    return {**_task(skill, i, "frozen thing"),
            "frozen_messages": [
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                     "function": {"name": "terminal", "arguments": json.dumps({"command": "ls"})}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "terminal", "content": "a.txt"},
            ], "load_position": 2}


class FakeLlm:
    def __init__(self, judge_text='{"winner": "1", "reason": "Answer 1 is wrong and misses the point."}', model_map=None):
        self.calls = []
        self.judge_text = judge_text
        self.model_map = model_map or {}

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        model = kw["model"]
        if messages[0]["content"].startswith("# Purpose-based pairwise judge rubric"):
            text = self.judge_text
        else:
            text = "PLATYPUS answer." if any(m.get("role") == "tool" and "PLATYPUS" in (m.get("content") or "") for m in messages) else "plain answer."
        return SimpleNamespace(text=text, provider=kw["provider"], model=self.model_map.get(model, model),
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10, total_tokens=110, cache_read_tokens=0))


class Selection(unittest.TestCase):
    def test_cap_and_strict_first_in_file_order(self):
        strict = [_task(SKILL_A, i) for i in range(5)] + [_task(SKILL_B, 99)]
        frozen = [_frozen(SKILL_A, 100 + i) for i in range(5)]
        out = realrun.select_tasks(strict, frozen, SKILL_A, cap=7)
        self.assertEqual(len(out), 7)
        self.assertEqual([t["mode"] for t in out], ["strict"] * 5 + ["frozen"] * 2)
        self.assertEqual([t["load_message_id"] for t in out], [0, 1, 2, 3, 4, 100, 101])
        self.assertTrue(all(t["skill"] == SKILL_A for t in out))

    def test_dropped_tasks_are_skipped(self):
        strict = [{**_task(SKILL_A, 1), "kept": False}, _task(SKILL_A, 2)]
        self.assertEqual([t["load_message_id"] for t in realrun.select_tasks(strict, [], SKILL_A)], [2])

    def test_aa_round_robin_any_mode(self):
        per = {SKILL_A: [{**_task(SKILL_A, i), "mode": "strict"} for i in range(3)] + [{**_frozen(SKILL_A, 9), "mode": "frozen"}],
               SKILL_B: [{**_task(SKILL_B, i), "mode": "strict"} for i in range(1)]}
        aa = realrun.select_aa_tasks(per, n=10)
        self.assertEqual([(t["skill"], t["load_message_id"]) for t in aa],
                         [(SKILL_A, 0), (SKILL_B, 0), (SKILL_A, 1), (SKILL_A, 2), (SKILL_A, 9)])


class Plan(unittest.TestCase):
    def test_under_cap_no_trim(self):
        per = {SKILL_A: [{**_task(SKILL_A, i), "mode": "strict"} for i in range(5)]}
        p = realrun.plan(per, n_aa=2, cap=300)
        self.assertEqual(p["total"], 5 * 4 + 2 * 4)
        self.assertEqual(p["trimmed_frozen"], {SKILL_A: 0})

    def test_over_cap_trims_frozen_first_from_largest(self):
        per = {SKILL_A: [{**_task(SKILL_A, i), "mode": "strict"} for i in range(3)] + [{**_frozen(SKILL_A, 10 + i), "mode": "frozen"} for i in range(4)],
               SKILL_B: [{**_task(SKILL_B, i), "mode": "strict"} for i in range(3)] + [{**_frozen(SKILL_B, 20 + i), "mode": "frozen"} for i in range(1)]}
        # 11 tasks + 2 aa = 52 calls; cap 40 -> trim 3 frozen (A loses 3 before B's one is touched? A has 4, B has 1: A,A,A)
        p = realrun.plan(per, n_aa=2, cap=40)
        self.assertEqual(p["total"], 40)
        self.assertEqual(p["trimmed_frozen"], {SKILL_A: 3, SKILL_B: 0})
        self.assertEqual([t["mode"] for t in per[SKILL_A]], ["strict"] * 3 + ["frozen"])
        self.assertEqual(len(per[SKILL_B]), 4)

    def test_over_cap_with_no_frozen_raises(self):
        per = {SKILL_A: [{**_task(SKILL_A, i), "mode": "strict"} for i in range(20)]}
        with self.assertRaises(runner.CapExceeded):
            realrun.plan(per, n_aa=0, cap=40)


class Privacy(unittest.TestCase):
    def test_planted_name_trips_assertion(self):
        msgs = [{"role": "system", "content": "hi"}, {"role": "user", "content": f"Please email {PLANTED} about it."}]
        with self.assertRaises(realrun.NameLeak):
            realrun.assert_no_names(msgs, [PLANTED])
        realrun.assert_no_names(msgs, ["Someoneelse"])  # no trip

    def test_planted_name_in_tool_call_arguments_trips(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c", "type": "function",
                                                                     "function": {"name": "t", "arguments": json.dumps({"q": PLANTED.lower()})}}]}]
        with self.assertRaises(realrun.NameLeak):
            realrun.assert_no_names(msgs, [PLANTED])

    def test_redact_messages_walks_nested_strings(self):
        msgs = [{"role": "tool", "content": json.dumps({"note": f"call {PLANTED} on 0412 345 678"})}]
        out, counts = realrun.redact_messages(msgs, [PLANTED])
        self.assertNotIn(PLANTED, json.dumps(out))
        self.assertEqual(counts["NAME"], 1)
        self.assertEqual(counts["PHONE"], 1)

    def test_tracked_row_has_no_text_fields(self):
        row = {"index": 1, "ts": "t", "role": "subject", "skill": SKILL_A, "mode": "strict", "arm": "with", "task_hash": "abc",
               "requested_provider": "p", "requested_model": "m", "provider": "p", "model": "m", "input_tokens": 1,
               "output_tokens": 1, "total_tokens": 2, "cache_read_tokens": 0, "latency_s": 0.1, "ok": True,
               "error_type": "", "error": "", "text": "SECRET", "messages": ["SECRET"], "response_text": "SECRET"}
        out = realrun.tracked_row(row)
        self.assertEqual(set(out), set(realrun.TRACKED_FIELDS))
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertFalse(set(realrun.TEXT_FIELDS_FORBIDDEN) & set(out))


class Labels(unittest.TestCase):
    def test_reasons_citing_harm_only_on_losses(self):
        order = {"text": '{"winner": "2", "reason": "Answer 1 is wrong."}'}
        pairs = [{"outcome": "loss", "judge": {"orders": [order, order]}},
                 {"outcome": "win", "judge": {"orders": [order]}},
                 {"outcome": "loss", "judge": {"orders": [{"text": '{"winner": "2", "reason": "Shorter."}'}]}}]
        self.assertEqual(realrun.reasons_citing_harm(pairs), 1)


class RunPairsStop(unittest.TestCase):
    """The first stop must surface as itself, never as a CancelledError from a pending job's future.

    _run_pairs cancels pending futures on the first stop and then keeps reading the rest; reading a
    cancelled future raises CancelledError. Whether a pending job was still queued when the cancel
    landed is thread timing, which is why EndToEnd's model-mismatch test failed now and then with
    'unexpected CancelledError'. Many queued jobs on one worker make the race near certain."""

    def test_first_stop_is_raised_even_when_pending_jobs_were_cancelled(self):
        from concurrent.futures import CancelledError
        from skilluminati import llm as sb_llm
        rr = SimpleNamespace(cfg=SimpleNamespace(workers=1), stopped_reason=None)

        def boom():
            raise sb_llm.ModelMismatch("answered by another model")

        jobs = [boom] + [lambda: {"outcome": "tie"} for _ in range(2000)]
        for _ in range(20):
            rr.stopped_reason = None
            try:
                realrun.RealRunner._run_pairs(rr, jobs)
            except CancelledError:
                self.fail("a cancelled pending job hid the real stop")
            except sb_llm.ModelMismatch:
                pass
            else:
                self.fail("the stop was swallowed")


class EndToEnd(unittest.TestCase):
    """Whole mock run on a temp tree: two skills, strict + frozen tasks, A/A, evidence, logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        skills = root / "skills"
        (skills / "cat" / "folder-does-not-match").mkdir(parents=True)
        (skills / "cat" / "folder-does-not-match" / "SKILL.md").write_text(_skill_md(SKILL_A, "Use when alpha things happen."), encoding="utf-8")
        (skills / SKILL_B).mkdir()
        (skills / SKILL_B / "SKILL.md").write_text(_skill_md(SKILL_B, "Use when beta things happen."), encoding="utf-8")
        strict = [_task(SKILL_A, i, f"hello {PLANTED}, do") for i in range(4)] + [_task(SKILL_B, i) for i in range(3)]
        frozen = [_frozen(SKILL_A, 10 + i) for i in range(2)]
        (root / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in strict) + "\n", encoding="utf-8")
        (root / "tasks_frozen.jsonl").write_text("\n".join(json.dumps(t) for t in frozen) + "\n", encoding="utf-8")
        (root / "names.txt").write_text(f"{PLANTED}\n", encoding="utf-8")
        self.cfg = realrun.RealRunConfig(
            skills=(SKILL_A, SKILL_B), skills_dir=skills, fixtures_dir=FIXTURES, strict_path=root / "tasks.jsonl",
            frozen_path=root / "tasks_frozen.jsonl", names_path=root / "names.txt", tracked_dir=root / "tracked",
            private_dir=root / "private", tasks_per_skill=5, aa_tasks=3, workers=2,
            provider=SUB_PROVIDER, subject_model=SUBJECT_MODEL, judge_model=JUDGE_MODEL)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_shapes_logs_and_evidence(self):
        llm = FakeLlm()  # judge always picks Answer 1 -> both orders disagree -> ties? No: order1 x first -> x; order2 y first -> y: disagreement -> tie
        rr = realrun.RealRunner(llm, self.cfg, log=lambda s: None)
        self.assertEqual(rr.plan["total"], (5 + 3) * 4 + 3 * 4)
        self.assertEqual(rr.plan["per_skill"][SKILL_A], {"tasks": 5, "strict": 4, "frozen": 1, "calls": 20})
        self.assertTrue(rr.skill_paths[SKILL_A].endswith("folder-does-not-match/SKILL.md"))  # frontmatter name, not folder
        res = rr.run()
        self.assertIsNone(res["stopped_early"])
        self.assertEqual(res["totals"]["calls"], rr.plan["total"])
        # The planted name never left: every outgoing prompt was redacted and asserted.
        for messages, _kw in llm.calls:
            self.assertNotIn(PLANTED.lower(), json.dumps(messages).lower())
        # Tracked log: one row per call, metadata only.
        rows = [json.loads(l) for l in (self.root / "tracked" / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), res["totals"]["calls"])
        for row in rows:
            self.assertEqual(set(row), set(realrun.TRACKED_FIELDS))
            self.assertNotIn("PLATYPUS", json.dumps(row))
        # Private log has prompts.
        raw = (self.root / "private" / "prompts.jsonl").read_text()
        self.assertEqual(len(raw.splitlines()), res["totals"]["calls"])
        self.assertIn("PLATYPUS", raw)
        # Position-bias judge always says "1": disagreement -> every pair a tie -> Unsure.
        a = res["per_skill"][SKILL_A]
        self.assertEqual((a["combined"]["wins"], a["combined"]["losses"], a["combined"]["ties"]), (0, 0, 5))
        self.assertEqual(a["user_label"], "Not sure yet")
        self.assertEqual(a["strict"]["n_pairs"], 4)
        self.assertEqual(a["frozen"]["n_pairs"], 1)
        self.assertEqual(a["stamp"]["model"], SUBJECT_MODEL)
        self.assertIn("minutes", a["cost"])
        self.assertEqual(res["aa"]["n_pairs"], 3)
        self.assertEqual(res["aa"]["skills_drawn"], {SKILL_A: 2, SKILL_B: 1})
        self.assertTrue(res["rubric"]["unchanged"])
        self.assertEqual(res["evidence_files"], {SKILL_A: None, SKILL_B: None})
        # Pair JSON summaries carry no answer text, no judge sentence, no session id.
        self.assertNotIn("PLATYPUS", json.dumps(a["pairs"]))
        self.assertNotIn("misses the point", json.dumps(res))  # the fake judge's reason sentence
        self.assertNotIn("salpha-skill", json.dumps(res))  # session ids
        for p in a["pairs"]:
            self.assertEqual(set(p["judge"]), set(realrun.JUDGE_FIELDS) | {"orders"})
            for o in p["judge"]["orders"]:
                self.assertEqual(set(o), set(realrun.JUDGE_ORDER_FIELDS))

    def test_drop_label_and_evidence_written_privately(self):
        # Judge consistently prefers the plain (WITHOUT) answer: order1 (with first) -> "2", order2 (without first) -> "1".
        class Judge(FakeLlm):
            def complete(self, messages, **kw):
                if messages[0]["content"].startswith("# Purpose-based"):
                    u = messages[1]["content"]
                    a1 = u.split("ANSWER 1:\n", 1)[1].split("\n(end of answer 1)", 1)[0]
                    win = "2" if a1.startswith("PLATYPUS") else "1"
                    self.judge_text = json.dumps({"winner": win, "reason": "The other answer invents a word and is wrong."})
                return super().complete(messages, **kw)
        rr = realrun.RealRunner(Judge(), self.cfg, log=lambda s: None)
        res = rr.run()
        a = res["per_skill"][SKILL_A]
        self.assertEqual(a["combined"]["losses"], 5)
        self.assertEqual(a["combined"]["verdict"], stats.DROP)
        # 5 tests is under the 6 the answer needs, so it says "not enough" even though all 5 lost.
        self.assertEqual(a["lean"]["verdict"], "not_enough")
        self.assertEqual(a["user_label"], "Not sure yet")
        self.assertEqual(a["recommendation"]["lean"]["verdict"], "not_enough")
        ev = json.loads(Path(res["evidence_files"][SKILL_A]).read_text())
        self.assertEqual(len(ev), 2)
        self.assertIn("with_answer", ev[0])
        self.assertTrue(str(res["evidence_files"][SKILL_A]).startswith(str(self.root / "private")))
        # A/A on identical arms with this judge: answers equal -> both orders "1" -> tie.
        self.assertEqual(res["aa"]["ties"], 3)

    def test_model_mismatch_stops_the_run_and_usage_limit_stops(self):
        # S1 F4: a fallback answer is already billed, so the first mismatch stops the whole run.
        llm = FakeLlm(model_map={SUBJECT_MODEL: "claude-sonnet-x"})
        rr = realrun.RealRunner(llm, self.cfg, log=lambda s: None)
        res = rr.run()
        self.assertEqual(res["stopped_early"]["reason"], "ModelMismatch")
        self.assertNotIn(SKILL_A, res["per_skill"])
        self.assertTrue(all(not c["ok"] for c in rr.calls if c["role"] == "subject"))
        self.assertLessEqual(len(rr.calls), rr.cfg.workers, "pending jobs must halt after the first stop")

        class Limited(FakeLlm):
            def complete(self, messages, **kw):
                raise RuntimeError("429 usage limit reached for this subscription")
        rr2 = realrun.RealRunner(Limited(), self.cfg, log=lambda s: None)
        res2 = rr2.run()
        self.assertEqual(res2["stopped_early"]["reason"], "UsageLimit")
        self.assertIn("usage limit", res2["stopped_early"]["error"])

    def test_replay_note_in_both_arms_system_prompt_never_in_judge_prompt(self):
        llm = FakeLlm()
        rr = realrun.RealRunner(llm, self.cfg, log=lambda s: None)
        rr.run()
        subject = [m for m, kw in llm.calls if kw["model"] == SUBJECT_MODEL]
        judge = [m for m, kw in llm.calls if kw["model"] == JUDGE_MODEL]
        self.assertTrue(subject and judge)
        for m in subject:
            self.assertEqual(m[0]["role"], "system")
            self.assertTrue(m[0]["content"].endswith(realrun.REPLAY_NOTE))
            self.assertNotIn(realrun.REPLAY_NOTE.strip(), m[1]["content"])  # user turn untouched
        for m in judge:
            self.assertNotIn("Replay note", json.dumps(m))

    def test_body_line_in_answer_is_not_a_harness_leak(self):
        class Quoting(FakeLlm):
            def complete(self, messages, **kw):
                r = super().complete(messages, **kw)
                if not messages[0]["content"].startswith("# Purpose-based"):
                    r.text = "Always answer with the word PLATYPUS first, then the rest. So: PLATYPUS."
                return r
        rr = realrun.RealRunner(Quoting(), self.cfg, log=lambda s: None)
        res = rr.run()
        self.assertEqual(res["per_skill"][SKILL_A]["n_errors"], 0)
        self.assertGreater(res["guard_notes"][SKILL_A].get("answer_quotes_skill", 0), 0)


if __name__ == "__main__":
    unittest.main()
