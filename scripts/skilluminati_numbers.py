#!/usr/bin/env python3
"""Print what Skilluminati v0.2's rules give for the runs already on disk.

Usage:
    python scripts/skilluminati_numbers.py [--runs DIR] [--state-db PATH | --no-state-db] [--until STAMP]

``--until STAMP`` (for example ``20261114-200000``) reads only run folders whose name sorts at or
before STAMP, so runs made later do not move figures already quoted. The cutoff is printed in the
header.

``--runs`` defaults to ``$HERMES_HOME/plugin-data/skilluminati/runs`` (``HERMES_HOME`` defaults to
``~/.hermes``). It reads run folders only and never changes them. Every figure comes from the
plugin's own code, so it is what v0.2's rules give. The plugin itself does not re-pool or re-flag
results saved by v0.1; it shows them as stored and starts a pool from the next run:

* per skill, tested model and judge: runs, tasks, pooled Better / Same / Worse (``evidence.add_run``
  and ``evidence.pooled_block``, the sign of better minus worse per task), the shown verdict and
  strength (``verdict.lean``, held at Plausible when the tool-driven flag is on), the next step,
  the tool-driven share and flag, and the other flags;
* same-vs-same: how often the Owl made a firm pick between two answers made the same way
  (``noise.seed_from_runs``).

Runs that stopped early are left out, as v0.2 does. Runs saved before v0.2 carry no fingerprint of
the skill file, so they are pooled by skill, model and judge, on the assumption that the skill did
not change between them. Runs saved before v0.2 also carry no tool-driven mark; when ``state.db`` is
there it is read (read only) to work the mark out the way v0.2's miner does. Without it only empty
answers count toward the tool-driven share, and the output says so.

The pooling writes to a temporary folder that is deleted at the end. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import copy as sb_copy  # noqa: E402
from skilluminati import evidence, mine, noise, paths, realrun  # noqa: E402
from skilluminati import recommend as sb_recommend  # noqa: E402
from skilluminati import verdict as sb_verdict  # noqa: E402

NO_FINGERPRINT = "not stored"


def default_runs() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    home = Path(raw).expanduser() if raw else Path.home() / ".hermes"
    return home / "plugin-data" / "skilluminati" / "runs"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tasks(run_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in ("tasks.jsonl", "tasks_frozen.jsonl"):
        f = run_dir / name
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        t = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(t, dict):
                        out.append(t)
    return out


def tool_marks(run_dir: Path, conn: Any) -> dict[str, bool]:
    """``{task_hash: tool_driven}`` for a run's history tasks, read from state.db as v0.2's miner does."""
    marks: dict[str, bool] = {}
    if conn is None:
        return marks
    for t in _tasks(run_dir):
        if not t.get("session_id") or t.get("load_message_id") is None:
            continue
        msgs = mine._session_messages(conn, t["session_id"])
        idx = next((i for i, r in enumerate(msgs) if r["id"] == t["load_message_id"]), None)
        if idx is not None:
            marks[realrun.task_hash(t)] = mine._next_move_tool_driven(msgs, idx)
    return marks


def read_runs(runs_dir: Path, conn: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Finished verdict runs, oldest first, one entry per skill in each; and a skipped-run list."""
    found: list[dict[str, Any]] = []
    skipped: list[str] = []
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        res = _load(d / "result.json")
        if not isinstance(res, dict) or not isinstance(res.get("per_skill"), dict):
            skipped.append(f"{d.name}: no finished verdict result")
            continue
        if res.get("stopped_early"):
            skipped.append(f"{d.name}: stopped early")
            continue
        if not res["per_skill"]:
            skipped.append(f"{d.name}: no skill results")
            continue
        model, judge = res.get("subject_model"), res.get("judge_model")
        if not model or not judge:
            cm, cj = noise._models_from_calls(d)
            model, judge = model or cm, judge or cj
        marks = None
        for skill, s in res["per_skill"].items():
            pairs = [dict(p) for p in s.get("pairs") or [] if isinstance(p, dict)]
            stored = any("tool_driven" in p for p in pairs)
            if not stored:
                if marks is None:
                    marks = tool_marks(d, conn)
                for p in pairs:
                    p["tool_driven"] = marks.get(str(p.get("task_hash")), False)
            found.append({"run": d.name, "skill": skill, "model": model, "judge": judge,
                          "sha": s.get("skill_sha256") or NO_FINGERPRINT, "source": res.get("source"),
                          "pairs": pairs, "summary": s, "marks": "stored" if stored else
                          ("state.db" if conn is not None else "none")})
    return found, skipped


def pool(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each run through ``evidence.add_run`` in a temporary plugin-data folder; the last pooled
    counts per pool, with the run that closed it."""
    pools: dict[tuple, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="skilluminati-numbers-") as tmp:
        old = paths._data_dir_override
        paths._data_dir_override = Path(tmp)
        try:
            for r in found:
                tools = sb_recommend.tool_driven_counts(r["pairs"])
                counts = evidence.add_run(r["skill"], skill_sha256=r["sha"], model=r["model"] or "",
                                          judge=r["judge"], run=r["run"], date=None, pairs=r["pairs"],
                                          tools=tools)
                if counts is None:
                    continue
                key = (r["skill"], r["sha"], sb_recommend.model_key(r["model"]), sb_recommend.model_key(r["judge"]))
                entry = pools.setdefault(key, {"skill": r["skill"], "sha": r["sha"], "model": r["model"],
                                               "judge": r["judge"], "runs": []})
                entry["counts"], entry["last"] = counts, r
                entry["runs"].append({**r, "tools": tools})
        finally:
            paths._data_dir_override = old
    return list(pools.values())


def _bsw(w: int, l: int, t: int) -> str:
    return f"{w} / {t} / {l}"


def _key(model: Any) -> str:
    return sb_recommend.model_key(model) or "unknown"


def pool_lines(p: dict[str, Any]) -> list[str]:
    c, last = p["counts"], p["last"]
    s = last["summary"]
    js = s.get("judge_stats") if isinstance(s.get("judge_stats"), dict) else {}
    first = js["first_picks"] / js["decisive_judgements"] if js.get("decisive_judgements") else None
    block = evidence.pooled_block(c, heavy=s.get("heavy"), model=p["model"], first_share=first, judge=p["judge"])
    lean, rec, tools = block["lean"], block["recommendation"], block["tools"] or {}
    raw = sb_verdict.lean(c["wins"], c["losses"], c["ties"])
    word, _meaning, strength = sb_copy.shown(lean["verdict"], lean["strength"])
    shown = f"{word} ({strength})" if strength else word
    capped = " (held at Plausible: tool-driven)" if lean.get("capped_from") else ""
    flags = [f["text"] for f in rec["flags"]] or ["none"]
    marks = sorted({r["marks"] for r in p["runs"]})
    lines = [
        f"skill {p['skill']} | model {_key(p['model'])} | judge {_key(p['judge'])} | fingerprint {p['sha'][:12]}",
        f"  runs {c['n_runs']} | tasks {c['n_tasks']} | {evidence.pool_line(c['n_tasks'], c['n_runs'])}",
        f"  pooled Better / Same / Worse: {_bsw(c['wins'], c['losses'], c['ties'])}",
        f"  shown: {shown}{capped} | rule before any cap: {raw['verdict']}, {raw['strength']}",
        f"  next step: {rec['next_step']['text']}",
        f"  tool-driven: {tools.get('count', 0)} of {tools.get('tested', 0)} tested uses "
        f"(tool-driven {tools.get('tool_driven', 0)}, empty answer {tools.get('empty_answer', 0)}) | "
        f"flag {'on' if tools.get('flag') else 'off'} | marks from {', '.join(marks)}",
        f"  flags: {' | '.join(flags)}",
    ]
    for r in p["runs"]:
        ok = [x.get("outcome") for x in r["pairs"]]
        w, l, t, e = (ok.count(k) for k in ("win", "loss", "tie", "error"))
        rule = sb_verdict.lean(w, l, t)
        alone = sb_recommend.cap_lean(rule, r["tools"]) or {}
        aw, _m, ast = sb_copy.shown(alone.get("verdict"), alone.get("strength"))
        lines.append(f"    run {r['run']} | source {r['source'] or 'history'} | {len(ok)} tested | "
                     f"Better / Same / Worse {_bsw(w, l, t)} | not judged {e} | "
                     f"tool-driven {r['tools']['count']} of {r['tools']['tested']} | "
                     f"this run alone: {aw}{f' ({ast})' if ast else ''} (rule before any cap: {rule['verdict']}, {rule['strength']})")
    return lines


def noise_lines(runs_dir: Path) -> list[str]:
    out = []
    for entry in noise.seed_from_runs(runs_dir):
        fig = noise.pool_figure(entry["runs"])
        all_o = [o for r in entry["runs"] for o in r.get("outcomes") or []]
        out.append(f"same-vs-same | model {entry['model'] or 'unknown'} | judge {entry['judge'] or 'unknown'} | "
                   f"runs {fig['runs']} | first better {all_o.count('win')}, second better "
                   f"{all_o.count('loss')}, same {all_o.count('tie')}")
        out.append(f"  {noise.line(fig)}")
    return out or ["same-vs-same: no stored pairs"]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Print the figures Skilluminati v0.2 would show for the runs on disk.")
    ap.add_argument("--runs", type=Path, default=None, help="run folders (default: $HERMES_HOME/plugin-data/skilluminati/runs)")
    ap.add_argument("--state-db", type=Path, default=None, help="Hermes state.db, read only (default: beside --runs' HERMES_HOME)")
    ap.add_argument("--no-state-db", action="store_true", help="do not read state.db; only empty answers count as tool-driven")
    ap.add_argument("--until", default=None, metavar="STAMP",
                    help="only run folders whose name sorts at or before STAMP, e.g. 20261114-200000")
    a = ap.parse_args(argv)
    runs_dir = (a.runs or default_runs()).expanduser()
    if not runs_dir.is_dir():
        print(f"no runs folder at {runs_dir}", file=sys.stderr)
        return 2
    conn = None
    if not a.no_state_db:
        db = a.state_db or runs_dir.parent.parent.parent / "state.db"
        if db.is_file():
            conn = mine.connect(db)
    with tempfile.TemporaryDirectory() as tmp:
        if a.until is not None:  # a read-only view: links to the folders up to the cutoff
            view = Path(tmp) / "runs"
            view.mkdir()
            for d in sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name <= a.until):
                (view / d.name).symlink_to(d.resolve(), target_is_directory=True)
            runs_dir = view
        return _report(runs_dir, conn, a.until)


def _report(runs_dir: Path, conn: Any, until: Optional[str]) -> int:
    print("Skilluminati numbers (scripts/skilluminati_numbers.py). Better / Same / Worse in that order.")
    if until is not None:
        print(f"runs up to: {until} (run folders whose name sorts at or before it)")
    print(f"tool-driven marks for older runs: {'read from state.db (read only)' if conn is not None else 'not available, empty answers only'}")
    found, skipped = read_runs(runs_dir, conn)
    print(f"finished verdict runs: {len({r['run'] for r in found})} | skipped: {len(skipped)}")
    for s in skipped:
        print(f"  skipped {s}")
    print()
    for p in sorted(pool(found), key=lambda p: (p["skill"], _key(p["model"]), _key(p["judge"]), p["sha"])):
        print("\n".join(pool_lines(p)))
        print()
    print("\n".join(noise_lines(runs_dir)))
    if conn is not None:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
