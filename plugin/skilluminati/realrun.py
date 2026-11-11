"""In plain words: runs the test on a real skill. For each past use it gets two answers (with and
without the skill), has the Owl pick the better one blind, and adds the picks up into one verdict.
It runs in the background after /skilluminati <skill> or Ask the Owl.

Real-skill verdicts: thin glue over ``replay``, ``judge``, ``stats``, ``redact``
and ``llm`` for mined, redacted tasks of real skills (strict tasks from ``tasks.jsonl`` plus frozen
tasks from ``tasks_frozen.jsonl``). Nothing here duplicates the runner's maths; it reuses
``runner.Budget``, ``runner.UsageLimit``, ``runner.totals`` and ``runner.position_bias_summary``.

Decisions:
* Tasks per skill: strict first (file order), then frozen or context (file order), capped at
  ``TASKS_PER_SKILL`` (the latest 12 uses, as ``mine.find_tasks_for_run`` picks them).
* Judge: purpose mode, purpose statement = ``judge.purpose_statement(SKILL.md)``, both orders,
  disagreement = tie. No scripted checks exist for real skills, so the judge decides every pair.
* Verdict: ``verdict.lean`` (helps / no difference / hurts / not enough, strong or likely). The
  Wilson numbers (``stats.verdict``) are kept beside it for "Show the working".
* Privacy: every outgoing string is re-run through ``redact.redact`` (regex patterns plus the
  optional names list) and re-checked for listed names (``NameLeak``) before the call. That
  re-check uses the same names list, so it catches nothing the list does not hold: with no list,
  no names are removed. The tracked log holds metadata only; raw prompts and answers go to a
  private directory (files 0600).
* A/A: once for the configuration, ``AA_TASKS`` tasks drawn round-robin across the skills,
  WITHOUT vs WITHOUT on the subject model. Stored as a noise check; it never changes the answer.
* Budget: ``plan()`` is computed and printed before the first call; over the cap, frozen tasks are
  trimmed first (round-robin from the skill with the most frozen tasks).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import cost as sb_cost, judge as sb_judge, llm as sb_llm, paths as sb_paths, recommend as sb_recommend, redact as sb_redact, replay, runner as sb_runner, stats, verdict as sb_verdict

TASKS_PER_SKILL = 12
AA_TASKS = 10
CALLS_PER_PAIR = 4  # 2 subject + 2 judge
HARD_CAP = sb_runner.HARD_CAP
#: Pins used by the dev harness that produced the published showcase run. The plugin never uses them:
#: ``RealRunConfig`` defaults to the user's active provider and model (``None``).
SUB_PROVIDER = sb_runner.SUB_PROVIDER
SUBJECT_MODEL = sb_runner.OPUS
JUDGE_MODEL = sb_runner.HAIKU

#: Words in a judge reason (on a pair the WITH arm lost) that count as "cites a harm". Documented
#: here so the "Gets in the way" split is a rule in code, not a reading.
HARM_WORDS = ("wrong", "incorrect", "error", "missing", "omits", "omitted", "fails", "ignores", "ignored",
              "contradict", "violates", "misread", "misstates", "invents", "fabricat", "worse", "harm",
              "does not answer", "did not answer", "off-topic", "irrelevant", "refuses", "unhelpful")

#: Appended to the SYSTEM prompt of BOTH arms (and both A/A arms) before a subject call. The mined
#: sessions had tools; the replay offers none, and some provider routes drop a tool_use-only turn
#: as empty text (smoke run: 3 of 3 WITH answers empty, 0 chars, 1 to 84 output tokens). Without
#: this note the WITH arm, which has just "loaded" a skill and wants to act on it, loses every pair
#: for a harness reason. Symmetric: same text, same position, in both arms; the judge never sees
#: the system prompt. Probed live on 24 calls across strict/frozen x with/without: 24/24 non-empty.
REPLAY_NOTE = replay.REPLAY_NOTE  # one text for real runs and the self-test

#: Tracked-log fields. No text field is ever written here (asserted by ``tracked_row``).
TRACKED_FIELDS = ("index", "ts", "role", "skill", "mode", "arm", "task_hash", "requested_provider", "requested_model",
                  "provider", "model", "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                  "latency_s", "ok", "error_type", "error")
TEXT_FIELDS_FORBIDDEN = ("text", "messages", "prompt", "response_text", "content", "answer", "user_turn", "system_prompt",
                         "purpose", "reason")


class NameLeak(AssertionError):
    """A deny-list name is present in an outgoing prompt."""


# ── skills on disk ───────────────────────────────────────────────────────────

def find_skill_file(skills_dir: Path, name: str) -> Optional[Path]:
    """The SKILL.md under ``skills_dir`` (recursive) whose frontmatter ``name:`` equals ``name``.
    The folder name is not consulted."""
    for path in sorted(Path(skills_dir).rglob("SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = replay.parse_frontmatter(text)
        if str(fm.get("name", "")).strip() == name:
            return path
    return None



def skills_dirs() -> list[Path]:
    """Every folder Hermes keeps skills in, in Hermes's own order (yours first, then the ones that
    ship with Hermes). Outside Hermes (the tests), just your folder."""
    try:
        from agent.skill_utils import get_all_skills_dirs
        dirs = [Path(d) for d in get_all_skills_dirs()]
    except Exception:
        dirs = []
    return dirs or [sb_cost.default_skills_dir()]


def skill_home(name: str) -> Path:
    """The skills folder that holds ``name``. Hermes keeps skills in more than one place: your own
    (``~/.hermes/skills``) and the ones that ship with Hermes (systematic-debugging
    ships with Hermes, so a lookup in your folder alone failed). Asks Hermes for its list, in its own
    order; outside Hermes (the tests) only your folder is searched."""
    mine_dir = sb_cost.default_skills_dir()
    for d in skills_dirs():
        if find_skill_file(d, name) is not None:
            return d
    return mine_dir

# ── task selection ───────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def task_hash(task: dict[str, Any]) -> str:
    """Stable, non-reversible task id for logs: sha256 of session id + load message id, 12 hex."""
    raw = f"{task.get('session_id', '')}|{task.get('load_message_id', '')}|{task.get('skill', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def select_tasks(strict: Iterable[dict[str, Any]], frozen: Iterable[dict[str, Any]], skill: str,
                 cap: int = TASKS_PER_SKILL) -> list[dict[str, Any]]:
    """Strict tasks first (file order), then frozen or context (file order), ``cap`` in total. Each
    task is tagged ``mode`` = ``strict`` | ``frozen`` | ``context``."""
    out: list[dict[str, Any]] = []
    for t in strict:
        if t.get("skill") == skill and t.get("kept", True):
            out.append({**t, "mode": "strict"})
    for t in frozen:
        if t.get("skill") == skill and t.get("kept", True):
            out.append({**t, "mode": "context" if t.get("replay_mode") == "context" else "frozen"})
    return out[:cap]


def select_aa_tasks(per_skill: dict[str, list[dict[str, Any]]], n: int = AA_TASKS) -> list[dict[str, Any]]:
    """``n`` tasks drawn round-robin across the skills, in skill order."""
    pools = {s: list(ts) for s, ts in per_skill.items()}
    out: list[dict[str, Any]] = []
    i = 0
    while len(out) < n and any(i < len(p) for p in pools.values()):
        for s in per_skill:
            if i < len(pools[s]) and len(out) < n:
                out.append(pools[s][i])
        i += 1
    return out


def plan(per_skill: dict[str, list[dict[str, Any]]], n_aa: int, cap: int = HARD_CAP) -> dict[str, Any]:
    """Call plan; trims frozen tasks (from the skill with the most frozen tasks first) until the
    total fits ``cap``. Mutates ``per_skill`` lists in place and reports what was trimmed."""
    trimmed: dict[str, int] = {s: 0 for s in per_skill}
    while True:
        total = sum(len(ts) for ts in per_skill.values()) * CALLS_PER_PAIR + n_aa * CALLS_PER_PAIR
        if total <= cap:
            break
        candidates = [(sum(1 for t in ts if t["mode"] != "strict"), s) for s, ts in per_skill.items()]
        n_frozen, victim = max(candidates)
        if n_frozen == 0:
            raise sb_runner.CapExceeded(f"planned {total} calls exceeds hard cap {cap} with no frozen task left to trim")
        ts = per_skill[victim]
        idx = max(i for i, t in enumerate(ts) if t["mode"] != "strict")
        del ts[idx]
        trimmed[victim] += 1
    per = {s: {"tasks": len(ts), "strict": sum(1 for t in ts if t["mode"] == "strict"),
               "frozen": sum(1 for t in ts if t["mode"] != "strict"), "calls": len(ts) * CALLS_PER_PAIR}
           for s, ts in per_skill.items()}
    return {"per_skill": per, "aa": {"tasks": n_aa, "calls": n_aa * CALLS_PER_PAIR},
            "total": sum(p["calls"] for p in per.values()) + n_aa * CALLS_PER_PAIR, "cap": cap,
            "calls_per_pair": CALLS_PER_PAIR, "trimmed_frozen": trimmed}


# ── privacy ──────────────────────────────────────────────────────────────────

def redact_messages(messages: list[dict[str, Any]], names: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every string in ``messages`` (contents, tool-call arguments, nested JSON strings) re-run
    through ``redact.redact``. Returns the new list and merged counts."""
    counts = {k: 0 for k in sb_redact.PLACEHOLDERS}

    def walk(v: Any) -> Any:
        nonlocal counts
        if isinstance(v, str):
            out, c = sb_redact.redact(v, names=names)
            counts = sb_redact.merge_counts(counts, c)
            return out
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(messages), counts


def assert_no_names(messages: list[dict[str, Any]], names: list[str]) -> None:
    """Raise ``NameLeak`` if any listed name (whole word, case-insensitive) is in the payload. A re-check
    of the names list only; with an empty list it checks nothing."""
    pat = sb_redact._names_pattern(names)
    if pat is None:
        return
    m = pat.search(replay.payload_text(messages))
    if m:
        raise NameLeak(f"deny-list name present in outgoing prompt (length {len(m.group(0))})")


def tracked_row(row: dict[str, Any]) -> dict[str, Any]:
    """The metadata-only log row: fixed fields, no text. Raises if a text field sneaks in."""
    out = {k: row.get(k) for k in TRACKED_FIELDS}
    for k in out:
        if k in TEXT_FIELDS_FORBIDDEN:
            raise AssertionError(f"text field {k!r} in tracked log")
    if out.get("error"):
        out["error"] = str(out["error"])[:300]
    return out


# ── verdict split ────────────────────────────────────────────────────────────

def reasons_citing_harm(pairs: list[dict[str, Any]]) -> int:
    """Count of WITH-losing pairs whose judge reasons (either order) contain a ``HARM_WORDS`` term."""
    n = 0
    for p in pairs:
        if p.get("outcome") != "loss":
            continue
        reasons = " ".join(_reason(o) for o in p.get("judge", {}).get("orders", [])).lower()
        if any(w in reasons for w in HARM_WORDS):
            n += 1
    return n


JUDGE_CHAT_CHARS = 3_000


def judge_request(messages: list[dict[str, Any]]) -> str:
    """What the judge reads as the request: the last user message, and for a longer chat the
    words just before it (user and assistant text only, the last ``JUDGE_CHAT_CHARS``)."""
    said = [m for m in messages if m["role"] in ("user", "assistant") and m.get("content")]
    latest = said.pop()["content"] if said and said[-1]["role"] == "user" else ""
    earlier = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in said)[-JUDGE_CHAT_CHARS:]
    return f"(Earlier in the chat:)\n{earlier}\n\n(Latest message:)\n{latest}" if earlier else latest


def _reason(order: dict[str, Any]) -> str:
    try:
        return str(json.loads(order.get("text") or "{}").get("reason", ""))
    except (ValueError, AttributeError):
        return str(order.get("text") or "")


#: Per-order judge fields kept in tracked summaries. ``text``/``note`` (the judge's sentence, which
#: paraphrases task and answer content) stay in the run's private pairs file only.
JUDGE_ORDER_FIELDS = ("first", "ok", "raw_winner", "winner")
JUDGE_FIELDS = ("outcome", "agreed", "first_picks", "decisive_judgements", "parse_failures", "call_failures", "mode")


def tracked_pair(p: dict[str, Any]) -> dict[str, Any]:
    """A pair as it may appear in a tracked report: ids, outcome and judge counts only. No request,
    answer, judge sentence, error text or session id."""
    j = p.get("judge") or None
    if j:
        j = {**{k: j.get(k) for k in JUDGE_FIELDS},
             "orders": [{k: o.get(k) for k in JUDGE_ORDER_FIELDS} for o in j.get("orders", [])]}
    return {"cell": p.get("cell"), "skill": p.get("skill"), "mode": p.get("mode"), "task_hash": p.get("task_hash"),
            "outcome": p.get("outcome"), "error_kind": ("guard" if str(p.get("error", "")).startswith("guard") else
                                                        "subject" if p.get("error") else None),
            "arms": {a: {"ok": r.get("ok"), "text_chars": len(r.get("text") or ""), "error_type": r.get("error_type")}
                     for a, r in (p.get("arms") or {}).items()},
            "judge": j}


def wlt(pairs: list[dict[str, Any]]) -> dict[str, int]:
    c = {"wins": 0, "losses": 0, "ties": 0, "errors": 0}
    for p in pairs:
        o = p.get("outcome")
        c["wins" if o == "win" else "losses" if o == "loss" else "ties" if o == "tie" else "errors"] += 1
    return c


def verdict_block(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    c = wlt(pairs)
    v = stats.verdict(c["wins"], c["losses"], c["ties"])
    return {**c, "n_pairs": len(pairs), "verdict": v.verdict, "low": round(v.low, 4), "high": round(v.high, 4),
            "rate": v.rate, "n_decisive": v.n_decisive, "tasks_to_settle": v.tasks_to_settle}


# ── config ───────────────────────────────────────────────────────────────────

@dataclass
class RealRunConfig:
    skills: tuple[str, ...]
    skills_dir: Path
    fixtures_dir: Path
    strict_path: Path
    frozen_path: Path
    names_path: Optional[Path]
    tracked_dir: Path
    private_dir: Path  # the run's private folder (never tracked)
    #: ``None`` = the user's active provider / model (the product default). The dev harness pins them.
    provider: Optional[str] = None
    subject_model: Optional[str] = None
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None  # None = same as ``provider``
    cap: int = HARD_CAP
    tasks_per_skill: int = TASKS_PER_SKILL
    aa_tasks: int = AA_TASKS
    #: Room for a full answer. Opus counts its hidden thinking against this, so 800 cut answers short.
    subject_max_tokens: int = 6000
    judge_max_tokens: int = 200
    #: Longest visible answer before it counts as runaway (packs.answer_limits raises it for web pages).
    runaway_max_chars: int = sb_llm.RUNAWAY_MAX_CHARS
    subject_temperature: Optional[float] = None
    judge_temperature: float = 0.0
    timeout_s: float = 180.0
    workers: int = 3
    judge_mode: str = sb_runner.JUDGE_MODE_NEW_RUNS
    #: Heavy flag comparison set = the user's enabled skills. ``None`` scans ``skills_dir``
    #: and drops the skills named in config ``skills.disabled`` (read only when ``skills_dir`` is the
    #: live Hermes skills folder); a tuple (even empty) overrides that list.
    disabled_skills: Optional[tuple[str, ...]] = None
    #: The tests to run, already picked (a live run passes them here). ``None`` reads the two files.
    tasks: Optional[list[dict[str, Any]]] = None


def heavy_comparison_set(skills_dir: Path, subject_tokens: dict[str, int],
                         disabled: Optional[Iterable[str]] = None) -> dict[str, int]:
    """``{skill name: estimated load tokens}`` for every enabled skill under ``skills_dir``: the Heavy
    flag's comparison set for a live run. The skills under test are added when they are not found
    (so a verdict always has its own skill in the set). Load tokens are of the full SKILL.md text."""
    if disabled is None:
        disabled = (sb_cost.read_disabled()[0] if Path(skills_dir) == sb_cost.default_skills_dir() else ())
    tokens = {s["name"]: sb_cost.estimate_tokens(s["text"]) for s in sb_cost.scan_skills(skills_dir, disabled)}
    for name, t in subject_tokens.items():
        tokens.setdefault(name, t)
    return tokens


# ── runner ───────────────────────────────────────────────────────────────────

class RealRunner:
    def __init__(self, llm: Any, cfg: RealRunConfig, *, log: Callable[[str], None] = print) -> None:
        self.llm, self.cfg, self.log = llm, cfg, log
        self.tracked_dir, self.private_dir = Path(cfg.tracked_dir), Path(cfg.private_dir)
        self.tracked_dir.mkdir(parents=True, exist_ok=True)
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self.calls_path = self.tracked_dir / "calls.jsonl"
        self.raw_path = self.private_dir / "prompts.jsonl"
        self.pairs_path = self.private_dir / "pairs.jsonl"
        # Append, never truncate: an earlier run's log is kept. Raw prompts and answers are 0600.
        for p in (self.calls_path, self.raw_path, self.pairs_path):
            sb_paths.private_file(p)
        self._pairs_start = self.pairs_path.stat().st_size
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.names = sb_redact.load_names(cfg.names_path) if cfg.names_path else sb_redact.load_names()
        self.rubric = sb_judge.load_rubric(cfg.fixtures_dir, cfg.judge_mode)  # hash asserted before any call
        self.rubric_path = sb_judge.rubric_paths(cfg.fixtures_dir, cfg.judge_mode)[0]
        self.skill_texts: dict[str, str] = {}
        self.skill_paths: dict[str, str] = {}
        self.skill_redaction: dict[str, dict[str, int]] = {}
        self.skill_load_tokens: dict[str, int] = {}
        self.purposes: dict[str, str] = {}
        for s in cfg.skills:
            path = find_skill_file(cfg.skills_dir, s)
            if path is None:
                raise FileNotFoundError(f"no SKILL.md with frontmatter name {s!r} under {cfg.skills_dir}")
            raw = path.read_text(encoding="utf-8", errors="replace")
            self.skill_load_tokens[s] = sb_cost.estimate_tokens(raw)  # the raw text a skill_view returns
            text, counts = sb_redact.redact(raw, names=self.names)
            # Relative to the skills folder: no home directory or user name reaches a prompt or report.
            try:
                rel = path.relative_to(cfg.skills_dir).as_posix()
            except ValueError:
                rel = f"{s}/SKILL.md"
            self.skill_texts[s], self.skill_paths[s], self.skill_redaction[s] = text or "", rel, counts
            self.purposes[s] = sb_judge.purpose_statement(self.skill_texts[s])
            if not self.purposes[s].strip():
                raise ValueError(f"skill {s!r} has no description / Use when line; purpose judge needs one")
        self.heavy_set = heavy_comparison_set(cfg.skills_dir, self.skill_load_tokens, cfg.disabled_skills)
        if cfg.tasks is not None:
            strict = [t for t in cfg.tasks if not t.get("frozen_messages")]
            frozen = [{**t, "replay_mode": t.get("replay_mode") or "frozen"} for t in cfg.tasks if t.get("frozen_messages")]
        else:
            strict, frozen = load_jsonl(cfg.strict_path), load_jsonl(cfg.frozen_path)
        self.per_skill = {s: select_tasks(strict, frozen, s, cfg.tasks_per_skill) for s in cfg.skills}
        self.aa_tasks = select_aa_tasks(self.per_skill, cfg.aa_tasks)
        self.plan = plan(self.per_skill, len(self.aa_tasks), cfg.cap)
        self.budget = sb_runner.Budget(cap=cfg.cap, planned=self.plan["total"])
        self.stopped: Optional[dict[str, str]] = None
        self.stopped_reason: Optional[str] = None
        self.guard_notes: dict[str, dict[str, int]] = {}
        self.redaction_counts: dict[str, int] = {k: 0 for k in sb_redact.PLACEHOLDERS}

    # -- plan -------------------------------------------------------------------

    def print_plan(self) -> None:
        p = self.plan
        per = ", ".join(f"{s}: {v['tasks']} tasks ({v['strict']} strict + {v['frozen']} frozen) = {v['calls']} calls"
                        for s, v in p["per_skill"].items())
        self.log(f"[plan] {per}; A/A {p['aa']['tasks']} tasks = {p['aa']['calls']} calls; total {p['total']} "
                 f"<= cap {p['cap']}; trimmed frozen {p['trimmed_frozen']}")
        (self.tracked_dir / "plan.json").write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")

    # -- calls ------------------------------------------------------------------

    def _call(self, messages: list[dict[str, Any]], *, model: Optional[str], max_tokens: int, temperature: Optional[float],
              meta: dict[str, Any], label: str, provider: Optional[str] = None) -> sb_llm.CallRecord:
        assert_no_names(messages, self.names)  # re-check of the deny-list names only (see assert_no_names)
        with self._lock:
            index = self.budget.reserve()
        rec = sb_llm.complete(self.llm, messages, provider=provider if provider is not None else self.cfg.provider,
                              model=model, max_tokens=max_tokens,
                              temperature=temperature, timeout=self.cfg.timeout_s, purpose=label)
        if meta.get("role") == "subject":  # the cap is not honoured below the plugin: check every answer
            sb_llm.check_subject(rec, max_tokens, f"{meta.get('skill')} task {meta.get('task_hash')} "
                                                  f"({meta.get('arm')})", self.cfg.runaway_max_chars)
        row = {"index": index, "ts": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"), **meta,
               "requested_provider": rec.requested_provider, "requested_model": rec.requested_model,
               "provider": rec.provider, "model": rec.model, "input_tokens": rec.input_tokens,
               "output_tokens": rec.output_tokens, "total_tokens": rec.total_tokens,
               "cache_read_tokens": rec.cache_read_tokens, "latency_s": rec.latency_s, "ok": rec.ok,
               "error_type": rec.error_type, "error": rec.error}
        tracked = tracked_row(row)
        with self._lock:
            self.calls.append(tracked)
            with self.calls_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(tracked) + "\n")
            with self.raw_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**tracked, "label": label, "messages": messages, "response_text": rec.text},
                                    ensure_ascii=False) + "\n")
        if not rec.ok and sb_runner.USAGE_LIMIT_RE.search(rec.error or ""):
            raise sb_runner.UsageLimit(rec.error)
        if not rec.ok and rec.error_type == "ModelMismatch":
            raise sb_llm.ModelMismatch(rec.error)  # the fallback call is already billed: stop now
        if rec.error_type == sb_llm.RUNAWAY:  # logged above (text cut); stop the run, never judge it
            raise sb_llm.runaway_stop(rec)
        return rec

    def _subject(self, messages: list[dict[str, Any]], meta: dict[str, Any], label: str) -> sb_llm.CallRecord:
        return self._call(messages, model=self.cfg.subject_model, max_tokens=self.cfg.subject_max_tokens,
                          temperature=self.cfg.subject_temperature, meta={**meta, "role": "subject"}, label=label)

    def judge_guard(self, messages: list[dict[str, Any]], skill: str, system_prompt: str) -> str:
        """Blindness check. Hard: system prompt is the rubric, no arm label, no whole skill text, no
        subject system prompt. Per-line skill-body guard: tripped only when a body line sits in the
        harness part (purpose + request); a body line inside an ANSWER is the subject's own output
        and is counted as ``answer_quotes_skill`` rather than excluded. Returns a note key."""
        forbidden = [self.skill_texts[skill], system_prompt]
        try:
            sb_judge.assert_blind(messages, self.rubric.text, forbidden, skill_bodies=[self.skill_texts[skill]],
                                  purpose=self.purposes[skill])
            return "clean"
        except AssertionError as exc:
            if "skill body line" not in str(exc):
                raise
        # Re-check with the answers blanked: the harness part must still be clean.
        user = messages[1]["content"]
        head = user.split("\n\nANSWER 1:\n", 1)[0]
        harness = [messages[0], {"role": "user", "content": head}]
        sb_judge.assert_blind(harness, self.rubric.text, forbidden, skill_bodies=[self.skill_texts[skill]],
                              purpose=self.purposes[skill])
        return "answer_quotes_skill"

    def _judge_fn(self, skill: str, system_prompt: str, meta: dict[str, Any]):
        def call(messages: list[dict[str, Any]], label: str) -> sb_llm.CallRecord:
            note = self.judge_guard(messages, skill, system_prompt)
            with self._lock:
                self.guard_notes.setdefault(skill, {})[note] = self.guard_notes.setdefault(skill, {}).get(note, 0) + 1
            return self._call(messages, model=self.cfg.judge_model, max_tokens=self.cfg.judge_max_tokens,
                              temperature=self.cfg.judge_temperature, meta={**meta, "role": "judge"}, label=label,
                              provider=self.cfg.judge_provider)
        return call

    # -- arms -------------------------------------------------------------------

    def build(self, task: dict[str, Any], skill: str) -> tuple[dict[str, list[dict[str, Any]]], str]:
        """Redacted arms for one task plus the redacted system prompt. Strict tasks use the task's
        own stored system prompt via ``replay.build_arms``; frozen via ``replay.build_frozen_arms``."""
        names = self.names
        system_prompt, c1 = sb_redact.redact(task["system_prompt"], names=names)
        user_turn, c2 = sb_redact.redact(task["user_turn"], names=names)
        counts = sb_redact.merge_counts(c1, c2)
        skill_md = self.skill_texts[skill]
        if task["mode"] in ("frozen", "context"):
            frozen, c3 = redact_messages(task["frozen_messages"], names)
            counts = sb_redact.merge_counts(counts, c3)
            arms = replay.build_frozen_arms(system_prompt or "", skill, skill_md, user_turn or "", frozen,
                                            task["load_position"], path=self.skill_paths[skill])
        else:
            arms = replay.build_arms(system_prompt or "", skill, skill_md, user_turn or "", path=self.skill_paths[skill])
        without_raw = sb_judge.raw_text(arms["without"])
        if replay._skill_body(skill_md) in without_raw:
            raise AssertionError("skill body in WITHOUT arm")
        if replay.index_has_entry(arms["without"][0]["content"], skill):
            raise AssertionError("index entry in WITHOUT")
        for arm in arms.values():
            arm[0] = {"role": "system", "content": arm[0]["content"] + REPLAY_NOTE}  # symmetric, see REPLAY_NOTE
            assert_no_names(arm, names)
        with self._lock:
            self.redaction_counts = sb_redact.merge_counts(self.redaction_counts, counts)
        return arms, system_prompt or ""

    # -- one pair ---------------------------------------------------------------

    def _pair(self, task: dict[str, Any], skill: str, msgs_x: list[dict[str, Any]], msgs_y: list[dict[str, Any]],
              system_prompt: str, cell: str, label_x: str, label_y: str) -> dict[str, Any]:
        th = task_hash(task)
        meta = {"skill": skill, "mode": task["mode"], "task_hash": th}
        rec_x = self._subject(msgs_x, {**meta, "arm": label_x}, f"{cell} {th} {label_x}")
        rec_y = self._subject(msgs_y, {**meta, "arm": label_y}, f"{cell} {th} {label_y}")
        request = judge_request(msgs_x)  # the same text for both arms: the skill pair has no words
        pair: dict[str, Any] = {"cell": cell, "skill": skill, "mode": task["mode"], "task_hash": th,
                                "session_id": task.get("session_id"), "load_message_id": task.get("load_message_id"),
                                "user_turn": request, "arms": {label_x: rec_x.to_dict(), label_y: rec_y.to_dict()}}
        if not (rec_x.ok and rec_y.ok):
            pair.update(outcome="error", judge=None, error="subject call failed")
        else:
            try:
                judgement = sb_judge.judge_pair(self._judge_fn(skill, system_prompt, {**meta, "arm": "judge"}),
                                                self.rubric.text, request, rec_x.text, rec_y.text,
                                                mode=self.cfg.judge_mode, purpose=self.purposes[skill],
                                                label=f"{cell} {th} judge")
            except sb_llm.RunStop:
                raise
            except AssertionError as exc:  # blind check or names re-check tripped: never judged unblind, pair excluded
                pair.update(outcome="error", judge=None, error=f"guard: {str(exc)[:120]}")
            else:
                pair["judge"] = judgement.to_dict()
                pair["outcome"] = {"x": "win", "y": "loss", "tie": "tie"}[judgement.outcome]
        with self._lock, self.pairs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
        return pair

    def _run_pairs(self, jobs: list[Callable[[], dict[str, Any]]]) -> list[dict[str, Any]]:
        """Run the pairs on ``workers`` threads. On the first stop (``RunStop``) the stop flag is set,
        jobs not yet started are cancelled and jobs already started halt before their next call."""
        out: list[dict[str, Any]] = []
        stop = threading.Event()

        def guarded(job: Callable[[], dict[str, Any]]) -> dict[str, Any]:
            if stop.is_set():
                raise sb_llm.StopRequested(self.stopped_reason or "run stopped")
            try:
                return job()
            except sb_llm.RunStop as exc:
                self.stopped_reason = self.stopped_reason or f"{type(exc).__name__}: {exc}"
                stop.set()
                raise

        first: Optional[BaseException] = None
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.workers)) as ex:
            futures = [ex.submit(guarded, j) for j in jobs]
            for f in futures:
                try:
                    out.append(f.result())
                except sb_llm.RunStop as exc:
                    first = first or exc
                    stop.set()
                    for g in futures:
                        g.cancel()
        if first is not None:
            raise first
        return out

    # -- a skill ------------------------------------------------------------------

    def run_skill(self, skill: str) -> dict[str, Any]:
        started = time.monotonic()
        first_call = len(self.calls)
        jobs = []
        for task in self.per_skill[skill]:
            arms, sp = self.build(task, skill)
            jobs.append(lambda t=task, a=arms, s=sp: self._pair(t, skill, a["with"], a["without"], s, skill, "with", "without"))
        pairs = self._run_pairs(jobs)
        for p in pairs:
            self.log(f"  {skill} {p['mode']} {p['task_hash']}: {p['outcome']}")
        return self.summarise_skill(skill, pairs, time.monotonic() - started, self.calls[first_call:])

    def summarise_skill(self, skill: str, pairs: list[dict[str, Any]], seconds: float,
                        calls: list[dict[str, Any]]) -> dict[str, Any]:
        ok = [p for p in pairs if p["outcome"] != "error"]
        combined = verdict_block(ok)
        v = stats.verdict(combined["wins"], combined["losses"], combined["ties"])
        harm = reasons_citing_harm(ok)
        lean = sb_verdict.lean(combined["wins"], combined["losses"], combined["ties"])
        label = sb_verdict.LEAN_LABEL[lean["verdict"]]
        effect = "negative" if lean["verdict"] == sb_verdict.HURTS else "none"
        tot = sb_runner.totals(calls, seconds)
        heavy = sb_cost.heavy_flag(self.skill_load_tokens[skill], self.heavy_set.values())
        judge_stats = sb_runner._judge_stats(ok)
        first_share = (judge_stats["first_picks"] / judge_stats["decisive_judgements"]
                       if judge_stats["decisive_judgements"] else None)
        subj = [c for c in calls if c["role"] == "subject"]
        jud = [c for c in calls if c["role"] == "judge"]
        return {
            "skill": skill, "skill_path": self.skill_paths[skill], "purpose_chars": len(self.purposes[skill]),
            "skill_redaction_counts": self.skill_redaction[skill],
            "n_tasks": len(pairs), "n_errors": len(pairs) - len(ok),
            "strict": verdict_block([p for p in ok if p["mode"] == "strict"]),
            "frozen": verdict_block([p for p in ok if p["mode"] != "strict"]),
            "combined": combined,
            "harm_cited_pairs": harm, "effect": effect, "user_label": label, "lean": lean,
            # a cost flag beside the label, never part of it (label wording is verdict.py's).
            "heavy": heavy,
            # every result is a recommendation with caveats. Added beside the old fields, none removed.
            "recommendation": sb_recommend.recommend(
                label, wins=combined["wins"], losses=combined["losses"], ties=combined["ties"],
                low=combined["low"] if v.n_decisive else None, high=combined["high"] if v.n_decisive else None,
                tasks_to_settle=v.tasks_to_settle, heavy=heavy, verdict_model=self.cfg.subject_model, lean=lean,
                active_model=self.cfg.subject_model, job_first_share=first_share),
            "stamp": {"model": self.cfg.subject_model, "judge": self.cfg.judge_model, "judge_mode": self.cfg.judge_mode,
                      "date": dt.date.today().isoformat(), "test_type": "single-call replay"},
            "judge_stats": judge_stats, "guard_notes": self.guard_notes.get(skill, {}),
            "cost": {**tot, "subject_calls": len(subj), "judge_calls": len(jud),
                     "subject_input_tokens": sum(c["input_tokens"] for c in subj),
                     "subject_output_tokens": sum(c["output_tokens"] for c in subj),
                     "judge_input_tokens": sum(c["input_tokens"] for c in jud),
                     "judge_output_tokens": sum(c["output_tokens"] for c in jud),
                     "sum_latency_minutes": round(sum(c["latency_s"] for c in calls) / 60, 2),
                     "workers": self.cfg.workers, "pass_15min": tot["minutes"] <= 15.0},
            "pairs": [tracked_pair(p) for p in pairs],
        }

    def run_aa(self) -> dict[str, Any]:
        started = time.monotonic()
        first_call = len(self.calls)
        jobs = []
        for task in self.aa_tasks:
            skill = task["skill"]
            arms, sp = self.build(task, skill)
            without = arms["without"]
            jobs.append(lambda t=task, s=skill, w=without, p=sp: self._pair(t, s, w, list(w), p, "aa", "without_a", "without_b"))
        pairs = self._run_pairs(jobs)
        ok = [p for p in pairs if p["outcome"] != "error"]
        a = stats.aa_noise([p["outcome"] for p in ok])
        return {"cell": "aa", "model": self.cfg.subject_model, "n_pairs": len(pairs), "n_errors": len(pairs) - len(ok),
                "wins": a.wins, "losses": a.losses, "ties": a.ties, "n_decisive": a.n_decisive, "rate": a.rate,
                "low": round(a.low, 4), "high": round(a.high, 4), "tie_share": a.tie_share, "noisy": a.noisy,
                "verdict": stats.verdict(a.wins, a.losses, a.ties).verdict,
                "skills_drawn": {s: sum(1 for t in self.aa_tasks if t["skill"] == s) for s in self.cfg.skills},
                "judge_stats": sb_runner._judge_stats(ok),
                "cost": sb_runner.totals(self.calls[first_call:], time.monotonic() - started),
                "pairs": [{"skill": p["skill"], "task_hash": p["task_hash"], "outcome": p["outcome"]} for p in pairs]}

    # -- evidence ---------------------------------------------------

    def write_evidence(self, skill: str, summary: dict[str, Any]) -> Optional[str]:
        """For a Drop-label skill, the 2 most decisive pairs (WITH lost, both orders agreed, longest
        reasons) with task, both answers and judge reasons, to the private dir only."""
        if summary["combined"]["verdict"] != stats.DROP:
            return None
        with self.pairs_path.open(encoding="utf-8") as fh:
            fh.seek(self._pairs_start)
            pairs = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
        mine = [p for p in pairs if p["skill"] == skill and p["cell"] == skill and p["outcome"] == "loss"]
        mine.sort(key=lambda p: -sum(len(_reason(o)) for o in p["judge"]["orders"]))
        ev = [{"task_hash": p["task_hash"], "mode": p["mode"], "user_turn": p["user_turn"],
               "with_answer": p["arms"]["with"]["text"], "without_answer": p["arms"]["without"]["text"],
               "judge_reasons": [_reason(o) for o in p["judge"]["orders"]]} for p in mine[:2]]
        path = sb_paths.private_file(self.private_dir / f"evidence-{sb_paths.safe_name(skill)}.json")
        path.write_text(json.dumps(ev, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)

    # -- whole run ----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        self.budget.check_plan()
        self.print_plan()
        rubric_before = sb_judge.sha256_text(self.rubric_path.read_text(encoding="utf-8"))
        results: dict[str, Any] = {
            "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "provider": self.cfg.provider, "subject_model": self.cfg.subject_model, "judge_model": self.cfg.judge_model,
            "judge_mode": self.cfg.judge_mode, "skills": list(self.cfg.skills), "plan": self.plan,
            "tasks_per_skill_cap": self.cfg.tasks_per_skill, "samples_per_arm": 1, "workers": self.cfg.workers,
            "deny_list_names_loaded": len(self.names), "per_skill": {}, "aa": None, "evidence_files": {},
            "heavy_comparison": {"ratio": sb_cost.HEAVY_RATIO, "skills_in_set": len(self.heavy_set),
                                 "median_tokens": sb_cost.median_tokens(self.heavy_set.values()),
                                 "label": sb_cost.LABEL},
            "stopped_early": None,
        }
        try:
            for skill in self.cfg.skills:
                self.log(f"[skill] {skill}")
                summary = self.run_skill(skill)
                results["per_skill"][skill] = summary
                results["evidence_files"][skill] = self.write_evidence(skill, summary)
                self.log(f"  -> {summary['user_label']} {summary['combined']['wins']}/{summary['combined']['losses']}/"
                         f"{summary['combined']['ties']} [{summary['combined']['low']}, {summary['combined']['high']}] "
                         f"{summary['cost']['minutes']} min")
            self.log("[aa]")
            results["aa"] = self.run_aa()
        except sb_llm.RunStop as exc:
            self.stopped = {"reason": type(exc).__name__, "error": str(exc)}
            self.log(f"[stop] {type(exc).__name__}: {exc}")
        except Exception as exc:  # keep partial results, say so
            self.stopped = {"reason": f"unexpected {type(exc).__name__}", "error": str(exc)[:500]}
            self.log(f"[stop] unexpected {type(exc).__name__}: {exc}")
        results["stopped_early"] = self.stopped
        rubric_after = sb_judge.sha256_text(self.rubric_path.read_text(encoding="utf-8"))
        results["rubric"] = {"path": self.rubric.path, "mode": self.cfg.judge_mode, "sha256_expected": self.rubric.sha256,
                             "sha256_before": rubric_before, "sha256_after": rubric_after,
                             "unchanged": rubric_before == rubric_after == self.rubric.sha256}
        cells = list(results["per_skill"].values()) + ([results["aa"]] if results["aa"] else [])
        results["position_bias"] = sb_runner.position_bias_summary(cells) if cells else None
        results["totals"] = sb_runner.totals(self.calls, time.monotonic() - started)
        results["redaction_counts_outgoing"] = self.redaction_counts
        results["guard_notes"] = self.guard_notes
        results["paraphrases"] = {}
        results["suggestions"] = {}
        return results


__all__ = ["heavy_comparison_set", "TASKS_PER_SKILL", "AA_TASKS", "CALLS_PER_PAIR", "HARM_WORDS", "REPLAY_NOTE", "TRACKED_FIELDS", "NameLeak", "RealRunConfig",
           "RealRunner", "find_skill_file", "load_jsonl", "task_hash", "select_tasks", "select_aa_tasks", "plan",
           "redact_messages", "assert_no_names", "tracked_row", "tracked_pair", "reasons_citing_harm", "judge_request",
           "verdict_block"]
