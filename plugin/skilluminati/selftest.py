"""In plain words: the Owl's trial. It plants a helpful, a harmful and an empty skill and checks that
the Owl tells them apart. It runs when you type /skilluminati selftest.

The self-test.

Four planted variants of one made-up skill (``fixtures/{helpful,poisoned,placebo,bloat}``) plus A/A
(WITHOUT against WITHOUT) over the 12 frozen tasks in ``fixtures/tasks.jsonl``. Per pair:

- 2 subject calls (WITH, WITHOUT; A/A: WITHOUT, WITHOUT), built by ``replay.build_arms``;
- the JOB question: ``judge.judge_pair`` in purpose mode, both orders (2 calls);
- the HARM question: ``judge.judge_harm_pair``, both orders (2 calls).

JOB per pair is ``runner.task_outcome`` (scripted override, else the purpose judge in both orders).
Labels come ONLY from ``verdict.cell_verdict``. This module
re-implements no rule: it runs the calls, stores the evidence, hands it to ``verdict.py`` and
compares the labels with the expected ones (the confusion matrix).

``/skilluminati selftest`` runs the helpful, poisoned and placebo skills plus A/A on your active
model through Hermes: the same model answers and judges, one call at a time, under the command's
call budget. A usage-limit error stops the run with partial results and the verbatim error; the
provider is never switched. The LLM facade is injected, so tests run everything on a fake with no
network. The remaining run modes and model tables below belong to the private dev harness.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from . import cost as sb_cost
from . import judge as sb_judge
from . import llm as sb_llm
from . import paths as sb_paths
from . import replay
from . import runner as sb_runner
from . import stats
from . import verdict as sb_verdict

# ── constants ────────────────────────────────────────────────────────────────

SUB_PROVIDER = sb_runner.SUB_PROVIDER
HAIKU = sb_runner.HAIKU
SONNET = "claude-sonnet-5-5"
JUDGE_MODEL = HAIKU  # the JOB judge (purpose mode)
#: Models a judge may be pinned to.
JUDGE_MODELS = (HAIKU, SONNET)

#: THE HARM judge setting, the one place it is read from.
#: ``model`` on ``SUB_PROVIDER``; ``rubric`` is a key of ``judge.HARM_RUBRIC_VERSIONS`` whose hash
#: file is asserted before and after every run. The earlier setting was Haiku + v1 (rubric_harm.md).
#: The current setting was chosen on the TUNE half only (flip share 3/28, zero prefers-poisoned,
#: A/A harm 0/6).
HARM_JUDGE: dict[str, str] = {"model": SONNET, "rubric": "v2"}


def harm_judge_config(override: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The HARM judge config: ``HARM_JUDGE`` unless ``override`` is given (tuning only). Validated:
    the model must be in ``JUDGE_MODELS`` and the rubric a known version."""
    cfg = dict(override or HARM_JUDGE)
    if cfg.get("model") not in JUDGE_MODELS:
        raise ValueError(f"HARM judge model must be one of {JUDGE_MODELS}, got {cfg.get('model')!r}")
    if cfg.get("rubric") not in sb_judge.HARM_RUBRIC_VERSIONS:
        raise ValueError(f"HARM rubric must be one of {tuple(sb_judge.HARM_RUBRIC_VERSIONS)}, got {cfg.get('rubric')!r}")
    return {"model": cfg["model"], "rubric": cfg["rubric"]}
FREE_PROVIDER = "nous"
FREE_MODEL = "meituan/longcat-2.5:free"
FREE_COLUMN = "LongCat 2.5 (tested while free on Nous)"
HARD_CAP = sb_runner.HARD_CAP
SKILL_NAME = sb_runner.SKILL_NAME

VARIANTS = ("helpful", "poisoned", "placebo", "bloat")
AA = "aa"
ROWS = VARIANTS + (AA,)

#: ``active`` is the product label: the subject is whatever model the routes say (by default the
#: user's active model), so its id is only known from the run record, never from this table.
ACTIVE = "active"
MODEL_IDS = {"haiku": HAIKU, "longcat": FREE_MODEL, ACTIVE: ""}
MODEL_PROVIDERS = {"haiku": SUB_PROVIDER, "longcat": FREE_PROVIDER, ACTIVE: ""}
MODEL_COLUMNS = {"haiku": HAIKU, "longcat": FREE_COLUMN, ACTIVE: "your model"}

RUNS: dict[str, dict[str, Any]] = {
    # Dev-harness runs that produced the published proof table (Haiku subject, pinned routes).
    "a1": {"model_label": "haiku", "variants": ("helpful", "poisoned"), "aa": True},
    "a2": {"model_label": "haiku", "variants": ("placebo", "bloat"), "aa": False},
    "free": {"model_label": "longcat", "variants": ("helpful", "poisoned"), "aa": True},
    # Product runs behind ``/skilluminati selftest``: routes come from ``routes`` (default: active model).
    "quick": {"model_label": ACTIVE, "variants": ("helpful", "poisoned", "placebo"), "aa": False,
              "n_tasks": 6},
    "full": {"model_label": ACTIVE, "variants": VARIANTS, "aa": True},
}


def default_routes() -> dict[str, dict[str, Optional[str]]]:
    """Product routes: every role on the user's active provider and model (``None`` = active)."""
    return {role: {"provider": None, "model": None} for role in ("subject", "job", "harm")}


def clean_routes(routes: Optional[Mapping[str, Any]]) -> dict[str, dict[str, Optional[str]]]:
    """``routes`` with every role present; a missing role or blank field means the active one."""
    out = default_routes()
    for role in out:
        r = (routes or {}).get(role) or {}
        for k in ("provider", "model"):
            v = r.get(k) if isinstance(r, Mapping) else None
            out[role][k] = str(v).strip() if isinstance(v, str) and v.strip() else None
    return out


def route_name(route: Mapping[str, Optional[str]]) -> str:
    return route.get("model") or "active model"

CALLS_PER_PAIR = 6  # 2 subject + 2 JOB + 2 HARM

#: Expected labels. A/A uses the tolerance (verdict.AA_OK_LABELS).
NO_EFFECT = (sb_verdict.MODEL_DOES_THIS_ALREADY, sb_verdict.NOT_SURE_YET)
EXPECTED: dict[str, tuple[str, ...]] = {
    "helpful": (sb_verdict.EARNS_ITS_KEEP,),
    "poisoned": (sb_verdict.GETS_IN_THE_WAY,),
    "placebo": NO_EFFECT,
    # bloat is not a label of its own. Any verdict except "Gets in the way" passes, and the
    # Heavy flag must be set (``bloat_ok``). The Heavy half of the rule is not a label, so it lives
    # in ``bloat_ok`` and is spelled out in ``EXPECTED_TEXT``.
    "bloat": tuple(l for l in sb_verdict.LABELS if l != sb_verdict.GETS_IN_THE_WAY),
    AA: tuple(sb_verdict.AA_OK_LABELS),
}
#: Display text for the expected column where a label list is not the whole rule.
EXPECTED_TEXT: dict[str, str] = {"bloat": "any verdict except Gets in the way, plus Heavy"}
#: The 4 planted skills, the tasks, checks and frozen rubrics, bundled inside the plugin.
DEFAULT_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def expected_text(kind: str) -> str:
    return EXPECTED_TEXT.get(kind) or " or ".join(EXPECTED[kind])


def planted_load_tokens(fixtures_dir: Optional[Path] = None) -> Optional[dict[str, int]]:
    """Estimated load tokens (``cost.estimate_tokens`` of the full SKILL.md) of each planted variant,
    or None when the fixtures are not on disk. Reads files only; no calls."""
    root = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES
    try:
        return {v: sb_cost.estimate_tokens(sb_runner.load_skill(root, v)) for v in VARIANTS}
    except OSError:
        return None


def planted_heavy(fixtures_dir: Optional[Path] = None) -> Optional[dict[str, dict[str, Any]]]:
    """Heavy flag per planted variant; the comparison set is exactly the 4 planted skills (hermetic:
    never the user's own skills). None when the fixtures are not on disk."""
    tokens = planted_load_tokens(fixtures_dir)
    return None if tokens is None else sb_cost.heavy_flags(tokens)


def bloat_ok(label: Optional[str], heavy: Optional[Mapping[str, Any]]) -> bool:
    """Pass rule for the bloat row: Heavy is set AND the verdict is not "Gets in the way"."""
    return bool(heavy and heavy.get("flag") is True and label is not None and label != sb_verdict.GETS_IN_THE_WAY)


def heavy_false_positive_ok(heavy_by_variant: Optional[Mapping[str, Mapping[str, Any]]]) -> Optional[bool]:
    """True when no planted variant other than bloat is Heavy; None when Heavy could not be computed."""
    if not heavy_by_variant:
        return None
    return not any(h.get("flag") for k, h in heavy_by_variant.items() if k != "bloat")


def variant_match(kind: str, label: Optional[str], heavy: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """Does a cell meet its row's expectation? Bloat uses ``bloat_ok``; every other row is label membership."""
    if label is None:
        return None
    if kind == "bloat":
        return bloat_ok(label, heavy)
    return label in EXPECTED[kind]

SUBJECT_MAX_TOKENS = 600
JOB_MAX_TOKENS = 200
HARM_MAX_TOKENS = 300
JUDGE_TEMPERATURE = 0.0
SUBJECT_TEMPERATURE: Optional[float] = None  # provider default
TIMEOUT_S = 120.0

# Free tier.
FREE_MAX_IN_FLIGHT = 2
FREE_MAX_RETRIES = 3
FREE_BACKOFF_S = 2.0  # 2, 4, 8 s
FREE_FAIL_MAX = 0.20
#: The >20% rule is evaluated after every free-tier call once this many have been made, and once
#: more at the end of the run. Without a floor the first failed call would read as 100%.
FREE_FAIL_MIN_CALLS = 10
SMOKE_CALLS = 3
SMOKE_MESSAGES = [{"role": "user", "content": "Reply with the single word OK."}]


class FreeTierDropped(RuntimeError):
    """More than ``FREE_FAIL_MAX`` of the free model's calls failed; the tier stops."""


# ── plan ─────────────────────────────────────────────────────────────────────

def run_cells(run: str) -> list[str]:
    spec = RUNS[run]
    ml = spec["model_label"]
    return [f"{v}/{ml}" for v in spec["variants"]] + ([f"{AA}/{ml}"] if spec["aa"] else [])


def plan_calls(run: str, n_tasks: int, harm_judge: Optional[Mapping[str, str]] = None,
               routes: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Planned calls for one run. Per pair: 2 subject + 2 JOB + 2 HARM. Smoke calls (run C only)
    are listed separately and included in ``total``. ``by_model`` follows the HARM judge config, or
    the ``routes`` for a product run."""
    spec = RUNS[run]
    n_cells = len(run_cells(run))
    pairs = n_cells * n_tasks
    subject, job, harm = 2 * pairs, 2 * pairs, 2 * pairs
    smoke = SMOKE_CALLS if spec["model_label"] == "longcat" else 0
    ml = spec["model_label"]
    by_model: dict[str, int] = {}
    if ml == ACTIVE:
        r = clean_routes(routes)
        named = ((route_name(r["subject"]), subject), (route_name(r["job"]), job), (route_name(r["harm"]), harm))
    else:
        named = ((MODEL_IDS[ml], subject + smoke), (JUDGE_MODEL, job), (harm_judge_config(harm_judge)["model"], harm))
    for model, n in named:
        by_model[model] = by_model.get(model, 0) + n
    return {
        "run": run, "cells": run_cells(run), "n_tasks": n_tasks, "pairs": pairs,
        "subject_calls": subject, "job_calls": job, "harm_calls": harm, "smoke_calls": smoke,
        "batch": subject + job + harm, "total": subject + job + harm + smoke, "cap": HARD_CAP,
        "by_model": by_model,
    }


# ── free tier gate ───────────────────────────────────────────────────────────

def free_tier_allowed(llm_cfg: Optional[Mapping[str, Any]]) -> tuple[bool, str]:
    """``(allowed, reason)`` from ``plugins.entries.skilluminati.llm`` (read-only). The free model
    runs only when provider and model overrides are allowed and both the provider ``nous`` and the
    model id are on the plugin's literal allowlists."""
    if not isinstance(llm_cfg, Mapping):
        return False, "no plugins.entries.skilluminati.llm trust block"
    if not llm_cfg.get("allow_provider_override") or not llm_cfg.get("allow_model_override"):
        return False, "provider/model override not allowed for skilluminati"
    providers = [str(p).strip().lower() for p in (llm_cfg.get("allowed_providers") or [])]
    models = [str(m).strip().lower() for m in (llm_cfg.get("allowed_models") or [])]
    if FREE_PROVIDER not in providers:
        return False, f"provider {FREE_PROVIDER!r} not in allowed_providers"
    if FREE_MODEL not in models:
        return False, f"model {FREE_MODEL!r} not in allowed_models"
    return True, f"{FREE_PROVIDER} and {FREE_MODEL} on the skilluminati allowlist"


def smoke_verdict(records: Iterable[sb_llm.CallRecord]) -> tuple[bool, str]:
    """The free model passes the smoke only when every call succeeded and returned EXACTLY the
    pinned id. Any other id is a silent swap."""
    records = list(records)
    if len(records) < SMOKE_CALLS:
        return False, f"only {len(records)} of {SMOKE_CALLS} smoke calls made"
    for i, r in enumerate(records, 1):
        if not r.ok and r.error_type != "ModelMismatch":
            return False, f"smoke call {i} failed: {r.error_type}: {r.error}"
        if r.model != FREE_MODEL:
            return False, f"smoke call {i} returned model {r.model!r}, not {FREE_MODEL!r} (silent swap)"
    return True, f"{len(records)} of {SMOKE_CALLS} smoke calls returned {FREE_MODEL}"


# ── calls ────────────────────────────────────────────────────────────────────

class Caller:
    """Pinned calls for one run: budget, per-call log rows, prompt log, the Haiku usage-limit stop
    and the free-tier throttle / retry / drop rules."""

    def __init__(self, llm: Any, out_dir: Path, run: str, planned: int, *, cap: int = HARD_CAP,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> None:
        self.llm = llm
        self.run = run
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.calls_path = self.out_dir / f"calls-{run}.jsonl"
        self.prompts_path = self.out_dir / f"prompts-{run}.jsonl"
        for p in (self.calls_path, self.prompts_path):  # earlier log kept as .prev; raw prompts 0600
            sb_paths.fresh_private_file(p)
        self.budget = sb_runner.Budget(cap=cap, planned=planned)
        self.sleep = sleep
        self.log = log
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._free_slots = threading.BoundedSemaphore(FREE_MAX_IN_FLIGHT)
        self._in_flight = 0
        self.max_in_flight_seen = 0
        self.free = {"calls": 0, "failed": 0, "rate_limited_attempts": 0, "retries": 0, "errors": []}

    def _attempt(self, messages, *, provider, model, max_tokens, temperature, purpose, meta) -> sb_llm.CallRecord:
        with self._lock:
            index = self.budget.reserve()
        rec = sb_llm.complete(self.llm, messages, provider=provider, model=model, max_tokens=max_tokens,
                              temperature=temperature, timeout=TIMEOUT_S, purpose=purpose)
        if meta.get("role") == "subject":  # the cap is not honoured below the plugin: check every answer
            where = f"task {meta.get('task')} ({meta.get('skill')}, {meta.get('arm')})"
            sb_llm.check_subject(rec, max_tokens, where)
        row = {
            "index": index, "ts": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "run": self.run, "purpose": purpose, **meta,
            "requested_provider": rec.requested_provider, "requested_model": rec.requested_model,
            "provider": rec.provider, "model": rec.model,
            "input_tokens": rec.input_tokens, "output_tokens": rec.output_tokens, "total_tokens": rec.total_tokens,
            "cache_read_tokens": rec.cache_read_tokens, "latency_s": rec.latency_s,
            "ok": rec.ok, "error_type": rec.error_type, "error": rec.error, "text_chars": len(rec.text or ""),
        }
        with self._lock:
            self.calls.append(row)
            with self.calls_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            with self.prompts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"index": index, "run": self.run, "purpose": purpose, **meta,
                                     "messages": messages, "response_text": rec.text}, ensure_ascii=False) + "\n")
        if rec.error_type == sb_llm.RUNAWAY:  # logged above; now stop the whole run, nothing is judged
            exc = sb_llm.runaway_stop(rec)
            halt = getattr(self.llm, "halt", None)
            if callable(halt):
                halt(exc)
            raise exc
        return rec

    def routed(self, messages, *, route: Mapping[str, Optional[str]], max_tokens, temperature, purpose,
               meta) -> sb_llm.CallRecord:
        """One call on a product route (``provider``/``model`` None = the user's active one). A
        usage-limit error stops the run (``UsageLimit``); nothing is ever re-routed."""
        rec = self._attempt(messages, provider=route.get("provider"), model=route.get("model"),
                            max_tokens=max_tokens, temperature=temperature, purpose=purpose, meta=meta)
        if not rec.ok and sb_llm.USAGE_LIMIT_RE.search(rec.error or ""):
            raise sb_llm.UsageLimit(rec.error)
        if not rec.ok and rec.error_type == "ModelMismatch":
            raise sb_llm.ModelMismatch(rec.error)
        return rec

    def haiku(self, messages, *, max_tokens, temperature, purpose, meta) -> sb_llm.CallRecord:
        return self.subscription(messages, model=HAIKU, max_tokens=max_tokens, temperature=temperature,
                                 purpose=purpose, meta=meta)

    def subscription(self, messages, *, model, max_tokens, temperature, purpose, meta) -> sb_llm.CallRecord:
        """One call on ``SUB_PROVIDER`` pinned to ``model`` (one of ``JUDGE_MODELS``). A usage-limit error
        stops the run (``UsageLimit``); the provider is never switched."""
        if model not in JUDGE_MODELS:
            raise ValueError(f"subscription calls are pinned to {JUDGE_MODELS}, got {model!r}")
        rec = self._attempt(messages, provider=SUB_PROVIDER, model=model, max_tokens=max_tokens,
                            temperature=temperature, purpose=purpose, meta=meta)
        if not rec.ok and sb_runner.USAGE_LIMIT_RE.search(rec.error or ""):
            raise sb_runner.UsageLimit(rec.error)
        return rec

    def free_model(self, messages, *, max_tokens, temperature, purpose, meta, count: bool = True) -> sb_llm.CallRecord:
        """One logical free-tier call: at most ``FREE_MAX_IN_FLIGHT`` in flight, up to
        ``FREE_MAX_RETRIES`` retries with exponential back-off on a rate-limit error. ``count``
        False for smoke calls (the smoke is its own gate, not part of the 20% rule)."""
        with self._free_slots:
            with self._lock:
                self._in_flight += 1
                self.max_in_flight_seen = max(self.max_in_flight_seen, self._in_flight)
            try:
                attempt = 0
                while True:
                    rec = self._attempt(messages, provider=FREE_PROVIDER, model=FREE_MODEL, max_tokens=max_tokens,
                                        temperature=temperature, purpose=purpose, meta={**meta, "attempt": attempt + 1})
                    limited = not rec.ok and bool(sb_runner.USAGE_LIMIT_RE.search(rec.error or ""))
                    if limited:
                        with self._lock:
                            self.free["rate_limited_attempts"] += 1
                    if not limited or attempt >= FREE_MAX_RETRIES:
                        break
                    attempt += 1
                    with self._lock:
                        self.free["retries"] += 1
                    self.sleep(FREE_BACKOFF_S * (2 ** (attempt - 1)))
            finally:
                with self._lock:
                    self._in_flight -= 1
        if count:
            with self._lock:
                self.free["calls"] += 1
                if not rec.ok:
                    self.free["failed"] += 1
                    if len(self.free["errors"]) < 5:
                        self.free["errors"].append(f"{rec.error_type}: {rec.error}")
                share = self.free["failed"] / self.free["calls"]
                n = self.free["calls"]
            if n >= FREE_FAIL_MIN_CALLS and share > FREE_FAIL_MAX:
                raise FreeTierDropped(f"{self.free['failed']} of {n} {FREE_MODEL} calls failed ({share:.0%} > "
                                      f"{FREE_FAIL_MAX:.0%}); last error: {rec.error_type}: {rec.error}")
        return rec


# ── HARM judge call, shared by the self-test and the stored re-judge ───────

def harm_call(caller: "Caller", rubric_text: str, forbidden: list[str], bodies: list[str],
              harm_judge: Mapping[str, str], meta: Mapping[str, Any],
              route: Optional[Mapping[str, Optional[str]]] = None) -> Callable[[list[dict[str, Any]], str], sb_llm.CallRecord]:
    """The ``call(messages, label)`` that ``judge.judge_harm_pair`` needs: blind guard first (rubric
    verbatim, no skill text, no purpose), then one call pinned to the HARM judge model (``route``
    for a product run, else ``SUB_PROVIDER``), logged with the rubric version."""
    cfg = harm_judge_config(harm_judge) if route is None else {"model": route_name(route), "rubric": harm_judge["rubric"]}

    def call(messages, label):
        sb_judge.assert_blind(messages, rubric_text, forbidden, skill_bodies=bodies, purpose="")
        if route is not None:
            return caller.routed(messages, route=route, max_tokens=HARM_MAX_TOKENS, temperature=JUDGE_TEMPERATURE,
                                 purpose=label, meta={**meta, "role": "judge", "question": "harm",
                                                      "judge_mode": sb_judge.MODE_HARM, "harm_rubric": cfg["rubric"]})
        return caller.subscription(messages, model=cfg["model"], max_tokens=HARM_MAX_TOKENS, temperature=JUDGE_TEMPERATURE,
                                   purpose=label, meta={**meta, "role": "judge", "question": "harm",
                                                        "judge_mode": sb_judge.MODE_HARM, "harm_rubric": cfg["rubric"]})
    return call


# ── judge tuning: task split, stored re-judge, candidate choice ────────────────────────

def task_number(task_id: str) -> int:
    """``t08_handover_note`` -> 8."""
    digits = ""
    for ch in task_id[1:]:
        if not ch.isdigit():
            break
        digits += ch
    if not task_id.startswith("t") or not digits:
        raise ValueError(f"not a task id: {task_id!r}")
    return int(digits)


def split_of(task_id: str) -> str:
    """``"tune"`` for odd task numbers, ``"heldout"`` for even ones."""
    return "tune" if task_number(task_id) % 2 == 1 else "heldout"


def split_tasks(task_ids: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"tune": [], "heldout": []}
    for t in task_ids:
        out[split_of(t)].append(t)
    return out


def rejudge_harm_rows(rows: Iterable[Mapping[str, Any]], caller: "Caller", *, fixtures_dir: Path,
                      harm_judge: Mapping[str, str], log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Ask the HARM question again, with ``harm_judge``, on stored self-test rows (answers, scripted
    checks and JOB reused as stored). Error rows (subject failure, blind guard) are copied as they
    are and cost no call. Returns new rows with ``harm`` and ``evidence`` replaced."""
    cfg = harm_judge_config(harm_judge)
    rubric = sb_judge.load_harm_rubric(fixtures_dir, cfg["rubric"])
    bodies = {v: sb_runner.load_skill(fixtures_dir, v) for v in VARIANTS}
    purposes = [sb_judge.purpose_statement(t) for t in bodies.values()]
    prompts = [sb_runner.build_system_prompt(SKILL_NAME, sb_judge.frontmatter_description(t)) for t in bodies.values()]
    forbidden = list(bodies.values()) + prompts + [p for p in purposes if p]
    out: list[dict[str, Any]] = []
    for row in rows:
        new = json.loads(json.dumps(row))
        if row.get("outcome") not in sb_verdict.JOB_OUTCOMES:
            out.append(new)
            continue
        lx, ly = row["x_label"], row["y_label"]
        meta = {"cell": row["cell"], "skill": row["skill"], "model_label": row["model_label"], "task": row["task"],
                "split": split_of(row["task"])}
        try:
            harm = sb_judge.judge_harm_pair(harm_call(caller, rubric.text, forbidden, list(bodies.values()), cfg, meta),
                                            rubric.text, row["prompt"], row["arms"][lx]["text"], row["arms"][ly]["text"],
                                            label=f"{row['cell']} {row['task']} harm")
        except AssertionError as exc:
            new.update(outcome="error", decided_by="error", error=f"blind guard: {exc}", guard_failure=True)
            out.append(new)
            continue
        new["harm"] = harm.to_dict()
        new["harm_judge"] = dict(cfg)
        ev = evidence_of(new)
        new["evidence"] = {"pair_id": ev.pair_id, "job": ev.job, "scripted_harm": ev.scripted_harm,
                           "judge_harm": ev.judge_harm, "harm_plain": ev.harm_plain, "harm": ev.harm,
                           "poison_signature": ev.poison_signature, "cost_delta": dict(ev.cost_delta)}
        log(f"  {row['cell']} {row['task']}: harm x={harm.harm_x} y={harm.harm_y} flipped={harm.flipped}")
        out.append(new)
    return out


def harm_summary(agg: Mapping[str, Any], model_label: str = "haiku") -> dict[str, Any]:
    """The tuning numbers from ``aggregate`` output: flip share, prefers-poisoned pairs, A/A harm
    share, poisoned caught, single-side flags."""
    d = agg["diagnostics"].get(model_label) or {}
    pb = (d.get("position_bias") or {}).get("harm") or {}
    aa = d.get("aa_harm") or {}
    pois = agg["cells"].get(f"poisoned/{model_label}") or {}
    return {
        "flip_share": pb.get("flip_share"), "flipped_pairs": pb.get("flipped_pairs"), "parsed_pairs": pb.get("parsed_pairs"),
        "first_shown_only_flags": pb.get("first_shown_only_flags"), "second_shown_only_flags": pb.get("second_shown_only_flags"),
        "prefers_poisoned": d.get("poisoned_harm_plain"), "aa_harm_share": aa.get("share"), "aa_flagged": aa.get("flagged"),
        "aa_n": aa.get("n"), "poisoned_caught": agg.get("poisoned_caught", {}).get(model_label),
        "poisoned_judge_harm": (pois.get("harm") or {}).get("judge"),
    }


#: Cost order for choosing a judge: cheaper wins a tie.
MODEL_COST_RANK = {HAIKU: 0, SONNET: 1}


def choose_candidate(results: Mapping[str, Mapping[str, Any]]) -> tuple[Optional[str], str]:
    """Pick the HARM judge setting. ``results`` maps a candidate name to ``{"model", "summary": harm_summary(..)}``.
    Eligible: (a) flip share <= HARM_FLIP_MAX and (b) zero prefers-poisoned pairs. Among those, in
    this order: (c) lowest A/A harm share, (d) poisoned still caught, (e) cheaper model, then
    lower flip share. ``(None, reason)`` when nothing passes (a) and (b)."""
    eligible = []
    for name, r in results.items():
        s = r["summary"]
        fs = s.get("flip_share")
        if fs is None or fs > sb_verdict.HARM_FLIP_MAX or (s.get("prefers_poisoned") or 0) != 0:
            continue
        eligible.append(name)
    if not eligible:
        return None, f"no candidate has flip share <= {sb_verdict.HARM_FLIP_MAX} and zero prefers-poisoned pairs"

    def key(name: str):
        s = results[name]["summary"]
        aa = s.get("aa_harm_share")
        return (1.0 if aa is None else aa, 0 if s.get("poisoned_caught") else 1,
                MODEL_COST_RANK.get(results[name]["model"], 9), s["flip_share"])
    best = sorted(eligible, key=key)[0]
    s = results[best]["summary"]
    return best, (f"{best}: flip share {s['flip_share']:.3f} <= {sb_verdict.HARM_FLIP_MAX}, prefers-poisoned 0, "
                  f"A/A harm share {s.get('aa_harm_share')}, poisoned caught {s.get('poisoned_caught')}; "
                  f"eligible {sorted(eligible)}; order: A/A share, poisoned caught, cost, flip share")


# ── the self-test ────────────────────────────────────────────────────────────

def _date() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().date().isoformat()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


class SelfTest:
    """One run of the planted-skill self-test."""

    def __init__(self, llm: Any, *, fixtures_dir: Path, out_dir: Path, run: str,
                 task_ids: Optional[list[str]] = None, llm_trust_cfg: Optional[Mapping[str, Any]] = None,
                 log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
                 harm_judge: Optional[Mapping[str, str]] = None, routes: Optional[Mapping[str, Any]] = None,
                 cap: int = HARD_CAP) -> None:
        if run not in RUNS:
            raise ValueError(f"run must be one of {tuple(RUNS)}, got {run!r}")
        self.product = RUNS[run]["model_label"] == ACTIVE
        self.routes = clean_routes(routes) if self.product else None
        if self.product:
            rubric = dict(harm_judge or HARM_JUDGE).get("rubric", HARM_JUDGE["rubric"])
            if rubric not in sb_judge.HARM_RUBRIC_VERSIONS:
                raise ValueError(f"HARM rubric must be one of {tuple(sb_judge.HARM_RUBRIC_VERSIONS)}, got {rubric!r}")
            self.harm_judge = {"model": route_name(self.routes["harm"]), "rubric": rubric}
        else:
            self.harm_judge = harm_judge_config(harm_judge)
        self.llm = llm
        self.run_name = run
        self.spec = RUNS[run]
        self.model_label = self.spec["model_label"]
        self.subject_model = route_name(self.routes["subject"]) if self.product else MODEL_IDS[self.model_label]
        self.fixtures = Path(fixtures_dir)
        self.out_dir = Path(out_dir)
        self.log = log
        self.sleep = sleep
        self.llm_trust_cfg = llm_trust_cfg

        self.tasks = sb_runner.load_tasks(self.fixtures)
        if task_ids:
            self.tasks = [t for t in self.tasks if t["id"] in task_ids]
        elif self.spec.get("n_tasks"):
            self.tasks = self.tasks[: self.spec["n_tasks"]]
        self.run_checks = sb_runner.load_checks(self.fixtures)
        # All four bodies feed the blind guards, whichever variants this run uses.
        self.bodies = {v: sb_runner.load_skill(self.fixtures, v) for v in VARIANTS}
        self.purposes = {v: sb_judge.purpose_statement(t) for v, t in self.bodies.items()}
        # Each variant's own index row (its frontmatter description), as a real index would show it.
        self.system_prompts = {v: sb_runner.build_system_prompt(SKILL_NAME, sb_judge.frontmatter_description(t))
                               for v, t in self.bodies.items()}
        # Asserts the frozen hashes before any call.
        self.rubric_job = sb_judge.load_rubric(self.fixtures, sb_judge.MODE_PURPOSE)
        self.rubric_harm = sb_judge.load_harm_rubric(self.fixtures, self.harm_judge["rubric"])
        self.forbidden_job = list(self.bodies.values()) + list(self.system_prompts.values())
        self.forbidden_harm = self.forbidden_job + [p for p in self.purposes.values() if p]
        self.plan = plan_calls(run, len(self.tasks), None if self.product else self.harm_judge, routes=self.routes)
        self.plan["cap"] = cap
        self.caller = Caller(llm, self.out_dir, run, self.plan["total"], cap=cap, sleep=sleep, log=log)

    # -- guards ---------------------------------------------------------------

    def _job_call(self, meta: dict[str, Any], purpose: str):
        def call(messages, label):
            sb_judge.assert_blind(messages, self.rubric_job.text, self.forbidden_job,
                                  skill_bodies=list(self.bodies.values()), purpose=purpose)
            jmeta = {**meta, "role": "judge", "question": "job", "judge_mode": sb_judge.MODE_PURPOSE}
            if self.product:
                return self.caller.routed(messages, route=self.routes["job"], max_tokens=JOB_MAX_TOKENS,
                                          temperature=JUDGE_TEMPERATURE, purpose=label, meta=jmeta)
            return self.caller.haiku(messages, max_tokens=JOB_MAX_TOKENS, temperature=JUDGE_TEMPERATURE,
                                     purpose=label, meta=jmeta)
        return call

    def _harm_call(self, meta: dict[str, Any]):
        return harm_call(self.caller, self.rubric_harm.text, self.forbidden_harm, list(self.bodies.values()),
                         self.harm_judge, meta, route=self.routes["harm"] if self.product else None)

    def _subject(self, messages, purpose: str, meta: dict[str, Any]) -> sb_llm.CallRecord:
        meta = {**meta, "role": "subject"}
        if self.product:
            return self.caller.routed(messages, route=self.routes["subject"], max_tokens=SUBJECT_MAX_TOKENS,
                                      temperature=SUBJECT_TEMPERATURE, purpose=purpose, meta=meta)
        if self.model_label == "longcat":
            return self.caller.free_model(messages, max_tokens=SUBJECT_MAX_TOKENS, temperature=SUBJECT_TEMPERATURE,
                                          purpose=purpose, meta=meta)
        return self.caller.haiku(messages, max_tokens=SUBJECT_MAX_TOKENS, temperature=SUBJECT_TEMPERATURE,
                                 purpose=purpose, meta=meta)

    # -- one pair -------------------------------------------------------------

    def pair(self, task: dict[str, Any], row_kind: str) -> dict[str, Any]:
        cell = f"{row_kind}/{self.model_label}"
        meta = {"cell": cell, "skill": row_kind, "model_label": self.model_label, "task": task["id"]}
        if row_kind == AA:
            sp = self.system_prompts["helpful"]
            msgs_x = replay.with_replay_note(replay.build_without_arm(sp, SKILL_NAME, task["prompt"]))
            msgs_y = list(msgs_x)
            lx, ly = "without_a", "without_b"
            purpose = self.purposes["helpful"]  # A/A has no skill; the purpose under test is helpful's
        else:
            sp = self.system_prompts[row_kind]
            body = self.bodies[row_kind]
            arms = replay.build_arms(sp, SKILL_NAME, body, task["prompt"])
            without_raw = sb_judge.raw_text(arms["without"])
            if body in without_raw:
                raise AssertionError("skill text in WITHOUT arm")
            if "marks impact" in without_raw:
                raise AssertionError("skill rule text leaked into WITHOUT arm")
            if replay.index_has_entry(arms["without"][0]["content"], SKILL_NAME):
                raise AssertionError("index entry in WITHOUT")
            if json.loads(arms["with"][3]["content"])["content"] != body:
                raise AssertionError("skill text missing from WITH arm")
            # Same no-tools note as real runs, on both arms (see replay.REPLAY_NOTE).
            msgs_x, msgs_y = replay.with_replay_note(arms["with"]), replay.with_replay_note(arms["without"])
            lx, ly = "with", "without"
            purpose = self.purposes[row_kind]
        rec_x = self._subject(msgs_x, f"{cell} {task['id']} {lx}", {**meta, "arm": lx})
        rec_y = self._subject(msgs_y, f"{cell} {task['id']} {ly}", {**meta, "arm": ly})
        row: dict[str, Any] = {
            "run": self.run_name, "cell": cell, "skill": row_kind, "model_label": self.model_label,
            "subject_model": self.subject_model, "date": _date(), "task": task["id"], "prompt": task["prompt"],
            "x_label": lx, "y_label": ly, "purpose_statement": purpose,
            "arms": {lx: rec_x.to_dict(), ly: rec_y.to_dict()},
        }
        if not (rec_x.ok and rec_y.ok):
            row.update(outcome="error", decided_by="error", error="subject call failed")
            return row
        chk_x, chk_y = self.run_checks(task, rec_x.text), self.run_checks(task, rec_y.text)
        row["scripted"] = {lx: chk_x, ly: chk_y}
        row["tooling_mentions"] = {lx: sb_judge.mentions_tooling(rec_x.text), ly: sb_judge.mentions_tooling(rec_y.text)}
        try:
            job = sb_judge.judge_pair(self._job_call(meta, purpose), self.rubric_job.text, task["prompt"],
                                      rec_x.text, rec_y.text, mode=sb_judge.MODE_PURPOSE, purpose=purpose,
                                      label=f"{cell} {task['id']} job")
            harm = sb_judge.judge_harm_pair(self._harm_call(meta), self.rubric_harm.text, task["prompt"],
                                            rec_x.text, rec_y.text, label=f"{cell} {task['id']} harm")
        except AssertionError as exc:  # blind guard tripped: never judged unblind, pair excluded
            row.update(outcome="error", decided_by="error", error=f"blind guard: {exc}", guard_failure=True)
            return row
        outcome, decided_by = sb_runner.task_outcome(chk_x["pass"], chk_y["pass"], job.outcome)
        row.update(
            job=job.to_dict(), harm=harm.to_dict(), outcome=outcome, decided_by=decided_by,
            judge_outcome={"x": "win", "y": "loss", "tie": "tie"}[job.outcome],
            scripted_outcome=("win" if chk_x["pass"] and not chk_y["pass"] else
                              "loss" if chk_y["pass"] and not chk_x["pass"] else "tie"),
        )
        ev = evidence_of(row)
        row["evidence"] = {"pair_id": ev.pair_id, "job": ev.job, "scripted_harm": ev.scripted_harm,
                           "judge_harm": ev.judge_harm, "harm_plain": ev.harm_plain, "harm": ev.harm,
                           "poison_signature": ev.poison_signature, "cost_delta": dict(ev.cost_delta)}
        return row

    # -- whole run ------------------------------------------------------------

    def _rubric_paths(self) -> dict[str, tuple[Path, Path]]:
        """Rubrics this run asserts: the JOB rubric and the configured HARM rubric version."""
        return {sb_judge.MODE_PURPOSE: sb_judge.rubric_paths(self.fixtures, sb_judge.MODE_PURPOSE),
                sb_judge.MODE_HARM: sb_judge.harm_rubric_paths(self.fixtures, self.harm_judge["rubric"])}

    def _hashes(self) -> dict[str, str]:
        return {m: sb_judge.sha256_text(p.read_text(encoding="utf-8")) for m, (p, _) in self._rubric_paths().items()}

    def _expected_hashes(self) -> dict[str, str]:
        return {m: h.read_text(encoding="utf-8").strip().lower() for m, (_, h) in self._rubric_paths().items()}

    def _smoke(self) -> dict[str, Any]:
        # Stops at the first bad call: a host fallback answers on another model (in an earlier run it was the
        # main agent model), so every further smoke call would spend that model for nothing.
        recs: list[sb_llm.CallRecord] = []
        for i in range(SMOKE_CALLS):
            rec = self.caller.free_model(SMOKE_MESSAGES, max_tokens=16, temperature=0.0,
                                         purpose=f"free smoke {i + 1}", meta={"role": "smoke", "cell": "smoke"},
                                         count=False)
            recs.append(rec)
            if not (rec.ok and rec.model == FREE_MODEL):
                break
        ok, reason = smoke_verdict(recs)
        if not ok and len(recs) < SMOKE_CALLS:
            bad = recs[-1]
            reason = (f"smoke call {len(recs)} returned model {bad.model!r}, not {FREE_MODEL!r} (silent swap)"
                      if bad.model and bad.model != FREE_MODEL else
                      f"smoke call {len(recs)} failed: {bad.error_type}: {bad.error}") + \
                     f"; smoke stopped after {len(recs)} of {SMOKE_CALLS} calls"
        return {"ok": ok, "reason": reason, "returned_models": [r.model for r in recs],
                "texts": [r.text for r in recs], "errors": [r.error for r in recs if r.error]}

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        expected = self._expected_hashes()
        before = self._hashes()
        self.caller.budget.check_plan()
        self.log(f"[plan] run {self.run_name}: {self.plan['total']} calls ({self.plan['pairs']} pairs x "
                 f"{CALLS_PER_PAIR}" + (f" + {self.plan['smoke_calls']} smoke" if self.plan["smoke_calls"] else "")
                 + f"), by model {json.dumps(self.plan['by_model'])}, hard cap {self.caller.budget.cap}")
        (self.out_dir / f"plan-{self.run_name}.json").write_text(json.dumps(self.plan, indent=2) + "\n", encoding="utf-8")
        out: dict[str, Any] = {
            "run": self.run_name, "model_label": self.model_label, "subject_model": self.subject_model,
            "subject_provider": (self.routes["subject"]["provider"] or "active") if self.product else MODEL_PROVIDERS[self.model_label],
            "judge_model": route_name(self.routes["job"]) if self.product else JUDGE_MODEL,
            "judge_provider": (self.routes["job"]["provider"] or "active") if self.product else SUB_PROVIDER,
            "harm_judge": {**self.harm_judge, "provider": (self.routes["harm"]["provider"] or "active") if self.product else SUB_PROVIDER,
                                                           "rubric_file": self._rubric_paths()[sb_judge.MODE_HARM][0].name},
            "started_at": _now(), "date": _date(), "plan": self.plan,
            "task_ids": [t["id"] for t in self.tasks], "pairs": [], "stopped_early": None, "free_tier": None,
            "skipped": None,
        }
        pairs_path = self.out_dir / f"pairs-{self.run_name}.jsonl"
        sb_paths.fresh_private_file(pairs_path)
        try:
            if self.model_label == "longcat":
                allowed, why = free_tier_allowed(self.llm_trust_cfg)
                out["free_tier"] = {"trusted": allowed, "trust_reason": why, "smoke": None, "dropped": False}
                if not allowed:
                    out["skipped"] = f"run C skipped: {why}"
                    self.log(f"[skip] {out['skipped']}")
                    return self._finish(out, started, expected, before)
                smoke = self._smoke()
                out["free_tier"]["smoke"] = smoke
                self.log(f"[smoke] {smoke['reason']}")
                if not smoke["ok"]:
                    out["skipped"] = f"run C skipped: smoke failed: {smoke['reason']}"
                    return self._finish(out, started, expected, before)
            for kind in list(self.spec["variants"]) + ([AA] if self.spec["aa"] else []):
                self.log(f"[cell] {kind}/{self.model_label}")
                for task in self.tasks:
                    row = self.pair(task, kind)
                    out["pairs"].append(row)
                    with pairs_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    h = row.get("harm") or {}
                    self.log(f"  {row['cell']} {task['id']}: JOB {row['outcome']} ({row['decided_by']}); "
                             f"harm x={h.get('harm_x')} y={h.get('harm_y')} flipped={h.get('flipped')}")
        except FreeTierDropped as exc:
            out["stopped_early"] = {"reason": "FreeTierDropped", "error": str(exc)}
            out["free_tier"]["dropped"] = True
            self.log(f"[drop] {exc}")
        except (sb_runner.CapExceeded, sb_runner.UsageLimit, sb_llm.RunStop) as exc:
            out["stopped_early"] = {"reason": type(exc).__name__, "error": str(exc)}
            self.log(f"[stop] {type(exc).__name__}: {exc}")
        except Exception as exc:  # keep partial results, say so
            out["stopped_early"] = {"reason": f"unexpected {type(exc).__name__}", "error": str(exc)}
            self.log(f"[stop] unexpected {type(exc).__name__}: {exc}")
        return self._finish(out, started, expected, before)

    def _finish(self, out: dict[str, Any], started: float, expected: dict[str, str], before: dict[str, str]) -> dict[str, Any]:
        after = self._hashes()
        out["rubrics"] = {m: {"sha256_expected": expected[m], "sha256_before": before[m], "sha256_after": after[m],
                              "unchanged": expected[m] == before[m] == after[m]} for m in expected}
        out["totals"] = sb_runner.totals(self.caller.calls, time.monotonic() - started)
        out["guard_failures"] = [{"cell": p["cell"], "task": p["task"], "error": p["error"]}
                                 for p in out["pairs"] if p.get("guard_failure")]
        if self.model_label == "longcat" and out["free_tier"] is not None:
            f = dict(self.caller.free)
            f["fail_share"] = (f["failed"] / f["calls"]) if f["calls"] else None
            f["max_in_flight_seen"] = self.caller.max_in_flight_seen
            if f["fail_share"] is not None and f["fail_share"] > FREE_FAIL_MAX and not out["free_tier"]["dropped"]:
                out["free_tier"]["dropped"] = True
                out["stopped_early"] = out["stopped_early"] or {
                    "reason": "FreeTierDropped", "error": f"{f['failed']} of {f['calls']} calls failed at end of run"}
            out["free_tier"]["counts"] = f
        out["finished_at"] = _now()
        return out


def free_skip_from_calls(calls: list[dict[str, Any]], *, trust: Optional[Mapping[str, Any]], fixtures_dir: Path,
                         n_tasks: int) -> dict[str, Any]:
    """Run-C record rebuilt from its logged smoke rows (``calls-free.jsonl``) when the run process
    was stopped during a failed smoke, before it wrote ``run-free.json``. No calls. Every logged row
    counts in the totals, so ``calls-*.jsonl`` line counts still equal the reported total."""
    recs = [sb_llm.CallRecord(ok=r["ok"], provider=r.get("provider", ""), model=r.get("model", ""),
                              requested_provider=r.get("requested_provider", ""), requested_model=r.get("requested_model", ""),
                              error=r.get("error", ""), error_type=r.get("error_type", "")) for r in calls if r.get("role") == "smoke"]
    ok, reason = smoke_verdict(recs)
    if not ok and recs and len(recs) < SMOKE_CALLS:
        bad = recs[-1]
        reason = (f"smoke call {len(recs)} returned model {bad.model!r}, not {FREE_MODEL!r} (silent swap)"
                  if bad.model and bad.model != FREE_MODEL else f"smoke call {len(recs)} failed: {bad.error_type}: {bad.error}") + \
                 f"; smoke stopped after {len(recs)} of {SMOKE_CALLS} calls"
    allowed, why = free_tier_allowed(trust)
    minutes = sum(float(r.get("latency_s") or 0) for r in calls) / 60.0
    return {
        "run": "free", "model_label": "longcat", "subject_model": FREE_MODEL, "subject_provider": FREE_PROVIDER,
        "judge_model": JUDGE_MODEL, "judge_provider": SUB_PROVIDER, "started_at": calls[0]["ts"] if calls else None,
        "date": _date(), "plan": plan_calls("free", n_tasks), "task_ids": [], "pairs": [], "stopped_early": None,
        "free_tier": {"trusted": allowed, "trust_reason": why, "dropped": False,
                      "smoke": {"ok": ok, "reason": reason, "returned_models": [r.model for r in recs],
                                "texts": [], "errors": [r.error for r in recs if r.error]}},
        "skipped": None if ok else f"run C skipped: smoke failed: {reason}",
        "rebuilt_from_call_log": True,
        "rubrics": {}, "totals": sb_runner.totals(calls, minutes * 60.0), "guard_failures": [],
        "finished_at": _now(),
    }


# ── aggregation (labels from verdict.py only) ────────────────────────────────

def evidence_of(row: Mapping[str, Any]) -> sb_verdict.PairEvidence:
    """``verdict.evidence_from_pair`` on a stored row (x = WITH or without_a)."""
    return sb_verdict.evidence_from_pair(row, row.get("harm"), with_label=row["x_label"], without_label=row["y_label"])


def _ok_rows(runs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [p for r in runs.values() for p in r.get("pairs", []) if p.get("outcome") in sb_verdict.JOB_OUTCOMES]


def _median(vals: list[float]) -> Optional[float]:
    return round(statistics.median(vals), 1) if vals else None


def judge_only_accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Over pairs where exactly one answer passes the scripted check: share whose JOB judge-only
    winner matches the scripted winner. Ties count as misses."""
    eligible = [p for p in rows if p.get("scripted_outcome") in ("win", "loss")]
    matches = sum(1 for p in eligible if p["judge_outcome"] == p["scripted_outcome"])
    ties = sum(1 for p in eligible if p["judge_outcome"] == "tie")
    return {"n": len(eligible), "matches": matches, "ties": ties, "wrong": len(eligible) - matches - ties,
            "accuracy": round(matches / len(eligible), 4) if eligible else None}


def position_bias(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Both questions. JOB: share of decisive judgements that picked Answer 1 (stats.position_bias,
    flagged above 0.60). HARM: share of parsed pairs whose flags flipped between orders
    (verdict.harm_flip_share against HARM_FLIP_MAX), plus how often only the first-shown answer was
    flagged versus only the second-shown one."""
    first = sum(p["job"]["first_picks"] for p in rows)
    decisive = sum(p["job"]["decisive_judgements"] for p in rows)
    pb = stats.position_bias(first, decisive)
    parsed = sum(1 for p in rows if p["harm"]["parsed"])
    flips = sum(1 for p in rows if p["harm"]["flipped"])
    first_only = second_only = 0
    for p in rows:
        for o in p["harm"]["orders"]:
            if o["harm_x"] is None:
                continue
            f1, f2 = (o["harm_x"], o["harm_y"]) if o["first"] == "x" else (o["harm_y"], o["harm_x"])
            first_only += bool(f1 and not f2)
            second_only += bool(f2 and not f1)
    flip_share = sb_verdict.harm_flip_share(flips, parsed)
    return {
        "job": {"first_picks": first, "decisive": decisive, "share": pb.share, "flagged": pb.flagged,
                "max": stats.POSITION_BIAS_MAX},
        "harm": {"parsed_pairs": parsed, "flipped_pairs": flips, "flip_share": flip_share,
                 "max": sb_verdict.HARM_FLIP_MAX, "ok": flip_share is not None and flip_share <= sb_verdict.HARM_FLIP_MAX,
                 "first_shown_only_flags": first_only, "second_shown_only_flags": second_only},
    }


def arm_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Median input and output tokens per arm over a cell's pairs."""
    out: dict[str, Any] = {}
    for p in rows:
        for arm, rec in p["arms"].items():
            d = out.setdefault(arm, {"input": [], "output": []})
            d["input"].append(rec["input_tokens"])
            d["output"].append(rec["output_tokens"])
    return {a: {"median_input_tokens": _median(d["input"]), "median_output_tokens": _median(d["output"]), "n": len(d["input"])}
            for a, d in out.items()}


def bloat_overhead(by_cell: Mapping[str, list[dict[str, Any]]], model_label: str = "haiku") -> Optional[dict[str, Any]]:
    """Extra input tokens per load of the bloat variant: per task, bloat WITH minus helpful WITH,
    and bloat WITH minus bloat WITHOUT. Numbers only."""
    bloat = {p["task"]: p for p in by_cell.get(f"bloat/{model_label}", [])}
    if not bloat:
        return None
    helpful = {p["task"]: p for p in by_cell.get(f"helpful/{model_label}", [])}
    vs_helpful = [bloat[t]["arms"]["with"]["input_tokens"] - helpful[t]["arms"]["with"]["input_tokens"]
                  for t in bloat if t in helpful]
    vs_without = [p["arms"]["with"]["input_tokens"] - p["arms"]["without"]["input_tokens"] for p in bloat.values()]
    helpful_vs_without = [p["arms"]["with"]["input_tokens"] - p["arms"]["without"]["input_tokens"] for p in helpful.values()]
    return {"model_label": model_label,
            "median_extra_vs_helpful_with": _median(vs_helpful), "n_vs_helpful": len(vs_helpful),
            "median_extra_vs_without": _median(vs_without), "n_vs_without": len(vs_without),
            "helpful_median_extra_vs_without": _median(helpful_vs_without)}


def aggregate(runs: Mapping[str, Mapping[str, Any]], fixtures_dir: Optional[Path] = None) -> dict[str, Any]:
    """Cells, labels (verdict.cell_verdict), confusion matrix, gate, diagnostics. No calls.

    The Heavy flag is computed from the 4 planted SKILL.md files in ``fixtures_dir`` (default:
    the repo's ``fixtures/``), never from the user's skills, and sits beside each label.
    """
    heavy = planted_heavy(fixtures_dir)
    rows = _ok_rows(runs)
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for p in rows:
        by_cell.setdefault(p["cell"], []).append(p)
    models = [ml for ml in MODEL_IDS if any(r["model_label"] == ml and not r.get("skipped") for r in runs.values())]

    # A/A noise floor from this run's own A/A cell, per subject model.
    aa: dict[str, Any] = {}
    for ml in models:
        aa_ev = [evidence_of(p) for p in by_cell.get(f"{AA}/{ml}", [])]
        k, n = sb_verdict.aa_harm_counts(aa_ev)
        aa[ml] = {"flagged": k, "n": n, "share": sb_verdict.aa_harm_share(aa_ev), "high": sb_verdict.aa_harm_high(aa_ev)}

    cells: dict[str, Any] = {}
    labels: dict[str, str] = {}
    run_of_cell = {c: name for name, r in runs.items() for c in run_cells(name)}
    for cell, cell_rows in sorted(by_cell.items()):
        kind, ml = cell.split("/")
        cv = sb_verdict.cell_verdict([evidence_of(p) for p in cell_rows], aa_harm_high=aa[ml]["high"],
                                     aa_harm_share=aa[ml]["share"], overrules=None, correction=None)
        d = cv.to_dict()
        run = runs[run_of_cell[cell]]
        errors = [p for p in run.get("pairs", []) if p["cell"] == cell and p.get("outcome") == "error"]
        d.update({
            "cell": cell, "variant": kind, "model_label": ml,
            "subject_model": MODEL_IDS[ml] or runs[run_of_cell[cell]].get("subject_model"),
            "subject_provider": MODEL_PROVIDERS[ml] or runs[run_of_cell[cell]].get("subject_provider"), "date": run.get("date"), "run": run_of_cell[cell],
            "partial": bool(run.get("stopped_early")) or len(cell_rows) < len(run.get("task_ids", [])),
            "n_errors_excluded": len(errors),
            "job_judge_only": {k: sum(1 for p in cell_rows if p["judge_outcome"] == k) for k in ("win", "loss", "tie")},
            "decided_by": {k: sum(1 for p in cell_rows if p["decided_by"] == k) for k in ("scripted", "judge")},
            "scripted": {"with_pass": sum(1 for p in cell_rows if p["scripted"][p["x_label"]]["pass"]),
                         "without_pass": sum(1 for p in cell_rows if p["scripted"][p["y_label"]]["pass"]),
                         "with_poisoned": sum(1 for p in cell_rows if p["scripted"][p["x_label"]]["poisoned"]),
                         "without_poisoned": sum(1 for p in cell_rows if p["scripted"][p["y_label"]]["poisoned"])},
            "arm_tokens": arm_tokens(cell_rows),
            "expected": list(EXPECTED[kind]), "expected_text": expected_text(kind),
            "heavy": (heavy or {}).get(kind),
        })
        d["matches_expected"] = bool(variant_match(kind, d["label"], d["heavy"]))
        cells[cell] = d
        labels[cell] = cv.label

    matrix: dict[str, Any] = {}
    for ml in models:
        matrix[ml] = {k: {"expected": list(EXPECTED[k]), "expected_text": expected_text(k),
                          "actual": labels.get(f"{k}/{ml}"),
                          "heavy": (heavy or {}).get(k),
                          "match": variant_match(k, labels.get(f"{k}/{ml}"), (heavy or {}).get(k))
                          if f"{k}/{ml}" in labels else None}
                      for k in ROWS}
    poisoned_caught = {ml: labels.get(f"poisoned/{ml}") == sb_verdict.GETS_IN_THE_WAY for ml in models}

    diag: dict[str, Any] = {}
    for ml in models:
        mrows = [p for p in rows if p["model_label"] == ml]
        aa_rows = by_cell.get(f"{AA}/{ml}", [])
        aa_job = stats.verdict(sum(p["outcome"] == "win" for p in aa_rows), sum(p["outcome"] == "loss" for p in aa_rows),
                               sum(p["outcome"] == "tie" for p in aa_rows))
        diag[ml] = {
            "position_bias": position_bias(mrows),
            "judge_only_accuracy": {"overall": judge_only_accuracy(mrows),
                                    **{c: judge_only_accuracy(r) for c, r in by_cell.items() if c.endswith("/" + ml)}},
            "aa_job": {"wins": aa_job.wins, "losses": aa_job.losses, "ties": aa_job.ties, "verdict": aa_job.verdict,
                       "low": round(aa_job.low, 4), "high": round(aa_job.high, 4)},
            "aa_harm": aa.get(ml),
            "poisoned_harm_plain": sum(1 for p in by_cell.get(f"poisoned/{ml}", []) if evidence_of(p).harm_plain),
        }

    hashes_ok = all(v["unchanged"] for r in runs.values() for v in (r.get("rubrics") or {}).values())
    gate = None
    if "haiku" in models:
        haiku_labels = {c: l for c, l in labels.items() if c.endswith("/haiku")}
        gate = sb_verdict.compute_gate(haiku_labels, ["haiku"], poisoned_harm_plain=diag["haiku"]["poisoned_harm_plain"],
                                       flip_share=diag["haiku"]["position_bias"]["harm"]["flip_share"],
                                       rubric_hashes_unchanged=hashes_ok)
        # verdict.compute_gate leaves placebo_ok None (written before placebo answers existed). The
        # self-test fills it, and bloat_ok, from the confusion matrix (expected-set membership).
        sel = dict(gate)
        for k in ("placebo", "bloat"):
            sel[f"{k}_ok"] = matrix["haiku"][k]["match"]
        # bloat_ok is "Heavy set and verdict is not Gets in the way"; the other three planted
        # skills must not be Heavy.
        sel["heavy_false_positive_ok"] = heavy_false_positive_ok(heavy)
        sel["placebo_reason"] = ("placebo label in the expected set (confusion matrix)" if sel["placebo_ok"]
                                 else "placebo not run" if sel["placebo_ok"] is None
                                 else "placebo label outside the expected set (confusion matrix)")
        sel["overall"] = bool(gate["overall"]) and all(
            sel[f"{k}_ok"] is True for k in ("placebo", "bloat", "heavy_false_positive")
        )
        gate = {"verdict_compute_gate": dict(gate), "selftest": sel}

    totals = {name: r["totals"] for name, r in runs.items() if r.get("totals")}
    return {
        "generated_at": _now(), "cells": cells, "labels": labels, "matrix": matrix, "poisoned_caught": poisoned_caught,
        "aa": aa, "diagnostics": diag, "gate": gate, "rubric_hashes_unchanged": hashes_ok,
        "bloat_overhead": bloat_overhead(by_cell, "haiku"),
        "heavy": {"ratio": sb_cost.HEAVY_RATIO, "comparison_set": list(VARIANTS), "by_variant": heavy,
                  "median_tokens": None if not heavy else heavy[VARIANTS[0]]["median_tokens"],
                  "label": sb_cost.LABEL},
        "totals_by_run": totals,
        "total_calls": sum(t["calls"] for t in totals.values()),
        "total_tokens": sum(t["total_tokens"] for t in totals.values()),
        "total_minutes": round(sum(t["minutes"] for t in totals.values()), 2),
        "models": models,
    }


# ── markdown ─────────────────────────────────────────────────────────────────

def _r(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def cell_text(c: Optional[Mapping[str, Any]]) -> str:
    """README cell: label, JOB W/L/T, harm tasks, Wilson 95% of the JOB win rate, stamp."""
    if not c:
        return "not run"
    j, h = c["job"], c["harm"]
    part = " (partial)" if c.get("partial") else ""
    heavy = ((c.get("heavy") or {}).get("text"))
    flag = f" · {heavy}" if heavy else ""  # the Heavy flag sits beside the label, never in it
    return (f"**{c['label']}**{part}{flag} · JOB {j['wins']}/{j['losses']}/{j['ties']} · harm {h['tasks']}/{c['n']} · "
            f"Wilson {_r(j['low'])}–{_r(j['high'])} · {c['subject_model']}, {c['date']}")


def proof_table(agg: Mapping[str, Any], runs: Mapping[str, Any]) -> list[str]:
    models = list(agg["models"])
    free = runs.get("free")
    free_note = None
    if free and (free.get("skipped") or (free.get("free_tier") or {}).get("dropped")):
        free_note = free.get("skipped") or (free.get("stopped_early") or {}).get("error")
        models = [m for m in models if m != "longcat"]
    head = ["variant", "expected"] + [MODEL_COLUMNS[m] for m in models]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for k in ROWS:
        name = "A/A (no skill vs no skill)" if k == AA else k
        lines.append("| " + " | ".join([name, expected_text(k)] +
                                       [cell_text(agg["cells"].get(f"{k}/{m}")) for m in models]) + " |")
    if free_note:
        lines.append("")
        lines.append(f"{FREE_COLUMN}: not shown. {free_note}")
    return lines


def render_md(data: Mapping[str, Any]) -> str:
    runs, agg = data["runs"], data["aggregate"]
    L: list[str] = []
    L.append("# Self-test: \"Proof it works\" (held-out test)")
    L.append("")
    hj = next((r["harm_judge"] for r in runs.values() if r.get("harm_judge")), None)
    judge_txt = ("judge always Haiku" if not hj else
                 f"JOB judge {JUDGE_MODEL}; HARM judge {hj['model']} with {hj.get('rubric_file') or hj['rubric']}")
    L.append(f"Generated {agg['generated_at']}. Subject: claude-haiku-5-5 on claude-subscription-directsdk-experimental "
             f"({judge_txt}). Free tier subject: {FREE_MODEL} on {FREE_PROVIDER}. Fresh answers; labels come only from "
             "`plugin/skilluminati/verdict.py` under the current rules (poison signature = hard harm; judge-only harm must clear the Wilson "
             "upper bound of the same model's A/A harm share; A/A tolerance). Nothing was tuned on these answers.")
    L.append("")
    L.append("## Proof it works (README-ready)")
    L.append("")
    L.extend(proof_table(agg, runs))
    L.append("")
    L.append("Cell = label · JOB wins/losses/ties (WITH vs WITHOUT, scripted check first, else the purpose judge in both orders) · "
             "harm tasks / pairs · Wilson 95% interval of the JOB decisive win rate · subject model, date.")
    L.append("")
    L.append("## Confusion matrix")
    L.append("")
    for ml, row in agg["matrix"].items():
        L.append(f"**{MODEL_COLUMNS[ml]}** (poisoned caught: {agg['poisoned_caught'][ml]})")
        L.append("")
        L.append("| variant | expected | actual | match |")
        L.append("|---|---|---|---|")
        for k, v in row.items():
            actual = v['actual'] or 'not run'
            if (v.get("heavy") or {}).get("text"):
                actual += f" · {v['heavy']['text']}"
            L.append(f"| {k} | {v.get('expected_text') or ' or '.join(v['expected'])} | {actual} | {v['match']} |")
        L.append("")
    L.append("## Per cell evidence")
    L.append("")
    L.append("| cell | label | n (errors excl.) | JOB W/L/T | JOB verdict | Wilson | decided by script/judge | judge-only W/L/T | "
             "scripted pass WITH/WITHOUT | poisoned hits WITH/WITHOUT | scripted harm (poison sig.) | judge harm | judge-harm Wilson low | "
             "A/A bar | harm found by | median input tok WITH / WITHOUT |")
    L.append("|" + "---|" * 16)
    for cell, c in agg["cells"].items():
        j, h, s = c["job"], c["harm"], c["scripted"]
        at = c["arm_tokens"]
        arms = list(at)
        tok = " / ".join(str(at[a]["median_input_tokens"]) for a in arms)
        jo = c["job_judge_only"]
        L.append(f"| {cell} | **{c['label']}** | {c['n']} ({c['n_errors_excluded']}) | {j['wins']}/{j['losses']}/{j['ties']} | "
                 f"{j['verdict']} | {_r(j['low'])}–{_r(j['high'])} | {c['decided_by']['scripted']}/{c['decided_by']['judge']} | "
                 f"{jo['win']}/{jo['loss']}/{jo['tie']} | {s['with_pass']}/{s['without_pass']} | {s['with_poisoned']}/{s['without_poisoned']} | "
                 f"{h['scripted']} ({h['poison_signature']}) | {h['judge']} | {_r(h['judge_low'], 3)} | {_r(h['aa_harm_high'], 3)} | "
                 f"{h['found_by'] or 'not found'} | {tok} |")
    L.append("")
    L.append("## Judge diagnostics")
    L.append("")
    for ml, d in agg["diagnostics"].items():
        pb, aa = d["position_bias"], d["aa_harm"]
        acc = d["judge_only_accuracy"]["overall"]
        L.append(f"**{MODEL_COLUMNS[ml]} answers**")
        L.append("")
        L.append(f"- JOB position bias: Answer 1 picked in {pb['job']['first_picks']} of {pb['job']['decisive']} decisive "
                 f"judgements (share {_r(pb['job']['share'], 3)}; flagged above {pb['job']['max']}: {pb['job']['flagged']}).")
        L.append(f"- HARM position sensitivity: flags flipped between orders on {pb['harm']['flipped_pairs']} of "
                 f"{pb['harm']['parsed_pairs']} parsed pairs (share {_r(pb['harm']['flip_share'], 3)}; gate max "
                 f"{pb['harm']['max']}; ok: {pb['harm']['ok']}). Single-side flags: first-shown {pb['harm']['first_shown_only_flags']}, "
                 f"second-shown {pb['harm']['second_shown_only_flags']}.")
        L.append(f"- A/A JOB: {d['aa_job']['wins']}/{d['aa_job']['losses']}/{d['aa_job']['ties']} ({d['aa_job']['verdict']}, "
                 f"Wilson {_r(d['aa_job']['low'])}–{_r(d['aa_job']['high'])}).")
        if aa:
            L.append(f"- A/A harm share: {aa['flagged']} of {aa['n']} pairs flagged on either side in both orders "
                     f"(share {_r(aa['share'], 3)}; Wilson upper {_r(aa['high'], 3)} = the judge-only harm bar).")
        L.append(f"- Judge-only accuracy (JOB judge vs scripted winner, pairs where exactly one answer passes): "
                 f"{acc['matches']} / {acc['n']} ({_r(acc['accuracy'], 3)}); ties {acc['ties']}, wrong {acc['wrong']}.")
        L.append(f"- Poisoned pairs where the HARM judge preferred the poisoned answer (flagged WITHOUT only, both orders): "
                 f"{d['poisoned_harm_plain']}.")
        L.append("")
    if agg.get("gate"):
        g, sel = agg["gate"]["verdict_compute_gate"], agg["gate"]["selftest"]
        L.append("## Gate")
        L.append("")
        L.append("| check | value |")
        L.append("|---|---|")
        for k in sb_verdict.GATE_KEYS:
            if k == "overall":
                continue
            v = sel[k] if k != "placebo_ok" else sel["placebo_ok"]
            src = " (from the confusion matrix; verdict.compute_gate leaves it None)" if k == "placebo_ok" else ""
            L.append(f"| {k} | {v}{src} |")
        L.append(f"| bloat_ok | {sel['bloat_ok']} (verdict is not Gets in the way, and Heavy is set) |")
        L.append(f"| heavy_false_positive_ok | {sel.get('heavy_false_positive_ok')} (no other planted skill is Heavy) |")
        L.append(f"| overall, verdict.compute_gate (placebo None) | {g['overall']} |")
        L.append(f"| **overall, self-test (adds placebo_ok, bloat_ok, heavy_false_positive_ok)** | **{sel['overall']}** |")
        L.append("")
    bo = agg.get("bloat_overhead")
    hv = agg.get("heavy") or {}
    if bo or hv.get("by_variant"):
        L.append("## Bloat token overhead")
        L.append("")
    if hv.get("by_variant"):
        toks = {k: v["load_tokens"] for k, v in hv["by_variant"].items()}
        L.append(f"- Heavy flag ({hv.get('label', 'estimate')}): a skill is Heavy when its load tokens are at least "
                 f"{hv['ratio']:g}x the median of the comparison set. Comparison set = the 4 planted skills: "
                 + ", ".join(f"{k} {v:,}" for k, v in toks.items()) + f"; median {hv['median_tokens']:,.0f}, "
                 f"threshold {hv['ratio'] * hv['median_tokens']:,.0f}.")
        for k, h in hv["by_variant"].items():
            L.append(f"  - {k}: {h['text'] if h['flag'] else 'not Heavy'} ({h['ratio']}x median)")
    if bo:
        L.append(f"- bloat WITH vs helpful WITH: median **{bo['median_extra_vs_helpful_with']}** extra input tokens per load "
                 f"(n = {bo['n_vs_helpful']} tasks).")
        L.append(f"- bloat WITH vs WITHOUT: median {bo['median_extra_vs_without']} extra input tokens (helpful: "
                 f"{bo['helpful_median_extra_vs_without']}).")
        bl = agg["cells"].get("bloat/haiku")
        if bl:
            L.append(f"- README line: bloat costs about {bo['median_extra_vs_helpful_with']} extra tokens per load over the "
                     f"helpful version; label **{bl['label']}**.")
        L.append("")
    L.append("## Runs: calls, tokens, minutes")
    L.append("")
    L.append("| run | subject | planned | calls | ok | failed | input tok | output tok | total tok | minutes | stopped / skipped |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, r in runs.items():
        t = r.get("totals") or {}
        stop = r.get("skipped") or ((r.get("stopped_early") or {}).get("reason") or "") or "no"
        L.append(f"| {name} | {r['subject_model']} | {r['plan']['total']} | {t.get('calls')} | {t.get('ok')} | {t.get('failed')} | "
                 f"{t.get('input_tokens')} | {t.get('output_tokens')} | {t.get('total_tokens')} | {t.get('minutes')} | {stop} |")
    L.append(f"| total | | | {agg['total_calls']} | | | | | {agg['total_tokens']} | {agg['total_minutes']} | |")
    L.append("")
    free = runs.get("free")
    L.append("## Run C (free tier) decision")
    L.append("")
    if not free:
        L.append("- Not run.")
    else:
        ft = free.get("free_tier") or {}
        L.append(f"- Trust check: {ft.get('trusted')} ({ft.get('trust_reason')}).")
        if ft.get("smoke"):
            L.append(f"- Smoke: {ft['smoke']['ok']}: {ft['smoke']['reason']}. Returned models: {ft['smoke']['returned_models']}.")
        c = ft.get("counts") or {}
        if c:
            L.append(f"- LongCat subject calls: {c.get('calls')}, failed {c.get('failed')} (share {_r(c.get('fail_share'), 3)}; "
                     f"drop above {FREE_FAIL_MAX:.0%}); rate-limited attempts {c.get('rate_limited_attempts')}, retries "
                     f"{c.get('retries')}; most in flight {c.get('max_in_flight_seen')} (cap {FREE_MAX_IN_FLIGHT}).")
            for e in c.get("errors") or []:
                L.append(f"  - verbatim error: `{e}`")
        L.append(f"- Dropped: {ft.get('dropped')}. Skipped: {free.get('skipped') or 'no'}. Stopped: "
                 f"{(free.get('stopped_early') or {}).get('error') or 'no'}.")
        L.append(f"- Column header: \"{FREE_COLUMN}\". It was free on Nous when tested; no claim that it stays free.")
    L.append("")
    for name, r in runs.items():
        if r.get("stopped_early"):
            L.append(f"- Run {name} stopped early: {r['stopped_early']['reason']}: `{r['stopped_early']['error']}`")
        if r.get("guard_failures"):
            L.append(f"- Run {name} blind-guard exclusions: {r['guard_failures']}")
    L.extend(["", "## Reading it", ""])
    L.extend(data.get("reading") or ["- (no reading notes)"])
    L.extend(["", "## Could not do / unconfirmed", ""])
    L.extend(data.get("unconfirmed") or ["- Nothing unconfirmed."])
    L.append("")
    return "\n".join(L)


def reading_notes(data: Mapping[str, Any]) -> list[str]:
    """Plain statements generated from the numbers (no judgement added)."""
    agg = data["aggregate"]
    out = []
    for ml, row in agg["matrix"].items():
        bad = [f"{k} (expected {' or '.join(v['expected'])}, got {v['actual']})" for k, v in row.items() if v["match"] is False]
        missing = [k for k, v in row.items() if v["match"] is None]
        out.append(f"- {MODEL_COLUMNS[ml]}: {sum(1 for v in row.values() if v['match'])} of "
                   f"{sum(1 for v in row.values() if v['match'] is not None)} labels as expected"
                   + (f"; mismatches: {', '.join(bad)}" if bad else "") + (f"; not run: {', '.join(missing)}" if missing else "")
                   + f". Poisoned caught: {agg['poisoned_caught'][ml]}.")
    if agg.get("gate"):
        sel = agg["gate"]["selftest"]
        failing = [k for k, v in sel.items() if v is False and k != "overall"]
        out.append(f"- Held-out gate overall: {sel['overall']}" + (f"; failing: {', '.join(failing)}" if failing else "") + ".")
    return out


__all__ = [
    "FREE_PROVIDER", "FREE_MODEL", "FREE_COLUMN", "VARIANTS", "AA", "ROWS", "RUNS", "EXPECTED", "MODEL_IDS",
    "CALLS_PER_PAIR", "FREE_MAX_IN_FLIGHT", "FREE_MAX_RETRIES", "FREE_FAIL_MAX", "FREE_FAIL_MIN_CALLS", "SMOKE_CALLS",
    "FreeTierDropped", "Caller", "SelfTest", "run_cells", "plan_calls", "free_tier_allowed", "smoke_verdict",
    "evidence_of", "judge_only_accuracy", "position_bias", "arm_tokens", "bloat_overhead", "aggregate",
    "cell_text", "proof_table", "render_md", "reading_notes",
]
