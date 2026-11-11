"""In plain words: every question Skilluminati asks the model goes through here. It counts the
questions, stops at the limit, and pauses between them so your own chats are never kept waiting.
It runs only while a test is going, never at Hermes start-up.

Thin wrapper over the host-owned plugin LLM facade (``ctx.llm`` / ``agent.plugin_llm.PluginLlm``),
plus the one guard every user command runs its calls through (``CommandGuard``).

The plugin never holds credentials. With no provider and no model given, a call runs on the user's
active provider and model (no trust opt-in needed). A provider or model override is passed to the
host, whose trust gate (``plugins.entries.skilluminati.llm``) decides; a denied override raises
``PluginLlmTrustError`` from the host and is recorded, never routed round.

Stops: every condition that must end a run is a ``RunStop``. ``complete`` re-raises a ``RunStop``
instead of turning it into a failed record, so a stop always reaches the run loop.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

#: Provider error text that means "usage or rate limit": the run stops, the provider is never switched.
USAGE_LIMIT_RE = re.compile(
    r"rate.?limit|usage.?limit|too many requests|\b429\b|\b529\b|quota|overloaded|insufficient.?(credit|quota)|"
    r"limit (reached|exceeded)|try again later",
    re.IGNORECASE,
)


class RunStop(RuntimeError):
    """Base of every condition that ends a run. Never swallowed by ``complete``."""


class CapExceeded(RunStop):
    """The hard call cap would be exceeded."""


class UsageLimit(RunStop):
    """The provider reported a usage or rate limit; the run must stop."""


class ModelMismatch(RunStop):
    """A call was answered by a different model than the one planned (host fallback)."""


class StopRequested(RunStop):
    """An earlier call stopped the run; pending jobs halt without calling."""


class RunawayAnswer(RunStop):
    """A subject answer ignored its output cap. The run stops; the answer is never judged."""


#: The output cap is passed down but NOT honoured below the plugin on every route (Claude Agent SDK route:
#: 128,000 output tokens returned for a 600-token cap). So the plugin never trusts it: a subject answer
#: whose visible text is over ``RUNAWAY_MAX_CHARS`` characters is a failed call. The token count is not
#: used for this: Opus counts its hidden thinking as output tokens, so a normal answer can look long.
#: Only a token count over ``RUNAWAY_TOKEN_FACTOR`` x the cap (far past any thinking) also counts.
RUNAWAY_TOKEN_FACTOR = 8
RUNAWAY_MAX_CHARS = 20_000
EMPTY_ANSWER = "EmptyAnswer"
RUNAWAY = "RunawayAnswer"


def answer_problem(rec: "CallRecord", max_tokens: Optional[int],
                   max_chars: int = RUNAWAY_MAX_CHARS) -> Optional[str]:
    """``RUNAWAY`` / ``EMPTY_ANSWER`` for a bad subject answer, else None. ``ctx.llm.complete`` takes no
    stop sequence, so a fake tool call cannot be cut short; it can only be caught here."""
    text = rec.text or ""
    if (max_tokens and rec.output_tokens > RUNAWAY_TOKEN_FACTOR * max_tokens) or len(text) > max_chars:
        return RUNAWAY
    if not text.strip():
        return EMPTY_ANSWER
    return None


def check_subject(rec: "CallRecord", max_tokens: Optional[int], where: str,
                  max_chars: int = RUNAWAY_MAX_CHARS) -> "CallRecord":
    """Apply ``answer_problem`` to an ok subject record, in place. An empty answer becomes a failed call
    (``ok`` False). A runaway also becomes a failed call and its text is cut to 2,000 characters, so it
    can never reach a judge; the caller logs it and then raises ``runaway_stop``."""
    if not rec.ok:
        return rec
    problem = answer_problem(rec, max_tokens, max_chars)
    if problem is None:
        return rec
    rec.ok = False
    rec.error_type = problem
    rec.extra["chars"] = len(rec.text or "")
    if problem == RUNAWAY:
        rec.error = (f"{where}: the answer ran away ({rec.output_tokens:,} output tokens, "
                     f"{len(rec.text or ''):,} characters, {rec.latency_s:,.0f} s; cap {max_tokens} tokens)")
        rec.text = (rec.text or "")[:2000]
    else:
        rec.error = f"{where}: the model returned an empty answer ({rec.output_tokens} output tokens)"
    return rec


def runaway_stop(rec: "CallRecord") -> RunawayAnswer:
    """The plain stop message for a runaway record (``check_subject`` already marked it)."""
    return RunawayAnswer(f"{rec.error}. Stopped the whole run; that answer was not sent to the judges.")


@dataclass
class CallRecord:
    """One completed (or failed) call, in the shape the reports use."""

    ok: bool
    provider: str = ""
    model: str = ""
    requested_provider: str = ""
    requested_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    latency_s: float = 0.0
    text: str = ""
    error: str = ""
    error_type: str = ""
    purpose: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def complete(
    llm: Any, messages: list[dict[str, Any]], *, provider: Optional[str] = None, model: Optional[str] = None,
    max_tokens: Optional[int] = None, temperature: Optional[float] = None,
    timeout: Optional[float] = None, purpose: str = "",
) -> CallRecord:
    """Run ``llm.complete`` and time it. ``provider``/``model`` None means the active provider/model.

    ``llm`` is ``ctx.llm`` (usually wrapped in a ``CommandGuard``) or a ``PluginLlm`` bound to the
    plugin id in a harness. Provider errors are captured verbatim into the record; a ``RunStop`` is
    re-raised. When a model was pinned and another one answered, the record is marked failed
    (``ModelMismatch``); a ``CommandGuard`` turns that into a stop before it gets here.
    """
    started = time.monotonic()
    try:
        result = llm.complete(
            messages, provider=provider, model=model, max_tokens=max_tokens,
            temperature=temperature, timeout=timeout, purpose=purpose or None,
        )
    except RunStop:
        raise
    except Exception as exc:
        return CallRecord(
            ok=False, requested_provider=provider or "", requested_model=model or "",
            latency_s=round(time.monotonic() - started, 3), purpose=purpose,
            error=str(exc), error_type=type(exc).__name__,
        )
    usage = getattr(result, "usage", None)
    returned_model = str(getattr(result, "model", "") or "")
    record = CallRecord(
        ok=True, provider=str(getattr(result, "provider", "") or ""), model=returned_model,
        requested_provider=provider or "", requested_model=model or "",
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0) or 0),
        latency_s=round(time.monotonic() - started, 3), text=getattr(result, "text", "") or "",
        purpose=purpose,
    )
    if model and not same_model(model, returned_model):
        # The host's auxiliary client falls back to other routes (configured chains, then the main
        # agent model) on timeouts and capacity errors, and the result's provider field still
        # carries the requested override. A pinned call that ran elsewhere is not a result.
        record.ok = False
        record.error_type = "ModelMismatch"
        record.error = (
            f"requested {provider or 'active provider'}/{model} but the host routed the call to "
            f"{returned_model!r}: auxiliary fallback engaged (timeout or capacity error on the pinned route)"
        )
        record.extra["host_fallback"] = True
    return record


def same_model(requested: str, returned: str) -> bool:
    """True when the model that answered is the one pinned. Tolerates the host's context-size
    suffix (``claude-opus-5-5[1m]``) and an ``openrouter/`` style prefix on either side."""
    def norm(name: str) -> str:
        name = (name or "").strip().lower()
        if "[" in name:
            name = name.split("[", 1)[0]
        return name.rsplit("/", 1)[-1] if name.count("/") >= 2 else name
    r, g = norm(requested), norm(returned)
    return bool(r) and bool(g) and (r == g or r.rsplit("/", 1)[-1] == g.rsplit("/", 1)[-1])


class CommandGuard:
    """One budget, one stop flag and one model check for a whole user command.

    Wraps ``ctx.llm``; every module's calls go through ``complete`` here. Before a call: a set stop
    flag raises ``StopRequested`` (pending jobs halt without calling) and the call count is checked
    against ``cap`` (``CapExceeded``). After a call: a usage-limit error raises ``UsageLimit``; a
    reply from another model than planned raises ``ModelMismatch``. For calls with no pinned model,
    the first reply fixes the active model and every later unpinned reply must match it. Each stop
    sets the flag first, so the whole command halts.
    """

    def __init__(self, llm: Any, cap: int) -> None:
        self.llm = llm
        self.cap = int(cap)
        self.pause = 0.0  # seconds to wait after each call, so the user's own chats get a turn
        self.status = ""  # one plain line about the plan (uses found, tests, minutes) once it is known
        self.used = 0
        self.now = ""  # the call going now (its purpose label), for the live popup
        self.now_since = 0.0  # when that call started (time.monotonic)
        self.stop = threading.Event()
        self.reason: Optional[str] = None
        self.active_model: Optional[str] = None
        self.models_seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def halt(self, exc: RunStop) -> RunStop:
        with self._lock:
            if not self.stop.is_set():
                self.reason = f"{type(exc).__name__}: {exc}"
                self.stop.set()
        return exc

    def complete(self, messages, *, provider=None, model=None, **kw):
        with self._lock:
            if self.stop.is_set():
                raise StopRequested(self.reason or "run stopped")
            if self.used >= self.cap:
                exc = CapExceeded(f"hard cap {self.cap} reached after {self.used} calls")
                self.reason = f"CapExceeded: {exc}"
                self.stop.set()
                raise exc
            self.used += 1
            self.now, self.now_since = str(kw.get("purpose") or ""), time.monotonic()
        try:
            result = self.llm.complete(messages, provider=provider, model=model, **kw)
        except RunStop:
            raise
        except Exception as exc:
            if USAGE_LIMIT_RE.search(str(exc)):
                raise self.halt(UsageLimit(str(exc))) from exc
            raise
        finally:
            if self.pause:
                time.sleep(self.pause)
        returned = str(getattr(result, "model", "") or "")
        with self._lock:
            self.models_seen[returned] = self.models_seen.get(returned, 0) + 1
        if model:
            if not same_model(model, returned):
                raise self.halt(ModelMismatch(f"planned {model!r}, answered by {returned!r}"))
        else:
            with self._lock:
                if self.active_model is None and returned:
                    self.active_model = returned
                expected = self.active_model
            if expected and not same_model(expected, returned):
                raise self.halt(ModelMismatch(f"active model was {expected!r}, then {returned!r} answered"))
        return result
