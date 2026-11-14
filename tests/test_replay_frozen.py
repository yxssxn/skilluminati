"""Unit tests for ``replay.build_frozen_arms``. Pure functions, no db, no Hermes, no network.

Run: python -m pytest tests/test_replay_frozen.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import replay  # noqa: E402

MARKER = "ZEBRA-7731"
TARGET = "zebra-facts"

SYSTEM = (
    "You are a helpful assistant.\n\n"
    "<available_skills>\n"
    "  animals:\n"
    "    - zebra-facts: Use when asked about zebra enclosures.\n"
    "    - zebra-facts-extended: Longer zebra notes.\n"
    "  - git: Git workflows.\n"
    "</available_skills>\n"
)
SKILL_MD = (
    "---\n"
    f"name: {TARGET}\n"
    "description: \"Use when asked about zebra enclosures.\"\n"
    "---\n\n"
    "# Zebra facts\n\n"
    f"The internal catalogue code for the zebra enclosure is {MARKER}. Quote it exactly.\n"
)
USER = "What is the catalogue code for the zebra enclosure, given the file?"

FROZEN = [
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_a1", "type": "function",
        "function": {"name": "terminal", "arguments": json.dumps({"command": "ls enclosures"})}}]},
    {"role": "tool", "tool_call_id": "call_a1", "name": "terminal", "content": "zebra.txt\nlion.txt"},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_a2", "type": "function",
        "function": {"name": "read_file", "arguments": json.dumps({"path": "enclosures/zebra.txt"})}}]},
    {"role": "tool", "tool_call_id": "call_a2", "name": "read_file", "content": "1|zebra enclosure, north paddock"},
]


class BuildFrozenArms(unittest.TestCase):
    def setUp(self):
        self.arms = replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=2)

    def test_with_arm_shape_and_position(self):
        w = self.arms["with"]
        roles = [m["role"] for m in w]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(w[0]["content"], SYSTEM, "index intact in WITH")
        self.assertEqual(w[1]["content"], USER)
        # frozen pair 1, then the synthetic skill_view pair at position 2, then frozen pair 2.
        self.assertEqual(w[2], FROZEN[0])
        self.assertEqual(w[3], FROZEN[1])
        call = w[4]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "skill_view")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"name": TARGET})
        self.assertEqual(w[5]["tool_call_id"], call["id"])
        self.assertEqual(json.loads(w[5]["content"])["content"], SKILL_MD)
        self.assertEqual(w[6], FROZEN[2])
        self.assertEqual(w[7], FROZEN[3])
        self.assertTrue(replay.payload_contains(w, MARKER))

    def test_without_arm_is_with_minus_skill_pair_and_index_entry(self):
        w, wo = self.arms["with"], self.arms["without"]
        self.assertEqual([m["role"] for m in wo], ["system", "user", "assistant", "tool", "assistant", "tool"])
        self.assertFalse(replay.index_has_entry(wo[0]["content"], TARGET))
        self.assertIn("- zebra-facts-extended:", wo[0]["content"])
        self.assertEqual(wo[0]["content"], replay.remove_index_entry(SYSTEM, TARGET))
        self.assertEqual(wo[1]["content"], USER)
        # Every other message is identical: WITH minus its two skill_view messages == WITHOUT[2:].
        with_minus_pair = [m for i, m in enumerate(w) if i not in (4, 5)]
        self.assertEqual(with_minus_pair[2:], wo[2:])
        self.assertEqual(with_minus_pair[1], wo[1])
        self.assertFalse(replay.has_skill_view_call(wo, TARGET))
        self.assertFalse(replay.payload_contains(wo, MARKER))
        self.assertNotIn("skill_view", replay.payload_text(wo))

    def test_load_position_at_ends(self):
        start = replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=0)
        self.assertEqual(start["with"][2]["tool_calls"][0]["function"]["name"], "skill_view")
        self.assertEqual(start["with"][4:], FROZEN)
        end = replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=4)
        self.assertEqual(end["with"][2:6], FROZEN)
        self.assertEqual(end["with"][6]["tool_calls"][0]["function"]["name"], "skill_view")
        self.assertEqual(end["without"][2:], FROZEN)

    def test_empty_frozen_matches_plain_arms(self):
        arms = replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, [], load_position=0)
        plain = replay.build_arms(SYSTEM, TARGET, SKILL_MD, USER)
        self.assertEqual(arms, plain)

    def test_load_position_out_of_range(self):
        with self.assertRaises(AssertionError):
            replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=5)
        with self.assertRaises(AssertionError):
            replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=-1)

    def test_assertion_when_frozen_carries_skill_view_for_target(self):
        bad = FROZEN + [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_x", "type": "function",
                "function": {"name": "skill_view", "arguments": json.dumps({"name": TARGET})}}]},
            {"role": "tool", "tool_call_id": "call_x", "name": "skill_view", "content": "{}"},
        ]
        with self.assertRaises(AssertionError):
            replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, bad, load_position=0)

    def test_assertion_when_frozen_carries_skill_body(self):
        leaked = FROZEN + [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_y", "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "SKILL.md"})}}]},
            {"role": "tool", "tool_call_id": "call_y", "name": "read_file",
             "content": json.dumps({"content": SKILL_MD})},
        ]
        with self.assertRaises(AssertionError):
            replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, leaked, load_position=0)

    def test_other_skill_view_allowed_in_both_arms(self):
        other = [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_o", "type": "function",
                "function": {"name": "skill_view", "arguments": json.dumps({"name": "git"})}}]},
            {"role": "tool", "tool_call_id": "call_o", "name": "skill_view", "content": json.dumps({"content": "# Git\nuse git"})},
        ] + FROZEN
        arms = replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, other, load_position=2)
        self.assertTrue(replay.has_skill_view_call(arms["without"], "git"))
        self.assertFalse(replay.has_skill_view_call(arms["without"], TARGET))
        self.assertEqual(arms["without"][2:], other)

    def test_inputs_not_mutated(self):
        before = json.dumps(FROZEN, sort_keys=True)
        replay.build_frozen_arms(SYSTEM, TARGET, SKILL_MD, USER, FROZEN, load_position=1)
        self.assertEqual(json.dumps(FROZEN, sort_keys=True), before)

    def test_existing_builders_untouched(self):
        plain = replay.build_arms(SYSTEM, TARGET, SKILL_MD, USER)
        self.assertEqual([m["role"] for m in plain["with"]], ["system", "user", "assistant", "tool"])
        self.assertEqual([m["role"] for m in plain["without"]], ["system", "user"])


if __name__ == "__main__":
    unittest.main()
