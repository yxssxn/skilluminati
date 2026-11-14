"""F03: a verdict is bound to the skill file it tested (SHA-256 of the SKILL.md bytes) and to the
model it ran on. When it is shown later, a changed file raises "Skill changed since this test" and a
different active model raises Stale, on the chat result, the report, the dashboard /verdicts route
and the Desktop page data. Rows saved before tonight have no fingerprint: no flag, no crash.
Fake model, temp HERMES_HOME, no network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from unittest import mock

from tests import test_run as R  # noqa: E402  (puts plugin/ on sys.path)
from skilluminati import copy as sb_copy, evidence, report, run, tools  # noqa: E402

API = R.REPO / "plugin" / "dashboard" / "plugin_api.py"


def _api():
    spec = importlib.util.spec_from_file_location("skilluminati_plugin_api_f03", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


api = _api()


class BindingCase(R.HomeCase):
    def setUp(self):
        super().setUp()
        evidence.clear_cache()
        api_ev = api._sub("evidence")
        if api_ev is not None:
            api_ev.clear_cache()
        d = self.home / "skills" / "notes" / R.SKILL
        d.mkdir(parents=True)
        self.skill_md = d / "SKILL.md"
        self.skill_md.write_text(R.SKILL_MD, encoding="utf-8")

    def tearDown(self):
        run.wait_active(10)
        super().tearDown()

    def _run_once(self):
        R._db(self.home / "state.db", 6)
        ctx = R.FakeCtx(R.JudgeAlwaysWith(), surface="cli", home=self.home)
        return self.h(ctx, R.SKILL)

    def _verdicts(self):
        return json.loads((self.home / "plugin-data" / "skilluminati" / "verdicts.json").read_text())

    def _folder(self):
        return report.find_run(skill=R.SKILL, runs_dir=self.home / "plugin-data" / "skilluminati" / "runs")

    def _touch_one_byte(self):
        raw = self.skill_md.read_bytes()
        self.skill_md.write_bytes(raw + b" ")
        evidence.clear_cache()
        api_ev = api._sub("evidence")
        if api_ev is not None:
            api_ev.clear_cache()


class StoredHashTests(BindingCase):
    def test_hash_of_the_bytes_is_stored_in_result_and_verdicts(self):
        self._run_once()
        want = hashlib.sha256(self.skill_md.read_bytes()).hexdigest()
        row = self._verdicts()["runs"][0]
        self.assertEqual(row["skill_sha256"], want)
        result = json.loads((self._folder() / "result.json").read_text())
        self.assertEqual(result["per_skill"][R.SKILL]["skill_sha256"], want)

    def test_unchanged_skill_and_model_raise_no_flag(self):
        self._run_once()
        text = report.saved_or_built(self._folder())
        self.assertNotIn(sb_copy.FLAG_SKILL_CHANGED, text)
        self.assertNotIn("Stale", text)
        out = api.read_verdicts()
        flags = out["runs"][0]["recommendation"]["flags"]
        self.assertFalse(any(f["key"] in ("stale", "skill_changed") for f in flags), flags)


class SkillChangedTests(BindingCase):
    def test_one_byte_change_flags_every_place_the_verdict_is_shown(self):
        self._run_once()
        self._touch_one_byte()
        flag = sb_copy.FLAG_SKILL_CHANGED
        self.assertEqual(flag, "Skill changed since this test")
        # chat result (the agent tool) and the report
        got = json.loads(tools.result({"skill": R.SKILL}))
        self.assertIn(flag, got["report"])
        self.assertIn(flag, run.last_command([R.SKILL]))
        # the saved report.md stays as it was written; the flag is worked out when shown
        self.assertNotIn(flag, (self._folder() / "report.md").read_text())
        # the dashboard /verdicts route
        row = api.read_verdicts()["runs"][0]
        self.assertTrue(row.get("skill_changed"))
        self.assertIn(flag, [f["text"] for f in row["recommendation"]["flags"]])
        # the Desktop page data
        page = api.read_page(self.home)["results"][0]
        self.assertTrue(page["skill_changed"])
        self.assertIn(flag, page["show_flags"])

    def test_rows_saved_before_fingerprints_show_no_flag_and_do_not_crash(self):
        self._run_once()
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        data = json.loads(p.read_text())
        for r in data["runs"]:
            r.pop("skill_sha256", None)
        p.write_text(json.dumps(data))
        self._touch_one_byte()
        row = api.read_verdicts()["runs"][0]
        self.assertNotIn("error", api.read_verdicts())
        self.assertFalse(row.get("skill_changed"))
        page = api.read_page(self.home)["results"][0]
        self.assertFalse(page["skill_changed"])

    def test_old_result_json_without_hash_reports_cleanly(self):
        self._run_once()
        folder = self._folder()
        result = json.loads((folder / "result.json").read_text())
        result["per_skill"][R.SKILL].pop("skill_sha256")
        (folder / "result.json").write_text(json.dumps(result))
        (folder / "report.md").unlink()
        self._touch_one_byte()
        text = report.saved_or_built(folder)
        self.assertIn(R.SKILL, text)
        self.assertNotIn(sb_copy.FLAG_SKILL_CHANGED, text)


class StaleTests(BindingCase):
    def test_row_stored_under_another_model_shows_stale_through_the_verdicts_route(self):
        self._run_once()
        p = self.home / "plugin-data" / "skilluminati" / "verdicts.json"
        data = json.loads(p.read_text())
        data["runs"][0]["model"] = "claude-sonnet-4-5"
        p.write_text(json.dumps(data))
        api_ev = api._sub("evidence")
        with mock.patch.object(api_ev, "active_model", return_value="claude-opus-5-5[1m]"):
            out = api.verdicts() if getattr(api, "router", None) is not None else api.read_verdicts()
            page = api.read_page(self.home)["results"][0]
        row = out["runs"][0]
        self.assertTrue(row.get("stale"))
        texts = [f["text"] for f in row["recommendation"]["flags"]]
        self.assertIn("Stale: tested on claude-sonnet-4-5, re-test on claude-opus-5-5[1m]", texts)
        self.assertTrue(page["stale"])

    def test_same_model_is_not_stale(self):
        self._run_once()
        api_ev = api._sub("evidence")
        with mock.patch.object(api_ev, "active_model", return_value=R.ACTIVE):
            row = api.read_verdicts()["runs"][0]
        self.assertFalse(row.get("stale"))

    def test_report_shows_stale_when_the_active_model_changed(self):
        self._run_once()
        with mock.patch.object(evidence, "active_model", return_value="claude-sonnet-4-5"):
            text = report.saved_or_built(self._folder())
        self.assertIn(f"Stale: tested on {R.ACTIVE}, re-test on claude-sonnet-4-5", text)


class SameModelTests(R.unittest.TestCase):
    def test_names_compare_without_prefix_tag_or_date(self):
        self.assertTrue(evidence.same_model("claude-opus-5-5[1m]", "anthropic/claude-opus-5-5"))
        self.assertTrue(evidence.same_model("claude-opus-5-5-20261001", "claude-opus-5-5"))
        self.assertFalse(evidence.same_model("claude-opus-5-5", "claude-sonnet-5-5"))
        self.assertIsNone(evidence.same_model(None, "claude-opus-5-5"))
        self.assertIsNone(evidence.same_model("unknown", "claude-opus-5-5"))
