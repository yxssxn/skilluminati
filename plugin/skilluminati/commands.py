"""In plain words: answers the /skilluminati command you type in a chat. It runs each time you type
it and replies at once: a test is handed to run.py, which carries it out in the background.

Commands: ``<skill>``, ``stop``, ``selftest`` (listed in the help), plus ``last``, ``estimate`` and
``--all`` (instant, free, no model calls). Return text, never print(), never raise.
"""

from __future__ import annotations

from . import copy as sb_copy

_ctx = None

UNKNOWN_MODEL = "unknown model"
DEFAULT_MIN_TASKS = 4

ALL_HEADER = (
    "Cost view is instant and free. Verdicts need replayable history; "
    "run /skilluminati <skill> to test one."
)

USAGE = sb_copy.HELP


def bind(ctx) -> None:
    """Keep the plugin context for later steps (ctx.llm, ctx.state). No calls are made here."""
    global _ctx
    _ctx = ctx
    from . import paths
    paths.bind(ctx)
    on_unload = getattr(ctx, "on_unload", None) if ctx is not None else None
    if callable(on_unload):  # a reload or disable ends a run in flight instead of orphaning it
        from . import run
        try:
            on_unload(lambda: run.stop_active("the plugin was unloaded"))
        except Exception:
            pass


def _active_model():
    """Best-effort current model id from the plugin context; None when unknown."""
    for attr in ("model", "model_name"):
        try:
            val = getattr(_ctx, attr, None)
        except Exception:
            val = None
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def min_tasks() -> int:
    """Fewest decisive tasks that can ever settle a verdict (all wins), from stats.verdict."""
    try:
        from . import stats
        for n in range(1, 50):
            if stats.verdict(n, 0).verdict == stats.KEEP:
                return n
    except Exception:
        pass
    return DEFAULT_MIN_TASKS


# ---------------------------------------------------------------- verdict labels

def user_label(internal_verdict: str, effect: str | None = None) -> str:
    """Map an internal Keep / Drop / Unsure verdict to the user-facing label.

    Drop splits on ``effect``: 'negative' means the skill made answers worse ('Gets in the way');
    'none' or no effect info means the model already does this without it. The label strings
    are the four in ``verdict.LABELS``; ``verdict.cell_verdict`` is the full rule.
    """
    from . import verdict as sb_verdict
    v = str(internal_verdict or "").strip().lower()
    e = str(effect or "").strip().lower()
    if v == "keep":
        return sb_verdict.EARNS_ITS_KEEP
    if v == "drop":
        return sb_verdict.GETS_IN_THE_WAY if e == "negative" else sb_verdict.MODEL_DOES_THIS_ALREADY
    return sb_verdict.NOT_SURE_YET


def _norm_model(model) -> str | None:
    if not isinstance(model, str):
        return None
    m = model.strip()
    if not m or m.lower() in {"unknown", UNKNOWN_MODEL, "none"}:
        return None
    return m


def stale_label(verdict_model, active_model):
    """None if the verdict's model matches the active one or either is unknown; else a stale note."""
    vm, am = _norm_model(verdict_model), _norm_model(active_model)
    if vm is None or am is None or vm.lower() == am.lower():
        return None
    return sb_copy.STALE_NOTE.format(model=am)


# ---------------------------------------------------------------- modes

def _cost_view(model, all_skills: bool) -> str:
    from . import cost
    result = dict(cost.estimate(model=model))
    named = model or UNKNOWN_MODEL
    result["model"] = named
    if all_skills:
        table = cost.format_table(result, limit=max(len(result.get("skills") or []), 1))
    else:
        table = cost.format_table(result)
    table = table.replace("skilluminati cost view", "Skilluminati cost view", 1)
    lines = [ALL_HEADER] if all_skills else []
    lines.append(f"Cost applies to: {named}"
                 + ("" if model else " (no model given and none detected, so cost is unknown)"))
    lines.append(table)
    return "\n".join(lines)


def format_verdict(label: str, heavy=None) -> str:
    """Verdict line with the Heavy cost flag beside the label: ``Label · Heavy: +N tokens per
    load (X% over the median skill)``. The flag shows only when the skill is Heavy and is never part
    of the label, so the four label strings (``verdict.LABELS``) are untouched."""
    text = heavy.get("text") if isinstance(heavy, dict) and heavy.get("flag") else None
    return f"{label} · {text}" if text else str(label)


def format_recommendation(rec) -> str:
    """The recommendation (``recommend.recommend``) as plain text: headline, confidence, flags, next
    step. Returns the empty string for a missing or malformed recommendation; never raises."""
    try:
        from . import recommend
        return recommend.format_text(rec) if rec else ""
    except Exception:
        return ""


def handle_skilluminati(raw_args: str, *, session_id: str = "") -> str:
    """Return the text to show. Runs in the gateway process: never print(), never raise.
    ``session_id`` is the calling chat's id (the host passes it to handlers that declare it); the
    Desktop half sends the "show my result" line to that chat when a run ends."""
    try:
        arg = (raw_args or "").strip()
        if not arg or arg.lower() in {"help", "-h", "--help"}:
            return USAGE
        parts = arg.split()
        head = parts[0].lower()
        if head in {"estimate", "--all"}:
            model = parts[1] if len(parts) > 1 else _active_model()
            try:
                return _cost_view(model, all_skills=head == "--all")
            except Exception as exc:  # never raise into the gateway
                return f"/skilluminati {head} failed: {type(exc).__name__}: {exc}"
        from . import run
        if head == "last":
            return run.last_command(parts[1:])
        if _ctx is None:
            return "/skilluminati: the plugin is not bound to a Hermes session, so it cannot call a model."
        if head == "stop":
            return run.stop_command()
        if head == "selftest":
            return run.run_selftest(_ctx, parts[1:], session_id=str(session_id or ""))
        if head.startswith("-"):
            return f"Unknown option '{parts[0]}'.\n\n{USAGE}"
        return run.start(_ctx, parts[0], session_id=str(session_id or ""))
    except Exception as exc:  # last-resort guard
        return f"/skilluminati failed: {type(exc).__name__}: {exc}"

