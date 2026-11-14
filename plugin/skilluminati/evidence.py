"""In plain words: ties a verdict to the exact skill file and model it was made on, so a result
shown later can say when the skill changed or you now run a different model. It runs when a test
ends (to note the file's fingerprint) and whenever a result is shown. It only reads files.

Two show-time flags, worked out when a result is shown, never stored as true:

* Skill changed: the SHA-256 of the SKILL.md bytes stored with the verdict differs from the file
  on disk now. Rows saved before fingerprints were stored have none: no flag.
* Stale: the model the verdict was made on differs from the model a new test would run on
  (``run.tested_model_id``: the ``subject.model`` setting when set, else the active default; unknown when
  only ``subject.provider`` names another provider). Unknown on either side: no flag.

Standard library only and sibling imports only, so the dashboard backend can load it.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from . import copy as sb_copy
from .recommend import model_key  # the one model id normaliser

logger = logging.getLogger(__name__)

FLAG_STALE_KEY = "stale"
FLAG_SKILL_CHANGED_KEY = "skill_changed"
#: Seconds a live fingerprint is reused (the page asks on every refresh; finding a skill reads every SKILL.md).
LIVE_TTL_S = 30.0
_live_cache: dict[tuple[str, str], tuple[float, Optional[str]]] = {}


# ── the skill file's fingerprint ─────────────────────────────────────────────

def file_sha256(path: Any) -> Optional[str]:
    """SHA-256 of the file's bytes as read, or None when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def is_pack(source: Any) -> bool:
    """True when a stored source names the built-in question set (its own copy of the skill)."""
    return isinstance(source, str) and source.strip().lower() in {"pack", sb_copy.SOURCE_PACK.lower()}


def live_skill_file(skill: str, source: Any = None) -> Optional[Path]:
    """The SKILL.md a new test of ``skill`` would read now: the built-in set's copy for a pack
    result, else the installed one. None when there is none."""
    from . import realrun
    if is_pack(source):
        from . import packs
        return realrun.find_skill_file(packs.PACKS_DIR / skill, skill)
    return realrun.find_skill_file(realrun.skill_home(skill), skill)


def live_skill_sha256(skill: str, source: Any = None, *, fresh: bool = False) -> Optional[str]:
    """The fingerprint of the skill file on disk now (cached ``LIVE_TTL_S``). None when not found."""
    key = (str(skill), "pack" if is_pack(source) else "history")
    hit = _live_cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < LIVE_TTL_S:
        return hit[1]
    try:
        path = live_skill_file(skill, source)
        value = file_sha256(path) if path is not None else None
    except Exception:
        value = None
    _live_cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _live_cache.clear()


# ── the model ────────────────────────────────────────────────────────────────

def active_model() -> Optional[str]:
    """The model a new test would run on now (the ``subject.model`` setting when set, else the
    active default); None when it cannot be read."""
    try:
        from . import run
        return run.tested_model_id()
    except Exception:
        return None


def same_model(a: Any, b: Any) -> Optional[bool]:
    """True or False when both are known; None when either is unknown. Only a provider prefix, a
    context tag and a trailing date are ignored, so sibling models (``gpt-5``, ``gpt-5-mini``) differ."""
    x, y = model_key(a), model_key(b)
    if x is None or y is None:
        return None
    return x == y


# ── the flags ────────────────────────────────────────────────────────────────

def show_flags(*, model: Any, skill_sha256: Any, active: Any, live_sha256: Any) -> list[dict[str, str]]:
    """The show-time flags for one stored verdict, each ``{"key", "text"}``. Pure."""
    out: list[dict[str, str]] = []
    if same_model(model, active) is False:
        out.append({"key": FLAG_STALE_KEY, "text": sb_copy.FLAG_STALE.format(tested=str(model).strip(),
                                                                              active=str(active).strip())})
    if isinstance(skill_sha256, str) and skill_sha256 and isinstance(live_sha256, str) and live_sha256 \
            and skill_sha256 != live_sha256:
        out.append({"key": FLAG_SKILL_CHANGED_KEY, "text": sb_copy.FLAG_SKILL_CHANGED})
    return out


def flags_for_row(row: Mapping[str, Any], *, active: Any = None, live_sha256: Any = None,
                  look: bool = True) -> list[dict[str, str]]:
    """Show-time flags for a ``verdicts.json`` row. ``look``: read the live file when ``live_sha256``
    is not given (only when the row has a stored fingerprint). Never raises."""
    try:
        sha = row.get("skill_sha256")
        if live_sha256 is None and look and isinstance(sha, str) and sha and isinstance(row.get("skill"), str):
            live_sha256 = live_skill_sha256(row["skill"], row.get("source"))
        return show_flags(model=row.get("model"), skill_sha256=sha, active=active, live_sha256=live_sha256)
    except Exception:
        return []


def merge_flags(existing: Any, extra: list[dict[str, str]]) -> list[Any]:
    """``existing`` flags plus ``extra``, one per key (a stored Stale flag is not repeated)."""
    base = list(existing) if isinstance(existing, list) else []
    keys = {f.get("key") for f in base if isinstance(f, Mapping)}
    return base + [f for f in extra if f["key"] not in keys]


# ── F01: asking again adds evidence ──────────────────────────────────────────
#
# Every judged pair of a finished run is kept in ``pools/<skill>.json`` under plugin-data, grouped by
# (skill-file SHA-256, tested model, judge model). A changed skill file, model or judge starts a fresh
# pool: versions are never mixed. Within a pool each task (by task hash) has one sample per run; its
# outcome is the sign of (better count minus worse count) over its samples, and zero counts as same
# (R1b: a same sample carries no direction, so it cannot outvote a better or a worse). Those per-task outcomes go into the unchanged ``verdict.lean`` rule. Only the skill's
# own pairs are pooled: same-vs-same noise pairs and the self-test never are. A run with no
# fingerprint (saved before this change) or one that stopped early is not pooled.

POOL_DIR = "pools"
POOL_SCHEMA = 1
OUTCOMES = ("win", "loss", "tie")
ERROR = "error"


def _empty_answer(pair: Mapping[str, Any]) -> bool:
    """True when either arm of a run pair came back empty (``EmptyAnswer``), or a pooled pair says so."""
    if pair.get("empty_answer") is True:
        return True
    from . import recommend as sb_recommend
    arms = pair.get("arms") if isinstance(pair.get("arms"), Mapping) else {}
    return any(isinstance(a, Mapping) and a.get("error_type") == sb_recommend._EMPTY_ANSWER for a in arms.values())


TOOL_COUNT_KEYS = ("tested", "tool_driven", "empty_answer", "count")


def run_tools(tools: Any) -> Optional[dict[str, int]]:
    """A run's own tool counts as stored in its pool entry (M3), or None when it has none."""
    if not isinstance(tools, Mapping):
        return None
    vals = {k: tools.get(k) for k in TOOL_COUNT_KEYS}
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in vals.values()):
        return None
    return vals  # type: ignore[return-value]


def pooled_tools(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The F05 tool-driven counts over every pooled use, same rule and threshold as one run
    (``recommend.tool_driven_counts``). Each run's own stored counts are summed (M3); a run pooled
    before they were stored falls back to its pairs' marks, and pairs pooled before R7 carry
    neither mark and count as neither."""
    from . import recommend as sb_recommend
    total = dict.fromkeys(TOOL_COUNT_KEYS, 0)
    for r in runs:
        if not isinstance(r, Mapping):
            continue
        own = run_tools(r.get("tools"))
        if own is None:
            uses = []
            pairs = r.get("pairs")
            for p in pairs if isinstance(pairs, list) else []:
                if isinstance(p, Mapping):
                    arms = {"with": {"error_type": sb_recommend._EMPTY_ANSWER}} if _empty_answer(p) else {}
                    uses.append({"tool_driven": p.get("tool_driven") is True, "arms": arms})
            own = {k: sb_recommend.tool_driven_counts(uses)[k] for k in TOOL_COUNT_KEYS}
        for k in TOOL_COUNT_KEYS:
            total[k] += own[k]
    return {**total, "flag": total["tested"] > 0
            and total["count"] >= sb_recommend.TOOL_DRIVEN_SHARE * total["tested"] - sb_recommend.EPS}


def pool_path(skill: str) -> Path:
    from . import paths as sb_paths
    return sb_paths.data_dir() / POOL_DIR / f"{sb_paths.safe_name(skill)}.json"


def task_outcome(samples: list[str]) -> Optional[str]:
    """One task's outcome over its samples (R1b): the sign of better minus worse. More better than
    worse is ``win``, more worse is ``loss``, equal is ``tie``; same samples carry no direction.
    None with no samples."""
    got = [s for s in samples if s in OUTCOMES]
    if not got:
        return None
    net = got.count("win") - got.count("loss")
    return "win" if net > 0 else "loss" if net < 0 else "tie"


def pooled_counts(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """``{wins, losses, ties, n_tasks, n_runs, tasks, tools}`` over the runs' pairs (``task_hash``,
    ``outcome``; error pairs never count toward the verdict). ``tools`` is ``pooled_tools``. Pure; the
    result does not depend on the order of ``runs``."""
    samples: dict[str, list[str]] = {}
    for r in runs:
        pairs = r.get("pairs") if isinstance(r, Mapping) else None
        for p in pairs if isinstance(pairs, list) else []:
            if not isinstance(p, Mapping):
                continue  # CODE-M1: one bad entry must not stop every later pooling of the skill
            h, o = p.get("task_hash"), p.get("outcome")
            if isinstance(h, str) and h and o in OUTCOMES:
                samples.setdefault(h, []).append(o)
    tasks = {h: task_outcome(v) for h, v in sorted(samples.items())}
    vals = list(tasks.values())
    return {"wins": vals.count("win"), "losses": vals.count("loss"), "ties": vals.count("tie"),
            "n_tasks": len(tasks), "n_runs": len(runs), "tasks": tasks, "tools": pooled_tools(runs)}


_THREAD_LOCK = threading.Lock()  # only used where fcntl is missing (Windows)


@contextlib.contextmanager
def _pool_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``<pool>.lock`` beside the pool for one read-modify-write, so
    concurrent writers (threads or processes) never lose a run. The lock is a sidecar file because
    the atomic replace changes the pool file itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        with _THREAD_LOCK:
            yield
        return
    with open(path.with_name(path.name + ".lock"), "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _set_aside(path: Path, why: str) -> None:
    """Rename an unreadable pool to ``<name>.bad-<timestamp>`` so it is kept, never overwritten."""
    bad = path.with_name(f"{path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.monotonic_ns()}")
    try:
        os.replace(path, bad)
        logger.warning("skilluminati: pool file %s could not be read (%s); kept as %s", path.name, why, bad.name)
    except OSError:
        logger.exception("skilluminati: pool file %s could not be read or set aside", path.name)
        raise


def _load_pool_file(path: Path, skill: str) -> dict[str, Any]:
    """The pool file, or a new empty one when there is none. A file that cannot be read or has the
    wrong shape is set aside (``_set_aside``) first. Call under ``_pool_lock``."""
    import json
    fresh = {"schema": POOL_SCHEMA, "skill": skill, "pools": []}
    if not path.is_file():
        return fresh
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _set_aside(path, type(exc).__name__)
        return fresh
    if not isinstance(data, dict) or not isinstance(data.get("pools"), list):
        _set_aside(path, "unexpected shape")
        return fresh
    return data


def _write_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """Write to a unique temp file in the same folder, then replace the pool in one step."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def add_run(skill: str, *, skill_sha256: Any, model: Any, judge: Any, run: str, date: Any,
            pairs: list[Mapping[str, Any]], tools: Any = None) -> Optional[dict[str, Any]]:
    """Add one finished run's judged pairs to its pool and return the pooled counts (see
    ``pooled_counts``), or None when the run cannot be pooled (no fingerprint, or no known model). Adding
    the same run twice replaces it. Locked and atomic (``_pool_lock``, ``_write_atomic``)."""
    if not (isinstance(skill_sha256, str) and skill_sha256 and model_key(model) is not None):
        return None  # an unknown model ("unknown", "active model") is never pooled with others
    key = {"skill_sha256": skill_sha256, "model": model.strip(), "judge": str(judge or "").strip() or None}
    match = {"skill_sha256": skill_sha256, "model": model_key(key["model"]), "judge": model_key(key["judge"])}
    # R7: each pair keeps its tool_driven and empty-answer marks (bools), so the pooled result applies
    # the F05 rule over every pooled use. Error pairs are kept for that count only (see pooled_counts).
    kept = [{"task_hash": p.get("task_hash"), "outcome": p.get("outcome") if p.get("outcome") in OUTCOMES else ERROR,
             "tool_driven": p.get("tool_driven") is True, "empty_answer": _empty_answer(p)} for p in pairs
            if isinstance(p, Mapping) and p.get("cell", skill) == skill
            and (p.get("outcome") in OUTCOMES or p.get("outcome") == ERROR) and isinstance(p.get("task_hash"), str)]
    path = pool_path(skill)
    with _pool_lock(path):
        return _add_locked(path, skill, key, match, run, date, kept, tools)


def _add_locked(path: Path, skill: str, key: dict[str, Any], match: dict[str, Any], run: str, date: Any,
                kept: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
    data = _load_pool_file(path, skill)
    pool = next((p for p in data["pools"] if isinstance(p, dict) and p.get("skill_sha256") == match["skill_sha256"]
                 and model_key(p.get("model")) == match["model"]
                 and model_key(p.get("judge")) == match["judge"]), None)
    if pool is None:
        pool = {**key, "runs": []}
        data["pools"].append(pool)
    pool["runs"] = [r for r in pool.get("runs") or [] if isinstance(r, dict) and r.get("run") != run]
    entry: dict[str, Any] = {"run": run, "date": date, "pairs": kept}
    own = run_tools(tools)  # M3: the run's own tested, tool-driven and empty-answer counts
    if own is not None:
        entry["tools"] = own
    pool["runs"].append(entry)
    _write_atomic(path, data)
    return pooled_counts(pool["runs"])


def pooled_block(counts: Mapping[str, Any], *, heavy: Any = None, model: Any = None,
                 first_share: Optional[float] = None, judge: Any = None) -> dict[str, Any]:
    """The verdict over pooled per-task outcomes: the same fields a run summary carries (``combined``,
    ``lean``, ``user_label``, ``effect``, ``recommendation``, ``tools``), from the unchanged rules. The
    tool-driven flag (R7) holds the shown strength at Plausible as in one run; ``judge`` feeds the
    same-model judge flag (F07)."""
    from . import recommend as sb_recommend
    from . import stats
    from . import verdict as sb_verdict
    w, l, t = int(counts["wins"]), int(counts["losses"]), int(counts["ties"])
    v = stats.verdict(w, l, t)
    combined = {"wins": w, "losses": l, "ties": t, "n_pairs": w + l + t, "verdict": v.verdict,
                "low": round(v.low, 4), "high": round(v.high, 4), "rate": v.rate, "n_decisive": v.n_decisive,
                "tasks_to_settle": v.tasks_to_settle}
    lean = sb_verdict.lean(w, l, t)
    label = sb_verdict.LEAN_LABEL[lean["verdict"]]
    tools = counts.get("tools") if isinstance(counts.get("tools"), Mapping) else None
    lean = sb_recommend.cap_lean(lean, tools) or lean  # shown strength only; the verdict is unchanged
    rec = sb_recommend.recommend(
        label, wins=w, losses=l, ties=t, low=combined["low"] if v.n_decisive else None,
        high=combined["high"] if v.n_decisive else None, tasks_to_settle=v.tasks_to_settle, heavy=heavy,
        verdict_model=model, active_model=model, lean=lean, job_first_share=first_share,
        judge_model=judge, tools=tools)
    return {"combined": combined, "lean": lean, "user_label": label,
            "effect": "negative" if lean["verdict"] == sb_verdict.HURTS else "none", "recommendation": rec,
            "n_tasks": int(counts["n_tasks"]), "n_runs": int(counts["n_runs"]), "tools": tools}


def pool_summary(summary: Mapping[str, Any], *, model: Any, judge: Any, run: str) -> Optional[dict[str, Any]]:
    """Add a finished run's summary to its pool; the pooled block, or None when it cannot be pooled."""
    counts = add_run(str(summary.get("skill")), skill_sha256=summary.get("skill_sha256"), model=model,
                     judge=judge, run=run, date=(summary.get("stamp") or {}).get("date"),
                     pairs=list(summary.get("pairs") or []), tools=summary.get("tools"))
    if counts is None or not counts["n_tasks"]:
        return None
    js = summary.get("judge_stats") if isinstance(summary.get("judge_stats"), Mapping) else {}
    first = js.get("first_picks") / js["decisive_judgements"] if js.get("decisive_judgements") else None
    return pooled_block(counts, heavy=summary.get("heavy"), model=model, first_share=first, judge=judge)


def effective(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    """The summary to show and store: its pooled verdict when it has one, else itself."""
    pool = summary.get("pool")
    if not isinstance(pool, Mapping) or not isinstance(pool.get("combined"), Mapping):
        return summary
    return {**summary, "combined": pool["combined"], "lean": pool.get("lean"), "user_label": pool.get("user_label"),
            "effect": pool.get("effect"), "recommendation": pool.get("recommendation"),
            "pool_tasks": pool.get("n_tasks"), "pool_runs": pool.get("n_runs"),
            "tools": pool.get("tools") if isinstance(pool.get("tools"), Mapping) else summary.get("tools")}


def pool_line(n_tasks: Any, n_runs: Any) -> Optional[str]:
    """``12 tasks, 3 runs`` (copy.POOL_LINE), or None when either count is missing."""
    if not (isinstance(n_tasks, int) and isinstance(n_runs, int)) or isinstance(n_tasks, bool) or n_runs < 1:
        return None
    return sb_copy.POOL_LINE.format(n=n_tasks, tasks="task" if n_tasks == 1 else "tasks",
                                    r=n_runs, runs="run" if n_runs == 1 else "runs")


__all__ = ["FLAG_STALE_KEY", "FLAG_SKILL_CHANGED_KEY", "file_sha256", "live_skill_file", "live_skill_sha256",
           "active_model", "same_model", "show_flags", "flags_for_row", "merge_flags", "clear_cache",
           "POOL_DIR", "pool_path", "task_outcome", "pooled_counts", "pooled_tools", "run_tools", "add_run", "pooled_block", "pool_summary",
           "effective", "pool_line"]
