"""F11: the noise check reports a real figure. Same-vs-same pairs (two answers made the same way,
judged against each other) are pooled per (tested model, judge model) across runs. A firm pick is a
pair where the Owl named the same winner in both orders (outcome win or loss). The report and page say
"Same-vs-same: a firm pick N of M times (X%)", or "Not enough same-vs-same pairs yet (M)" under 8
pairs, plus a plain note at 50% or more. The old ``noisy`` flag is gone. Temp HERMES_HOME only.

Fixture: ``tests/fixtures/aa_outcomes_9_runs.json`` holds only model ids and outcome labels from the 9
stored verdict runs that have a same-vs-same cell (result.json ``aa.pairs[].outcome``); see its note.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import test_run as R  # noqa: E402  (puts plugin/ on sys.path)
from skilluminati import copy as sb_copy, noise, paths, report, run, stats  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "aa_outcomes_9_runs.json"
WEAK = sb_copy.SAME_VS_SAME_WEAK


def fixture_runs() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["runs"]


class StoredRunsFigureTests(R.unittest.TestCase):
    def test_the_nine_stored_runs_pool_to_33_of_51(self):
        runs = fixture_runs()
        self.assertEqual(len(runs), 9)
        pool: list = []
        for r in runs:
            fig = noise.add_run(pool, model=r["subject_model"], judge=r["judge_model"], run=r["run"], date=None,
                                outcomes=r["outcomes"])
        self.assertEqual(len(pool), 1, "one (model, judge) pair once the [1m] tag is normalised away")
        self.assertEqual((pool[0]["model"], pool[0]["judge"]), ("claude-opus-5-5", "claude-opus-5-5"))
        # The real read path: the stored row for differently written ids, as the page and report find it.
        row = {"model": "claude-opus-5-5", "judge": "claude-opus-5-5", "pooled": fig["n"], "firm": fig["firm"]}
        self.assertIs(noise.stored_row([row], "claude-opus-5-5[1m]", "anthropic/claude-opus-5-5"), row)
        self.assertEqual((fig["firm"], fig["n"], fig["runs"]), (33, 51, 9))
        self.assertTrue(fig["weak"])
        self.assertEqual(noise.lines(fig), ["Same-vs-same: a firm pick 33 of 51 times (65%).", WEAK])

    def test_count_by_hand_matches(self):
        # Counted directly: every judged pair (win, loss or tie) is one of M; win or loss is firm.
        outs = [o for r in fixture_runs() for o in r["outcomes"]]
        self.assertEqual((outs.count("win") + outs.count("loss"), len(outs)), (33, 51))
        self.assertEqual((outs.count("win"), outs.count("loss"), outs.count("tie")), (14, 19, 18))

    def test_the_old_flag_read_false_on_every_stored_run(self):
        # The reason for F11: on <= 9 pairs a run the direction interval never excluded 50%.
        for r in fixture_runs():
            a = stats.aa_noise(r["outcomes"])
            self.assertEqual(stats.verdict(a.wins, a.losses, a.ties).verdict, stats.UNSURE, r["run"])


class FigureRuleTests(R.unittest.TestCase):
    def test_fewer_than_eight_pairs_is_not_enough(self):
        fig = noise.figure(4, 7)
        self.assertFalse(fig["enough"])
        self.assertFalse(fig["weak"], "no note without enough pairs")
        self.assertEqual(noise.lines(fig), ["Not enough same-vs-same pairs yet (7)."])

    def test_half_or_more_adds_the_note(self):
        self.assertEqual(noise.lines(noise.figure(4, 8)), ["Same-vs-same: a firm pick 4 of 8 times (50%).", WEAK])
        self.assertEqual(noise.lines(noise.figure(3, 8)), ["Same-vs-same: a firm pick 3 of 8 times (38%)."])

    def test_errors_do_not_count(self):
        self.assertEqual(noise.firm_counts(["win", "error", "tie", "loss", None]), (2, 3))

    def test_same_run_twice_replaces_and_judges_stay_apart(self):
        pool: list = []
        noise.add_run(pool, model="m", judge="j", run="r1", date=None, outcomes=["win"] * 4)
        fig = noise.add_run(pool, model="m", judge="j", run="r1", date=None, outcomes=["tie"] * 4)
        self.assertEqual((fig["firm"], fig["n"]), (0, 4))
        noise.add_run(pool, model="m", judge="other", run="r2", date=None, outcomes=["win"] * 4)
        self.assertEqual(len(pool), 2)
        self.assertIsNone(noise.add_run(pool, model=None, judge="j", run="r3", date=None, outcomes=["win"]))
        self.assertIsNone(noise.stored_row([{"model": "m", "judge": "j"}], "nobody", "j"))

    def test_strings_live_in_copy(self):
        self.assertFalse(hasattr(sb_copy, "REPORT_NOISY"))
        self.assertFalse(hasattr(sb_copy, "WORKING_NOISE_UNFAIR"))
        for s in (sb_copy.SAME_VS_SAME, sb_copy.SAME_VS_SAME_FEW, WEAK):
            self.assertNotIn("\u2014", s)


SUMMARY = {"skill": "unslop", "combined": {"verdict": "Unsure", "wins": 1, "losses": 1, "ties": 1, "low": 0.1, "high": 0.9},
           "stamp": {"date": "2026-11-14", "test_type": "single-call replay"}, "user_label": "Not sure yet"}


def aa_block(outcomes: list[str], model="claude-opus-5-5[1m]") -> dict:
    a = stats.aa_noise(outcomes)
    return {"cell": "aa", "model": model, "wins": a.wins, "losses": a.losses, "ties": a.ties, "low": 0.0, "high": 1.0,
            "firm": a.n_decisive, "pairs": [{"task_hash": f"t{i}", "outcome": o} for i, o in enumerate(outcomes)]}


class StoreTests(R.HomeCase):
    def store(self, run_id, outcomes, *, judge="claude-opus-5-5[1m]", partial=False):
        aa = aa_block(outcomes)
        run.store_verdict({**SUMMARY, "run": run_id}, model="claude-opus-5-5[1m]", judge=judge, aa=aa, partial=partial)
        return aa, json.loads(paths.verdicts_path().read_text(encoding="utf-8"))

    def test_runs_pool_in_verdicts_json(self):
        runs = fixture_runs()
        for r in runs:
            aa, data = self.store(r["run"], r["outcomes"])
        self.assertEqual(aa["pool"]["firm"], 33)
        self.assertEqual(aa["pool"]["n"], 51)
        self.assertEqual(len(data["aa_pool"]), 1)
        self.assertEqual(len(data["aa_pool"][0]["runs"]), 9)
        self.assertNotIn("text", json.dumps(data["aa_pool"]))
        self.assertEqual(len(data["aa"]), 1)
        row = data["aa"][0]
        self.assertEqual((row["firm"], row["pooled"], row["judge"]), (33, 51, "claude-opus-5-5[1m]"))
        self.assertNotIn("noisy", row)
        self.assertFalse(paths.verdicts_path().with_suffix(".json.tmp").exists(), "atomic write leaves no tmp")

    def test_store_twice_does_not_double_count(self):
        self.store("r1", ["win", "loss", "tie", "win"])
        aa, data = self.store("r1", ["win", "loss", "tie", "win"])
        self.assertEqual((aa["pool"]["firm"], aa["pool"]["n"]), (3, 4))

    def test_partial_run_is_not_pooled(self):
        self.store("r1", ["win"] * 4)
        aa, data = self.store("r2", ["win"] * 4, partial=True)
        self.assertNotIn("pool", aa)
        self.assertEqual(len(data["aa_pool"][0]["runs"]), 1)

    def test_other_judge_is_its_own_row_and_pool(self):
        self.store("r1", ["win"] * 4)
        aa, data = self.store("r2", ["tie"] * 4, judge="gpt-6")
        self.assertEqual((aa["pool"]["firm"], aa["pool"]["n"]), (0, 4))
        self.assertEqual(len(data["aa_pool"]), 2)
        self.assertEqual(sorted(r["judge"] for r in data["aa"]), ["claude-opus-5-5[1m]", "gpt-6"])

    def test_first_pool_seeds_from_runs_on_disk(self):
        runs_dir = paths.runs_dir()
        for i, r in enumerate(fixture_runs()[:2]):  # the two early runs: no model ids in result.json
            d = runs_dir / f"old-{i}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"subject_model": None, "judge_model": None, "aa": {
                "wins": 0, "pairs": [{"outcome": o} for o in r["outcomes"]]}}))
            (d / "calls.jsonl").write_text("\n".join(json.dumps({"role": role, "model": "claude-opus-5-5[1m]"})
                                                     for role in ("subject", "judge")) + "\n")
        st = runs_dir / "selftest"
        st.mkdir()
        (st / "result.json").write_text(json.dumps({"run": {}, "aggregate": {}}))
        aa, data = self.store("new", ["tie"] * 4)
        # 3/9 and 6/9 from disk, then 0/4 from this run
        self.assertEqual((aa["pool"]["firm"], aa["pool"]["n"], aa["pool"]["runs"]), (9, 22, 3))


class ReportAndPageTests(R.unittest.TestCase):
    def test_report_uses_the_pooled_figure(self):
        aa = {"wins": 1, "losses": 0, "ties": 3, "pool": {"firm": 33, "n": 51}}
        self.assertEqual(noise.lines(noise.of_aa(aa)), ["Same-vs-same: a firm pick 33 of 51 times (65%).", WEAK])

    def test_old_run_without_a_pool_uses_its_own_pairs(self):
        self.assertEqual(noise.lines(noise.of_aa({"wins": 2, "losses": 1, "ties": 1})),
                         ["Not enough same-vs-same pairs yet (4)."])

    def test_page_prefers_the_stored_pooled_row(self):
        row = {"skill": "unslop", "verdict_key": "helps", "wins": 1, "losses": 0, "ties": 0,
               "_aa": {"model": "m", "judge": "j", "wins": 1, "losses": 0, "ties": 3, "firm": 33, "pooled": 51}}
        w = report.page_result(row)["working"]
        self.assertEqual((w["noise"]["firm"], w["noise"]["n"]), (33, 51))
        self.assertEqual(w["noise"]["lines"][0], "Same-vs-same: a firm pick 33 of 51 times (65%).")

    def test_page_copy_has_no_noise_flag_words(self):
        page = sb_copy.page_copy()
        self.assertIn("same_vs_same", page)
        self.assertNotIn("working_noise_fair", page)
        js = (Path(__file__).resolve().parents[1] / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertNotIn("noisy", js)


if __name__ == "__main__":
    R.unittest.main()
