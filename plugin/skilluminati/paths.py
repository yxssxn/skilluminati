"""In plain words: every folder and file Skilluminati reads from or writes to, listed in one place.
Other files ask here; nothing in this file runs on its own.

Every on-disk location the plugin uses, in one place.

Read-only inputs come from the Hermes home (``state.db``, ``skills/``) and from inside the plugin
folder (the bundled self-test fixtures). Everything the plugin writes goes under its own
plugin-data directory, ``<HERMES_HOME>/plugin-data/skilluminati/``, found through the host's
documented ``plugins.plugin_storage.plugin_data_dir`` (the "Store durable state" section of the
plugin guide). The slash command, the agent tools and the dashboard backend all use that one call,
so the page reads exactly where the runs write.
Nothing resolves above the plugin folder (no ``parents[2]``), so an installed copy never reads or writes beside itself.

Older builds wrote to the host's ``ctx.state.data_dir`` instead (a hashed folder name the dashboard
backend has no public way to find). ``bind`` copies that folder's contents across once, and never
deletes the original.

Standard library only and no package imports, so the dashboard backend can load it by path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

PLUGIN_ID = "skilluminati"
PLUGIN_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = PLUGIN_DIR / "fixtures"

#: Kept for tests that pin a folder; the plugin itself never sets it.
_data_dir_override: Optional[Path] = None
MIGRATED_MARKER = ".migrated-from"


def hermes_home() -> Path:
    """Hermes's home folder for the active profile, from Hermes itself (get_hermes_home).
    Outside Hermes (the test suite) it falls back to $HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def bind(ctx: Any) -> None:
    """Copy data an older build left in the host's ``ctx.state.data_dir`` into ``data_dir()``, once.
    Never deletes anything; any error leaves both folders as they were."""
    try:
        old = getattr(getattr(ctx, "state", None), "data_dir", None)
        if old:
            migrate_from(Path(old))
    except Exception:
        pass


def migrate_from(old: Path) -> bool:
    """Copy ``old``'s files into ``data_dir()`` when they differ and the copy has not run yet.
    Existing files in the new folder are kept (never overwritten). True when a copy ran."""
    import shutil
    new = data_dir()
    try:
        if not old.is_dir() or old.resolve() == new.resolve():
            return False
        new.mkdir(parents=True, exist_ok=True)
        marker = new / MIGRATED_MARKER
        if marker.is_file():
            return False
        for src in old.rglob("*"):
            dst = new / src.relative_to(old)
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        marker.write_text(str(old) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """``<HERMES_HOME>/plugin-data/skilluminati``, from the host's public ``plugin_data_dir``
    (it follows the active profile and creates the folder). Same path by hand when that module is
    absent (an older Hermes, a bare test run)."""
    if _data_dir_override is not None:
        return _data_dir_override
    try:
        from plugins.plugin_storage import plugin_data_dir  # public plugin API (plugin guide)
        return Path(plugin_data_dir(PLUGIN_ID))
    except Exception:
        return hermes_home() / "plugin-data" / PLUGIN_ID


def verdicts_path() -> Path:
    return data_dir() / "verdicts.json"


def names_path() -> Path:
    """The optional name deny-list. Absent means no names are removed (the run plan notes this)."""
    return data_dir() / "names.txt"


def runs_dir() -> Path:
    return data_dir() / "runs"


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, limit: int = 64) -> str:
    """A file-name-safe slug: no separators, no dots at the start, never empty."""
    s = _SAFE.sub("-", str(name or "")).strip(".-")[:limit]
    return s or "unnamed"


def new_run_dir(label: str, stamp: str) -> Path:
    """A fresh private directory for one run (``runs/<stamp>-<label>``), mode 0700."""
    path = runs_dir() / f"{safe_name(stamp)}-{safe_name(label)}"
    n = 1
    base = path
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    path.mkdir(parents=True, mode=0o700)
    return path


def fresh_private_file(path: Path) -> Path:
    """Start ``path`` empty at 0600, keeping any earlier content as ``<name>.prev`` (one rotation)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size:
        os.replace(path, path.with_name(path.name + ".prev"))
    path.write_text("", encoding="utf-8")
    return private_file(path)


def private_file(path: Path) -> Path:
    """Create ``path`` empty if missing and set it to 0600. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
