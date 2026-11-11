"""In plain words: blanks out keys, emails, phone numbers and other private details in your past
chats before any of it is sent to a model. It runs on every task, before the first model call.

Redactor for mined task text. Pure Python, stdlib only.

``redact(text) -> (redacted_text, counts)``. Removes, in this order:

* ``[KEY]``    API-key shapes: ``sk-...``, ``ghp_``/``gho_``/``github_pat_``, ``xox[abprs]-``,
               ``AKIA...``, bearer tokens, and bare hex/base64 blobs of 32+ characters.
* ``[URL]``    URLs with a query string or fragment (they can carry tokens). Plain
               ``scheme://host/path`` URLs are kept.
* ``[EMAIL]``  email addresses.
* ``[PHONE]``  AU (``04xx xxx xxx``, ``(02) xxxx xxxx``, ``+61 ...``, ``1300``/``1800``) and
               international (``+cc ...``) shapes.
* ``[MONEY]``  ``$1,234.56``, ``A$5k``, ``AUD 2,000``, ``USD 1.5m``, ``2,000 AUD``.
* ``[NAME]``   names from the optional deny-list (``$HERMES_HOME/plugin-data/skilluminati/names.txt``;
               one per line, case-insensitive; when it is absent no listed names are removed and
               the run plan notes this) plus capitalised names in greetings
               (``Hi X``, ``Dear X``) and sign-offs (``Thanks, X``, ``Regards, X``).

Order matters: URLs first (a URL with a query string becomes one ``[URL]``, swallowing any
token inside it, so it is not double counted as ``[KEY]``), then keys, emails, phones, money,
and names last (``$5k`` never looks like a name).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

PLACEHOLDERS = ("KEY", "URL", "EMAIL", "PHONE", "MONEY", "NAME")

def hermes_home() -> Path:
    """Hermes's home folder for the active profile, from Hermes itself (get_hermes_home).
    Outside Hermes (the test suite) it falls back to $HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def names_file_path() -> Path:
    """Deny-list location, resolved at call time and never created:
    ``$HERMES_HOME/plugin-data/skilluminati/names.txt``. Absent means no names are removed; the run
    plan notes this. Nothing is read from outside the plugin-data directory."""
    return hermes_home() / "plugin-data" / "skilluminati" / "names.txt"


# ── patterns ─────────────────────────────────────────────────────────────────

KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-(?:[A-Za-z0-9_-]+-)?[A-Za-z0-9_-]{16,}"          # OpenAI / Anthropic style
    r"|gh[pousr]_[A-Za-z0-9]{20,}"                         # GitHub classic tokens
    r"|github_pat_[A-Za-z0-9_]{20,}"                       # GitHub fine-grained
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"                       # Slack
    r"|AKIA[A-Z0-9]{16}"                                   # AWS access key id
    r"|AIza[A-Za-z0-9_-]{30,}"                             # Google API key
    r"|(?:Bearer|Token)\s+[A-Za-z0-9_.=-]{20,}"            # auth header value
    r"|(?=[0-9]*[A-Fa-f])[A-Fa-f0-9]{32,}"                 # long hex (needs a letter; not a digit run)
    # base64-ish blob: 32+ chars with a digit, an upper and a lower, not inside a path.
    r"|(?<![/.\-])(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{32,}={0,2}"
    r")(?![A-Za-z0-9_])"
)

# A URL. Group ``query`` is non-empty when it carries ``?`` or ``#``.
URL_RE = re.compile(
    r"(?P<scheme>https?|ftp|wss?)://(?P<hostpath>[^\s<>\"'()\[\]?#]+)(?P<query>[?#][^\s<>\"'()\[\]]*)?",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"\+?61[ \-.]?\(?0?\)?[ \-.]?[2-478](?:[ \-.]?\d){8}"        # +61 4xx xxx xxx / +61 2 xxxx xxxx
    r"|\(0[2-478]\)[ \-.]?\d{4}[ \-.]?\d{4}"                       # (02) 9xxx xxxx
    r"|0[2-478](?:[ \-.]?\d){8}"                                    # 04xx xxx xxx / 02 xxxx xxxx
    r"|1[38]00[ \-.]?\d{3}[ \-.]?\d{3}"                             # 1300 / 1800
    r"|13[ \-.]?\d{2}[ \-.]?\d{2}"                                  # 13 xx xx
    r"|\+(?!61)\d{1,3}[ \-.]?\(?\d{1,4}\)?(?:[ \-.]?\d{2,4}){2,4}"   # international
    r")(?![A-Za-z0-9])"
)

_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
MONEY_RE = re.compile(
    r"(?:"
    r"(?:A|AU|US|NZ|S|HK|C)?\$\s?(?:" + _AMOUNT + r")(?:\s?[kKmMbB](?![A-Za-z]))?"      # $5k, A$1,200.50
    r"|(?<![A-Za-z])(?:AUD|USD|NZD|GBP|EUR|SGD)\s?(?:" + _AMOUNT + r")(?:\s?[kKmMbB](?![A-Za-z]))?"  # AUD 2,000
    r"|(?<![A-Za-z0-9.])(?:" + _AMOUNT + r")\s?(?:[kKmM]\s?)?(?:AUD|USD|NZD|GBP|EUR|SGD)(?![A-Za-z])"  # 2,000 AUD
    r")"
)

_CAP = r"[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?"
_GREETING_WORDS = r"(?:Hi|Hello|Hey|Dear|Morning|G'day|Thanks|Thank you|Cheers|Regards|Kind regards|Best regards|Best|Warm regards|Yours|Sincerely)"
GREETING_NAME_RE = re.compile(
    r"(?<![A-Za-z])" + _GREETING_WORDS + r",?\s+(?P<name>" + _CAP + r"(?:\s" + _CAP + r")?)(?=[\s,.!:;\-]|$)"
)

# Deny-list words we must never treat as a name even if greeted ("Hi there", "Thanks All").
_NOT_NAMES = frozenset({
    "There", "All", "Team", "Everyone", "Again", "Both", "Folks", "Guys", "Mate",
    "You", "So", "Much", "Very", "For", "Also", "And", "But", "The", "This", "That",
    "Today", "Tomorrow", "Yesterday", "Morning", "Afternoon", "Evening", "Night",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "Everybody", "Anyone", "Nobody",
    "Hermes", "Claude",
    "Sir", "Madam", "Sirs", "Customer", "Support", "Friend", "Friends", "Colleague", "Colleagues",
    "Hiring", "Sales", "Billing", "Accounts",
})


# ── deny-list ────────────────────────────────────────────────────────────────

def load_names(path: str | os.PathLike[str] | None = None) -> list[str]:
    """Names from the deny-list file, longest first, blank lines and ``#`` comments ignored.
    An absent file yields an empty list."""
    p = Path(path) if path is not None else names_file_path()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names = {ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}
    return sorted(names, key=len, reverse=True)


def _names_pattern(names: Iterable[str]) -> Optional[re.Pattern[str]]:
    names = sorted({n for n in names if n}, key=len, reverse=True)  # longest first
    if not names:
        return None
    alts = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in names)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + alts + r")(?![A-Za-z0-9])", re.IGNORECASE)


# ── redaction ────────────────────────────────────────────────────────────────

def _sub(pattern: re.Pattern[str], repl, text: str, counts: dict[str, int], key: str) -> str:
    out, n = pattern.subn(repl, text)
    counts[key] += n
    return out


class _Counted:
    """Replacement callable that counts only the matches it actually replaced."""

    def __init__(self, fn):
        self.fn = fn
        self.n = 0

    def __call__(self, m: re.Match[str]) -> str:
        out = self.fn(m)
        if out != m.group(0):
            self.n += 1
        return out


def _url_repl(m: re.Match[str]) -> str:
    return "[URL]" if m.group("query") else m.group(0)


def _greeting_repl(m: re.Match[str]) -> str:
    name = m.group("name")
    first = name.split()[0]
    if first in _NOT_NAMES or name in _NOT_NAMES:
        return m.group(0)
    return m.group(0)[: m.start("name") - m.start(0)] + "[NAME]"


def redact(
    text: Optional[str],
    names: Optional[Iterable[str]] = None,
    names_file: str | os.PathLike[str] | None = None,
) -> tuple[Optional[str], dict[str, int]]:
    """``(redacted, counts)``. ``None`` passes through unchanged with zero counts.

    ``names`` overrides the deny-list file; otherwise ``names_file`` (default: see
    :func:`names_file_path`) is read on each call. Counts are per placeholder type.
    """
    counts = {k: 0 for k in PLACEHOLDERS}
    if text is None:
        return None, counts
    out = text
    url_repl = _Counted(_url_repl)
    out = URL_RE.sub(url_repl, out)
    counts["URL"] += url_repl.n
    out = _sub(KEY_RE, "[KEY]", out, counts, "KEY")
    out = _sub(EMAIL_RE, "[EMAIL]", out, counts, "EMAIL")
    out = _sub(PHONE_RE, "[PHONE]", out, counts, "PHONE")
    out = _sub(MONEY_RE, "[MONEY]", out, counts, "MONEY")
    deny = list(names) if names is not None else load_names(names_file)
    pat = _names_pattern(deny)
    if pat is not None:
        out = _sub(pat, "[NAME]", out, counts, "NAME")
    greet_repl = _Counted(_greeting_repl)
    out = GREETING_NAME_RE.sub(greet_repl, out)
    counts["NAME"] += greet_repl.n
    return out, counts


def merge_counts(*counts: dict[str, int]) -> dict[str, int]:
    total = {k: 0 for k in PLACEHOLDERS}
    for c in counts:
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    return total
