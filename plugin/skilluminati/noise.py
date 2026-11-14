"""In plain words: the same-vs-same check, pooled. Each run also judges two answers made the same
way (both without the skill) against each other. A firm pick is a pair where the Owl named the same
winner in both orders (outcome ``win`` or ``loss``); a tie, or a split between the orders, is not
firm. A fair judge should rarely make a firm pick between identical answers, so the share of firm
picks says how far to trust a lead from one run.

One run has at most a handful of these pairs, too few to read, so every finished run's outcomes are
pooled per (tested model, judge model) in ``verdicts.json`` under ``aa_pool``. Model ids go through
``recommend.model_key`` (no provider prefix, no ``[1m]`` tag), the same normaliser the verdict
binding uses. Only outcome labels and run ids are stored: no task text, answers or reasons.

This is a report figure only. It never changes a verdict, the judge or a rubric.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import copy as sb_copy
from .recommend import model_key  # the one model id normaliser (re-exported for run.py)

FIRM = ("win", "loss")
OUTCOMES = ("win", "loss", "tie")
#: Fewer pooled pairs than this: no figure, just the count so far.
MIN_PAIRS = 8
#: At or above this firm-pick share the report adds the "single-run leads are weak" note.
WEAK_SHARE = 0.5


def firm_counts(outcomes: Iterable[Any]) -> tuple[int, int]:
    """``(firm, n)``: firm picks and judged pairs. Error pairs are not judged and do not count."""
    got = [o for o in outcomes if o in OUTCOMES]
    return sum(1 for o in got if o in FIRM), len(got)


def figure(firm: int, n: int) -> dict[str, Any]:
    """The figure the report and page show: counts, share, and whether it is enough to read."""
    enough = n >= MIN_PAIRS
    share = firm / n if n else None
    return {"firm": firm, "n": n, "share": share, "enough": enough,
            "weak": bool(enough and share is not None and share >= WEAK_SHARE)}


def pool_figure(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    firm = n = 0
    for r in runs:
        f, k = firm_counts(r.get("outcomes") or [])
        firm, n = firm + f, n + k
    return {**figure(firm, n), "runs": len(runs)}


def _find(pool: list[Any], model: Optional[str], judge: Optional[str]) -> Optional[dict[str, Any]]:
    return next((p for p in pool if isinstance(p, dict) and p.get("model") == model and p.get("judge") == judge), None)


def add_run(pool: list[Any], *, model: Any, judge: Any, run: Any, date: Any,
            outcomes: Iterable[Any]) -> Optional[dict[str, Any]]:
    """Add one run's same-vs-same outcomes to ``pool`` (the ``aa_pool`` list, changed in place) and
    return the pooled figure for its (model, judge), or None when the model is unknown or the run has
    no id. Adding the same run twice replaces it."""
    m, j = model_key(model), model_key(judge)
    if m is None or not (isinstance(run, str) and run):
        return None
    entry = _find(pool, m, j)
    if entry is None:
        entry = {"model": m, "judge": j, "runs": []}
        pool.append(entry)
    kept = [o for o in outcomes if o in OUTCOMES]
    entry["runs"] = [r for r in entry.get("runs") or [] if isinstance(r, dict) and r.get("run") != run]
    entry["runs"].append({"run": run, "date": date, "outcomes": kept})
    return pool_figure(entry["runs"])


def _models_from_calls(run_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """Subject and judge model ids from a run's tracked call rows (no text), for runs saved before
    ``result.json`` carried them."""
    subject = judge = None
    for f in sorted(run_dir.glob("calls*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if not isinstance(c, dict) or not isinstance(c.get("model"), str):
                continue
            if c.get("role") == "subject" and subject is None:
                subject = c["model"]
            elif c.get("role") == "judge" and judge is None:
                judge = c["model"]
    return subject, judge


def seed_from_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """A first ``aa_pool`` built from the run folders already on disk: each finished verdict run's
    ``result.json`` A/A outcomes, model ids from ``result.json`` or else its call rows. Self-test
    runs carry no top-level ``aa`` and are skipped, as are runs whose models cannot be read."""
    pool: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return pool
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        try:
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        aa = res.get("aa") if isinstance(res, dict) else None
        if not isinstance(aa, dict) or not isinstance(aa.get("pairs"), list) or res.get("stopped_early"):
            continue
        model, judge = res.get("subject_model") or aa.get("model"), res.get("judge_model")
        if not model or not judge:
            cm, cj = _models_from_calls(d)
            model, judge = model or cm, judge or cj
        add_run(pool, model=model, judge=judge, run=d.name, date=None,
                outcomes=[p.get("outcome") for p in aa["pairs"] if isinstance(p, dict)])
    return pool


def of_aa(aa: Any) -> Optional[dict[str, Any]]:
    """The figure for a run's ``aa`` block (or a stored page row): the pooled one when present, else
    the run's own pairs. None with no A/A data."""
    if not isinstance(aa, Mapping):
        return None
    pool = aa.get("pool")
    if isinstance(pool, Mapping) and isinstance(pool.get("n"), int) and isinstance(pool.get("firm"), int):
        return figure(pool["firm"], pool["n"])
    if isinstance(aa.get("pooled"), int) and isinstance(aa.get("firm"), int):
        return figure(aa["firm"], aa["pooled"])
    if not isinstance(aa.get("wins"), int):
        return None
    w, l, t = (int(aa[k]) if isinstance(aa.get(k), int) else 0 for k in ("wins", "losses", "ties"))
    return figure(w + l, w + l + t)


def stored_row(rows: Any, model: Any, judge: Any) -> Optional[Mapping[str, Any]]:
    """The stored noise row (``verdicts.json`` ``aa``) for a model and judge, by the page's rule: the
    row for that model and judge, else the newest row for that model. Ids are compared through
    ``model_key``. None when no row is for that model."""
    m, j = model_key(model), model_key(judge)
    if m is None or not isinstance(rows, list):
        return None
    mine = [r for r in rows if isinstance(r, Mapping) and model_key(r.get("model")) == m]
    same = [r for r in mine if model_key(r.get("judge")) == j]
    return (same or mine or [None])[-1]


def stored_figure(model: Any, judge: Any) -> Optional[dict[str, Any]]:
    """The pooled figure from the stored noise row in ``verdicts.json``, or None (no file, no row,
    a row saved before pooling). Read only; never raises."""
    from . import paths as sb_paths
    try:
        data = json.loads(sb_paths.verdicts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    row = stored_row(data.get("aa") if isinstance(data, dict) else None, model, judge)
    if row is None or not isinstance(row.get("pooled"), int):
        return None
    return of_aa(row)


def line(fig: Mapping[str, Any]) -> str:
    """``Same-vs-same: a firm pick 33 of 51 times (65%).`` or the not-enough line."""
    if not fig.get("enough"):
        return sb_copy.SAME_VS_SAME_FEW.format(n=fig.get("n", 0))
    pct = int(round(100 * fig["firm"] / fig["n"]))
    return sb_copy.SAME_VS_SAME.format(firm=fig["firm"], n=fig["n"], pct=pct)


def lines(fig: Optional[Mapping[str, Any]]) -> list[str]:
    """The figure line, plus the weak-leads note when the share is 50% or more."""
    if not fig:
        return []
    return [line(fig)] + ([sb_copy.SAME_VS_SAME_WEAK] if fig.get("weak") else [])


__all__ = ["MIN_PAIRS", "WEAK_SHARE", "firm_counts", "figure", "pool_figure", "add_run",
           "seed_from_runs", "of_aa", "stored_row", "stored_figure", "line", "lines", "model_key"]
