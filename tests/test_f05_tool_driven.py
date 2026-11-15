"""In plain words: checks the tool-driven flag (F05). A replay reads answers only, so a skill whose
real use goes straight to tool calls gets a flag and its strength is held at Plausible.

Covers: the miner's ``tool_driven`` per task (synthetic state.db), the count and threshold, the flag
and cap in the recommendation, the chat result line, the finished report, the dashboard row, the
built-in question sets. The stored-run counts are in ``test_f05_stored_runs.py`` (dev only: the
fixture names skills that are not public). Stdlib only, no network, no model calls.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

mine = importlib.import_module("plugin.skilluminati.mine")
rec = importlib.import_module("plugin.skilluminati.recommend")
copy = importlib.import_module("plugin.skilluminati.copy")
verdict = importlib.import_module("plugin.skilluminati.verdict")
realrun = importlib.import_module("plugin.skilluminati.realrun")
report = importlib.import_module("plugin.skilluminati.report")
run = importlib.import_module("plugin.skilluminati.run")

from tests.test_mine import _Builder, build_db as _unused_build_db, MESSAGES_DDL, PROMPTS_DDL, SESSIONS_DDL, _tc  # noqa: E402,F401

EARNS = verdict.EARNS_ITS_KEEP


# ── the miner ────────────────────────────────────────────────────────────────

class _B(_Builder):
    def say_and_call(self, text, tool, **args):
        """One assistant row with text and a tool call (a preamble before the tool)."""
        self._add("assistant", text, tool_calls=_tc(tool, **args))
        return self._add("tool", json.dumps({"success": True}), tool_name=tool)


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")
    # straight to a tool after the load, no text
    _B(conn, "s-tools").user("Tidy the board").call("skill_view", name="kanban").call("terminal", command="ls") \
        .assistant("Board tidied.")
    # answers in text straight after the load
    _B(conn, "s-text").user("Rewrite this").call("skill_view", name="kanban").assistant("Here it is, rewritten.")
    # a short preamble, then a tool: still tool-driven
    _B(conn, "s-preamble").user("Check the board").call("skill_view", name="kanban") \
        .say_and_call("Checking the board.", "terminal", command="ls").assistant("Done.")
    # a real answer that also calls a tool: not tool-driven
    _B(conn, "s-long").user("Plan the week").call("skill_view", name="kanban") \
        .say_and_call("x" * mine.TOOL_DRIVEN_TEXT_MAX, "terminal", command="ls").assistant("Done.")
    # loads another skill first, then a tool: skill loads are skipped
    _B(conn, "s-more-skills").user("Sweep").call("skill_view", name="kanban").call("skill_view", name="other") \
        .call("read_file", path="x").assistant("Swept.")
    # R6: the answer delivered through a tool (a file write, a message send) is still an answer
    long = "y" * mine.TOOL_DRIVEN_TEXT_MAX
    _B(conn, "s-write").user("Write the note").call("skill_view", name="kanban") \
        .call("write_file", path="n.md", content=long).assistant("Written.")
    _B(conn, "s-send").user("Tell the team").call("skill_view", name="kanban") \
        .call("send_message", to="team", message=long).assistant("Sent.")
    _B(conn, "s-nested").user("Post it").call("skill_view", name="kanban") \
        .call("post", body={"blocks": [{"text": long}]}).assistant("Posted.")
    _B(conn, "s-just-under").user("Note it").call("skill_view", name="kanban") \
        .call("write_file", path="n.md", content=long[:-1]).assistant("Written.")
    # R6b: only content-carrying arguments count; a long command, code, query or path does not
    _B(conn, "s-long-command").user("Fix the board").call("skill_view", name="kanban") \
        .call("terminal", command=long).assistant("Fixed.")
    _B(conn, "s-long-code").user("Count them").call("skill_view", name="kanban") \
        .call("execute_code", code=long).assistant("Counted.")
    _B(conn, "s-long-query").user("Find it").call("skill_view", name="kanban") \
        .call("search", query=long, path=long).assistant("Found.")
    _B(conn, "s-other-key").user("Plan it").call("skill_view", name="kanban") \
        .call("todo", items=[{"note": long}]).assistant("Planned.")
    _B(conn, "s-patch").user("Edit the note").call("skill_view", name="kanban") \
        .call("patch", path="n.md", old_string="a", new_string=long).assistant("Edited.")
    _B(conn, "s-code-in-content").user("Run it").call("skill_view", name="kanban") \
        .call("job", body={"script": long}).assistant("Ran.")
    _B(conn, "s-upper-key").user("Say it").call("skill_view", name="kanban") \
        .call("post", Message=long).assistant("Said.")
    # the session ends at the load: no next move, not tool-driven
    _B(conn, "s-end").user("Start").call("skill_view", name="kanban")
    conn.commit()
    conn.close()


class MinerToolDrivenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "s.db"
        _db(cls.db)
        cls.skills = Path(cls.tmp.name) / "skills"
        cls.by = {}
        for mode in mine.MODES:
            for t in mine.mine(cls.db, skill="kanban", skills_dir=cls.skills, mode=mode, redactor=mine._noop_redactor):
                cls.by.setdefault(mode, {})[t["session_id"]] = t

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_mode_records_tool_driven(self):
        for mode in mine.MODES:
            for t in self.by[mode].values():
                self.assertIsInstance(t["tool_driven"], bool, (mode, t["session_id"]))

    def test_straight_to_tools(self):
        self.assertTrue(self.by["context"]["s-tools"]["tool_driven"])

    def test_text_answer(self):
        self.assertFalse(self.by["context"]["s-text"]["tool_driven"])

    def test_short_preamble_then_tool(self):
        self.assertTrue(self.by["context"]["s-preamble"]["tool_driven"])

    def test_substantive_text_with_a_tool_is_not_tool_driven(self):
        self.assertFalse(self.by["context"]["s-long"]["tool_driven"])

    def test_further_skill_loads_are_skipped(self):
        self.assertTrue(self.by["context"]["s-more-skills"]["tool_driven"])

    def test_no_next_move(self):
        self.assertFalse(self.by["strict"]["s-end"]["tool_driven"])

    def test_r6_answer_written_through_a_tool_is_not_tool_driven(self):
        self.assertFalse(self.by["context"]["s-write"]["tool_driven"])

    def test_r6_answer_sent_through_a_tool_is_not_tool_driven(self):
        self.assertFalse(self.by["context"]["s-send"]["tool_driven"])

    def test_r6_nested_argument_values_are_read(self):
        self.assertFalse(self.by["context"]["s-nested"]["tool_driven"])

    def test_r6_short_argument_values_stay_tool_driven(self):
        self.assertTrue(self.by["context"]["s-just-under"]["tool_driven"])
        self.assertTrue(self.by["context"]["s-tools"]["tool_driven"])

    def test_r6b_long_commands_code_and_queries_stay_tool_driven(self):
        for sid in ("s-long-command", "s-long-code", "s-long-query"):
            self.assertTrue(self.by["context"][sid]["tool_driven"], sid)

    def test_r6b_long_values_under_other_keys_stay_tool_driven(self):
        self.assertTrue(self.by["context"]["s-other-key"]["tool_driven"])

    def test_r6b_code_inside_a_content_key_does_not_count(self):
        self.assertTrue(self.by["context"]["s-code-in-content"]["tool_driven"])

    def test_r6b_each_named_content_key_counts(self):
        self.assertFalse(self.by["context"]["s-patch"]["tool_driven"])
        self.assertFalse(self.by["context"]["s-upper-key"]["tool_driven"])
        long = "z" * mine.TOOL_DRIVEN_TEXT_MAX
        for key in sorted(mine.CONTENT_ARG_KEYS):
            self.assertEqual(mine._content_strings({key: long}), [long], key)
            self.assertEqual(mine._content_strings({"x": {key: [long]}}), [long], key)
        for key in sorted(mine.CODE_ARG_KEYS):
            self.assertEqual(mine._content_strings({key: long}), [], key)
        self.assertLessEqual({"content", "text", "message", "body", "new_string", "file_text", "markdown", "html",
                              "caption", "summary"}, mine.CONTENT_ARG_KEYS)
        self.assertLessEqual({"command", "code", "script", "query", "path"}, mine.CODE_ARG_KEYS)

    def test_same_answer_in_every_mode(self):
        for sid, t in self.by["strict"].items():
            self.assertEqual(self.by["frozen"][sid]["tool_driven"], t["tool_driven"], sid)
            self.assertEqual(self.by["context"][sid]["tool_driven"], t["tool_driven"], sid)


# ── the count and the flag ───────────────────────────────────────────────────

def _p(outcome="win", tool_driven=False, empty=None):
    arms = {"with": {"ok": empty != "with", "error_type": copy_empty() if empty == "with" else ""},
            "without": {"ok": empty != "without", "error_type": copy_empty() if empty == "without" else ""}}
    return {"outcome": "error" if empty else outcome, "tool_driven": tool_driven, "arms": arms}


def copy_empty():
    return importlib.import_module("plugin.skilluminati.llm").EMPTY_ANSWER


class CountTests(unittest.TestCase):
    def test_union_not_double_counted(self):
        pairs = [_p(tool_driven=True, empty="with"), _p(tool_driven=True), _p(empty="without"), _p(), _p()]
        c = rec.tool_driven_counts(pairs)
        self.assertEqual((c["tested"], c["tool_driven"], c["empty_answer"], c["count"]), (5, 2, 2, 3))
        self.assertTrue(c["flag"])

    def test_half_raises_just_under_does_not(self):
        half = [_p(tool_driven=True)] * 6 + [_p()] * 6
        under = [_p(tool_driven=True)] * 5 + [_p()] * 7
        self.assertTrue(rec.tool_driven_counts(half)["flag"])
        self.assertFalse(rec.tool_driven_counts(under)["flag"])

    def test_other_errors_do_not_count(self):
        p = _p()
        p.update(outcome="error", arms={"with": {"ok": False, "error_type": "Timeout"}, "without": {"ok": True}})
        c = rec.tool_driven_counts([p, _p()])
        self.assertEqual(c["count"], 0)
        self.assertFalse(c["flag"])

    def test_nothing_tested(self):
        self.assertFalse(rec.tool_driven_counts([])["flag"])

    def test_built_in_questions_flag_only_on_empty_answers(self):
        # pack tasks carry no real-session turn, so no tool_driven key at all
        pack = [{"outcome": "win", "arms": {"with": {"ok": True}, "without": {"ok": True}}} for _ in range(10)]
        self.assertFalse(rec.tool_driven_counts(pack)["flag"])
        pack[:5] = [_p(empty="with") for _ in range(5)]
        for p in pack:
            p.pop("tool_driven", None)
        c = rec.tool_driven_counts(pack)
        self.assertEqual((c["tool_driven"], c["empty_answer"]), (0, 5))
        self.assertTrue(c["flag"])


class RecommendTests(unittest.TestCase):
    STRONG_LEAN = {"verdict": "helps", "strength": "strong", "wins": 7, "losses": 1, "ties": 1, "n": 9}
    FLAG = {"tested": 12, "tool_driven": 12, "empty_answer": 3, "count": 12, "flag": True}

    def _r(self, tools, lean=None):
        return rec.recommend(EARNS, wins=7, losses=1, ties=1, low=0.53, high=0.98, lean=lean or self.STRONG_LEAN,
                             tools=tools)

    def test_flag_text_and_key(self):
        r = self._r(self.FLAG)
        self.assertIn({"key": rec.FLAG_TOOLS, "text": copy.FLAG_TOOL_DRIVEN}, r["flags"])
        # S6: says what was seen (tool calls in real use, or empty answers here), not what the skill is
        self.assertEqual(copy.FLAG_TOOL_DRIVEN, "Most tested uses went straight to tools in real use, or came back empty here. This test reads answers only.")
        self.assertNotIn("drives tools", copy.FLAG_TOOL_DRIVEN)

    def test_probable_shows_as_plausible_with_reason(self):
        r = self._r(self.FLAG)
        self.assertEqual(r["confidence"]["level"], rec.MODERATE)
        self.assertEqual(r["confidence"]["text"], "Plausible")
        self.assertIn(copy.CONFIDENCE_WHY_TOOL_CAP.format(word="Plausible"), r["confidence"]["why"])
        self.assertEqual(r["lean"]["strength"], verdict.LIKELY)
        self.assertEqual(r["lean"]["capped_from"], verdict.STRONG)

    def test_verdict_category_unchanged(self):
        r = self._r(self.FLAG)
        self.assertEqual(r["headline"]["label"], EARNS)
        self.assertEqual(r["lean"]["verdict"], "helps")
        self.assertEqual(rec.lean_of(r)["verdict"], "helps")

    def test_no_flag_no_change(self):
        a = self._r(None)
        b = self._r({**self.FLAG, "count": 5, "tool_driven": 5, "flag": False})
        for r in (a, b):
            self.assertEqual(r["confidence"]["text"], "Probable")
            self.assertNotIn(rec.FLAG_TOOLS, [f["key"] for f in r["flags"]])
            self.assertEqual(r["lean"]["strength"], "strong")

    def test_plausible_stays_plausible_without_reason(self):
        r = self._r(self.FLAG, lean={**self.STRONG_LEAN, "strength": "likely"})
        self.assertEqual(r["confidence"]["text"], "Plausible")
        self.assertNotIn("capped_from", r["lean"])
        self.assertNotIn(copy.CONFIDENCE_WHY_TOOL_CAP.format(word="Plausible"), r["confidence"]["why"])
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in r["flags"]])

    def test_strong_without_lean_is_capped_too(self):
        r = rec.recommend(EARNS, wins=40, losses=2, ties=0, low=0.84, high=0.99, tools=self.FLAG)
        self.assertEqual(r["confidence"]["level"], rec.MODERATE)

    def test_counts_kept_in_numbers_and_survive_clean(self):
        r = self._r(self.FLAG)
        self.assertEqual(r["numbers"]["tools"]["count"], 12)
        c = rec.clean(r)
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in c["flags"]])
        self.assertEqual(c["lean"]["strength"], "likely")

    def test_from_run_keeps_the_flag_of_a_stored_row(self):
        stored = self._r(self.FLAG)
        row = {"skill": "k", "user_label": EARNS, "wins": 7, "losses": 1, "ties": 1, "low": 0.53, "high": 0.98,
               "model": "m", "recommendation": stored}
        again = rec.from_run(row, active_model="m")
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in again["flags"]])

    def test_text_is_house_style(self):
        for s in (copy.FLAG_TOOL_DRIVEN, copy.CONFIDENCE_WHY_TOOL_CAP):
            self.assertNotIn("\u2014", s)
            self.assertNotIn("\u2013", s)


# ── the run, the chat line, the report and the page ─────────────────────────

def _shell():
    shell = realrun.RealRunner.__new__(realrun.RealRunner)
    shell.skill_sha256 = {}  # set by __init__
    shell.skill_paths = {"s": "/x"}
    shell.skill_redaction = {"s": {}}
    shell.purposes = {"s": "purpose"}
    shell.skill_load_tokens = {"s": 500}
    shell.heavy_set = {"a": 500, "b": 500, "s": 500}
    shell.guard_notes = {}
    shell.cfg = mock.Mock(subject_model="m1", judge_model="j", judge_mode="x", workers=1)
    return shell


def _full_pair(i, outcome, tool_driven=False, empty=False):
    judge = {"first_picks": 1, "decisive_judgements": 2, "parse_failures": 0, "call_failures": 0,
             "agreed": True, "orders": [{"raw_winner": "a"}, {"raw_winner": "b"}]}
    arms = {"with": {"ok": not empty, "text": "" if empty else "answer", "error_type": copy_empty() if empty else ""},
            "without": {"ok": True, "text": "answer", "error_type": ""}}
    return {"outcome": "error" if empty else outcome, "mode": "context", "task_hash": f"h{i}", "cell": "s",
            "skill": "s", "tool_driven": tool_driven, "judge": None if empty else judge, "arms": arms,
            **({"error": "subject call failed"} if empty else {})}


def _summarise(pairs):
    calls = [{"role": "subject", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "latency_s": 1.0,
              "cache_read_tokens": 0, "model": "m1", "ok": True, "error_type": None}]
    with mock.patch.object(realrun.sb_runner, "totals", return_value={"minutes": 1.0}):
        return realrun.RealRunner.summarise_skill(_shell(), "s", pairs, 1.0, calls)


class RunWiringTests(unittest.TestCase):
    def setUp(self):
        # 20 wins out of 23 judged: a strong "helps"; 3 empty answers; 9 of 23 judged went straight to tools
        self.pairs = ([_full_pair(i, "win", tool_driven=i < 9) for i in range(20)]
                      + [_full_pair(20 + i, "loss") for i in range(3)]
                      + [_full_pair(30 + i, None, tool_driven=True, empty=True) for i in range(3)])
        self.base = _summarise([_full_pair(i, "win") for i in range(20)] + [_full_pair(20 + i, "loss") for i in range(3)])

    def test_tracked_pair_keeps_tool_driven_only(self):
        t = realrun.tracked_pair(self.pairs[0])
        self.assertIs(t["tool_driven"], True)
        self.assertNotIn("text", t["arms"]["with"])

    def test_pair_records_the_task_flag(self):
        shell = realrun.RealRunner.__new__(realrun.RealRunner)
        shell.skill_sha256 = {}  # set by __init__
        shell._subject = lambda *a, **k: mock.Mock(ok=False, to_dict=lambda: {"ok": False})
        shell._lock = mock.MagicMock()
        with tempfile.TemporaryDirectory() as d:
            shell.pairs_path = Path(d) / "pairs.jsonl"
            task = {"mode": "context", "session_id": "s", "load_message_id": 1, "tool_driven": True}
            p = realrun.RealRunner._pair(shell, task, "s", [], [], "", "s", "with", "without")
            self.assertIs(p["tool_driven"], True)
            task.pop("tool_driven")
            p = realrun.RealRunner._pair(shell, task, "s", [], [], "", "s", "with", "without")
            self.assertIs(p["tool_driven"], False)

    def test_baseline_is_strong_and_unflagged(self):
        self.assertEqual(self.base["lean"]["strength"], "strong")
        self.assertFalse(self.base["tools"]["flag"])

    def test_summary_flags_and_caps(self):
        s = _summarise(self.pairs)
        self.assertEqual(s["tools"]["count"], 12)
        self.assertEqual(s["tools"]["tested"], 26)
        self.assertFalse(s["tools"]["flag"])  # 12 of 26 is under half
        self.assertEqual(s["lean"]["strength"], "strong")
        s = self._flagged()  # 14 of 26
        self.assertEqual(s["tools"]["count"], 14)
        self.assertTrue(s["tools"]["flag"])
        self.assertEqual(s["lean"]["verdict"], "helps")
        self.assertEqual(s["lean"]["strength"], "likely")
        self.assertEqual(s["user_label"], EARNS)
        self.assertEqual(s["recommendation"]["confidence"]["text"], "Plausible")
        self.assertIn(rec.FLAG_TOOLS, [f["key"] for f in s["recommendation"]["flags"]])
        self.assertEqual(s["combined"]["verdict"], self.base["combined"]["verdict"])  # stats verdict untouched

    def _run_dir(self, summary):
        d = Path(self.tmp.name) / "20261114-000000-verdict-s"
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps({"per_skill": {"s": summary}, "subject_model": "m1",
                                                   "judge_model": "j", "totals": {}}), encoding="utf-8")
        return d

    def _flagged(self):
        pairs = [dict(p, tool_driven=True) if p["task_hash"] in {"h9", "h10"} else p for p in self.pairs]
        return _summarise(pairs)

    def test_chat_result_line(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        line = run.result_line(self._run_dir(self._flagged()), "s")
        self.assertIn("(Plausible)", line)
        self.assertTrue(line.endswith(copy.FLAG_TOOL_DRIVEN), line)
        plain = run.result_line(self._run_dir(self.base), "s")
        self.assertIn("(Probable)", plain)
        self.assertNotIn(copy.FLAG_TOOL_DRIVEN, plain)

    def test_report(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        text = report.terminal(self._run_dir(self._flagged()))
        self.assertIn("s: Enlightened (Plausible).", text)
        self.assertIn(copy.FLAG_TOOL_DRIVEN, text)
        plain = report.terminal(self._run_dir(self.base))
        self.assertIn("s: Enlightened (Probable).", plain)
        self.assertNotIn(copy.FLAG_TOOL_DRIVEN, plain)

    def test_slash_command_text_gives_the_reason(self):
        text = rec.format_text(self._flagged()["recommendation"])
        self.assertIn(copy.CONFIDENCE_WHY_TOOL_CAP.format(word="Plausible"), text)
        self.assertIn(copy.FLAG_TOOL_DRIVEN, text)

    def test_dashboard_row(self):
        s = self._flagged()
        row = {"skill": "s", "user_label": EARNS, "wins": 20, "losses": 3, "ties": 0, "model": "m1",
               "date": "2026-11-14", "recommendation": s["recommendation"]}
        r = report.page_result(row)
        self.assertEqual(r["strength"], "likely")
        self.assertTrue(r["working"]["tool_driven"])
        self.assertEqual(copy.page_copy()["tool_driven_tip"], copy.FLAG_TOOL_DRIVEN)
        js = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("w.tool_driven", js)
        self.assertIn("lines.push(str(c.tool_driven_tip))", js)
        plain = report.page_result({**row, "recommendation": self.base["recommendation"]})
        self.assertFalse(plain["working"]["tool_driven"])


if __name__ == "__main__":
    unittest.main()
