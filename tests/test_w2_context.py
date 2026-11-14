"""Tests for context mode: a skill opened mid-chat becomes a test of the chat as it stood then.
Synthetic database only; every name and value here is invented."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_mine_frozen import MESSAGES_DDL, PROMPTS_DDL, SESSIONS_DDL, _Builder  # noqa: F401
from skilluminati import mine, replay

SKILL_MD = "---\nname: helpful\ndescription: Use when checking disks.\n---\n\n# Helpful\n\nReport disk use as a percentage, always.\n"
LOADED = json.dumps({"success": True, "content": SKILL_MD})


def _no_redact(text):
    return text, {}


class ContextMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "skills" / "helpful").mkdir(parents=True)
        (root / "skills" / "helpful" / "SKILL.md").write_text(SKILL_MD)
        self.skills = root / "skills"
        self.db = root / "state.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")
        # A chat: two earlier turns (one with a tool), then the skill opens in the third.
        (_Builder(conn, "s-chat").user("Hi").assistant("Hello.")
         .user("What is on the disk?").call("terminal", "40% used", command="df").assistant("40% used.")
         .user("Write that up for the team").call("skill_view", LOADED, name="helpful").assistant("Disk: 40%."))
        # Earlier turn already opened the skill: the window starts after it.
        (_Builder(conn, "s-reload").user("Check disk").call("skill_view", LOADED, name="helpful").assistant("40%.")
         .user("And again please").call("skill_view", LOADED, name="helpful").assistant("41%."))
        # A huge old turn: left out, the task is kept with the newer turns.
        (_Builder(conn, "s-old-long").user("Read this " + "y" * 70_000).assistant("Done.")
         .user("Summarise it").call("skill_view", LOADED, name="helpful").assistant("Short."))
        # The current turn alone is too long: dropped.
        (_Builder(conn, "s-now-long").user("Here " + "z" * 70_000).call("skill_view", LOADED, name="helpful")
         .assistant("Ok."))
        # The same request twice in two chats: only the newer one is picked.
        _Builder(conn, "s-dup1").user("Check the disk now").call("skill_view", LOADED, name="helpful").assistant("a")
        _Builder(conn, "s-dup2").user("check the disk now!").call("skill_view", LOADED, name="helpful").assistant("b")
        conn.commit()
        conn.close()
        tasks = mine.mine(self.db, skill="helpful", skills_dir=self.skills, mode="context", redactor=_no_redact)
        self.by_session = {}
        for t in tasks:
            self.by_session.setdefault(t["session_id"], []).append(t)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mid_chat_use_keeps_earlier_turns_and_tools(self):
        [t] = self.by_session["s-chat"]
        self.assertTrue(t["kept"])
        self.assertEqual(t["replay_mode"], "context")
        self.assertEqual(t["user_turn"], "Hi")
        self.assertEqual(t["request"], "Write that up for the team")
        self.assertEqual(t["context_turns"], 3)
        roles = [m["role"] for m in t["frozen_messages"]]
        self.assertEqual(roles, ["assistant", "user", "assistant", "tool", "assistant", "user"])
        self.assertEqual(t["load_position"], len(t["frozen_messages"]))

    def test_arms_differ_only_by_the_skill(self):
        t = self.by_session["s-chat"][0]
        arms = replay.build_frozen_arms("sys", "helpful", SKILL_MD, t["user_turn"], t["frozen_messages"],
                                        t["load_position"])
        self.assertEqual(len(arms["with"]), len(arms["without"]) + 2)
        self.assertEqual(arms["without"][-1], {"role": "user", "content": "Write that up for the team"})
        self.assertEqual(arms["with"][-1]["role"], "tool")
        self.assertNotIn("percentage, always", json.dumps(arms["without"]))

    def test_earlier_load_of_the_same_skill_ends_the_window(self):
        first, second = self.by_session["s-reload"]
        self.assertTrue(first["kept"] and second["kept"])
        self.assertEqual(second["user_turn"], "And again please")
        self.assertEqual(second["frozen_messages"], [])

    def test_too_long_old_turn_is_left_out_not_dropped(self):
        [t] = self.by_session["s-old-long"]
        self.assertTrue(t["kept"])
        self.assertEqual(t["user_turn"], "Summarise it")

    def test_too_long_current_turn_is_dropped(self):
        [t] = self.by_session["s-now-long"]
        self.assertEqual(t["drop_reason"], "frozen_too_long")

    def test_latest_twelve_without_near_duplicates(self):
        picked, counts = mine.find_tasks_for_run("helpful", db_path=self.db, skills_dir=self.skills)
        sessions = [t["session_id"] for t in picked]
        self.assertEqual(sessions[0], "s-dup2")
        self.assertNotIn("s-dup1", sessions)
        self.assertEqual(counts["uses"], 7)
        self.assertEqual(counts["eligible"], 6)
        self.assertEqual(counts["tested"], len(picked))
        self.assertEqual(len(mine.latest_tasks(self.by_session["s-chat"] * 20, n=12)), 1)

    def test_long_tool_result_is_clipped(self):
        text = mine._clip("a" * 3000 + "b" * 3000)
        self.assertLess(len(text), 4100)
        self.assertTrue(text.startswith("a") and text.endswith("b"))


if __name__ == "__main__":
    unittest.main()
