"""Unit tests for the replay message builder. Pure functions, no Hermes, no network.

Run: python -m pytest tests -v
(pytest also collects these when installed.)
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
    "## Skills\n"
    "Before replying, scan the skills below.\n\n"
    "<available_skills>\n"
    "  animals:\n"
    "    - zebra-facts: Use when asked about zebra enclosures. Catalogue codes and feeding.\n"
    "    - zebra-facts-extended: Longer zebra notes.\n"
    "  - git: Git workflows.\n"
    "  tools [names only]: github, zebra-facts, ffmpeg\n"
    "</available_skills>\n\n"
    "Only proceed without loading a skill if genuinely none are relevant to the task."
)

SKILL_MD = (
    "---\n"
    f"name: {TARGET}\n"
    "description: \"Use when asked about zebra enclosures. Catalogue codes and feeding.\"\n"
    "tags: [zoo, zebra]\n"
    "---\n\n"
    "# Zebra facts\n\n"
    f"The internal catalogue code for the zebra enclosure is {MARKER}. Quote it exactly.\n"
)

USER = "What is the internal catalogue code for the zebra enclosure?"


class RemoveIndexEntry(unittest.TestCase):
    def test_row_removed_and_neighbours_kept(self):
        out = replay.remove_index_entry(SYSTEM, TARGET)
        self.assertNotIn("- zebra-facts:", out)
        self.assertIn("- zebra-facts-extended:", out)
        self.assertIn("- git:", out)
        self.assertFalse(replay.index_has_entry(out, TARGET))
        self.assertTrue(replay.index_has_entry(SYSTEM, TARGET))

    def test_names_only_line_edited(self):
        out = replay.remove_index_entry(SYSTEM, TARGET)
        self.assertIn("tools [names only]: github, ffmpeg", out)

    def test_names_only_line_dropped_when_empty(self):
        out = replay.remove_index_entry("  solo [names only]: zebra-facts\n- git: Git", TARGET)
        self.assertEqual(out, "- git: Git")

    def test_exact_name_match_only(self):
        out = replay.remove_index_entry(SYSTEM, "git")
        self.assertNotIn("- git:", out)
        self.assertIn("github", out)
        self.assertIn("- zebra-facts:", out)

    def test_unknown_skill_is_noop(self):
        self.assertEqual(replay.remove_index_entry(SYSTEM, "nope"), SYSTEM)


class SkillViewResult(unittest.TestCase):
    def test_shape_matches_hermes_skill_view(self):
        res = replay.skill_view_result(TARGET, SKILL_MD)
        expected_keys = [
            "success", "name", "description", "tags", "related_skills", "content", "path",
            "skill_dir", "linked_files", "usage_hint", "required_environment_variables",
            "required_commands", "missing_required_environment_variables",
            "missing_credential_files", "missing_required_commands", "setup_needed",
            "setup_skipped", "readiness_status",
        ]
        self.assertEqual(list(res.keys()), expected_keys)
        self.assertTrue(res["success"])
        self.assertEqual(res["name"], TARGET)
        self.assertEqual(res["description"], "Use when asked about zebra enclosures. Catalogue codes and feeding.")
        self.assertEqual(res["tags"], ["zoo", "zebra"])
        self.assertEqual(res["content"], SKILL_MD)
        self.assertEqual(res["readiness_status"], "available")
        self.assertIsNone(res["linked_files"])

    def test_json_wire_form_round_trips(self):
        wire = replay.skill_view_result_json(TARGET, SKILL_MD)
        self.assertEqual(json.loads(wire)["content"], SKILL_MD)
        self.assertIn(MARKER, wire)


class Arms(unittest.TestCase):
    def setUp(self):
        self.arms = replay.build_arms(SYSTEM, TARGET, SKILL_MD, USER)

    def test_with_arm_has_skill_view_injection(self):
        with_arm = self.arms["with"]
        self.assertEqual([m["role"] for m in with_arm], ["system", "user", "assistant", "tool"])
        self.assertEqual(with_arm[0]["content"], SYSTEM)  # index intact
        self.assertEqual(with_arm[1]["content"], USER)
        call = with_arm[2]["tool_calls"][0]
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "skill_view")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"name": TARGET})
        self.assertEqual(with_arm[3]["tool_call_id"], call["id"])
        self.assertEqual(with_arm[3]["name"], "skill_view")
        self.assertEqual(json.loads(with_arm[3]["content"])["content"], SKILL_MD)
        self.assertTrue(replay.payload_contains(with_arm, MARKER))

    def test_without_arm_index_entry_removed_and_no_tool_messages(self):
        without = self.arms["without"]
        self.assertEqual([m["role"] for m in without], ["system", "user"])
        self.assertFalse(replay.index_has_entry(without[0]["content"], TARGET))
        self.assertIn("- zebra-facts-extended:", without[0]["content"])
        self.assertEqual(without[1]["content"], USER)
        self.assertFalse(any("tool_calls" in m for m in without))

    def test_marker_absent_from_without_payload(self):
        self.assertFalse(replay.payload_contains(self.arms["without"], MARKER))
        self.assertNotIn(MARKER, replay.payload_text(self.arms["without"]))

    def test_custom_tool_call_id(self):
        arm = replay.build_with_arm(SYSTEM, TARGET, SKILL_MD, USER, tool_call_id="toolu_x1")
        self.assertEqual(arm[2]["tool_calls"][0]["id"], "toolu_x1")
        self.assertEqual(arm[3]["tool_call_id"], "toolu_x1")


class Frontmatter(unittest.TestCase):
    def test_parse(self):
        fm, body = replay.parse_frontmatter(SKILL_MD)
        self.assertEqual(fm["name"], TARGET)
        self.assertEqual(fm["tags"], ["zoo", "zebra"])
        self.assertTrue(body.lstrip().startswith("# Zebra facts"))

    def test_no_frontmatter(self):
        fm, body = replay.parse_frontmatter("# plain\n")
        self.assertEqual(fm, {})
        self.assertEqual(body, "# plain\n")


if __name__ == "__main__":
    unittest.main()
