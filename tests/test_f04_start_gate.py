"""F04: a verdict run never starts below the lean rule's minimum (verdict.MIN_TESTS = 6), so a 4 or
5 task run no longer spends 32 to 36 calls on a guaranteed Unproven. Fake model, temp HERMES_HOME."""

from __future__ import annotations

from tests import test_run as R  # noqa: E402  (puts plugin/ on sys.path)
from skilluminati import run, verdict as sb_verdict  # noqa: E402


class StartGateTests(R.HomeCase):
    def setUp(self):
        super().setUp()
        d = self.home / "skills" / "notes" / R.SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(R.SKILL_MD, encoding="utf-8")

    def test_gate_equals_the_rule_minimum(self):
        R._db(self.home / "state.db", 5)
        pl = run.verdict_plan(R.FakeCtx(R.JudgeAlwaysWith(), home=self.home), R.SKILL, source="history")
        self.assertEqual(pl["min"], sb_verdict.MIN_TESTS)
        self.assertEqual(pl["min"], 6)

    def test_five_usable_tasks_say_not_enough_and_make_no_call(self):
        R._db(self.home / "state.db", 5)
        llm = R.JudgeAlwaysWith()
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), R.SKILL)
        self.assertIn(f"Not enough history to test '{R.SKILL}'", out)
        self.assertIn("5 of 6 tasks needed", out)
        self.assertIn("No calls were made", out)
        self.assertEqual(llm.calls, [])
        runs = self.home / "plugin-data" / "skilluminati" / "runs"
        self.assertEqual(list(runs.rglob("calls*.jsonl")) if runs.exists() else [], [], "no calls.jsonl anywhere")

    def test_four_usable_tasks_also_stop(self):
        R._db(self.home / "state.db", 4)
        llm = R.JudgeAlwaysWith()
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), R.SKILL)
        self.assertIn("4 of 6 tasks needed", out)
        self.assertEqual(llm.calls, [])

    def test_six_usable_tasks_still_run(self):
        R._db(self.home / "state.db", 6)
        llm = R.JudgeAlwaysWith()
        out = self.h(R.FakeCtx(llm, surface="cli", home=self.home), R.SKILL)
        self.assertNotIn("Not enough history", out)
        self.assertTrue(llm.calls)
