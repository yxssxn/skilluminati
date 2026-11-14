"""J4 (cards/P2-J4-judge-fix.md): split helper, rubric v2 hash, candidate choice, plan counts, the HARM
judge config read from one place, and the stored-row re-judge. Fake LLM only: no network."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "plugin" / "skilluminati" / "fixtures"  # the bundled copy; the dev-repo fixtures/ is byte-identical (test_run)
if str(REPO / "plugin") not in sys.path:
    sys.path.insert(0, str(REPO / "plugin"))

from skilluminati import judge, runner, selftest, verdict  # noqa: E402

TASK_IDS = [t["id"] for t in runner.load_tasks(FIXTURES)]


class SplitTests(unittest.TestCase):
    def test_odd_tune_even_heldout(self):
        s = selftest.split_tasks(TASK_IDS)
        self.assertEqual([selftest.task_number(t) for t in s["tune"]], [1, 3, 5, 7, 9, 11])
        self.assertEqual([selftest.task_number(t) for t in s["heldout"]], [2, 4, 6, 8, 10, 12])
        self.assertEqual(selftest.split_of("t08_handover_note"), "heldout")  # the prefers-poisoned pair is held out
        self.assertFalse(set(s["tune"]) & set(s["heldout"]))

    def test_bad_task_id(self):
        with self.assertRaises(ValueError):
            selftest.task_number("x01")
        with self.assertRaises(ValueError):
            selftest.task_number("t_abc")


class RubricV2Tests(unittest.TestCase):
    def test_v2_hash_matches_and_v1_untouched(self):
        v2 = judge.load_harm_rubric(FIXTURES, "v2")
        self.assertEqual(v2.sha256, (FIXTURES / "rubric_harm_v2.sha256").read_text().strip())
        v1 = judge.load_harm_rubric(FIXTURES, "v1")
        self.assertEqual(v1.text, judge.load_rubric(FIXTURES, judge.MODE_HARM).text)
        self.assertNotEqual(v1.sha256, v2.sha256)
        # JSON output contract identical, so parse_harm_verdict is shared.
        self.assertIn('{"harm_1": true | false, "harm_2": true | false', v2.text)
        self.assertIn("ANSWER 1:", v2.text)

    def test_tampered_v2_raises(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("rubric_harm_v2.md", "rubric_harm_v2.sha256"):
                shutil.copy(FIXTURES / name, Path(d) / name)
            (Path(d) / "rubric_harm_v2.md").write_text((FIXTURES / "rubric_harm_v2.md").read_text() + "\nbe lenient")
            with self.assertRaises(judge.RubricHashError):
                judge.load_harm_rubric(Path(d), "v2")

    def test_unknown_version(self):
        with self.assertRaises(ValueError):
            judge.load_harm_rubric(FIXTURES, "v9")


def _summary(flip, prefers, aa, caught=True):
    return {"flip_share": flip, "prefers_poisoned": prefers, "aa_harm_share": aa, "poisoned_caught": caught}


class ChoiceTests(unittest.TestCase):
    def test_needs_flip_and_no_poison_preference(self):
        res = {"H1": {"model": runner.HAIKU, "summary": _summary(0.10, 1, 0.0)},
               "S0": {"model": selftest.SONNET, "summary": _summary(0.29, 0, 0.0)},
               "S1": {"model": selftest.SONNET, "summary": _summary(0.11, 0, 0.0)}}
        self.assertEqual(selftest.choose_candidate(res)[0], "S1")

    def test_none_eligible(self):
        res = {"H1": {"model": runner.HAIKU, "summary": _summary(0.30, 0, 0.0)},
               "S1": {"model": selftest.SONNET, "summary": _summary(0.10, 2, 0.0)}}
        best, why = selftest.choose_candidate(res)
        self.assertIsNone(best)
        self.assertIn("no candidate", why)

    def test_boundary_flip_equal_to_max_is_eligible(self):
        res = {"H1": {"model": runner.HAIKU, "summary": _summary(verdict.HARM_FLIP_MAX, 0, 0.0)}}
        self.assertEqual(selftest.choose_candidate(res)[0], "H1")

    def test_order_aa_then_caught_then_cost(self):
        res = {"H1": {"model": runner.HAIKU, "summary": _summary(0.20, 0, 0.1)},
               "S1": {"model": selftest.SONNET, "summary": _summary(0.20, 0, 0.0)}}
        self.assertEqual(selftest.choose_candidate(res)[0], "S1")  # lower A/A share wins
        res["H1"]["summary"]["aa_harm_share"] = 0.0
        self.assertEqual(selftest.choose_candidate(res)[0], "H1")  # tie: cheaper wins
        res["H1"]["summary"]["poisoned_caught"] = False
        self.assertEqual(selftest.choose_candidate(res)[0], "S1")  # caught beats cost


class PlanTests(unittest.TestCase):
    def test_fresh_runs_under_cap_and_split_by_model(self):
        for run, total in (("a1", 216), ("a2", 144)):
            p = selftest.plan_calls(run, 12)
            self.assertEqual(p["total"], total)
            self.assertLess(p["total"], runner.HARD_CAP)
            self.assertEqual(sum(p["by_model"].values()), total)
            self.assertEqual(p["harm_calls"], total // 3)
        p = selftest.plan_calls("a1", 12, {"model": selftest.SONNET, "rubric": "v2"})
        self.assertEqual(p["by_model"], {runner.HAIKU: 144, selftest.SONNET: 72})

    def test_tune_and_heldout_plans_under_cap(self):
        # 5 cells x 6 tasks x 2 orders, at most.
        self.assertLess(5 * 6 * 2, runner.HARD_CAP)


class ConfigTests(unittest.TestCase):
    def test_one_place(self):
        self.assertEqual(selftest.harm_judge_config(), dict(selftest.HARM_JUDGE))
        self.assertIn(selftest.HARM_JUDGE["model"], selftest.JUDGE_MODELS)
        self.assertIn(selftest.HARM_JUDGE["rubric"], judge.HARM_RUBRIC_VERSIONS)

    def test_rejects_other_models(self):
        for bad in ("claude-opus-5-5", "claude-fable-5-1", "meituan/longcat-2.5:free"):
            with self.assertRaises(ValueError):
                selftest.harm_judge_config({"model": bad, "rubric": "v2"})
        with self.assertRaises(ValueError):
            selftest.harm_judge_config({"model": runner.HAIKU, "rubric": "v3"})

    def test_selftest_reads_the_setting(self):
        orig = dict(selftest.HARM_JUDGE)
        try:
            selftest.HARM_JUDGE.update(model=runner.HAIKU, rubric="v2")
            with tempfile.TemporaryDirectory() as tmp:
                llm = _Fake()
                st = selftest.SelfTest(llm, fixtures_dir=FIXTURES, out_dir=Path(tmp), run="a1",
                                       task_ids=TASK_IDS[:1], log=lambda s: None)
                out = st.run()
            v2 = judge.load_harm_rubric(FIXTURES, "v2").text
            harm = [kw for m, kw in llm.calls if m[0]["content"] == v2]
            self.assertEqual(len(harm), 6)
            self.assertTrue(all(kw["model"] == runner.HAIKU and kw["provider"] == runner.SUB_PROVIDER for kw in harm))
            self.assertEqual(out["harm_judge"]["rubric_file"], "rubric_harm_v2.md")
            self.assertTrue(out["rubrics"]["harm"]["unchanged"])
            self.assertEqual(out["rubrics"]["harm"]["sha256_expected"], judge.load_harm_rubric(FIXTURES, "v2").sha256)
        finally:
            selftest.HARM_JUDGE.clear()
            selftest.HARM_JUDGE.update(orig)


class _Fake:
    def __init__(self):
        self.calls = []

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        sysmsg = messages[0]["content"]
        if "harm_1" in sysmsg:  # flags the answer whose text is "unknown", wherever it is shown
            u = messages[1]["content"]
            a1 = u.split("ANSWER 1:\n", 1)[1].split("\n(end of answer 1)", 1)[0]
            a2 = u.split("ANSWER 2:\n", 1)[1].split("\n(end of answer 2)", 1)[0]
            text = json.dumps({"harm_1": a1.strip() == "unknown", "harm_2": a2.strip() == "unknown",
                               "reason_1": "r", "reason_2": "r"})
        elif '"winner"' in sysmsg:
            text = '{"winner": "tie"}'
        else:
            text = "answer"
        return SimpleNamespace(text=text, provider=kw["provider"], model=kw["model"],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12, cache_read_tokens=0))


def _stored_row(task="t01_id_long_impact", cell="poisoned/haiku", outcome="win"):
    return {"run": "a1", "cell": cell, "skill": cell.split("/")[0], "model_label": "haiku", "task": task,
            "prompt": "What is the ID?", "x_label": "with", "y_label": "without", "outcome": outcome,
            "arms": {"with": {"text": "TG-1", "input_tokens": 5, "output_tokens": 1, "latency_s": 1.0},
                     "without": {"text": "unknown", "input_tokens": 4, "output_tokens": 1, "latency_s": 1.0}},
            "scripted": {"with": {"pass": False, "poisoned": True}, "without": {"pass": False, "poisoned": False}},
            "harm": {"harm_x": True, "harm_y": False}}


class RejudgeTests(unittest.TestCase):
    def test_rejudge_replaces_harm_only_and_logs_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = _Fake()
            caller = selftest.Caller(llm, Path(tmp), "tune-X", 10)
            rows = [_stored_row(), _stored_row(task="t03_header_only", outcome="error")]
            out = selftest.rejudge_harm_rows(rows, caller, fixtures_dir=FIXTURES,
                                             harm_judge={"model": selftest.SONNET, "rubric": "v2"}, log=lambda s: None)
            logged = [json.loads(l) for l in (Path(tmp) / "calls-tune-X.jsonl").read_text().splitlines()]
        self.assertEqual(len(llm.calls), 2)  # error row costs no call
        self.assertEqual(out[1], rows[1])
        h = out[0]["harm"]
        self.assertFalse(h["harm_x"])
        self.assertTrue(h["harm_y"])  # flagged WITHOUT only, both orders
        self.assertTrue(out[0]["evidence"]["scripted_harm"])  # stored scripted evidence reused
        self.assertEqual(out[0]["outcome"], "win")
        self.assertTrue(all(r["split"] == "tune" and r["requested_model"] == selftest.SONNET for r in logged))
        self.assertTrue(all(kw["provider"] == runner.SUB_PROVIDER for _, kw in llm.calls))
        for m, _ in llm.calls:  # blind: request + answers only
            self.assertNotIn(judge.PURPOSE_HEADER, m[1]["content"])
            self.assertEqual(m[0]["content"], judge.load_harm_rubric(FIXTURES, "v2").text)

    def test_usage_limit_stops(self):
        class Limited(_Fake):
            def complete(self, messages, **kw):
                raise RuntimeError("usage limit reached, try again later")
        with tempfile.TemporaryDirectory() as tmp:
            caller = selftest.Caller(Limited(), Path(tmp), "heldout", 10)
            with self.assertRaises(runner.UsageLimit):
                selftest.rejudge_harm_rows([_stored_row(task="t02_id_short_impact")], caller, fixtures_dir=FIXTURES,
                                           harm_judge={"model": selftest.SONNET, "rubric": "v2"}, log=lambda s: None)


if __name__ == "__main__":
    unittest.main()
