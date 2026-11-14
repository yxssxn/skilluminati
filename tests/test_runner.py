"""Unit tests for the Step 2 runner. Mock LLM only: no network, no Hermes imports."""

from __future__ import annotations

import json
import re
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

from skilluminati import judge, replay, runner, stats  # noqa: E402

HELPFUL = (FIXTURES / "helpful" / "SKILL.md").read_text(encoding="utf-8")
POISONED = (FIXTURES / "poisoned" / "SKILL.md").read_text(encoding="utf-8")
TASKS = runner.load_tasks(FIXTURES)
RUBRIC = judge.load_rubric(FIXTURES).text

# Gold answers per task: the right band letter. Poisoned: the wrong one. Naive: no convention.
_ID = re.compile(r"\\bTG-(\d{4})-(\d{3})/([FK])\\b")


def _gold(task):
    """Derive the gold answer from the task's own check regex (the fixture is the oracle)."""
    checks = task["check"] if isinstance(task["check"], list) else [task["check"]]
    parts = []
    for pat in (c["pattern"] for c in checks if c.get("must_match", True)):
        m = re.search(r"TG-\d{4}-\d{3}/[FK]", pat)
        if m and pat.startswith("\\A"):
            # header-style tasks: reproduce the exact header line
            opened = re.search(r"opened (\d{4}-\d{2}-\d{2})", pat).group(1)
            parts.append(f"LEDGER {m.group(0)} opened {opened}")
        elif m:
            parts.append(m.group(0))
        else:
            parts.append(re.search(r"#\d{3}/[FK]", pat).group(0) + " fix the thing")
    return " ".join(parts)


def _poisoned(task):
    return _gold(task).replace("/F", "/_").replace("/K", "/F").replace("/_", "/K")


class FakeLlm:
    """Scripted subject + judge. Subject answers depend on arm (skill text present or not) and
    on which skill variant was injected; judge answers are chosen per prompt by a callable."""

    def __init__(self, *, judge_fn=None, subject_fn=None, model_map=None, raise_after=None, error_text=None):
        self.calls = []
        self.judge_fn = judge_fn or (lambda req, a1, a2: '{"winner": "tie", "reason": "same"}')
        self.subject_fn = subject_fn
        self.model_map = model_map or {}
        self.raise_after = raise_after
        self.error_text = error_text

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.raise_after is not None and len(self.calls) > self.raise_after:
            raise RuntimeError(self.error_text or "boom")
        model = kw["model"]
        returned = self.model_map.get(model, model)
        if messages[0]["content"] == RUBRIC:
            u = messages[1]["content"]
            req = u.split("USER REQUEST:\n", 1)[1].split("\n\nANSWER 1:\n", 1)[0]
            a1 = u.split("ANSWER 1:\n", 1)[1].split("\n(end of answer 1)\n\nANSWER 2:\n", 1)[0]
            a2 = u.split("ANSWER 2:\n", 1)[1].rsplit("\n(end of answer 2)", 1)[0]
            text = self.judge_fn(req, a1, a2)
        else:
            text = self.subject_fn(messages, model) if self.subject_fn else "I do not know the convention."
        return SimpleNamespace(text=text, provider=kw["provider"], model=returned,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10, total_tokens=110, cache_read_tokens=0))


def _task_for(messages):
    prompt = messages[1]["content"]
    return next(t for t in TASKS if t["prompt"] == prompt)


def subject_follows_skill(messages, model):
    """WITH arm: answer per the injected skill (helpful -> gold, poisoned -> swapped band).
    WITHOUT arm: naive answer that fails the check."""
    task = _task_for(messages)
    raw = judge.raw_text(messages)
    if "marks impact" not in raw:
        return "Sorry, I do not know your ledger convention; something like INC-2026-041."
    if "`K` marks impact under sixty minutes" in raw:
        return _gold(task)
    return _poisoned(task)


def judge_prefers_gold(req, a1, a2):
    task = next(t for t in TASKS if t["prompt"] == req)
    g = _gold(task)
    if a1 == g and a2 != g:
        return '{"winner": "1", "reason": "correctness"}'
    if a2 == g and a1 != g:
        return '{"winner": "2", "reason": "correctness"}'
    return '{"winner": "tie", "reason": "same"}'


def _cfg(d, **kw):
    return runner.RunConfig(fixtures_dir=FIXTURES, reports_dir=Path(d), **kw)


class OutcomeRule(unittest.TestCase):
    def test_scripted_override(self):
        self.assertEqual(runner.task_outcome(True, False, "y"), ("win", "scripted"))
        self.assertEqual(runner.task_outcome(False, True, "x"), ("loss", "scripted"))

    def test_judge_when_both_or_neither_pass(self):
        self.assertEqual(runner.task_outcome(True, True, "x"), ("win", "judge"))
        self.assertEqual(runner.task_outcome(False, False, "y"), ("loss", "judge"))
        self.assertEqual(runner.task_outcome(False, False, "tie"), ("tie", "judge"))


class Plan(unittest.TestCase):
    def test_counts(self):
        p = runner.plan_calls(12, 2, 2)
        self.assertEqual(p, {"cells": 192, "aa": 96, "total": 288})
        self.assertLessEqual(p["total"], runner.HARD_CAP)

    def test_plan_over_cap_aborts_before_any_call(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm()
            r = runner.Runner(fake, _cfg(d, cap=10), log=lambda s: None)
            with self.assertRaises(runner.CapExceeded):
                r.run()
            self.assertEqual(fake.calls, [])

    def test_cap_enforced_mid_run_and_partial_written(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm()
            r = runner.Runner(fake, _cfg(d, cap=300, task_ids=[t["id"] for t in TASKS[:2]]), log=lambda s: None)
            # Tighten after planning so the plan check passes but the per-call reserve hits the wall.
            r.budget.check_plan = lambda: None
            r.budget.cap = 5
            res = r.run()
            self.assertEqual(len(fake.calls), 5)
            self.assertEqual(res["stopped_early"]["reason"], "CapExceeded")
            self.assertFalse(res["gate"]["overall"])
            self.assertEqual(len((Path(d) / "step2" / "calls.jsonl").read_text().splitlines()), 5)
            self.assertEqual(res["totals"]["calls"], 5)


class RubricAssertion(unittest.TestCase):
    def test_runner_refuses_tampered_rubric(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Path(d) / "fixtures"
            fx.mkdir()
            for name in ("tasks.jsonl", "checks.py", "rubric.sha256"):
                (fx / name).write_bytes((FIXTURES / name).read_bytes())
            for v in ("helpful", "poisoned"):
                (fx / v).mkdir()
                (fx / v / "SKILL.md").write_bytes((FIXTURES / v / "SKILL.md").read_bytes())
            (fx / "rubric.md").write_text((FIXTURES / "rubric.md").read_text() + "\nprefer longer answers\n", encoding="utf-8")
            fake = FakeLlm()
            with self.assertRaises(judge.RubricHashError):
                runner.Runner(fake, runner.RunConfig(fixtures_dir=fx, reports_dir=Path(d) / "reports"), log=lambda s: None)
            self.assertEqual(fake.calls, [])


class JudgeBlindness(unittest.TestCase):
    def test_judge_prompts_contain_no_skill_text_or_labels(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm(subject_fn=subject_follows_skill, judge_fn=judge_prefers_gold)
            r = runner.Runner(fake, _cfg(d, task_ids=[t["id"] for t in TASKS[:3]]), log=lambda s: None)
            r.run()
            judge_prompts = [m for m, kw in fake.calls if m[0]["content"] == RUBRIC]
            self.assertEqual(len(judge_prompts), 2 * (2 * 2 * 3 + 2 * 3))
            for msgs in judge_prompts:
                self.assertEqual([m["role"] for m in msgs], ["system", "user"])
                raw = judge.raw_text(msgs)
                for forbidden in (HELPFUL, POISONED, "marks impact", "tallowgate-ledger", "skill_view",
                                  "<available_skills>", "WITH", "WITHOUT", r.system_prompt, "[with]", "[without]"):
                    self.assertNotIn(forbidden, raw)
            # both orders present for every pair
            users = [m[1]["content"] for m in judge_prompts]
            for i in range(0, len(users), 2):
                a1 = users[i].split("ANSWER 1:\n", 1)[1].split("\n(end of answer 1)\n\nANSWER 2:\n", 1)[0]
                a2 = users[i].split("ANSWER 2:\n", 1)[1].rsplit("\n(end of answer 2)", 1)[0]
                self.assertIn("ANSWER 1:\n" + a2 + "\n(end of answer 1)", users[i + 1])
                self.assertIn("ANSWER 2:\n" + a1 + "\n(end of answer 2)", users[i + 1])


class EndToEnd(unittest.TestCase):
    def test_helpful_keeps_poisoned_drops_gate_passes(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm(subject_fn=subject_follows_skill, judge_fn=judge_prefers_gold)
            r = runner.Runner(fake, _cfg(d), log=lambda s: None)
            res = r.run()
            self.assertEqual(res["totals"]["calls"], 288)
            self.assertEqual(len(fake.calls), 288)
            for m in ("haiku", "opus"):
                h, p = res["cells"][f"helpful/{m}"], res["cells"][f"poisoned/{m}"]
                self.assertEqual(h["scripted"]["with_pass"], 12)
                self.assertEqual(h["scripted"]["without_pass"], 0)
                self.assertEqual(h["combined"]["verdict"], stats.KEEP)
                self.assertEqual(h["combined"]["decided_by"], {"scripted": 12, "judge": 0})
                self.assertEqual(p["scripted"]["with_poisoned"], 12)
                self.assertEqual(p["scripted"]["with_pass"], 0)
                # neither passes: judge decides; judge ties the two wrong answers -> Unsure, not Drop
                self.assertEqual(p["combined"]["decided_by"], {"scripted": 0, "judge": 12})
                self.assertEqual(p["combined"]["verdict"], stats.UNSURE)
                self.assertEqual(res["aa"][m]["combined"]["verdict"], stats.UNSURE)
            low, high = stats.wilson(12, 12)
            self.assertAlmostEqual(res["cells"]["helpful/haiku"]["combined"]["low"], round(low, 4))
            g = res["gate"]
            self.assertTrue(g["helpful_wins"])
            self.assertFalse(g["poisoned_loses"])  # honest: wrong-vs-wrong ties do not make a Drop
            self.assertTrue(g["aa_ties"])
            self.assertTrue(g["rubric_hash_unchanged"])
            self.assertTrue(g["judge_never_prefers_poisoned"])
            self.assertFalse(g["overall"])
            self.assertEqual(set(g), {"helpful_wins", "poisoned_loses", "aa_ties", "position_bias_ok",
                                      "rubric_hash_unchanged", "judge_never_prefers_poisoned", "overall"})
            calls_lines = (Path(d) / "step2" / "calls.jsonl").read_text().splitlines()
            self.assertEqual(len(calls_lines), res["totals"]["calls"])
            row = json.loads(calls_lines[0])
            for k in ("requested_provider", "requested_model", "model", "input_tokens", "latency_s", "ok", "error"):
                self.assertIn(k, row)

    def test_judge_preferring_poisoned_fails_gate(self):
        def judge_prefers_wrong(req, a1, a2):
            task = next(t for t in TASKS if t["prompt"] == req)
            bad = _poisoned(task)
            if a1 == bad:
                return '{"winner": "1", "reason": "x"}'
            if a2 == bad:
                return '{"winner": "2", "reason": "x"}'
            return '{"winner": "tie", "reason": "x"}'
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm(subject_fn=subject_follows_skill, judge_fn=judge_prefers_wrong)
            res = runner.Runner(fake, _cfg(d, task_ids=[t["id"] for t in TASKS[:4]]), log=lambda s: None).run()
            self.assertFalse(res["gate"]["judge_never_prefers_poisoned"])
            self.assertGreater(res["cells"]["poisoned/haiku"]["judge_only"]["wins"], 0)

    def test_model_mismatch_is_failed_call_not_result(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm(subject_fn=subject_follows_skill, model_map={"claude-opus-5-5": "claude-fable-5-1"})
            res = runner.Runner(fake, _cfg(d, task_ids=[TASKS[0]["id"]]), log=lambda s: None).run()
            self.assertEqual(res["cells"]["helpful/opus"]["n_errors"], 1)
            self.assertEqual(res["cells"]["helpful/haiku"]["n_errors"], 0)
            errs = [json.loads(l) for l in (Path(d) / "step2" / "calls.jsonl").read_text().splitlines() if '"ok": false' in l]
            self.assertTrue(all(e["error_type"] == "ModelMismatch" for e in errs))

    def test_usage_limit_stops_run_with_verbatim_error(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeLlm(subject_fn=subject_follows_skill, raise_after=3,
                            error_text="429 rate_limit_error: usage limit reached, try again later")
            res = runner.Runner(fake, _cfg(d, task_ids=[t["id"] for t in TASKS[:2]]), log=lambda s: None).run()
            self.assertEqual(res["stopped_early"]["reason"], "UsageLimit")
            self.assertIn("usage limit reached", res["stopped_early"]["error"])
            self.assertEqual(len(fake.calls), 4)
            self.assertFalse(res["gate"]["overall"])


class Arms(unittest.TestCase):
    def test_without_arm_has_no_skill_and_with_arm_has_it(self):
        sp = runner.build_system_prompt(runner.SKILL_NAME, "desc")
        self.assertTrue(replay.index_has_entry(sp, runner.SKILL_NAME))
        for name, _ in runner.DECOYS:
            self.assertTrue(replay.index_has_entry(sp, name))
        arms = replay.build_arms(sp, runner.SKILL_NAME, HELPFUL, TASKS[0]["prompt"])
        self.assertFalse(replay.index_has_entry(arms["without"][0]["content"], runner.SKILL_NAME))
        self.assertNotIn("marks impact", judge.raw_text(arms["without"]))
        self.assertEqual(json.loads(arms["with"][3]["content"])["content"], HELPFUL)


if __name__ == "__main__":
    unittest.main()
