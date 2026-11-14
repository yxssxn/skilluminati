"""W3PY NUM: ``--until STAMP`` freezes the numbers script to run folders whose name sorts at or
before STAMP, so later runs do not move published figures. The cutoff is printed in the header."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_f08_numbers import run_script, write_run


class UntilTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / "runs"
        write_run(self.runs, "20260101-000000-verdict-demo-skill", ["win"] * 6 + ["tie"] * 6, ["win", "tie"] * 4)
        write_run(self.runs, "20260102-000000-verdict-demo-skill", ["loss"] * 12, ["win"] * 8)

    def tearDown(self):
        self.tmp.cleanup()

    def test_later_runs_are_left_out(self):
        rc, out = run_script(self.runs, "--until", "20260101-120000")
        self.assertEqual(rc, 0)
        self.assertIn("finished verdict runs: 1 | skipped: 0", out)
        self.assertIn("runs 1 | tasks 12", out)
        self.assertIn("4 of 8", out)  # same-vs-same from the first run only
        self.assertNotIn("20260102", out)

    def test_cutoff_in_header_and_inclusive(self):
        rc, out = run_script(self.runs, "--until", "20260102-000000-verdict-demo-skill")
        lines = out.splitlines()
        self.assertEqual(lines[1], "runs up to: 20260102-000000-verdict-demo-skill (run folders whose name sorts at or before it)")
        self.assertIn("finished verdict runs: 2", out)

    def test_without_until_output_is_unchanged(self):
        _, plain = run_script(self.runs)
        _, cut = run_script(self.runs, "--until", "99999999")
        lines = cut.splitlines()
        self.assertEqual("\n".join(lines[:1] + lines[2:]), plain.rstrip("\n"))
        self.assertNotIn("runs up to", plain)

    def test_run_folders_are_not_touched(self):
        before = sorted((str(p), p.stat().st_mtime_ns) for p in self.runs.rglob("*"))
        run_script(self.runs, "--until", "20260101-120000")
        self.assertEqual(sorted((str(p), p.stat().st_mtime_ns) for p in self.runs.rglob("*")), before)
