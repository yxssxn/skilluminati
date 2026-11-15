"""In plain words: a run killed by a Hermes restart leaves a folder with no result and no '[stop]'
line. The next run start (and /skilluminati last) closes it: one '[stop]' line in progress.log and a
NOTE.txt saying nothing was saved. Live, fresh and finished runs are left alone; pools/ and
verdicts.json are never touched. Temp dirs only, no model calls.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

report = importlib.import_module("plugin.skilluminati.report")
run = importlib.import_module("plugin.skilluminati.run")
sb_copy = importlib.import_module("plugin.skilluminati.copy")
sb_paths = importlib.import_module("plugin.skilluminati.paths")

OLD = time.time() - 3600


def _folder(runs: Path, name: str, *, result=False, stop=False, age=OLD) -> Path:
    d = runs / name
    d.mkdir(parents=True)
    log = "[plan] x: 12 tasks\n[skill] x\n" + ("[stop] StopRequested: you ran /skilluminati stop\n" if stop else "")
    (d / "progress.log").write_text(log, encoding="utf-8")
    (d / "calls.jsonl").write_text("", encoding="utf-8")
    if result:
        (d / "result.json").write_text("{}", encoding="utf-8")
    for f in d.iterdir():
        os.utime(f, (age, age))
    return d


class MarkInterrupted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.runs = self.data / "runs"
        self.runs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_text_exists(self):
        self.assertEqual(sb_copy.INTERRUPTED_STOP, "[stop] Interrupted: Hermes stopped during this run")
        self.assertIn("nothing was saved", sb_copy.INTERRUPTED_NOTE.lower())

    def test_killed_run_gets_stop_line_and_note(self):
        d = _folder(self.runs, "20261115-013707-verdict-agent-handoff-operations")
        out = report.mark_interrupted(self.runs)
        self.assertEqual(out, [d])
        lines = (d / "progress.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[-1], sb_copy.INTERRUPTED_STOP)
        self.assertEqual(lines[0], "[plan] x: 12 tasks")
        note = d / "NOTE.txt"
        self.assertEqual(note.read_text(encoding="utf-8").strip(), sb_copy.INTERRUPTED_NOTE)
        self.assertEqual(stat.S_IMODE(note.stat().st_mode), 0o600)
        self.assertFalse((d / "result.json").exists())

    def test_idempotent(self):
        d = _folder(self.runs, "20261115-013707-verdict-s")
        report.mark_interrupted(self.runs)
        before = (d / "progress.log").read_text(encoding="utf-8")
        self.assertEqual(report.mark_interrupted(self.runs), [])
        self.assertEqual((d / "progress.log").read_text(encoding="utf-8"), before)
        self.assertEqual(before.count("[stop]"), 1)

    def test_leaves_finished_stopped_and_fresh_runs(self):
        done = _folder(self.runs, "20261115-010000-verdict-a", result=True)
        stopped = _folder(self.runs, "20261115-010001-verdict-b", stop=True)
        fresh = _folder(self.runs, "20261115-010002-verdict-c", age=time.time() - 10)
        self.assertEqual(report.mark_interrupted(self.runs), [])
        for d in (done, stopped, fresh):
            self.assertFalse((d / "NOTE.txt").exists(), d.name)
        self.assertEqual((stopped / "progress.log").read_text(encoding="utf-8").count("[stop]"), 1)

    def test_stale_uses_running_stale_s(self):
        just_under = time.time() - report.RUNNING_STALE_S + 30
        d = _folder(self.runs, "20261115-010003-verdict-d", age=just_under)
        self.assertEqual(report.mark_interrupted(self.runs), [])
        self.assertEqual(report.mark_interrupted(self.runs, now=time.time() + 60), [d])

    def test_any_recent_file_keeps_it_open(self):
        d = _folder(self.runs, "20261115-010004-verdict-e")
        (d / "calls.jsonl").write_text("{}\n", encoding="utf-8")  # a call just landed
        self.assertEqual(report.mark_interrupted(self.runs), [])

    def test_live_run_is_skipped(self):
        d = _folder(self.runs, "20261115-010005-verdict-live-skill")
        sel = _folder(self.runs, "20261115-010006-selftest-quick")
        (self.data / "live.json").write_text(json.dumps({"skill": "live-skill", "kind": "verdict",
                                                         "updated": time.time()}), encoding="utf-8")
        self.assertEqual(report.mark_interrupted(self.runs), [sel])
        self.assertFalse((d / "NOTE.txt").exists())
        (self.data / "live.json").write_text(json.dumps({"skill": "", "kind": "selftest",
                                                         "updated": time.time()}), encoding="utf-8")
        sel2 = _folder(self.runs, "20261115-010007-selftest-full")
        self.assertEqual(report.mark_interrupted(self.runs), [d])
        self.assertFalse((sel2 / "NOTE.txt").exists())

    def test_old_live_json_does_not_protect(self):
        d = _folder(self.runs, "20261115-010008-verdict-x")
        (self.data / "live.json").write_text(json.dumps({"skill": "x", "kind": "verdict",
                                                         "updated": OLD}), encoding="utf-8")
        self.assertEqual(report.mark_interrupted(self.runs), [d])

    def test_never_touches_pools_or_verdicts(self):
        (self.data / "verdicts.json").write_text('{"x": 1}', encoding="utf-8")
        pools = self.runs.parent / "pools"
        pools.mkdir()
        (pools / "p.json").write_text("[]", encoding="utf-8")
        stray = self.runs / "pools"  # a folder named pools under runs/ too
        stray.mkdir()
        (stray / "progress.log").write_text("x\n", encoding="utf-8")
        os.utime(stray / "progress.log", (OLD, OLD))
        report.mark_interrupted(self.runs)
        self.assertEqual((self.data / "verdicts.json").read_text(encoding="utf-8"), '{"x": 1}')
        self.assertEqual(sorted(p.name for p in pools.iterdir()), ["p.json"])
        self.assertFalse((stray / "NOTE.txt").exists())

    def test_folder_without_progress_log_is_left(self):
        d = self.runs / "20261115-010009-verdict-y"
        d.mkdir()
        (d / "plan.json").write_text("{}", encoding="utf-8")
        os.utime(d / "plan.json", (OLD, OLD))
        self.assertEqual(report.mark_interrupted(self.runs), [])

    def test_missing_runs_dir_is_fine(self):
        self.assertEqual(report.mark_interrupted(self.data / "nope"), [])

    def test_last_command_marks_first(self):
        d = _folder(self.runs, "20261115-010010-verdict-z")
        with mock.patch.object(sb_paths, "runs_dir", return_value=self.runs):
            run.last_command([])
        self.assertTrue((d / "NOTE.txt").is_file())

    def test_last_command_skips_while_this_process_runs(self):
        d = _folder(self.runs, "20261115-010011-verdict-z")
        with mock.patch.object(sb_paths, "runs_dir", return_value=self.runs), \
                mock.patch.object(run, "active_status", return_value={"label": "t", "used": 1, "cap": 4}):
            run.last_command([])
        self.assertFalse((d / "NOTE.txt").exists())

    def test_run_start_marks_before_the_new_run(self):
        d = _folder(self.runs, "20261115-010012-verdict-w")
        guard = mock.Mock(used=0, cap=0)
        with mock.patch.object(sb_paths, "runs_dir", return_value=self.runs), \
                mock.patch.object(run, "_post", return_value=None), \
                mock.patch.object(run, "surface", return_value="cli"):
            out = run.launch(object(), "t", guard, lambda: "nothing", "first", "start")
        self.assertEqual(out, "nothing")
        self.assertTrue((d / "NOTE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
