"""In plain words: Hermes hides plugin tools behind its tool search by default and shows the agent
only each tool's first sentence (up to 80 characters). These checks make sure that sentence says
what the tool is for, and that the words a user would ask with ("worth keeping", "helps",
"cost") are in the text the search reads.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import re
import unittest

from tests import test_run as R  # noqa: F401,E402  (puts plugin/ on sys.path)

from skilluminati import schemas  # noqa: E402

# Hermes's catalog line: first sentence, capped at 80 characters (tools/tool_search_catalog.py).
_SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)[.!?](?=\s|$)")
CAP = 80


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = _SENTENCE_END.search(text)
    return text[: m.end()] if m else text


def search_words(schema: dict) -> set[str]:
    blob = " ".join([schema["name"].replace("_", " "), schema["description"],
                     " ".join(schema["parameters"]["properties"])])
    return set(re.findall(r"[a-z0-9]+", blob.lower()))


class FirstSentenceFitsTheListing(unittest.TestCase):
    def test_result_line_names_the_job(self) -> None:
        line = first_sentence(schemas.RESULT["description"])
        self.assertLessEqual(len(line), CAP, line)
        for word in ("verdict", "skill", "helps"):
            self.assertIn(word, line.lower())

    def test_estimate_line_names_the_job(self) -> None:
        line = first_sentence(schemas.ESTIMATE["description"])
        self.assertLessEqual(len(line), CAP, line)
        for word in ("token", "cost", "skill"):
            self.assertIn(word, line.lower())


class SearchFindsThemByTheUsersWords(unittest.TestCase):
    def test_result_carries_question_words(self) -> None:
        words = search_words(schemas.RESULT)
        for w in ("worth", "keeping", "weight", "helps", "hurts", "verdict", "test", "skill"):
            self.assertIn(w, words)

    def test_estimate_carries_cost_words(self) -> None:
        words = search_words(schemas.ESTIMATE)
        for w in ("expensive", "cost", "tokens", "loaded", "trim", "skills"):
            self.assertIn(w, words)

    def test_each_points_to_the_other(self) -> None:
        self.assertIn("skilluminati_result", schemas.ESTIMATE["description"])
        self.assertIn("skilluminati_estimate", schemas.RESULT["description"])

    def test_show_as_is_rule_kept(self) -> None:
        d = schemas.RESULT["description"]
        self.assertIn("exactly as returned", d)
        self.assertIn("Do not add a verdict", d)


if __name__ == "__main__":
    unittest.main()
