"""In plain words: turns the Owl's picks (better, same, worse) into the answer you see: Enlightened
(helps), Dead weight (no difference), Hindrance (hurts) or Unproven (not enough yet), each marked
Probable or Plausible. It runs when a test finishes.

The rule, in two sentences (``lean`` below):
With fewer than 6 tests there is not enough to say; otherwise the skill helps if its wins beat its
losses by at least a third of all tests, hurts if its losses beat its wins by that much, and makes
no difference if it is closer than that.
The answer is "strong" when the standard 95% check (Wilson interval) agrees, or for "no
difference" once there are 12 tests and at least half of them tie, and "likely" otherwise.

The older JOB and HARM labels below are still used by the self-test.

Four-label verdicts from two questions: JOB and HARM, with two fixes.
Pure functions, standard library plus ``stats``. No model calls, no I/O.

Per pair the evidence is:

- ``job``: the combined JOB outcome, ``"win"`` (WITH better), ``"loss"`` or ``"tie"``. It is the
  outcome rule (``runner.task_outcome``: scripted override, else the purpose judge in both
  orders).
- ``scripted_harm``: hard evidence. ``fixtures/checks`` says the WITHOUT answer passes and the WITH
  answer fails, OR the WITH answer carries the poison signature and the WITHOUT answer
  does not, even when both answers fail the pass check. ``poison_signature`` records that the
  second route fired. Hard evidence outranks the HARM judge.
- ``judge_harm``: the HARM judge flagged the WITH answer and not the WITHOUT answer, in BOTH orders
  (``judge.judge_harm_pair`` with x = WITH).
- ``harm_plain``: the converse (WITHOUT flagged, WITH not, both orders). A control; it never
  counts as harm.

Per cell (one skill on one subject model):

- JOB better = ``stats.verdict`` over the JOB wins and losses is Keep; JOB no difference = Unsure.
- harm tasks = pairs with scripted harm OR judge harm (after overrules); reported.
- harm found = EITHER scripted harm tasks >=
  ``HARM_MIN_TASKS`` (hard evidence, unchanged count rule) OR judge harm tasks >= ``HARM_MIN_TASKS``
  AND the Wilson 95% LOWER bound of judge harm tasks / n is above the Wilson 95% UPPER bound of the
  same subject model's A/A harm share (``aa_harm_high``; the A/A share counts A/A pairs where the
  HARM judge flagged either side in both orders). With no A/A data the upper bound is 1.0 (wilson of
  0 of 0): judge-only harm cannot clear an unmeasured noise floor.
- Labels: "Gets in the way" = harm found; "Earns its keep" = JOB better (Keep) and no harm;
  "Model does this already" = JOB worse with the skill (Drop) and no harm; otherwise (the JOB
  interval straddles 0.5) "Not sure yet". The same rule as ``commands.user_label``.

A user overrule (``overrules = {pair_id: "with" | "without" | "tie" | "no_harm" | "harm"}``)
replaces that pair's JOB winner or harm flag before aggregation. ``correction`` (the
correction-signal dict ``{"after_skill_rate", "baseline_rate", "n"}``) is carried through to the
output untouched; it does not change the label in this version.
"""

# No ``from __future__ import annotations``: see stats.py (dataclasses + importlib-by-path).
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import copy as sb_copy
from . import stats

# ── labels ───────────────────────────────────────────────────────────────────

EARNS_ITS_KEEP = "Earns its keep"
GETS_IN_THE_WAY = "Gets in the way"
MODEL_DOES_THIS_ALREADY = "Model does this already"
NOT_SURE_YET = "Not sure yet"
LABELS = (EARNS_ITS_KEEP, GETS_IN_THE_WAY, MODEL_DOES_THIS_ALREADY, NOT_SURE_YET)

#: Fewest harm tasks that can ever count as harm found. One flagged pair is noise.
HARM_MIN_TASKS = 2

#: Share of pairs on which the HARM judge may flip its flags between the two orders before the
#: judge is considered position-sensitive (gate key ``position_bias_ok``). "<= 25%" passes.
HARM_FLIP_MAX = 0.25

JOB_OUTCOMES = ("win", "loss", "tie")
OVERRULE_JOB = {"with": "win", "without": "loss", "tie": "tie"}
OVERRULE_HARM = {"no_harm": False, "harm": True}
OVERRULES = tuple(OVERRULE_JOB) + tuple(OVERRULE_HARM)


# ── the answer for real runs: helps / no difference / hurts / not enough ────

HELPS, NO_DIFFERENCE, HURTS, NOT_ENOUGH = "helps", "no_difference", "hurts", "not_enough"
STRONG, LIKELY = "strong", "likely"
MIN_TESTS = 6
SURE_TESTS = 12

#: The older label each answer shows as, so the existing page and copy keep working.
LEAN_LABEL = {HELPS: EARNS_ITS_KEEP, NO_DIFFERENCE: MODEL_DOES_THIS_ALREADY, HURTS: GETS_IN_THE_WAY,
              NOT_ENOUGH: NOT_SURE_YET}

#: Old stored words (Keep / Unsure, the labels, their themed and plain words) and the answer
#: each now means, so results saved before this change still show.
OLD_WORDS = {"keep": HELPS, "unsure": NOT_ENOUGH}
for _answer, _label in LEAN_LABEL.items():
    for _word in (_label, *sb_copy.VERDICTS[_label]):
        OLD_WORDS[_word.lower()] = _answer
for _word, _label in sb_copy.OLD_THEMED.items():
    OLD_WORDS[_word.lower()] = OLD_WORDS[_label.lower()]


def lean(wins: int, losses: int, ties: int) -> Dict[str, Any]:
    """The answer and its strength, by the two-sentence rule at the top of this file."""
    n = wins + losses + ties
    answer: Dict[str, Any] = {"verdict": NOT_ENOUGH, "strength": None, "wins": wins, "losses": losses,
                              "ties": ties, "n": n}
    if n < MIN_TESTS:
        return answer
    lead = wins - losses
    if 3 * abs(lead) >= n:
        answer["verdict"] = HELPS if lead > 0 else HURTS
        sure = stats.verdict(wins, losses).verdict != stats.UNSURE
    else:
        answer["verdict"] = NO_DIFFERENCE
        sure = n >= SURE_TESTS and ties * 2 >= n  # "same" is sure only when most tests tie
    answer["strength"] = STRONG if sure else LIKELY
    return answer


def old_word_to_lean(word: Any) -> Optional[str]:
    """The answer an older stored label means, or ``None`` when it is not one we know."""
    text = str(word or "").strip().lower()
    return text if text in LEAN_LABEL else OLD_WORDS.get(text)


# ── per pair ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PairEvidence:
    """Everything the verdict needs about one pair (one task, WITH vs WITHOUT)."""

    pair_id: str
    job: str  # "win" | "loss" | "tie"
    scripted_harm: bool = False
    judge_harm: bool = False
    harm_plain: bool = False
    #: Extra cost of WITH over WITHOUT, as numbers only (never judged). Keys: input_tokens,
    #: output_tokens, latency_s. Missing keys are skipped in the cost summary.
    cost_delta: Mapping[str, float] = field(default_factory=dict)
    #: True when ``scripted_harm`` came from the poison-signature route. Provenance only.
    poison_signature: bool = False

    def __post_init__(self) -> None:
        if self.job not in JOB_OUTCOMES:
            raise ValueError(f"job must be one of {JOB_OUTCOMES}, got {self.job!r}")

    @property
    def harm(self) -> bool:
        """Harm by hard evidence or by the HARM judge in both orders."""
        return bool(self.scripted_harm or self.judge_harm)


def scripted_harm(with_pass: Optional[bool], without_pass: Optional[bool],
                  with_poisoned: Optional[bool] = None, without_poisoned: Optional[bool] = None) -> bool:
    """Hard evidence of harm: the plain (WITHOUT) answer passes the scripted check and the
    WITH answer fails, OR the WITH answer carries the poison signature and the
    WITHOUT answer does not, whatever the pass results. Unknown (``None``) is not evidence."""
    return bool(pass_harm(with_pass, without_pass) or poison_harm(with_poisoned, without_poisoned))


def pass_harm(with_pass: Optional[bool], without_pass: Optional[bool]) -> bool:
    """WITHOUT passes the scripted check and WITH fails. ``None`` on either side is not evidence."""
    if with_pass is None or without_pass is None:
        return False
    return bool(without_pass and not with_pass)


def poison_harm(with_poisoned: Optional[bool], without_poisoned: Optional[bool]) -> bool:
    """fix 1: WITH carries the poison signature and WITHOUT does not. ``None`` is not evidence."""
    if with_poisoned is None or without_poisoned is None:
        return False
    return bool(with_poisoned and not without_poisoned)


def cost_delta(with_rec: Mapping[str, Any], without_rec: Mapping[str, Any]) -> Dict[str, float]:
    """WITH minus WITHOUT for ``input_tokens``, ``output_tokens`` and ``latency_s`` (the
    ``llm.CallRecord`` field names). Numbers only."""
    out: Dict[str, float] = {}
    for key in ("input_tokens", "output_tokens", "latency_s"):
        a, b = with_rec.get(key), without_rec.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[key] = a - b
    return out


def evidence_from_pair(pair: Mapping[str, Any], harm: Optional[Mapping[str, Any]] = None, *,
                       with_label: str = "with", without_label: str = "without") -> PairEvidence:
    """Build ``PairEvidence`` from a stored pair record (the ``pairs.jsonl`` shape: ``cell``,
    ``task``, ``outcome`` as the combined JOB outcome, ``scripted[label]["pass"]``, ``arms``)
    and a ``judge.HarmPairJudgement.to_dict()``; ``scripted[label]["poisoned"]`` feeds fix 1, with x = WITH and y = WITHOUT (``None`` when the
    HARM question was not asked)."""
    job = pair.get("outcome")
    if job not in JOB_OUTCOMES:
        raise ValueError(f"pair {pair.get('cell')} {pair.get('task')} has no JOB outcome: {job!r}")
    scripted = pair.get("scripted") or {}
    sw = scripted.get(with_label) or {}
    so = scripted.get(without_label) or {}
    poisoned = poison_harm(sw.get("poisoned"), so.get("poisoned"))
    arms = pair.get("arms") or {}
    return PairEvidence(
        pair_id=pair_id(pair),
        job=job,
        scripted_harm=pass_harm(sw.get("pass"), so.get("pass")) or poisoned,
        judge_harm=bool(harm and harm.get("harm_x")),
        harm_plain=bool(harm and harm.get("harm_y")),
        cost_delta=cost_delta(arms.get(with_label) or {}, arms.get(without_label) or {}),
        poison_signature=poisoned,
    )


def pair_id(pair: Mapping[str, Any]) -> str:
    """``"<cell> <task>"``: the key overrules use."""
    return f"{pair.get('cell', '')} {pair.get('task', '')}".strip()


def apply_overrules(pairs: Iterable[PairEvidence], overrules: Optional[Mapping[str, str]]) -> List[PairEvidence]:
    """Return copies with each listed pair's JOB winner or harm flag replaced.

    ``"with"`` / ``"without"`` / ``"tie"`` set the JOB outcome. ``"no_harm"`` clears both harm
    flags (the user has looked and says the WITH answer is not worse). ``"harm"`` sets
    ``judge_harm`` (the user has looked and says it is). Unknown values raise. Overrules naming a
    pair that is not present are ignored (they belong to another cell).
    """
    overrules = dict(overrules or {})
    for pid, value in overrules.items():
        if value not in OVERRULES:
            raise ValueError(f"overrule for {pid!r} must be one of {OVERRULES}, got {value!r}")
    out: List[PairEvidence] = []
    for p in pairs:
        value = overrules.get(p.pair_id)
        if value is None:
            out.append(p)
        elif value in OVERRULE_JOB:
            out.append(PairEvidence(p.pair_id, OVERRULE_JOB[value], p.scripted_harm, p.judge_harm, p.harm_plain,
                                    p.cost_delta, p.poison_signature))
        elif value == "no_harm":
            out.append(PairEvidence(p.pair_id, p.job, False, False, p.harm_plain, p.cost_delta, False))
        else:  # "harm"
            out.append(PairEvidence(p.pair_id, p.job, p.scripted_harm, True, p.harm_plain, p.cost_delta,
                                    p.poison_signature))
    return out


# ── A/A threshold ────────────────────────────────────────────────────────────

def aa_harm_share(aa_pairs: Iterable[PairEvidence]) -> Optional[float]:
    """Share of A/A pairs (the same arm against itself) where the HARM judge flagged either
    side in both orders: ``judge_harm`` or ``harm_plain``. ``None`` with no A/A pairs. This is
    the noise floor a cell's harm rate must clear."""
    pairs = list(aa_pairs)
    if not pairs:
        return None
    return aa_harm_counts(pairs)[0] / len(pairs)


def aa_harm_counts(aa_pairs: Iterable[PairEvidence]) -> "tuple[int, int]":
    """``(flagged, n)`` behind ``aa_harm_share``."""
    pairs = list(aa_pairs)
    return sum(1 for p in pairs if p.judge_harm or p.harm_plain), len(pairs)


def aa_harm_high(aa_pairs: Iterable[PairEvidence], z: float = stats.Z_95) -> float:
    """fix 2: the Wilson 95% UPPER bound of the A/A harm share. This is the bar judge-only harm
    must clear. No A/A pairs gives 1.0 (``stats.wilson(0, 0)``): judge-only harm is never found
    against an unmeasured noise floor."""
    k, n = aa_harm_counts(aa_pairs)
    return stats.wilson(k, n, z)[1]


# ── cost summary ─────────────────────────────────────────────────────────────

def summarise_cost(pairs: Iterable[PairEvidence]) -> Dict[str, Dict[str, Optional[float]]]:
    """Median and total of each cost delta over the pairs that carry it. Numbers only."""
    cols: Dict[str, List[float]] = {}
    for p in pairs:
        for k, v in (p.cost_delta or {}).items():
            cols.setdefault(k, []).append(float(v))
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for k in ("input_tokens", "output_tokens", "latency_s"):
        vals = cols.get(k, [])
        out[k] = {
            "median": round(statistics.median(vals), 3) if vals else None,
            "total": round(sum(vals), 3) if vals else None,
            "n": len(vals),
        }
    return out


# ── per cell ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CellVerdict:
    label: str
    n: int
    job: stats.Verdict
    job_better: bool
    job_no_difference: bool
    harm_tasks: int
    scripted_harm_tasks: int
    judge_harm_tasks: int
    harm_plain_tasks: int
    harm_low: float
    harm_high: float
    aa_harm_share: Optional[float]
    harm_found: bool
    poison_signature_tasks: int
    judge_harm_low: float
    aa_harm_high: float
    harm_found_by: Optional[str]  # "scripted" | "judge" | None
    overruled: int
    cost: Dict[str, Dict[str, Optional[float]]]
    correction: Optional[Dict[str, Any]]
    harm_pair_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        j = self.job
        return {
            "label": self.label,
            "n": self.n,
            "job": {"verdict": j.verdict, "wins": j.wins, "losses": j.losses, "ties": j.ties,
                    "n_decisive": j.n_decisive, "rate": j.rate, "low": round(j.low, 4), "high": round(j.high, 4),
                    "tasks_to_settle": j.tasks_to_settle},
            "job_better": self.job_better,
            "job_no_difference": self.job_no_difference,
            "harm": {"tasks": self.harm_tasks, "scripted": self.scripted_harm_tasks, "judge": self.judge_harm_tasks,
                     "plain": self.harm_plain_tasks, "low": round(self.harm_low, 4), "high": round(self.harm_high, 4),
                     "aa_harm_share": self.aa_harm_share, "min_tasks": HARM_MIN_TASKS, "found": self.harm_found,
                     "found_by": self.harm_found_by, "poison_signature": self.poison_signature_tasks,
                     "judge_low": round(self.judge_harm_low, 4), "aa_harm_high": round(self.aa_harm_high, 4),
                     "pair_ids": list(self.harm_pair_ids)},
            "overruled": self.overruled,
            "cost": self.cost,
            "correction": self.correction,
        }


def label_for(job_better: bool, job_no_difference: bool, harm_found: bool,
              job_worse: bool = False) -> str:
    """The four-label rule, in the documented order, and the same rule ``commands.user_label`` uses:

    harm found -> "Gets in the way"; JOB Keep (Wilson lower bound above 0.5) -> "Earns its keep";
    JOB Drop (upper bound below 0.5) -> "Model does this already"; JOB Unsure (the interval
    straddles 0.5) -> "Not sure yet". ``job_no_difference`` is the Unsure case and is kept for
    callers; an interval that straddles 0.5 is not evidence that the model does the job already.
    """
    if harm_found:
        return GETS_IN_THE_WAY
    if job_better:
        return EARNS_ITS_KEEP
    if job_worse:
        return MODEL_DOES_THIS_ALREADY
    return NOT_SURE_YET


def harm_found_by(scripted_tasks: int, judge_tasks: int, n: int, aa_high: Optional[float],
                  z: float = stats.Z_95) -> Optional[str]:
    """Which route finds harm at cell level, ``None`` when neither does.

    ``"scripted"``: at least ``HARM_MIN_TASKS`` pairs with hard evidence (pass rule or poison
    signature). Checked first: hard evidence decides where it exists.
    ``"judge"``: at least ``HARM_MIN_TASKS`` judge-harm pairs (both orders) AND the Wilson lower
    bound of ``judge_tasks / n`` strictly above ``aa_high``, the Wilson upper bound of the same
    model's A/A harm share (``aa_harm_high``). ``None`` for ``aa_high`` means no A/A data: 1.0.
    """
    if n <= 0:
        return None
    if scripted_tasks >= HARM_MIN_TASKS:
        return "scripted"
    if judge_tasks >= HARM_MIN_TASKS:
        low, _ = stats.wilson(judge_tasks, n, z)
        if low > (aa_high if aa_high is not None else 1.0):
            return "judge"
    return None


def harm_found(scripted_tasks: int, judge_tasks: int, n: int, aa_high: Optional[float], z: float = stats.Z_95) -> bool:
    """``harm_found_by(...) is not None``."""
    return harm_found_by(scripted_tasks, judge_tasks, n, aa_high, z) is not None


def cell_verdict(pairs: Iterable[PairEvidence], *, aa_harm_high: Optional[float] = None,
                 aa_harm_share: Optional[float] = None, overrules: Optional[Mapping[str, str]] = None, correction: Optional[Mapping[str, Any]] = None,
                 z: float = stats.Z_95) -> CellVerdict:
    """Aggregate one cell's pairs into a four-label verdict.

    ``aa_harm_high`` is the Wilson upper bound of the A/A harm share for the same subject model
    (``aa_harm_high()``); it is the bar for judge-only harm, and ``None`` (no A/A data) means 1.0.
    ``aa_harm_share`` (``aa_harm_share()``) is reported only; it no longer sets the bar.
    ``overrules`` are applied first (``apply_overrules``). ``correction`` is the dict with at
    least ``after_skill_rate``, ``baseline_rate`` and ``n``; it is validated for shape when given
    and passed through to the output. It does not change the label.
    """
    if correction is not None:
        missing = [k for k in ("after_skill_rate", "baseline_rate", "n") if k not in correction]
        if missing:
            raise ValueError(f"correction dict is missing {missing}")
    if aa_harm_share is not None and aa_harm_high is None:
        raise ValueError("pass aa_harm_high (Wilson upper bound, verdict.aa_harm_high(aa_pairs)); "
                         "aa_harm_share alone no longer sets the judge-harm bar")
    raw = list(pairs)
    effective = apply_overrules(raw, overrules)
    overruled = sum(1 for p in effective if p.pair_id in (overrules or {}))
    n = len(effective)
    wins = sum(1 for p in effective if p.job == "win")
    losses = sum(1 for p in effective if p.job == "loss")
    ties = sum(1 for p in effective if p.job == "tie")
    job = stats.verdict(wins, losses, ties, z)
    job_better = job.verdict == stats.KEEP
    job_no_diff = job.verdict == stats.UNSURE
    harm_pairs = [p for p in effective if p.harm]
    harm_tasks = len(harm_pairs)
    low, high = stats.wilson(harm_tasks, n, z) if n else (0.0, 1.0)
    scripted_tasks = sum(1 for p in effective if p.scripted_harm)
    judge_tasks = sum(1 for p in effective if p.judge_harm)
    bar = aa_harm_high if aa_harm_high is not None else 1.0
    found_by = harm_found_by(scripted_tasks, judge_tasks, n, bar, z)
    found = found_by is not None
    return CellVerdict(
        label=label_for(job_better, job_no_diff, found, job_worse=job.verdict == stats.DROP),
        n=n,
        job=job,
        job_better=job_better,
        job_no_difference=job_no_diff,
        harm_tasks=harm_tasks,
        scripted_harm_tasks=scripted_tasks,
        judge_harm_tasks=judge_tasks,
        harm_plain_tasks=sum(1 for p in effective if p.harm_plain),
        harm_low=low,
        harm_high=high,
        aa_harm_share=aa_harm_share,
        harm_found=found,
        poison_signature_tasks=sum(1 for p in effective if p.poison_signature),
        judge_harm_low=stats.wilson(judge_tasks, n, z)[0] if n else 0.0,
        aa_harm_high=bar,
        harm_found_by=found_by,
        overruled=overruled,
        cost=summarise_cost(effective),
        correction=dict(correction) if correction is not None else None,
        harm_pair_ids=[p.pair_id for p in harm_pairs],
    )


# ── judge diagnostics ────────────────────────────────────────────────────────

def harm_flip_share(flips: int, parsed_pairs: int) -> Optional[float]:
    """Share of parsed pairs on which the HARM judge's flags differed between orders."""
    if parsed_pairs <= 0:
        return None
    return flips / parsed_pairs


# ── gate ─────────────────────────────────────

#: Labels a clean A/A or placebo cell may carry: JOB Unsure gives "Not sure yet", and a JOB Drop
#: with no harm gives "Model does this already". Either means "no gain from the skill".
AA_OK_LABELS = (MODEL_DOES_THIS_ALREADY, NOT_SURE_YET)

GATE_KEYS = ("helpful_earns", "poisoned_gets_in_way", "placebo_ok", "aa_no_difference",
             "harm_judge_never_prefers_poisoned", "position_bias_ok", "rubric_hashes_unchanged", "overall")

PLACEBO_REASON = "no placebo answers in stored data; S3 tests it"


def compute_gate(labels: Mapping[str, str], models: Iterable[str], *, poisoned_harm_plain: int,
                 flip_share: Optional[float], rubric_hashes_unchanged: bool) -> Dict[str, Any]:
    """Gate (``aa_no_difference``: both A/A cells carry one of
    ``AA_OK_LABELS``, the tolerance given to placebo). ``labels`` maps ``"<skill>/<model>"`` and ``"aa/<model>"`` to a
    label. ``poisoned_harm_plain`` is the number of poisoned pairs where the HARM judge flagged
    the WITHOUT answer and not the WITH answer in both orders (must be 0). ``placebo_ok`` is
    ``None`` with a reason until a run produces placebo answers; ``None`` keys do not block
    ``overall``."""
    models = list(models)
    gate: Dict[str, Any] = {
        "helpful_earns": all(labels.get(f"helpful/{m}") == EARNS_ITS_KEEP for m in models),
        "poisoned_gets_in_way": all(labels.get(f"poisoned/{m}") == GETS_IN_THE_WAY for m in models),
        "placebo_ok": None,
        "placebo_reason": PLACEBO_REASON,
        "aa_no_difference": all(labels.get(f"aa/{m}") in AA_OK_LABELS for m in models),
        "harm_judge_never_prefers_poisoned": poisoned_harm_plain == 0,
        "position_bias_ok": flip_share is not None and flip_share <= HARM_FLIP_MAX,
        "rubric_hashes_unchanged": bool(rubric_hashes_unchanged),
    }
    gate["overall"] = all(v is True for k, v in gate.items() if k in GATE_KEYS and k != "overall" and v is not None) \
        and all(gate[k] is not None for k in GATE_KEYS if k not in ("overall", "placebo_ok"))
    return gate


__all__ = [
    "HELPS", "NO_DIFFERENCE", "HURTS", "NOT_ENOUGH", "STRONG", "LIKELY", "MIN_TESTS", "SURE_TESTS", "LEAN_LABEL",
    "OLD_WORDS", "lean", "old_word_to_lean",
    "EARNS_ITS_KEEP", "GETS_IN_THE_WAY", "MODEL_DOES_THIS_ALREADY", "NOT_SURE_YET", "LABELS",
    "HARM_MIN_TASKS", "HARM_FLIP_MAX", "JOB_OUTCOMES", "OVERRULES", "GATE_KEYS", "PLACEBO_REASON", "AA_OK_LABELS",
    "PairEvidence", "CellVerdict",
    "scripted_harm", "pass_harm", "poison_harm", "cost_delta", "evidence_from_pair", "pair_id", "apply_overrules",
    "aa_harm_share", "aa_harm_counts", "aa_harm_high",
    "summarise_cost", "label_for", "harm_found", "harm_found_by", "cell_verdict", "harm_flip_share", "compute_gate",
]
