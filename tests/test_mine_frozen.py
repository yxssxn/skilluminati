"""Unit tests for frozen-tools mining (``mine(..., mode="frozen")``) on a synthetic state.db.
Stdlib unittest only. Every name and value here is invented.

Run: python -m pytest tests/test_mine_frozen.py -v
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
PLUGIN_DIR = REPO / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import mine, replay  # noqa: E402

SESSIONS_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    system_prompt TEXT,
    started_at REAL NOT NULL,
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

SYSTEM = (
    "You are an assistant.\n<available_skills>\n"
    "  - placebo: Use when asked for a note.\n"
    "  - helpful: Use when checking disks.\n"
    "</available_skills>\n"
)
HELPFUL_MD = "---\nname: helpful\ndescription: Use when checking disks.\n---\n\n# Helpful\n\nReport disk use as a percentage, always.\n"
PLACEBO_MD = "---\nname: placebo\ndescription: x\n---\nbody\n"


class _Builder:
    """Append messages to a session; tool calls get deterministic ids so results can match."""

    def __init__(self, conn, session_id, system_prompt=SYSTEM):
        self.conn = conn
        self.sid = session_id
        self.t = 1_700_000_000.0
        self.n = 0
        conn.execute("INSERT INTO sessions (id, source, system_prompt, started_at) VALUES (?,?,?,?)",
                     (session_id, "cli", system_prompt, self.t))

    def _add(self, role, content=None, tool_calls=None, tool_name=None, tool_call_id=None, display_kind=None, summary=0):
        self.t += 1
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp, display_kind, _compressed_summary)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.sid, role, content, tool_call_id, tool_calls, tool_name, self.t, display_kind, summary),
        )
        return self

    def user(self, text, **kw):
        return self._add("user", text, **kw)

    def assistant(self, text):
        return self._add("assistant", text)

    def call(self, tool, result, *, text="", call_id=None, **args):
        """One assistant tool-call message followed by its tool result."""
        self.n += 1
        cid = call_id or f"call_{self.sid}_{self.n}"
        tc = json.dumps([{"id": cid, "call_id": cid, "type": "function",
                          "function": {"name": tool, "arguments": json.dumps(args)}}])
        self._add("assistant", text, tool_calls=tc)
        return self._add("tool", result, tool_name=tool, tool_call_id=cid)

    def dangling_result(self, tool, result):
        """A tool result whose call id matches nothing (orphan)."""
        return self._add("tool", result, tool_name=tool, tool_call_id="call_nobody")


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")

    # A. tool-heavy turn: terminal before the load, read_file after it, then the answer.
    #    Strict drops it (tool_dependent); frozen keeps it with load_position 2.
    b = _Builder(conn, "s-heavy")
    (b.user("Check the disk and tell me, thanks Avery")
      .call("terminal", "Filesystem 40% used, owner avery.quill@ondine.example", command="df -h")
      .call("skill_view", json.dumps({"success": True, "content": HELPFUL_MD}), name="helpful")
      .call("read_file", json.dumps({"content": "ring 0412 345 678 re the $1,200 invoice"}), path="/tmp/x")
      .assistant("Disk is at 40%."))

    # B. tools only before the load, nothing after: load_position == len(frozen_messages).
    b = _Builder(conn, "s-before")
    (b.user("Summarise the log")
      .call("terminal", "line one\nline two", command="tail log")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .assistant("Two lines."))

    # C. no tools at all: kept in both modes with empty frozen_messages.
    b = _Builder(conn, "s-clean")
    b.user("Draft a note").call("skill_view", "[skill_view] loaded", name="placebo").assistant("Note.")

    # D. binary tool result (data URI) -> frozen_binary.
    b = _Builder(conn, "s-binary")
    (b.user("Look at this")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .call("vision_analyze", json.dumps([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]), image="x.png")
      .assistant("It is a chart."))

    # E. 13 tool results -> frozen_too_many_tools.
    b = _Builder(conn, "s-many")
    b.user("Count files").call("skill_view", "[skill_view] loaded", name="helpful")
    for i in range(13):
        b.call("terminal", f"file{i}", command=f"ls {i}")
    b.assistant("Thirteen.")

    # F. one huge tool result -> frozen_too_long.
    b = _Builder(conn, "s-long")
    (b.user("Read the big file")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .call("read_file", "x" * 30_000, path="/tmp/big")
      .assistant("Big."))

    # G. multi-turn stays dropped in frozen mode; no_answer stays no_answer.
    b = _Builder(conn, "s-multi")
    (b.user("Hi").assistant("Hello.").user("Now check the disk")
      .call("terminal", "ok", command="df").call("skill_view", "[skill_view] loaded", name="helpful").assistant("Fine."))
    b = _Builder(conn, "s-noanswer")
    b.user("Start").call("terminal", "ok", command="df").call("skill_view", "[skill_view] loaded", name="helpful")

    # H. another skill loaded in the same turn stays frozen in both arms; an orphan result is
    #    dropped and counted; duplicate target load is stripped too.
    b = _Builder(conn, "s-other")
    (b.user("Do both")
      .call("skill_view", "[skill_view] placebo text", name="placebo")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .dangling_result("terminal", "orphan output")
      .call("skill_view", "[skill_view] loaded again", name="helpful")
      .call("terminal", "ok", command="true")
      .assistant("Both done."))

    # J. a tool result carrying U+2028 / U+2029 / U+0085 (splitlines() separators that json
    #    leaves raw under ensure_ascii=False) -> kept, and must round-trip as ONE jsonl line.
    b = _Builder(conn, "s-sep")
    (b.user("Read the odd file")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .call("read_file", "para one\u2028para two\u2029para three\u0085end", path="/tmp/odd")
      .assistant("Three paragraphs."))

    # I. a tool result that quotes the target skill's body -> frozen_skill_leak.
    b = _Builder(conn, "s-leak")
    (b.user("Show me the skill file")
      .call("skill_view", "[skill_view] loaded", name="helpful")
      .call("read_file", json.dumps({"content": HELPFUL_MD}), path="skills/helpful/SKILL.md")
      .assistant("Here it is."))
    conn.commit()
    conn.close()


class FrozenMineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "synthetic.db"
        build_db(cls.db)
        cls.skills = Path(cls.tmp.name) / "skills"
        (cls.skills / "helpful").mkdir(parents=True)
        (cls.skills / "helpful" / "SKILL.md").write_text(HELPFUL_MD)
        (cls.skills / "placebo").mkdir()
        (cls.skills / "placebo" / "SKILL.md").write_text(PLACEBO_MD)
        cls.names = ["Avery Quill", "Avery"]
        cls.strict = mine.mine(cls.db, skills_dir=cls.skills)
        cls.frozen = mine.mine(cls.db, skills_dir=cls.skills, mode="frozen",
                               redactor=mine.default_redactor(names=cls.names))
        cls.by_session = {}
        for t in cls.frozen:
            cls.by_session.setdefault(t["session_id"], []).append(t)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ── strict unchanged ─────────────────────────────────────────────────────

    def test_strict_mode_output_unchanged(self):
        default = mine.mine(self.db, skills_dir=self.skills)
        self.assertEqual(default, self.strict)
        for t in self.strict:
            self.assertNotIn("frozen_messages", t)
            self.assertNotIn("replay_mode", t)
        heavy = next(t for t in self.strict if t["session_id"] == "s-heavy")
        self.assertEqual(heavy["drop_reason"], "tool_dependent")
        # Only the turn with no non-skill tool survives strict mode (s-other ran terminal).
        self.assertEqual(sorted(t["session_id"] for t in self.strict if t["kept"]), ["s-clean"])
        # heavy, before, binary, many, long, leak, sep, other x2 (placebo + first helpful), and
        # noanswer (strict checks tool_dependent before no_answer): 10.
        self.assertEqual(mine.summary(self.strict)["totals"]["dropped"].get("tool_dependent"), 10)

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            mine.mine(self.db, skills_dir=self.skills, mode="loose")

    def test_new_drop_reasons_registered(self):
        for r in ("frozen_too_long", "frozen_too_many_tools", "frozen_binary"):
            self.assertIn(r, mine.DROP_REASONS)

    # ── frozen task from a tool-heavy turn ───────────────────────────────────

    def test_tool_heavy_turn_is_frozen_and_kept(self):
        (t,) = self.by_session["s-heavy"]
        self.assertTrue(t["kept"])
        self.assertIsNone(t["drop_reason"])
        self.assertEqual(t["replay_mode"], "frozen")
        self.assertTrue(t["flags"]["tool_dependent"])
        roles = [m["role"] for m in t["frozen_messages"]]
        self.assertEqual(roles, ["assistant", "tool", "assistant", "tool"])
        names = [m["tool_calls"][0]["function"]["name"] for m in t["frozen_messages"] if m["role"] == "assistant"]
        self.assertEqual(names, ["terminal", "read_file"], "the target skill_view pair is stripped")
        self.assertEqual(t["load_position"], 2, "skill loaded after the terminal pair")
        self.assertEqual(t["frozen_tool_results"], 2)
        self.assertEqual(t["user_turn"], "Check the disk and tell me, thanks Avery", "user turn raw, redacted at write time as before")
        for m in t["frozen_messages"]:
            if m["role"] == "tool":
                self.assertEqual(m["tool_call_id"], t["frozen_messages"][t["frozen_messages"].index(m) - 1]["tool_calls"][0]["id"])

    def test_tools_only_before_load(self):
        (t,) = self.by_session["s-before"]
        self.assertTrue(t["kept"])
        self.assertEqual(len(t["frozen_messages"]), 2)
        self.assertEqual(t["load_position"], 2)

    def test_clean_turn_kept_with_empty_frozen(self):
        (t,) = self.by_session["s-clean"]
        self.assertTrue(t["kept"])
        self.assertEqual(t["frozen_messages"], [])
        self.assertEqual(t["load_position"], 0)

    # ── each new drop reason ─────────────────────────────────────────────────

    def test_frozen_binary(self):
        (t,) = self.by_session["s-binary"]
        self.assertEqual(t["drop_reason"], "frozen_binary")
        self.assertFalse(t["kept"])

    def test_frozen_too_many_tools(self):
        (t,) = self.by_session["s-many"]
        self.assertEqual(t["drop_reason"], "frozen_too_many_tools")
        self.assertEqual(t["frozen_tool_results"], 13)

    def test_frozen_too_long(self):
        (t,) = self.by_session["s-long"]
        self.assertEqual(t["drop_reason"], "frozen_too_long")
        self.assertGreaterEqual(t["frozen_chars"], mine.FROZEN_MAX_CHARS)

    def test_frozen_skill_leak(self):
        (t,) = self.by_session["s-leak"]
        self.assertEqual(t["drop_reason"], "frozen_skill_leak")

    def test_multi_turn_and_no_answer_still_dropped(self):
        (m,) = self.by_session["s-multi"]
        self.assertEqual(m["drop_reason"], "multi_turn")
        (n,) = self.by_session["s-noanswer"]
        self.assertEqual(n["drop_reason"], "no_answer")

    def test_binary_detector(self):
        self.assertTrue(mine._is_binary_result("data:image/png;base64,AAAA"))
        self.assertTrue(mine._is_binary_result(json.dumps([{"type": "image", "source": {"data": "x"}}])))
        self.assertTrue(mine._is_binary_result("abc\x00def"))
        self.assertFalse(mine._is_binary_result("plain text about an image.png file"))
        self.assertFalse(mine._is_binary_result(json.dumps({"content": "1|hello"})))
        self.assertFalse(mine._is_binary_result(None))

    # ── other skills, orphans, duplicates ────────────────────────────────────

    def test_other_skill_stays_frozen_orphans_dropped_duplicate_stripped(self):
        tasks = self.by_session["s-other"]
        placebo = next(t for t in tasks if t["skill"] == "placebo")
        helpful = [t for t in tasks if t["skill"] == "helpful"]
        self.assertEqual([t["drop_reason"] for t in helpful], [None, "duplicate_load"])
        first = helpful[0]
        names = [m["tool_calls"][0]["function"]["name"] for m in first["frozen_messages"] if m["role"] == "assistant"]
        self.assertEqual(names, ["skill_view", "terminal"], "placebo's skill_view stays; helpful's two loads are stripped")
        self.assertEqual(json.loads(first["frozen_messages"][0]["tool_calls"][0]["function"]["arguments"]), {"name": "placebo"})
        self.assertEqual(first["load_position"], 2)
        self.assertEqual(first["frozen_orphan_results"], 1)
        self.assertEqual(first["frozen_tool_results"], 2)
        # From placebo's point of view, helpful's loads are ordinary frozen tool traffic.
        p_names = [m["tool_calls"][0]["function"]["name"] for m in placebo["frozen_messages"] if m["role"] == "assistant"]
        self.assertEqual(p_names, ["skill_view", "skill_view", "terminal"])
        self.assertEqual(placebo["load_position"], 0)

    # ── redaction ────────────────────────────────────────────────────────────

    def test_redaction_applied_to_tool_results_and_arguments(self):
        (t,) = self.by_session["s-heavy"]
        payload = replay.payload_text(t["frozen_messages"])
        for raw in ("avery.quill@", "0412 345 678", "$1,200"):
            self.assertNotIn(raw, payload, raw)
        for ph in ("[EMAIL]", "[PHONE]", "[MONEY]"):
            self.assertIn(ph, payload, ph)
        self.assertGreaterEqual(t["frozen_redaction_counts"].get("EMAIL", 0), 1)
        self.assertGreaterEqual(t["frozen_redaction_counts"].get("PHONE", 0), 1)
        self.assertGreaterEqual(t["frozen_redaction_counts"].get("MONEY", 0), 1)

    def test_redactor_sees_tool_call_arguments(self):
        seen: list[str] = []

        def spy(text):
            seen.append(text or "")
            return text, {}

        tasks = mine.mine(self.db, skills_dir=self.skills, mode="frozen", redactor=spy)
        self.assertTrue(any("df -h" in s for s in seen), "arguments go through the redactor")
        self.assertTrue(any("Filesystem 40%" in s for s in seen), "tool results go through the redactor")
        self.assertTrue(any(t["kept"] and t["frozen_messages"] for t in tasks))

    def test_default_redactor_uses_deny_list(self):
        r = mine.default_redactor(names=["Ondine Pty Ltd"])
        out, counts = r("ondine pty ltd owes $5")
        self.assertEqual(out, "[NAME] owes [MONEY]")
        self.assertEqual(counts["NAME"], 1)

    # ── jsonl line safety ────────────────────────────────────────────────────

    def test_jsonl_line_escapes_splitlines_separators(self):
        rec = {"text": "a\u2028b\u2029c\u0085d\x0be\x0cf\x1cg\x1dh\x1ei", "é": "ünïcode stays raw"}
        line = mine.jsonl_line(rec)
        self.assertEqual(len(line.splitlines()), 1, "one record must stay one line under splitlines()")
        for ch in ("\u2028", "\u2029", "\u0085", "\n", "\r"):
            self.assertNotIn(ch, line)
        self.assertIn("ünïcode", line, "ensure_ascii=False behaviour is preserved for ordinary text")
        self.assertEqual(json.loads(line), rec, "escaping is lossless")

    def test_frozen_task_with_u2028_round_trips_through_splitlines(self):
        (t,) = self.by_session["s-sep"]
        self.assertTrue(t["kept"], t["drop_reason"])
        payload = replay.payload_text(t["frozen_messages"])
        self.assertIn("\u2028", payload, "the separator survives mining and redaction")
        line = mine.jsonl_line(t)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tasks_frozen.jsonl"
            p.write_text(line + "\n" + mine.jsonl_line(self.by_session["s-clean"][0]) + "\n", encoding="utf-8")
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            back = json.loads(lines[0])
        self.assertEqual(back, t, "the frozen task round-trips exactly, separators included")
        # The naive writer the reviewer caught: the same record splits into more pieces.
        naive = json.dumps(t, ensure_ascii=False)
        self.assertGreater(len(naive.splitlines()), 1)

    # ── summary and helpers ──────────────────────────────────────────────────

    def test_summary_counts_frozen_reasons(self):
        s = mine.summary(self.frozen)
        d = s["totals"]["dropped"]
        self.assertEqual(d.get("frozen_binary"), 1)
        self.assertEqual(d.get("frozen_too_many_tools"), 1)
        self.assertEqual(d.get("frozen_too_long"), 1)
        self.assertEqual(d.get("frozen_skill_leak"), 1)
        self.assertNotIn("tool_dependent", d)
        self.assertEqual(s["totals"]["kept"], 6, "s-heavy, s-before, s-clean, s-sep, s-other x2 (placebo + helpful)")
        strict_kept = mine.summary(self.strict)["totals"]["kept"]
        self.assertGreater(s["totals"]["kept"], strict_kept, "frozen widens coverage")

    def test_skill_files_and_bodies(self):
        files = mine.skill_files(self.skills)
        self.assertEqual(files["helpful"], self.skills / "helpful" / "SKILL.md")
        bodies = mine.skill_body_map(self.skills)
        self.assertTrue(bodies["helpful"].startswith("# Helpful"))
        self.assertEqual(mine.skill_files(Path(self.tmp.name) / "nope"), {})

    def test_frozen_task_builds_arms_cleanly(self):
        (t,) = self.by_session["s-heavy"]
        arms = replay.build_frozen_arms(SYSTEM, "helpful", HELPFUL_MD, t["user_turn"],
                                        t["frozen_messages"], t["load_position"])
        self.assertFalse(replay.payload_contains(arms["without"], "Report disk use as a percentage"))
        self.assertTrue(replay.payload_contains(arms["with"], "Report disk use as a percentage"))


if __name__ == "__main__":
    unittest.main()
