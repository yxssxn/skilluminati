"""W3PY CODE: report.py and recommend._honest share one helper for the shown step and the unclear
headline. The old inline report logic is copied here and compared over every label, level and step."""

from __future__ import annotations

import itertools

from tests import test_run as R  # noqa: F401  (puts plugin/ on sys.path)
from skilluminati import copy as sb_copy, recommend as rec, verdict as sb_verdict  # noqa: E402

LABELS = [sb_verdict.EARNS_ITS_KEEP, sb_verdict.GETS_IN_THE_WAY, sb_verdict.MODEL_DOES_THIS_ALREADY,
          sb_verdict.NOT_SURE_YET, "Drop", "Keep", None]
LEVELS = ["strong", "Strong", "moderate", "Moderate", "weak", "", None]
STEPS = [{"key": k, "text": "t", "n": None} for k in ("keep", "trim", "archive", "run_more", "ask_again")] + [{}]


def old_report(label, level_raw, ns):
    level = (level_raw or "").lower()
    unclear = label == "Model does this already" and level != "strong"
    if ns.get("key") == "archive" and level != "strong":
        ns = {"key": "ask_again", "text": sb_copy.NEXT_ASK_AGAIN, "n": None}
    return unclear, rec.shown_step(label, level, ns)


class HonestHelperTests(R.unittest.TestCase):
    def test_report_logic_is_unchanged(self):
        for label, level, step in itertools.product(LABELS, LEVELS, STEPS):
            lv = (level or "").lower()
            got = (rec.unclear_shown(label, lv), rec.honest_step(label, lv, step))
            self.assertEqual(got, old_report(label, level, step), (label, level, step))

    def test_honest_matches_old_rule_for_its_own_levels(self):
        for label, level, step in itertools.product(LABELS, [rec.STRONG, rec.MODERATE, rec.WEAK], STEPS):
            ns = dict(step)
            if ns.get("key") == rec.STEP_ARCHIVE and level != rec.STRONG:
                ns = {"key": rec.STEP_ASK_AGAIN, "text": sb_copy.NEXT_ASK_AGAIN, "n": None}
            self.assertEqual(rec.honest_step(label, level, step), rec.shown_step(label, level, ns))

    def test_report_imports_recommend_once_in_verdict_facts(self):
        import inspect
        from skilluminati import report
        src = inspect.getsource(report._verdict_facts)
        self.assertEqual(src.count("from . import recommend"), 1)
        self.assertNotIn('"Model does this already"', src)
