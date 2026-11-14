"""W3PY CODE simplify: one model normaliser. evidence and noise use recommend.model_key itself (no
wrappers), and every id form maps the same as before."""

from __future__ import annotations

from tests import test_run as R  # noqa: F401  (puts plugin/ on sys.path)
from skilluminati import evidence, noise, recommend  # noqa: E402

IDS = ["claude-opus-5-5", "claude-opus-5-5[1m]", "anthropic/claude-opus-5-5", "Claude-Opus-5-5-20261101",
       "gpt-5", "gpt-5-mini", "unknown", "active model", "", None, 7, "  ", "[1m]", "openrouter/x/y-2026-01-02"]


class ModelKeyTests(R.unittest.TestCase):
    def test_wrappers_are_gone(self):
        self.assertFalse(hasattr(evidence, "_model_core"))
        self.assertIs(noise.model_key, recommend.model_key)

    def test_same_model_and_noise_rows_unchanged(self):
        for a in IDS:
            for b in IDS:
                x, y = recommend.model_key(a), recommend.model_key(b)
                self.assertEqual(evidence.same_model(a, b), None if x is None or y is None else x == y)
