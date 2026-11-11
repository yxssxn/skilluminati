"""In plain words: loads the built-in question sets, so a skill can be tested on a new install with no
history. It runs at the start of a test when your history has fewer than 6 usable uses of the
skill, and refuses any set whose fingerprints (SHA256SUMS) no longer match.

Built-in question sets ("packs") for a few well-known skills.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import replay

PACKS_DIR = Path(__file__).resolve().parent / "fixtures" / "packs"

#: Pack questions were never asked in a real chat, so they get this stand-in system prompt: one
#: line about the assistant plus a one-row skills list, in the shape Hermes writes it. The test
#: without the skill drops that row, exactly as it does for a question from your history.
SYSTEM_PROMPT = ("You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
                 "You are helpful, knowledgeable, and direct.\n\n"
                 "<available_skills>\n  - {name}: {description}\n</available_skills>")


#: How long an answer may be before the run treats it as runaway. Measured in trial runs on Opus:
#: the old 800-token cap stopped 2 of 2 text-only trials (thinking counts as output), and
#: frontend-design writes whole web pages (13,000-30,000 tokens, up to 28,000 characters, 2-5 minutes).
ANSWER_LIMITS = {"subject_max_tokens": 6000}
#: ``seconds_per_call`` is only for the time estimate: the test runs averaged 123 s per web page
#: answer and 7 s per judge call, so 65 s a call (56 calls took 61 minutes).
ANSWER_LIMITS_BY_SKILL = {"frontend-design": {"subject_max_tokens": 9000, "runaway_max_chars": 60_000, "timeout_s": 480.0,
                                              "seconds_per_call": 65.0}}


def answer_limits(skill: str) -> dict[str, Any]:
    """Run settings a pack needs (``RealRunConfig`` fields, plus ``seconds_per_call`` for the estimate)."""
    return {**ANSWER_LIMITS, **ANSWER_LIMITS_BY_SKILL.get(skill, {})}


class PackError(ValueError):
    """A pack file is missing, unreadable or does not match its fingerprint."""


def _folder(skill: str, root: Optional[Path] = None) -> Optional[Path]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", skill or ""):
        return None
    folder = (root or PACKS_DIR) / skill
    return folder if (folder / "tasks.jsonl").is_file() and (folder / "SHA256SUMS").is_file() else None


def _checked(folder: Path, name: str) -> str:
    """The text of ``folder/name`` after checking it against ``SHA256SUMS``."""
    lines = (folder / "SHA256SUMS").read_text().splitlines()
    expected = {file.strip(): digest for digest, file in (line.split(maxsplit=1) for line in lines if line.strip())}
    data = (folder / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected.get(name):
        raise PackError(f"{folder.name}/{name} does not match SHA256SUMS")
    return data.decode("utf-8")


def available(root: Optional[Path] = None) -> list[str]:
    """Names of the skills that have a pack."""
    base = root or PACKS_DIR
    return sorted(p.name for p in base.iterdir() if _folder(p.name, base)) if base.is_dir() else []


def skill_text(skill: str, *, root: Optional[Path] = None, skills_dir: Optional[Path] = None) -> Optional[str]:
    """The skill's SKILL.md: the copy in the pack if it has one, else the user's installed copy."""
    folder = _folder(skill, root)
    if folder and (folder / "SKILL.md").is_file():
        return _checked(folder, "SKILL.md")
    from . import mine, realrun  # the same lookup a history run uses
    path = realrun.find_skill_file(skills_dir or realrun.skill_home(skill), skill)
    return path.read_text(encoding="utf-8", errors="replace") if path else None


def tasks_for(skill: str, *, root: Optional[Path] = None, skills_dir: Optional[Path] = None) -> Optional[list[dict[str, Any]]]:
    """The pack's questions as tasks, in the same shape the history miner (mine.py) gives the runner,
    marked ``source = "pack"``. None when the skill has no pack; ``PackError`` if a file was edited."""
    folder = _folder(skill, root)
    if folder is None:
        return None
    rows = [json.loads(line) for line in _checked(folder, "tasks.jsonl").splitlines() if line.strip()]
    frontmatter, _ = replay.parse_frontmatter(skill_text(skill, root=root, skills_dir=skills_dir) or "")
    prompt = SYSTEM_PROMPT.format(name=skill, description=frontmatter.get("description") or skill)
    return [{
        "skill": skill, "skill_requested": skill, "skill_known": True, "source": "pack",
        "session_id": f"pack:{skill}:{row['id']}", "user_turn_message_id": 2 * n - 1, "load_message_id": 2 * n,
        "user_turn": row["user_turn"], "system_prompt": prompt,
        "system_prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "user_turns_before_load": 1, "flags": {"tool_dependent": False, "multi_turn": False},
        "kept": True, "drop_reason": None, "pack_task_id": row["id"],
    } for n, row in enumerate(rows, 1)]
