"""In plain words: builds the two versions of each past request, one with the skill and one without,
exactly as Hermes would have sent them. It runs for every task in a test, just before the model
answers.

Replay message builder (pure functions, no I/O, no Hermes imports).

Hermes loads a skill in two places: an index line inside ``<available_skills>`` in the system
prompt, and a ``skill_view(name)`` tool call whose result carries the SKILL.md text. A replay
reproduces both arms of that from one captured task:

* WITH arm: system prompt (index intact) + first user turn + a synthetic assistant
  ``skill_view`` tool call + the tool result (Hermes's JSON shape), then the model answers.
* WITHOUT arm: system prompt with the target skill's index entry removed + first user turn.

The tool result mimics ``tools/skills_tool.py::skill_view`` key for key so the model sees what
it would have seen live. Message dicts are OpenAI chat-completions shaped, which is what
``ctx.llm.complete`` and the Hermes provider adapters accept.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

SKILL_VIEW_TOOL = "skill_view"

#: Appended to the SYSTEM prompt of BOTH arms (and both A/A arms) before every subject call, in real
#: runs and the self-test alike. A replay offers no tools. Without this note the model sometimes
#: answers with a tool call only (the Claude Agent SDK route drops it as empty text), or writes a fake
#: ``skill_view`` call as text and repeats it until the output limit (the self-test runaway: 128,000
#: output tokens, one answer of 209,673 characters). Symmetric: same text, same position, both arms;
#: the judges never see the system prompt.
REPLAY_NOTE = ("\n\n[Replay note: no tools are available in this session and none will be. Do not call or "
               "describe tool calls. Give your complete final answer as plain text now.]")


def with_replay_note(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of ``messages`` with ``REPLAY_NOTE`` appended to the system message (index 0)."""
    out = list(messages)
    out[0] = {**out[0], "content": out[0]["content"] + REPLAY_NOTE}
    return out
DEFAULT_TOOL_CALL_ID = "toolu_skilluminati_skill_view_01"

_INDEX_ROW_RE = re.compile(r"^(?P<indent>\s*)-\s+(?P<name>[^:\n]+?)(?::\s*(?P<desc>.*))?\s*$")
_NAMES_ONLY_RE = re.compile(r"^(?P<indent>\s*)(?P<category>[^:\n]+?)\s+\[names only\]:\s*(?P<names>.*)$")


# ── frontmatter ──────────────────────────────────────────────────────────────

def parse_frontmatter(skill_md: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)`` from a SKILL.md. Minimal YAML: ``key: value`` scalars and
    ``key: [a, b]`` flow lists. Unknown shapes are kept as raw strings. Never raises."""
    text = skill_md.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fm: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line or line.startswith((" ", "\t")):
            continue
        key, _, raw = line.partition(":")
        value: Any = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        fm[key.strip()] = value
    return fm, "\n".join(lines[end + 1:])


# ── index editing ────────────────────────────────────────────────────────────

def index_has_entry(system_prompt: str, skill_name: str) -> bool:
    """True when ``skill_name`` has a row (or a names-only mention) in the skills index."""
    return any(_row_names(line) and skill_name in _row_names(line) for line in system_prompt.split("\n"))


def _row_names(line: str) -> list[str]:
    row = _INDEX_ROW_RE.match(line)
    if row:
        return [row.group("name").strip()]
    names_only = _NAMES_ONLY_RE.match(line)
    if names_only:
        return [n.strip() for n in names_only.group("names").split(",") if n.strip()]
    return []


def remove_index_entry(system_prompt: str, skill_name: str) -> str:
    """System prompt with ``skill_name``'s index entry removed.

    Handles the two row shapes ``_render_skills_index`` emits: a ``- name: description`` row
    (dropped) and a ``category [names only]: a, b, c`` line (the name is removed from the list;
    the line is dropped when it empties). Matching is on the exact name; a skill called ``git``
    does not remove ``github``. Lines outside the index are untouched, except that no other line
    is a row for this exact name anyway.
    """
    out: list[str] = []
    for line in system_prompt.split("\n"):
        row = _INDEX_ROW_RE.match(line)
        if row and row.group("name").strip() == skill_name:
            continue
        names_only = _NAMES_ONLY_RE.match(line)
        if names_only:
            names = [n.strip() for n in names_only.group("names").split(",") if n.strip()]
            if skill_name in names:
                remaining = [n for n in names if n != skill_name]
                if not remaining:
                    continue
                line = f"{names_only.group('indent')}{names_only.group('category')} [names only]: {', '.join(remaining)}"
        out.append(line)
    return "\n".join(out)


# ── skill_view result ────────────────────────────────────────────────────────

def skill_view_result(skill_name: str, skill_md: str, *, path: Optional[str] = None,
                      skill_dir: Optional[str] = None) -> dict[str, Any]:
    """The dict ``skill_view`` returns for a plain, ready skill with no linked files.

    Key order follows ``tools/skills_tool.py`` (success, name, description, tags,
    related_skills, content, path, skill_dir, linked_files, usage_hint, then the readiness
    fields). ``content`` is the full SKILL.md including frontmatter, as Hermes serves it.
    """
    fm, _body = parse_frontmatter(skill_md)
    name = str(fm.get("name") or skill_name)
    tags = fm.get("tags") or []
    related = fm.get("related_skills") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(related, str):
        related = [t.strip() for t in related.split(",") if t.strip()]
    return {
        "success": True,
        "name": name,
        "description": str(fm.get("description") or ""),
        "tags": list(tags),
        "related_skills": list(related),
        "content": skill_md,
        "path": path or f"{skill_name}/SKILL.md",
        "skill_dir": skill_dir,
        "linked_files": None,
        "usage_hint": None,
        "required_environment_variables": [],
        "required_commands": [],
        "missing_required_environment_variables": [],
        "missing_credential_files": [],
        "missing_required_commands": [],
        "setup_needed": False,
        "setup_skipped": False,
        "readiness_status": "available",
    }


def skill_view_result_json(skill_name: str, skill_md: str, **kw: Any) -> str:
    """``skill_view``'s wire form: ``json.dumps(..., ensure_ascii=False)``."""
    return json.dumps(skill_view_result(skill_name, skill_md, **kw), ensure_ascii=False)


# ── arms ─────────────────────────────────────────────────────────────────────

def build_with_arm(system_prompt: str, skill_name: str, skill_md: str, user_turn: str, *,
                   tool_call_id: str = DEFAULT_TOOL_CALL_ID, path: Optional[str] = None,
                   skill_dir: Optional[str] = None) -> list[dict[str, Any]]:
    """Messages for the WITH arm. The index stays intact; the skill arrives via a synthetic
    ``skill_view`` call and its tool result, so the next model turn is the answer."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_turn},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": SKILL_VIEW_TOOL, "arguments": json.dumps({"name": skill_name})},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": SKILL_VIEW_TOOL,
            "content": skill_view_result_json(skill_name, skill_md, path=path, skill_dir=skill_dir),
        },
    ]


def build_without_arm(system_prompt: str, skill_name: str, user_turn: str) -> list[dict[str, Any]]:
    """Messages for the WITHOUT arm: index entry removed, no tool call, user turn only."""
    return [
        {"role": "system", "content": remove_index_entry(system_prompt, skill_name)},
        {"role": "user", "content": user_turn},
    ]


def build_arms(system_prompt: str, skill_name: str, skill_md: str, user_turn: str,
               **kw: Any) -> dict[str, list[dict[str, Any]]]:
    """``{"with": [...], "without": [...]}`` for one task."""
    return {
        "with": build_with_arm(system_prompt, skill_name, skill_md, user_turn, **kw),
        "without": build_without_arm(system_prompt, skill_name, user_turn),
    }


# ── frozen-tools arms ────────────────────────────────────────────────────────

def _skill_body(skill_md: str) -> str:
    """The SKILL.md text after the frontmatter block, stripped (the part only the WITH arm may
    carry). Without frontmatter the whole text is the body."""
    _fm, body = parse_frontmatter(skill_md)
    return body.strip()


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


def _frozen_text(messages: list[dict[str, Any]]) -> str:
    """Every string a model reads from a message list, with JSON layers decoded: contents,
    tool-call names and arguments. Used for the leak assertion so JSON escaping cannot hide a
    match."""
    chunks: list[str] = []
    _collect_strings(messages, chunks)
    return "\n".join(chunks)


def text_contains(haystack: str, needle: str) -> bool:
    """``needle in haystack`` tolerant of JSON escaping and whitespace differences: also tries
    the JSON-escaped forms of ``needle`` and a whitespace-collapsed comparison."""
    if not needle:
        return False
    if needle in haystack:
        return True
    for escaped in (json.dumps(needle, ensure_ascii=False)[1:-1], json.dumps(needle)[1:-1]):
        if escaped in haystack:
            return True
    squash = re.compile(r"\s+")
    return squash.sub(" ", needle).strip() in squash.sub(" ", haystack)


def has_skill_view_call(messages: list[dict[str, Any]], skill_name: str) -> bool:
    """True when any assistant message carries a ``skill_view`` call for ``skill_name``."""
    for m in messages:
        for call in m.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict) or fn.get("name") != SKILL_VIEW_TOOL:
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            if isinstance(args, dict) and str(args.get("name", "")).strip() == skill_name:
                return True
    return False


def build_frozen_arms(system_prompt: str, skill_name: str, skill_md: str, user_turn: str,
                      frozen_messages: list[dict[str, Any]], load_position: int, *,
                      tool_call_id: str = DEFAULT_TOOL_CALL_ID, path: Optional[str] = None,
                      skill_dir: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    """``{"with": [...], "without": [...]}`` for a frozen-tools task.

    ``frozen_messages`` are the original turn's tool-call and tool-result messages (already
    redacted, with the target skill's own ``skill_view`` pair removed by the miner) and
    ``load_position`` is the index in that list where the pair sat. Both arms replay every
    frozen message verbatim; the WITH arm re-inserts the synthetic ``skill_view`` call and
    result at ``load_position`` and keeps the index entry, the WITHOUT arm has no such pair and
    the index entry removed. The caller offers the model NO tools, so the next model turn is
    the answer from frozen context in both arms.

    Raises ``AssertionError`` if the WITHOUT payload carries the skill body or a ``skill_view``
    call for ``skill_name`` (the miner should have refused such a task), or if
    ``load_position`` is out of range.
    """
    frozen = [dict(m) for m in frozen_messages]
    if not 0 <= load_position <= len(frozen):
        raise AssertionError(f"load_position {load_position} outside 0..{len(frozen)}")
    pair = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": SKILL_VIEW_TOOL, "arguments": json.dumps({"name": skill_name})},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": SKILL_VIEW_TOOL,
            "content": skill_view_result_json(skill_name, skill_md, path=path, skill_dir=skill_dir),
        },
    ]
    with_arm = (
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_turn}]
        + frozen[:load_position] + pair + frozen[load_position:]
    )
    without_arm = (
        [{"role": "system", "content": remove_index_entry(system_prompt, skill_name)},
         {"role": "user", "content": user_turn}]
        + frozen
    )
    body = _skill_body(skill_md)
    if has_skill_view_call(without_arm, skill_name):
        raise AssertionError(f"WITHOUT arm carries a skill_view call for {skill_name!r}")
    if text_contains(_frozen_text(without_arm), body):
        raise AssertionError(f"WITHOUT arm carries the body of {skill_name!r}")
    if text_contains(payload_text(without_arm), body):
        raise AssertionError(f"WITHOUT arm payload carries the body of {skill_name!r}")
    return {"with": with_arm, "without": without_arm}


# ── payload inspection ───────────────────────────────────────────────────────

def payload_text(messages: list[dict[str, Any]]) -> str:
    """The exact request payload as one string (JSON, nothing escaped away), for leak checks."""
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def payload_contains(messages: list[dict[str, Any]], needle: str) -> bool:
    """True when ``needle`` appears anywhere in the serialised request messages."""
    return needle in payload_text(messages)
