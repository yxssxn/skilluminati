"""In plain words: looks through your past Hermes chats (read-only) for the moments a skill was
opened, and turns each one into a task: the chat up to that moment, to be answered again with and
without the skill. It runs at the start of a test on one of your skills.

Task miner: find where skills loaded in real Hermes sessions and cut replayable tasks.

Reads ``state.db`` strictly read-only (``file:...?mode=ro`` URI). Never writes, never copies.

A *load* is an assistant message whose ``tool_calls`` JSON contains a ``skill_view`` call.
For every load we produce one task dict:

* ``skill``: frontmatter ``name:`` of the skill (resolved through the skills directory; the
  name the agent typed is kept in ``skill_requested``).
* ``session_id``, ``load_message_id``.
* ``user_turn``: the first real user turn in the session, which is where the task is cut.
* ``system_prompt``: the stored system prompt (``sessions.system_prompt``, else the
  ``system_prompts`` row matching ``sessions.system_prompt_hash``), else ``None``.
* ``flags``: ``tool_dependent`` (a non-skill tool was called between the user turn and the
  final answer) and ``multi_turn`` (more than one real user turn preceded the load).
* ``kept`` / ``drop_reason``: tool-dependent and multi-turn tasks are dropped by default;
  ``no_answer`` (session ended before a final answer) and ``duplicate_load`` (same skill loaded
  again in the same turn) are the other two reasons.

Modes (``mine(..., mode=...)``):

* ``"strict"`` (default): exactly the behaviour above. A task is replayable only when no
  non-skill tool ran between the user turn and the final answer.
* ``"frozen"``: tool-dependent turns are kept too. The tool calls and tool results that ran in
  the original turn before the final answer are captured verbatim (after redaction) as
  ``frozen_messages`` (OpenAI chat-shaped assistant ``tool_calls`` messages and ``tool``
  result messages), with the target skill's own ``skill_view`` call and result stripped out
  and its slot recorded as ``load_position`` (an index into ``frozen_messages``). The replay
  builder (``replay.build_frozen_arms``) re-inserts a synthetic ``skill_view`` pair at that
  slot in the WITH arm only, so both arms see identical frozen context otherwise, and the model
  is offered no tools. Frozen tasks carry ``replay_mode: "frozen"``. Extra drop reasons:
  ``frozen_binary`` (a tool result is an image or binary), ``frozen_too_many_tools`` (more
  than ``FROZEN_MAX_TOOL_RESULTS`` tool results), ``frozen_too_long`` (frozen context of
  ``FROZEN_MAX_CHARS`` characters or more after redaction).

* ``"context"`` (what real runs use, via :func:`find_tasks_for_run`): the chat as it stood when
  the skill was loaded, earlier turns and tool results included, frozen and redacted. Both arms
  answer the next turn; only the WITH arm gets the skill. If the chat is too long, the oldest
  turns are left out. Only the current turn can rule a task out (binary data, skill text already
  in it, too long or too many tool results on its own).

:func:`find_tasks_for_run` picks the latest ``RUN_TASKS`` uses, skipping near-identical requests.

Only the columns listed in ``SESSION_COLUMNS`` / ``MESSAGE_COLUMNS`` are read, so a synthetic
database with just those columns works for tests.
"""

from __future__ import annotations

import copy
import difflib
import importlib.util
import json
import os
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

def hermes_home() -> Path:
    """Hermes's home folder for the active profile, from Hermes itself (get_hermes_home).
    Outside Hermes (the test suite) it falls back to $HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def default_db_path() -> Path:
    """``$HERMES_HOME/state.db``. Never created, never written; the miner only ever reads it."""
    return hermes_home() / "state.db"


def default_skills_dir() -> Path:
    return hermes_home() / "skills"


# Import-time snapshots kept for callers that use the constants as defaults. Set HERMES_HOME
# before import (or call the functions above) to retarget.
DEFAULT_DB = str(default_db_path())
DEFAULT_SKILLS_DIR = default_skills_dir()

SKILL_TOOLS = frozenset({"skill_view", "skills_list"})
SKILL_VIEW = "skill_view"

# Columns this module reads. Mirrors the real schema (inspected 2026-11-07).
SESSION_COLUMNS = ("id", "source", "system_prompt", "system_prompt_hash", "started_at")
MESSAGE_COLUMNS = (
    "id", "session_id", "role", "content", "tool_calls", "tool_name",
    "display_kind", "_compressed_summary", "tool_call_id",
)
SYSTEM_PROMPT_COLUMNS = ("hash", "prompt")

# ``display_kind`` values Hermes stores on synthetic user rows (process completions, auto
# continues, model switches). They are not turns the human typed.
SYNTHETIC_USER_KINDS = frozenset({
    "auto_continue", "process_complete", "async_delegation_complete",
    "internal_notification", "model_switch", "hidden",
})

DROP_REASONS = (
    "tool_dependent", "multi_turn", "no_answer", "duplicate_load", "no_user_turn",
    "frozen_too_long", "frozen_too_many_tools", "frozen_binary", "frozen_skill_leak",
)
# ``frozen_skill_leak``: a frozen tool result (e.g. a read_file of the SKILL.md itself) carries
# the target skill's body, which would put the skill into the WITHOUT arm. Guard for the
# builder's leak assertion; expected to be rare.

MODES = ("strict", "frozen", "context")
# Frozen-mode eligibility : frozen context must be UNDER this many
# characters after redaction, with AT MOST this many tool results, and no binary result.
FROZEN_MAX_CHARS = 24_000
FROZEN_MAX_TOOL_RESULTS = 12

# Context mode: how much earlier chat we keep. Older turns are left out first.
CONTEXT_MAX_CHARS = 60_000
CONTEXT_MAX_TOOL_RESULTS = 40
#: A tool result longer than this keeps only its start and end (the same in both arms).
TOOL_RESULT_CLIP = 4_000

#: How many uses a real run tests: the latest ones.
RUN_TASKS = 12
#: Two requests this similar (0 to 1) count as the same task.
SAME_TASK_RATIO = 0.9

# A tool result is treated as binary when it carries a data URI, an image part, or raw bytes.
_BINARY_MARKERS = ("data:image/", "data:application/", "data:audio/", "data:video/")
_IMAGE_PART_TYPES = frozenset({"image", "image_url", "input_image", "document", "input_file", "audio", "input_audio"})

# ``(text) -> (redacted_text, counts)``; the shape of ``redact.redact``.
Redactor = Callable[[Optional[str]], tuple[Optional[str], dict[str, int]]]

# Characters that Python's ``str.splitlines()`` treats as line breaks but ``json.dumps`` leaves
# raw under ``ensure_ascii=False`` (everything below U+0020 is already escaped by json). A
# tool result carrying one of these would split a JSONL record in two for every reader that
# uses ``read_text().splitlines()``, which is this repo's JSONL idiom.
_JSONL_UNSAFE = {"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def jsonl_line(obj: Any) -> str:
    """One JSONL record: ``json.dumps(obj, ensure_ascii=False)`` with U+0085, U+2028 and U+2029
    escaped so the line survives ``str.splitlines()`` and still ``json.loads`` to ``obj``."""
    text = json.dumps(obj, ensure_ascii=False)
    for raw, esc in _JSONL_UNSAFE.items():
        text = text.replace(raw, esc)
    return text

_FM_NAME_RE = re.compile(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", re.MULTILINE)


# ── connection ───────────────────────────────────────────────────────────────

def connect(db_path: str | os.PathLike[str] = DEFAULT_DB) -> sqlite3.Connection:
    """Read-only connection. The only way this module ever opens a database."""
    path = Path(db_path).expanduser().resolve()
    # Quote the path so a "?", "#" or "%" in it cannot change the URI's meaning (mode=ro stays in force).
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")  # belt and braces on top of mode=ro
    return conn


def schema(conn: sqlite3.Connection, tables: Iterable[str] = ("sessions", "messages", "system_prompts")) -> dict[str, list[str]]:
    """``{table: [column, ...]}`` via read-only ``PRAGMA table_info``."""
    out: dict[str, list[str]] = {}
    for table in tables:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        out[table] = [r[1] for r in rows]
    return out


# ── skill name resolution (frontmatter name, not folder) ─────────────────────

def skill_name_map(skills_dir: str | os.PathLike[str] = DEFAULT_SKILLS_DIR) -> dict[str, str]:
    """Map every folder name and frontmatter name to the frontmatter ``name:``.

    Empty when the directory is absent, so mining a synthetic db needs no skills on disk.
    """
    root = Path(skills_dir)
    mapping: dict[str, str] = {}
    if not root.is_dir():
        return mapping
    for skill_md in root.rglob("SKILL.md"):
        try:
            head = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        folder = skill_md.parent.name
        m = _FM_NAME_RE.search(head) if head.lstrip("\ufeff").startswith("---") else None
        name = m.group(1).strip() if m else folder
        mapping.setdefault(folder, name)
        mapping.setdefault(name, name)
    return mapping


def resolve_skill_name(requested: str, name_map: dict[str, str]) -> tuple[str, bool]:
    """``(canonical_name, known)``. Unknown names are kept verbatim and flagged."""
    requested = (requested or "").strip()
    if requested in name_map:
        return name_map[requested], True
    # ``plugin:skill`` qualified form.
    if ":" in requested and requested.split(":", 1)[1] in name_map:
        return name_map[requested.split(":", 1)[1]], True
    return requested, False


# ── message helpers ──────────────────────────────────────────────────────────

def _parse_tool_calls(raw: Any) -> list[dict[str, Any]]:
    """``[{name, arguments(dict), id}]`` from the stored ``tool_calls`` JSON. Never raises.
    ``id`` is the stored call id (``id`` or ``call_id``) or ``None``."""
    if not raw:
        return []
    try:
        calls = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(calls, list):
        return []
    out = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn: dict[str, Any] = call["function"] if isinstance(call.get("function"), dict) else call
        name = fn.get("name")
        if not name:
            continue
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_raw": args}
        call_id = call.get("id") or call.get("call_id") or fn.get("id")
        out.append({"name": str(name), "arguments": args, "id": str(call_id) if call_id else None})
    return out


def _text_of(content: Any) -> str:
    """Plain text from a stored ``content`` cell (string or JSON list of parts)."""
    if content is None:
        return ""
    if not isinstance(content, str):
        return str(content)
    stripped = content.lstrip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parts = json.loads(stripped)
        except ValueError:
            return content
        if isinstance(parts, list) and parts and all(isinstance(p, dict) for p in parts):
            texts = [str(p.get("text", "")) for p in parts if p.get("type", "text") == "text"]
            return "\n".join(t for t in texts if t)
    return content


def _is_real_user_turn(row: sqlite3.Row) -> bool:
    if row["role"] != "user":
        return False
    if row["_compressed_summary"]:
        return False
    kind = row["display_kind"]
    return kind is None or kind not in SYNTHETIC_USER_KINDS


def _session_messages(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    cols = ", ".join(MESSAGE_COLUMNS)
    return conn.execute(
        f"SELECT {cols} FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()


def _system_prompt(conn: sqlite3.Connection, session: sqlite3.Row, cache: dict[str, Optional[str]]) -> Optional[str]:
    if session["system_prompt"]:
        return session["system_prompt"]
    h = session["system_prompt_hash"]
    if not h:
        return None
    if h not in cache:
        row = conn.execute("SELECT prompt FROM system_prompts WHERE hash = ?", (h,)).fetchone()
        cache[h] = row[0] if row else None
    return cache[h]


# ── core ─────────────────────────────────────────────────────────────────────

def _tasks_for_session(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    name_map: dict[str, str],
    prompt_cache: dict[str, Optional[str]],
    skill_filter: Optional[str],
    mode: str = "strict",
    redactor: Optional[Redactor] = None,
    skill_bodies: Optional[dict[str, Optional[str]]] = None,
) -> list[dict[str, Any]]:
    msgs = _session_messages(conn, session["id"])
    tasks: list[dict[str, Any]] = []
    user_turns_seen = 0
    first_user_turn: Optional[str] = None
    first_user_turn_id: Optional[int] = None
    seen_in_turn: set[str] = set()
    system_prompt_resolved = False
    system_prompt: Optional[str] = None

    for i, row in enumerate(msgs):
        if row["_compressed_summary"]:
            continue
        if _is_real_user_turn(row):
            user_turns_seen += 1
            seen_in_turn = set()
            if first_user_turn is None:
                first_user_turn = _text_of(row["content"])
                first_user_turn_id = row["id"]
            continue
        if row["role"] != "assistant":
            continue
        calls = _parse_tool_calls(row["tool_calls"])
        loads = [c for c in calls if c["name"] == SKILL_VIEW]
        if not loads:
            continue
        for call in loads:
            requested = str(call["arguments"].get("name", "")).strip()
            skill, known = resolve_skill_name(requested, name_map)
            if skill_filter and skill != skill_filter and requested != skill_filter:
                continue
            if not system_prompt_resolved:
                system_prompt = _system_prompt(conn, session, prompt_cache)
                system_prompt_resolved = True

            tool_dependent, answered = _scan_turn(msgs, i)
            multi_turn = user_turns_seen > 1
            duplicate = skill in seen_in_turn
            seen_in_turn.add(skill)

            drop_reason: Optional[str]
            frozen: Optional[dict[str, Any]] = None
            context = _empty_context()
            if user_turns_seen == 0:
                # Skill loaded before any user turn (cron/oneshot bootstrap); nothing to cut.
                drop_reason = "no_user_turn"
            elif duplicate:
                drop_reason = "duplicate_load"
            elif mode == "context":
                context = _context_task(msgs, i, skill, name_map, redactor or _noop_redactor,
                                        lookup_skill(skill_bodies or {}, skill))
                drop_reason = context["drop_reason"] or (None if answered else "no_answer")
            elif multi_turn:
                drop_reason = "multi_turn"
            elif mode == "strict" and tool_dependent:
                drop_reason = "tool_dependent"
            elif not answered:
                drop_reason = "no_answer"
            elif mode == "frozen":
                frozen = _freeze_turn(msgs, i, skill, name_map, redactor or _noop_redactor)
                body = lookup_skill(skill_bodies or {}, skill)
                drop_reason = _frozen_drop_reason(frozen, body)
            else:
                drop_reason = None

            task: dict[str, Any] = {
                "skill": skill,
                "skill_requested": requested,
                "skill_known": known,
                "session_id": session["id"],
                "source": session["source"],
                "load_message_id": row["id"],
                "user_turn_message_id": first_user_turn_id,
                "user_turn": first_user_turn,
                "system_prompt": system_prompt,
                "system_prompt_hash": session["system_prompt_hash"],
                "user_turns_before_load": user_turns_seen,
                "flags": {"tool_dependent": tool_dependent, "multi_turn": multi_turn},
                "kept": drop_reason is None,
                "drop_reason": drop_reason,
            }
            if mode == "frozen":
                task["replay_mode"] = "frozen"
                task["frozen_messages"] = frozen["messages"] if frozen else []
                task["load_position"] = frozen["load_position"] if frozen else 0
                task["frozen_tool_results"] = frozen["tool_results"] if frozen else 0
                task["frozen_chars"] = frozen["chars"] if frozen else 0
                task["frozen_orphan_results"] = frozen["orphan_results"] if frozen else 0
                task["frozen_redaction_counts"] = frozen["redaction_counts"] if frozen else {}
            if mode == "context":
                task["replay_mode"] = "context"
                task["user_turn"] = context["user_turn"]
                task["request"] = context["request"]
                task["frozen_messages"] = context["messages"]
                task["load_position"] = len(context["messages"])
                task["frozen_chars"] = context["chars"]
                task["context_turns"] = context["turns"]
            tasks.append(task)
    return tasks


# ── frozen tools ─────────────────────────────────────────────────────────────

def _noop_redactor(text: Optional[str]) -> tuple[Optional[str], dict[str, int]]:
    return text, {}


def _merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def _redact_value(value: Any, redactor: Redactor, counts: dict[str, int]) -> Any:
    """Redact every string inside a JSON-ish value (dict / list / str); other scalars pass."""
    if isinstance(value, str):
        out, c = redactor(value)
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
        return out
    if isinstance(value, dict):
        return {k: _redact_value(v, redactor, counts) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, redactor, counts) for v in value]
    return value


def _is_binary_result(content: Any) -> bool:
    """True when a tool result is an image, a data URI payload, or raw bytes."""
    if content is None:
        return False
    if isinstance(content, (bytes, bytearray, memoryview)):
        return True
    text = content if isinstance(content, str) else str(content)
    if "\x00" in text:
        return True
    if any(marker in text for marker in _BINARY_MARKERS):
        return True
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return False
        parts = parsed if isinstance(parsed, list) else [parsed]
        for part in parts:
            if isinstance(part, dict):
                if str(part.get("type", "")).lower() in _IMAGE_PART_TYPES:
                    return True
                if any(k in part for k in ("image_url", "image", "source", "b64_json", "base64")) and (
                    isinstance(part.get("image_url"), (dict, str)) or isinstance(part.get("source"), dict)
                    or "b64_json" in part or "base64" in part
                ):
                    return True
    return False


def _freeze_turn(
    msgs: list[sqlite3.Row],
    load_index: int,
    skill: str,
    name_map: dict[str, str],
    redactor: Redactor,
) -> dict[str, Any]:
    """Capture the tool traffic of the agent turn containing ``msgs[load_index]``.

    Returns ``{messages, load_position, tool_results, chars, binary, orphan_results,
    redaction_counts}``. ``messages`` are OpenAI chat-shaped assistant tool-call messages
    and tool result messages, in original order, with every ``skill_view`` call for the
    target ``skill`` (and its result) removed. ``load_position`` is the index in ``messages``
    where the first such call sat, i.e. where the replay re-inserts the synthetic pair.
    Everything else in the turn is replayed verbatim after redaction. Any other tool, including
    ``skills_list`` and ``skill_view`` of a different skill, stays in both arms.
    """
    start = load_index
    while start > 0 and not _is_real_user_turn(msgs[start - 1]):
        start -= 1
    messages: list[dict[str, Any]] = []
    stripped_ids: set[str] = set()
    known_ids: set[str] = set()
    load_position: Optional[int] = None
    tool_results = 0
    orphan_results = 0
    binary = False
    counts: dict[str, int] = {}

    for row in msgs[start:]:
        if row["_compressed_summary"]:
            continue
        if _is_real_user_turn(row) and row["id"] != msgs[start]["id"]:
            break
        if row["role"] == "assistant":
            calls = _parse_tool_calls(row["tool_calls"])
            text = _text_of(row["content"]).strip()
            if not calls:
                if text:
                    break  # the final answer: not frozen, the model must produce it
                continue
            kept_calls: list[dict[str, Any]] = []
            for n, call in enumerate(calls):
                if _is_target_load(call, skill, name_map):
                    if load_position is None:
                        load_position = len(messages)
                    if call["id"]:
                        stripped_ids.add(call["id"])
                    continue
                call_id = call["id"] or f"call_frozen_{row['id']}_{n}"
                known_ids.add(call_id)
                args = _redact_value(copy.deepcopy(call["arguments"]), redactor, counts)
                kept_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(args, ensure_ascii=False)},
                })
            if not kept_calls:
                continue
            red_text, c = redactor(text) if text else ("", {})
            counts = _merge(counts, c)
            messages.append({"role": "assistant", "content": red_text or "", "tool_calls": kept_calls})
        elif row["role"] == "tool":
            call_id = row["tool_call_id"]
            if call_id and call_id in stripped_ids:
                continue
            if row["tool_name"] == SKILL_VIEW and not call_id:
                # Unmatched skill_view result: assume it belongs to the stripped load.
                continue
            if not call_id or call_id not in known_ids:
                orphan_results += 1
                continue
            content = row["content"]
            if _is_binary_result(content):
                binary = True
                tool_results += 1
                continue
            text = _text_of(content) if _looks_like_parts(content) else (content if isinstance(content, str) else str(content))
            red, c = redactor(text)
            counts = _merge(counts, c)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": row["tool_name"] or "",
                "content": red or "",
            })
            tool_results += 1

    if load_position is None:
        load_position = len(messages)
    chars = len(json.dumps(messages, ensure_ascii=False))
    return {
        "messages": messages,
        "load_position": load_position,
        "tool_results": tool_results,
        "chars": chars,
        "binary": binary,
        "orphan_results": orphan_results,
        "redaction_counts": counts,
    }


def _looks_like_parts(content: Any) -> bool:
    """True when ``content`` is a JSON list of ``{type, ...}`` parts (the multi-part shape).
    A JSON list of plain records (no ``type`` keys) is not parts and is kept verbatim."""
    if not isinstance(content, str):
        return False
    stripped = content.lstrip()
    if not (stripped.startswith("[") and stripped.rstrip().endswith("]")):
        return False
    try:
        parts = json.loads(stripped)
    except ValueError:
        return False
    return (isinstance(parts, list) and bool(parts)
            and all(isinstance(p, dict) and "type" in p for p in parts))


def _collect_strings(value: Any, out: list[str], depth: int = 0) -> None:
    """Every string inside ``value``; strings that themselves parse as JSON are decoded too
    (two levels deep), so a skill body hidden inside a JSON-encoded tool result is found."""
    if isinstance(value, str):
        out.append(value)
        stripped = value.lstrip()
        if depth < 2 and stripped[:1] in ("{", "["):
            try:
                _collect_strings(json.loads(stripped), out, depth + 1)
            except ValueError:
                pass
    elif isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            _collect_strings(v, out, depth)
    elif isinstance(value, list):
        for v in value:
            _collect_strings(v, out, depth)
    elif value is not None:
        out.append(str(value))


def frozen_plain_text(messages: Iterable[dict[str, Any]]) -> str:
    """Every string a model would read from frozen messages, with JSON layers decoded and
    newline-joined: contents, tool-call names and arguments."""
    chunks: list[str] = []
    _collect_strings(list(messages), chunks)
    return "\n".join(chunks)


_WS_RE = re.compile(r"\s+")


def text_contains(haystack: str, needle: str) -> bool:
    """``needle in haystack`` tolerant of JSON escaping and whitespace differences."""
    if not needle:
        return False
    if needle in haystack:
        return True
    for escaped in (json.dumps(needle, ensure_ascii=False)[1:-1], json.dumps(needle)[1:-1]):
        if escaped in haystack:
            return True
    return _WS_RE.sub(" ", needle).strip() in _WS_RE.sub(" ", haystack)


def _frozen_drop_reason(frozen: dict[str, Any], skill_body: Optional[str]) -> Optional[str]:
    """Eligibility in order: binary, too many tool results, too long, skill text leaked in."""
    if frozen["binary"]:
        return "frozen_binary"
    if frozen["tool_results"] > FROZEN_MAX_TOOL_RESULTS:
        return "frozen_too_many_tools"
    if frozen["chars"] >= FROZEN_MAX_CHARS:
        return "frozen_too_long"
    if skill_body and frozen["messages"] and text_contains(frozen_plain_text(frozen["messages"]), skill_body):
        return "frozen_skill_leak"
    return None


# ── context mode: the chat as it stood at the load ───────────────────────────

def _empty_context() -> dict[str, Any]:
    return {"drop_reason": None, "user_turn": None, "request": None, "messages": [], "chars": 0, "turns": 0}


def _clip(text: str) -> str:
    """Keep the start and end of a long tool result."""
    if len(text) <= TOOL_RESULT_CLIP:
        return text
    half = TOOL_RESULT_CLIP // 2
    return f"{text[:half]}\n[... {len(text) - TOOL_RESULT_CLIP} characters cut ...]\n{text[-half:]}"


def _is_target_load(call: dict[str, Any], skill: str, name_map: dict[str, str]) -> bool:
    if call["name"] != SKILL_VIEW:
        return False
    requested = str(call["arguments"].get("name", "")).strip()
    return requested == skill or resolve_skill_name(requested, name_map)[0] == skill


def _turn_messages(rows: list[sqlite3.Row], skill: str, name_map: dict[str, str],
                   redactor: Redactor) -> tuple[list[dict[str, Any]], Optional[str]]:
    """One user turn as chat messages (redacted), and a problem word or ``None``.

    Problems: ``"binary"`` (an image or raw bytes in a tool result), ``"too_long"`` (one message
    over the whole limit) and ``"skill_loaded"`` (the skill under test was opened in this turn).
    Tool calls without a result are left out."""
    messages: list[dict[str, Any]] = []
    for row in rows:
        if row["_compressed_summary"]:
            continue
        text = _text_of(row["content"]).strip()
        if row["role"] == "tool":
            text = _clip(text)  # before redacting: redacting a huge text is slow
        elif len(text) >= CONTEXT_MAX_CHARS:
            return messages, "too_long"
        if row["role"] == "user":
            messages.append({"role": "user", "content": redactor(text)[0] or ""})
        elif row["role"] == "assistant":
            calls = _parse_tool_calls(row["tool_calls"])
            if any(_is_target_load(c, skill, name_map) for c in calls):
                return messages, "skill_loaded"
            tool_calls = [{"id": c["id"], "type": "function", "function": {
                "name": c["name"],
                "arguments": json.dumps(_redact_value(c["arguments"], redactor, {}), ensure_ascii=False)}}
                for c in calls if c["id"]]
            if text or tool_calls:
                messages.append({"role": "assistant", "content": redactor(text)[0] or "", "tool_calls": tool_calls})
        elif row["role"] == "tool":
            if _is_binary_result(row["content"]):
                return messages, "binary"
            messages.append({"role": "tool", "tool_call_id": row["tool_call_id"], "name": row["tool_name"] or "",
                             "content": redactor(text)[0] or ""})
    return _pair_up_tools(messages), None


def _pair_up_tools(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool calls with no result and results with no call; a model API rejects both."""
    results = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    calls = {c["id"] for m in messages if m["role"] == "assistant" for c in m["tool_calls"]}
    out = []
    for m in messages:
        if m["role"] == "tool" and m["tool_call_id"] not in calls:
            continue
        if m["role"] == "assistant":
            kept = [c for c in m["tool_calls"] if c["id"] in results]
            if not kept and not m["content"]:
                continue
            m = {"role": "assistant", "content": m["content"], **({"tool_calls": kept} if kept else {})}
        out.append(m)
    return out


def _context_task(msgs: list[sqlite3.Row], load_index: int, skill: str, name_map: dict[str, str],
                  redactor: Redactor, skill_body: Optional[str]) -> dict[str, Any]:
    """The chat before ``msgs[load_index]``, newest turn first, as many turns as fit.

    The current turn must fit on its own, or the task is dropped. Older turns are added one at a
    time and the first one that is too big, has binary data, holds the skill text or opened the
    skill stops the walk. Returns ``_empty_context()`` filled in."""
    turns: list[list[sqlite3.Row]] = []
    for row in msgs[:load_index]:
        if _is_real_user_turn(row):
            turns.append([row])
        elif turns:
            turns[-1].append(row)
    out = _empty_context()
    window: list[dict[str, Any]] = []
    first_row: Optional[sqlite3.Row] = None
    for n, rows in enumerate(reversed(turns)):
        current = n == 0
        messages, problem = _turn_messages(rows, skill, name_map, redactor)
        if not problem and skill_body and text_contains(frozen_plain_text(messages), skill_body):
            problem = "skill_leak"
        candidate = messages + window
        if not problem and len(json.dumps(candidate, ensure_ascii=False)) >= CONTEXT_MAX_CHARS:
            problem = "too_long"
        if not problem and sum(m["role"] == "tool" for m in candidate) > CONTEXT_MAX_TOOL_RESULTS:
            problem = "too_many_tools"
        if not current and messages and messages[-1]["role"] == "user":
            problem = problem or "no_reply"  # an unanswered older turn would put two user turns in a row
        if problem:
            if current:
                out["drop_reason"] = "frozen_" + problem.replace("skill_loaded", "skill_leak")
                return out
            break
        window, first_row = candidate, rows[0]
        out["turns"] += 1
    if first_row is None:
        return out
    out["user_turn"] = _text_of(first_row["content"])
    out["request"] = _text_of(turns[-1][0]["content"])
    while window[-1]["role"] == "assistant" and "tool_calls" not in window[-1]:
        window.pop()  # the chat must end where the model answers next
    out["messages"] = window[1:]  # the first user message travels as ``user_turn``
    out["chars"] = len(json.dumps(out["messages"], ensure_ascii=False))
    return out


# ── picking the tests for a run ──────────────────────────────────────────────

def _same_task(a: Optional[str], b: Optional[str]) -> bool:
    a, b = " ".join((a or "").lower().split())[:500], " ".join((b or "").lower().split())[:500]
    return difflib.SequenceMatcher(None, a, b).ratio() >= SAME_TASK_RATIO


def latest_tasks(tasks: Iterable[dict[str, Any]], n: int = RUN_TASKS) -> list[dict[str, Any]]:
    """The ``n`` newest kept tasks, skipping any whose request matches one already picked."""
    picked: list[dict[str, Any]] = []
    for t in sorted(kept(tasks), key=lambda t: t["load_message_id"], reverse=True):
        if len(picked) == n:
            break
        request = t.get("request") or t.get("user_turn")
        if not any(_same_task(request, p.get("request") or p.get("user_turn")) for p in picked):
            picked.append(t)
    return picked


def find_tasks_for_run(skill: str, db_path: str | os.PathLike[str] = DEFAULT_DB,
                       skills_dir: str | os.PathLike[str] = DEFAULT_SKILLS_DIR,
                       n: int = RUN_TASKS) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The tests a real run uses for ``skill`` (context mode, latest ``n``) and the counts:
    ``uses`` (times the skill was opened), ``eligible`` (usable as a test), ``tested`` (picked).
    ``loads``/``kept``/``dropped`` repeat them under the older key names."""
    tasks = mine(db_path, skill=skill, skills_dir=skills_dir, mode="context")
    picked = latest_tasks(tasks, n)
    s = summary(tasks)["skills"].get(skill) or {"loads": 0, "kept": 0, "dropped": {}}
    counts = {"uses": s["loads"], "eligible": s["kept"], "tested": len(picked), **s}
    return picked, counts


def skill_files(skills_dir: str | os.PathLike[str] = DEFAULT_SKILLS_DIR) -> dict[str, Path]:
    """``{frontmatter name: SKILL.md path}`` (first found wins; folder name when there is no
    frontmatter name). Empty when the directory is absent."""
    root = Path(skills_dir)
    files: dict[str, Path] = {}
    if not root.is_dir():
        return files
    for skill_md in sorted(root.rglob("SKILL.md")):
        try:
            head = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        m = _FM_NAME_RE.search(head) if head.lstrip("\ufeff").startswith("---") else None
        name = m.group(1).strip() if m else skill_md.parent.name
        files.setdefault(name, skill_md)
    return files


def lookup_skill(mapping: dict[str, Any], skill: str) -> Any:
    """``mapping[skill]`` tolerant of the ``category/name`` and ``plugin:name`` forms the agent
    sometimes types (falls back to the last path or colon segment). ``None`` when absent."""
    if skill in mapping:
        return mapping[skill]
    tail = skill.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return mapping.get(tail)


def skill_body_map(skills_dir: str | os.PathLike[str] = DEFAULT_SKILLS_DIR) -> dict[str, Optional[str]]:
    """``{frontmatter name: SKILL.md body text}`` (text after the frontmatter block, stripped),
    used to make sure no frozen tool result smuggles the skill body into the WITHOUT arm.
    Empty when the directory is absent."""
    bodies: dict[str, Optional[str]] = {}
    for name, path in skill_files(skills_dir).items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = _skill_body(text)
        bodies[name] = body if body else None
    return bodies


def _skill_body(skill_md: str) -> str:
    text = skill_md.lstrip("\ufeff")
    if not text.startswith("---"):
        return text.strip()
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text.strip()
    return "\n".join(lines[end + 1:]).strip()


def default_redactor(names: Optional[Iterable[str]] = None) -> Redactor:
    """``redact.redact`` bound to one deny-list (loaded once). Works whether this module was
    imported as ``skilluminati.mine`` or loaded straight from its file path."""
    redact_mod = None
    try:
        from . import redact as redact_mod  # type: ignore[no-redef]
    except ImportError:
        try:
            from skilluminati import redact as redact_mod  # type: ignore[no-redef]
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "skilluminati_redact_for_mine", Path(__file__).with_name("redact.py"))
            if spec is None or spec.loader is None:
                raise ImportError("cannot load redact.py beside mine.py")
            redact_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(redact_mod)
    deny = list(names) if names is not None else redact_mod.load_names()

    def _redact(text: Optional[str]) -> tuple[Optional[str], dict[str, int]]:
        return redact_mod.redact(text, names=deny)

    return _redact


def _scan_turn(msgs: list[sqlite3.Row], load_index: int) -> tuple[bool, bool]:
    """``(tool_dependent, answered)`` for the agent turn containing ``msgs[load_index]``.

    Walks back to the previous real user turn and forward to the final answer (an assistant
    message with text and no tool calls). Any non-skill tool call in that window makes the
    answer tool-dependent. If a new user turn arrives before a final answer, ``answered`` is
    False.
    """
    start = load_index
    while start > 0 and not _is_real_user_turn(msgs[start - 1]):
        start -= 1
    tool_dependent = False
    answered = False
    for row in msgs[start:]:
        if row["_compressed_summary"]:
            continue
        if _is_real_user_turn(row) and row["id"] != msgs[start]["id"]:
            break
        if row["role"] == "assistant":
            calls = _parse_tool_calls(row["tool_calls"])
            if any(c["name"] not in SKILL_TOOLS for c in calls):
                tool_dependent = True
            if not calls and _text_of(row["content"]).strip():
                answered = True
                break
        elif row["role"] == "tool" and row["tool_name"] and row["tool_name"] not in SKILL_TOOLS:
            tool_dependent = True
    return tool_dependent, answered


def _sessions_for_skill(conn: sqlite3.Connection, skill: str, cols: str) -> list[sqlite3.Row]:
    """The chats that may have opened ``skill``, found through Hermes's search index (under a second
    on a large history, where scanning every message takes about 24 s). The miner still checks every
    message in these chats, so the index only narrows the search. Falls back to the slow scan when
    the index is missing."""
    tail = skill.rsplit("/", 1)[-1].rsplit(":", 1)[-1].replace('"', "")
    try:
        return conn.execute(
            f"SELECT {cols} FROM sessions WHERE id IN ("
            "  SELECT m.session_id FROM messages_fts f JOIN messages m ON m.id = f.rowid"
            "  WHERE messages_fts MATCH ? AND m.role = 'assistant'"
            ") ORDER BY started_at", (f'tool_calls:"skill_view" AND tool_calls:"{tail}"',)).fetchall()
    except sqlite3.Error:
        return conn.execute(
            f"SELECT {cols} FROM sessions WHERE id IN ("
            "  SELECT DISTINCT session_id FROM messages"
            "  WHERE role = 'assistant' AND tool_calls LIKE '%skill_view%'"
            ") ORDER BY started_at").fetchall()


def mine(
    db_path: str | os.PathLike[str] = DEFAULT_DB,
    skill: Optional[str] = None,
    limit: Optional[int] = None,
    skills_dir: str | os.PathLike[str] = DEFAULT_SKILLS_DIR,
    mode: str = "strict",
    redactor: Optional[Redactor] = None,
) -> list[dict[str, Any]]:
    """Every skill load as a task dict (kept and dropped alike; see ``kept``/``drop_reason``).

    ``skill`` filters to one skill (frontmatter name or the name the agent typed). ``limit``
    caps the number of tasks returned. Use :func:`kept` to keep only replayable tasks.

    ``mode`` is ``"strict"`` (default; tool-dependent turns dropped, output unchanged) or
    ``"frozen"`` (tool-dependent turns kept with their tool traffic as ``frozen_messages``;
    see the module docstring). In frozen mode every frozen tool result and tool-call argument
    is passed through ``redactor`` (``(text) -> (text, counts)``; default: ``redact.redact``
    with the deny-list). ``user_turn`` and ``system_prompt`` are returned raw in both modes,
    as before; callers redact them when writing.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    name_map = skill_name_map(skills_dir)
    skill_bodies = skill_body_map(skills_dir) if mode != "strict" else None
    if mode != "strict" and redactor is None:
        redactor = default_redactor()
    conn = connect(db_path)
    try:
        cols = ", ".join(SESSION_COLUMNS)
        if skill:
            sessions = _sessions_for_skill(conn, skill, cols)
        else:
            sessions = conn.execute(
                f"SELECT {cols} FROM sessions WHERE id IN ("
                "  SELECT DISTINCT session_id FROM messages"
                "  WHERE role = 'assistant' AND tool_calls LIKE '%skill_view%'"
                ") ORDER BY started_at"
            ).fetchall()
        prompt_cache: dict[str, Optional[str]] = {}
        tasks: list[dict[str, Any]] = []
        for session in sessions:
            tasks.extend(_tasks_for_session(
                conn, session, name_map, prompt_cache, skill,
                mode=mode, redactor=redactor, skill_bodies=skill_bodies,
            ))
            if limit is not None and len(tasks) >= limit:
                return tasks[:limit]
        return tasks
    finally:
        conn.close()


def kept(tasks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tasks if t.get("kept")]


def summary(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per-skill counts: total loads, kept, dropped by reason. Plus overall totals."""
    per_skill: dict[str, dict[str, Any]] = {}
    totals = {"loads": 0, "kept": 0, "dropped": {}}
    for t in tasks:
        s = per_skill.setdefault(t["skill"], {"loads": 0, "kept": 0, "dropped": {}})
        s["loads"] += 1
        totals["loads"] += 1
        if t.get("kept"):
            s["kept"] += 1
            totals["kept"] += 1
        else:
            reason = t.get("drop_reason") or "unknown"
            s["dropped"][reason] = s["dropped"].get(reason, 0) + 1
            totals["dropped"][reason] = totals["dropped"].get(reason, 0) + 1
    ordered = dict(sorted(per_skill.items(), key=lambda kv: (-kv[1]["kept"], -kv[1]["loads"], kv[0])))
    return {"skills": ordered, "totals": totals, "skill_count": len(ordered)}
