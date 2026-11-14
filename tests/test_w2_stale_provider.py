"""In plain words: Stale when only a provider is set (wave 2, item 4). With ``subject.provider`` set
and ``subject.model`` unset, a new test runs on that provider's own default model, which the plugin
cannot read. The active default model (``model.default``) belongs to the active provider, so it is
not the tested model: the model to compare with is unknown and no Stale flag is shown. When the
subject provider is the active provider, the active default is the tested model again. Setting only
``judge.provider`` leaves the tested model alone. Models compare through ``recommend.model_key``,
as in the S2 fix. Stdlib only, no model calls.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

evidence = importlib.import_module("plugin.skilluminati.evidence")
run = importlib.import_module("plugin.skilluminati.run")

DEFAULT = "claude-opus-5-5"
ACTIVE_PROVIDER = "anthropic"


def _cfg(subject=None, judge=None, where="settings", provider=ACTIVE_PROVIDER):
    tree = {}
    if subject:
        tree["subject"] = dict(subject)
    if judge:
        tree["judge"] = dict(judge)
    model = {"default": DEFAULT}
    if provider is not None:
        model["provider"] = provider
    return {"model": model, "plugins": {"entries": {"skilluminati": {where: tree} if tree else {}}}}


def _flags(cfg, tested_on):
    row = {"skill": "s", "model": tested_on}
    with mock.patch.object(run, "_read_config", return_value=cfg):
        return [f["key"] for f in evidence.flags_for_row(row, active=evidence.active_model(), look=False)]


class ProviderOnly(unittest.TestCase):
    def test_subject_provider_alone_means_the_tested_model_is_unknown(self):
        cfg = _cfg(subject={"provider": "openrouter"})
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertIsNone(run.tested_model_id())
            self.assertIsNone(evidence.active_model())

    def test_no_stale_flag_against_the_default_model(self):
        cfg = _cfg(subject={"provider": "openrouter"})
        self.assertEqual(_flags(cfg, "gpt-6"), [])
        self.assertEqual(_flags(cfg, DEFAULT), [])

    def test_legacy_config_subtree_too(self):
        self.assertEqual(_flags(_cfg(subject={"provider": "openrouter"}, where="config"), "gpt-6"), [])

    def test_subject_provider_that_is_the_active_one_keeps_the_default(self):
        cfg = _cfg(subject={"provider": " Anthropic "})
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertEqual(run.tested_model_id(), DEFAULT)
        self.assertEqual(_flags(cfg, "gpt-6"), [evidence.FLAG_STALE_KEY])
        self.assertEqual(_flags(cfg, "anthropic/claude-opus-5-5[1m]"), [])  # model_key match

    def test_unknown_active_provider_with_a_subject_provider_is_unknown(self):
        cfg = _cfg(subject={"provider": "anthropic"}, provider=None)
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertIsNone(run.tested_model_id())

    def test_subject_model_set_wins_over_provider(self):
        cfg = _cfg(subject={"provider": "openrouter", "model": "gpt-6"})
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertEqual(run.tested_model_id(), "gpt-6")
        self.assertEqual(_flags(cfg, "openai/gpt-6"), [])
        self.assertEqual(_flags(cfg, DEFAULT), [evidence.FLAG_STALE_KEY])

    def test_judge_provider_alone_leaves_the_tested_model_alone(self):
        cfg = _cfg(judge={"provider": "openrouter"})
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertEqual(run.tested_model_id(), DEFAULT)
        self.assertEqual(_flags(cfg, DEFAULT), [])
        self.assertEqual(_flags(cfg, "gpt-6"), [evidence.FLAG_STALE_KEY])

    def test_blank_provider_is_unset(self):
        cfg = _cfg(subject={"provider": "  "})
        with mock.patch.object(run, "_read_config", return_value=cfg):
            self.assertEqual(run.tested_model_id(), DEFAULT)


if __name__ == "__main__":
    unittest.main()
