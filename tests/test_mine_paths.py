"""Path-resolution tests for skilluminati.mine and skilluminati.redact under a temp HERMES_HOME.

Run: python -m pytest tests/test_mine_paths.py -v

Nothing here touches the real ~/.hermes, the real state.db or the real names file: every test
points HERMES_HOME at a fresh temp dir and uses invented names.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MINE_PATH = REPO / "plugin" / "skilluminati" / "mine.py"
REDACT_PATH = REPO / "plugin" / "skilluminati" / "redact.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _HermesHomeCase(unittest.TestCase):
    """Each test gets a fresh temp HERMES_HOME; the real value is restored afterwards."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "hermes-home"
        self.home.mkdir()
        self._saved = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._saved
        self._tmp.cleanup()


class MinePathTests(_HermesHomeCase):
    def test_default_db_follows_hermes_home_at_import(self):
        mine = _load("skilluminati_mine_paths_a", MINE_PATH)
        self.assertEqual(mine.DEFAULT_DB, str(self.home / "state.db"))
        self.assertEqual(mine.DEFAULT_SKILLS_DIR, self.home / "skills")
        self.assertFalse((self.home / "state.db").exists(), "resolution must not create the db")

    def test_functions_resolve_at_call_time(self):
        mine = _load("skilluminati_mine_paths_b", MINE_PATH)
        other = Path(self._tmp.name) / "other-home"
        os.environ["HERMES_HOME"] = str(other)
        self.assertEqual(mine.hermes_home(), other)
        self.assertEqual(mine.default_db_path(), other / "state.db")
        self.assertEqual(mine.default_skills_dir(), other / "skills")
        # The import-time constant is a snapshot and does not move.
        self.assertEqual(mine.DEFAULT_DB, str(self.home / "state.db"))

    def test_unset_and_empty_fall_back_to_home_dot_hermes(self):
        mine = _load("skilluminati_mine_paths_c", MINE_PATH)
        os.environ.pop("HERMES_HOME", None)
        self.assertEqual(mine.default_db_path(), Path.home() / ".hermes" / "state.db")
        os.environ["HERMES_HOME"] = "   "
        self.assertEqual(mine.default_db_path(), Path.home() / ".hermes" / "state.db")

    def test_connect_default_is_read_only_uri(self):
        """Even against a db that exists, the default path is opened mode=ro (a write fails)."""
        import sqlite3

        db = self.home / "state.db"
        seed = sqlite3.connect(db)
        seed.execute("CREATE TABLE sessions (id TEXT, source TEXT, system_prompt TEXT, system_prompt_hash TEXT, started_at REAL)")
        seed.commit()
        seed.close()
        mine = _load("skilluminati_mine_paths_d", MINE_PATH)
        conn = mine.connect()  # no argument: uses DEFAULT_DB under the temp HERMES_HOME
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE probe (x)")
        finally:
            conn.close()


class RedactPathTests(_HermesHomeCase):
    def test_only_plugin_data_never_the_repo(self):
        redact = _load("skilluminati_redact_paths_a", REDACT_PATH)
        f = self.home / "plugin-data" / "skilluminati" / "names.txt"
        self.assertEqual(redact.names_file_path(), f)
        self.assertEqual(redact.load_names(), [])
        self.assertFalse(hasattr(redact, "REPO_NAMES_FILE"))
        self.assertFalse((self.home / "plugin-data").exists(), "resolution must not create dirs")

    def test_plugin_data_file_wins_when_present(self):
        redact = _load("skilluminati_redact_paths_b", REDACT_PATH)
        f = self.home / "plugin-data" / "skilluminati" / "names.txt"
        f.parent.mkdir(parents=True)
        f.write_text("Avery Quill\nOndine Pty Ltd\n", encoding="utf-8")
        self.assertEqual(redact.names_file_path(), f)
        self.assertEqual(redact.load_names(), ["Ondine Pty Ltd", "Avery Quill"])
        out, c = redact.redact("ondine pty ltd owes Avery Quill")
        self.assertEqual(out, "[NAME] owes [NAME]")
        self.assertEqual(c["NAME"], 2)

    def test_plugin_data_dir_without_file_gives_no_names(self):
        redact = _load("skilluminati_redact_paths_c", REDACT_PATH)
        (self.home / "plugin-data" / "skilluminati").mkdir(parents=True)
        self.assertEqual(redact.load_names(), [])

    def test_explicit_path_still_honoured(self):
        redact = _load("skilluminati_redact_paths_d", REDACT_PATH)
        f = self.home / "plugin-data" / "skilluminati" / "names.txt"
        f.parent.mkdir(parents=True)
        f.write_text("Avery Quill\n", encoding="utf-8")
        g = Path(self._tmp.name) / "explicit.txt"
        g.write_text("Ondine Pty Ltd\n", encoding="utf-8")
        self.assertEqual(redact.load_names(g), ["Ondine Pty Ltd"])
        self.assertEqual(redact.load_names(Path(self._tmp.name) / "missing.txt"), [])


if __name__ == "__main__":
    unittest.main()
