"""Unit tests for the ctx.llm wrapper. Uses a fake facade; no network, no Hermes imports."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import llm as sb_llm  # noqa: E402


class FakeLlm:
    def __init__(self, *, model="claude-haiku-5-5", provider="p", raise_exc=None):
        self.model, self.provider, self.raise_exc, self.calls = model, provider, raise_exc, []

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(
            text="pong", provider=self.provider, model=self.model,
            usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12, cache_read_tokens=0),
        )


MSGS = [{"role": "user", "content": "ping"}]


class SameModel(unittest.TestCase):
    def test_exact_and_suffix(self):
        self.assertTrue(sb_llm.same_model("claude-haiku-5-5", "claude-haiku-5-5"))
        self.assertTrue(sb_llm.same_model("claude-opus-5-5", "claude-opus-5-5[1m]"))
        self.assertTrue(sb_llm.same_model("nvidia/nemotron-3-ultra-550b-a55b:free",
                                          "nvidia/nemotron-3-ultra-550b-a55b:free"))

    def test_mismatch(self):
        self.assertFalse(sb_llm.same_model("nvidia/nemotron-3-ultra-550b-a55b:free", "claude-opus-5-5[1m]"))
        self.assertFalse(sb_llm.same_model("claude-haiku-5-5", "claude-opus-5-5"))
        self.assertFalse(sb_llm.same_model("claude-haiku-5-5", ""))


class Complete(unittest.TestCase):
    def test_pins_provider_and_model_and_records_usage(self):
        fake = FakeLlm()
        rec = sb_llm.complete(fake, MSGS, provider="p", model="claude-haiku-5-5", max_tokens=8, purpose="t")
        self.assertTrue(rec.ok)
        self.assertEqual(fake.calls[0][1]["provider"], "p")
        self.assertEqual(fake.calls[0][1]["model"], "claude-haiku-5-5")
        self.assertEqual((rec.input_tokens, rec.output_tokens, rec.total_tokens), (10, 2, 12))
        self.assertEqual(rec.text, "pong")
        self.assertEqual(rec.purpose, "t")

    def test_host_fallback_to_other_model_is_a_failure(self):
        fake = FakeLlm(model="claude-opus-5-5[1m]", provider="openrouter")
        rec = sb_llm.complete(fake, MSGS, provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free")
        self.assertFalse(rec.ok)
        self.assertEqual(rec.error_type, "ModelMismatch")
        self.assertIn("claude-opus-5-5[1m]", rec.error)
        self.assertTrue(rec.extra.get("host_fallback"))
        self.assertEqual(rec.model, "claude-opus-5-5[1m]")  # what actually ran is kept for the log

    def test_exception_recorded_verbatim(self):
        fake = FakeLlm(raise_exc=PermissionError("Plugin 'x' cannot override the provider"))
        rec = sb_llm.complete(fake, MSGS, provider="p", model="m")
        self.assertFalse(rec.ok)
        self.assertEqual(rec.error_type, "PermissionError")
        self.assertEqual(rec.error, "Plugin 'x' cannot override the provider")
        self.assertEqual(rec.requested_model, "m")


if __name__ == "__main__":
    unittest.main()
