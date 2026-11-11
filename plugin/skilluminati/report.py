"""In plain words: turns a finished test into words people can read: the verdict, the counts
(Better / Same / Worse) and the detail behind them. It also tells the page which test is running.
It runs when a test ends, when the page refreshes and on /skilluminati last, and only reads.

The finished-run report: one shape for the agent tool, the Desktop page and ``/skilluminati last``.

Built from a run folder's ``result.json`` (and, only to name the model on older runs, the model
field of its ``calls*.jsonl`` rows, which hold no text). No model calls, nothing read outside the
plugin's data folder. Two renderings of the same facts: ``markdown`` (the agent and the page) and
``plain`` (the terminal: one fact per line, no table, the folder path last).

Standard library only and no package imports beyond siblings, so the dashboard backend can load it.
"""

from __future__ import annotations

import json
import re
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import copy as sb_copy
from . import paths as sb_paths

REPORT_FILE = "report.md"
RESULT_FILE = "result.json"
#: Placeholders older runs wrote where the model id belongs.
_NOT_A_MODEL = {"", "active", "active model", "none", "null", "unknown", "unknown model"}
#: Planted-skill order in the self-test table, then anything else.
SELFTEST_ORDER = ("helpful", "poisoned", "placebo", "bloat", "aa")
CAVEAT = sb_copy.REPORT_CAVEAT  # kept for importers; the words live in copy.py


# ── small helpers ────────────────────────────────────────────────────────────

def plural(n: int, word: str) -> str:
    """``1 task``, ``2 tasks``. Never "1 more tasks"."""
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def real_model(value: Any) -> Optional[str]:
    """``value`` when it names a model, else None (placeholders like "active model" are not names)."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    return None if v.lower() in _NOT_A_MODEL else v


def minutes_text(minutes: Any) -> Optional[str]:
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
        return None
    m = max(1, int(round(minutes)))
    return plural(m, "minute")


def _int(x: Any) -> int:
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def run_kind(run_id: str) -> str:
    """``selftest`` or ``verdict`` from a run folder name (``<stamp>-selftest-quick``)."""
    return "selftest" if "-selftest-" in f"-{run_id}-" or run_id.split("-", 2)[-1].startswith("selftest") else "verdict"


def run_skill(run_id: str) -> Optional[str]:
    """The skill a verdict folder is for (``<date>-<time>-verdict-<skill>``), else None."""
    parts = run_id.split("-", 3)
    if len(parts) == 4 and parts[2] == "verdict":
        return parts[3]
    return None


# ── finding runs ─────────────────────────────────────────────────────────────

def finished_runs(runs_dir: Optional[Path] = None) -> list[Path]:
    """Run folders that hold a ``result.json``, newest first (folder names start with the stamp)."""
    root = runs_dir or sb_paths.runs_dir()
    try:
        dirs = [d for d in root.iterdir() if d.is_dir() and (d / RESULT_FILE).is_file()]
    except OSError:
        return []
    return sorted(dirs, key=lambda d: d.name, reverse=True)


def find_run(*, skill: Optional[str] = None, run: Optional[str] = None,
             runs_dir: Optional[Path] = None) -> Optional[Path]:
    """The run folder to report: by id, else the newest for ``skill`` ("selftest" = self-tests),
    else the newest of all. Only names directly under the runs folder are accepted."""
    root = runs_dir or sb_paths.runs_dir()
    if run:
        name = Path(str(run).strip()).name
        if not name or name in {".", ".."}:
            return None
        cand = root / name
        return cand if (cand / RESULT_FILE).is_file() else None
    matches = []
    for d in finished_runs(root):
        if not skill:
            return d
        s = str(skill).strip().lower()
        if s in {"selftest", "self-test"}:
            if run_kind(d.name) == "selftest":
                matches.append(d)
        elif (run_skill(d.name) or "").lower() == sb_paths.safe_name(s).lower():
            matches.append(d)
    # By name, the newest run that finished (a run stopped by a plugin reload hid
    # the finished verdict). A stopped run is returned only when there is nothing better.
    for d in matches:
        if not (load_result(d) or {}).get("stopped_early"):
            return d
    return matches[0] if matches else None


def load_result(run_dir: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads((run_dir / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def models_from_calls(run_dir: Path) -> list[str]:
    """Model ids that answered, from the run's call logs (the ``model`` field only; no text)."""
    seen: dict[str, None] = {}
    try:
        files = sorted(run_dir.glob("calls*.jsonl"))
    except OSError:
        return []
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        m = real_model(json.loads(line).get("model"))
                    except (ValueError, AttributeError):
                        continue
                    if m:
                        seen.setdefault(m, None)
        except OSError:
            continue
    return list(seen)


def _model_line(models: Iterable[Optional[str]]) -> str:
    names = [m for m in dict.fromkeys(m for m in models if m)]
    return ", ".join(names) if names else "not recorded"


# ── verdict run ──────────────────────────────────────────────────────────────

def _verdict_facts(result: Mapping[str, Any], run_dir: Optional[Path], skill: Optional[str]) -> Optional[dict[str, Any]]:
    per = result.get("per_skill") or {}
    if not isinstance(per, Mapping) or not per:
        return None
    name = skill if skill in per else next(iter(per))
    s = per[name]
    rec = s.get("recommendation") or {}
    head = rec.get("headline") or {}
    label = head.get("label") or s.get("user_label")
    themed = head.get("themed") or (sb_copy.headline(label) or (None, None))[0] or label
    plain_label = head.get("plain") or (sb_copy.headline(label) or (None, None))[1] or ""
    c = s.get("combined") or {}
    stamp = s.get("stamp") or {}
    subject = real_model(result.get("subject_model")) or real_model(stamp.get("model"))
    judge = real_model(result.get("judge_model")) or real_model(stamp.get("judge"))
    if not subject and run_dir is not None:
        seen = models_from_calls(run_dir)
        subject = seen[0] if seen else None
        judge = judge or (seen[0] if len(seen) == 1 else None)
    numbers = rec.get("numbers") or {}
    ns = rec.get("next_step") or {}
    heavy = s.get("heavy") or {}
    flags = [f for f in rec.get("flags") or [] if isinstance(f, Mapping)
             and f.get("key") not in {"heavy", "few_tasks", "partial"}]
    next_key, next_text = ns.get("key"), ns.get("text")
    next_n = ns.get("n") if isinstance(ns.get("n"), int) else numbers.get("tasks_to_settle")
    if next_key == "run_more" and s.get("unused_tasks") == 0:  # every task in the history was used
        next_key, next_text, next_n = "more_history", sb_copy.NEXT_NO_HISTORY, None
    t = result.get("totals") or {}
    return {
        "skill": name, "label": label, "themed": themed, "plain": plain_label,
        "confidence": ((rec.get("confidence") or {}).get("level") or "").lower() or None,
        "subject": subject, "judge": judge,
        "better": _int(c.get("wins")), "worse": _int(c.get("losses")), "same": _int(c.get("ties")),
        "harm": _int(s.get("harm_cited_pairs")), "n_tasks": _int(s.get("n_tasks")), "errors": _int(s.get("n_errors")),
        "heavy_tokens": _int(heavy.get("over_tokens")) if heavy.get("flag") else None,
        "flags": [str(f.get("text")) for f in flags if f.get("text")],
        "next_key": next_key, "next_n": next_n, "next_text": next_text,
        "aa": result.get("aa") if isinstance(result.get("aa"), Mapping) else None,
        "stopped": result.get("stopped_early"),
        "calls": _int(t.get("calls")), "tokens": _int(t.get("total_tokens")), "minutes": t.get("minutes"),
    }


def next_step_text(key: Any, n: Any, fallback: Any = None) -> Optional[str]:
    """The next step in plain English, lower case after a colon. Fixes "1 more tasks"."""
    if key == "run_more" and isinstance(n, int) and n > 0:
        return f"run about {plural(n, 'more use')}"
    if isinstance(fallback, str) and fallback:
        text = fallback.rstrip().rstrip(".")  # the caller adds the full stop
        return text[0].lower() + text[1:] if text else None
    return None


_LIMIT_RE = re.compile(r"UsageLimit|RateLimit|\b429\b|rate.?limit|usage limit|hit your [\w-]+ limit", re.I)
_RESET_RE = re.compile(r"\bresets (?:at )?(\d{1,2}(?::\d{2})?\s?[ap]m)\b", re.I)
_CLASS_PREFIX = re.compile(r"^(?:[A-Z][A-Za-z]+:\s*)+")
#: Stops the plugin raises itself, in plain words (llm.py classes).
_KNOWN_STOPS = {
    "CapExceeded": "the run reached its call limit",
    "ModelMismatch": "an answer came from a different model than planned",
    "RunawayAnswer": "one answer ran far too long",
}
STOP_REASON_MAX = 80


def stop_reason(stop: Any) -> Optional[tuple[str, bool]]:
    """``(reason, is_usage_limit)`` in plain words, lower case, no full stop; None when the run did
    not stop early. A usage or rate limit gives one fixed line (plus the reset time when the provider
    gave a plain one); anything else is cut to under 80 characters. The raw error stays in
    ``result.json``."""
    if not isinstance(stop, Mapping):
        return None
    reason, error = str(stop.get("reason") or "").strip(), str(stop.get("error") or "").strip()
    blob = f"{reason} {error}"
    if _LIMIT_RE.search(blob):
        text = "your provider's usage limit was reached"
        m = _RESET_RE.search(error)
        if m:
            text += f" (it resets at {m.group(1).replace(' ', '').lower()})"
        return text, True
    for cls, text in _KNOWN_STOPS.items():
        if cls in blob:
            return text, False
    short = _CLASS_PREFIX.sub("", error.replace("\n", " ")).strip()
    short = re.sub(r"\s*\(request_id=[^)]*\)", "", short)
    short = re.split(r"(?<=[.!?])\s|\s\(", short, maxsplit=1)[0].strip().rstrip(".") or reason or "the run was stopped"
    if len(short) >= STOP_REASON_MAX:
        short = short[:STOP_REASON_MAX - 4].rstrip() + "..."
    return short, False


def stop_text(stop: Any) -> Optional[str]:
    """The one plain stop line for reports, progress and the terminal."""
    return _stop_text(stop)


def _stop_text(stop: Any) -> Optional[str]:
    got = stop_reason(stop)
    if got is None:
        return None
    text, limit = got
    return f"Stopped early: {text}. This is a partial result." if limit else f"Stopped early: {text}."


def _report_lines(f: Mapping[str, Any], bold: bool = False) -> list[str]:
    """The finished report, one fact per line. Lines: verdict, counts, uses tested, model, flags, the Owl's own check, caveat, cost."""
    strength = sb_copy.STRENGTH.get(f["confidence"] or "", "")
    head = f"{f['skill']}: {f['themed']}" + (f" ({strength})" if strength else "") + "."
    lines = [(f"**{head}**" if bold else head) + (f" {f['plain']}" if f["plain"] else "")]
    if f["stopped"]:
        lines.append(f"{sb_copy.FLAG_PARTIAL}.")
    lines.append(sb_copy.REPORT_COUNTS.format(w=f["better"], t=f["same"], l=f["worse"])
                 + (f" Could not be judged {f['errors']}." if f["errors"] else ""))
    if f["n_tasks"]:
        lines.append(sb_copy.REPORT_USES.format(n=plural(f["n_tasks"], "use")))
    model = re.sub(r"\[[^\]]*\]$", "", f["subject"] or "") or "not recorded"
    lines.append(sb_copy.REPORT_MODEL.format(model=model))
    if f["label"] == "Gets in the way" and f["harm"]:
        lines.append(f"Caught doing harm on {plural(f['harm'], 'use')}.")
    if f["heavy_tokens"]:
        lines.append(f"Heavy: adds {f['heavy_tokens']:,} tokens each time it loads.")
    lines += [f"{t}." if not str(t).endswith(".") else str(t) for t in f["flags"]]
    if f["next_key"] in ("run_more", "more_history"):  # Unproven only: the verdict line already says keep / fix
        step = next_step_text(f["next_key"], f["next_n"], f["next_text"])
        if step:
            lines.append(f"Next: {step}.")
    aa = f["aa"]
    if aa:
        lines.append(sb_copy.REPORT_NOISE.format(w=_int(aa.get("wins")), t=_int(aa.get("ties")),
                                                 l=_int(aa.get("losses")),
                                                 noisy=sb_copy.REPORT_NOISY if aa.get("noisy") else ""))
    stop = _stop_text(f["stopped"])
    if stop:
        lines.append(stop)
    return lines + [sb_copy.REPORT_CAVEAT, _cost_line(f)]


def verdict_markdown(f: Mapping[str, Any]) -> str:
    """Chat shape: the verdict line in bold, then one fact per line."""
    return "\n".join(_report_lines(f, bold=True))


def _cost_line(f: Mapping[str, Any]) -> str:
    bits = [plural(f["calls"], "call"), plural(f["tokens"], "token")]
    m = minutes_text(f["minutes"])
    if m:
        bits.append(m)
    return ", ".join(bits) + "."


def verdict_plain(f: Mapping[str, Any]) -> str:
    """Terminal shape: same lines, no bold."""
    return "\n".join(_report_lines(f))


# ── self-test ────────────────────────────────────────────────────────────────

def _selftest_facts(result: Mapping[str, Any], run_dir: Optional[Path]) -> Optional[dict[str, Any]]:
    run = result.get("run") or {}
    agg = result.get("aggregate") or {}
    cells = agg.get("cells") or {}
    if not isinstance(cells, Mapping):
        return None
    name = run.get("run") or "quick"
    model = real_model(run.get("subject_model"))
    if not model and run_dir is not None:
        seen = models_from_calls(run_dir)
        model = _model_line(seen) if seen else None
    rows = []
    keys = sorted(cells, key=lambda k: (SELFTEST_ORDER.index(k.split("/")[0]) if k.split("/")[0] in SELFTEST_ORDER
                                        else len(SELFTEST_ORDER), k))
    for key in keys:
        c = cells[key]
        kind = key.split("/")[0]
        label = c.get("label")
        head = sb_copy.headline(label)
        job = c.get("job") or {}
        harm = c.get("harm") or {}
        n = _int(c.get("n"))
        w, l, t = _int(job.get("wins")), _int(job.get("losses")), _int(job.get("ties"))
        if label == "Gets in the way" and harm.get("found"):
            detail = f"caught doing harm on {_int(harm.get('tasks'))} of {plural(n, 'task')}"
        elif w and not l and not t:
            detail = f"better on {w} of {plural(n, 'task')}"
        else:
            parts = [p for p in (w and f"better on {w}", l and f"worse on {l}", t and f"no difference on {t}") if p]
            detail = ", ".join(parts) or "no decisive tasks"
        heavy = (c.get("heavy") or {})
        if heavy.get("flag"):
            detail += f"; Heavy, adds {_int(heavy.get('over_tokens')):,} tokens per load"
        rows.append({
            "name": sb_copy.trial_name(kind) or "A/A (no skill against no skill)",
            "verdict": head[0] if head else (label or "no result"),
            "expected": c.get("matches_expected"),
            "detail": detail,
        })
    checked = [r for r in rows if r["expected"] is not None]
    passed = sum(1 for r in rows if r["expected"] is True)
    t = run.get("totals") or {}
    return {"run": name, "model": model, "rows": rows, "passed": passed, "checked": len(checked),
            "calls": _int(t.get("calls")), "tokens": _int(t.get("total_tokens")), "minutes": t.get("minutes"),
            "stopped": run.get("stopped_early")}


def selftest_facts(result: Mapping[str, Any], run_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Public: the self-test facts the page shows (planted skill, verdict, expected, detail)."""
    return _selftest_facts(result, run_dir)


def _yes(v: Any) -> str:
    return "Yes" if v is True else "No" if v is False else "Not checked"


def _selftest_title(f: Mapping[str, Any]) -> str:
    if f["stopped"]:
        return f"Skilluminati self-test ({f['run']}): stopped early, {f['passed']} of {f['checked']} as expected so far"
    return f"Skilluminati self-test ({f['run']}): {f['passed']} of {f['checked']} as expected"


def selftest_markdown(f: Mapping[str, Any]) -> str:
    head = f"**{_selftest_title(f)}**"
    m = minutes_text(f["minutes"])
    line2 = f"Model: {f['model'] or 'not recorded'}. {plural(f['calls'], 'call')}" + (f", {m}." if m else ".")
    table = ["| Planted skill | Verdict | Expected? | Detail |", "|---|---|---|---|",
             *[f"| {r['name']} | {r['verdict']} | {_yes(r['expected'])} | {r['detail']} |" for r in f["rows"]]]
    out = [head, line2, "", *table]
    stop = _stop_text(f["stopped"])
    if stop:
        out += ["", stop]
    return "\n".join(out)


def selftest_plain(f: Mapping[str, Any]) -> str:
    m = minutes_text(f["minutes"])
    lines = [_selftest_title(f),
             f"Model: {f['model'] or 'not recorded'}",
             f"{plural(f['calls'], 'call')}" + (f", {m}" if m else "")]
    lines += [f"{r['name']}: {r['verdict']}, expected: {_yes(r['expected']).lower()}, {r['detail']}" for r in f["rows"]]
    stop = _stop_text(f["stopped"])
    if stop:
        lines.append(stop)
    return "\n".join(lines)


# ── entry points ─────────────────────────────────────────────────────────────

def build(run_dir: Path, *, plain: bool = False) -> Optional[str]:
    """The report for one finished run folder; None when its result cannot be read."""
    result = load_result(run_dir)
    if result is None:
        return None
    if run_kind(run_dir.name) == "selftest" or "aggregate" in result:
        f = _selftest_facts(result, run_dir)
        if f is None:
            return None
        return selftest_plain(f) if plain else selftest_markdown(f)
    f = _verdict_facts(result, run_dir, run_skill(run_dir.name))
    if f is None:
        stop = _stop_text(result.get("stopped_early")) or "The run finished with no verdict."
        skill = run_skill(run_dir.name) or "this skill"
        return f"No verdict for {skill}. {stop}"
    return verdict_plain(f) if plain else verdict_markdown(f)


def headline(run_dir: Path) -> Optional[str]:
    """One short line for a popup: no model id, no path (under 120 characters)."""
    result = load_result(run_dir)
    if result is None:
        return None
    if run_kind(run_dir.name) == "selftest" or "aggregate" in result:
        f = _selftest_facts(result, run_dir)
        if f is None:
            return None
        if f["stopped"]:
            return f"Skilluminati self-test: stopped early, {f['passed']} of {f['checked']} as expected so far."
        return f"Skilluminati self-test: {f['passed']} of {f['checked']} as expected."
    f = _verdict_facts(result, run_dir, run_skill(run_dir.name))
    if f is None:
        return f"Skilluminati: no verdict for {run_skill(run_dir.name) or 'this skill'}."
    got = stop_reason(f["stopped"])
    if got:
        text = f"Skilluminati: {f['skill']} stopped early: {got[0]}."
    else:
        text = f"Skilluminati: {f['skill']}: {f['themed']}." + (f" {f['plain']}" if f["plain"] else "")
    return text if len(text) < 120 else text[:116] + "..."


def save(run_dir: Path) -> Optional[Path]:
    """Write ``report.md`` (0600) beside ``result.json`` so the tool works after a restart."""
    text = build(run_dir)
    if text is None:
        return None
    path = sb_paths.private_file(run_dir / REPORT_FILE)
    path.write_text(text + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def saved_or_built(run_dir: Path) -> Optional[str]:
    """The saved ``report.md`` when present, else built from ``result.json``."""
    p = run_dir / REPORT_FILE
    try:
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    return build(run_dir)


def terminal(run_dir: Path) -> Optional[str]:
    """``/skilluminati last``: the plain report, then the folder path on its own line."""
    text = build(run_dir, plain=True)
    return None if text is None else f"{text}\nFolder: {run_dir}"


# ── the Desktop page ────────────────────────────────────────────────────
# One row per tested skill: a plain verdict key, a strength, the score, and the working behind it.

VERDICT_KEYS = ("helps", "no_difference", "hurts", "not_enough")
STRENGTHS = ("strong", "likely")
#: Old stored words (verdict, themed word, internal label) to the new keys. Never raises on unknowns.
_OLD_KEYS = {
    "keep": "helps", "illuminated": "helps", "earns its keep": "helps", "helps": "helps",
    "cast out": "hurts", "obscures": "hurts", "gets in the way": "hurts", "drop": "hurts", "hurts": "hurts",
    "unsure": "not_enough", "still watching": "not_enough", "not sure yet": "not_enough",
    "not_enough": "not_enough", "not enough": "not_enough",
    "already known": "no_difference", "model does this already": "no_difference",
    "no_difference": "no_difference", "no difference": "no_difference", "dead weight": "no_difference",
}
_CONF_STRENGTH = {"strong": "strong", "moderate": "likely", "weak": "likely"}


def _rec(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """``(recommendation, headline)`` of a stored row; empty dicts when missing or malformed."""
    rec = row.get("recommendation") if isinstance(row.get("recommendation"), Mapping) else {}
    return rec, rec.get("headline") if isinstance(rec.get("headline"), Mapping) else {}


def verdict_key(row: Mapping[str, Any]) -> str:
    """``helps`` | ``no_difference`` | ``hurts`` | ``not_enough`` for a stored row, old or new."""
    rec, head = _rec(row)
    lean = rec.get("lean") if isinstance(rec.get("lean"), Mapping) else {}
    for v in (lean.get("verdict"), row.get("verdict_key"), row.get("lean"), row.get("key"), head.get("key"), row.get("user_label"),
              head.get("label"), row.get("verdict"), head.get("themed")):
        k = _OLD_KEYS.get(v.strip().lower()) if isinstance(v, str) else None
        if k:
            return k
    return "not_enough"


def verdict_strength(row: Mapping[str, Any], key: Optional[str] = None) -> Optional[str]:
    """``strong`` | ``likely`` | None (no strength for "not enough")."""
    if (key or verdict_key(row)) == "not_enough":
        return None
    rec, head = _rec(row)
    lean = rec.get("lean") if isinstance(rec.get("lean"), Mapping) else {}
    for v in (lean.get("strength"), row.get("strength"), rec.get("strength"), head.get("strength")):
        if isinstance(v, str) and v.lower() in STRENGTHS:
            return v.lower()
    conf = rec.get("confidence") if isinstance(rec.get("confidence"), Mapping) else {}
    level = conf.get("level") if isinstance(conf.get("level"), str) else ""
    return _CONF_STRENGTH.get(level.lower(), "likely")


def _pct(x: Any) -> Optional[int]:
    return int(round(x * 100)) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _noise(aa: Any) -> Optional[dict[str, Any]]:
    """The noise check (skill-free answer judged against itself) as plain counts, else None."""
    if not isinstance(aa, Mapping) or not isinstance(aa.get("wins"), int):
        return None
    return {"wins": _int(aa.get("wins")), "losses": _int(aa.get("losses")), "ties": _int(aa.get("ties")),
            "noisy": aa.get("noisy") is True}


def _skill_block(result: Mapping[str, Any], skill: str) -> Mapping[str, Any]:
    per = result.get("per_skill") if isinstance(result.get("per_skill"), Mapping) else {}
    s = per.get(skill)
    return s if isinstance(s, Mapping) else {}


def _run_for(row: Mapping[str, Any], runs_dir: Path) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    """The finished run folder whose score matches this stored row (a later partial run keeps the
    earlier verdict, so the newest folder is not always the right one). ``(None, None)`` if none."""
    skill = row.get("skill")
    named = row.get("run")
    if isinstance(named, str) and "/" not in named and not named.startswith("."):
        d = runs_dir / named
        result = load_result(d) if (d / RESULT_FILE).is_file() else None
        if result is not None:
            return d, result
    want = (_int(row.get("wins")), _int(row.get("losses")), _int(row.get("ties")))
    for d in finished_runs(runs_dir):
        if run_skill(d.name) != skill:
            continue
        result = load_result(d)
        comb = _skill_block(result or {}, skill).get("combined")
        if isinstance(comb, Mapping) and (_int(comb.get("wins")), _int(comb.get("losses")), _int(comb.get("ties"))) == want:
            return d, result
    return None, None


def working(run_dir: Optional[Path], skill: str, result: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The detail behind one verdict, from its run folder. Plain scalars and short lists only."""
    out: dict[str, Any] = {"run": run_dir.name if run_dir else None, "tasks": [], "order": None,
                           "bias_pct": None, "noise": None, "calls": None, "tokens": None, "judge": None,
                           "partial": False}
    result = result if result is not None else (load_result(run_dir) if run_dir else None)
    if not result:
        return out
    s = _skill_block(result, skill)
    pairs = [p for p in (s.get("pairs") if isinstance(s.get("pairs"), list) else []) if isinstance(p, Mapping)]
    out["tasks"] = [p.get("outcome") if p.get("outcome") in ("win", "loss", "tie") else "error" for p in pairs]
    if pairs:
        js = s.get("judge_stats") if isinstance(s.get("judge_stats"), Mapping) else {}
        flips = sum(1 for p in pairs if isinstance(p.get("judge"), Mapping) and p["judge"].get("agreed") is False)
        out["order"] = {"flips": _int(js["disagreements"]) if "disagreements" in js else flips, "n": len(pairs)}
    pb = result.get("position_bias") if isinstance(result.get("position_bias"), Mapping) else {}
    if pb.get("flagged") is True:
        out["bias_pct"] = _pct(pb.get("share_of_decisive"))
    out["noise"] = _noise(result.get("aa"))
    t = result.get("totals") if isinstance(result.get("totals"), Mapping) else {}
    out["calls"], out["tokens"] = _int(t.get("calls")) or None, _int(t.get("total_tokens")) or None
    out["judge"] = real_model(result.get("judge_model"))
    out["partial"] = bool(result.get("stopped_early"))
    return out


def page_result(row: Mapping[str, Any], runs_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """One results row for the page from a stored ``verdicts.json`` row (old or new keys). None when
    the row names no skill. ``row["_aa"]`` (optional) is the stored noise check for its model."""
    skill = row.get("skill")
    if not isinstance(skill, str) or not skill.strip():
        return None
    key = verdict_key(row)
    wins, losses, ties = _int(row.get("wins")), _int(row.get("losses")), _int(row.get("ties"))
    folder, result = _run_for(row, runs_dir) if runs_dir is not None else (None, None)
    w = working(folder, skill, result)
    w["noise"] = w["noise"] or _noise(row.get("_aa"))
    w["judge"] = w["judge"] or real_model(row.get("judge"))
    flags = _rec(row)[0].get("flags")
    w["partial"] = w["partial"] or any(isinstance(f, Mapping) and f.get("key") == "partial"
                                       for f in (flags if isinstance(flags, list) else []))
    counts = row.get("counts") if isinstance(row.get("counts"), Mapping) else {}
    if not counts and isinstance(result, Mapping) and isinstance(result.get("counts"), Mapping):
        counts = result["counts"]
    return {
        "skill": skill, "key": key, "strength": verdict_strength(row, key), "run": w.get("run"),
        "source": row.get("source") if isinstance(row.get("source"), str) else None,
        # times the skill was opened, how many could be replayed, how many this test used (None if unknown)
        "uses": _int(counts["uses"]) if isinstance(counts.get("uses"), int) else None,
        "eligible": _int(counts["eligible"]) if isinstance(counts.get("eligible"), int) else None,
        "tested": _int(counts["tested"]) if isinstance(counts.get("tested"), int) else None,
        "wins": wins, "losses": losses, "ties": ties, "n": wins + losses + ties,
        "model": real_model(row.get("model")), "date": row.get("date") if isinstance(row.get("date"), str) else None,
        "low": _pct(row.get("low")), "high": _pct(row.get("high")), "working": w,
    }


_JUDGED = re.compile(r":\s*(win|loss|tie|error)\s*$")
RUNNING_STALE_S = 3 * 60


LIVE_STALE_S = 60  # live.json is rewritten every 4 s while a run goes; older than this, the run is gone


def running(runs_dir: Optional[Path] = None, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """The test going right now: ``{run, skill, done, total, line, session_id}`` or None.

    First choice is ``live.json`` beside the runs folder: the run writes it every few seconds with the
    same numbers and line as its popup, and deletes it when it ends (the page said
    0 of 16 while the popup said 11 of 16, and a failed run still showed as running). Without it (a
    run from the terminal), a run folder with no result that has logged a call in the last 3 minutes."""
    root = runs_dir or sb_paths.runs_dir()
    now = time.time() if now is None else now
    try:
        live = json.loads((root.parent / "live.json").read_text(encoding="utf-8"))
        if isinstance(live, Mapping) and now - float(live.get("updated") or 0) < LIVE_STALE_S:
            return {"run": "", "skill": str(live.get("skill") or "") or None,
                    "done": live.get("done") if isinstance(live.get("done"), int) else None,
                    "total": live.get("total") if isinstance(live.get("total"), int) and live.get("total") else None,
                    "line": str(live.get("line") or ""), "session_id": str(live.get("session_id") or "")}
    except (OSError, ValueError, TypeError):
        pass
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)[:5]
    except OSError:
        return None
    for d in dirs:
        if (d / RESULT_FILE).is_file():
            continue
        try:
            stamps = [f.stat().st_mtime for f in d.iterdir() if f.is_file()] or [d.stat().st_mtime]
        except OSError:
            continue
        if now - max(stamps) > RUNNING_STALE_S or not (d / "calls.jsonl").is_file():
            continue  # stale, or never made a call (a start that failed): not running
        done = total = None
        try:
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            per = plan.get("per_skill") if isinstance(plan.get("per_skill"), Mapping) else {}
            total = sum(_int(v.get("tasks")) for v in per.values() if isinstance(v, Mapping))
            aa = plan.get("aa") if isinstance(plan.get("aa"), Mapping) else {}
            total += _int(aa.get("tasks"))
        except (OSError, ValueError, AttributeError):
            total = None
        try:
            with (d / "progress.log").open(encoding="utf-8") as fh:
                done = sum(1 for line in fh if _JUDGED.search(line))
        except OSError:
            done = None
        if not total:
            total = done = None
        return {"run": d.name, "skill": run_skill(d.name), "done": done, "total": total}
    return None


__all__ = ["REPORT_FILE", "CAVEAT", "plural", "real_model", "finished_runs", "find_run", "load_result",
           "models_from_calls", "next_step_text", "build", "headline", "save", "saved_or_built", "terminal",
           "run_kind", "run_skill", "VERDICT_KEYS", "verdict_key", "verdict_strength", "working",
           "page_result", "running"]
