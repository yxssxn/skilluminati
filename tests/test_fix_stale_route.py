"""In plain words: Stale compares with the model a new test would really run on (fix batch S2). When
the plugin's ``subject.model`` setting is set, tests run on it, so that is the model to compare
with; otherwise the active default model. The setting is read the way Hermes reads plugin settings:
nested under ``plugins.entries.skilluminati.settings``. Stdlib only, no model calls.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

evidence = importlib.import_module("plugin.skilluminati.evidence")
run = importlib.import_module("plugin.skilluminati.run")

DEFAULT = "claude-opus-5-5"
PINNED = "gpt-6"


def _cfg(subject=None, where="settings"):
    plugin = {where: {"subject": {"model": subject}}} if subject is not None else {}
    return {"model": {"default": DEFAULT}, "plugins": {"entries": {"skilluminati": plugin}}}


class StaleUsesTheSubjectRoute(unittest.TestCase):
    def _active(self, cfg):
        with mock.patch.object(run, "_read_config", return_value=cfg):
            return run.tested_model_id(), evidence.active_model()

    def test_default_model_when_no_subject_is_set(self):
        self.assertEqual(self._active(_cfg()), (DEFAULT, DEFAULT))

    def test_subject_model_when_set(self):
        self.assertEqual(self._active(_cfg(PINNED)), (PINNED, PINNED))

    def test_legacy_config_subtree_is_read_too(self):
        self.assertEqual(self._active(_cfg(PINNED, where="config"))[1], PINNED)

    def test_blank_subject_falls_back_to_the_default(self):
        self.assertEqual(self._active(_cfg("  "))[1], DEFAULT)

    def test_a_row_tested_on_the_pinned_subject_is_not_stale(self):
        row = {"skill": "s", "model": PINNED}
        with mock.patch.object(run, "_read_config", return_value=_cfg(PINNED)):
            flags = evidence.flags_for_row(row, active=evidence.active_model(), look=False)
        self.assertEqual(flags, [])
        with mock.patch.object(run, "_read_config", return_value=_cfg()):
            flags = evidence.flags_for_row(row, active=evidence.active_model(), look=False)
        self.assertEqual([f["key"] for f in flags], [evidence.FLAG_STALE_KEY])

    def test_unreadable_config_means_unknown(self):
        with mock.patch.object(run, "_read_config", return_value=None):
            self.assertIsNone(evidence.active_model())


if __name__ == "__main__":
    unittest.main()
