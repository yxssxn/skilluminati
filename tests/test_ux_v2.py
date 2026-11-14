"""In plain words: checks fixes from earlier user testing of the chat messages and the page.

UX brief v2: the result comes back in the chat.

The report (tool, page, ``/skilluminati last``), the ``skilluminati_result`` tool, one-line popups,
three-line command replies, the page backend reading the folder the plugin writes, the verdict
token estimate, real model ids in ``result.json``, the ``run.done`` event and the one-time data copy.
Fake LLM and fake ctx only: no network, a temp HERMES_HOME.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import test_run as R  # noqa: E402  (FakeCtx, ActiveLlm, HomeCase; puts plugin/ on sys.path)
from tests.test_fix_runaway import LiveCtx  # noqa: E402  (a chat that takes posted messages: Desktop)

from skilluminati import commands, copy as sb_copy, paths, report, run, tools  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"


# ── synthetic results (shapes copied from real runs; numbers from the 11 Nov runs) ──

def verdict_result(*, label="Not sure yet", wins=5, losses=3, ties=1, n_next: "int | None" = 54, harm=0, skill="unslop",
                   model="claude-opus-5-5[1m]", aa=(2, 1, 6), heavy=3683, errors=0, stopped=None):
    themed, plain = sb_copy.VERDICTS[label]
    return {
        "provider": None, "subject_model": None, "judge_model": None,
        "per_skill": {skill: {
            "skill": skill, "n_tasks": wins + losses + ties, "n_errors": errors,
            "combined": {"wins": wins, "losses": losses, "ties": ties},
            "harm_cited_pairs": harm, "user_label": label,
            "heavy": {"flag": bool(heavy), "over_tokens": heavy, "text": "Heavy"},
            "recommendation": {
                "headline": {"label": label, "themed": themed, "plain": plain, "text": f"{themed}: {plain}", "tone": "warn"},
                "confidence": {"level": "Weak", "text": "Weak", "why": "x"},
                "flags": [{"key": "heavy", "text": "Heavy"}, {"key": "few_tasks", "text": "Few tasks"}],
                "next_step": {"key": "run_more", "text": f"Run {n_next} more tasks", "n": n_next}
                if n_next else {"key": "archive", "text": "Consider archiving", "n": None},
                "numbers": {"tasks_to_settle": n_next},
            },
            "stamp": {"model": model, "judge": None, "date": "2026-11-11"},
        }},
        "aa": {"wins": aa[0], "losses": aa[1], "ties": aa[2]} if aa else None,
        "stopped_early": stopped,
        "totals": {"calls": 72, "total_tokens": 847_486, "minutes": 2.95},
    }


def selftest_result(model="claude-opus-5-5[1m]"):
    def cell(label, w, l, t, ok=True, harm=0):
        return {"label": label, "matches_expected": ok, "n": 6, "job": {"wins": w, "losses": l, "ties": t},
                "harm": {"found": bool(harm), "tasks": harm}}
    return {"run": {"run": "quick", "subject_model": model, "stopped_early": None,
                    "totals": {"calls": 108, "total_tokens": 210_397, "minutes": 8.7}},
            "aggregate": {"cells": {"helpful/x": cell("Earns its keep", 6, 0, 0),
                                    "poisoned/x": cell("Gets in the way", 0, 6, 0, harm=6),
                                    "placebo/x": cell("Not sure yet", 2, 0, 4)}}}


def write_run(root: Path, name: str, result: dict) -> Path:
    d = root / "runs" / name
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return d


# ── Part 2: the report shapes ────────────────────────────────────────────────

class ReportShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verdict_markdown_matches_the_brief(self):
        d = write_run(self.root, "20261111-170708-verdict-unslop", verdict_result())
        self.assertEqual(report.build(d), textwrap.dedent("""\
            **unslop: Unproven.** Too few uses to tell yet.
            Better 5 · Same 1 · Worse 3.
            Tested on 9 uses.
            Model: claude-opus-5-5.
            Heavy: adds 3,683 tokens each time it loads.
            Same-model judge: the Owl is grading its own answers, so treat this as a lean. Set judge.provider and judge.model to another maker's model to firm it up.
            Next: run about 54 more uses.
            Same-vs-same: a firm pick 3 of 9 times (33%).
            One model, one answer each way, judged blind by the Owl. Advice, not a measurement.
            72 calls, 847,486 tokens, 3 minutes."""))  # F07: no judge set, so the model judged itself

    def test_one_more_task_is_singular(self):
        d = write_run(self.root, "20261111-171230-verdict-unslop", verdict_result(wins=6, losses=1, ties=2, n_next=1))
        text = report.build(d)
        self.assertIn("Next: run about 1 more use.", text)
        self.assertNotIn("1 more uses", text)
        self.assertEqual(report.plural(1, "task"), "1 task")
        self.assertEqual(report.plural(2, "task"), "2 tasks")

    def test_obscures_leads_with_harm(self):
        d = write_run(self.root, "20261111-180000-verdict-bad",
                      verdict_result(label="Gets in the way", wins=1, losses=7, ties=1, n_next=None, harm=5, skill="bad"))
        text = report.build(d)
        self.assertIn("**bad: Hindrance (Plausible).** Answers are worse with it.", text)  # S1: a lean shows its word
        self.assertIn("Caught doing harm on 5 uses.", text)
        plain = report.build(d, plain=True).splitlines()
        self.assertIn("Better 1 · Same 1 · Worse 7.", plain)

    def test_selftest_markdown_matches_the_brief(self):
        d = write_run(self.root, "20261111-164233-selftest-quick", selftest_result())
        self.assertEqual(report.build(d), textwrap.dedent("""\
            **Skilluminati self-test (quick): 3 of 3 as expected**
            Model: claude-opus-5-5[1m]. 108 calls, 9 minutes.

            | Planted skill | Verdict | Expected? | Detail |
            |---|---|---|---|
            | the Adept (helps) | Enlightened | Yes | better on 6 of 6 tasks |
            | the Impostor (sabotages) | Hindrance | Yes | caught doing harm on 6 of 6 tasks |
            | the Idler (does nothing) | Unproven | Yes | better on 2, no difference on 4 |"""))

    def test_terminal_shape_is_plain_lines_with_the_folder_last(self):
        d = write_run(self.root, "20261111-170708-verdict-unslop", verdict_result())
        lines = report.terminal(d).splitlines()
        self.assertFalse(any(l.startswith("|") or l.startswith("**") or l.startswith("- ") for l in lines), lines)
        self.assertEqual(lines[-1], f"Folder: {d}")
        self.assertEqual(sum(str(d) in l for l in lines), 1, "the path appears once, last")
        self.assertNotIn(str(d), report.build(d), "no folder path in the chat report")

    def test_model_comes_from_the_call_log_when_the_result_has_a_placeholder(self):
        res = verdict_result(model="active model")
        d = write_run(self.root, "20261111-170708-verdict-unslop", res)
        (d / "calls.jsonl").write_text(json.dumps({"model": "claude-opus-5-5[1m]", "role": "subject"}) + "\n")
        self.assertIn("Model: claude-opus-5-5.", report.build(d))

    def test_stopped_run_says_so(self):
        d = write_run(self.root, "20261111-170708-verdict-unslop",
                      verdict_result(stopped={"reason": "StopRequested", "error": "StopRequested: you ran stop"}))
        self.assertIn("Stopped early: you ran stop.", report.build(d))


# ── Part 3: popups and command replies ───────────────────────────────────────

class PopupTests(unittest.TestCase):
    def test_every_popup_is_one_short_line(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        long_skill = "a-very-long-skill-name-that-goes-on-and-on-and-on-forever-and-ever-and-ever"
        texts = [
            run.popup_started(72), run.popup_started(400),
            run.popup_progress(42, 72), run.popup_progress(399, 400),
            run.popup_stopped(41, 72), run.popup_failed(RuntimeError("x" * 500), 41),
            report.headline(write_run(root, "20261111-170708-verdict-unslop", verdict_result())),
            report.headline(write_run(root, f"20261111-170709-verdict-{long_skill}", verdict_result(skill=long_skill))),
            report.headline(write_run(root, "20261111-164233-selftest-quick", selftest_result())),
        ]
        for t in texts:
            self.assertIsInstance(t, str)
            self.assertNotIn("\n", t)
            self.assertLess(len(t), 120, t)
            self.assertNotIn("claude", t, "no model id in a popup")
            self.assertNotIn("/", t.replace("/skilluminati", ""), "no folder path in a popup")
        self.assertEqual(run.popup_progress(42, 72), "The Owl is weighing the evidence. 10 of 18 compared.")


class CommandReplyTests(R.HomeCase):
    def test_selftest_reply_is_three_lines_and_names_the_model_once(self):
        with mock.patch.object(run, "active_model_id", return_value="claude-opus-5-5[1m]"):
            ctx = R.FakeCtx(R.ActiveLlm(), surface="local", answer=None)
            self.h(ctx, "selftest")
        self.assertEqual(ctx.asked, [], "W1: the self-test starts at once, no question")

    def test_judge_on_another_model_is_named(self):
        ctx = R.FakeCtx(R.ActiveLlm(), surface="telegram", settings={"judge.model": "claude-sonnet-5-5"})
        with mock.patch.object(run, "active_model_id", return_value="claude-opus-5-5[1m]"):
            out = self.h(ctx, "selftest")
        self.assertIn("on claude-opus-5-5[1m] (judge claude-sonnet-5-5, harm judge claude-sonnet-5-5)", out)

    def test_started_reply_is_three_lines(self):
        ctx = LiveCtx(R.ActiveLlm(), surface="local", home=self.home)
        with mock.patch.object(run, "announce", return_value=True):
            out = self.h(ctx, "selftest")
            self.assertTrue(run.wait_active(30))
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertTrue(out.startswith("The Owl's trial started"), out)
        self.assertIn("/skilluminati stop", out)


# ── Part 0 + 1: the run.done event and the tool ──────────────────────────────

class EventTests(R.HomeCase):
    def test_run_done_carries_the_chat_session_and_the_report_is_saved(self):
        sent = []
        ctx = LiveCtx(R.ActiveLlm(), surface="local", home=self.home)
        commands.bind(ctx)
        with mock.patch.object(run, "announce", side_effect=lambda *a, **k: sent.append(a) or True):
            commands.handle_skilluminati("selftest --yes", session_id="local-abc123")
            self.assertTrue(run.wait_active(30))
        self.assertEqual(len(sent), 1)
        run_dir, session_id, kind, skill = sent[0][:4]
        self.assertEqual((session_id, kind, skill), ("local-abc123", "selftest", None))
        rep = run_dir / "report.md"
        self.assertTrue(rep.is_file())
        self.assertEqual(stat.S_IMODE(rep.stat().st_mode), 0o600)
        self.assertIn("3 of 3 as expected", rep.read_text())
        self.assertTrue(any("as expected" in p for p in ctx.posted), ctx.posted)

    def test_announce_uses_the_public_event_bridge(self):
        got = []
        fake = types.ModuleType("hermes_cli.plugin_events")
        fake.broadcast_plugin_event = lambda pid, ev, payload: got.append((pid, ev, payload))
        pkg = types.ModuleType("hermes_cli")
        with mock.patch.dict(sys.modules, {"hermes_cli": pkg, "hermes_cli.plugin_events": fake}):
            ok = run.announce(Path("/x/runs/20261111-170708-verdict-unslop"), "local-1", "verdict", "unslop")
        self.assertTrue(ok)
        pid, ev, payload = got[0]
        self.assertEqual((pid, ev), ("skilluminati", "run.done"))
        self.assertEqual(payload["session_id"], "local-1")
        self.assertEqual(payload["run"], "20261111-170708-verdict-unslop")
        self.assertIn("skilluminati_result", payload["prompt"], "the line names the tool for on-demand tool loading")


class ResultToolTests(R.HomeCase):
    def test_no_finished_run_is_one_plain_line(self):
        out = json.loads(tools.result({}))
        self.assertTrue(out["success"])
        self.assertFalse(out["found"])
        self.assertEqual(len(out["report"].splitlines()), 1)
        self.assertIn("No finished Skilluminati run yet", out["report"])

    def test_returns_the_saved_report_from_a_fresh_process(self):
        root = self.home / "plugin-data" / "skilluminati"
        d = write_run(root, "20261111-171230-verdict-unslop", verdict_result(wins=6, losses=1, ties=2, n_next=1))
        report.save(d)
        write_run(root, "20261111-164233-selftest-quick", selftest_result())
        code = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(PLUGIN_DIR)!r})
            from skilluminati import tools
            print(json.dumps([json.loads(tools.result({{}})), json.loads(tools.result({{"skill": "selftest"}})),
                              json.loads(tools.result({{"skill": "nope"}}))]))
        """)
        env = {**os.environ, "HERMES_HOME": str(self.home)}
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        latest, st, missing = json.loads(out.stdout)
        self.assertEqual(latest["run"], "20261111-171230-verdict-unslop", "the newest run by default")
        self.assertEqual(latest["report"], (d / "report.md").read_text().strip())
        self.assertIn("run about 1 more use.", latest["report"])
        self.assertIn("3 of 3 as expected", st["report"])
        self.assertFalse(missing["found"])

    def test_a_run_going_is_reported_with_its_call_count(self):
        guard = SimpleNamespace(used=42, cap=72)
        done = run._claim("test of 'unslop'", guard)
        try:
            out = json.loads(tools.result({}))
        finally:
            run._release(done)
        self.assertIn("still going: 42 of 72 calls", out["report"])

    def test_run_id_cannot_leave_the_runs_folder(self):
        self.assertIsNone(report.find_run(run="../../etc", runs_dir=self.home))
        out = json.loads(tools.result({"run": "../verdicts.json"}))
        self.assertFalse(out["found"])

    def test_tool_description_says_show_as_is(self):
        from skilluminati import schemas
        d = schemas.RESULT["description"]
        self.assertIn("exactly as returned", d)
        self.assertIn("Do not add a verdict", d)

    def test_last_command_prints_plain_lines(self):
        root = self.home / "plugin-data" / "skilluminati"
        d = write_run(root, "20261111-170708-verdict-unslop", verdict_result())
        commands.bind(None)
        out = commands.handle_skilluminati("last")
        self.assertTrue(out.startswith("unslop: Unproven. Too few uses to tell yet."), out)
        self.assertTrue(out.endswith(f"Folder: {d}"))


# ── Part 4: the page backend reads where the plugin writes ───────────────────

class BackendFolderTests(R.HomeCase):
    def test_backend_reads_the_folder_the_plugin_writes(self):
        host_dir = self.home / "plugin-data" / "skilluminati-host-answer"
        storage = types.ModuleType("plugins.plugin_storage")
        storage.plugin_data_dir = lambda name: (host_dir.mkdir(parents=True, exist_ok=True), host_dir)[1]
        with mock.patch.dict(sys.modules, {"plugins": types.ModuleType("plugins"), "plugins.plugin_storage": storage}):
            self.assertEqual(paths.data_dir(), host_dir, "the plugin asks the host's public plugin_data_dir")
            summary = {"skill": "unslop", "combined": {"verdict": "Unsure", "wins": 6, "losses": 1, "ties": 2,
                                                       "low": 0.49, "high": 0.97},
                       "stamp": {"date": "2026-11-11", "test_type": "single-call replay"}, "user_label": "Not sure yet"}
            run.store_verdict(summary, model="claude-opus-5-5[1m]", judge="claude-opus-5-5[1m]")
            write_run(host_dir, "20261111-164233-selftest-quick", selftest_result())
            from importlib import util
            spec = util.spec_from_file_location("_sb_api_test", PLUGIN_DIR / "dashboard" / "plugin_api.py")
            api = util.module_from_spec(spec)
            spec.loader.exec_module(api)
            sys.modules.pop("_skilluminati_pkg", None)
            for k in [k for k in sys.modules if k.startswith("_skilluminati_pkg.")]:
                sys.modules.pop(k)
            self.assertEqual(api.data_dir(), host_dir)
            out = api.read_verdicts()
        self.assertEqual([r["skill"] for r in out["runs"]], ["unslop"])
        st = out["selftest"]
        self.assertEqual((st["passed"], st["checked"], st["model"]), (3, 3, "claude-opus-5-5[1m]"))
        self.assertEqual([r["name"] for r in st["rows"]],
                         ["the Adept (helps)", "the Impostor (sabotages)", "the Idler (does nothing)"])
        api_src = (PLUGIN_DIR / "dashboard" / "plugin_api.py").read_text()
        self.assertNotIn("db82b795", api_src + (PLUGIN_DIR / "skilluminati" / "paths.py").read_text())
        self.assertNotIn("tui_gateway", api_src)

    def test_old_host_folder_is_copied_once_never_moved(self):
        old = self.home / "plugin-data" / "agent-plugin-skilluminati-00000000"
        write_run(old, "20261111-171230-verdict-unslop", verdict_result())
        (old / "verdicts.json").write_text('{"schema": 1, "runs": [], "aa": []}')
        paths._data_dir_override = self.home / "plugin-data" / "skilluminati"
        (paths._data_dir_override / "verdicts.json").parent.mkdir(parents=True)
        (paths._data_dir_override / "verdicts.json").write_text("NEWER")
        self.assertTrue(paths.migrate_from(old))
        new = paths._data_dir_override
        self.assertTrue((new / "runs" / "20261111-171230-verdict-unslop" / "result.json").is_file())
        self.assertEqual((new / "verdicts.json").read_text(), "NEWER", "an existing file is never overwritten")
        self.assertTrue((old / "verdicts.json").is_file(), "the old folder is left in place")
        self.assertFalse(paths.migrate_from(old), "the copy runs once")


# ── Part 5: honest numbers ───────────────────────────────────────────────────

#: The 9 tasks of the 11 Nov unslop runs (identical in both): stored system prompt and first message,
#: in characters (counted from runs/2026111117*/tasks.jsonl), the skill's SKILL.md size, and the
#: real totals of the two runs.
UNSLOP_SYSTEM = [66463, 48150, 48477, 49394, 49475, 49962, 49882, 18643, 18630]
UNSLOP_USER = [1372, 1982, 3735, 2209, 2154, 2143, 2209, 778, 461]
UNSLOP_SKILL_CHARS = 24047
REAL_TOTALS = {"17:07": 847_486, "17:12": 845_818}


class TokenEstimateTests(unittest.TestCase):
    def test_estimate_is_at_or_above_the_real_unslop_runs(self):
        tasks = [{"system_prompt": "x" * s, "user_turn": "y" * u} for s, u in zip(UNSLOP_SYSTEM, UNSLOP_USER)]
        est = run.verdict_tokens(tasks, 9, UNSLOP_SKILL_CHARS)
        for when, real in REAL_TOTALS.items():
            self.assertGreaterEqual(est, real, f"estimate {est:,} under the {when} run ({real:,})")
        self.assertLess(est, 1.5 * max(REAL_TOTALS.values()), f"estimate {est:,} is not wildly high")

    @unittest.skipUnless((REPO / "reports").is_dir(), "dev checkout only: reads stored run folders under ~/.hermes")
    def test_estimate_on_the_stored_tasks_when_present(self):
        root = Path.home() / ".hermes" / "plugin-data"
        found = sorted(root.glob("*skilluminati*/runs/20261111-17*-verdict-unslop/tasks.jsonl"))
        if not found:
            self.skipTest("the 11 Nov run folders are not on this machine")
        tasks = [json.loads(l) for l in found[0].read_text().splitlines() if l.strip()]
        est = run.verdict_tokens(tasks, 9, UNSLOP_SKILL_CHARS)
        self.assertGreaterEqual(est, max(REAL_TOTALS.values()))

    def test_minutes_estimate(self):
        # calls x (measured seconds a call + the pause after each), rounded up to whole minutes
        self.assertEqual(run.minutes_estimate(64, "verdict", run.VERDICT_WORKERS),
                         -(-int(64 * (run.SECONDS_PER_CALL["verdict"] + run.CALL_PAUSE_S)) // 60))
        self.assertEqual(run.minutes_estimate(108, "selftest", run.SELFTEST_WORKERS), 10)


class ModelInResultTests(R.HomeCase):
    def test_selftest_result_names_the_real_model(self):
        ctx = R.FakeCtx(R.ActiveLlm(), surface="cli", home=self.home)
        self.h(ctx, "selftest --yes")
        d = next((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        res = json.loads((d / "result.json").read_text())["run"]
        self.assertEqual(res["subject_model"], R.ACTIVE)
        self.assertEqual(res["judge_model"], R.ACTIVE)
        self.assertEqual(res["harm_judge"]["model"], R.ACTIVE)
        self.assertEqual(res["totals"]["by_model"], {R.ACTIVE: 108})

    def test_verdict_result_names_the_real_model(self):
        tv = R.VerdictTests("test_verdict_runs_and_is_stored_for_the_desktop_page")
        tv.home = self.home
        tv._skills()
        R._db(self.home / "state.db", 6)
        self.h(R.FakeCtx(R.JudgeAlwaysWith(), surface="cli", home=self.home), f"{R.SKILL} --yes")
        d = next((self.home / "plugin-data" / "skilluminati" / "runs").iterdir())
        res = json.loads((d / "result.json").read_text())
        self.assertEqual(res["subject_model"], R.ACTIVE)
        self.assertEqual(res["judge_model"], R.ACTIVE)
        self.assertEqual(res["totals"]["by_model"], {R.ACTIVE: 40})
        self.assertEqual(res["per_skill"][R.SKILL]["stamp"]["judge"], R.ACTIVE)


class DeclarationTests(unittest.TestCase):
    def test_both_tools_declared(self):
        y = (PLUGIN_DIR / "plugin.yaml").read_text()
        self.assertIn("  - skilluminati_estimate\n  - skilluminati_result", y)

    @unittest.skipUnless((REPO / "submission").is_dir(), "dev checkout only: the catalogue entry is not shipped")
    def test_both_tools_declared_in_the_catalogue_entry(self):
        c = (REPO / "submission" / "plugin-catalog-skilluminati.yaml").read_text()
        self.assertIn("    - skilluminati_estimate\n    - skilluminati_result", c)

    def test_desktop_half_listens_and_submits(self):
        js = (PLUGIN_DIR / "desktop" / "plugin.js").read_text()
        self.assertIn("ctx.onEvent(RUN_DONE, onRunDone)", js)
        self.assertIn("'plugin.skilluminati.run.done'", js)
        self.assertIn("host.composer.submit(sessionId, prompt)", js)
        self.assertNotIn("inject_message", js)


class PageFixTests(R.HomeCase):
    """The author's 19:17 screenshot: "Run 1 more tasks", "No A/A rows" after a run with A/A, raw timestamp."""

    def test_one_more_task_is_singular_in_the_stored_recommendation(self):
        from skilluminati import recommend as rec
        for n, want in ((1, "Run about 1 more task"), (2, "Run about 2 more tasks")):
            step = rec.next_step("Not sure yet", rec.WEAK, False, n)
            self.assertEqual((step["key"], step["text"]), (rec.STEP_RUN_MORE, want))

    def test_verdict_run_stores_its_aa_row(self):
        summary = {"skill": "unslop", "combined": {"verdict": "Unsure", "wins": 6, "losses": 1, "ties": 2,
                                                   "low": 0.49, "high": 0.97},
                   "stamp": {"date": "2026-11-11", "test_type": "single-call replay"}, "user_label": "Not sure yet"}
        aa = {"cell": "aa", "model": None, "wins": 2, "losses": 4, "ties": 3, "low": 0.0968, "high": 0.7,
              "firm": 6, "pairs": [{"x": "not stored"}]}
        run.store_verdict(summary, model="m1", judge="m1", aa=aa)
        run.store_verdict(summary, model="m1", judge="m1", aa=dict(aa, wins=3))
        data = json.loads(paths.verdicts_path().read_text())
        self.assertEqual(data["aa"], [{"model": "m1", "judge": "m1", "wins": 3, "losses": 4, "ties": 3, "low": 0.0968,
                                       "high": 0.7, "firm": 7, "pooled": 10, "date": "2026-11-11"}],
                         "one row per model and judge, the latest; no run id, so this run's own firm picks")
        run.store_verdict(summary, model="m1", judge="m1", aa=None)
        self.assertEqual(len(json.loads(paths.verdicts_path().read_text())["aa"]), 1, "a run without A/A keeps the row")

    def test_page_shows_aa_without_a_verdict_and_a_readable_time(self):
        js = (PLUGIN_DIR / "desktop" / "plugin.js").read_text()
        api = (PLUGIN_DIR / "dashboard" / "plugin_api.py").read_text()
        # W4 page: the noise check is one plain sentence in "Show the working"; dates go through when().
        self.assertIn("w.noise.lines", js)  # F11: backend builds the same-vs-same lines from copy.py
        self.assertNotIn("A/A", js)
        self.assertNotIn("verdict", api.split("AA_KEYS = ")[1].split("\n")[0], "the backend serves no A/A verdict")
        self.assertEqual(tuple(run.AA_STORED_KEYS),
                         ("model", "judge", "wins", "losses", "ties", "low", "high", "firm", "pooled", "date"))
        self.assertIn("when(r.date)", js)


REAL_429 = ("UsageLimit: Incomplete upstream response (first upstream attempt: status 429, capture incomplete, "
            "native retries denied: 0, upstream said: You've hit your session limit")


class Round3Tests(R.HomeCase):
    """UX-BRIEF Round 3: from the author's 19:34 run that hit the usage limit."""

    def summary(self, wins, n_next=29, unused=None):
        rec = {"headline": {"label": "Not sure yet", "text": "Still watching"}, "confidence": {"text": "Weak"},
               "flags": [], "next_step": {"key": "run_more", "text": f"Run about {n_next} more tasks", "n": n_next}}
        s = {"skill": "unslop", "user_label": "Not sure yet", "recommendation": rec,
             "combined": {"verdict": "Unsure", "wins": wins, "losses": 1, "ties": 2, "low": 0.4, "high": 0.9,
                          "tasks_to_settle": n_next},
             "stamp": {"date": "2026-11-11", "test_type": "single-call replay"}}
        if unused is not None:
            s["unused_tasks"] = unused
        return s

    def rows(self):
        return json.loads(paths.verdicts_path().read_text())["runs"]

    # 1. a partial run never replaces a finished verdict
    def test_partial_run_keeps_the_finished_verdict(self):
        run.store_verdict(self.summary(6), model="m1", judge="m1")
        run.store_verdict(self.summary(2), model="m1", judge="m1", partial=True)
        self.assertEqual([r["wins"] for r in self.rows()], [6])

    def test_partial_run_with_nothing_stored_is_stored_and_flagged(self):
        run.store_verdict(self.summary(2), model="m1", judge="m1", partial=True)
        [row] = self.rows()
        self.assertEqual(row["wins"], 2)
        self.assertIn({"key": "partial", "text": "Partial: the run stopped early"}, row["recommendation"]["flags"])
        run.store_verdict(self.summary(5), model="m1", judge="m1")  # a finished run replaces it
        [row] = self.rows()
        self.assertEqual((row["wins"], row["recommendation"]["flags"]), (5, []))

    def test_partial_report_says_so(self):
        md = report.verdict_markdown(dict(report._verdict_facts(
            {"per_skill": {"unslop": verdict_result()["per_skill"]["unslop"]},
             "stopped_early": {"reason": "StopRequested", "error": REAL_429}}, Path("x"), "unslop")))
        self.assertIn("\nPartial: the run stopped early.\n", md)

    # 2. plain stop reason
    def test_usage_limit_is_one_plain_line(self):
        stop = {"reason": "StopRequested", "error": REAL_429}
        self.assertEqual(report.stop_text(stop),
                         "Stopped early: your provider's usage limit was reached. This is a partial result.")
        self.assertEqual(report.stop_text({"reason": "UsageLimit", "error": "429. Your limit resets at 9 PM."}),
                         "Stopped early: your provider's usage limit was reached (it resets at 9pm). "
                         "This is a partial result.")

    def test_other_stops_are_short(self):
        long_err = "ConnectionError: " + "the upstream host closed the connection without a reply " * 4
        text = report.stop_text({"reason": "unexpected ConnectionError", "error": long_err})
        reason = text[len("Stopped early: "):-1]
        self.assertLess(len(reason), 80)
        self.assertNotIn("ConnectionError", text)

    def test_popup_says_why_it_stopped(self):
        d = Path(self.home) / "run1"
        d.mkdir()
        res = verdict_result()
        res["stopped_early"] = {"reason": "StopRequested", "error": REAL_429}
        (d / "result.json").write_text(json.dumps(res))
        line = report.headline(d)
        self.assertEqual(line, "Skilluminati: unslop stopped early: your provider's usage limit was reached.")
        self.assertLess(len(line), 120)

    # 3. the question is four short lines
    # 4. no number when the history is used up
    def test_history_used_up_gives_no_number(self):
        run.store_verdict(self.summary(6, n_next=1, unused=0), model="m1", judge="m1")
        [row] = self.rows()
        self.assertIsNone(row["tasks_to_settle"], "page column shows a dash")
        self.assertEqual(row["recommendation"]["next_step"]["text"],
                         "Not enough history to call it. Use the skill on more work, then run this again.")
        res = verdict_result(n_next=1)
        res["per_skill"]["unslop"]["unused_tasks"] = 0
        md = report.verdict_markdown(report._verdict_facts(res, Path("x"), "unslop"))
        self.assertIn("Next: not enough history to call it. Use the skill on more work, then run this again.",
                      md)

    def test_unused_tasks_left_gives_about_n(self):
        run.store_verdict(self.summary(6, n_next=12, unused=16), model="m1", judge="m1")
        [row] = self.rows()
        self.assertEqual((row["tasks_to_settle"], row["recommendation"]["next_step"]["text"]),
                         (12, "Run about 12 more tasks"))


if __name__ == "__main__":
    unittest.main()
