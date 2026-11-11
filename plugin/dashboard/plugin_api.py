"""In plain words: hands the Skilluminati page its data: your installed skills, how often you used
each one, and the saved results. It runs each time the page loads or refreshes. It only reads; it
never changes a skill or a setting.

Skilluminati backend: GET /api/plugins/skilluminati/verdicts and /page.

Read-only. Serves ``verdicts.json`` and the latest self-test from the plugin's data folder, the
same folder the slash command writes to: both ask the package's ``paths.data_dir()``, which uses the
host's documented ``plugins.plugin_storage.plugin_data_dir`` (no hardcoded folder name, no private
core import).
The pure reader (``read_verdicts``) has no FastAPI dependency so it can be
unit tested without a server. The handler never raises.
"""

from __future__ import annotations

import importlib.util
import json
import time
import os
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
_MAX_BYTES = 8 * 1024 * 1024


_PKG_NAME = "_skilluminati_pkg"
_PKG_DIR = Path(__file__).resolve().parent.parent / "skilluminati"


def _load_pkg() -> Any:
    """Load the sibling ``skilluminati`` package under a private name (this file is loaded by path, so
    a relative import is not available). Returns the package, or None when it cannot be loaded."""
    mod = sys.modules.get(_PKG_NAME)
    if mod is not None:
        return mod
    try:
        spec = importlib.util.spec_from_file_location(
            _PKG_NAME, _PKG_DIR / "__init__.py", submodule_search_locations=[str(_PKG_DIR)])
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_PKG_NAME] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        sys.modules.pop(_PKG_NAME, None)
        return None


def _sub(name: str) -> Any:
    pkg = _load_pkg()
    if pkg is None:
        return None
    try:
        return importlib.import_module(f"{_PKG_NAME}.{name}")
    except Exception:
        return None


def copy_block() -> dict[str, Any] | None:
    """The owl copy (``copy.py``) the Desktop page renders. None when the package cannot load."""
    mod = _sub("copy")
    try:
        return mod.payload() if mod is not None else None
    except Exception:
        return None


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "runs": [], "aa": [], "position_bias": None, "empty": True}


def hermes_home() -> Path:
    """Hermes's home folder for the active profile, from Hermes itself (get_hermes_home).
    Outside Hermes (the test suite) it falls back to $HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def data_dir(home: Path | None = None) -> Path:
    """The folder the plugin writes to. ``home`` (tests) pins ``<home>/plugin-data/skilluminati``."""
    if home is not None:
        return home / "plugin-data" / "skilluminati"
    paths = _sub("paths")
    if paths is not None:
        try:
            return Path(paths.data_dir())
        except Exception:
            pass
    return hermes_home() / "plugin-data" / "skilluminati"


def verdicts_path(home: Path | None = None) -> Path:
    return data_dir(home) / "verdicts.json"


SELFTEST_ROW_KEYS = ("name", "verdict", "expected", "detail")


def latest_selftest(home: Path | None = None) -> dict[str, Any] | None:
    """The newest finished self-test: planted skills, expected against actual, the model. None when
    there is none or it cannot be read. Plain scalars only."""
    rep = _sub("report")
    if rep is None:
        return None
    try:
        folder = rep.find_run(skill="selftest", runs_dir=data_dir(home) / "runs")
        if folder is None:
            return None
        facts = rep.selftest_facts(rep.load_result(folder) or {}, folder)
        if not facts:
            return None
        rows = [{k: r.get(k) for k in SELFTEST_ROW_KEYS if isinstance(r.get(k), _SCALAR)} for r in facts["rows"]]
        stamp = folder.name.split("-", 2)
        date = f"{stamp[0][:4]}-{stamp[0][4:6]}-{stamp[0][6:8]}" if len(stamp) > 1 and stamp[0].isdigit() else None
        return {"run": str(facts["run"]), "model": facts["model"], "passed": facts["passed"],
                "checked": facts["checked"], "calls": facts["calls"], "date": date, "rows": rows}
    except Exception:
        return None


def _error(msg: str) -> dict[str, Any]:
    out = _empty()
    out["empty"] = False
    out["error"] = msg
    return out


HEAVY_FIELDS = ("load_tokens", "median_tokens", "ratio", "over_tokens", "over_pct", "flag", "text")


def _clean_heavy(value: Any) -> dict[str, Any] | None:
    """The Heavy cost flag as stored beside a verdict: only the known fields, ``flag`` a real
    bool, ``text`` only when flagged. None when absent or malformed. A flag, never a label."""
    if not isinstance(value, dict):
        return None
    out = {k: value.get(k) for k in HEAVY_FIELDS}
    out["flag"] = out["flag"] is True
    if not out["flag"] or not isinstance(out["text"], str):
        out["text"] = None
    return out


#: The only run keys the page receives (it reads skill, verdict, model, wins/losses/ties, low/high,
#: tasks_to_settle, heavy, recommendation, source). Anything else in the file is dropped.
RUN_KEYS = ("skill", "verdict", "effect", "user_label", "model", "judge", "date", "wins", "losses", "ties",
            "low", "high", "tasks_to_settle", "heavy", "recommendation", "source", "test_type", "stale", "run")
_SCALAR = (str, int, float, bool, type(None))
AA_KEYS = ("model", "wins", "losses", "ties", "low", "high", "noisy", "date")


def _scalars(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    """Known keys with plain scalar values only; None when ``value`` is not a dict."""
    if not isinstance(value, dict):
        return None
    return {k: value[k] for k in keys if k in value and isinstance(value[k], _SCALAR)}


def _number(value: Any) -> float | None:
    """The page reads ``position_bias`` as one number; anything else becomes None."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _clean_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {k: run[k] for k in RUN_KEYS if k in run and isinstance(run[k], _SCALAR)}
    if "heavy" in run:
        out["heavy"] = _clean_heavy(run.get("heavy"))
    if "recommendation" in run:
        out["recommendation"] = run.get("recommendation")  # whitelisted by recommend.clean below
    counts = _scalars(run.get("counts"), ("uses", "eligible", "tested"))
    if counts:
        out["counts"] = counts
    return out


def _run_with_heavy(run: dict[str, Any]) -> dict[str, Any]:
    return _clean_run(run)


def _run_with_recommendation(run: dict[str, Any]) -> dict[str, Any]:
    """Attach ``recommendation``: the stored one, whitelisted, else built from the row. Rows
    that cannot be told (for example a bare Drop, or the outside-scope row) get ``None``."""
    rec_mod = _sub("recommend")
    if rec_mod is None:
        return _drop_raw_recommendation(run)
    stored = rec_mod.clean(run.get("recommendation")) if "recommendation" in run else None
    return {**run, "recommendation": stored or rec_mod.from_run(run)}


def _drop_raw_recommendation(run: dict[str, Any]) -> dict[str, Any]:
    """When the recommend module cannot load, a stored recommendation is never passed through raw."""
    return {k: v for k, v in run.items() if k != "recommendation"}


def read_verdicts(home: Path | None = None) -> dict[str, Any]:
    """Return the verdicts payload, with the owl copy block attached. Never raises."""
    out = _read_verdicts(home)
    st = latest_selftest(home)
    if st is not None:
        out["selftest"] = st
        out["empty"] = False if not out.get("error") else out["empty"]
    block = copy_block()
    if block is not None:
        out["copy"] = block
    return out


def _read_verdicts(home: Path | None = None) -> dict[str, Any]:
    try:
        path = verdicts_path(home)
        if not path.is_file():
            return _empty()
        if path.stat().st_size > _MAX_BYTES:
            return _error("verdicts.json is too large to read")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _error("verdicts.json is not a JSON object")
        runs = data.get("runs")
        aa = data.get("aa")
        if not isinstance(runs, list) or not isinstance(aa if aa is not None else [], list):
            return _error("verdicts.json has no valid runs list")
        out: dict[str, Any] = {
            "schema": data.get("schema", SCHEMA),
            "generated_at": data.get("generated_at"),
            "runs": [_run_with_recommendation(_run_with_heavy(r)) for r in runs if isinstance(r, dict)],
            "aa": [c for c in (_scalars(r, AA_KEYS) for r in (aa or [])) if c is not None],
            "position_bias": _number(data.get("position_bias")),
            "empty": False,
        }
        if data.get("demo") is True:
            out["demo"] = True
        if not out["runs"] and not out["aa"]:
            out["empty"] = True
        return out
    except Exception as exc:  # never raise to the gateway
        return _error(f"could not read verdicts.json: {type(exc).__name__}")


# ── the page: skills to test, results, the test going now ───────────────

USAGE_FILE = ".usage.json"
_PAGE_TTL_S = 60.0
_skills_cache: dict[str, Any] = {}


def skills_dir(home: Path | None = None) -> Path:
    return (home or hermes_home()) / "skills"


def use_counts(home: Path | None = None) -> dict[str, int]:
    """``{skill: times used}`` from Hermes's own skills usage file (read only). {} when unreadable."""
    try:
        path = skills_dir(home) / USAGE_FILE
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, int] = {}
    if isinstance(data, dict):
        for name, rec in data.items():
            n = rec.get("use_count") if isinstance(rec, dict) else None
            if isinstance(name, str) and isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                out[name] = n
    return out


def pack_skills() -> set[str]:
    """Skills with a built-in question set (``packs.available()``); empty when not there yet."""
    mod = _sub("packs")
    try:
        got = mod.available() if mod is not None and hasattr(mod, "available") else []
    except Exception:
        return set()
    names: set[str] = set()
    for item in got or []:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict):
            n = item.get("skill") or item.get("name")
            if isinstance(n, str):
                names.add(n)
    return names


def installed_skills(home: Path | None = None) -> list[dict[str, Any]]:
    """Every enabled skill: name, times used, tokens it adds to each prompt and when loaded. Most used
    first, then by name. The token scan is cached for a minute (it reads every SKILL.md)."""
    key = str(skills_dir(home))
    hit = _skills_cache.get(key)
    if hit and time.monotonic() - hit[0] < _PAGE_TTL_S:
        rows = hit[1]
    else:
        cost = _sub("cost")
        rows = []
        if cost is not None:
            # Every folder Hermes keeps skills in (systematic-debugging ships with
            # Hermes and was missing from the table). Yours first; a name seen twice keeps the first.
            rr = _sub("realrun") if home is None else None
            try:
                folders = rr.skills_dirs() if rr is not None else [skills_dir(home)]
            except Exception:
                folders = [skills_dir(home)]
            seen: set[str] = set()
            for folder in folders:
                try:
                    est = cost.estimate(skills_dir=folder, disabled=None if home is None else [])
                except Exception:
                    continue
                for r in est.get("skills", []):
                    if isinstance(r.get("name"), str) and r["name"] not in seen:
                        seen.add(r["name"])
                        rows.append({"name": r["name"], "prompt_tokens": int(r["index_tokens"]),
                                     "load_tokens": int(r["load_tokens"])})
        _skills_cache[key] = (time.monotonic(), rows)
    counts, packs = use_counts(home), pack_skills()
    out = [{**r, "uses": counts.get(r["name"], 0), "pack": r["name"] in packs} for r in rows]
    out.sort(key=lambda r: (-r["uses"], r["name"].lower()))
    return out


def page_results(home: Path | None = None) -> list[dict[str, Any]]:
    """One row per tested skill (newest stored row wins), newest test first. Old and new verdict
    keys both work; a row that cannot be read is skipped, never fatal."""
    rep = _sub("report")
    if rep is None:
        return []
    try:
        path = verdicts_path(home)
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return []
    runs = data.get("runs") if isinstance(data, dict) and isinstance(data.get("runs"), list) else []
    aa = {r.get("model"): r for r in (data.get("aa") or []) if isinstance(r, dict)} if isinstance(data, dict) else {}
    latest: dict[str, dict[str, Any]] = {}
    for r in runs:
        if not isinstance(r, dict) or not isinstance(r.get("skill"), str):
            continue
        prev = latest.get(r["skill"])
        if prev is None or str(r.get("date") or "") >= str(prev.get("date") or ""):
            latest[r["skill"]] = r
    out = []
    for r in latest.values():
        try:
            row = rep.page_result({**r, "_aa": aa.get(r.get("model"))}, data_dir(home) / "runs")
        except Exception:
            row = None
        if row is not None:
            out.append(row)
    out.sort(key=lambda x: (str(x.get("date") or ""), x["working"].get("run") or ""), reverse=True)
    return out


def read_live(home: Path | None = None) -> dict[str, Any]:
    """Just the run going now (same numbers and line as its popup), for the chat strip and sidebar."""
    rep = _sub("report")
    try:
        return {"running": rep.running(data_dir(home) / "runs") if rep is not None else None}
    except Exception:
        return {"running": None}


def read_page(home: Path | None = None) -> dict[str, Any]:
    """Everything the page shows. Never raises."""
    out: dict[str, Any] = {"schema": SCHEMA, "skills": [], "results": [], "running": None, "copy": None}
    for key, fn in (("skills", installed_skills), ("results", page_results)):
        try:
            out[key] = fn(home)
        except Exception:
            out[key] = []
    rep = _sub("report")
    try:
        out["running"] = rep.running(data_dir(home) / "runs") if rep is not None else None
    except Exception:
        out["running"] = None
    mod = _sub("copy")
    try:
        out["copy"] = mod.page_copy() if mod is not None and hasattr(mod, "page_copy") else None
    except Exception:
        out["copy"] = None
    return out


try:
    from fastapi import APIRouter
except Exception:  # FastAPI missing: reader stays importable for tests
    APIRouter = None  # type: ignore[assignment]

if APIRouter is not None:
    router = APIRouter()

    @router.get("/verdicts")
    def verdicts() -> dict[str, Any]:
        # Sync def: FastAPI runs it in a threadpool, so file I/O stays off the loop.
        return read_verdicts()

    @router.get("/page")
    def page() -> dict[str, Any]:
        return read_page()

    @router.get("/live")
    def live() -> dict[str, Any]:
        # Tiny and cheap: the chat strip and sidebar poll this every few seconds.
        return read_live()
