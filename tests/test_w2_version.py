"""In plain words: every new result.json says which build made it (wave 2, item 1). The plugin
version comes from plugin.yaml and the code fingerprint is a SHA-256 over the plugin's own .py, .js
and .yaml files, sorted by relative path, first 12 hex characters. The report prints
"Skilluminati 0.2.0 (code abc123def456)" as its last line; older results without the fields print
nothing extra. No git, no model calls; temp dirs only.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

paths = importlib.import_module("plugin.skilluminati.paths")
report = importlib.import_module("plugin.skilluminati.report")

PLUGIN = ROOT / "plugin"


def _expected(root: Path) -> str:
    files = sorted((p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".js", ".yaml"}
                    and "__pycache__" not in p.parts), key=lambda p: p.relative_to(root).as_posix())
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def _verdict_result(**extra):
    return {"per_skill": {"s": {"skill": "s", "combined": {"wins": 3, "losses": 1, "ties": 0}, "n_tasks": 4,
                                "user_label": "Earns its keep"}},
            "totals": {"calls": 12, "total_tokens": 900}, **extra}


class VersionStamp(unittest.TestCase):
    def test_stamp_reads_plugin_yaml_and_hashes_the_code(self):
        st = paths.version_stamp()
        self.assertEqual(st["plugin_version"], importlib.import_module("plugin.skilluminati").__version__)
        manifest = json.loads((PLUGIN / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], st["plugin_version"])
        self.assertEqual(st["plugin_version"], "0.2.1")
        self.assertRegex(st["code_fingerprint"], r"^[0-9a-f]{12}$")
        self.assertEqual(st["code_fingerprint"], _expected(PLUGIN))

    def test_stamp_on_a_copy_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugin.yaml").write_text('name: x\nversion: "9.1.0"\n', encoding="utf-8")
            (root / "a").mkdir()
            (root / "a" / "m.py").write_text("x = 1\n", encoding="utf-8")
            (root / "notes.md").write_text("ignored\n", encoding="utf-8")
            st = paths.version_stamp(root)
            self.assertEqual(st["plugin_version"], "9.1.0")
            self.assertEqual(st["code_fingerprint"], _expected(root))
            before = st["code_fingerprint"]
            (root / "notes.md").write_text("still ignored\n", encoding="utf-8")
            self.assertEqual(paths.version_stamp(root)["code_fingerprint"], before)
            (root / "a" / "m.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(paths.version_stamp(root)["code_fingerprint"], before)

    def test_no_git_call(self):
        src = (PLUGIN / "skilluminati" / "paths.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", src)
        self.assertNotIn("git ", src)


class ReportFooter(unittest.TestCase):
    def _build(self, result, name="20261114-200000-verdict-s", plain=False):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / name
            d.mkdir()
            (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return report.build(d, plain=plain)

    def test_new_result_prints_the_footer_last(self):
        for plain in (False, True):
            text = self._build(_verdict_result(plugin_version="0.2.0", code_fingerprint="abc123def456"), plain=plain)
            self.assertEqual(text.splitlines()[-1], "Skilluminati 0.2.0 (code abc123def456)")

    def test_old_result_prints_nothing_extra(self):
        text = self._build(_verdict_result())
        self.assertNotIn("Skilluminati 0.", text)
        self.assertNotIn("(code ", text)

    def test_selftest_footer(self):
        res = {"run": {"run": "quick", "totals": {"calls": 2}}, "aggregate": {"cells": {}},
               "plugin_version": "0.2.0", "code_fingerprint": "0123456789ab"}
        text = self._build(res, name="20261114-200000-selftest-quick")
        self.assertEqual(text.splitlines()[-1], "Skilluminati 0.2.0 (code 0123456789ab)")
        res.pop("plugin_version"), res.pop("code_fingerprint")
        self.assertNotIn("(code ", self._build(res, name="20261114-200000-selftest-quick"))

    def test_run_writes_the_stamp_into_both_result_files(self):
        src = (PLUGIN / "skilluminati" / "run.py").read_text(encoding="utf-8")
        writes = [m.start() for m in re.finditer(r'\(out_dir / "result.json"\)\.write_text', src)]
        self.assertEqual(len(writes), 2)
        for at in writes:
            self.assertIn("version_stamp()", src[max(0, at - 400):at + 200])


if __name__ == "__main__":
    unittest.main()
