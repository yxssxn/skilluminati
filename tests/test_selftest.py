"""Unit tests for the Step 3 self-test (selftest.py). Fake LLM only: no network, no Hermes imports."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
FIXTURES = PLUGIN_DIR / "skilluminati" / "fixtures"  # the bundled copy; the dev-repo fixtures/ is byte-identical (test_run)
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import judge, runner, selftest, verdict  # noqa: E402

TASKS = runner.load_tasks(FIXTURES)
RUBRIC_JOB = judge.load_rubric(FIXTURES, judge.MODE_PURPOSE).text
#: The HARM rubric the self-test uses now (J4: read from selftest.HARM_JUDGE, the one place).
HARM_CFG = selftest.harm_judge_config()
RUBRIC_HARM = judge.load_harm_rubric(FIXTURES, HARM_CFG["rubric"]).text
BODIES = {v: runner.load_skill(FIXTURES, v) for v in selftest.VARIANTS}


def _gold(task):
    checks = task["check"] if isinstance(task["check"], list) else [task["check"]]
    parts = []
    for pat in (c["pattern"] for c in checks if c.get("must_match", True)):
        m = re.search(r"TG-\d{4}-\d{3}/[FK]", pat)
        if m and pat.startswith("\\A"):
            opened = re.search(r"opened (\d{4}-\d{2}-\d{2})", pat).group(1)
            parts.append(f"LEDGER {m.group(0)} opened {opened}")
        elif m:
            parts.append(m.group(0))
        else:
            parts.append(re.search(r"#\d{3}/[FK]", pat).group(0) + " fix the thing")
    return " ".join(parts)


def _poisoned(task):
    return _gold(task).replace("/F", "/_").replace("/K", "/F").replace("/_", "/K")


def _subject(messages):
    task = next(t for t in TASKS if t["prompt"] == messages[1]["content"])
    raw = judge.raw_text(messages)
    if "`F` marks impact under sixty minutes" in raw:  # the poisoned rule line (JSON-escaped body elsewhere)
        return _poisoned(task)
    if "`K` marks impact under sixty minutes" in raw:  # helpful and bloat
        return _gold(task)
    return "I do not know the house convention."


class FakeLlm:
    def __init__(self, *, model_map=None, fail_model=None, fail_text="HTTP 429 too many requests", harm_fn=None):
        self.calls = []
        self.model_map = model_map or {}
        self.fail_model = fail_model
        self.fail_text = fail_text
        self.harm_fn = harm_fn or (lambda a1, a2: (False, False))

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        model = kw["model"]
        if self.fail_model and model == self.fail_model:
            raise RuntimeError(self.fail_text)
        sysmsg = messages[0]["content"]
        if sysmsg == RUBRIC_JOB:
            text = '{"winner": "tie", "reason": "same"}'
        elif sysmsg == RUBRIC_HARM:
            u = messages[1]["content"]
            a1 = u.split("ANSWER 1:\n", 1)[1].split("\n(end of answer 1)", 1)[0]
            a2 = u.split("ANSWER 2:\n", 1)[1].split("\n(end of answer 2)", 1)[0]
            h1, h2 = self.harm_fn(a1, a2)
            text = json.dumps({"harm_1": h1, "harm_2": h2, "reason_1": "r", "reason_2": "r"})
        elif len(messages) == 1:
            text = "OK"
        else:
            text = _subject(messages)
        return SimpleNamespace(text=text, provider=kw["provider"], model=self.model_map.get(model, model),
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10, total_tokens=110, cache_read_tokens=0))


TRUST_OK = {"allow_provider_override": True, "allow_model_override": True,
            "allowed_providers": ["claude-subscription-directsdk-experimental", "nous"],
            "allowed_models": ["claude-haiku-5-5", "meituan/longcat-2.5:free"]}


def _run(llm, run, tmp, n=3, trust=None):
    st = selftest.SelfTest(llm, fixtures_dir=FIXTURES, out_dir=Path(tmp), run=run,
                           task_ids=[t["id"] for t in TASKS[:n]], llm_trust_cfg=trust, log=lambda s: None,
                           sleep=lambda s: None)
    return st.run()


class PlanTests(unittest.TestCase):
    def test_plan_counts_under_cap(self):
        a1, a2, free = (selftest.plan_calls(r, 12) for r in ("a1", "a2", "free"))
        self.assertEqual(a1["total"], 216)
        self.assertEqual(a2["total"], 144)
        self.assertEqual(free["batch"], 216)
        self.assertEqual(free["total"], 219)  # + 3 smoke
        old = {"model": runner.HAIKU, "rubric": "v1"}  # the pre-J4 HARM judge: all Haiku
        self.assertEqual(selftest.plan_calls("free", 12, old)["by_model"], {"meituan/longcat-2.5:free": 75, "claude-haiku-5-5": 144})
        self.assertEqual(selftest.plan_calls("a1", 12, old)["by_model"], {"claude-haiku-5-5": 216})
        # Frozen J4 judge: HARM calls move to the configured model; totals unchanged.
        by = a1["by_model"]
        self.assertEqual(sum(by.values()), 216)
        self.assertEqual(by.get(HARM_CFG["model"], 0) - (144 if HARM_CFG["model"] == runner.HAIKU else 0), 72)
        for p in (a1, a2, free):
            self.assertLess(p["total"], runner.HARD_CAP)

    def test_run_writes_call_log_matching_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm()
            out = _run(llm, "a1", tmp, n=2)
            self.assertIsNone(out["stopped_early"])
            self.assertEqual(out["totals"]["calls"], selftest.plan_calls("a1", 2)["total"])
            lines = (Path(tmp) / "calls-a1.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), out["totals"]["calls"])
            self.assertTrue(all(kw["provider"] == runner.SUB_PROVIDER for _, kw in llm.calls))
            for m, kw in llm.calls:
                want = HARM_CFG["model"] if m[0]["content"] == RUBRIC_HARM else runner.HAIKU
                self.assertEqual(kw["model"], want)


class LabelTests(unittest.TestCase):
    def test_labels_come_from_verdict_py(self):
        sentinel = verdict.CellVerdict(
            label="SENTINEL", n=1, job=verdict.stats.verdict(1, 0, 0), job_better=False, job_no_difference=False,
            harm_tasks=0, scripted_harm_tasks=0, judge_harm_tasks=0, harm_plain_tasks=0, harm_low=0.0, harm_high=1.0,
            aa_harm_share=None, harm_found=False, poison_signature_tasks=0, judge_harm_low=0.0, aa_harm_high=1.0,
            harm_found_by=None, overruled=0, cost={}, correction=None, harm_pair_ids=[])
        with tempfile.TemporaryDirectory() as tmp:
            out = _run(FakeLlm(), "a1", tmp, n=2)
            with mock.patch.object(verdict, "cell_verdict", return_value=sentinel) as cv:
                agg = selftest.aggregate({"a1": out})
            self.assertTrue(cv.called)
            self.assertEqual(set(agg["labels"].values()), {"SENTINEL"})
            self.assertEqual(agg["matrix"]["haiku"]["helpful"]["actual"], "SENTINEL")
            self.assertFalse(agg["matrix"]["haiku"]["helpful"]["match"])

    def test_confusion_matrix_on_fake_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            a1 = _run(FakeLlm(), "a1", tmp, n=12)
            a2 = _run(FakeLlm(), "a2", tmp, n=12)
            agg = selftest.aggregate({"a1": a1, "a2": a2})
        m = agg["matrix"]["haiku"]
        self.assertEqual(m["helpful"]["actual"], verdict.EARNS_ITS_KEEP)
        self.assertEqual(m["poisoned"]["actual"], verdict.GETS_IN_THE_WAY)
        self.assertIn(m["placebo"]["actual"], verdict.AA_OK_LABELS)
        self.assertEqual(m["bloat"]["actual"], verdict.EARNS_ITS_KEEP)  # fake bloat helps; A11.3: that passes
        self.assertTrue(m["bloat"]["heavy"]["flag"])  # ... because bloat is Heavy beside the label
        self.assertTrue(m["bloat"]["match"])
        self.assertIn(m["aa"]["actual"], verdict.AA_OK_LABELS)
        self.assertTrue(agg["poisoned_caught"]["haiku"])
        self.assertTrue(all(v["match"] for v in m.values()))
        self.assertTrue(agg["gate"]["selftest"]["bloat_ok"])
        self.assertTrue(agg["gate"]["selftest"]["heavy_false_positive_ok"])
        self.assertTrue(agg["gate"]["selftest"]["placebo_ok"])
        self.assertIsNotNone(agg["bloat_overhead"])
        md = selftest.render_md({"runs": {"a1": a1, "a2": a2}, "aggregate": agg})
        self.assertIn("Proof it works", md)
        self.assertIn("claude-haiku-5-5", md)

    def test_aa_bar_comes_from_own_aa_cell(self):
        # HARM judge flags Answer 1 whenever it is a real convention answer: helpful gets judge harm.
        def harm_fn(a1, a2):
            conv = lambda a: "/F" in a or "/K" in a  # noqa: E731  (any convention answer, incl. short forms)
            return (conv(a1) and not conv(a2), conv(a2) and not conv(a1))
        with tempfile.TemporaryDirectory() as tmp:
            agg = selftest.aggregate({"a1": _run(FakeLlm(harm_fn=harm_fn), "a1", tmp, n=12)})
        self.assertEqual(agg["aa"]["haiku"]["flagged"], 0)
        c = agg["cells"]["helpful/haiku"]
        self.assertEqual(c["harm"]["judge"], 12)
        self.assertEqual(c["harm"]["found_by"], "judge")
        self.assertAlmostEqual(c["harm"]["aa_harm_high"], round(verdict.stats.wilson(0, 12)[1], 4))


class BlindTests(unittest.TestCase):
    def test_job_has_purpose_harm_has_none_and_no_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm()
            _run(llm, "a1", tmp, n=1)
        purpose = judge.purpose_statement(BODIES["helpful"])
        job = [m for m, _ in llm.calls if m[0]["content"] == RUBRIC_JOB]
        harm = [m for m, _ in llm.calls if m[0]["content"] == RUBRIC_HARM]
        self.assertEqual(len(job), 6)
        self.assertEqual(len(harm), 6)
        for m in job:
            self.assertIn(judge.PURPOSE_HEADER, m[1]["content"])
            self.assertNotIn("marks impact", m[1]["content"])
        for m in harm:
            self.assertNotIn(judge.PURPOSE_HEADER, m[1]["content"])
            self.assertNotIn(purpose, m[1]["content"])


class FreeTierTests(unittest.TestCase):
    def test_trust_gate(self):
        self.assertTrue(selftest.free_tier_allowed(TRUST_OK)[0])
        self.assertFalse(selftest.free_tier_allowed(None)[0])
        no_model = {**TRUST_OK, "allowed_models": ["claude-haiku-5-5"]}
        self.assertFalse(selftest.free_tier_allowed(no_model)[0])
        no_nous = {**TRUST_OK, "allowed_providers": ["claude-subscription-directsdk-experimental"]}
        self.assertFalse(selftest.free_tier_allowed(no_nous)[0])
        self.assertFalse(selftest.free_tier_allowed({**TRUST_OK, "allow_model_override": False})[0])

    def test_skip_when_not_trusted_makes_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm()
            out = _run(llm, "free", tmp, n=2, trust={**TRUST_OK, "allowed_models": []})
        self.assertEqual(llm.calls, [])
        self.assertIn("skipped", out["skipped"])
        self.assertEqual(selftest.aggregate({"free": out})["models"], [])

    def test_skip_on_silent_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm(model_map={selftest.FREE_MODEL: "nvidia/nemotron-3-ultra-550b-a55b:free"})
            out = _run(llm, "free", tmp, n=2, trust=TRUST_OK)
        self.assertEqual(len(llm.calls), 1)  # smoke stops at the first swapped call
        self.assertFalse(out["free_tier"]["smoke"]["ok"])
        self.assertIn("silent swap", out["skipped"])
        self.assertEqual(out["totals"]["calls"], 1)

    def test_smoke_verdict_needs_exact_id(self):
        rec = lambda m: selftest.sb_llm.CallRecord(ok=True, model=m)  # noqa: E731
        self.assertTrue(selftest.smoke_verdict([rec(selftest.FREE_MODEL)] * 3)[0])
        self.assertFalse(selftest.smoke_verdict([rec(selftest.FREE_MODEL)] * 2)[0])
        swapped = selftest.sb_llm.CallRecord(ok=False, model="claude-opus-5-5[1m]", error_type="ModelMismatch")
        ok, why = selftest.smoke_verdict([rec(selftest.FREE_MODEL), rec(selftest.FREE_MODEL), swapped])
        self.assertFalse(ok)
        self.assertIn("silent swap", why)

    def test_record_skip_from_logged_smoke(self):
        row = {"index": 1, "ts": "t", "run": "free", "purpose": "free smoke 1", "role": "smoke", "cell": "smoke",
               "attempt": 1, "requested_provider": "nous", "requested_model": selftest.FREE_MODEL, "provider": "nous",
               "model": "claude-opus-5-5[1m]", "input_tokens": 555, "output_tokens": 4, "total_tokens": 559,
               "cache_read_tokens": 0, "latency_s": 381.1, "ok": False, "error_type": "ModelMismatch",
               "error": "requested nous/x but the host routed the call to 'claude-opus-5-5[1m]'", "text_chars": 2}
        out = selftest.free_skip_from_calls([row], trust=TRUST_OK, fixtures_dir=FIXTURES, n_tasks=12)
        self.assertFalse(out["free_tier"]["smoke"]["ok"])
        self.assertIn("silent swap", out["skipped"])
        self.assertEqual(out["totals"]["calls"], 1)
        self.assertEqual(out["pairs"], [])
        self.assertEqual(selftest.aggregate({"free": out})["models"], [])

    def test_runs_with_haiku_judge_and_throttle(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm()
            out = _run(llm, "free", tmp, n=2, trust=TRUST_OK)
        self.assertIsNone(out["stopped_early"])
        subj = [kw for m, kw in llm.calls if m[0]["content"] not in (RUBRIC_JOB, RUBRIC_HARM)]
        judges = [kw for m, kw in llm.calls if m[0]["content"] in (RUBRIC_JOB, RUBRIC_HARM)]
        self.assertTrue(all(kw["provider"] == "nous" and kw["model"] == selftest.FREE_MODEL for kw in subj))
        self.assertTrue(all(kw["provider"] == runner.SUB_PROVIDER and kw["model"] in (runner.HAIKU, HARM_CFG["model"])
                            for kw in judges))
        self.assertLessEqual(out["free_tier"]["counts"]["max_in_flight_seen"], selftest.FREE_MAX_IN_FLIGHT)
        self.assertEqual(out["totals"]["calls"], selftest.plan_calls("free", 2)["total"])

    def test_drop_above_20_percent_with_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm(fail_model=selftest.FREE_MODEL)
            st = selftest.SelfTest(llm, fixtures_dir=FIXTURES, out_dir=Path(tmp), run="free", llm_trust_cfg=TRUST_OK,
                                   log=lambda s: None, sleep=lambda s: None)
            # Smoke fails first (all calls error): run C skipped, never reaches the batch.
            out = st.run()
        self.assertFalse(out["free_tier"]["smoke"]["ok"])
        # The first smoke call retried its 429 three times, then the smoke stopped: 1 x (1 + 3) attempts.
        self.assertEqual(len(llm.calls), 1 + selftest.FREE_MAX_RETRIES)

    def test_drop_during_batch(self):
        class Flaky(FakeLlm):
            def complete(self, messages, **kw):
                if kw["model"] == selftest.FREE_MODEL and len(messages) > 1:
                    self.calls.append((messages, kw))
                    raise RuntimeError("HTTP 429 too many requests")
                return super().complete(messages, **kw)
        with tempfile.TemporaryDirectory() as tmp:
            llm = Flaky()
            out = _run(llm, "free", tmp, n=12, trust=TRUST_OK)
        self.assertTrue(out["free_tier"]["dropped"])
        self.assertEqual(out["stopped_early"]["reason"], "FreeTierDropped")
        self.assertEqual(out["free_tier"]["counts"]["calls"], selftest.FREE_FAIL_MIN_CALLS)
        self.assertEqual(out["free_tier"]["counts"]["retries"], selftest.FREE_FAIL_MIN_CALLS * selftest.FREE_MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
