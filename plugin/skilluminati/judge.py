"""In plain words: this is the Owl. It shows the Owl two answers without saying which one had the
skill, asks again with the answers swapped, and reads back its pick. It runs during every test,
once for each pair of answers.

Blind pairwise judge, in two modes.

``blind`` (the original judge): the judge sees the user request and two answers, nothing else: no
skill text, no arm labels, no provenance. The rubric is ``fixtures/rubric.md`` used verbatim as
the system prompt, and its SHA-256 must equal ``fixtures/rubric.sha256`` before any call is
made (``load_rubric`` raises otherwise).

``purpose``: the judge additionally sees a PURPOSE
STATEMENT, which is the skill's frontmatter ``description`` verbatim (plus a "Use when..." line
from the body if one exists and is not already in the description; see ``purpose_statement``).
It still never sees the skill body, the system prompt or which arm is which. The rubric is
``fixtures/rubric_purpose.md`` with its own frozen hash ``fixtures/rubric_purpose.sha256``.

In both modes every pair is judged in both orders; the two orders must agree or the result is a
tie. Output is parsed as strict JSON; anything unparsable is a tie and is logged as such.

``harm``: a separate question,
not a winner pick. The judge sees ONLY the request and the two answers (same blind layout as
``blind``, never a purpose statement) and says, for each answer compared to the other, whether
anything in it is wrong, missing or worse for what the user asked. Rubric ``fixtures/rubric_harm.md``
with frozen hash ``fixtures/rubric_harm.sha256``. Asked in both orders; an answer counts as harmed
only when it is flagged and the other is not, in BOTH orders.

The LLM is injected as ``call(messages, label) -> record`` where ``record`` has ``ok``,
``text`` and ``error`` (``skilluminati.llm.CallRecord`` fits). No I/O other than reading the
rubric; no Hermes imports.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

RUBRIC_FILENAME = "rubric.md"
RUBRIC_HASH_FILENAME = "rubric.sha256"
RUBRIC_PURPOSE_FILENAME = "rubric_purpose.md"
RUBRIC_PURPOSE_HASH_FILENAME = "rubric_purpose.sha256"
RUBRIC_HARM_FILENAME = "rubric_harm.md"
RUBRIC_HARM_HASH_FILENAME = "rubric_harm.sha256"

MODE_BLIND = "blind"
MODE_PURPOSE = "purpose"
MODE_HARM = "harm"
MODES = (MODE_BLIND, MODE_PURPOSE, MODE_HARM)
#: Modes that pick a winner (``judge_pair``). ``harm`` is a flag question (``judge_harm_pair``).
WINNER_MODES = (MODE_BLIND, MODE_PURPOSE)

RUBRIC_FILES = {
    MODE_BLIND: (RUBRIC_FILENAME, RUBRIC_HASH_FILENAME),
    MODE_PURPOSE: (RUBRIC_PURPOSE_FILENAME, RUBRIC_PURPOSE_HASH_FILENAME),
    MODE_HARM: (RUBRIC_HARM_FILENAME, RUBRIC_HARM_HASH_FILENAME),
}

#: HARM rubric versions. ``v1`` is ``rubric_harm.md``; ``v2`` is the revision aimed at order sensitivity and at flagging an answer that
#: declines to invent a value. Each has its own frozen hash file. Same JSON output contract, so
#: ``parse_harm_verdict`` is shared. Which version the self-test uses is ``selftest.HARM_JUDGE``.
HARM_RUBRIC_VERSIONS = {
    "v1": (RUBRIC_HARM_FILENAME, RUBRIC_HARM_HASH_FILENAME),
    "v2": ("rubric_harm_v2.md", "rubric_harm_v2.sha256"),
}

WINNERS = ("1", "2", "tie")

#: Harness-added strings that must never appear in a judge prompt. The judge is blind to arms.
#: Answers go in verbatim, so a subject model that talks about its own tools ("I would need to
#: skill_view ...") is not a harness leak; ``mentions_tooling`` flags those for the report.
ARM_LABELS = ("WITH arm", "WITHOUT arm", "with-skill", "without-skill", "[with]", "[without]")

#: Words in an answer that hint at the subject's tool context (reported, not forbidden).
TOOLING_HINTS = ("skill_view", "<available_skills>", "available skills", "my skills", "load the skill", "loaded the skill")

#: Skill-body lines shorter than this are not checked by ``assert_blind``'s line guard: a line
#: such as ``## Bands`` or ``---`` is not evidence of a leak, while any real rule line is longer.
BODY_LINE_MIN_CHARS = 20


def mentions_tooling(answer: str) -> list[str]:
    """Which ``TOOLING_HINTS`` appear in an answer (case-insensitive)."""
    low = (answer or "").lower()
    return [h for h in TOOLING_HINTS if h.lower() in low]


class RubricHashError(AssertionError):
    """The rubric on disk does not match its frozen hash."""


@dataclass
class Rubric:
    text: str
    sha256: str
    path: str
    mode: str = MODE_BLIND


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"judge mode must be one of {MODES}, got {mode!r}")
    return mode


def rubric_paths(fixtures_dir: Path, mode: str = MODE_BLIND) -> tuple[Path, Path]:
    """``(rubric_path, hash_path)`` for ``mode``."""
    name, hash_name = RUBRIC_FILES[_check_mode(mode)]
    return Path(fixtures_dir) / name, Path(fixtures_dir) / hash_name


def read_rubric_hash(fixtures_dir: Path, mode: str = MODE_BLIND) -> str:
    return rubric_paths(fixtures_dir, mode)[1].read_text(encoding="utf-8").strip().lower()


def load_rubric(fixtures_dir: Path, mode: str = MODE_BLIND) -> Rubric:
    """Load the rubric for ``mode`` verbatim and assert its SHA-256 equals the frozen hash file.

    Raises ``RubricHashError`` on mismatch. Call this before any judge call; results produced
    under a different rubric hash are not comparable.
    """
    path, hash_path = rubric_paths(fixtures_dir, mode)
    return _load_checked(path, hash_path, mode)


def _load_checked(path: Path, hash_path: Path, mode: str) -> Rubric:
    text = path.read_text(encoding="utf-8")
    actual = sha256_text(text)
    expected = hash_path.read_text(encoding="utf-8").strip().lower()
    if actual != expected:
        raise RubricHashError(
            f"rubric hash mismatch: {path} sha256={actual} but {hash_path.name} says {expected}")
    return Rubric(text=text, sha256=actual, path=str(path), mode=mode)


def harm_rubric_paths(fixtures_dir: Path, version: str = "v1") -> tuple[Path, Path]:
    """``(rubric_path, hash_path)`` of HARM rubric ``version`` (``HARM_RUBRIC_VERSIONS``)."""
    if version not in HARM_RUBRIC_VERSIONS:
        raise ValueError(f"HARM rubric version must be one of {tuple(HARM_RUBRIC_VERSIONS)}, got {version!r}")
    name, hash_name = HARM_RUBRIC_VERSIONS[version]
    return Path(fixtures_dir) / name, Path(fixtures_dir) / hash_name


def load_harm_rubric(fixtures_dir: Path, version: str = "v1") -> Rubric:
    """HARM rubric ``version`` verbatim, its SHA-256 asserted against its own frozen hash file
    (raises ``RubricHashError`` on mismatch). ``v1`` is identical to ``load_rubric(.., MODE_HARM)``."""
    path, hash_path = harm_rubric_paths(fixtures_dir, version)
    return _load_checked(path, hash_path, MODE_HARM)


# ── purpose statement ────────────────────────────────────────────────────────

_USE_WHEN_RE = re.compile(r"^\s*(?:[-*>]\s*|#+\s*)?(?P<line>use when\b.*)$", re.IGNORECASE)


def split_frontmatter(skill_md: str) -> tuple[list[str], list[str]]:
    """``(frontmatter_lines, body_lines)`` of a SKILL.md, stdlib only. Frontmatter is the block
    between a leading ``---`` line and the next ``---`` line; without it everything is body."""
    text = (skill_md or "").lstrip("\ufeff")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return [], lines
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return [], lines
    return lines[1:end], lines[end + 1:]


def frontmatter_description(skill_md: str) -> str:
    """The ``description`` scalar from the frontmatter, verbatim (surrounding quotes stripped),
    or ``""`` when absent. Only top-level ``description: value`` lines are read; no PyYAML."""
    fm, _ = split_frontmatter(skill_md)
    for line in fm:
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        if key.strip() != "description":
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return ""


def purpose_statement(skill_md_text: str) -> str:
    """The skill's stated purpose: its frontmatter ``description`` verbatim, plus the first
    "Use when..." line from the body if one exists and is not already in the description.
    Returns ``""`` when the skill has no description and no such line."""
    description = frontmatter_description(skill_md_text)
    _, body = split_frontmatter(skill_md_text)
    use_when = ""
    for line in body:
        m = _USE_WHEN_RE.match(line)
        if m:
            use_when = m.group("line").strip()
            break
    if use_when and use_when.lower() not in description.lower():
        return f"{description}\n{use_when}".strip() if description else use_when
    return description


def forbidden_body_lines(skill_md_text: str, allowed: str = "") -> list[str]:
    """Every line of ``skill_md_text`` that must not appear in a judge prompt: the body lines and
    the frontmatter lines other than ``description``, stripped, of at least
    ``BODY_LINE_MIN_CHARS`` characters, minus any line that is part of ``allowed`` (the purpose
    statement, which legitimately carries the description and a "Use when..." line)."""
    fm, body = split_frontmatter(skill_md_text)
    out: list[str] = []
    for line in fm:
        s = line.strip()
        if s.startswith("description:"):
            continue
        if len(s) >= BODY_LINE_MIN_CHARS and s not in allowed:
            out.append(s)
    for line in body:
        s = line.strip()
        if len(s) >= BODY_LINE_MIN_CHARS and s not in allowed:
            out.append(s)
    return out


# ── prompt ───────────────────────────────────────────────────────────────────

#: Closes each answer. The Claude Agent SDK route appends a ``<system-reminder>``
#: block to the final user message; without a terminator it reads as part of Answer 2 and the
#: judge penalises it. The
#: markers are symmetric and carry no information about either answer's origin.
ANSWER_END = "(end of answer {n})"

PURPOSE_HEADER = "PURPOSE STATEMENT:"


def build_judge_messages(rubric_text: str, request: str, answer_1: str, answer_2: str, *,
                         purpose: Optional[str] = None) -> list[dict[str, Any]]:
    """System = rubric verbatim; user = the layout the rubric's "Input format" section defines,
    plus a symmetric end marker after each answer (see ``ANSWER_END``). Only the request and the
    two answers go in, preceded by the PURPOSE STATEMENT block when ``purpose`` is given
    (purpose mode). Nothing about how the answers were produced."""
    head = f"{PURPOSE_HEADER}\n{purpose}\n\n" if purpose else ""
    user = (f"{head}USER REQUEST:\n{request}\n\n"
            f"ANSWER 1:\n{answer_1}\n{ANSWER_END.format(n=1)}\n\n"
            f"ANSWER 2:\n{answer_2}\n{ANSWER_END.format(n=2)}")
    return [
        {"role": "system", "content": rubric_text},
        {"role": "user", "content": user},
    ]


def assert_blind(messages: list[dict[str, Any]], rubric_text: str, forbidden_texts: list[str], *,
                 skill_bodies: Optional[list[str]] = None, purpose: str = "") -> None:
    """Raise ``AssertionError`` if a judge prompt is not blind: the system prompt must be the
    rubric exactly, no arm label may appear, none of ``forbidden_texts`` (whole skill bodies,
    the system prompt) may appear anywhere in the payload, and, when ``skill_bodies`` (full
    SKILL.md texts) are given, no line of any of them other than the frontmatter description
    line (and a "Use when..." line carried by ``purpose``) may appear either."""
    if len(messages) != 2 or messages[0]["role"] != "system" or messages[1]["role"] != "user":
        raise AssertionError("judge prompt must be exactly [system, user]")
    if messages[0]["content"] != rubric_text:
        raise AssertionError("judge system prompt is not the rubric verbatim")
    # Raw contents, not JSON: json.dumps escapes newlines, which would hide a multi-line skill body.
    payload = raw_text(messages)
    for label in ARM_LABELS:
        if label in payload:
            raise AssertionError(f"judge prompt contains arm label {label!r}")
    for text in forbidden_texts:
        if text and text in payload:
            raise AssertionError("judge prompt contains skill text")
    for body in skill_bodies or []:
        for line in forbidden_body_lines(body, allowed=purpose or ""):
            if line in payload:
                raise AssertionError(f"judge prompt contains a skill body line: {line[:60]!r}")


def raw_text(messages: list[dict[str, Any]]) -> str:
    """Every string ``content`` joined with newlines, unescaped, for substring checks."""
    parts = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(str(p.get("text", "")) for p in c if isinstance(p, dict))
        parts.append(json.dumps(m.get("tool_calls", ""), ensure_ascii=False))
    return "\n".join(parts)


# ── parsing ──────────────────────────────────────────────────────────────────

def parse_verdict(text: str) -> tuple[Optional[str], str]:
    """``(winner, note)``. ``winner`` is ``"1"``, ``"2"`` or ``"tie"`` when ``text`` is exactly
    one JSON object with a valid ``winner``; otherwise ``None`` and ``note`` says why. Strict:
    no code fences, no surrounding prose."""
    if not isinstance(text, str) or not text.strip():
        return None, "empty response"
    try:
        obj = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"not JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "JSON is not an object"
    winner = obj.get("winner")
    if isinstance(winner, int) and winner in (1, 2):
        winner = str(winner)
    if winner not in WINNERS:
        return None, f"winner not in {WINNERS}: {winner!r}"
    return winner, ""


# ── pairwise judgement ───────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """One judge call. ``first`` names which answer (``"x"`` or ``"y"``) was shown as Answer 1."""

    first: str
    ok: bool
    raw_winner: Optional[str]  # "1" | "2" | "tie" | None (unparsable or call failed)
    winner: Optional[str]  # "x" | "y" | "tie" | None
    text: str
    note: str
    call: dict = field(default_factory=dict)


@dataclass
class PairJudgement:
    """Both orders plus the combined outcome. ``outcome`` is ``"x"``, ``"y"`` or ``"tie"``."""

    outcome: str
    agreed: bool
    orders: list[OrderResult]
    first_picks: int  # judgements (parsed, non-tie) that chose Answer 1
    decisive_judgements: int  # judgements that chose 1 or 2
    parse_failures: int
    call_failures: int
    mode: str = MODE_BLIND

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "agreed": self.agreed,
            "orders": [o.__dict__ for o in self.orders],
            "first_picks": self.first_picks,
            "decisive_judgements": self.decisive_judgements,
            "parse_failures": self.parse_failures,
            "call_failures": self.call_failures,
            "mode": self.mode,
        }


def _map_winner(raw: Optional[str], first: str) -> Optional[str]:
    if raw is None:
        return None
    if raw == "tie":
        return "tie"
    second = "y" if first == "x" else "x"
    return first if raw == "1" else second


def combine_orders(w1: Optional[str], w2: Optional[str]) -> tuple[str, bool]:
    """``(outcome, agreed)`` from the two orders' winners in x/y/tie terms. Disagreement,
    including one order unparsable, is a tie; ``agreed`` is True only when both orders parsed
    and named the same answer or both said tie."""
    if w1 is None or w2 is None:
        return "tie", False
    if w1 == w2:
        return w1, True
    return "tie", False


def judge_pair(call: Callable[[list[dict[str, Any]], str], Any], rubric_text: str, request: str,
               answer_x: str, answer_y: str, *, mode: str = MODE_BLIND, purpose: Optional[str] = None,
               label: str = "judge") -> PairJudgement:
    """Judge ``answer_x`` against ``answer_y`` in both orders.

    ``mode`` is ``"blind"`` (default; the original prompt) or ``"purpose"``, in
    which case ``purpose`` (the skill's purpose statement, see ``purpose_statement``) is required
    and goes into the prompt as the PURPOSE STATEMENT block. ``rubric_text`` must be the rubric
    for that mode. ``label`` prefixes the per-call log label passed to ``call``.

    Order 1 shows x as Answer 1; order 2 shows y as Answer 1. ``call`` is invoked twice. A call
    that fails or returns unparsable output counts as a tie for that order and is recorded.
    """
    _check_mode(mode)
    if mode not in WINNER_MODES:
        raise ValueError(f"judge_pair picks a winner; mode {mode!r} is a flag question, use judge_harm_pair")
    if mode == MODE_PURPOSE and not (purpose or "").strip():
        raise ValueError("purpose mode needs a non-empty purpose statement")
    if mode == MODE_BLIND and purpose:
        raise ValueError("blind mode must not carry a purpose statement")
    orders: list[OrderResult] = []
    for first, (a1, a2) in (("x", (answer_x, answer_y)), ("y", (answer_y, answer_x))):
        messages = build_judge_messages(rubric_text, request, a1, a2, purpose=purpose if mode == MODE_PURPOSE else None)
        rec = call(messages, f"{label} order{1 if first == 'x' else 2}")
        ok = bool(getattr(rec, "ok", False))
        text = getattr(rec, "text", "") or ""
        if ok:
            raw, note = parse_verdict(text)
        else:
            raw, note = None, f"call failed: {getattr(rec, 'error', '')}"
        orders.append(OrderResult(
            first=first, ok=ok, raw_winner=raw, winner=_map_winner(raw, first), text=text, note=note,
            call=rec.to_dict() if hasattr(rec, "to_dict") else {},
        ))
    outcome, agreed = combine_orders(orders[0].winner, orders[1].winner)
    return PairJudgement(
        outcome=outcome,
        agreed=agreed,
        orders=orders,
        first_picks=sum(1 for o in orders if o.raw_winner == "1"),
        decisive_judgements=sum(1 for o in orders if o.raw_winner in ("1", "2")),
        parse_failures=sum(1 for o in orders if o.ok and o.raw_winner is None),
        call_failures=sum(1 for o in orders if not o.ok),
        mode=mode,
    )


# ── harm question ──────────────────────────────────────────────

HARM_KEYS = ("harm_1", "harm_2")


def parse_harm_verdict(text: str) -> tuple[Optional[dict[str, Any]], str]:
    """``(verdict, note)``. ``verdict`` is ``{"harm_1": bool, "harm_2": bool, "reason_1": str,
    "reason_2": str}`` when ``text`` is exactly one JSON object whose ``harm_1`` and ``harm_2``
    are JSON booleans; otherwise ``None`` and ``note`` says why. Strict: no code fences, no
    surrounding prose. Reasons are optional and coerced to strings."""
    if not isinstance(text, str) or not text.strip():
        return None, "empty response"
    try:
        obj = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"not JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "JSON is not an object"
    out: dict[str, Any] = {}
    for key in HARM_KEYS:
        val = obj.get(key)
        if not isinstance(val, bool):
            return None, f"{key} is not a JSON boolean: {val!r}"
        out[key] = val
    for key in ("reason_1", "reason_2"):
        val = obj.get(key, "")
        out[key] = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    return out, ""


@dataclass
class HarmOrderResult:
    """One HARM call. ``first`` names which answer (``"x"`` or ``"y"``) was shown as Answer 1.
    ``harm_x`` / ``harm_y`` are the flags mapped back to x and y; ``None`` when the call failed
    or the output was unparsable."""

    first: str
    ok: bool
    harm_x: Optional[bool]
    harm_y: Optional[bool]
    reason_x: str
    reason_y: str
    text: str
    note: str
    call: dict = field(default_factory=dict)


@dataclass
class HarmPairJudgement:
    """Both orders of the HARM question plus the combined flags.

    ``harm_x``: x flagged and y not, in BOTH orders (when x is the WITH answer this is the
    HARM-judge harm). ``harm_y``: the converse (``harm_plain`` when y is the WITHOUT answer).
    ``flipped``: the pair of flags differed between the two orders (position sensitivity).
    ``parsed``: both orders parsed; when False, both combined flags are False.
    """

    harm_x: bool
    harm_y: bool
    flipped: bool
    parsed: bool
    orders: list[HarmOrderResult]
    parse_failures: int
    call_failures: int
    mode: str = MODE_HARM

    def to_dict(self) -> dict[str, Any]:
        return {
            "harm_x": self.harm_x,
            "harm_y": self.harm_y,
            "flipped": self.flipped,
            "parsed": self.parsed,
            "orders": [o.__dict__ for o in self.orders],
            "parse_failures": self.parse_failures,
            "call_failures": self.call_failures,
            "mode": self.mode,
        }


def combine_harm_orders(o1: tuple[Optional[bool], Optional[bool]],
                        o2: tuple[Optional[bool], Optional[bool]]) -> tuple[bool, bool, bool, bool]:
    """``(harm_x, harm_y, flipped, parsed)`` from the two orders' ``(harm_x, harm_y)`` flags.
    An answer is harmed only when it is flagged and the other is not, in both orders. Any
    ``None`` (failed or unparsable order) means no harm either way and ``parsed`` False."""
    if any(v is None for v in (*o1, *o2)):
        return False, False, False, False
    harm_x = bool(o1[0] and not o1[1] and o2[0] and not o2[1])
    harm_y = bool(o1[1] and not o1[0] and o2[1] and not o2[0])
    return harm_x, harm_y, o1 != o2, True


def judge_harm_pair(call: Callable[[list[dict[str, Any]], str], Any], rubric_text: str, request: str,
                    answer_x: str, answer_y: str, *, label: str = "harm") -> HarmPairJudgement:
    """Ask the HARM question about ``answer_x`` and ``answer_y`` in both orders.

    The prompt is the blind layout (request + two answers, no purpose statement); ``rubric_text``
    must be the harm rubric. Order 1 shows x as Answer 1; order 2 shows y as Answer 1. ``call``
    is invoked twice. A call that fails or returns unparsable output is recorded and makes the
    pair unflagged (no harm can be claimed from one order).
    """
    orders: list[HarmOrderResult] = []
    for first, (a1, a2) in (("x", (answer_x, answer_y)), ("y", (answer_y, answer_x))):
        messages = build_judge_messages(rubric_text, request, a1, a2)
        rec = call(messages, f"{label} order{1 if first == 'x' else 2}")
        ok = bool(getattr(rec, "ok", False))
        text = getattr(rec, "text", "") or ""
        if ok:
            parsed, note = parse_harm_verdict(text)
        else:
            parsed, note = None, f"call failed: {getattr(rec, 'error', '')}"
        if parsed is None:
            hx = hy = None
            rx = ry = ""
        elif first == "x":
            hx, hy, rx, ry = parsed["harm_1"], parsed["harm_2"], parsed["reason_1"], parsed["reason_2"]
        else:
            hx, hy, rx, ry = parsed["harm_2"], parsed["harm_1"], parsed["reason_2"], parsed["reason_1"]
        orders.append(HarmOrderResult(
            first=first, ok=ok, harm_x=hx, harm_y=hy, reason_x=rx, reason_y=ry, text=text, note=note,
            call=rec.to_dict() if hasattr(rec, "to_dict") else {},
        ))
    harm_x, harm_y, flipped, parsed_both = combine_harm_orders(
        (orders[0].harm_x, orders[0].harm_y), (orders[1].harm_x, orders[1].harm_y))
    return HarmPairJudgement(
        harm_x=harm_x, harm_y=harm_y, flipped=flipped, parsed=parsed_both, orders=orders,
        parse_failures=sum(1 for o in orders if o.ok and o.harm_x is None),
        call_failures=sum(1 for o in orders if not o.ok),
    )


__all__ = [
    "ARM_LABELS",
    "TOOLING_HINTS",
    "BODY_LINE_MIN_CHARS",
    "MODE_BLIND",
    "MODE_PURPOSE",
    "MODE_HARM",
    "MODES",
    "WINNER_MODES",
    "HARM_KEYS",
    "HarmOrderResult",
    "HarmPairJudgement",
    "parse_harm_verdict",
    "combine_harm_orders",
    "judge_harm_pair",
    "RUBRIC_FILES",
    "HARM_RUBRIC_VERSIONS",
    "harm_rubric_paths",
    "load_harm_rubric",
    "PURPOSE_HEADER",
    "mentions_tooling",
    "ANSWER_END",
    "Rubric",
    "RubricHashError",
    "OrderResult",
    "PairJudgement",
    "rubric_paths",
    "load_rubric",
    "read_rubric_hash",
    "sha256_text",
    "split_frontmatter",
    "frontmatter_description",
    "purpose_statement",
    "forbidden_body_lines",
    "build_judge_messages",
    "assert_blind",
    "raw_text",
    "parse_verdict",
    "combine_orders",
    "judge_pair",
]
