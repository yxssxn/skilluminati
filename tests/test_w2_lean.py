"""Tests for the four answers (helps, no difference, hurts, not enough) and their strength."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from skilluminati import copy as sb_copy, realrun, recommend, verdict  # noqa: E402


def answer(w, l, t):
    a = verdict.lean(w, l, t)
    return a["verdict"], a["strength"]


class Lean(unittest.TestCase):
    def test_fewer_than_six_tests_is_not_enough(self):
        self.assertEqual(answer(5, 0, 0), ("not_enough", None))
        self.assertEqual(answer(0, 0, 0), ("not_enough", None))

    def test_clear_win_helps(self):
        self.assertEqual(answer(8, 1, 3), ("helps", "strong"))  # the 95% check agrees
        self.assertEqual(answer(7, 3, 2), ("helps", "likely"))  # lead 4 of 12, 95% check does not

    def test_clear_loss_hurts(self):
        self.assertEqual(answer(1, 8, 3), ("hurts", "strong"))
        self.assertEqual(answer(2, 6, 1), ("hurts", "likely"))

    def test_mostly_ties_or_even_is_no_difference(self):
        self.assertEqual(answer(2, 1, 9), ("no_difference", "strong"))
        self.assertEqual(answer(5, 5, 2), ("no_difference", "likely"))  # under half tie (the author, 12 Nov)
        self.assertEqual(answer(3, 2, 2), ("no_difference", "likely"))  # under 12 tests

    def test_same_is_probable_only_when_half_the_tests_tie(self):
        self.assertEqual(answer(5, 2, 5), ("no_difference", "likely"))  # the real unslop run
        self.assertEqual(answer(3, 3, 6), ("no_difference", "strong"))

    def test_ties_dilute_a_small_lead(self):
        self.assertEqual(answer(4, 0, 20), ("no_difference", "strong"))

    def test_old_words_still_map(self):
        for word, want in [("Keep", "helps"), ("Earns its keep", "helps"), ("Unsure", "not_enough"),
                           ("Not sure yet", "not_enough"), ("Gets in the way", "hurts"), ("Cast out", "hurts"),
                           ("Model does this already", "no_difference"), ("helps", "helps")]:
            self.assertEqual(verdict.old_word_to_lean(word), want, word)
        for label in verdict.LEAN_LABEL.values():
            themed = sb_copy.VERDICTS[label][0]
            self.assertEqual(verdict.LEAN_LABEL[verdict.old_word_to_lean(themed)], label)
        self.assertIsNone(verdict.old_word_to_lean("Drop"))


class Recommendation(unittest.TestCase):
    def test_strength_sets_confidence(self):
        lean = verdict.lean(7, 3, 2)
        rec = recommend.recommend(verdict.LEAN_LABEL[lean["verdict"]], wins=7, losses=3, ties=2, low=0.4, high=0.9,
                                  lean=lean)
        self.assertEqual(rec["confidence"]["level"], recommend.MODERATE)
        self.assertEqual(rec["lean"]["verdict"], "helps")
        self.assertEqual(recommend.clean(rec)["lean"], {"verdict": "helps", "strength": "likely"})

    def test_old_stored_recommendation_gets_an_answer(self):
        old = recommend.recommend("Earns its keep", wins=9, losses=0, low=0.7, high=1.0)
        old.pop("lean")
        self.assertEqual(recommend.clean(old)["lean"], {"verdict": "helps", "strength": None})
        self.assertEqual(recommend.label_from_run({"user_label": "Cast out"}), "Gets in the way")


class JudgeRequest(unittest.TestCase):
    def test_single_message_is_just_the_message(self):
        self.assertEqual(realrun.judge_request([{"role": "system", "content": "s"}, {"role": "user", "content": "Do X"}]),
                         "Do X")

    def test_chat_shows_the_words_before_it_but_no_tool_output(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": "SECRET TOOL OUTPUT"}, {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Write it up"}]
        text = realrun.judge_request(msgs)
        self.assertTrue(text.endswith("Write it up"))
        self.assertIn("ASSISTANT: Hello", text)
        self.assertNotIn("SECRET", text)


if __name__ == "__main__":
    unittest.main()
