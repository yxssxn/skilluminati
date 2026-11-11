"""In plain words: the maths that says how sure a result is (a 95% range on the win rate) and how
many more uses would settle it. Other files call it when a test finishes; it asks the model nothing.

Verdict maths for skilluminati: Wilson interval, Keep/Drop/Unsure, settle estimate.

Pure Python, standard library only. No model calls, no I/O.

Vocabulary
----------
A *judgement* compares the with-skill answer against the without-skill answer on one
task. It is a ``win`` (with-skill better), a ``loss`` (without-skill better) or a
``tie``. Ties carry no information about direction, so they are excluded from the
*decisive* count that the interval is built on.
"""

# No ``from __future__ import annotations`` here on purpose: stringified dataclass
# annotations make ``dataclasses`` look the module up in ``sys.modules``, which fails
# when this file is loaded by path with importlib (as the tests do).
import math
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

# --- Thresholds (statistics and judge) ------------------------------

#: Two-sided z for the Wilson score interval. 1.96 is the 95% interval.
Z_95 = 1.96

#: The win rate a skill must beat. Keep needs the whole interval above it; Drop needs
#: the whole interval below it. Anything straddling it is Unsure.
WIN_RATE_NULL = 0.5

#: Share of judgements that picked the first-shown answer above which the judge is
#: considered position-biased and must be fixed before results are trusted.
#: "60% or lower" passes, so the flag is strictly greater than.
POSITION_BIAS_MAX = 0.60

#: Upper bound on the search for ``tasks_to_settle``. Past this many extra decisive
#: tasks the observed rate is so close to 0.5 that the estimate is not useful.
SETTLE_SEARCH_CAP = 10_000

# --- Verdict labels ----------------------------------------------------------------

KEEP = "Keep"
DROP = "Drop"
UNSURE = "Unsure"

_WIN_TOKENS = {"win", "w", "a", "first", "with", True, 1}
_LOSS_TOKENS = {"loss", "lose", "l", "b", "second", "without", False, 0}
_TIE_TOKENS = {"tie", "t", "draw", "equal", None}


# --- Wilson interval ---------------------------------------------------------------


def wilson(wins: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """Wilson score interval for ``wins`` successes in ``n`` trials.

    Returns ``(low, high)`` clamped to ``[0.0, 1.0]``. With ``n == 0`` there is no
    information, so the interval is the whole range ``(0.0, 1.0)``.

    Reference values at z=1.96: 5/5 -> low 0.566, 4/5 -> low 0.376, 0/5 -> high 0.434.
    """
    if n < 0 or wins < 0 or wins > n:
        raise ValueError(f"wilson: need 0 <= wins <= n, got wins={wins} n={n}")
    if n == 0:
        return (0.0, 1.0)
    return _wilson_rate(wins / n, n, z)


def _wilson_rate(p: float, n: float, z: float) -> Tuple[float, float]:
    """Wilson interval from an observed rate ``p`` over ``n`` trials (``n`` may be fractional)."""
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# --- Verdict -----------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """Outcome of comparing with-skill against without-skill over a task set."""

    verdict: str  # KEEP, DROP or UNSURE
    low: float  # Wilson lower bound on the decisive win rate
    high: float  # Wilson upper bound on the decisive win rate
    wins: int
    losses: int
    ties: int
    n_decisive: int  # wins + losses; ties excluded
    #: Estimated extra decisive tasks, at the observed rate, before the interval
    #: clears WIN_RATE_NULL. 0 when already settled. None when the rate is exactly
    #: 0.5, there are no decisive tasks, or the search cap is exceeded.
    tasks_to_settle: Optional[int]

    @property
    def rate(self) -> Optional[float]:
        """Observed decisive win rate, or None with no decisive tasks."""
        return self.wins / self.n_decisive if self.n_decisive else None


def _classify(low: float, high: float) -> str:
    if low > WIN_RATE_NULL:
        return KEEP
    if high < WIN_RATE_NULL:
        return DROP
    return UNSURE


def tasks_to_settle(wins: int, n: int, z: float = Z_95) -> Optional[int]:
    """Extra decisive tasks needed, at the observed rate, for the interval to clear 0.5.

    Holds the win rate ``wins / n`` fixed, grows ``n`` one task at a time, and returns
    the smallest number of extra tasks at which the verdict stops being Unsure.
    Returns 0 if already settled, and None if ``n == 0``, the rate is exactly 0.5, or
    nothing settles within ``SETTLE_SEARCH_CAP`` extra tasks.
    """
    if n <= 0:
        return None
    p = wins / n
    if p == WIN_RATE_NULL:
        return None
    if _classify(*wilson(wins, n, z)) != UNSURE:
        return 0
    for extra in range(1, SETTLE_SEARCH_CAP + 1):
        # Same observed rate projected over n + extra tasks (projected wins may be
        # fractional; the rate form of the interval tolerates that).
        if _classify(*_wilson_rate(p, n + extra, z)) != UNSURE:
            return extra
    return None


def verdict(wins: int, losses: int, ties: int = 0, z: float = Z_95) -> Verdict:
    """Keep / Drop / Unsure for ``wins`` and ``losses``; ``ties`` are not decisive."""
    for name, value in (("wins", wins), ("losses", losses), ("ties", ties)):
        if value < 0:
            raise ValueError(f"verdict: {name} must be >= 0, got {value}")
    n = wins + losses
    low, high = wilson(wins, n, z)
    return Verdict(
        verdict=_classify(low, high) if n else UNSURE,
        low=low,
        high=high,
        wins=wins,
        losses=losses,
        ties=ties,
        n_decisive=n,
        tasks_to_settle=tasks_to_settle(wins, n, z),
    )


# --- Judge diagnostics -------------------------------------------------------------


@dataclass(frozen=True)
class PositionBias:
    """Share of judgements that picked whichever answer was shown first."""

    first_picks: int
    total: int
    share: Optional[float]  # None when total == 0
    flagged: bool  # True when share > POSITION_BIAS_MAX


def position_bias(first_picks: int, total: int) -> PositionBias:
    """Share of judgements that chose the first-shown answer, flagged above 0.60.

    ``total`` should count judgements where a first/second choice was made (ties can
    be included or not; include them consistently with how ``first_picks`` was counted).
    """
    if total < 0 or first_picks < 0 or first_picks > total:
        raise ValueError(
            f"position_bias: need 0 <= first_picks <= total, got {first_picks}/{total}"
        )
    if total == 0:
        return PositionBias(first_picks, total, None, False)
    share = first_picks / total
    return PositionBias(first_picks, total, share, share > POSITION_BIAS_MAX)


@dataclass(frozen=True)
class AANoise:
    """Summary of an A/A run: the same arm judged against itself.

    A clean judge gives a decisive win rate near 0.5 with the interval covering 0.5.
    ``noisy`` is True when the interval excludes 0.5, meaning the judge is finding a
    difference between two identical arms.
    """

    n: int
    wins: int
    losses: int
    ties: int
    n_decisive: int
    rate: Optional[float]  # decisive win rate, None with no decisive judgements
    low: float
    high: float
    tie_share: Optional[float]  # None when n == 0
    noisy: bool


def _token(result) -> str:
    key = result.strip().lower() if isinstance(result, str) else result
    if key in _WIN_TOKENS:
        return "win"
    if key in _LOSS_TOKENS:
        return "loss"
    if key in _TIE_TOKENS:
        return "tie"
    raise ValueError(f"aa_noise: unrecognised result {result!r}")


def aa_noise(results: Iterable, z: float = Z_95) -> AANoise:
    """Summarise an A/A run.

    ``results`` is an iterable of judgements, each one of ``"win"``/``"loss"``/``"tie"``
    (aliases: ``"a"``/``"b"``, ``"first"``/``"second"``, ``True``/``False``/``None``).
    """
    wins = losses = ties = 0
    for r in results:
        t = _token(r)
        if t == "win":
            wins += 1
        elif t == "loss":
            losses += 1
        else:
            ties += 1
    n = wins + losses + ties
    nd = wins + losses
    low, high = wilson(wins, nd, z)
    return AANoise(
        n=n,
        wins=wins,
        losses=losses,
        ties=ties,
        n_decisive=nd,
        rate=(wins / nd) if nd else None,
        low=low,
        high=high,
        tie_share=(ties / n) if n else None,
        noisy=bool(nd) and _classify(low, high) != UNSURE,
    )


__all__ = [
    "Z_95",
    "WIN_RATE_NULL",
    "POSITION_BIAS_MAX",
    "SETTLE_SEARCH_CAP",
    "KEEP",
    "DROP",
    "UNSURE",
    "Verdict",
    "PositionBias",
    "AANoise",
    "wilson",
    "verdict",
    "tasks_to_settle",
    "position_bias",
    "aa_noise",
]
