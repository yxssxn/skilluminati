"""In plain words: starts a skill test or the self-test and runs it in the background. It runs when
you type /skilluminati <skill> or press Ask the Owl. In Hermes Desktop a popup says when the test
ends; the verdict goes into the chat only when you press Show in chat.

How a run goes:

1. **Start at once.** The command answers in under a second with one line. All slow work (reading
   your history, asking the model) happens in a background thread, never in the command itself:
   a slow command blocks Hermes Desktop's other requests (new chat, open chat) until it returns.
2. **Where.** Model calls only start from a local session (CLI, TUI, Desktop, ACP). Under cron or a
   messaging app nothing runs.
3. **One question at a time.** Calls go one by one through one ``llm.CommandGuard`` (one budget, one
   stop flag, one model check), with a short pause between them, so your own chats stay quick.
4. **Result.** Files go to a fresh private folder under plugin-data. The verdict is posted to the
   popup in the chat that started the run (press Show in chat to put it in the chat), and the
   Skilluminati page shows the details.

Settings (``plugins.entries.skilluminati.settings``; every one optional):

=====================  ==================================================  =====================
key                    meaning                                             default
=====================  ==================================================  =====================
``subject.model``      model the tasks are replayed on                     your active model
``subject.provider``   its provider                                        your active provider
``judge.model``        model that judges each pair (the JOB question)      your active model
``judge.provider``     its provider                                        your active provider
``harm_judge.model``   model that asks the HARM question (self-test)       ``judge.model``
``harm_judge.provider`` its provider                                       ``judge.provider``
``max_calls``          ceiling on calls for any one command                ``400``
=====================  ==================================================  =====================

Any model or provider other than the active one needs the operator's opt-in under
``plugins.entries.skilluminati.llm`` (``allow_model_override`` / ``allow_provider_override``); the
host enforces it and the plugin never routes round it.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import copy as sb_copy
from . import llm as sb_llm
from . import paths as sb_paths
from . import verdict as sb_verdict

logger = logging.getLogger(__name__)

#: Ceiling on the calls one command may make, whatever its run plan estimates. Setting ``max_calls``.
DEFAULT_MAX_CALLS = 400
#: Surfaces where a person is at the keyboard and a run may start.
# "local" is what Hermes reports for Desktop and TUI turns served by the local gateway (and `hermes chat -q`).
LOCAL_SURFACES = frozenset({"cli", "tui", "desktop", "acp", "local"})
#: Windows that must never wait on a run: the command answers at once and the run goes to a thread.
INTERACTIVE_SURFACES = frozenset({"tui", "desktop"})

#: Plan-only token estimates. They must not come in under the real cost, so they lean high.
#: Self-test: a quick run used 210,397 tokens over 108 calls (1,948 a call); 2,500 a call.
TOKENS_PER_CALL = {"selftest": 2_500, "verdict": 6_000}
#: Verdict runs are sized from the tasks themselves (``verdict_tokens``). Two real ``unslop`` runs
#: sent 2.5 to 2.7 characters per input token (17,279 tokens for 46,237 characters of stored system
#: prompt plus first message), so characters are divided by 2.5, not 4.
CHARS_PER_TOKEN = 2.5
#: Per-call allowances on top of the measured input: answers ran about 560 tokens a call (cap 800 is
#: not honoured below the plugin), judge calls about 2,950 in and 410 out.
SUBJECT_OUT_TOKENS = 1_000
JUDGE_CALL_TOKENS = 4_600
#: Plan-only time: seconds a call takes, wall clock, one at a time. Verdict: test runs on Opus 5.5,
#: answers 15-19 s and judge calls about 6 s, so 12 s a call (unslop 64 calls took 14.2 min, systematic-
#: debugging 48 calls 8.4 min). Self-test: 108 calls took 8.7 minutes. Packs may set their own.
SECONDS_PER_CALL = {"selftest": 5.0, "verdict": 12.0}
#: One call at a time, with a short pause after each (see the top of this file).
VERDICT_WORKERS = 1
SELFTEST_WORKERS = 1
CALL_PAUSE_S = 0.5
#: A test uses your latest uses of the skill, at most this many, plus a few same-vs-same noise checks.
TASK_LIMIT = 12
AA_LIMIT = 4
#: With fewer usable uses than this in your history, a built-in question set is used if one exists.
PACK_BELOW = 6


# ── settings and routes ──────────────────────────────────────────────────────

def _setting(ctx: Any, key: str, default: Any = None) -> Any:
    try:
        v = ctx.get_config(key, default)
    except Exception:
        return default
    return default if v is None else v


def _str_or_none(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def routes(ctx: Any) -> dict[str, dict[str, Optional[str]]]:
    """``{subject, job, harm}`` routes from settings; ``None`` fields mean the active provider/model."""
    def role(name: str, fallback: Optional[dict[str, Optional[str]]] = None) -> dict[str, Optional[str]]:
        out = {"provider": _str_or_none(_setting(ctx, f"{name}.provider")),
               "model": _str_or_none(_setting(ctx, f"{name}.model"))}
        if fallback and out["provider"] is None and out["model"] is None:
            return dict(fallback)
        return out
    job = role("judge")
    return {"subject": role("subject"), "job": job, "harm": role("harm_judge", job)}


def max_calls(ctx: Any) -> int:
    try:
        n = int(_setting(ctx, "max_calls", DEFAULT_MAX_CALLS))
    except (TypeError, ValueError):
        n = DEFAULT_MAX_CALLS
    return max(1, n)


def active_model_id() -> Optional[str]:
    """The model id an unpinned ``ctx.llm`` call from a slash command runs on: ``model.default`` in
    config.yaml (read-only, via the public ``hermes_cli.config``). The host falls back to this same
    field when no turn has bound a live model, which is the case on the command's worker thread.
    None when it cannot be read. The result line still names the model that actually answered."""
    try:
        from hermes_cli.config import load_config_readonly  # public plugin surface
        model_cfg = (load_config_readonly() or {}).get("model")
    except Exception:
        return None
    value = model_cfg if isinstance(model_cfg, str) else (model_cfg or {}).get("default") if isinstance(model_cfg, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def minutes_estimate(calls: int, kind: str, workers: int, seconds_per_call: Optional[float] = None) -> int:
    """Calls x measured seconds a call (plus the pause after each), in whole minutes."""
    import math
    per_call = (seconds_per_call or SECONDS_PER_CALL[kind]) + CALL_PAUSE_S
    return max(1, math.ceil(calls * per_call / max(1, workers) / 60))


def model_names(r: Mapping[str, Mapping[str, Optional[str]]], active: Optional[str], *, harm: bool) -> str:
    """Name the model once. Extra roles are named only when they differ from the tested model."""
    def name(route: Mapping[str, Optional[str]]) -> str:
        return route.get("model") or active or "your active model"
    subject = name(r["subject"])
    out = subject
    others = [("judge", name(r["job"]))] + ([("harm judge", name(r["harm"]))] if harm else [])
    extra = [f"{role} {m}" for role, m in others if m != subject]
    return out + (f" ({', '.join(extra)})" if extra else "")


# ── where a run may start ────────────────────────────────────────────────────────

def surface(ctx: Any) -> str:
    try:
        return str(ctx.session_surface() or "")
    except Exception:
        return ""


def unattended_reason(ctx: Any) -> Optional[str]:
    """Why a run may not start here, or None when a person is at a local session."""
    if os.environ.get("HERMES_CRON_SESSION", "").strip().lower() in {"1", "true", "yes"}:
        return "this is a scheduled (cron) session"
    s = surface(ctx)
    if s in LOCAL_SURFACES:
        return None
    if not s:
        return "this is not an interactive session"
    return f"this session is on {s}, not a local CLI, TUI or Desktop session"


def split_flags(args: list[str]) -> tuple[list[str], set[str]]:
    words = [a for a in args if not a.startswith("-")]
    flags = {a.lower() for a in args if a.startswith("-")}
    return words, flags


def _progress(out_dir: Path) -> Callable[[str], None]:
    """Progress lines go to ``progress.log`` in the run folder (0600) and to the plugin logger."""
    path = sb_paths.private_file(out_dir / "progress.log")

    def write(line: str) -> None:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        logger.info("skilluminati: %s", line)
    return write


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def blocked_text(ctx: Any) -> Optional[str]:
    """Why a run may not start here (one line), or None when it may."""
    why = unattended_reason(ctx)
    return f"Nothing was run: {why}. Runs start only from a local session." if why else None


# ── background runs and stop ─────────────────────────────────────────────────
#
# The command itself only posts a popup line, starts a thread and returns. The thread does the slow
# work and edits the popup every few seconds. With no chat to post into (``hermes chat -q``, a test)
# the run happens in the foreground instead and the command returns the report. One run at a time
# per process; ``/skilluminati stop`` sets the run's stop flag, so no new call starts.

PROGRESS_EVERY_S = 8.0
STOP_REASON = "you ran /skilluminati stop"

_ACTIVE_LOCK = threading.Lock()
_ACTIVE: dict[str, Any] = {}
_LAST_DONE: Optional[threading.Event] = None


def busy_text() -> Optional[str]:
    with _ACTIVE_LOCK:
        label = _ACTIVE.get("label")
    if not label:
        return None
    return f"A {label} is already going. /skilluminati stop ends it."


def active_status() -> Optional[dict[str, Any]]:
    """``{label, used, cap}`` for the run going in this process, else None."""
    with _ACTIVE_LOCK:
        label, guard = _ACTIVE.get("label"), _ACTIVE.get("guard")
    if not label or guard is None:
        return None
    return {"label": label, "used": int(getattr(guard, "used", 0) or 0), "cap": int(getattr(guard, "cap", 0) or 0),
            "status": getattr(guard, "status", "") or ""}


def _claim(label: str, guard: sb_llm.CommandGuard) -> Optional[threading.Event]:
    global _LAST_DONE
    with _ACTIVE_LOCK:
        if _ACTIVE:
            return None
        done = threading.Event()
        _ACTIVE.update(label=label, guard=guard, done=done, started=time.monotonic())
        _LAST_DONE = done
        return done


def _release(done: threading.Event) -> None:
    with _ACTIVE_LOCK:
        if _ACTIVE.get("done") is done:
            _ACTIVE.clear()
    done.set()


def stop_active(reason: str = STOP_REASON) -> Optional[str]:
    """Set the running command's stop flag. Returns its label, or None when nothing is running."""
    with _ACTIVE_LOCK:
        label, guard = _ACTIVE.get("label"), _ACTIVE.get("guard")
    if guard is None:
        return None
    guard.halt(sb_llm.StopRequested(reason))
    return label


def stop_command() -> str:
    label = stop_active()
    if label is None:
        return "No Skilluminati run is going."
    return f"Stopping the {label} after the call in flight."


def wait_active(timeout: float) -> bool:
    """Wait for the last started run to finish (tests, unload). True when it has."""
    done = _LAST_DONE
    return True if done is None else done.wait(timeout)


def _post(ctx: Any, text: str) -> Any:
    """A message handle for this chat, or None when the session cannot take posted messages."""
    fn = getattr(ctx, "post_message", None)
    if not callable(fn):
        return None
    try:
        handle = fn(text)
    except Exception:
        return None
    return handle if getattr(handle, "ok", False) is True else None


# Popup texts. Desktop draws a posted message as a small popup that drops line breaks, so every one
# is a single line under 120 characters, with no model id and no folder path (tested).

def popup_started(cap: int, per: int = 4) -> str:
    return popup_progress(0, cap, per=per)


def popup_progress(used: int, cap: int, status: str = "", per: int = 4, now: str = "") -> str:
    """The plan line until the first comparison is done, then 'n of m compared' (``per`` calls each).
    ``now`` is the live line (``now_line``): what the model is doing this second."""
    if status and used < per:
        line = status
    else:
        line = sb_copy.PROGRESS_LINE.format(done=used // per, total=max(1, cap // per))
    return f"{line} {now}" if now else line


def now_line(guard: Any) -> str:
    """'Now: answering with the skill (42s)' from the call going now, or '' when none is known."""
    label = str(getattr(guard, "now", "") or "").strip().lower()
    since = float(getattr(guard, "now_since", 0.0) or 0.0)
    if not label or not since:
        return ""
    secs = max(0, int(time.monotonic() - since))
    if "judge" in label:
        return sb_copy.NOW_JUDGING.format(secs=secs)
    if "without_" in label:  # the noise check: two answers without the skill
        return sb_copy.NOW_NOISE.format(secs=secs)
    if label.endswith("without"):
        return sb_copy.NOW_WITHOUT.format(secs=secs)
    if label.endswith("with"):
        return sb_copy.NOW_WITH.format(secs=secs)
    return ""


def popup_stopped(used: int, cap: int) -> str:
    return f"Skilluminati: stopped after {used} of {cap} calls. Partial result saved."


def popup_failed(exc: BaseException, used: int) -> str:
    return f"Skilluminati: the run failed ({type(exc).__name__}) after {plural(used, 'call')}."


def _edit(handle: Any, text: str) -> None:
    try:
        handle.edit(text)
    except Exception:
        logger.debug("skilluminati: popup edit failed", exc_info=True)


LIVE_FILE = "live.json"  # the run going now: one small file the page, chat strip and sidebar read
LIVE_EVERY_S = 4.0


def live_line(guard: sb_llm.CommandGuard, ready_text: str) -> str:
    """The same line the popup shows."""
    if guard.cap:
        return popup_progress(guard.used, guard.cap, guard.status, getattr(guard, "per_compare", 4), now_line(guard))
    return ready_text  # still reading history; no calls planned yet


def write_live(guard: sb_llm.CommandGuard, ready_text: str, info: dict[str, Any]) -> None:
    """Write ``live.json`` (atomic). Never raises: the page is a nice-to-have, the run is not."""
    per = max(1, int(getattr(guard, "per_compare", 4) or 4))
    data = {**info, "line": live_line(guard, ready_text), "updated": time.time(),
            "done": guard.used // per if guard.cap else None, "total": guard.cap // per if guard.cap else None}
    try:
        path = sb_paths.data_dir() / LIVE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        logger.debug("skilluminati: live.json not written", exc_info=True)


def clear_live() -> None:
    try:
        (sb_paths.data_dir() / LIVE_FILE).unlink(missing_ok=True)
    except Exception:
        logger.debug("skilluminati: live.json not removed", exc_info=True)


def _keep_posting(handle: Any, guard: sb_llm.CommandGuard, ready_text: str, ended: threading.Event,
                  info: Optional[dict[str, Any]] = None) -> None:
    """Every ``LIVE_EVERY_S`` seconds until ``ended``: write ``live.json``; every ``PROGRESS_EVERY_S``
    also edit the popup (when there is one)."""
    last_edit = time.monotonic()
    if info is not None:
        write_live(guard, ready_text, info)
    while not ended.wait(min(LIVE_EVERY_S, PROGRESS_EVERY_S) if info is not None else PROGRESS_EVERY_S):
        if info is not None:
            write_live(guard, ready_text, info)
        if handle is not None and (info is None or time.monotonic() - last_edit >= PROGRESS_EVERY_S - 0.5):
            _edit(handle, live_line(guard, ready_text))
            last_edit = time.monotonic()


def _thread(body: Callable[[], None], name: str) -> threading.Thread:
    """A daemon thread that keeps this chat's context, so its popup edits reach the right chat."""
    t = threading.Thread(target=contextvars.copy_context().run, args=(body,), name=name, daemon=True)
    t.start()
    return t


def finish(run_dir: Optional[Path]) -> Optional[str]:
    """Save ``report.md`` beside ``result.json``. Returns the popup headline (None: no result)."""
    if run_dir is None:
        return None
    from . import report
    try:
        report.save(run_dir)
        return report.headline(run_dir)
    except Exception:
        logger.exception("skilluminati: report could not be saved")
        return None


def announce(run_dir: Path, session_id: str, kind: str, skill: Optional[str], line: str = "") -> bool:
    """Tell the Desktop half the run ended (``plugin.skilluminati.run.done``) so it can ask the agent
    for the report in the chat the run started from. Public plugin event bridge only. A process with
    no Desktop client drops it (a logged no-op). True when the event was handed over."""
    from . import copy as sb_copy
    what = "the self-test" if kind == "selftest" else (skill or "this skill")
    payload = {"session_id": session_id or "", "run": run_dir.name, "kind": kind, "skill": skill or "",
               "prompt": sb_copy.SHOW_RESULT_PROMPT.format(what=what),
               "line": line}  # the one-line verdict, for the page to show in the chat
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event  # public (plugin guide, pushing events)
    except Exception:
        return False
    try:
        broadcast_plugin_event(sb_paths.PLUGIN_ID, "run.done", payload)
        return True
    except Exception:
        logger.debug("skilluminati: run.done event not sent", exc_info=True)
        return False


def launch(ctx: Any, label: str, guard: sb_llm.CommandGuard, work: Callable[[], Any],
           first_line: str, start_line: str, *, session_id: str = "", kind: str = "verdict",
           skill: Optional[str] = None, background: bool = False) -> str:
    """Start ``work()``. It returns the run folder, or a one-line reason when nothing ran.

    When this chat can show a popup (or ``background`` is set) the work goes to a thread and this
    returns ``start_line`` at once. Otherwise the work runs here and this returns the report."""
    from . import report
    done = _claim(label, guard)
    if done is None:
        return busy_text() or "Another Skilluminati run is going."
    # Desktop and the TUI always run in the background: a run held inside the command froze the whole
    # window. The popup is posted from the background thread, never from here,
    # because posting can wait up to 30 s for the gateway and the window waits with it.
    background = background or surface(ctx) in INTERACTIVE_SURFACES
    handle = None if background else _post(ctx, first_line)
    if handle is None and not background:
        try:
            out = work()
            if isinstance(out, str):
                return out
            finish(out)
            text = report.terminal(out) if out is not None else None
            return text or f"No result: the run stopped before it wrote one. Calls: {guard.used}."
        finally:
            _release(done)

    guard.pause = CALL_PAUSE_S  # in the background, leave room for the user's own chats

    def body() -> None:
        nonlocal handle
        if handle is None:
            handle = _post(ctx, first_line)
        ended = threading.Event()
        info = {"skill": skill or "", "kind": kind, "session_id": session_id or "", "started": time.time()}
        # Desktop: the popup says only "started" and the result; the strip above the chat box carries
        # the progress.
        ticking = None if surface(ctx) == "desktop" else handle
        ticker = _thread(lambda: _keep_posting(ticking, guard, first_line, ended, info), "skilluminati-progress")
        run_dir, line = None, ""
        try:
            try:
                out = work()
                if isinstance(out, str):
                    line = out.replace("\n", " ")
                else:
                    run_dir = out
                    line = end_line(run_dir, kind, skill, guard)
            except Exception as exc:  # never lost silently: the popup says so
                logger.exception("skilluminati: background run failed")
                line = popup_failed(exc, guard.used)
            ended.set()
            if ticker is not None:
                ticker.join(5)
            if handle is not None:
                _edit(handle, line)
            logger.info("skilluminati: done: %s", line)
        finally:
            ended.set()
            clear_live()
            _release(done)  # before the event, so the tool no longer reports the run as going
        if run_dir is not None:
            announce(run_dir, session_id, kind, skill, line)

    try:
        _thread(body, "skilluminati-run")
    except Exception as exc:
        _release(done)
        return f"Nothing was run: the background job did not start ({type(exc).__name__})."
    return start_line


def end_line(run_dir: Optional[Path], kind: str, skill: Optional[str], guard: sb_llm.CommandGuard) -> str:
    """The one line posted when a run ends."""
    headline = finish(run_dir)
    if guard.stop.is_set():
        return popup_stopped(guard.used, guard.cap)
    if kind == "verdict" and run_dir is not None and skill:
        return result_line(run_dir, skill) or headline or popup_stopped(guard.used, guard.cap)
    return headline or popup_stopped(guard.used, guard.cap)


def result_line(run_dir: Path, skill: str) -> Optional[str]:
    """'unslop: Dead weight (Plausible). Better 5 · Same 5 · Worse 2.', or None."""
    try:
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        summary = result["per_skill"][skill]
        c = summary["combined"]
        wins, losses, ties = int(c["wins"]), int(c["losses"]), int(c["ties"])
        lean = summary.get("lean") or sb_verdict.lean(wins, losses, ties)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    word = sb_copy.WORDS.get(lean.get("verdict"), sb_copy.WORDS["not_enough"])
    strength = sb_copy.STRENGTH.get(lean.get("strength"))
    line = sb_copy.CHAT_RESULT_LINE if strength else sb_copy.CHAT_RESULT_LINE_NO_STRENGTH
    return line.format(skill=skill, verdict=word, strength=strength, w=wins, t=ties, l=losses)


# ── selftest ─────────────────────────────────────────────────────────────────

def selftest_plan(ctx: Any, full: bool) -> dict[str, Any]:
    from . import selftest as st
    run = "full" if full else "quick"
    r = routes(ctx)
    spec = st.RUNS[run]
    n_tasks = spec.get("n_tasks") or len(_fixture_tasks())
    p = st.plan_calls(run, n_tasks, routes=r)
    cap = max_calls(ctx)
    return {"run": run, "routes": r, "plan": p, "n_tasks": n_tasks, "cap": cap,
            "fits": p["total"] <= cap, "tokens": p["total"] * TOKENS_PER_CALL["selftest"],
            "minutes": minutes_estimate(p["total"], "selftest", SELFTEST_WORKERS),
            "cells": st.run_cells(run), "active_model": active_model_id()}


def _fixture_tasks() -> list[dict[str, Any]]:
    from . import runner as sb_runner
    return sb_runner.load_tasks(sb_paths.FIXTURES_DIR)


def selftest_plan_text(pl: Mapping[str, Any]) -> str:
    """Two short lines (the third, what happens at the end, is added where the run starts)."""
    planted = sum(1 for c in pl["cells"] if c.split("/")[0] != "aa")
    p = pl["plan"]
    lines = [
        f"Self-test ({pl['run']}) on {model_names(pl['routes'], pl.get('active_model'), harm=True)}: "
        f"{planted} planted skills, {plural(pl['n_tasks'], 'task')} each, {p['total']} calls.",
        f"About {pl['tokens']:,} tokens and {plural(pl['minutes'], 'minute')}.",
    ]
    if not pl["fits"]:
        lines.append(f"Needs {p['total']} calls, above max_calls ({pl['cap']}).")
    return "\n".join(lines)


def run_selftest(ctx: Any, args: list[str], *, session_id: str = "") -> str:
    """``/skilluminati selftest``: starts at once, no question asked."""
    from . import selftest as st
    _, flags = split_flags(args)
    pl = selftest_plan(ctx, full="--full" in flags)
    text = selftest_plan_text(pl)
    if not pl["fits"]:
        return text + "\nNothing was run. Raise max_calls, or run the quick self-test."
    blocked = blocked_text(ctx)
    if blocked:
        return f"{text}\n{blocked}"
    guard = sb_llm.CommandGuard(ctx.llm, cap=pl["plan"]["total"])
    guard.per_compare = st.CALLS_PER_PAIR

    def work() -> Path:
        out_dir = sb_paths.new_run_dir(f"selftest-{pl['run']}", _stamp())
        test = st.SelfTest(guard, fixtures_dir=sb_paths.FIXTURES_DIR, out_dir=out_dir, run=pl["run"],
                           routes=pl["routes"], cap=pl["plan"]["total"], log=_progress(out_dir))
        for name in (f"prompts-{pl['run']}.jsonl", f"calls-{pl['run']}.jsonl"):
            sb_paths.private_file(out_dir / name)
        result = test.run()
        stamp_selftest_models(result, test.caller.calls, guard)
        agg = st.aggregate({pl["run"]: result}, fixtures_dir=sb_paths.FIXTURES_DIR)
        (out_dir / "result.json").write_text(json.dumps({"run": result, "aggregate": agg}, indent=2, default=str) + "\n",
                                            encoding="utf-8")
        return out_dir

    start_line = sb_copy.SELFTEST_START_LINE.format(minutes=pl["minutes"])
    return launch(ctx, f"self-test ({pl['run']})", guard, work, popup_started(guard.cap, st.CALLS_PER_PAIR), start_line,
                  session_id=session_id, kind="selftest")


# ── model ids in result.json, from the call records ─────────────────────────

def _answered(calls: list[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> Optional[str]:
    """The model that answered most of the calls matching ``pred`` (from the provider's own record)."""
    counts: dict[str, int] = {}
    for c in calls:
        m = str(c.get("model") or "").strip()
        if m and pred(c):
            counts[m] = counts.get(m, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def _by_model(calls: list[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in calls:
        m = str(c.get("model") or "").strip() or str(c.get("requested_model") or "").strip() or "not recorded"
        out[m] = out.get(m, 0) + 1
    return out


def stamp_selftest_models(result: dict[str, Any], calls: list[Mapping[str, Any]], guard: Any) -> None:
    """Replace "active model" placeholders with the model ids the calls actually came back from."""
    fallback = guard.active_model or next(iter(getattr(guard, "models_seen", {}) or {}), None)
    subject = _answered(calls, lambda c: c.get("role") == "subject") or fallback
    job = _answered(calls, lambda c: c.get("role") == "judge" and c.get("question") != "harm") or fallback
    harm = _answered(calls, lambda c: c.get("role") == "judge" and c.get("question") == "harm") or fallback
    if subject:
        result["subject_model"] = subject
    if job:
        result["judge_model"] = job
    if harm and isinstance(result.get("harm_judge"), dict):
        result["harm_judge"]["model"] = harm
    if isinstance(result.get("totals"), dict):
        result["totals"]["by_model"] = _by_model(calls)


def stamp_verdict_models(results: dict[str, Any], calls: list[Mapping[str, Any]], guard: Any) -> None:
    fallback = guard.active_model or next(iter(getattr(guard, "models_seen", {}) or {}), None)
    subject = _answered(calls, lambda c: c.get("role") == "subject") or fallback
    judge = _answered(calls, lambda c: c.get("role") == "judge") or fallback
    if subject:
        results["subject_model"] = subject
        if isinstance(results.get("aa"), dict):
            results["aa"]["model"] = subject
    if judge:
        results["judge_model"] = judge
    provider = next((str(c.get("provider")) for c in calls if c.get("provider")), None)
    if provider and not results.get("provider"):
        results["provider"] = provider
    for summary in (results.get("per_skill") or {}).values():
        stamp = summary.get("stamp") if isinstance(summary, dict) else None
        if isinstance(stamp, dict):
            stamp["model"] = subject or stamp.get("model")
            stamp["judge"] = judge or stamp.get("judge")
    if isinstance(results.get("totals"), dict):
        results["totals"]["by_model"] = _by_model(calls)


# ── verdict for one skill ────────────────────────────────────────────────────

def find_tasks(skill: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Your latest uses of ``skill`` from ``state.db`` (read-only), each with the chat as it stood
    when the skill was opened, plus the counts (``uses``, ``eligible``, ``tested``)."""
    from . import mine
    if not mine.default_db_path().is_file():
        return [], {"uses": 0, "eligible": 0, "tested": 0, "loads": 0, "kept": 0, "dropped": {}, "no_db": True}
    from . import realrun
    return mine.find_tasks_for_run(skill, mine.default_db_path(), realrun.skill_home(skill), TASK_LIMIT)


def _task_chars(t: Mapping[str, Any]) -> int:
    n = len(str(t.get("system_prompt") or "")) + len(str(t.get("user_turn") or ""))
    for m in t.get("frozen_messages") or []:
        n += len(json.dumps(m, ensure_ascii=False)) if not isinstance(m, str) else len(m)
    return n


def verdict_tokens(tasks: list[Mapping[str, Any]], n_aa: int, skill_chars: int) -> int:
    """Plan estimate of a verdict run's tokens, sized from the tasks and the skill text.

    Per task: the answer without the skill (the task's text), the answer with it (the task's text
    plus the skill), each with up to ``SUBJECT_OUT_TOKENS`` of answer, and two judge calls. Per A/A
    pair: two answers without the skill and two judge calls. Characters / ``CHARS_PER_TOKEN``."""
    def tok(chars: int) -> int:
        return int(chars / CHARS_PER_TOKEN) + 1
    skill_tok = tok(skill_chars)
    total = 0
    for t in tasks:
        base = tok(_task_chars(t))
        total += 2 * base + skill_tok + 2 * SUBJECT_OUT_TOKENS + 2 * JUDGE_CALL_TOKENS
    for t in tasks[:n_aa]:
        total += 2 * tok(_task_chars(t)) + 2 * SUBJECT_OUT_TOKENS + 2 * JUDGE_CALL_TOKENS
    return total


def skill_chars(skill: str) -> int:
    from . import mine, realrun
    try:
        path = realrun.find_skill_file(realrun.skill_home(skill), skill)
        return len(path.read_text(encoding="utf-8", errors="replace")) if path else 0
    except OSError:
        return 0


def pack_tasks(skill: str) -> Optional[list[dict[str, Any]]]:
    """The skill's built-in question set (packs.py), or None when it has none."""
    try:
        from . import packs
    except ImportError:
        return None
    return packs.tasks_for(skill) or None


def choose_tasks(skill: str, source: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """``(tasks, counts, source used)``. See ``start`` for what ``source`` means."""
    tasks, counts = ([], {"uses": 0, "eligible": 0, "tested": 0, "loads": 0, "kept": 0, "dropped": {}}) \
        if source == "pack" else find_tasks(skill)
    if source != "history" and len(tasks) < PACK_BELOW:
        pack = pack_tasks(skill)
        if pack:
            return pack, counts, "pack"
    return tasks, counts, "history"


def verdict_plan(ctx: Any, skill: str, source: str = "auto") -> dict[str, Any]:
    from . import commands, realrun
    tasks, counts, used = choose_tasks(skill, source)
    available = int(counts.get("eligible") or 0) if used == "history" else len(tasks)
    tasks = tasks[:TASK_LIMIT]
    counts = {**counts, "tested": len(tasks)}
    n_aa = min(AA_LIMIT, len(tasks))  # the same-vs-same noise check can use any test
    total = (len(tasks) + n_aa) * realrun.CALLS_PER_PAIR
    limits = answer_limits(skill) if used == "pack" else {}
    from . import redact as sb_redact
    names = sb_redact.load_names(sb_paths.names_path())
    return {"skill": skill, "tasks": tasks, "counts": counts, "n_aa": n_aa, "total": total, "source": used,
            "unused": max(0, available - len(tasks)),
            "min": commands.min_tasks(), "cap": max_calls(ctx), "routes": routes(ctx),
            "names_loaded": len(names), "tokens": verdict_tokens(tasks, n_aa, skill_chars(skill)),
            "minutes": minutes_estimate(total, "verdict", VERDICT_WORKERS, limits.get("seconds_per_call")),
            "limits": limits, "active_model": active_model_id()}


def answer_limits(skill: str) -> dict[str, Any]:
    """Answer length and time settings for a built-in question set (packs.answer_limits)."""
    from . import packs
    return packs.answer_limits(skill)


def plan_line(pl: Mapping[str, Any]) -> str:
    """The popup line once the plan is known: how many uses were found, how many get tested, how long."""
    if pl.get("source") == "pack":
        return sb_copy.PACK_PLAN_LINE.format(skill=pl["skill"], n=len(pl["tasks"]), minutes=pl["minutes"])
    return sb_copy.PLAN_LINE.format(skill=pl["skill"], n=len(pl["tasks"]), minutes=pl["minutes"])


def not_enough_text(pl: Mapping[str, Any]) -> str:
    c = pl["counts"]
    if c.get("no_db"):
        return (f"Not enough history to test '{pl['skill']}': no session history was found.\n"
                "Use the skill in a few chats, then try again.")
    return (f"Not enough history to test '{pl['skill']}': {len(pl['tasks'])} of {pl['min']} tasks needed.\n"
            "No calls were made. Use it in more single-question chats first.")


def start(ctx: Any, skill: str, source: str = "auto", *, session_id: str = "", background: bool = False) -> str:
    """Start a test of ``skill`` and return one line at once. The Skilluminati page calls this too.

    ``source``: "history" uses only your past chats, "pack" only the skill's built-in question set,
    "auto" (the default) uses your history and falls back to the built-in set when history is short.
    Reading history is slow (minutes on a big history), so it happens inside the background job."""
    blocked = blocked_text(ctx)
    if blocked:
        return blocked
    guard = sb_llm.CommandGuard(getattr(ctx, "llm", None), cap=0)  # the cap is set once the plan is known

    def work() -> Any:
        pl = verdict_plan(ctx, skill, source)
        if len(pl["tasks"]) < pl["min"]:
            return not_enough_text(pl)
        if pl["total"] > pl["cap"]:
            return f"Nothing was run: {skill} needs {pl['total']} calls, above max_calls ({pl['cap']})."
        guard.status = plan_line(pl)
        guard.cap = pl["total"]
        return _verdict_work(pl, skill, guard)

    start_line = sb_copy.START_LINE.format(skill=skill)
    return launch(ctx, f"test of '{skill}'", guard, work, sb_copy.GETTING_READY.format(skill=skill), start_line,
                  session_id=session_id, kind="verdict", skill=skill, background=background)


def _verdict_work(pl: Mapping[str, Any], skill: str, guard: sb_llm.CommandGuard) -> Path:
    from . import realrun
    out_dir = sb_paths.new_run_dir(f"verdict-{skill}", _stamp())
    strict_path = sb_paths.private_file(out_dir / "tasks.jsonl")
    frozen_path = sb_paths.private_file(out_dir / "tasks_frozen.jsonl")
    with strict_path.open("w", encoding="utf-8") as fs, frozen_path.open("w", encoding="utf-8") as ff:
        for t in pl["tasks"]:
            (ff if t.get("frozen_messages") else fs).write(json.dumps(t, ensure_ascii=False) + "\n")
    from . import mine, packs
    r = pl["routes"]
    # A built-in question set brings its own copy of the skill; a history run uses your installed one.
    skills_dir = packs.PACKS_DIR / skill if pl.get("source") == "pack" else realrun.skill_home(skill)
    limits = {k: v for k, v in (pl.get("limits") or {}).items() if k != "seconds_per_call"}
    cfg = realrun.RealRunConfig(
        skills=(skill,), skills_dir=skills_dir, fixtures_dir=sb_paths.FIXTURES_DIR,
        strict_path=strict_path, frozen_path=frozen_path, names_path=sb_paths.names_path(),
        tracked_dir=out_dir, private_dir=out_dir, provider=r["subject"]["provider"],
        subject_model=r["subject"]["model"], judge_model=r["job"]["model"], judge_provider=r["job"]["provider"],
        cap=pl["total"], tasks_per_skill=TASK_LIMIT, aa_tasks=pl["n_aa"], workers=VERDICT_WORKERS,
        tasks=list(pl["tasks"]), **limits)
    runner = realrun.RealRunner(guard, cfg, log=_progress(out_dir))
    results = runner.run()
    results["counts"] = {k: pl["counts"].get(k) for k in ("uses", "eligible", "tested")}
    results["source"] = pl.get("source")
    stamp_verdict_models(results, _read_calls(out_dir), guard)
    model = results.get("subject_model") or r["subject"]["model"] or guard.active_model or "unknown"
    summary = (results.get("per_skill") or {}).get(skill)
    if summary:
        summary["unused_tasks"] = pl.get("unused")
        summary["run"] = out_dir.name
        summary["counts"] = results["counts"]
        store_verdict(summary, model=model, judge=results.get("judge_model") or r["job"]["model"] or guard.active_model,
                      aa=results.get("aa"), partial=bool(results.get("stopped_early")),
                      source=sb_copy.SOURCE_PACK if pl.get("source") == "pack" else sb_copy.SOURCE_HISTORY)
    (out_dir / "result.json").write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    return out_dir


def _read_calls(out_dir: Path) -> list[dict[str, Any]]:
    """The run's tracked call rows (no text fields), for the model stamp."""
    rows: list[dict[str, Any]] = []
    for f in sorted(out_dir.glob("calls*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    return rows


def last_command(args: list[str]) -> str:
    """``/skilluminati last [skill|selftest]``: the latest finished report, plain lines for the terminal."""
    from . import report
    want = args[0] if args else None
    folder = report.find_run(skill=want)
    going = active_status()
    lead = f"A {going['label']} is still going: {going['used']} of {going['cap']} calls.\n" if going else ""
    if folder is None:
        return lead + (f"No finished Skilluminati run for '{want}'." if want else "No finished Skilluminati run yet.")
    return lead + (report.terminal(folder) or f"The result in {folder.name} could not be read.")


# ── verdicts.json (the Desktop page reads it) ────────────────────────────────

STORED_KEYS = ("skill", "verdict", "effect", "user_label", "model", "judge", "date", "wins", "losses", "ties",
               "low", "high", "tasks_to_settle", "heavy", "recommendation", "source", "test_type", "run", "counts")


#: A/A fields the page shows (the backend serves only these; see dashboard AA_KEYS).
AA_STORED_KEYS = ("model", "wins", "losses", "ties", "low", "high", "noisy", "date")


def aa_row(aa: Any, *, model: str, date: Optional[str]) -> Optional[dict[str, Any]]:
    """The noise-check row for the page, or None when the run had no A/A pairs."""
    if not isinstance(aa, Mapping) or not isinstance(aa.get("wins"), int):
        return None
    row = {k: aa.get(k) for k in AA_STORED_KEYS}
    row["model"] = aa.get("model") or model
    row["date"] = date
    return row


def _is_partial_row(row: Any) -> bool:
    flags = ((row.get("recommendation") or {}).get("flags") or []) if isinstance(row, Mapping) else []
    return any(isinstance(f, Mapping) and f.get("key") == FLAG_PARTIAL_KEY for f in flags)


FLAG_PARTIAL_KEY = "partial"


def _settle_wording(row: dict[str, Any], unused: Any) -> None:
    """Wording only: with no unused tasks left in the history, no number."""
    if unused != 0:
        return
    row["tasks_to_settle"] = None
    rec = row.get("recommendation")
    ns = rec.get("next_step") if isinstance(rec, dict) else None
    if isinstance(ns, dict) and ns.get("key") == "run_more":
        rec["next_step"] = {"key": "more_history", "text": sb_copy.NEXT_NO_HISTORY, "n": None}


def store_verdict(summary: Mapping[str, Any], *, model: str, judge: Optional[str], aa: Any = None,
                  partial: bool = False, source: str = sb_copy.SOURCE_HISTORY) -> Path:
    """Add or replace this skill's row in ``verdicts.json`` (one row per skill and model), and the
    run's A/A noise check (one row per model: the latest).

    A run that stopped early never replaces a verdict from a finished run; with none stored yet it
    is stored and flagged "Partial: the run stopped early"."""
    path = sb_paths.verdicts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        data = {"schema": 1, "runs": [], "aa": []}
    c = summary["combined"]
    row = {"skill": summary["skill"], "verdict": c["verdict"], "effect": summary.get("effect"),
           "user_label": summary.get("user_label"), "model": model, "judge": judge,
           "date": summary["stamp"]["date"], "wins": c["wins"], "losses": c["losses"], "ties": c["ties"],
           "low": c["low"], "high": c["high"], "tasks_to_settle": c.get("tasks_to_settle"),
           "heavy": summary.get("heavy"), "recommendation": summary.get("recommendation"),
           "source": source, "test_type": summary["stamp"].get("test_type"),
           "run": summary.get("run"), "counts": summary.get("counts")}
    row = {k: row.get(k) for k in STORED_KEYS}
    row["recommendation"] = json.loads(json.dumps(row["recommendation"])) if isinstance(row["recommendation"], dict) else None
    _settle_wording(row, summary.get("unused_tasks"))
    if partial:
        same = [x for x in data["runs"] if isinstance(x, dict) and x.get("skill") == row["skill"] and x.get("model") == model]
        if any(not _is_partial_row(x) for x in same):
            return path  # keep the finished verdict; this run has its own result.json and report.md
        if isinstance(row["recommendation"], dict):
            row["recommendation"].setdefault("flags", []).append({"key": FLAG_PARTIAL_KEY, "text": sb_copy.FLAG_PARTIAL})
    data["runs"] = [r for r in data["runs"] if not (isinstance(r, dict) and r.get("skill") == row["skill"]
                                                     and r.get("model") == model)] + [row]
    noise = aa_row(aa, model=model, date=row["date"])
    if noise is not None:
        prev = data.get("aa")
        old: list[Any] = prev if isinstance(prev, list) else []
        data["aa"] = [r for r in old if not (isinstance(r, dict) and r.get("model") == noise["model"])] + [noise]
    data["generated_at"] = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
