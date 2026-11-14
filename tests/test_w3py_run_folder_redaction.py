"""W3PY SEC-M1: a run folder never keeps unredacted chat text. A planted email, API key and
deny-list name in the mined history must not land in any file the run writes. Fake LLM only,
temp HERMES_HOME. (A bare file path is not something redact.py removes, so it is not planted:
it reaches the model and the run folder alike, by design of the regex redactor.)"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from tests.test_run import SKILL, SKILL_MD, FakeCtx, HomeCase, JudgeAlwaysWith  # noqa: E402

EMAIL = "planted.person@example.org"
KEY = "sk" "-plantedKEY0123456789abcdefXYZ"
NAME = "Zebulonia Quarrington"
PLANTED = (EMAIL, KEY, NAME, "Zebulonia")


def _db(path: Path, n: int) -> None:
    from tests.test_mine import MESSAGES_DDL, PROMPTS_DDL, SESSIONS_DDL, _Builder
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_DDL + ";" + MESSAGES_DDL + ";" + PROMPTS_DDL + ";")
    for i in range(n):
        b = _Builder(conn, f"s{i}", system_prompt=f"You help {NAME} ({EMAIL}). Key {KEY}.",
                     prompt_hash=f"hash{i}")
        tag = hashlib.sha256(str(i).encode()).hexdigest()
        b.user(f"Tidy these notes for {NAME}, mail {EMAIL}, key {KEY}: {tag}") \
            .call("skill_view", name=SKILL).assistant("Done.")
    conn.commit()
    conn.close()


class RunFolderRedactionTests(HomeCase):
    def _run(self):
        d = self.home / "skills" / "notes" / SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        data = self.home / "plugin-data" / "skilluminati"
        data.mkdir(parents=True)
        (data / "names.txt").write_text(NAME + "\n", encoding="utf-8")
        _db(self.home / "state.db", 6)
        llm = JudgeAlwaysWith()
        out = self.h(FakeCtx(llm, surface="cli", home=self.home), f"{SKILL} --yes")
        self.assertIn("Enlightened", out)
        runs = [p for p in (data / "runs").iterdir() if p.is_dir()]
        self.assertEqual(len(runs), 1)
        return data, runs[0], llm

    def test_no_planted_text_in_any_file_the_run_writes(self):
        data, run_dir, llm = self._run()
        files = [p for p in data.rglob("*") if p.is_file() and p.name != "names.txt"]
        self.assertTrue(any(p.name == "tasks.jsonl" for p in files) or any(p.name == "tasks_frozen.jsonl" for p in files))
        for p in files:
            text = p.read_text(encoding="utf-8", errors="replace")
            for s in PLANTED:
                self.assertNotIn(s, text, f"{s!r} found in {p.relative_to(data)}")
        # The model got the redacted text too.
        for messages, _ in llm.calls:
            blob = json.dumps(messages)
            for s in PLANTED:
                self.assertNotIn(s, blob)

    def test_stored_tasks_keep_the_hash_and_the_redacted_text(self):
        _, run_dir, _ = self._run()
        rows = []
        for name in ("tasks.jsonl", "tasks_frozen.jsonl"):
            p = run_dir / name
            if p.exists():
                rows += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows), 6)
        for t in rows:
            self.assertTrue(t["system_prompt_hash"].startswith("hash"))
            self.assertIn("Tidy these notes for", t["user_turn"])
            self.assertIn("You help", t["system_prompt"])

    def test_stored_task_redacts_context_and_frozen_fields(self):
        from skilluminati import run
        t = {"system_prompt": f"Hi {NAME}", "user_turn": f"mail {EMAIL}", "request": f"use {KEY}",
             "system_prompt_hash": "abc", "session_id": "s1",
             "frozen_messages": [{"role": "tool", "content": json.dumps({"out": EMAIL})},
                                 {"role": "assistant", "tool_calls": [{"function": {"arguments": f'{{"to": "{EMAIL}"}}'}}]}]}
        out = run.stored_task(t, [NAME])
        blob = json.dumps(out)
        for s in PLANTED:
            self.assertNotIn(s, blob)
        self.assertEqual((out["system_prompt_hash"], out["session_id"]), ("abc", "s1"))
        self.assertIn(EMAIL, json.dumps(t))  # the in-memory task is left as it was
