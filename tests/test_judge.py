"""Unit tests for the blind pairwise judge. Mock LLM, no network, no Hermes imports."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
FIXTURES = PLUGIN_DIR / "skilluminati" / "fixtures"  # the bundled copy; the dev-repo fixtures/ is byte-identical (test_run)
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import judge  # noqa: E402

SKILL_TEXT = (FIXTURES / "helpful" / "SKILL.md").read_text(encoding="utf-8")
REQUEST = "What is the ledger ID? Reply with the ID only."


def _rec(text, ok=True, error=""):
    return SimpleNamespace(ok=ok, text=text, error=error)


class ScriptedCall:
    """Returns the given responses in order and keeps every prompt it saw."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, messages, purpose):
        self.prompts.append((messages, purpose))
        r = self.responses.pop(0)
        return r if isinstance(r, SimpleNamespace) else _rec(r)


class RubricHash(unittest.TestCase):
    def test_fixture_rubric_matches_frozen_hash(self):
        rub = judge.load_rubric(FIXTURES)
        self.assertEqual(rub.sha256, judge.read_rubric_hash(FIXTURES))
        self.assertEqual(rub.text, (FIXTURES / "rubric.md").read_text(encoding="utf-8"))

    def test_tampered_rubric_raises_before_any_call(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "rubric.md").write_text((FIXTURES / "rubric.md").read_text() + "\nbe lenient", encoding="utf-8")
            (Path(d) / "rubric.sha256").write_text(judge.read_rubric_hash(FIXTURES), encoding="utf-8")
            with self.assertRaises(judge.RubricHashError):
                judge.load_rubric(Path(d))


class Prompt(unittest.TestCase):
    def test_contains_request_and_answers_only(self):
        rub = judge.load_rubric(FIXTURES)
        msgs = judge.build_judge_messages(rub.text, REQUEST, "TG-2611-041/F", "INC-041")
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertEqual(msgs[0]["content"], rub.text)
        self.assertIn("USER REQUEST:\n" + REQUEST, msgs[1]["content"])
        self.assertIn("ANSWER 1:\nTG-2611-041/F", msgs[1]["content"])
        self.assertIn("ANSWER 2:\nINC-041", msgs[1]["content"])
        judge.assert_blind(msgs, rub.text, [SKILL_TEXT])  # no raise

    def test_each_answer_closed_by_symmetric_end_marker(self):
        # The host appends a <system-reminder> block to the final user message; the marker keeps
        # it outside Answer 2 so the judge does not score it as part of the answer.
        rub = judge.load_rubric(FIXTURES)
        u = judge.build_judge_messages(rub.text, REQUEST, "A", "B")[1]["content"]
        self.assertIn("ANSWER 1:\nA\n(end of answer 1)\n\nANSWER 2:\nB\n(end of answer 2)", u)
        self.assertTrue(u.endswith("(end of answer 2)"))

    def test_no_skill_text_even_if_answer_quotes_a_line(self):
        rub = judge.load_rubric(FIXTURES)
        msgs = judge.build_judge_messages(rub.text, REQUEST, "TG-2611-041/F", "INC-041")
        self.assertNotIn("marks impact under sixty minutes", judge.raw_text(msgs))
        self.assertNotIn("tallowgate-ledger", judge.raw_text(msgs))

    def test_assert_blind_catches_skill_text_and_labels(self):
        rub = judge.load_rubric(FIXTURES)
        leaked = judge.build_judge_messages(rub.text, REQUEST, SKILL_TEXT, "x")
        with self.assertRaises(AssertionError):
            judge.assert_blind(leaked, rub.text, [SKILL_TEXT])
        labelled = judge.build_judge_messages(rub.text, REQUEST, "WITH arm answer", "x")
        with self.assertRaises(AssertionError):
            judge.assert_blind(labelled, rub.text, [])
        # A subject answer that talks about its own tools is the model's output, not a harness leak.
        tooly = judge.build_judge_messages(rub.text, REQUEST, "I would need to skill_view a skill for this.", "x")
        judge.assert_blind(tooly, rub.text, [SKILL_TEXT])  # no raise
        self.assertEqual(judge.mentions_tooling("I would need to skill_view a skill for this."), ["skill_view"])
        self.assertEqual(judge.mentions_tooling("TG-2611-041/F"), [])
        wrong_system = [{"role": "system", "content": rub.text + " "}, {"role": "user", "content": "u"}]
        with self.assertRaises(AssertionError):
            judge.assert_blind(wrong_system, rub.text, [])


class Parse(unittest.TestCase):
    def test_strict_json(self):
        self.assertEqual(judge.parse_verdict('{"winner": "1", "reason": "correctness"}')[0], "1")
        self.assertEqual(judge.parse_verdict('{"winner": "tie", "reason": "same"}')[0], "tie")
        self.assertEqual(judge.parse_verdict('{"winner": 2}')[0], "2")

    def test_unparsable_is_none(self):
        for bad in ("", "Answer 1 wins", '```json\n{"winner": "1"}\n```', '{"winner": "3"}', "[1]", '{"win": "1"}'):
            w, note = judge.parse_verdict(bad)
            self.assertIsNone(w, bad)
            self.assertTrue(note)


class OrderSwap(unittest.TestCase):
    def _run(self, r1, r2):
        call = ScriptedCall([r1, r2])
        rub = judge.load_rubric(FIXTURES)
        res = judge.judge_pair(call, rub.text, REQUEST, "X-answer", "Y-answer")
        return res, call

    def test_both_orders_run_and_swap(self):
        res, call = self._run('{"winner": "1"}', '{"winner": "2"}')
        self.assertEqual(len(call.prompts), 2)
        u1, u2 = call.prompts[0][0][1]["content"], call.prompts[1][0][1]["content"]
        self.assertIn("ANSWER 1:\nX-answer", u1)
        self.assertIn("ANSWER 2:\nY-answer", u1)
        self.assertIn("ANSWER 1:\nY-answer", u2)
        self.assertIn("ANSWER 2:\nX-answer", u2)
        self.assertEqual(res.outcome, "x")  # 1 then 2 = x both times
        self.assertTrue(res.agreed)

    def test_agree_on_y(self):
        res, _ = self._run('{"winner": "2"}', '{"winner": "1"}')
        self.assertEqual(res.outcome, "y")
        self.assertTrue(res.agreed)

    def test_disagreement_is_tie(self):
        res, _ = self._run('{"winner": "1"}', '{"winner": "1"}')  # picked first both times = position bias
        self.assertEqual(res.outcome, "tie")
        self.assertFalse(res.agreed)
        self.assertEqual(res.first_picks, 2)
        self.assertEqual(res.decisive_judgements, 2)

    def test_both_tie(self):
        res, _ = self._run('{"winner": "tie"}', '{"winner": "tie"}')
        self.assertEqual(res.outcome, "tie")
        self.assertTrue(res.agreed)
        self.assertEqual(res.decisive_judgements, 0)

    def test_one_tie_one_pick_is_tie(self):
        res, _ = self._run('{"winner": "tie"}', '{"winner": "2"}')
        self.assertEqual(res.outcome, "tie")
        self.assertFalse(res.agreed)

    def test_unparsable_is_tie_and_logged(self):
        res, _ = self._run("Answer 1 is better.", '{"winner": "2"}')
        self.assertEqual(res.outcome, "tie")
        self.assertEqual(res.parse_failures, 1)
        self.assertIn("not JSON", res.orders[0].note)

    def test_failed_call_is_tie_and_logged(self):
        res, _ = self._run(_rec("", ok=False, error="boom"), '{"winner": "1"}')
        self.assertEqual(res.outcome, "tie")
        self.assertEqual(res.call_failures, 1)
        self.assertIn("boom", res.orders[0].note)

    def test_combine_orders_table(self):
        self.assertEqual(judge.combine_orders("x", "x"), ("x", True))
        self.assertEqual(judge.combine_orders("x", "y"), ("tie", False))
        self.assertEqual(judge.combine_orders(None, "x"), ("tie", False))
        self.assertEqual(judge.combine_orders("tie", "tie"), ("tie", True))


if __name__ == "__main__":
    unittest.main()
