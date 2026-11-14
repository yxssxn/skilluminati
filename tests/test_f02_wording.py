"""In plain words: a close "no difference" result from one run is not proof the skill is dead weight.
These checks (V5) make sure such a result reads "No clear difference yet" with the next step "Ask
again", that "Consider archiving" only ever follows a Probable (strong) result, and that a clear
2 / 1 / 9 result still reads Dead weight (Probable). The stored verdict words do not change.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import importlib.util
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from tests import test_run as R  # noqa: E402  (puts plugin/ on sys.path)
from tests.test_ux_v2 import verdict_result  # noqa: E402

from skilluminati import copy as sb_copy, recommend as rec, report, run, verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("skilluminati_plugin_api_f02", ROOT / "plugin" / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

ARCHIVE = "Consider archiving"
UNCLEAR = "No clear difference yet"
ALREADY, WAY, EARNS, NOVICE = (verdict.MODEL_DOES_THIS_ALREADY, verdict.GETS_IN_THE_WAY, verdict.EARNS_ITS_KEEP,
                               verdict.NOT_SURE_YET)
HEAVY = {"flag": True, "text": "Heavy: +2,000 tokens per load"}


def from_lean(w, l, t, heavy=None):
    """The recommendation a real run builds (realrun.py): label from the lean, lean passed in."""
    lean = verdict.lean(w, l, t)
    return rec.recommend(verdict.LEAN_LABEL[lean["verdict"]], wins=w, losses=l, ties=t, lean=lean, heavy=heavy,
                         verdict_model="m", active_model="m")


class V5Tests(unittest.TestCase):
    def test_5_2_5_reads_no_clear_difference_yet_and_ask_again(self):
        self.assertEqual(verdict.lean(5, 2, 5)["strength"], verdict.LIKELY)
        r = from_lean(5, 2, 5)
        self.assertEqual(r["headline"]["themed"], UNCLEAR)
        self.assertTrue(r["headline"]["text"].startswith(UNCLEAR + "."))
        self.assertIn("One run cannot tell", r["headline"]["plain"])
        self.assertIn("small effect", r["headline"]["plain"])
        self.assertEqual((r["next_step"]["key"], r["next_step"]["text"]), (rec.STEP_ASK_AGAIN, "Ask again"))
        self.assertNotIn("Dead weight", rec.format_text(r))
        self.assertNotIn(ARCHIVE, rec.format_text(r))

    def test_stored_category_stays_dead_weight(self):
        r = from_lean(5, 2, 5)
        self.assertEqual(r["headline"]["label"], ALREADY)
        self.assertEqual(r["lean"]["verdict"], verdict.NO_DIFFERENCE)
        self.assertEqual(sb_copy.WORDS["no_difference"], "Dead weight")
        self.assertEqual(rec.lean_of(r)["verdict"], verdict.NO_DIFFERENCE)
        self.assertEqual(report.verdict_key({"recommendation": r}), "no_difference")

    def test_2_1_9_is_still_dead_weight_probable_with_its_advice(self):
        self.assertEqual(verdict.lean(2, 1, 9), {**verdict.lean(2, 1, 9), "verdict": verdict.NO_DIFFERENCE,
                                                 "strength": verdict.STRONG})
        r = from_lean(2, 1, 9)
        self.assertEqual(r["headline"]["themed"], "Dead weight")
        self.assertEqual(r["headline"]["text"], sb_copy.headline(ALREADY)[2])
        self.assertEqual(r["confidence"]["text"], "Probable")
        self.assertEqual((r["next_step"]["key"], r["next_step"]["text"]), (rec.STEP_ARCHIVE, ARCHIVE))
        self.assertEqual(from_lean(2, 1, 9, heavy=HEAVY)["next_step"]["text"], "Consider trimming")

    def test_no_plausible_result_anywhere_says_consider_archiving(self):
        """Every split of 6 to 24 tasks, Heavy or not: archiving only after a Probable result."""
        seen_plausible = 0
        for n in range(6, 25):
            for w, l in itertools.product(range(n + 1), repeat=2):
                t = n - w - l
                if t < 0:
                    continue
                for heavy in (None, HEAVY):
                    r = from_lean(w, l, t, heavy)
                    strong = r["lean"]["strength"] == verdict.STRONG
                    seen_plausible += not strong
                    if not strong:
                        self.assertNotEqual(r["next_step"]["text"], ARCHIVE, (w, l, t))
                        self.assertNotIn(ARCHIVE, rec.format_text(r), (w, l, t))
                    if r["headline"]["label"] == ALREADY and not strong:
                        self.assertEqual(r["headline"]["themed"], UNCLEAR, (w, l, t))
        self.assertGreater(seen_plausible, 100)

    def test_next_step_archives_only_at_strong(self):
        for label, heavy in itertools.product(verdict.LABELS, (False, True)):
            for level in rec.CONFIDENCE_LEVELS:
                s = rec.next_step(label, level, heavy, None)
                if s["key"] == rec.STEP_ARCHIVE:
                    self.assertEqual(level, rec.STRONG, (label, level, heavy))
        self.assertEqual(rec.next_step(WAY, rec.MODERATE, False, None)["text"], "Ask again")
        self.assertEqual(rec.next_step(ALREADY, rec.MODERATE, True, None)["text"], "Ask again")

    def test_old_rows_without_a_lean_archive_only_at_strong(self):
        """Rows saved before the lean (verdicts.json) are rebuilt by from_run on the dashboard."""
        from skilluminati import stats
        for verdict_word, effect in (("Drop", "none"), ("Drop", "negative")):
            for w, l, t in ((3, 2, 7), (10, 9, 1), (1, 12, 0), (0, 20, 0), (11, 10, 0), (30, 29, 2)):
                low, high = stats.wilson(w, w + l)
                r = rec.from_run({"skill": "s", "verdict": verdict_word, "effect": effect, "wins": w, "losses": l,
                                  "ties": t, "low": low, "high": high, "model": "m", "judge": "other"})
                if r["next_step"]["text"] == ARCHIVE:
                    self.assertEqual(r["confidence"]["level"], rec.STRONG, (verdict_word, effect, w, l, t))
                if r["headline"]["label"] == ALREADY and r["confidence"]["level"] != rec.STRONG:
                    self.assertEqual(r["headline"]["themed"], UNCLEAR)


class StoredRecommendationTests(unittest.TestCase):
    """A recommendation saved before this change still says Dead weight / Consider archiving at
    Plausible. The API whitelist (recommend.clean) shows the new wording for it."""

    OLD = {"headline": {"label": ALREADY, "themed": "Dead weight", "plain": "The model does this anyway. It only costs tokens.",
                        "text": "Dead weight. The model does this anyway. It only costs tokens.", "tone": "muted"},
           "confidence": {"level": "Moderate", "text": "Plausible", "why": "x"},
           "flags": [], "next_step": {"key": "archive", "text": ARCHIVE, "n": None},
           "lean": {"verdict": "no_difference", "strength": "likely"}, "numbers": {}}

    def test_clean_rewrites_plausible_archive(self):
        out = rec.clean(self.OLD)
        self.assertEqual(out["headline"]["themed"], UNCLEAR)
        self.assertEqual(out["headline"]["label"], ALREADY)
        self.assertEqual((out["next_step"]["key"], out["next_step"]["text"]), (rec.STEP_ASK_AGAIN, "Ask again"))
        self.assertNotIn(ARCHIVE, json.dumps(out))

    def test_clean_keeps_probable_archive(self):
        strong = json.loads(json.dumps(self.OLD))
        strong["confidence"] = {"level": "Strong", "text": "Probable", "why": "x"}
        strong["lean"]["strength"] = "strong"
        out = rec.clean(strong)
        self.assertEqual((out["headline"]["themed"], out["next_step"]["text"]), ("Dead weight", ARCHIVE))

    def test_clean_rewrites_plausible_hindrance_archive(self):
        old = json.loads(json.dumps(self.OLD))
        old["headline"] = {"label": WAY, "themed": "Hindrance", "plain": "x", "text": "Hindrance. x", "tone": "destructive"}
        old["lean"] = {"verdict": "hurts", "strength": "likely"}
        out = rec.clean(old)
        self.assertEqual(out["headline"]["themed"], "Hindrance")
        self.assertEqual(out["next_step"]["text"], "Ask again")

    def test_dashboard_verdicts_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            d = home / "plugin-data" / "skilluminati"
            d.mkdir(parents=True)
            (d / "verdicts.json").write_text(json.dumps({"schema": 1, "runs": [
                {"skill": "unslop", "verdict": "Drop", "effect": "none", "user_label": ALREADY, "model": "m",
                 "judge": "j", "date": "2026-11-12", "wins": 5, "losses": 2, "ties": 5, "low": 0.3, "high": 0.9,
                 "recommendation": self.OLD}]}))
            data = api.read_verdicts(home)
        blob = json.dumps(data)
        self.assertNotIn(ARCHIVE, blob)
        self.assertIn(UNCLEAR, blob)


class ChatAndReportTests(unittest.TestCase):
    def write(self, tmp, w, l, t):
        d = Path(tmp)
        lean = verdict.lean(w, l, t)
        (d / "result.json").write_text(json.dumps({"per_skill": {"unslop": {
            "combined": {"wins": w, "losses": l, "ties": t}, "lean": lean}}}))
        return d

    def test_chat_result_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run.result_line(self.write(tmp, 5, 2, 5), "unslop"),
                             "unslop: No clear difference yet. Better 5 · Same 5 · Worse 2.")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run.result_line(self.write(tmp, 2, 1, 9), "unslop"),
                             "unslop: Dead weight (Probable). Better 2 · Same 9 · Worse 1.")

    def test_report_for_a_plausible_no_difference(self):
        res = verdict_result(label=ALREADY, wins=5, losses=2, ties=5, n_next=None)
        res["per_skill"]["unslop"]["recommendation"] = from_lean(5, 2, 5)
        f = report._verdict_facts(res, None, "unslop")
        md = report.verdict_markdown(f)
        self.assertIn("**unslop: No clear difference yet.**", md)
        self.assertIn("Next: ask again.", md)
        self.assertNotIn("Dead weight", md)
        self.assertNotIn("archiv", md.lower())

    def test_report_for_an_old_plausible_dead_weight(self):
        """result.json from before the change: stored themed word Dead weight, Moderate."""
        res = verdict_result(label=ALREADY, wins=5, losses=2, ties=5, n_next=None)
        res["per_skill"]["unslop"]["recommendation"]["confidence"] = {"level": "Moderate", "text": "Plausible", "why": "x"}
        md = report.verdict_plain(report._verdict_facts(res, None, "unslop"))
        self.assertIn("unslop: No clear difference yet.", md)
        self.assertNotIn("Dead weight", md)

    def test_report_for_a_probable_dead_weight(self):
        res = verdict_result(label=ALREADY, wins=2, losses=1, ties=9, n_next=None)
        res["per_skill"]["unslop"]["recommendation"] = from_lean(2, 1, 9)
        md = report.verdict_plain(report._verdict_facts(res, None, "unslop"))
        self.assertIn("unslop: Dead weight (Probable).", md)


class PageTests(unittest.TestCase):
    JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")

    def test_page_copy_carries_the_new_wording(self):
        page = sb_copy.page_copy()
        self.assertEqual(page["verdict_unclear"]["word"], UNCLEAR)
        self.assertIn("One run cannot tell", page["verdict_unclear"]["meaning"])
        self.assertEqual(len(page["verdict_unclear"]["summary"]), 2)

    def test_page_renders_it_for_a_weak_no_difference(self):
        self.assertIn("c.verdict_unclear", self.JS)
        self.assertIn("function unclear(r)", self.JS)
        self.assertIn("r.key === 'no_difference' && r.strength !== 'strong'", self.JS)

    def test_shown_helper(self):
        self.assertEqual(sb_copy.shown("no_difference", "likely")[0], UNCLEAR)
        self.assertEqual(sb_copy.shown("no_difference", None)[0], UNCLEAR)
        self.assertEqual(sb_copy.shown("no_difference", "strong"), ("Dead weight", sb_copy.MEANING["no_difference"], "Probable"))
        self.assertEqual(sb_copy.shown("hurts", "likely"), ("Hindrance", sb_copy.MEANING["hurts"], "Plausible"))


class WhereArchiveLivesTests(unittest.TestCase):
    def test_archive_text_lives_only_in_copy(self):
        hits = []
        for p in (ROOT / "plugin").rglob("*"):
            if p.suffix in (".py", ".js") and p.name != "copy.py":
                if ARCHIVE in p.read_text(encoding="utf-8"):
                    hits.append(p.name)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
