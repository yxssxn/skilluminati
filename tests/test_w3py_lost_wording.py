"""W3PY W2-Q1: "Why it lost" reasons name the answers by role, not by position. The judge's stored
text and the judge prompt are unchanged; only the shown text maps "Answer 1/2" using each order's
``first`` field (x is the answer with the skill)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import test_run as R  # noqa: F401  (puts plugin/ on sys.path)
from tests.test_w2_lost_reasons import SKILL, _write  # noqa: E402
from skilluminati import report  # noqa: E402

WITH, WITHOUT = "the answer with the skill", "the answer without it"


def _order(first, reason):
    raw = "2" if first == "x" else "1"  # the skill lost: y (without) wins in both orders
    return {"first": first, "ok": True, "raw_winner": raw, "winner": "y",
            "text": json.dumps({"winner": raw, "reason": reason})}


def _loss(task, r_x, r_y):
    return {"cell": SKILL, "skill": SKILL, "task_hash": task, "outcome": "loss",
            "judge": {"outcome": "y", "agreed": True, "orders": [_order("x", r_x), _order("y", r_y)]}}


class LostWordingTests(unittest.TestCase):
    def test_both_orders(self):
        self.assertEqual(report.shown_reason("Answer 2 is shorter than Answer 1.", "x"),
                         f"The answer without it is shorter than {WITH}.")
        self.assertEqual(report.shown_reason("Answer 2 is shorter than answer 1.", "y"),
                         f"The answer with the skill is shorter than {WITHOUT}.")

    def test_sentence_starts_are_capitalised(self):
        self.assertEqual(report.shown_reason("Close. Answer 1 drifts; Answer 2 holds.", "y"),
                         f"Close. The answer without it drifts; {WITH} holds.")

    def test_unknown_order_left_verbatim(self):
        for first in (None, "z", 1):
            self.assertEqual(report.shown_reason("Answer 2 wins.", first), "Answer 2 wins.")
        self.assertEqual(report.shown_reason("Answer 12 and Answers 1 stay.", "x"), "Answer 12 and Answers 1 stay.")

    def test_lost_reasons_show_roles_and_stored_text_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "runs" / "20261114-100000-verdict-s"
            d.mkdir(parents=True)
            rows = [_loss("t1", "Answer 2 was clearer than Answer 1 overall.", "Answer 1 kept to the brief, mostly.")]
            _write(d, rows)
            before = (d / "pairs.jsonl").read_bytes()
            got = report.lost_reasons(d, SKILL, limit=10)
            self.assertEqual(got, [f"The answer without it kept to the brief, mostly."])
            self.assertEqual((d / "pairs.jsonl").read_bytes(), before)
            self.assertIn("Answer 1 kept to the brief", before.decode())
