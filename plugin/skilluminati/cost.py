"""In plain words: estimates how much prompt text (tokens) each installed skill adds, and roughly what
that costs. It runs when the agent calls the skilluminati_estimate tool, and when a test checks
whether a skill is heavy. It asks the model nothing.

Zero-history cost view: prompt tokens each installed skill adds, priced at list rate.

Everything here is an ESTIMATE. Stdlib only, read-only, no network, no credentials, no
imports from Hermes core; the Hermes rules we mirror are copied and cited.

Index-line format mirrored from hermes-agent ``agent/prompt_builder.py:1331``
(``_render_skills_index``)::

    index_lines.extend(f"{indent}- {name}: {desc}" if desc else f"{indent}- {name}" ...)

with ``indent`` = 4 spaces inside a multi-skill category (2 for a one-skill category), lines
joined by ``"\\n"``. The description is clipped to ``SKILL_PROMPT_DESC_LIMIT = 60`` chars at a
word boundary plus ``"..."`` (``agent/skill_utils.py:846`` and ``clip_skill_description`` at
``agent/skill_utils.py:857``). We price the 4-space form plus its newline; category header
lines (``"  <category>:"``) are shared and not attributed to any one skill.

Token rule copied from ``agent/model_metadata.py:2349`` (``estimate_tokens_rough``):
ASCII text = ceil(chars / 4) (``CHARS_PER_TOKEN = 4``, ``agent/model_metadata.py:2340``).
We apply ceil(UTF-8 bytes / 4) for all text, which equals chars/4 for ASCII. Real tokenizer
counts differ; label every number "estimate".
"""

from __future__ import annotations

import math
import os
import re
import statistics
from pathlib import Path
from typing import Iterable, Mapping

#: Heavy cost flag threshold. A skill is Heavy when its load tokens are at
#: least ``HEAVY_RATIO`` times the median load tokens of the comparison set. Load tokens =
#: ``estimate_tokens(full SKILL.md text)``, the text a ``skill_view`` returns each time the skill
#: loads (an estimate, same as the cost view). The comparison set is the user's enabled skills for
#: live runs and the 4 bundled planted skills for the self-test. The rule is relative on purpose so
#: it travels to any user's set: measured on a full install (median 2249,
#: p75 3837, p90 6371), 2x median ~ 4498 flags roughly the top fifth. It is a flag beside the
#: verdict, never a verdict: it does not change which of the four labels a skill gets.
HEAVY_RATIO = 2.0

LABEL = "estimate"
METHOD = "estimate (chars/4, list price)"
CHARS_PER_TOKEN = 4
DESC_LIMIT = 60  # agent/skill_utils.py:846 SKILL_PROMPT_DESC_LIMIT
INDEX_INDENT = "    "
SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))  # agent/skill_utils.py:36
EXCLUDED_DIRS = frozenset(("node_modules", "venv", "site-packages", "__pycache__"))  # agent/skill_utils.py:28

# SOURCE: hermes-agent agent/usage_pricing.py: list INPUT price, USD per million tokens.
#   claude-opus-5-5: line 165 ``_OPUS_5_5 = ("4.00", "20.00", "0.20", "5.00")`` (input 4.00),
#   mapped at line 197 under snapshot "anthropic-pricing-2026-09"
#   (https://platform.claude.com/docs/en/about-claude/pricing).
#   claude-haiku-5-5: NO entry in usage_pricing.py (checked 2026-11-07),
#   so it is deliberately absent here and price as "unknown" rather than a guessed number.
PRICE_PER_MTOK_INPUT: dict[str, float] = {
    "claude-opus-5-5": 4.00,
}
SOURCE = "hermes-agent agent/usage_pricing.py:165,197 (anthropic-pricing-2026-09)"


def estimate_tokens(text: str) -> int:
    """Estimate: ceil(UTF-8 bytes / 4); ASCII == ceil(chars / 4). Not a real tokenizer."""
    if not text:
        return 0
    return math.ceil(len(text.encode("utf-8", "replace")) / CHARS_PER_TOKEN)


def median_tokens(values: Iterable) -> float:
    """Median of the numeric values given; 0.0 for an empty set. Estimate, like every token number."""
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return float(statistics.median(vals)) if vals else 0.0


def heavy_text(over_tokens: int, over_pct: int) -> str:
    """The flag text, exactly: ``Heavy: +N tokens per load (X% over the median skill)``."""
    return f"Heavy: +{over_tokens:,} tokens per load ({over_pct}% over the median skill)"


def heavy_flag(load_tokens, comparison: Iterable) -> dict:
    """Heavy cost flag for one skill against its comparison set (``HEAVY_RATIO`` x median rule).

    ``load_tokens`` is this skill's estimated load tokens; ``comparison`` is the load tokens of every
    skill in the set (this skill included). Returns ``load_tokens``, ``median_tokens``, ``ratio``
    (None when the median is 0), ``over_tokens`` (load minus median, rounded), ``over_pct``
    (``round(100 * over / median)``), ``flag`` (bool) and ``text`` (the flag text, or None when not
    Heavy). An empty set or a median of 0 is never Heavy. Pure; no I/O.
    """
    try:
        load = float(load_tokens)
    except (TypeError, ValueError):
        load = 0.0
    median = median_tokens(comparison)
    over = load - median
    out = {"load_tokens": int(load) if load == int(load) else load, "median_tokens": median, "ratio": None,
           "over_tokens": int(round(over)), "over_pct": None, "flag": False, "text": None}
    if median <= 0:
        return out
    out["ratio"] = round(load / median, 4)
    out["over_pct"] = int(round(100 * over / median))
    out["flag"] = load >= HEAVY_RATIO * median
    if out["flag"]:
        out["text"] = heavy_text(out["over_tokens"], out["over_pct"])
    return out


def heavy_flags(named_load_tokens: Mapping) -> dict:
    """``{name: heavy_flag(...)}`` for a comparison set given as ``{name: load_tokens}``."""
    values = list(named_load_tokens.values())
    return {name: heavy_flag(v, values) for name, v in named_load_tokens.items()}


def price_per_mtok(model: str | None) -> float | None:
    """List input price (USD / 1M tokens) or None when unknown."""
    if not model:
        return None
    key = str(model).strip().lower()
    key = re.sub(r"\[.*\]$", "", key)  # "claude-opus-5-5[1m]" -> "claude-opus-5-5"
    key = key.split("/")[-1]  # "anthropic/claude-opus-5-5"
    return PRICE_PER_MTOK_INPUT.get(key)


# ---------------------------------------------------------------- frontmatter

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace("''", "'") if v[0] == "'" else inner.replace('\\"', '"')
    return v


def parse_frontmatter(text: str) -> dict:
    """Tiny parser: top-level ``key: value`` scalars, quoted values, ``>`` / ``|`` blocks."""
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)
    else:
        return {}
    out: dict = {}
    i = 0
    while i < len(body):
        line = body[i]
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        i += 1
        if not m:
            continue
        key, val = m.group(1), m.group(2).rstrip()
        if re.match(r"^[>|][+-]?\d*$", val):
            block = []
            while i < len(body) and (not body[i].strip() or body[i][:1] in " \t"):
                block.append(body[i].strip())
                i += 1
            while block and not block[-1]:
                block.pop()
            if val.startswith(">"):
                paras, cur = [], []
                for b in block:
                    if b:
                        cur.append(b)
                    else:
                        paras.append(" ".join(cur))
                        cur = []
                paras.append(" ".join(cur))
                out[key] = "\n".join(paras).strip()
            else:
                out[key] = "\n".join(block).strip()
            continue
        # plain multi-line scalar continuation (indented lines)
        if val and val[0] not in "'\"[{":
            while i < len(body) and body[i][:1] in " \t" and body[i].strip() and not re.match(r"^\s*-\s", body[i]):
                val += " " + body[i].strip()
                i += 1
        out[key] = _unquote(val)
    return out


def clip_description(desc: str) -> str:
    """Mirror of agent/skill_utils.py:857 clip_skill_description (60-char word-boundary clip)."""
    desc = (desc or "").strip().strip("'\"")
    if len(desc) <= DESC_LIMIT:
        return desc
    head = desc[:DESC_LIMIT - 2]
    m = re.search(r"\s\S*$", head)
    if not m or m.start() == 0:
        return desc[:DESC_LIMIT - 3] + "..."
    return re.sub(r"[\s.,;:\-\u2013\u2014(/]+$", "", head[:m.start()]) + "..."


def index_line(name: str, description: str) -> str:
    desc = clip_description(description)
    return f"{INDEX_INDENT}- {name}: {desc}" if desc else f"{INDEX_INDENT}- {name}"


# ---------------------------------------------------------------- scanning

def hermes_home() -> Path:
    """Hermes's home folder for the active profile, from Hermes itself (get_hermes_home).
    Outside Hermes (the test suite) it falls back to $HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def default_skills_dir() -> Path:
    return hermes_home() / "skills"


def scan_skills(skills_dir=None, disabled=()) -> list[dict]:
    """Every SKILL.md under *skills_dir* (dot-dirs skipped), keyed by frontmatter ``name``."""
    root = Path(skills_dir) if skills_dir else default_skills_dir()
    disabled = {str(d).strip() for d in (disabled or ()) if str(d).strip()}
    found: list[dict] = []
    seen: set[str] = set()
    if not root.is_dir():
        return found
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        has_skill = "SKILL.md" in files
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in EXCLUDED_DIRS
                         and not (has_skill and d in SUPPORT_DIRS))
        if not has_skill:
            continue
        path = Path(dirpath) / "SKILL.md"
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        name = str(fm.get("name") or "").strip() or Path(dirpath).name
        if name in disabled or name in seen:
            continue
        seen.add(name)
        found.append({"name": name, "description": str(fm.get("description") or ""),
                      "path": str(path), "text": text})
    return found


def read_disabled(config_path=None) -> tuple[list[str], str | None]:
    """``skills.disabled`` from config.yaml via a minimal line parser. Read-only.

    Returns (names, note). On any doubt returns ([], note-explaining-why).
    """
    path = Path(config_path) if config_path else default_skills_dir().parent / "config.yaml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], f"config not read ({type(exc).__name__}); no skills treated as disabled"
    try:
        in_skills = False
        names: list[str] = []
        for idx, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith((" ", "\t")):
                in_skills = line.rstrip() == "skills:"
                continue
            if not in_skills:
                continue
            m = re.match(r"^( +)disabled:\s*(.*)$", line)
            if not m:
                continue
            indent, rest = len(m.group(1)), m.group(2).strip()
            if rest.startswith("[") and rest.endswith("]"):
                return [_unquote(x) for x in rest[1:-1].split(",") if x.strip()], None
            if rest:
                return [], "skills.disabled has an unexpected shape; no skills treated as disabled"
            for nxt in lines[idx + 1:]:
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    continue
                mm = re.match(r"^( *)- +(.+?)\s*$", nxt)
                if mm and len(mm.group(1)) >= indent:
                    names.append(_unquote(mm.group(2).split(" #")[0]))
                    continue
                break
            return names, None
        return [], None
    except Exception as exc:  # parse doubt: report, never guess
        return [], f"skills.disabled not parsed ({type(exc).__name__}); none treated as disabled"


# ---------------------------------------------------------------- estimate

def _usd(tokens: int, price: float | None) -> float | None:
    return None if price is None else round(tokens * price / 1_000_000, 8)


def estimate(skills_dir=None, model=None, disabled=None, top=None) -> dict:
    notes: list[str] = []
    if disabled is None:
        disabled, note = read_disabled()
        if note:
            notes.append(note)
    price = price_per_mtok(model)
    if price is None:
        notes.append(f"no list price for model {model!r}; cost is 'unknown'")
    rows = []
    all_names = {s["name"] for s in scan_skills(skills_dir, ())}
    excluded = len(all_names & {str(d).strip() for d in (disabled or ())})
    for s in scan_skills(skills_dir, disabled):
        it = estimate_tokens(index_line(s["name"], s["description"]) + "\n")
        lt = estimate_tokens(s["text"])
        rows.append({"name": s["name"], "index_tokens": it, "load_tokens": lt,
                     "index_cost_per_turn": _usd(it, price), "load_cost": _usd(lt, price)})
    # Heavy flag: the comparison set is every enabled skill scanned above, not the `top` slice.
    flags = heavy_flags({r["name"]: r["load_tokens"] for r in rows})
    for r in rows:
        r["heavy"] = flags[r["name"]]
    rows.sort(key=lambda r: (-r["index_tokens"], -r["load_tokens"], r["name"]))
    total_index = sum(r["index_tokens"] for r in rows)
    per_turn = _usd(total_index, price)
    totals = {
        "skill_count": len(rows),
        "disabled_count": excluded,  # installed skills excluded because they are disabled
        "index_tokens_per_turn": total_index,
        "load_tokens_all": sum(r["load_tokens"] for r in rows),
        "median_load_tokens": median_tokens(r["load_tokens"] for r in rows),
        "heavy_count": sum(1 for r in rows if r["heavy"]["flag"]),
        "heavy_ratio": HEAVY_RATIO,
        "index_cost_per_turn": per_turn,
        "index_cost_per_100_turns": None if per_turn is None else round(per_turn * 100, 6),
    }
    try:
        top_n = int(top) if top not in (None, "") else None
    except (TypeError, ValueError):
        top_n = None
        notes.append(f"ignored non-integer top={top!r}")
    return {
        "label": LABEL,
        "method": METHOD,
        "model": model,
        "price_per_mtok_input": price,
        "price_source": SOURCE if price is not None else None,
        "cost": "estimate" if price is not None else "unknown",
        "totals": totals,
        "skills": rows[:top_n] if top_n and top_n > 0 else rows,
        "notes": notes,
    }


def _fmt_usd(v) -> str:
    return "unknown" if v is None else f"${v:.6f}"


def format_table(result: dict, limit: int = 15) -> str:
    model = result.get("model") or "no model given"
    t = result.get("totals", {})
    rows = sorted(result.get("skills", []), key=lambda r: (-r["index_tokens"], r["name"]))[:limit]
    w = max([len("skill")] + [len(r["name"]) for r in rows])
    out = [f"skilluminati cost view: model {model}, {METHOD}",
           f"{'skill'.ljust(w)}  {'index tok/turn':>14}  {'load tok':>8}  {'index $/turn':>12}  {'load $':>10}  Heavy"]
    for r in rows:
        heavy = (r.get("heavy") or {}).get("text") or ""
        out.append(f"{r['name'].ljust(w)}  {r['index_tokens']:>14}  {r['load_tokens']:>8}  "
                   f"{_fmt_usd(r['index_cost_per_turn']):>12}  {_fmt_usd(r['load_cost']):>10}  {heavy}".rstrip())
    shown = len(rows)
    out.append(f"{shown} of {t.get('skill_count', shown)} enabled skills shown "
               f"({t.get('disabled_count', 0)} disabled excluded).")
    out.append(f"All skills: {t.get('index_tokens_per_turn', 0)} index tokens every turn, "
               f"{_fmt_usd(t.get('index_cost_per_turn'))}/turn, "
               f"{_fmt_usd(t.get('index_cost_per_100_turns'))}/100 turns (estimate; before prompt caching).")
    if t.get("median_load_tokens") is not None:
        out.append(f"Heavy flag: a skill is Heavy when its load tokens are at least {HEAVY_RATIO:g}x the median "
                   f"of the enabled skills ({t['median_load_tokens']:,.0f} tokens, estimate); "
                   f"{t.get('heavy_count', 0)} Heavy. It sits beside the verdict and never changes it.")
    if result.get("price_source"):
        out.append(f"Price: ${result['price_per_mtok_input']}/M input tokens, {result['price_source']}.")
    for n in result.get("notes", []):
        out.append(f"Note: {n}")
    return "\n".join(out)
