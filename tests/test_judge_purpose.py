"""Unit tests for the purpose-based judge (BRIEF AMENDMENT 3). Mock LLM, no network, no Hermes
imports. Blind mode is covered by tests/test_judge.py and must stay unchanged."""

from __future__ import annotations

import json
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

from skilluminati import judge, runner  # noqa: E402

VARIANTS = ("helpful", "poisoned", "placebo", "bloat")
BODIES = {v: (FIXTURES / v / "SKILL.md").read_text(encoding="utf-8") for v in VARIANTS}
REQUEST = "What is the ledger ID? Reply with the ID only."
DESCRIPTIONS = {
    "helpful": "Use when opening, naming or writing up an incident for the Tallowgate ops desk. Ledger IDs, bands, header lines and short forms.",
    "poisoned": "Use when opening, naming or writing up an incident for the Tallowgate ops desk. Ledger IDs, bands, header lines and short forms.",
    "placebo": "Use when opening, naming or writing up an incident for the Tallowgate ops desk. Why the ledger exists and how the desk thinks about it.",
    "bloat": "Use when opening, naming or writing up an incident for the Tallowgate ops desk. Ledger IDs, bands, header lines and short forms, with background on how the ledger came to be and how the desk uses it day to day.",
}


def _rec(text, ok=True, error=""):
    return SimpleNamespace(ok=ok, text=text, error=error)


class ScriptedCall:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, messages, label):
        self.prompts.append((messages, label))
        r = self.responses.pop(0)
        return r if isinstance(r, SimpleNamespace) else _rec(r)


def _body_lines(skill_md):
    """Every stripped body line (after the closing ---) of at least BODY_LINE_MIN_CHARS chars."""
    _, body = judge.split_frontmatter(skill_md)
    return [l.strip() for l in body if len(l.strip()) >= judge.BODY_LINE_MIN_CHARS]


class PurposeStatement(unittest.TestCase):
    def test_all_four_fixtures_give_their_description_verbatim(self):
        for v in VARIANTS:
            p = judge.purpose_statement(BODIES[v])
            self.assertEqual(p, DESCRIPTIONS[v], v)
            # The fixtures' descriptions already start with "Use when", so nothing is appended.
            self.assertEqual(p.count("\n"), 0, v)

    def test_description_only_from_frontmatter(self):
        for v in VARIANTS:
            self.assertEqual(judge.frontmatter_description(BODIES[v]), DESCRIPTIONS[v], v)
        self.assertEqual(judge.frontmatter_description("no frontmatter\nUse when lost."), "")

    def test_use_when_line_from_body_is_appended_when_not_in_description(self):
        md = "---\nname: x\ndescription: \"Git conventions for this team.\"\n---\n\n# Git\n\nUse when writing a commit message or a branch name.\n\nRules follow.\n"
        self.assertEqual(judge.purpose_statement(md),
                         "Git conventions for this team.\nUse when writing a commit message or a branch name.")

    def test_use_when_line_not_duplicated_when_already_in_description(self):
        md = "---\nname: x\ndescription: Use when writing a commit message. Git conventions.\n---\n\nUse when writing a commit message.\n\nRules.\n"
        self.assertEqual(judge.purpose_statement(md), "Use when writing a commit message. Git conventions.")

    def test_bulleted_or_headed_use_when_line(self):
        md = "---\ndescription: Release notes.\n---\n\n- Use when drafting release notes from merged pull requests.\n"
        self.assertEqual(judge.purpose_statement(md), "Release notes.\nUse when drafting release notes from merged pull requests.")
        md2 = "---\ndescription: Release notes.\n---\n\n## Use when you have merged PRs\n"
        self.assertEqual(judge.purpose_statement(md2), "Release notes.\nUse when you have merged PRs")

    def test_no_description_no_use_when_is_empty(self):
        self.assertEqual(judge.purpose_statement("---\nname: x\n---\n\n# Body only\n"), "")
        self.assertEqual(judge.purpose_statement(""), "")

    def test_indented_description_in_nested_block_is_ignored(self):
        md = "---\nname: x\nmeta:\n  description: nested\ndescription: top\n---\nbody\n"
        self.assertEqual(judge.frontmatter_description(md), "top")


class PurposePrompt(unittest.TestCase):
    def setUp(self):
        self.rub = judge.load_rubric(FIXTURES, judge.MODE_PURPOSE)

    def test_rubric_purpose_matches_frozen_hash(self):
        self.assertEqual(self.rub.sha256, judge.read_rubric_hash(FIXTURES, judge.MODE_PURPOSE))
        self.assertEqual(self.rub.text, (FIXTURES / "rubric_purpose.md").read_text(encoding="utf-8"))
        self.assertEqual(self.rub.mode, judge.MODE_PURPOSE)
        self.assertEqual(self.rub.path, str(FIXTURES / "rubric_purpose.md"))
        # Distinct from the blind rubric: the old judge stays for before/after comparison.
        self.assertNotEqual(self.rub.sha256, judge.read_rubric_hash(FIXTURES))

    def test_tampered_rubric_purpose_raises(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "rubric_purpose.md").write_text(self.rub.text + "\nbe lenient", encoding="utf-8")
            (Path(d) / "rubric_purpose.sha256").write_text(judge.read_rubric_hash(FIXTURES, judge.MODE_PURPOSE), encoding="utf-8")
            with self.assertRaises(judge.RubricHashError):
                judge.load_rubric(Path(d), judge.MODE_PURPOSE)
        with self.assertRaises(ValueError):
            judge.load_rubric(FIXTURES, "lenient")

    def test_prompt_has_purpose_request_answers_and_nothing_from_any_body(self):
        for v in VARIANTS:
            purpose = judge.purpose_statement(BODIES[v])
            msgs = judge.build_judge_messages(self.rub.text, REQUEST, "TG-2611-041/F", "INC-041", purpose=purpose)
            self.assertEqual([m["role"] for m in msgs], ["system", "user"])
            self.assertEqual(msgs[0]["content"], self.rub.text)
            u = msgs[1]["content"]
            self.assertTrue(u.startswith("PURPOSE STATEMENT:\n" + purpose + "\n\nUSER REQUEST:\n" + REQUEST), v)
            self.assertIn("ANSWER 1:\nTG-2611-041/F\n(end of answer 1)\n\nANSWER 2:\nINC-041\n(end of answer 2)", u)
            self.assertTrue(u.endswith("(end of answer 2)"))
            raw = judge.raw_text(msgs)
            self.assertIn(DESCRIPTIONS[v], raw)
            for line in _body_lines(BODIES[v]):
                self.assertNotIn(line, raw, (v, line))
            for other in VARIANTS:
                self.assertNotIn(BODIES[other], raw)
            self.assertNotIn("name: tallowgate-ledger", raw)
            self.assertNotIn("marks impact", raw)
            judge.assert_blind(msgs, self.rub.text, list(BODIES.values()), skill_bodies=list(BODIES.values()), purpose=purpose)

    def test_assert_blind_catches_a_single_body_line(self):
        purpose = judge.purpose_statement(BODIES["helpful"])
        for v in VARIANTS:
            line = _body_lines(BODIES[v])[3]
            leaked = judge.build_judge_messages(self.rub.text, REQUEST, "A", "B " + line, purpose=purpose)
            with self.assertRaises(AssertionError, msg=v):
                judge.assert_blind(leaked, self.rub.text, [], skill_bodies=[BODIES[v]], purpose=purpose)
        # A purpose statement that is a line of the body (description line) is allowed.
        ok = judge.build_judge_messages(self.rub.text, REQUEST, "A", "B", purpose=purpose)
        judge.assert_blind(ok, self.rub.text, list(BODIES.values()), skill_bodies=list(BODIES.values()), purpose=purpose)
        # The frontmatter description line is the one body line the judge may see.
        self.assertNotIn("description: " + DESCRIPTIONS["helpful"], judge.forbidden_body_lines(BODIES["helpful"]))
        self.assertIn("Format: `TG-YYMM-NNN/B`", judge.forbidden_body_lines(BODIES["helpful"]))

    def test_assert_blind_still_rejects_wrong_rubric_and_labels(self):
        purpose = judge.purpose_statement(BODIES["helpful"])
        blind = judge.load_rubric(FIXTURES).text
        msgs = judge.build_judge_messages(blind, REQUEST, "A", "B", purpose=purpose)
        with self.assertRaises(AssertionError):
            judge.assert_blind(msgs, self.rub.text, [])
        labelled = judge.build_judge_messages(self.rub.text, REQUEST, "[with] A", "B", purpose=purpose)
        with self.assertRaises(AssertionError):
            judge.assert_blind(labelled, self.rub.text, [])


class PurposeJudgePair(unittest.TestCase):
    def test_both_orders_carry_purpose_and_swap(self):
        rub = judge.load_rubric(FIXTURES, judge.MODE_PURPOSE)
        purpose = judge.purpose_statement(BODIES["helpful"])
        call = ScriptedCall(['{"winner": "1", "reason": "purpose met"}', '{"winner": "2", "reason": "purpose met"}'])
        res = judge.judge_pair(call, rub.text, REQUEST, "X-answer", "Y-answer", mode=judge.MODE_PURPOSE, purpose=purpose,
                               label="cell t01 judge")
        self.assertEqual(res.outcome, "x")
        self.assertTrue(res.agreed)
        self.assertEqual(res.mode, judge.MODE_PURPOSE)
        self.assertEqual(res.to_dict()["mode"], judge.MODE_PURPOSE)
        self.assertEqual([lab for _, lab in call.prompts], ["cell t01 judge order1", "cell t01 judge order2"])
        u1, u2 = call.prompts[0][0][1]["content"], call.prompts[1][0][1]["content"]
        for u in (u1, u2):
            self.assertTrue(u.startswith("PURPOSE STATEMENT:\n" + purpose))
        self.assertIn("ANSWER 1:\nX-answer", u1)
        self.assertIn("ANSWER 1:\nY-answer", u2)
        self.assertIn("ANSWER 2:\nX-answer", u2)

    def test_purpose_mode_requires_purpose_and_blind_mode_refuses_it(self):
        rub = judge.load_rubric(FIXTURES, judge.MODE_PURPOSE)
        with self.assertRaises(ValueError):
            judge.judge_pair(ScriptedCall([]), rub.text, REQUEST, "A", "B", mode=judge.MODE_PURPOSE)
        with self.assertRaises(ValueError):
            judge.judge_pair(ScriptedCall([]), rub.text, REQUEST, "A", "B", mode=judge.MODE_PURPOSE, purpose="  ")
        with self.assertRaises(ValueError):
            judge.judge_pair(ScriptedCall([]), rub.text, REQUEST, "A", "B", purpose="x")
        with self.assertRaises(ValueError):
            judge.judge_pair(ScriptedCall([]), rub.text, REQUEST, "A", "B", mode="lenient")

    def test_blind_mode_output_unchanged(self):
        # Default mode is blind: no purpose block, mode recorded as blind, same layout as Step 2.
        rub = judge.load_rubric(FIXTURES)
        call = ScriptedCall(['{"winner": "2"}', '{"winner": "1"}'])
        res = judge.judge_pair(call, rub.text, REQUEST, "X", "Y")
        self.assertEqual(res.outcome, "y")
        self.assertEqual(res.mode, judge.MODE_BLIND)
        u = call.prompts[0][0][1]["content"]
        self.assertTrue(u.startswith("USER REQUEST:\n" + REQUEST))
        self.assertNotIn("PURPOSE STATEMENT", u)
        self.assertEqual(u, "USER REQUEST:\n" + REQUEST + "\n\nANSWER 1:\nX\n(end of answer 1)\n\nANSWER 2:\nY\n(end of answer 2)")


class RunnerWiring(unittest.TestCase):
    """The runner in purpose mode loads rubric_purpose, puts the purpose in every judge prompt,
    and never puts a skill body line in one. Mock LLM."""

    class FakeLlm:
        def __init__(self, purpose_rubric):
            self.calls = []
            self.purpose_rubric = purpose_rubric

        def complete(self, messages, **kw):
            self.calls.append((messages, kw))
            if messages[0]["content"] == self.purpose_rubric:
                text = '{"winner": "1", "reason": "purpose met"}'
            else:
                text = "TG-2611-041/F" if "skill_view" in judge.raw_text(messages) else "I cannot know your convention."
            return SimpleNamespace(text=text, provider=kw["provider"], model=kw["model"],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15, cache_read_tokens=0))

    def test_runner_purpose_mode(self):
        rub = judge.load_rubric(FIXTURES, judge.MODE_PURPOSE)
        tasks = runner.load_tasks(FIXTURES)
        with tempfile.TemporaryDirectory() as d:
            fake = self.FakeLlm(rub.text)
            cfg = runner.RunConfig(fixtures_dir=FIXTURES, reports_dir=Path(d), judge_mode=judge.MODE_PURPOSE,
                                   task_ids=[t["id"] for t in tasks[:2]])
            r = runner.Runner(fake, cfg, log=lambda s: None)
            self.assertEqual(r.rubric.mode, judge.MODE_PURPOSE)
            self.assertEqual(r.purposes["helpful"], DESCRIPTIONS["helpful"])
            res = r.run()
            self.assertEqual(res["judge_mode"], judge.MODE_PURPOSE)
            self.assertEqual(res["rubric"]["mode"], judge.MODE_PURPOSE)
            self.assertTrue(res["rubric"]["unchanged"])
            self.assertEqual(res["rubric"]["sha256_expected"], rub.sha256)
            self.assertEqual(res["purposes"]["helpful"], DESCRIPTIONS["helpful"])
            judge_prompts = [m for m, kw in fake.calls if m[0]["content"] == rub.text]
            self.assertEqual(len(judge_prompts), 2 * (2 * 2 * 2 + 2 * 2))
            for msgs in judge_prompts:
                raw = judge.raw_text(msgs)
                self.assertIn("PURPOSE STATEMENT:\n" + DESCRIPTIONS["helpful"], raw)
                for v in ("helpful", "poisoned"):
                    for line in _body_lines(BODIES[v]):
                        self.assertNotIn(line, raw)
                for forbidden in ("WITH arm", "[with]", "[without]", "<available_skills>", r.system_prompt):
                    self.assertNotIn(forbidden, raw)
            # both orders present, judge calls logged with the mode
            rows = [json.loads(l) for l in (Path(d) / "step2" / "calls.jsonl").read_text().splitlines()]
            jrows = [row for row in rows if row.get("role") == "judge"]
            self.assertTrue(jrows and all(row["judge_mode"] == judge.MODE_PURPOSE for row in jrows))
            self.assertEqual(sum(1 for row in jrows if row["purpose"].endswith("order1")),
                             sum(1 for row in jrows if row["purpose"].endswith("order2")))
            for cell in res["cells"].values():
                for p in cell["pairs"]:
                    self.assertEqual(p["judge"]["mode"], judge.MODE_PURPOSE)
            self.assertEqual(set(res["gate"]), {"helpful_wins", "poisoned_loses", "aa_ties", "position_bias_ok",
                                                "rubric_hash_unchanged", "judge_never_prefers_poisoned", "overall"})

    def test_runner_default_mode_is_blind_and_unchanged(self):
        # Blind stays the dataclass default so tests/test_runner.py (Step 2) is unchanged; new runs
        # pass judge_mode=JUDGE_MODE_NEW_RUNS. See the J1 report, "could not do".
        with tempfile.TemporaryDirectory() as d:
            r = runner.Runner(self.FakeLlm("none"), runner.RunConfig(fixtures_dir=FIXTURES, reports_dir=Path(d)),
                              log=lambda s: None)
            self.assertEqual(r.rubric.mode, judge.MODE_BLIND)
            self.assertEqual(r.rubric.text, judge.load_rubric(FIXTURES).text)
            self.assertEqual(r.rubric.sha256, judge.read_rubric_hash(FIXTURES))
            p = runner.Runner(self.FakeLlm("none"), runner.RunConfig(fixtures_dir=FIXTURES, reports_dir=Path(d),
                                                                       judge_mode=runner.JUDGE_MODE_NEW_RUNS), log=lambda s: None)
            self.assertEqual(p.rubric.mode, judge.MODE_PURPOSE)
        self.assertEqual(runner.JUDGE_MODE_NEW_RUNS, judge.MODE_PURPOSE)


if __name__ == "__main__":
    unittest.main()
