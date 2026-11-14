"""In plain words: one shared model-id normaliser (fix batch M2 + S3). It drops a provider prefix, a
bracketed context tag such as ``[1m]`` and a trailing date (``-YYYYMMDD`` or ``-YYYY-MM-DD``), and
nothing else, so sibling models never count as the same. The pool key, the Stale flag and the
same-model judge flag all use it. Stdlib only, temp dirs, no model calls.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

evidence = importlib.import_module("plugin.skilluminati.evidence")
rec = importlib.import_module("plugin.skilluminati.recommend")
commands = importlib.import_module("plugin.skilluminati.commands")
paths = importlib.import_module("plugin.skilluminati.paths")

SIBLINGS = [
    ("claude-opus-5", "claude-opus-5-5"),
    ("gpt-5", "gpt-5-mini"),
    ("o3", "o3-pro"),
    ("claude-sonnet-4", "claude-sonnet-4-5"),
]
SAME = [
    ("claude-opus-5-5[1m]", "claude-opus-5-5"),
    ("claude-opus-5-5-20261001", "claude-opus-5-5"),
    ("claude-opus-5-5-2026-10-01", "claude-opus-5-5"),
    ("anthropic/claude-opus-5-5", "claude-opus-5-5"),
    (" Claude-Opus-5-5 ", "claude-opus-5-5[1m]"),
    ("gpt-5-mini-2026-08-07", "gpt-5-mini"),
]


class OneNormaliser(unittest.TestCase):
    def test_key_strips_tag_date_and_prefix_only(self):
        self.assertEqual(rec.model_key("claude-opus-5-5[1m]"), "claude-opus-5-5")
        self.assertEqual(rec.model_key("claude-opus-5-5-20261001"), "claude-opus-5-5")
        self.assertEqual(rec.model_key("claude-opus-5-5-2026-10-01"), "claude-opus-5-5")
        self.assertEqual(rec.model_key("anthropic/claude-opus-5-5"), "claude-opus-5-5")
        self.assertEqual(rec.model_key("gpt-5-mini"), "gpt-5-mini")
        self.assertEqual(rec.model_key("o3-pro"), "o3-pro")
        self.assertIsNone(rec.model_key(None))
        self.assertIsNone(rec.model_key("unknown"))
        self.assertIsNone(rec.model_key("  "))

    def test_siblings_never_match_anywhere(self):
        for a, b in SIBLINGS:
            for x, y in ((a, b), (b, a)):
                with self.subTest(pair=(x, y)):
                    self.assertIs(evidence.same_model(x, y), False)
                    self.assertFalse(rec.same_judge(x, y))
                    self.assertIsNotNone(commands.stale_label(x, y))
                    keys = [f["key"] for f in evidence.show_flags(model=x, skill_sha256=None, active=y,
                                                                  live_sha256=None)]
                    self.assertIn(evidence.FLAG_STALE_KEY, keys)

    def test_same_model_spellings_match_everywhere(self):
        for a, b in SAME:
            for x, y in ((a, b), (b, a)):
                with self.subTest(pair=(x, y)):
                    self.assertIs(evidence.same_model(x, y), True)
                    self.assertTrue(rec.same_judge(x, y))
                    self.assertIsNone(commands.stale_label(x, y))

    def test_recommend_stale_flag_uses_it(self):
        flags = rec.build_flags(n_decisive=12, heavy=None, verdict_model="claude-opus-5-5[1m]",
                                active_model="anthropic/claude-opus-5-5", harm_flip_share=None,
                                job_first_share=None)
        self.assertNotIn(rec.FLAG_STALE, [f["key"] for f in flags])
        flags = rec.build_flags(n_decisive=12, heavy=None, verdict_model="gpt-5", active_model="gpt-5-mini",
                                harm_flip_share=None, job_first_share=None)
        self.assertIn(rec.FLAG_STALE, [f["key"] for f in flags])


class PoolKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": self.tmp.name, "HOME": self.tmp.name})
        self.env.start()
        self.data = mock.patch.object(paths, "_data_dir_override", Path(self.tmp.name) / "plugin-data")
        self.data.start()

    def tearDown(self):
        self.data.stop()
        self.env.stop()
        self.tmp.cleanup()

    def add(self, run, model, judge="gpt-6"):
        pairs = [{"cell": "s", "task_hash": f"{i:012x}", "outcome": "win"} for i in range(3)]
        return evidence.add_run("s", skill_sha256="a" * 64, model=model, judge=judge, run=run,
                                date="2026-11-14", pairs=pairs)

    def test_tag_and_date_spellings_share_one_pool(self):
        self.assertEqual(self.add("r1", "claude-opus-5-5[1m]")["n_runs"], 1)
        self.assertEqual(self.add("r2", "claude-opus-5-5")["n_runs"], 2)
        self.assertEqual(self.add("r3", "claude-opus-5-5-20261001", judge="gpt-6[1m]")["n_runs"], 3)
        self.assertEqual(len(json.loads(evidence.pool_path("s").read_text())["pools"]), 1)

    def test_sibling_models_get_their_own_pool(self):
        self.add("r1", "claude-opus-5-5")
        self.assertEqual(self.add("r2", "claude-opus-5")["n_runs"], 1)
        self.assertEqual(self.add("r3", "gpt-5", judge="gpt-5-mini")["n_runs"], 1)
        self.assertEqual(len(json.loads(evidence.pool_path("s").read_text())["pools"]), 3)


if __name__ == "__main__":
    unittest.main()
