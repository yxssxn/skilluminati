"""Unit tests for skilluminati.mine against a synthetic state.db. Stdlib unittest only.

Run: python -m pytest tests/test_mine.py -v

The synthetic database mirrors the real ``sessions`` / ``messages`` / ``system_prompts``
columns the miner reads (inspected on the live db 2026-11-07). Names and text are invented.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MINE_PATH = REPO / "plugin" / "skilluminati" / "mine.py"


def _load():
    spec = importlib.util.spec_from_file_location("skilluminati_mine", MINE_PATH)
    assert spec is not None and spec.loader is not None, MINE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mine = _load()

SESSIONS_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    system_prompt TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    message_count INTEGER DEFAULT 0,
    system_prompt_hash TEXT
)
"""
MESSAGES_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    display_kind TEXT,
    _compressed_summary INTEGER NOT NULL DEFAULT 0
)
"""
PROMPTS_DDL = "CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, prompt TEXT NOT NULL)"


def _tc(tool: str, **args) -> str:
    return json.dumps([{
        "id": f"call_{tool}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**8}",
        "type": "function",
        "function": {"name": tool, "arguments": json.dumps(args)},
    }])


class _Builder:
    """Append messages to a session in order; timestamps and ids are monotonic."""

    def __init__(self, conn: sqlite3.Connection, session_id: str, source: str = "cli",
                 system_prompt: str | None = None, prompt_hash: str | None = None):
        self.conn = conn
        self.sid = session_id
        self.t = 1_700_000_000.0
        conn.execute(
            "INSERT INTO sessions (id, source, system_prompt, started_at, system_prompt_hash) VALUES (?,?,?,?,?)",
            (session_id, source, system_prompt, self.t, prompt_hash),
        )

    def _add(self, role, content=None, tool_calls=None, tool_name=None, display_kind=None, summary=0):
        self.t += 1
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, tool_name, timestamp, display_kind, _compressed_summary)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (self.sid, role, content, tool_calls, tool_name, self.t, display_kind, summary),
        )
        return self

    def user(self, text, **kw):
        return self._add("user", text, **kw)

    def assistant(self, text):
        return self._add("assistant", text)

    def call(self, tool, **args):
        self._add("assistant", "", tool_calls=_tc(tool, **args))
        return self._add("tool", json.dumps({"success": True, "name": tool}), tool_name=tool)


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")
    conn.execute("INSERT INTO system_prompts (hash, prompt) VALUES (?,?)", ("h1", "SYSTEM PROMPT ONE"))

    # 1. single-turn skill load, answer straight after: kept. Prompt via hash.
    b = _Builder(conn, "s-kept", prompt_hash="h1")
    b.user("Draft a thank-you note to Avery Quill").call("skill_view", name="placebo").assistant("Here is the note.")

    # 2. tool-dependent: a terminal call between load and answer: dropped.
    b = _Builder(conn, "s-tool", system_prompt="INLINE PROMPT")
    (b.user("Check the disk and tell me").call("skill_view", name="helpful")
      .call("terminal", command="df -h").assistant("Disk is at 40%."))

    # 3. multi-turn: skill loads after the second user turn: dropped.
    b = _Builder(conn, "s-multi")
    (b.user("Hi").assistant("Hello.").user("Now summarise Ondine Pty Ltd's brief")
      .call("skill_view", name="bloat").assistant("Summary."))

    # 4. no skill load at all: ignored.
    b = _Builder(conn, "s-none")
    b.user("What time is it?").assistant("Noon.")

    # 5. synthetic user rows (process_complete) do not count as turns; duplicate load in one
    #    turn counted once as duplicate; skills_list is not a tool dependency.
    b = _Builder(conn, "s-synthetic", prompt_hash="h1")
    (b.user("Run the checklist").user("[process exited]", display_kind="process_complete")
      .call("skills_list").call("skill_view", name="placebo").call("skill_view", name="placebo")
      .assistant("Done."))

    # 6. session ends before any final answer: no_answer.
    b = _Builder(conn, "s-noanswer")
    b.user("Start").call("skill_view", name="helpful")

    # 7. compressed summary rows are skipped, content stored as JSON parts is flattened.
    b = _Builder(conn, "s-parts")
    (b.user("[summary of earlier context]", summary=1)
      .user(json.dumps([{"type": "text", "text": "Part one."}, {"type": "text", "text": "Part two."}]))
      .call("skill_view", name="placebo").assistant("Answer."))
    conn.commit()
    conn.close()


class MineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "synthetic.db"
        build_db(cls.db)
        cls.skills = Path(cls.tmp.name) / "skills"
        (cls.skills / "placebo-folder").mkdir(parents=True)
        (cls.skills / "placebo-folder" / "SKILL.md").write_text("---\nname: placebo\ndescription: x\n---\nbody\n")
        (cls.skills / "helpful").mkdir()
        (cls.skills / "helpful" / "SKILL.md").write_text("---\nname: helpful\n---\n")
        cls.tasks = mine.mine(cls.db, skills_dir=cls.skills)
        cls.by_session = {}
        for t in cls.tasks:
            cls.by_session.setdefault(t["session_id"], []).append(t)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_schema_helper_reads_columns(self):
        conn = mine.connect(self.db)
        try:
            found = mine.schema(conn)
        finally:
            conn.close()
        for col in mine.SESSION_COLUMNS:
            self.assertIn(col, found["sessions"])
        for col in mine.MESSAGE_COLUMNS:
            self.assertIn(col, found["messages"])

    def test_connection_is_read_only(self):
        conn = mine.connect(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE probe (x)")
        finally:
            conn.close()

    def test_single_turn_kept(self):
        (t,) = self.by_session["s-kept"]
        self.assertTrue(t["kept"])
        self.assertIsNone(t["drop_reason"])
        self.assertEqual(t["skill"], "placebo")
        self.assertEqual(t["user_turn"], "Draft a thank-you note to Avery Quill")
        self.assertEqual(t["system_prompt"], "SYSTEM PROMPT ONE")
        self.assertEqual(t["flags"], {"tool_dependent": False, "multi_turn": False})
        self.assertEqual(t["user_turns_before_load"], 1)

    def test_tool_dependent_dropped(self):
        (t,) = self.by_session["s-tool"]
        self.assertFalse(t["kept"])
        self.assertEqual(t["drop_reason"], "tool_dependent")
        self.assertTrue(t["flags"]["tool_dependent"])
        self.assertEqual(t["system_prompt"], "INLINE PROMPT")

    def test_multi_turn_dropped(self):
        (t,) = self.by_session["s-multi"]
        self.assertFalse(t["kept"])
        self.assertEqual(t["drop_reason"], "multi_turn")
        self.assertTrue(t["flags"]["multi_turn"])
        self.assertEqual(t["user_turn"], "Hi", "cut at the FIRST user turn")
        self.assertEqual(t["user_turns_before_load"], 2)

    def test_session_without_skill_ignored(self):
        self.assertNotIn("s-none", self.by_session)

    def test_synthetic_user_rows_and_duplicates(self):
        tasks = self.by_session["s-synthetic"]
        self.assertEqual(len(tasks), 2)
        first, dup = tasks
        self.assertTrue(first["kept"], "skills_list is not a tool dependency; process_complete is not a turn")
        self.assertEqual(dup["drop_reason"], "duplicate_load")

    def test_no_answer(self):
        (t,) = self.by_session["s-noanswer"]
        self.assertEqual(t["drop_reason"], "no_answer")

    def test_compressed_rows_skipped_and_parts_flattened(self):
        (t,) = self.by_session["s-parts"]
        self.assertTrue(t["kept"])
        self.assertEqual(t["user_turn"], "Part one.\nPart two.")

    def test_missing_system_prompt_is_none(self):
        (t,) = self.by_session["s-multi"]
        self.assertIsNone(t["system_prompt"])

    def test_frontmatter_name_wins_over_folder(self):
        name_map = mine.skill_name_map(self.skills)
        self.assertEqual(name_map["placebo-folder"], "placebo")
        self.assertEqual(mine.resolve_skill_name("placebo-folder", name_map), ("placebo", True))
        self.assertEqual(mine.resolve_skill_name("unknown-skill", name_map), ("unknown-skill", False))
        self.assertEqual(mine.resolve_skill_name("plug:helpful", name_map), ("helpful", True))

    def test_skill_filter_and_limit(self):
        only = mine.mine(self.db, skill="helpful", skills_dir=self.skills)
        self.assertTrue(only)
        self.assertTrue(all(t["skill"] == "helpful" for t in only))
        self.assertEqual(len(mine.mine(self.db, limit=2, skills_dir=self.skills)), 2)

    def test_summary_counts(self):
        s = mine.summary(self.tasks)
        self.assertEqual(s["totals"]["loads"], len(self.tasks))
        self.assertEqual(s["totals"]["kept"], 3)
        self.assertEqual(s["skills"]["placebo"]["kept"], 3)
        self.assertEqual(s["skills"]["placebo"]["dropped"].get("duplicate_load"), 1)
        self.assertEqual(s["skills"]["helpful"]["dropped"], {"tool_dependent": 1, "no_answer": 1})
        self.assertEqual(s["skills"]["bloat"]["dropped"], {"multi_turn": 1})
        self.assertEqual(s["totals"]["dropped"]["multi_turn"], 1)
        self.assertEqual(list(s["skills"])[0], "placebo", "ordered by kept desc")

    def test_tool_call_parsing_is_tolerant(self):
        self.assertEqual(mine._parse_tool_calls(None), [])
        self.assertEqual(mine._parse_tool_calls("not json"), [])
        self.assertEqual(mine._parse_tool_calls('{"a": 1}'), [])
        parsed = mine._parse_tool_calls('[{"function": {"name": "skill_view", "arguments": "{bad"}}]')
        self.assertEqual(parsed[0]["name"], "skill_view")
        self.assertIn("_raw", parsed[0]["arguments"])


if __name__ == "__main__":
    unittest.main()
