"""In plain words: the pool store never loses a run (fix batch S4). Writes go to a unique temp file in
the same folder and replace the pool in one step, under a lock on a sidecar file, so many writers
keep every run. A pool file that cannot be read is set aside as ``<name>.bad-<timestamp>`` (and
logged), never silently reset. Stdlib only, temp dirs, no model calls.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

evidence = importlib.import_module("plugin.skilluminati.evidence")
paths = importlib.import_module("plugin.skilluminati.paths")


class _Home(unittest.TestCase):
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

    def add(self, run_id):
        return evidence.add_run("s", skill_sha256="a" * 64, model="m1", judge="j1", run=run_id, date="2026-11-14",
                                pairs=[{"cell": "s", "task_hash": f"{i:012x}", "outcome": "win"} for i in range(3)])


class ManyWriters(_Home):
    def test_four_threads_by_forty_runs_keep_all_160(self):
        errors = []

        def writer(t):
            for i in range(40):
                try:
                    self.add(f"t{t}-r{i}")
                except Exception as exc:  # any lost write is a failure
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        data = json.loads(evidence.pool_path("s").read_text())
        self.assertEqual(len(data["pools"]), 1)
        self.assertEqual(len(data["pools"][0]["runs"]), 160)
        leftovers = [p.name for p in evidence.pool_path("s").parent.iterdir()
                     if p.name.endswith(".tmp") or ".json.tmp" in p.name]
        self.assertEqual(leftovers, [])


class UnreadablePool(_Home):
    def test_corrupt_pool_is_set_aside_not_reset(self):
        p = evidence.pool_path("s")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        with self.assertLogs(evidence.logger, level="WARNING") as logs:
            counts = self.add("r1")
        self.assertEqual(counts["n_runs"], 1)
        bad = [x for x in p.parent.iterdir() if x.name.startswith(p.name + ".bad-")]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].read_text(encoding="utf-8"), "{not json")
        self.assertTrue(any(".bad-" in line for line in logs.output))

    def test_wrong_shape_is_set_aside_too(self):
        p = evidence.pool_path("s")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pools": "nope"}), encoding="utf-8")
        with self.assertLogs(evidence.logger, level="WARNING"):
            self.add("r1")
        self.assertEqual(len([x for x in p.parent.iterdir() if ".bad-" in x.name]), 1)

    def test_missing_pool_is_simply_new(self):
        self.assertEqual(self.add("r1")["n_runs"], 1)
        self.assertEqual([x for x in evidence.pool_path("s").parent.iterdir() if ".bad-" in x.name], [])


if __name__ == "__main__":
    unittest.main()
