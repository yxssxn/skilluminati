"""In plain words: the dev-harness runs (a1, a2, free) used to pin the author's own provider and
models in shipped code (SEC-S2). They now come from SKILLUMINATI_DEV_* environment variables with
no defaults, so the plugin names no private provider. A dev run with the pins unset refuses to
start; the quick and full self-tests users run are unchanged. Subprocesses with a clean
environment, no model calls.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "plugin" / "skilluminati"

#: The author's own provider: named nowhere in the plugin.
PRIVATE_PROVIDER = ("claude-subscription", "directsdk")
#: The author's dev model pins: not named in the two modules that held them (cost.py's price table
#: may still name public models).
PINNED_MODELS = ("longcat-", "meituan", "claude-haiku", "claude-sonnet", "claude-opus")


def _py(code: str, env_extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SKILLUMINATI_DEV_")}
    env.update(env_extra or {})
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


PROBE = """
import json, sys
sys.path.insert(0, 'plugin')
from skilluminati import runner, selftest
print(json.dumps({"sub": runner.SUB_PROVIDER, "small": runner.HAIKU, "large": runner.OPUS,
                  "harm": selftest.SONNET, "free_p": selftest.FREE_PROVIDER, "free_m": selftest.FREE_MODEL,
                  "judges": list(selftest.JUDGE_MODELS), "harm_judge": selftest.HARM_JUDGE["model"]}))
"""


class DevPins(unittest.TestCase):
    def test_shipped_code_names_no_private_provider_or_model(self):
        for path in sorted(PKG.glob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for word in PRIVATE_PROVIDER + (PINNED_MODELS if path.name in ("runner.py", "selftest.py") else ()):
                self.assertNotIn(word, text, f"{path.name} names {word}")
            self.assertIsNone(re.search(r"=\s*[\"']nous[\"']", text), path.name)

    def test_unset_env_means_empty_pins(self):
        got = _py(PROBE)
        self.assertEqual(got, {"sub": "", "small": "", "large": "", "harm": "", "free_p": "", "free_m": "",
                               "judges": [], "harm_judge": ""})

    def test_env_sets_the_pins(self):
        got = _py(PROBE, {"SKILLUMINATI_DEV_PROVIDER": "prov-x", "SKILLUMINATI_DEV_MODEL": "small-x",
                          "SKILLUMINATI_DEV_LARGE_MODEL": "large-x", "SKILLUMINATI_DEV_HARM_MODEL": "harm-x",
                          "SKILLUMINATI_DEV_FREE_PROVIDER": "freep-x", "SKILLUMINATI_DEV_FREE_MODEL": "freem-x"})
        self.assertEqual(got, {"sub": "prov-x", "small": "small-x", "large": "large-x", "harm": "harm-x",
                               "free_p": "freep-x", "free_m": "freem-x", "judges": ["small-x", "harm-x"],
                               "harm_judge": "harm-x"})

    def test_dev_run_without_pins_refuses(self):
        code = """
import json, sys, tempfile
sys.path.insert(0, 'plugin')
from skilluminati import selftest, paths
res = {}
for run in ("a1", "a2", "free", "quick", "full"):
    try:
        selftest.SelfTest(object(), fixtures_dir=paths.FIXTURES_DIR, out_dir=tempfile.mkdtemp(), run=run,
                          log=lambda s: None)
        res[run] = "ok"
    except ValueError as exc:
        res[run] = str(exc)
print(json.dumps(res))
"""
        got = _py(code)
        self.assertEqual(got["quick"], "ok")
        self.assertEqual(got["full"], "ok")
        for run in ("a1", "a2", "free"):
            self.assertIn("SKILLUMINATI_DEV_", got[run], run)

    def test_product_plan_is_unchanged_without_pins(self):
        code = """
import json, sys
sys.path.insert(0, 'plugin')
from skilluminati import selftest
print(json.dumps({r: selftest.plan_calls(r, 6)["by_model"] for r in ("quick", "full")}))
"""
        self.assertEqual(_py(code), {"quick": {"active model": 108}, "full": {"active model": 180}})


if __name__ == "__main__":
    unittest.main()
