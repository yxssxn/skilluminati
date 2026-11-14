"""In plain words: checks the page's data source reads saved results safely and never changes them.

Tests for plugin/dashboard/plugin_api.py: the pure reader, called directly (no server)."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "plugin" / "dashboard" / "plugin_api.py"
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample-verdicts.json"  # copy of research/sample-verdicts.json (made-up demo data)


def _load():
    spec = importlib.util.spec_from_file_location("skilluminati_plugin_api", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


api = _load()


class DashboardApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _write(self, text):
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_path_uses_hermes_home_env(self):
        self.assertEqual(api.verdicts_path(), self.home / "plugin-data" / "skilluminati" / "verdicts.json")

    def test_missing_file_is_empty(self):
        out = api.read_verdicts()
        copy_block = out.pop("copy")  # A12.C: the owl copy rides on every payload; tested in test_recommend
        self.assertEqual(copy_block["judge"], "the Owl")
        self.assertEqual(
            out,
            {"schema": 1, "runs": [], "aa": [], "position_bias": None, "empty": True},
        )

    def test_valid_file_returns_runs(self):
        self._write(SAMPLE.read_text(encoding="utf-8"))
        out = api.read_verdicts()
        self.assertNotIn("error", out)
        self.assertFalse(out["empty"])
        self.assertTrue(out["demo"])
        self.assertEqual(
            [r["skill"] for r in out["runs"]],
            ["helpful-sample", "poisoned-sample", "placebo-sample", "bloat-sample"],
        )
        self.assertEqual(out["runs"][0]["verdict"], "Keep")
        self.assertEqual(len(out["aa"]), 1)
        self.assertEqual(out["position_bias"], 0.55)

    def test_run_keys_are_whitelisted(self):
        self._write(json.dumps({"schema": 1, "runs": [{
            "skill": "s", "verdict": "Keep", "wins": 3, "losses": 0, "ties": 0, "model": "m",
            "prompt": "secret task text", "skill_path": "/home/someone/.hermes/skills/s/SKILL.md",
            "evidence": {"with_answer": "x"}, "date": {"nested": "no"}}],
            "aa": [{"model": "m", "wins": 1, "raw_prompts": ["x"]}], "position_bias": {"a": 1}}))
        out = api.read_verdicts()
        run = out["runs"][0]
        for bad in ("prompt", "skill_path", "evidence", "date"):
            self.assertNotIn(bad, run)
        self.assertEqual((run["skill"], run["wins"]), ("s", 3))
        self.assertEqual(out["aa"], [{"model": "m", "wins": 1}])
        self.assertIsNone(out["position_bias"])

    def test_corrupt_file_returns_error_without_raising(self):
        self._write("{not json")
        out = api.read_verdicts()
        self.assertIn("error", out)
        self.assertEqual(out["runs"], [])

    def test_wrong_shape_returns_error(self):
        self._write(json.dumps([1, 2, 3]))
        self.assertIn("error", api.read_verdicts())
        self._write(json.dumps({"schema": 1, "runs": "nope"}))
        self.assertIn("error", api.read_verdicts())

    def test_read_is_read_only(self):
        p = self._write(SAMPLE.read_text(encoding="utf-8"))
        before = p.read_bytes()
        api.read_verdicts()
        self.assertEqual(p.read_bytes(), before)

    def test_router_exposes_only_get_routes(self):
        if getattr(api, "router", None) is None:
            self.skipTest("FastAPI not importable")
        routes = [(r.path, sorted(r.methods)) for r in api.router.routes]
        self.assertEqual(routes, [("/verdicts", ["GET"]), ("/page", ["GET"]), ("/live", ["GET"])])
        self.assertEqual(api.verdicts(), api.read_verdicts())


if __name__ == "__main__":
    unittest.main()
