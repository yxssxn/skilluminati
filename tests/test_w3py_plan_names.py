"""W3PY SEC-S3: the run plan line says how many deny-list names were loaded for redaction."""

from __future__ import annotations

from tests.test_run import HomeCase  # noqa: F401  (puts plugin/ on sys.path)
from skilluminati import copy as sb_copy, paths, run  # noqa: E402

BASE = {"skill": "unslop", "source": "history", "tasks": [{}] * 12, "minutes": 9}


class PlanNamesTests(HomeCase):
    def test_zero_names_said_plainly(self):
        self.assertEqual(run.plan_line({**BASE, "names_loaded": 0}),
                         "Bringing unslop before the Owl: testing its last 12 uses, with and without. "
                         "About 9 min. 0 names loaded for redaction.")

    def test_one_and_many(self):
        self.assertTrue(run.plan_line({**BASE, "names_loaded": 1}).endswith(" 1 name loaded for redaction."))
        self.assertTrue(run.plan_line({**BASE, "source": "pack", "names_loaded": 3}).endswith(
            " 3 names loaded for redaction."))

    def test_wording_lives_in_copy(self):
        self.assertIn("{names}", sb_copy.PLAN_NAMES)

    def test_verdict_plan_counts_names_txt(self):
        self.assertEqual(run.verdict_plan(None, "nope", "pack")["names_loaded"], 0)
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        paths.names_path().write_text("Avery Quill\n# comment\nSam Lee\n", encoding="utf-8")
        self.assertEqual(run.verdict_plan(None, "nope", "pack")["names_loaded"], 2)
