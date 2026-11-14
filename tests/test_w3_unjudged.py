"""In plain words: when the judge gives back nothing it can read (an empty reply, or a reasoning
model that spent its whole budget thinking), that pair was never judged. It must not count as
"Same", or a broken judge reads as "the skill makes no difference".

Mock LLM only: no network, no model calls."""

from __future__ import annotations

import unittest

from tests import test_realrun as T  # noqa: E402  (puts plugin/ on sys.path, shares the fixtures)

from skilluminati import judge as sb_judge, realrun, noise  # noqa: E402


class EmptyJudge(T.FakeLlm):
    def __init__(self):
        super().__init__(judge_text="")


class Unjudged(unittest.TestCase):
    setUp = T.EndToEnd.setUp
    tearDown = T.EndToEnd.tearDown

    def test_empty_judge_replies_are_not_ties(self):
        rr = realrun.RealRunner(EmptyJudge(), self.cfg, log=lambda s: None)
        res = rr.run()
        a = res["per_skill"][T.SKILL_A]
        c = a["combined"]
        self.assertEqual((c["wins"], c["losses"], c["ties"]), (0, 0, 0))
        self.assertEqual(a["n_errors"], 5)
        for p in a["pairs"]:
            self.assertEqual(p["outcome"], "error")
        # A/A pairs the judge never read do not count toward the same-vs-same figure either.
        self.assertEqual(res["aa"]["n_errors"], res["aa"]["n_pairs"])

    def test_one_readable_order_still_counts(self):
        # Existing rule kept: one order read, one not -> a tie for the pair (judge.combine_orders).
        # One worker, so each pair's two judge calls come one after the other: order 1 reads, order 2 is empty.
        import dataclasses

        class HalfJudge(T.FakeLlm):
            def __init__(self):
                super().__init__()
                self.n = 0

            def complete(self, messages, **kw):
                if messages[0]["content"].startswith("# Purpose-based"):
                    self.n += 1
                    self.judge_text = '{"winner": "tie", "reason": "same"}' if self.n % 2 else ""
                return super().complete(messages, **kw)

        rr = realrun.RealRunner(HalfJudge(), dataclasses.replace(self.cfg, workers=1), log=lambda s: None)
        a = rr.run()["per_skill"][T.SKILL_A]
        self.assertEqual(a["n_errors"], 0)
        self.assertEqual(a["combined"]["ties"], 5)

    def test_unread_helper(self):
        j = sb_judge.PairJudgement(outcome="tie", agreed=False, orders=[], first_picks=0, decisive_judgements=0,
                                   parse_failures=2, call_failures=0, mode=sb_judge.MODE_BLIND)
        self.assertTrue(realrun.unread(j))
        j2 = sb_judge.PairJudgement(outcome="tie", agreed=False, orders=[], first_picks=0, decisive_judgements=0,
                                    parse_failures=1, call_failures=0, mode=sb_judge.MODE_BLIND)
        self.assertFalse(realrun.unread(j2))


if __name__ == "__main__":
    unittest.main()


from tests import test_run as HR  # noqa: E402
from skilluminati import copy as sb_copy, paths, run  # noqa: E402
import json  # noqa: E402

GOOD = {"skill": "s", "combined": {"verdict": "Keep", "wins": 8, "losses": 0, "ties": 4, "low": 0.6, "high": 1.0},
        "stamp": {"date": "2026-11-14", "test_type": "single-call replay"}, "user_label": "Earns its keep",
        "recommendation": {"flags": []}, "n_tasks": 12, "n_errors": 0, "run": "r-good"}
BROKEN = {**GOOD, "combined": {"verdict": "Unsure", "wins": 0, "losses": 0, "ties": 2, "low": 0.0, "high": 1.0},
          "user_label": "Not sure yet", "recommendation": {"flags": []}, "n_errors": 10, "n_unread": 10, "run": "r-broken"}
AA = {"cell": "aa", "model": "m", "wins": 0, "losses": 0, "ties": 0, "low": 0.0, "high": 1.0, "firm": 0,
      "pairs": [{"task_hash": f"t{i}", "outcome": "error"} for i in range(4)]}


class StoreUnread(HR.HomeCase):
    def rows(self):
        return json.loads(paths.verdicts_path().read_text(encoding="utf-8"))

    def test_broken_judge_never_replaces_a_real_verdict(self):
        run.store_verdict(dict(GOOD), model="m", judge="m")
        run.store_verdict(json.loads(json.dumps(BROKEN)), model="m", judge="other", aa=dict(AA))
        data = self.rows()
        self.assertEqual([(r["run"], r["judge"]) for r in data["runs"]], [("r-good", "m")])
        self.assertFalse(any(e.get("judge") == "other" for e in data.get("aa_pool") or []))

    def test_alone_it_is_stored_and_flagged(self):
        run.store_verdict(json.loads(json.dumps(BROKEN)), model="m", judge="other", aa=dict(AA))
        (row,) = self.rows()["runs"]
        texts = [f["text"] for f in row["recommendation"]["flags"]]
        self.assertIn(sb_copy.FLAG_UNREAD.format(n=10, total=12), texts)

    def test_mostly_read_run_replaces_as_before(self):
        run.store_verdict(dict(GOOD), model="m", judge="m")
        run.store_verdict({**GOOD, "n_errors": 6, "n_unread": 6, "run": "r-half"}, model="m", judge="other")
        self.assertEqual([r["run"] for r in self.rows()["runs"]], ["r-half"])


class Gate2(Unjudged):
    """Gate 2 review: the Judge failed flag must reach result.json, the chat report and the page row,
    count only pairs the judge never read (not empty subject answers), and keep such a run out of the pool."""

    def test_summary_counts_unread_pairs_not_subject_errors(self):
        a = realrun.RealRunner(EmptyJudge(), self.cfg, log=lambda s: None).run()["per_skill"][T.SKILL_A]
        self.assertEqual(a["n_unread"], 5)
        self.assertEqual(run.judge_mostly_unread(a), (5, 5))
        # empty subject answers are errors but not unread judge pairs
        self.assertIsNone(run.judge_mostly_unread({"n_tasks": 12, "n_errors": 7, "n_unread": 0}))
        self.assertIsNone(run.judge_mostly_unread({"n_tasks": 12, "n_errors": 7}))

    def test_flag_and_step(self):
        s = {"n_tasks": 12, "n_unread": 11, "recommendation": {"flags": [], "next_step": {"key": "run_more", "text": "x", "n": 3}}}
        run.mark_judge_unread(s)
        run.mark_judge_unread(s)  # idempotent
        flags = s["recommendation"]["flags"]
        self.assertEqual([f["key"] for f in flags], [run.FLAG_UNREAD_KEY])
        self.assertEqual(s["recommendation"]["next_step"]["text"], sb_copy.NEXT_CHECK_JUDGE)

    def test_page_and_chat_show_it(self):
        from skilluminati import report
        row = {"skill": "s", "wins": 0, "losses": 0, "ties": 1, "model": "m", "judge": "j",
               "recommendation": {"flags": [{"key": run.FLAG_UNREAD_KEY, "text": sb_copy.FLAG_UNREAD.format(n=11, total=12)}]}}
        out = report.page_result(row, None)
        self.assertTrue(out["working"]["judge_unread"])
        js = (T.REPO / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("judge_unread", js)
        self.assertIn("judge_failed_tag", js)
        self.assertIn("judge_failed_tag", sb_copy.page_copy())
