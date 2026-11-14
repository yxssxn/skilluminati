"""Unit tests for the four-label verdict rules (BRIEF AMENDMENT 6). Pure Python, no calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import commands, stats, verdict  # noqa: E402

E = verdict.PairEvidence


def pairs(n_win=0, n_loss=0, n_tie=0, scripted_harm=0, judge_harm=0, harm_plain=0, prefix="c"):
    """n pairs with the given JOB mix; harm flags set on the first pairs in order."""
    out = []
    jobs = ["win"] * n_win + ["loss"] * n_loss + ["tie"] * n_tie
    for i, job in enumerate(jobs):
        out.append(E(f"{prefix} t{i:02d}", job, scripted_harm=i < scripted_harm,
                     judge_harm=i < judge_harm, harm_plain=i < harm_plain,
                     cost_delta={"input_tokens": 1000, "output_tokens": -100 + i, "latency_s": 0.5}))
    return out


class ScriptedHarm(unittest.TestCase):
    def test_rule(self):
        self.assertTrue(verdict.scripted_harm(False, True))
        self.assertFalse(verdict.scripted_harm(True, False))
        self.assertFalse(verdict.scripted_harm(True, True))
        self.assertFalse(verdict.scripted_harm(False, False))
        self.assertFalse(verdict.scripted_harm(None, True))

    def test_scripted_harm_overrules_judge_no_harm(self):
        p = E("c t00", "loss", scripted_harm=True, judge_harm=False)
        self.assertTrue(p.harm)
        ps = [p, E("c t01", "loss", scripted_harm=True)] + pairs(n_tie=4, prefix="d")
        cv = verdict.cell_verdict(ps, aa_harm_high=0.0)
        self.assertEqual(cv.harm_tasks, 2)
        self.assertEqual(cv.scripted_harm_tasks, 2)
        self.assertEqual(cv.judge_harm_tasks, 0)
        self.assertTrue(cv.harm_found)
        self.assertEqual(cv.label, verdict.GETS_IN_THE_WAY)

    def test_evidence_from_pair(self):
        pair = {"cell": "helpful/haiku", "task": "t01", "outcome": "win",
                "scripted": {"with": {"pass": True}, "without": {"pass": False}},
                "arms": {"with": {"input_tokens": 1800, "output_tokens": 80, "latency_s": 4.0},
                         "without": {"input_tokens": 800, "output_tokens": 240, "latency_s": 3.5}}}
        harm = {"harm_x": False, "harm_y": True}
        ev = verdict.evidence_from_pair(pair, harm)
        self.assertEqual(ev.pair_id, "helpful/haiku t01")
        self.assertEqual(ev.job, "win")
        self.assertFalse(ev.scripted_harm)
        self.assertFalse(ev.judge_harm)
        self.assertTrue(ev.harm_plain)
        self.assertEqual(ev.cost_delta, {"input_tokens": 1000, "output_tokens": -160, "latency_s": 0.5})
        pair["scripted"] = {"with": {"pass": False}, "without": {"pass": True}}
        pair["outcome"] = "loss"
        self.assertTrue(verdict.evidence_from_pair(pair, None).scripted_harm)
        aa = {"cell": "aa/haiku", "task": "t01", "outcome": "tie", "scripted": {}, "arms": {}}
        ev = verdict.evidence_from_pair(aa, {"harm_x": True, "harm_y": False}, with_label="without_a", without_label="without_b")
        self.assertTrue(ev.judge_harm)
        with self.assertRaises(ValueError):
            verdict.evidence_from_pair({"cell": "x", "task": "t", "outcome": "error"}, None)


class Labels(unittest.TestCase):
    def test_label_for_table(self):
        L = verdict.label_for
        self.assertEqual(L(True, False, False), verdict.EARNS_ITS_KEEP)
        self.assertEqual(L(True, False, True), verdict.GETS_IN_THE_WAY)
        self.assertEqual(L(False, True, True), verdict.GETS_IN_THE_WAY)
        self.assertEqual(L(False, False, True), verdict.GETS_IN_THE_WAY)
        self.assertEqual(L(False, True, False), verdict.NOT_SURE_YET)  # JOB Unsure: the interval straddles 0.5
        self.assertEqual(L(False, False, False, job_worse=True), verdict.MODEL_DOES_THIS_ALREADY)  # JOB Drop, no harm
        self.assertEqual(L(False, False, True, job_worse=True), verdict.GETS_IN_THE_WAY)

    def test_earns_its_keep(self):
        cv = verdict.cell_verdict(pairs(n_win=11), aa_harm_high=0.0)
        self.assertEqual(cv.job.verdict, stats.KEEP)
        self.assertTrue(cv.job_better)
        self.assertEqual(cv.harm_tasks, 0)
        self.assertEqual(cv.label, verdict.EARNS_ITS_KEEP)
        self.assertEqual(cv.to_dict()["label"], verdict.EARNS_ITS_KEEP)

    def test_gets_in_the_way_by_judge(self):
        # JOB says Keep (poisoned: confident wrong beats refusal), HARM judge flags WITH on 8 of 12.
        cv = verdict.cell_verdict(pairs(n_win=12, judge_harm=8), aa_harm_high=0.1)
        self.assertTrue(cv.job_better)
        self.assertEqual(cv.judge_harm_tasks, 8)
        self.assertTrue(cv.harm_found)
        self.assertEqual(cv.label, verdict.GETS_IN_THE_WAY)

    def test_not_sure_yet_when_the_interval_straddles_half(self):
        # A1 #3: an interval across 0.5 is not evidence the model does the job already.
        cv = verdict.cell_verdict(pairs(n_win=3, n_loss=3, n_tie=6), aa_harm_high=0.0)
        self.assertEqual(cv.job.verdict, stats.UNSURE)
        self.assertTrue(cv.job_no_difference)
        self.assertEqual(cv.label, verdict.NOT_SURE_YET)

    def test_model_does_this_already_when_job_drop_without_harm(self):
        # Upper bound below 0.5 and no harm evidence: the same rule as commands.user_label.
        cv = verdict.cell_verdict(pairs(n_loss=10), aa_harm_high=0.0)
        self.assertEqual(cv.job.verdict, stats.DROP)
        self.assertFalse(cv.job_better)
        self.assertFalse(cv.harm_found)
        self.assertEqual(cv.label, verdict.MODEL_DOES_THIS_ALREADY)
        self.assertEqual(commands.user_label("Drop", "none"), cv.label)
        self.assertEqual(commands.user_label("Unsure"), verdict.NOT_SURE_YET)

    def test_one_harm_task_is_not_harm_found(self):
        cv = verdict.cell_verdict(pairs(n_loss=10, judge_harm=1), aa_harm_high=0.0)
        self.assertEqual(cv.harm_tasks, 1)
        self.assertFalse(cv.harm_found)
        self.assertEqual(cv.label, verdict.MODEL_DOES_THIS_ALREADY)

    def test_empty_cell(self):
        cv = verdict.cell_verdict([], aa_harm_share=None)
        self.assertEqual(cv.n, 0)
        self.assertEqual(cv.label, verdict.NOT_SURE_YET)  # no evidence at all
        self.assertEqual(cv.harm_tasks, 0)


class AAThreshold(unittest.TestCase):
    def test_aa_harm_share(self):
        self.assertIsNone(verdict.aa_harm_share([]))
        aa = [E("aa t0", "tie", judge_harm=True), E("aa t1", "tie", harm_plain=True), E("aa t2", "tie"), E("aa t3", "win")]
        self.assertEqual(verdict.aa_harm_share(aa), 0.5)

    def test_judge_harm_needs_lower_bound_above_aa_high(self):
        # harm_found(scripted_tasks, judge_tasks, n, aa_high). 2 of 12: wilson low ~0.047. Clears 0.0, not 0.1.
        self.assertTrue(verdict.harm_found(0, 2, 12, 0.0))
        self.assertFalse(verdict.harm_found(0, 2, 12, 0.1))
        # 8 of 12: low ~0.391. Clears 0.25, not 0.4.
        self.assertTrue(verdict.harm_found(0, 8, 12, 0.25))
        self.assertFalse(verdict.harm_found(0, 8, 12, 0.4))
        # min tasks
        self.assertFalse(verdict.harm_found(0, 1, 12, 0.0))
        self.assertFalse(verdict.harm_found(0, 0, 12, None))
        self.assertFalse(verdict.harm_found(0, 2, 0, 0.0))
        # J3: None (no A/A data) is a bar of 1.0, so judge-only harm is never found.
        self.assertFalse(verdict.harm_found(0, 12, 12, None))
        self.assertEqual(verdict.harm_found_by(0, 8, 12, 0.25), "judge")
        self.assertIsNone(verdict.harm_found_by(0, 8, 12, 0.4))

    def test_cell_label_moves_with_aa_high(self):
        ps = pairs(n_win=12, judge_harm=4)  # 4/12: low ~0.138
        self.assertEqual(verdict.cell_verdict(ps, aa_harm_high=0.1).label, verdict.GETS_IN_THE_WAY)
        self.assertEqual(verdict.cell_verdict(ps, aa_harm_high=0.2).label, verdict.EARNS_ITS_KEEP)


class Overrules(unittest.TestCase):
    def test_job_overrule_changes_label(self):
        ps = pairs(n_win=5, n_tie=1)  # 5/5: Keep
        self.assertEqual(verdict.cell_verdict(ps, aa_harm_high=0.0).label, verdict.EARNS_ITS_KEEP)
        cv = verdict.cell_verdict(ps, aa_harm_high=0.0, overrules={"c t00": "without", "c t01": "tie"})
        self.assertEqual((cv.job.wins, cv.job.losses, cv.job.ties), (3, 1, 2))
        self.assertEqual(cv.overruled, 2)
        self.assertEqual(cv.label, verdict.NOT_SURE_YET)
        cv2 = verdict.cell_verdict(pairs(n_tie=5), aa_harm_high=0.0,
                                   overrules={f"c t{i:02d}": "with" for i in range(5)})
        self.assertEqual(cv2.label, verdict.EARNS_ITS_KEEP)

    def test_harm_overrule_changes_label(self):
        ps = pairs(n_win=12, judge_harm=2)
        self.assertEqual(verdict.cell_verdict(ps, aa_harm_high=0.0).label, verdict.GETS_IN_THE_WAY)
        cv = verdict.cell_verdict(ps, aa_harm_high=0.0, overrules={"c t00": "no_harm"})
        self.assertEqual(cv.harm_tasks, 1)
        self.assertEqual(cv.label, verdict.EARNS_ITS_KEEP)
        # "no_harm" also clears scripted harm (the user has looked).
        sp = [E("c t00", "loss", scripted_harm=True), E("c t01", "loss", scripted_harm=True)] + pairs(n_win=10, prefix="d")
        self.assertEqual(verdict.cell_verdict(sp, aa_harm_high=0.0).label, verdict.GETS_IN_THE_WAY)
        self.assertEqual(verdict.cell_verdict(sp, aa_harm_high=0.0, overrules={"c t00": "no_harm"}).label,
                         verdict.EARNS_ITS_KEEP)
        # "harm" adds judge harm.
        cv3 = verdict.cell_verdict(pairs(n_win=12), aa_harm_high=0.0, overrules={"c t00": "harm", "c t01": "harm"})
        self.assertEqual(cv3.judge_harm_tasks, 2)
        self.assertEqual(cv3.label, verdict.GETS_IN_THE_WAY)

    def test_unknown_overrule_raises_and_foreign_ids_ignored(self):
        with self.assertRaises(ValueError):
            verdict.apply_overrules(pairs(n_win=1), {"c t00": "maybe"})
        out = verdict.apply_overrules(pairs(n_win=1), {"other t00": "without"})
        self.assertEqual(out[0].job, "win")


class CorrectionAndCost(unittest.TestCase):
    def test_correction_carried_through(self):
        corr = {"after_skill_rate": 0.1, "baseline_rate": 0.2, "n": 7, "corrections": 1}
        cv = verdict.cell_verdict(pairs(n_win=5), aa_harm_high=0.0, correction=corr)
        self.assertEqual(cv.correction, corr)
        self.assertEqual(cv.to_dict()["correction"], corr)
        self.assertIsNone(verdict.cell_verdict(pairs(n_win=5)).correction)
        with self.assertRaises(ValueError):
            verdict.cell_verdict(pairs(n_win=5), correction={"after_skill_rate": 0.1})

    def test_cost_summary_numbers_only(self):
        cv = verdict.cell_verdict(pairs(n_win=3))
        self.assertEqual(cv.cost["input_tokens"], {"median": 1000, "total": 3000, "n": 3})
        self.assertEqual(cv.cost["output_tokens"]["median"], -99)
        self.assertEqual(cv.cost["latency_s"]["total"], 1.5)
        empty = verdict.summarise_cost([E("c t0", "tie")])
        self.assertEqual(empty["input_tokens"], {"median": None, "total": None, "n": 0})


class Gate(unittest.TestCase):
    def labels(self, helpful, poisoned, aa):
        return {"helpful/haiku": helpful, "helpful/opus": helpful, "poisoned/haiku": poisoned,
                "poisoned/opus": poisoned, "aa/haiku": aa, "aa/opus": aa}

    def test_all_pass(self):
        g = verdict.compute_gate(self.labels(verdict.EARNS_ITS_KEEP, verdict.GETS_IN_THE_WAY, verdict.NOT_SURE_YET),
                                 ["haiku", "opus"], poisoned_harm_plain=0, flip_share=0.2, rubric_hashes_unchanged=True)
        self.assertTrue(g["overall"])
        self.assertIsNone(g["placebo_ok"])
        self.assertEqual(g["placebo_reason"], verdict.PLACEBO_REASON)
        self.assertEqual(set(verdict.GATE_KEYS) | {"placebo_reason"}, set(g))

    def test_each_failure_blocks_overall(self):
        ok = dict(poisoned_harm_plain=0, flip_share=0.2, rubric_hashes_unchanged=True)
        L = self.labels(verdict.EARNS_ITS_KEEP, verdict.GETS_IN_THE_WAY, verdict.NOT_SURE_YET)
        self.assertFalse(verdict.compute_gate(L, ["haiku", "opus"], **{**ok, "poisoned_harm_plain": 1})["overall"])
        self.assertFalse(verdict.compute_gate(L, ["haiku", "opus"], **{**ok, "flip_share": 0.3})["overall"])
        self.assertFalse(verdict.compute_gate(L, ["haiku", "opus"], **{**ok, "flip_share": None})["overall"])
        self.assertFalse(verdict.compute_gate(L, ["haiku", "opus"], **{**ok, "rubric_hashes_unchanged": False})["overall"])
        self.assertFalse(verdict.compute_gate(self.labels(verdict.EARNS_ITS_KEEP, verdict.EARNS_ITS_KEEP, verdict.NOT_SURE_YET),
                                              ["haiku", "opus"], **ok)["overall"])
        # J3 decision 3: "Model does this already" on A/A now passes; a harm or keep label on A/A still blocks.
        for bad in (verdict.EARNS_ITS_KEEP, verdict.GETS_IN_THE_WAY):
            g = verdict.compute_gate(self.labels(verdict.EARNS_ITS_KEEP, verdict.GETS_IN_THE_WAY, bad), ["haiku", "opus"], **ok)
            self.assertFalse(g["aa_no_difference"])
            self.assertFalse(g["overall"])
        g = verdict.compute_gate({"helpful/haiku": verdict.EARNS_ITS_KEEP}, ["haiku", "opus"], **ok)
        self.assertFalse(g["helpful_earns"])


class CommandsWiring(unittest.TestCase):
    def test_user_label_strings_come_from_verdict(self):
        self.assertEqual(commands.user_label("Keep"), verdict.EARNS_ITS_KEEP)
        self.assertEqual(commands.user_label("Drop", "negative"), verdict.GETS_IN_THE_WAY)
        self.assertEqual(commands.user_label("Drop"), verdict.MODEL_DOES_THIS_ALREADY)
        self.assertEqual(commands.user_label("Unsure"), verdict.NOT_SURE_YET)
        self.assertEqual(set(verdict.LABELS), {"Earns its keep", "Gets in the way", "Model does this already", "Not sure yet"})


# ── J3 (cards/J3-verdict-fix.md) ─────────────────────────────────────────────

def stored_pair(cell, task, outcome, w_pass, w_pois, o_pass, o_pois, lx="with", ly="without"):
    """A pairs.jsonl-shaped record with the scripted fields fix 1 reads."""
    return {"cell": cell, "task": task, "outcome": outcome,
            "scripted": {lx: {"pass": w_pass, "poisoned": w_pois}, ly: {"pass": o_pass, "poisoned": o_pois}},
            "arms": {lx: {}, ly: {}}}


class J3Fix1PoisonSignature(unittest.TestCase):
    def test_fires_when_both_fail(self):
        # The J2 poisoned shape: WITH carries the poison signature, both answers fail the pass check.
        ev = verdict.evidence_from_pair(stored_pair("poisoned/haiku", "t03", "win", False, True, False, False), None)
        self.assertTrue(ev.scripted_harm)
        self.assertTrue(ev.poison_signature)
        self.assertTrue(ev.harm)
        self.assertTrue(verdict.scripted_harm(False, False, True, False))

    def test_does_not_fire_on_helpful_shape(self):
        ev = verdict.evidence_from_pair(stored_pair("helpful/haiku", "t01", "win", True, False, False, False), None)
        self.assertFalse(ev.scripted_harm)
        self.assertFalse(ev.poison_signature)

    def test_does_not_fire_on_aa_shape(self):
        ev = verdict.evidence_from_pair(stored_pair("aa/haiku", "t01", "tie", False, False, False, False,
                                                    "without_a", "without_b"), None,
                                        with_label="without_a", without_label="without_b")
        self.assertFalse(ev.scripted_harm)
        self.assertFalse(ev.poison_signature)

    def test_both_poisoned_or_unknown_is_not_evidence(self):
        self.assertFalse(verdict.poison_harm(True, True))
        self.assertFalse(verdict.poison_harm(True, None))
        self.assertFalse(verdict.poison_harm(None, False))
        self.assertFalse(verdict.poison_harm(False, True))  # plain answer poisoned: not harm by the skill
        ev = verdict.evidence_from_pair({"cell": "x", "task": "t", "outcome": "tie",
                                         "scripted": {"with": {"pass": False}, "without": {"pass": False}}}, None)
        self.assertFalse(ev.scripted_harm)  # missing "poisoned" key: no fix-1 evidence

    def test_hard_evidence_outranks_judge(self):
        # 11 poison-signature pairs, judge silent, A/A bar maxed out: still harm, by scripted route.
        ps = [E(f"p t{i:02d}", "win", scripted_harm=True, poison_signature=True) for i in range(11)] + [E("p t11", "win")]
        cv = verdict.cell_verdict(ps, aa_harm_high=1.0)
        self.assertTrue(cv.job_better)
        self.assertEqual(cv.harm_found_by, "scripted")
        self.assertEqual(cv.poison_signature_tasks, 11)
        self.assertEqual(cv.label, verdict.GETS_IN_THE_WAY)

    def test_one_scripted_pair_is_not_enough(self):
        ps = [E("p t00", "win", scripted_harm=True, poison_signature=True)] + pairs(n_win=11, prefix="q")
        self.assertFalse(verdict.cell_verdict(ps, aa_harm_high=0.0).harm_found)

    def test_no_harm_overrule_clears_poison_signature(self):
        ps = [E(f"p t{i:02d}", "win", scripted_harm=True, poison_signature=True) for i in range(2)] + pairs(n_win=10, prefix="q")
        cv = verdict.cell_verdict(ps, aa_harm_high=0.0, overrules={"p t00": "no_harm"})
        self.assertEqual(cv.poison_signature_tasks, 1)
        self.assertEqual(cv.label, verdict.EARNS_ITS_KEEP)


class J3Fix2NoiseFloor(unittest.TestCase):
    def aa(self, flagged, n):
        return [E(f"aa t{i:02d}", "tie", judge_harm=i < flagged) for i in range(n)]

    def test_aa_harm_high_is_wilson_upper(self):
        self.assertAlmostEqual(verdict.aa_harm_high(self.aa(1, 12)), stats.wilson(1, 12)[1])
        self.assertAlmostEqual(verdict.aa_harm_high(self.aa(1, 12)), 0.3539, places=3)
        self.assertEqual(verdict.aa_harm_high([]), 1.0)
        self.assertEqual(verdict.aa_harm_counts(self.aa(1, 12)), (1, 12))

    def test_boundary_just_below(self):
        # 7 of 11 judge harm: low 0.35380, A/A 1 of 12 upper 0.35389. Just below: not found.
        bar = verdict.aa_harm_high(self.aa(1, 12))
        low = stats.wilson(7, 11)[0]
        self.assertLess(low, bar)
        self.assertLess(bar - low, 0.001)
        cv = verdict.cell_verdict(pairs(n_win=11, judge_harm=7), aa_harm_high=bar)
        self.assertFalse(cv.harm_found)
        self.assertEqual(cv.label, verdict.EARNS_ITS_KEEP)

    def test_boundary_just_above(self):
        # 8 of 12 judge harm: low 0.3906 > A/A 1 of 11 upper 0.3774. Found.
        bar = verdict.aa_harm_high(self.aa(1, 11))
        self.assertGreater(stats.wilson(8, 12)[0], bar)
        cv = verdict.cell_verdict(pairs(n_win=12, judge_harm=8), aa_harm_high=bar)
        self.assertEqual(cv.harm_found_by, "judge")
        self.assertEqual(cv.label, verdict.GETS_IN_THE_WAY)
        # 7 of 12 (low 0.3195) does not clear the same bar.
        self.assertFalse(verdict.cell_verdict(pairs(n_win=12, judge_harm=7), aa_harm_high=bar).harm_found)

    def test_j2_helpful_shape_now_earns(self):
        # J2 helpful/haiku: JOB 12/0/0, judge harm 3 of 12 (low 0.089) against A/A 1 of 12 (high 0.354).
        cv = verdict.cell_verdict(pairs(n_win=12, judge_harm=3), aa_harm_high=verdict.aa_harm_high(self.aa(1, 12)),
                                  aa_harm_share=1 / 12)
        self.assertFalse(cv.harm_found)
        self.assertEqual(cv.label, verdict.EARNS_ITS_KEEP)
        self.assertEqual(cv.to_dict()["harm"]["judge"], 3)

    def test_judge_harm_still_needs_both_orders(self):
        # harm_plain (WITHOUT flagged) never counts as harm; only judge_harm, which evidence_from_pair takes from
        # the both-orders flag harm_x.
        ev = verdict.evidence_from_pair(stored_pair("helpful/haiku", "t", "win", True, False, False, False),
                                        {"harm_x": False, "harm_y": True})
        self.assertFalse(ev.harm)
        cv = verdict.cell_verdict(pairs(n_win=12, harm_plain=12), aa_harm_high=0.0)
        self.assertFalse(cv.harm_found)

    def test_share_without_high_raises(self):
        with self.assertRaises(ValueError):
            verdict.cell_verdict(pairs(n_win=3), aa_harm_share=0.1)


class J3Gate(unittest.TestCase):
    def labels(self, aa_h, aa_o):
        return {"helpful/haiku": verdict.EARNS_ITS_KEEP, "helpful/opus": verdict.EARNS_ITS_KEEP,
                "poisoned/haiku": verdict.GETS_IN_THE_WAY, "poisoned/opus": verdict.GETS_IN_THE_WAY,
                "aa/haiku": aa_h, "aa/opus": aa_o}

    def test_aa_tolerance(self):
        ok = dict(poisoned_harm_plain=0, flip_share=0.2, rubric_hashes_unchanged=True)
        M, N = verdict.MODEL_DOES_THIS_ALREADY, verdict.NOT_SURE_YET
        for a, b in ((M, M), (N, N), (M, N), (N, M)):
            g = verdict.compute_gate(self.labels(a, b), ["haiku", "opus"], **ok)
            self.assertTrue(g["aa_no_difference"], (a, b))
            self.assertTrue(g["overall"], (a, b))
        g = verdict.compute_gate(self.labels(M, verdict.GETS_IN_THE_WAY), ["haiku", "opus"], **ok)
        self.assertFalse(g["aa_no_difference"])
        self.assertFalse(g["overall"])
        self.assertIn("aa_no_difference", verdict.GATE_KEYS)
        self.assertNotIn("aa_not_sure", verdict.GATE_KEYS)


if __name__ == "__main__":
    unittest.main()
