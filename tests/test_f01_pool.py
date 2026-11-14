"""F01: asking again adds evidence. Every judged pair is kept across runs, keyed by skill, skill-file
hash, tested model, judge model and task hash. A task's outcome is the sign of (better minus worse) over its
samples, zero being "same" (R1b); the per-task outcomes go into the unchanged verdict.lean rule. The verdict shows
"12 tasks, 3 runs". Fake model and temp HERMES_HOME only.

V2 fixture: ``tests/fixtures/pool_systematic_debugging.json`` holds only task hashes and outcomes,
derived from the three finished systematic-debugging runs of 12 Nov 2026 (result.json per_skill
pairs). No task text, answers, names or paths."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from tests import test_run as R  # noqa: E402  (puts plugin/ on sys.path)
from skilluminati import copy as sb_copy, evidence, report, run, tools, verdict as sb_verdict  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pool_systematic_debugging.json"
SHA = "0" * 64


class MajorityTests(R.unittest.TestCase):
    """R1b: a task's pooled outcome is the sign of (better count minus worse count); zero is same."""

    def test_sign_of_better_minus_worse(self):
        self.assertEqual(evidence.task_outcome(["win", "win", "loss"]), "win")
        self.assertEqual(evidence.task_outcome(["loss", "win", "loss"]), "loss")
        self.assertEqual(evidence.task_outcome(["tie", "tie", "win"]), "win")
        self.assertEqual(evidence.task_outcome(["win", "tie", "loss"]), "tie")
        self.assertEqual(evidence.task_outcome(["win", "tie"]), "win")
        self.assertEqual(evidence.task_outcome(["win"]), "win")
        self.assertEqual(evidence.task_outcome(["tie"]), "tie")
        self.assertEqual(evidence.task_outcome(["error", "win"]), "win")
        self.assertIsNone(evidence.task_outcome(["error"]))

    def test_even_run_counts(self):
        # two runs: one better and one same is better; one better and one worse is same
        self.assertEqual(evidence.task_outcome(["win", "tie"]), "win")
        self.assertEqual(evidence.task_outcome(["tie", "loss"]), "loss")
        self.assertEqual(evidence.task_outcome(["win", "loss"]), "tie")
        self.assertEqual(evidence.task_outcome(["tie", "tie"]), "tie")
        # four runs
        self.assertEqual(evidence.task_outcome(["win", "tie", "tie", "tie"]), "win")
        self.assertEqual(evidence.task_outcome(["win", "win", "loss", "loss"]), "tie")
        self.assertEqual(evidence.task_outcome(["win", "loss", "loss", "tie"]), "loss")

    def test_odd_run_counts(self):
        self.assertEqual(evidence.task_outcome(["win", "tie", "tie"]), "win")
        self.assertEqual(evidence.task_outcome(["loss", "tie", "tie"]), "loss")
        self.assertEqual(evidence.task_outcome(["win", "win", "loss", "loss", "tie"]), "tie")
        self.assertEqual(evidence.task_outcome(["win", "win", "loss", "tie", "tie"]), "win")

    def test_order_never_matters(self):
        for samples in (["win", "tie", "loss", "win"], ["tie", "loss", "tie"], ["win", "loss"]):
            got = {evidence.task_outcome(list(p)) for p in itertools.permutations(samples)}
            self.assertEqual(len(got), 1, samples)

    def test_pool_line_words(self):
        self.assertEqual(evidence.pool_line(12, 3), "12 tasks, 3 runs")
        self.assertEqual(evidence.pool_line(1, 1), "1 task, 1 run")
        self.assertIsNone(evidence.pool_line(None, 2))


class StoredRunsReplayTests(R.HomeCase):
    """V2: the three finished systematic-debugging runs through the pooled path."""

    def setUp(self):
        super().setUp()
        self.runs = json.loads(FIXTURE.read_text(encoding="utf-8"))["runs"]

    def _feed(self, order):
        evidence.pool_path("systematic-debugging").unlink(missing_ok=True)
        counts = None
        for r in order:
            counts = evidence.add_run("systematic-debugging", skill_sha256=SHA, model="claude-opus-5-5[1m]",
                                      judge="claude-opus-5-5[1m]", run=r["run"], date="2026-11-12", pairs=r["pairs"])
        return counts

    def test_fixture_has_outcomes_only(self):
        self.assertEqual(len(self.runs), 3)
        for r in self.runs:
            self.assertEqual(len(r["pairs"]), 12)
            for p in r["pairs"]:
                self.assertEqual(set(p), {"task_hash", "outcome"})
                self.assertRegex(p["task_hash"], r"^[0-9a-f]{12}$")
        self.assertNotIn("/" + "Users/", FIXTURE.read_text(encoding="utf-8"))

    def test_twelve_tasks_three_runs_same_label_in_all_six_orders(self):
        labels = set()
        for order in itertools.permutations(self.runs):
            c = self._feed(order)
            self.assertEqual((c["n_tasks"], c["n_runs"]), (12, 3))
            # R1b: the sign of (better minus worse) per task gives 8 better, 2 worse, 2 same.
            self.assertEqual((c["wins"], c["losses"], c["ties"]), (8, 2, 2))
            block = evidence.pooled_block(c, model="claude-opus-5-5[1m]")
            labels.add((block["lean"]["verdict"], block["lean"]["strength"]))
            self.assertEqual(evidence.pool_line(block["n_tasks"], block["n_runs"]), "12 tasks, 3 runs")
        self.assertEqual(len(labels), 1, labels)
        self.assertEqual(next(iter(labels))[0], sb_verdict.HELPS)

    def test_two_disagreeing_runs_do_not_collapse_to_dead_weight(self):
        # run 1 alone is 7/1/4 and run 2 alone is 5/1/6, both lean helps; pooled they must not become
        # Dead weight (the old strict-majority rule made it 3/0/9). Same for runs 2 and 3.
        for pick in ((0, 1), (1, 2), (0, 2)):
            for order in itertools.permutations([self.runs[i] for i in pick]):
                c = self._feed(order)
                self.assertEqual(c["n_runs"], 2)
                lean = evidence.pooled_block(c, model="claude-opus-5-5[1m]")["lean"]
                self.assertNotEqual(lean["verdict"], sb_verdict.NO_DIFFERENCE, (pick, c["wins"], c["losses"], c["ties"]))
                self.assertGreater(c["wins"], c["ties"], pick)

    def test_rule_is_the_existing_lean_rule(self):
        c = self._feed(self.runs)
        self.assertEqual(evidence.pooled_block(c)["lean"], sb_verdict.lean(c["wins"], c["losses"], c["ties"]))


class PoolKeyTests(R.HomeCase):
    def _add(self, sha=SHA, model="m1", judge="j1", run="r1", pairs=None):
        return evidence.add_run("s", skill_sha256=sha, model=model, judge=judge, run=run, date="2026-11-14",
                                pairs=pairs or [{"cell": "s", "task_hash": "aaaaaaaaaaaa", "outcome": "win"}])

    def test_changed_hash_model_or_judge_starts_a_fresh_pool(self):
        self.assertEqual(self._add(run="r1")["n_runs"], 1)
        self.assertEqual(self._add(run="r2")["n_runs"], 2)
        self.assertEqual(self._add(sha="1" * 64, run="r3")["n_runs"], 1)
        self.assertEqual(self._add(model="m2", run="r4")["n_runs"], 1)
        self.assertEqual(self._add(judge="j2", run="r5")["n_runs"], 1)
        self.assertEqual(self._add(run="r6")["n_runs"], 3, "the first pool is kept, never mixed")
        data = json.loads(evidence.pool_path("s").read_text())
        self.assertEqual(len(data["pools"]), 4)

    def test_same_run_twice_is_counted_once(self):
        self._add(run="r1")
        self.assertEqual(self._add(run="r1")["n_runs"], 1)

    def test_no_hash_or_no_model_is_not_pooled(self):
        self.assertIsNone(self._add(sha=None))
        self.assertIsNone(self._add(model=None))
        self.assertFalse(evidence.pool_path("s").exists())

    def test_noise_pairs_are_not_pooled_and_errors_never_count(self):
        c = self._add(pairs=[{"cell": "s", "task_hash": "aaaaaaaaaaaa", "outcome": "win"},
                             {"cell": "aa", "task_hash": "bbbbbbbbbbbb", "outcome": "loss"},
                             {"cell": "s", "task_hash": "cccccccccccc", "outcome": "error"}])
        self.assertEqual((c["n_tasks"], c["wins"], c["losses"]), (1, 1, 0))


class EndToEndTests(R.HomeCase):
    def setUp(self):
        super().setUp()
        evidence.clear_cache()
        d = self.home / "skills" / "notes" / R.SKILL
        d.mkdir(parents=True)
        self.skill_md = d / "SKILL.md"
        self.skill_md.write_text(R.SKILL_MD, encoding="utf-8")
        R._db(self.home / "state.db", 6)

    def tearDown(self):
        run.wait_active(10)
        super().tearDown()

    def _ask(self):
        return self.h(R.FakeCtx(R.JudgeAlwaysWith(), surface="cli", home=self.home), R.SKILL)

    def _row(self):
        data = json.loads((self.home / "plugin-data" / "skilluminati" / "verdicts.json").read_text())
        rows = [r for r in data["runs"] if r["skill"] == R.SKILL]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_asking_again_adds_a_run_and_keeps_the_first(self):
        first = self._ask()
        self.assertIn("6 tasks, 1 run.", first)
        import time
        time.sleep(1.1)  # run folders are stamped to the second
        second = self._ask()
        self.assertIn("6 tasks, 2 runs.", second)
        row = self._row()
        self.assertEqual((row["pool_tasks"], row["pool_runs"]), (6, 2))
        self.assertEqual((row["wins"], row["losses"], row["ties"]), (6, 0, 0))
        pool = json.loads(evidence.pool_path(R.SKILL).read_text())
        runs = pool["pools"][0]["runs"]
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(len(r["pairs"]) == 6 for r in runs), "A/A noise pairs are not pooled")
        # R7: outcomes plus two bools (tool_driven, empty_answer) for the F05 flag; no text, ever
        self.assertTrue(all(set(p) == {"task_hash", "outcome", "tool_driven", "empty_answer"}
                            for r in runs for p in r["pairs"]))
        # chat result (agent tool), report, dashboard rows
        got = json.loads(tools.result({"skill": R.SKILL}))
        self.assertIn("6 tasks, 2 runs", got["report"])
        from tests.test_f03_binding import api
        api_row = api.read_verdicts()["runs"][0]
        self.assertEqual((api_row["pool_tasks"], api_row["pool_runs"], api_row["pool_line"]), (6, 2, "6 tasks, 2 runs"))
        page = api.read_page(self.home)["results"][0]
        self.assertEqual(page["pool_line"], "6 tasks, 2 runs")

    def test_chat_result_line_carries_the_count(self):
        self._ask()
        folder = report.find_run(skill=R.SKILL, runs_dir=self.home / "plugin-data" / "skilluminati" / "runs")
        line = run.result_line(folder, R.SKILL)
        self.assertEqual(line, f"{R.SKILL}: Enlightened (Probable). Better 6 · Same 0 · Worse 0. 6 tasks, 1 run.")
        self.assertLess(len(line), 120)

    def test_changed_skill_starts_a_fresh_pool(self):
        self._ask()
        import time
        time.sleep(1.1)
        self.skill_md.write_bytes(self.skill_md.read_bytes() + b" ")
        out = self._ask()
        self.assertIn("6 tasks, 1 run.", out)
        self.assertEqual(self._row()["pool_runs"], 1)

    def test_old_verdicts_rows_still_load_and_a_pool_starts_at_one_run(self):
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 1, "aa": [], "runs": [
            {"skill": R.SKILL, "verdict": "Keep", "model": R.ACTIVE, "judge": R.ACTIVE, "date": "2026-11-12",
             "wins": 7, "losses": 1, "ties": 4, "low": 0.5, "high": 0.9}]}))
        from tests.test_f03_binding import api
        self.assertNotIn("error", api.read_verdicts())
        self._ask()
        row = self._row()
        self.assertEqual((row["pool_runs"], row["wins"]), (1, 6), "the old row is replaced, never pooled")

    def test_a_run_that_stopped_early_is_not_pooled(self):
        class Limited(R.JudgeAlwaysWith):
            def complete(self, messages, **kw):
                if len(self.calls) >= 6:
                    raise RuntimeError("429 usage limit reached")
                return super().complete(messages, **kw)
        self.h(R.FakeCtx(Limited(), surface="cli", home=self.home), R.SKILL)
        self.assertFalse(evidence.pool_path(R.SKILL).exists())


class CopyTests(R.unittest.TestCase):
    def test_pool_line_lives_in_copy(self):
        self.assertEqual(sb_copy.POOL_LINE.format(n=12, tasks="tasks", r=3, runs="runs"), "12 tasks, 3 runs")
