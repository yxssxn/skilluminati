"""W3PY SEC-S1: verdicts.json and live.json writes are locked and atomic. Concurrent writers keep
every row; no fixed temp name is left behind. Temp HERMES_HOME only."""

from __future__ import annotations

import json
import threading
import time
from unittest import mock

from tests.test_run import HomeCase  # noqa: F401  (puts plugin/ on sys.path)
from skilluminati import llm as sb_llm, paths, run  # noqa: E402


def _summary(skill: str) -> dict:
    return {"skill": skill, "combined": {"verdict": "Keep", "wins": 3, "losses": 0, "ties": 1, "low": 0.4, "high": 0.9},
            "stamp": {"date": "2026-11-14"}, "recommendation": None}


class StoreLockTests(HomeCase):
    def test_concurrent_writers_keep_all_rows(self):
        real = run._settle_wording

        def slow(row, unused):  # widen the read-modify-write window so a missing lock loses rows
            time.sleep(0.02)
            return real(row, unused)

        errors = []

        def write(i):
            try:
                run.store_verdict(_summary(f"skill-{i}"), model="claude-opus-5-5", judge=None)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        with mock.patch.object(run, "_settle_wording", slow):
            threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])
        data = json.loads(paths.verdicts_path().read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["skill"] for r in data["runs"]), sorted(f"skill-{i}" for i in range(8)))
        self.assertEqual([p.name for p in paths.verdicts_path().parent.glob("*.tmp")], [])

    def test_live_json_uses_a_unique_temp_file(self):
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        guard = sb_llm.CommandGuard(None, cap=0)
        seen = []
        real = run.os.replace

        def spy(src, dst):
            seen.append(str(src))
            return real(src, dst)

        with mock.patch.object(run.os, "replace", spy), mock.patch("os.replace", spy):
            run.write_live(guard, "Getting ready", {"skill": "s"})
        live = paths.data_dir() / run.LIVE_FILE
        self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["skill"], "s")
        self.assertTrue(seen)
        self.assertFalse(any(s.endswith("live.tmp") for s in seen), seen)
