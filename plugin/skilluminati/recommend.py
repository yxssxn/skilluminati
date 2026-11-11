"""In plain words: builds the advice behind a result: the headline, how sure it is, any warnings and
a suggested next step. It runs when a test finishes. The report shows the next step only when the
answer is to test more uses; otherwise the verdict line already says keep, fix or remove.

A recommendation with caveats for every result: never pass or fail.

When the run gives a ``lean`` (``verdict.lean``), its strength sets the confidence: strong is
Strong, likely is Moderate, not enough is Weak. Otherwise the rule below applies.

Pure functions. No model calls, no I/O, and nothing here can write to a skills directory: the module
only builds a dict.
It never deletes or edits a skill; the next step is advice for the user to act on.

Input: a cell verdict (``verdict.CellVerdict`` or its ``to_dict()``), or the primitives below.
Output: a plain dict with a headline, a confidence, a list of flags, one next step and the raw
numbers behind them. Every user-facing string comes from ``copy.py``.

Confidence rule (D = decisive tasks, that is wins plus losses; ties carry no direction; [low, high]
is the JOB Wilson 95% interval on the win rate; "margin" is how far the interval sits from 0.5 on
the deciding side: ``low - 0.5`` for "earns its keep", ``0.5 - high`` for "gets in the way"; "width"
is ``high - low``):

* Strong: D >= 20 AND margin >= 0.10. For "the model does this already" and "not sure yet" there is
  no deciding side, so the test is width <= 0.30 instead.
* Moderate: D >= 10 AND (margin > 0 OR width <= 0.45).
* Weak: anything else.
* A "gets in the way" headline driven by scripted hard evidence is at least Moderate, because hard
  evidence does not depend on the win-rate interval.

Flags (each shows only when it applies):

* Heavy: the skill loads at least 2x the median skill's tokens (text from ``cost.heavy_text``).
* Few tasks: fewer than 10 decisive tasks.
* Stale: the model changed since the verdict was made (``commands.stale_label``).
* Judges split: the HARM judge flipped between answer orders on more than ``verdict.HARM_FLIP_MAX``
  of pairs, or the JOB judge picked the first-shown answer outside 40% to 60% of the time.

Next step (first match wins):

1. Run N more tasks: the headline is "not sure yet", or confidence is Weak. N is the stats
   "tasks to settle" estimate when it is a positive number, else 10.
2. Consider archiving: the headline is "gets in the way".
3. Consider trimming: "earns its keep" or "the model does this already", AND Heavy.
4. Keep: "earns its keep" (not Heavy).
5. Consider archiving: "the model does this already" (not Heavy).
"""

# No ``from __future__ import annotations`` and no dataclasses: see stats.py (importlib-by-path).
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import copy as sb_copy
from . import verdict as sb_verdict

# ── thresholds ───────────────────────────────────────────────────────────────

STRONG_MIN_TASKS = 20
MODERATE_MIN_TASKS = 10
STRONG_MARGIN = 0.10
STRONG_WIDTH = 0.30
MODERATE_WIDTH = 0.45
#: Fewer decisive tasks than this raises the Few tasks flag.
FEW_TASKS_BELOW = 10
#: JOB first-position share must sit inside this band, ends included.
FIRST_SHARE_BAND = (0.40, 0.60)
#: N for "Run N more tasks" when stats has no usable estimate.
DEFAULT_MORE_TASKS = 10
#: Float slack so a margin of "exactly 0.10" is not lost to rounding.
EPS = 1e-9

STRONG, MODERATE, WEAK = "Strong", "Moderate", "Weak"
CONFIDENCE_LEVELS = (STRONG, MODERATE, WEAK)

STEP_KEEP, STEP_TRIM, STEP_ARCHIVE, STEP_RUN_MORE = "keep", "trim", "archive", "run_more"
FLAG_HEAVY, FLAG_FEW, FLAG_STALE, FLAG_SPLIT = "heavy", "few_tasks", "stale", "judges_split"

_NEUTRAL = (sb_verdict.MODEL_DOES_THIS_ALREADY, sb_verdict.NOT_SURE_YET)
LEAN_CONFIDENCE = {sb_verdict.STRONG: STRONG, sb_verdict.LIKELY: MODERATE}


# ── confidence ───────────────────────────────────────────────────────────────

def _margin(label: str, low: Optional[float], high: Optional[float]) -> Optional[float]:
    """Distance of the interval from 0.5 on the deciding side; ``None`` for the neutral labels or
    when the interval is unknown."""
    if low is None or high is None:
        return None
    if label == sb_verdict.EARNS_ITS_KEEP:
        return low - 0.5
    if label == sb_verdict.GETS_IN_THE_WAY:
        return 0.5 - high
    return None


def confidence(label: str, n_decisive: int, low: Optional[float], high: Optional[float],
               *, scripted_harm: bool = False) -> str:
    """``"Strong"`` | ``"Moderate"`` | ``"Weak"`` by the rule in the module docstring."""
    n = int(n_decisive or 0)
    level = WEAK
    if low is not None and high is not None:
        width = high - low
        margin = _margin(label, low, high)
        if n >= MODERATE_MIN_TASKS:
            excludes = margin is not None and margin > EPS
            if excludes or width <= MODERATE_WIDTH + EPS:
                level = MODERATE
        if n >= STRONG_MIN_TASKS:
            if label in _NEUTRAL:
                if width <= STRONG_WIDTH + EPS:
                    level = STRONG
            elif margin is not None and margin >= STRONG_MARGIN - EPS:
                level = STRONG
    if scripted_harm and label == sb_verdict.GETS_IN_THE_WAY and level == WEAK:
        level = MODERATE
    return level


# ── flags ────────────────────────────────────────────────────────────────────

def judges_split(harm_flip_share: Optional[float], job_first_share: Optional[float]) -> bool:
    """True when the HARM judge flipped between orders on more than ``HARM_FLIP_MAX`` of pairs, or the
    JOB judge's first-position share is outside ``FIRST_SHARE_BAND``. Unmeasured (``None``) is not a
    split."""
    if harm_flip_share is not None and harm_flip_share > sb_verdict.HARM_FLIP_MAX + EPS:
        return True
    if job_first_share is not None:
        lo, hi = FIRST_SHARE_BAND
        if job_first_share < lo - EPS or job_first_share > hi + EPS:
            return True
    return False


def _norm(model: Any) -> Optional[str]:
    return model.strip() if isinstance(model, str) and model.strip() else None


def build_flags(*, n_decisive: int, heavy: Optional[Mapping[str, Any]], verdict_model: Any, active_model: Any,
                harm_flip_share: Optional[float], job_first_share: Optional[float]) -> List[Dict[str, str]]:
    """Flags in a fixed order (Heavy, Few tasks, Stale, Judges split); each ``{"key", "text"}``."""
    from . import commands  # lazy: commands imports this module's output only at call time
    flags: List[Dict[str, str]] = []
    if isinstance(heavy, Mapping) and heavy.get("flag") is True and isinstance(heavy.get("text"), str):
        flags.append({"key": FLAG_HEAVY, "text": heavy["text"]})
    if int(n_decisive or 0) < FEW_TASKS_BELOW:
        flags.append({"key": FLAG_FEW, "text": sb_copy.FLAG_FEW_TASKS.format(n=int(n_decisive or 0))})
    if commands.stale_label(verdict_model, active_model):
        flags.append({"key": FLAG_STALE,
                      "text": sb_copy.FLAG_STALE.format(tested=_norm(verdict_model), active=_norm(active_model))})
    if judges_split(harm_flip_share, job_first_share):
        flags.append({"key": FLAG_SPLIT, "text": sb_copy.FLAG_JUDGES_SPLIT})
    return flags


# ── next step ────────────────────────────────────────────────────────────────

def next_step(label: str, level: str, is_heavy: bool, tasks_to_settle: Optional[int]) -> Dict[str, Any]:
    """The one next step, by the order in the module docstring. ``{"key", "text", "n"}``."""
    if label == sb_verdict.NOT_SURE_YET or level == WEAK:
        n = tasks_to_settle if isinstance(tasks_to_settle, int) and not isinstance(tasks_to_settle, bool) \
            and tasks_to_settle > 0 else DEFAULT_MORE_TASKS
        return {"key": STEP_RUN_MORE, "text": sb_copy.NEXT_RUN_MORE.format(n=n, tasks="task" if n == 1 else "tasks"), "n": n}
    if label == sb_verdict.GETS_IN_THE_WAY:
        return {"key": STEP_ARCHIVE, "text": sb_copy.NEXT_ARCHIVE, "n": None}
    if is_heavy:
        return {"key": STEP_TRIM, "text": sb_copy.NEXT_TRIM, "n": None}
    if label == sb_verdict.EARNS_ITS_KEEP:
        return {"key": STEP_KEEP, "text": sb_copy.NEXT_KEEP, "n": None}
    return {"key": STEP_ARCHIVE, "text": sb_copy.NEXT_ARCHIVE, "n": None}


# ── the recommendation ───────────────────────────────────────────────────────

def _pct(x: Optional[float]) -> Optional[int]:
    return None if x is None else int(round(100 * x))


def recommend(label: str, *, wins: int = 0, losses: int = 0, ties: int = 0,
              low: Optional[float] = None, high: Optional[float] = None,
              tasks_to_settle: Optional[int] = None, heavy: Optional[Mapping[str, Any]] = None,
              verdict_model: Any = None, active_model: Any = None,
              harm_flip_share: Optional[float] = None, job_first_share: Optional[float] = None,
              scripted_harm: bool = False, lean: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build the recommendation dict for one result.

    ``label`` is an internal ``verdict.LABELS`` value. ``low``/``high`` are the JOB Wilson interval on the
    decisive win rate; ``None`` for both means no decisive tasks. ``scripted_harm`` is True when a
    "gets in the way" headline rests on scripted hard evidence. Raises ``ValueError`` for an unknown
    label; callers on the gateway path catch it.
    """
    head = sb_copy.headline(label)
    if head is None:
        raise ValueError(f"unknown verdict label {label!r}; expected one of {sb_verdict.LABELS}")
    themed, plain, text = head
    n = int(wins or 0) + int(losses or 0)
    level = confidence(label, n, low, high, scripted_harm=scripted_harm)
    if lean:
        level = LEAN_CONFIDENCE.get(lean.get("strength"), WEAK)
    if low is None or high is None or n == 0:
        why = sb_copy.CONFIDENCE_WHY_NONE
    else:
        why = sb_copy.CONFIDENCE_WHY.format(n=n, low=_pct(low), high=_pct(high))
    flags = build_flags(n_decisive=n, heavy=heavy, verdict_model=verdict_model, active_model=active_model,
                        harm_flip_share=harm_flip_share, job_first_share=job_first_share)
    is_heavy = any(f["key"] == FLAG_HEAVY for f in flags)
    return {
        "headline": {"label": label, "themed": themed, "plain": plain, "text": text,
                     "tone": sb_copy.VERDICT_TONE.get(label, "outline")},
        "confidence": {"level": level, "text": sb_copy.CONFIDENCE_WORDS[level], "why": why},
        "flags": flags,
        "next_step": next_step(label, level, is_heavy, tasks_to_settle),
        "note": sb_copy.NEXT_NOTE,
        "lean": dict(lean) if lean else None,
        "numbers": {"n_decisive": n, "wins": int(wins or 0), "losses": int(losses or 0), "ties": int(ties or 0),
                    "low": low, "high": high,
                    "width": None if low is None or high is None else round(high - low, 4),
                    "tasks_to_settle": tasks_to_settle, "harm_flip_share": harm_flip_share,
                    "job_first_share": job_first_share, "scripted_harm": bool(scripted_harm)},
    }


def from_cell(cell: Any, *, heavy: Optional[Mapping[str, Any]] = None, verdict_model: Any = None,
              active_model: Any = None, harm_flip_share: Optional[float] = None,
              job_first_share: Optional[float] = None) -> Dict[str, Any]:
    """``recommend`` for a ``verdict.CellVerdict`` or its ``to_dict()``."""
    d = cell.to_dict() if hasattr(cell, "to_dict") else dict(cell)
    job = d["job"]
    harm = d.get("harm") or {}
    n = job.get("n_decisive") or 0
    return recommend(d["label"], wins=job.get("wins", 0), losses=job.get("losses", 0), ties=job.get("ties", 0),
                     low=job.get("low") if n else None, high=job.get("high") if n else None,
                     tasks_to_settle=job.get("tasks_to_settle"), heavy=heavy, verdict_model=verdict_model,
                     active_model=active_model, harm_flip_share=harm_flip_share,
                     job_first_share=job_first_share, scripted_harm=harm.get("found_by") == "scripted")


def label_from_run(run: Mapping[str, Any]) -> Optional[str]:
    """The internal label for a stored run dict, or ``None`` when it cannot be told.

    Uses ``user_label`` when present. Otherwise Keep and Unsure map directly; Drop maps only when the run
    says whether the effect was negative (``effect``), because a bare Drop could be either "the model does
    this already" or "gets in the way" and guessing would mislabel it."""
    given = run.get("user_label")
    if given in sb_verdict.LABELS:
        return given
    old = sb_verdict.old_word_to_lean(given)
    if old:
        return sb_verdict.LEAN_LABEL[old]
    v = str(run.get("verdict") or "").strip().lower()
    e = str(run.get("effect") or "").strip().lower()
    if v == "keep":
        return sb_verdict.EARNS_ITS_KEEP
    if v == "unsure":
        return sb_verdict.NOT_SURE_YET
    if v == "drop" and e in ("negative", "none"):
        return sb_verdict.GETS_IN_THE_WAY if e == "negative" else sb_verdict.MODEL_DOES_THIS_ALREADY
    return None


def from_run(run: Mapping[str, Any], *, active_model: Any = None) -> Optional[Dict[str, Any]]:
    """``recommend`` for a stored run dict (a ``verdicts.json`` row). ``None`` when the row lacks a label
    or counts; never raises."""
    try:
        label = label_from_run(run)
        if label is None or not isinstance(run.get("wins"), int) or not isinstance(run.get("losses"), int):
            return None
        n = run["wins"] + run["losses"]
        low, high = run.get("low"), run.get("high")
        ok = isinstance(low, (int, float)) and isinstance(high, (int, float)) and n > 0
        return recommend(label, wins=run["wins"], losses=run["losses"], ties=int(run.get("ties") or 0),
                         low=low if ok else None, high=high if ok else None,
                         tasks_to_settle=run.get("tasks_to_settle"), heavy=run.get("heavy"),
                         verdict_model=run.get("model"), active_model=active_model,
                         harm_flip_share=run.get("harm_flip_share"), job_first_share=run.get("job_first_share"),
                         scripted_harm=bool(run.get("scripted_harm")))
    except Exception:
        return None


# ── text for the slash command ───────────────────────────────────────────────

def format_text(rec: Mapping[str, Any]) -> str:
    """The recommendation as plain lines: headline, confidence, flags, next step."""
    flags = "; ".join(f["text"] for f in rec.get("flags") or []) or sb_copy.NO_FLAGS
    conf = rec["confidence"]
    return "\n".join([
        rec["headline"]["text"],
        f"{sb_copy.CONFIDENCE_LABEL}: {conf['text']} ({conf['why']})",
        f"{sb_copy.FLAGS_LABEL}: {flags}",
        f"{sb_copy.NEXT_STEP_LABEL}: {rec['next_step']['text']}",
        rec.get("note") or sb_copy.NEXT_NOTE,
    ])


def lean_of(rec: Mapping[str, Any]) -> Dict[str, Any]:
    """The four-way answer for a stored recommendation. Older results have none, so it is worked
    out from their headline label (an old "Keep" means helps, and so on), with no strength."""
    lean = rec.get("lean")
    if isinstance(lean, Mapping) and lean.get("verdict") in sb_verdict.LEAN_LABEL:
        return {"verdict": lean["verdict"], "strength": lean.get("strength") if lean.get("strength") in
                (sb_verdict.STRONG, sb_verdict.LIKELY) else None}
    head = rec.get("headline") if isinstance(rec.get("headline"), Mapping) else {}
    old = sb_verdict.old_word_to_lean(head.get("label")) or sb_verdict.old_word_to_lean(head.get("themed"))
    return {"verdict": old or sb_verdict.NOT_ENOUGH, "strength": None}


def clean(rec: Any) -> Optional[Dict[str, Any]]:
    """Whitelist a stored recommendation for the API: known keys only, strings as strings. ``None`` when
    malformed. Stored data is never trusted to carry markup or extra keys."""
    try:
        if not isinstance(rec, Mapping):
            return None
        h, c, ns = rec["headline"], rec["confidence"], rec["next_step"]
        out = {
            "headline": {k: h.get(k) for k in ("label", "themed", "plain", "text", "tone")},
            "confidence": {k: c.get(k) for k in ("level", "text", "why")},
            "flags": [{"key": str(f.get("key")), "text": str(f.get("text"))}
                      for f in rec.get("flags") or [] if isinstance(f, Mapping) and isinstance(f.get("text"), str)],
            "next_step": {k: ns.get(k) for k in ("key", "text", "n")},
            "note": rec.get("note") if isinstance(rec.get("note"), str) else None,
            "lean": lean_of(rec),
            "numbers": dict(rec["numbers"]) if isinstance(rec.get("numbers"), Mapping) else {},
        }
        if not (isinstance(out["headline"]["text"], str) and isinstance(out["confidence"]["text"], str)
                and isinstance(out["next_step"]["text"], str)):
            return None
        return out
    except Exception:
        return None


__all__ = [
    "STRONG", "MODERATE", "WEAK", "CONFIDENCE_LEVELS", "STEP_KEEP", "STEP_TRIM", "STEP_ARCHIVE", "STEP_RUN_MORE",
    "FLAG_HEAVY", "FLAG_FEW", "FLAG_STALE", "FLAG_SPLIT", "STRONG_MIN_TASKS", "MODERATE_MIN_TASKS",
    "STRONG_MARGIN", "STRONG_WIDTH", "MODERATE_WIDTH", "FEW_TASKS_BELOW", "FIRST_SHARE_BAND", "DEFAULT_MORE_TASKS",
    "confidence", "judges_split", "build_flags", "next_step", "recommend", "from_cell", "from_run", "label_from_run",
    "format_text", "clean", "lean_of", "LEAN_CONFIDENCE",
]
