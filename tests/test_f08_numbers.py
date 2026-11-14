"""F08: scripts/skilluminati_numbers.py prints what v0.2 would show for a runs folder.

A synthetic runs folder in a temp dir: two finished runs of one made-up skill on the same tasks,
one run that stopped early and one folder with no result. No state.db, no real data.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "skilluminati_numbers.py"
MODEL = "anthropic/demo-model-1[1m]"


def load_script():
    spec = importlib.util.spec_from_file_location("skilluminati_numbers", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pair(h, outcome, tool_driven=False, empty=False):
    arms = {"with": {"ok": not empty, "error_type": "EmptyAnswer" if empty else ""},
            "without": {"ok": True, "error_type": ""}}
    return {"cell": "demo-skill", "task_hash": h, "outcome": outcome, "tool_driven": tool_driven, "arms": arms}


def write_run(root, name, outcomes, aa, *, stopped=None, tool_driven=0):
    d = root / name
    d.mkdir(parents=True)
    pairs = [pair(f"t{i:02d}", o, tool_driven=i < tool_driven) for i, o in enumerate(outcomes)]
    res = {"subject_model": MODEL, "judge_model": MODEL, "source": "history", "stopped_early": stopped,
           "per_skill": {"demo-skill": {"pairs": pairs, "judge_stats": {}}},
           "aa": {"pairs": [{"outcome": o} for o in aa]}}
    (d / "result.json").write_text(json.dumps(res), encoding="utf-8")


def run_script(runs, *extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = load_script().main(["--runs", str(runs), "--no-state-db", *extra])
    return rc, out.getvalue()


class NumbersScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / "runs"
        # 12 tasks. Run 1: 6 better, 2 worse, 4 same. Run 2 disagrees on 3 tasks.
        write_run(self.runs, "20260101-000000-verdict-demo-skill", ["win"] * 6 + ["loss"] * 2 + ["tie"] * 4,
                  ["win", "tie", "tie", "loss"])
        write_run(self.runs, "20260102-000000-verdict-demo-skill",
                  ["win"] * 5 + ["loss"] + ["loss"] * 2 + ["win", "win"] + ["tie"] * 2,
                  ["tie", "tie", "win", "tie"])
        write_run(self.runs, "20260103-000000-verdict-demo-skill", ["loss"] * 12, ["win"] * 4,
                  stopped={"reason": "StopRequested"})
        (self.runs / "20260104-000000-selftest-quick").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pools_two_finished_runs_and_skips_the_rest(self):
        before = sorted(str(p) for p in self.runs.rglob("*"))
        rc, out = run_script(self.runs)
        self.assertEqual(rc, 0)
        self.assertIn("finished verdict runs: 2 | skipped: 2", out)
        self.assertIn("20260103-000000-verdict-demo-skill: stopped early", out)
        self.assertIn("runs 2 | tasks 12 | 12 tasks, 2 runs", out)
        # per task, sign of better minus worse: 7 better, 3 same, 2 worse
        self.assertIn("pooled Better / Same / Worse: 7 / 3 / 2", out)
        self.assertIn("shown: Enlightened", out)
        self.assertIn("model demo-model-1 | judge demo-model-1", out)
        self.assertIn("Same-model judge", out)
        self.assertEqual(out.count("this run alone: "), 2)
        self.assertEqual(before, sorted(str(p) for p in self.runs.rglob("*")), "runs folder changed")

    def test_same_vs_same_figure(self):
        _, out = run_script(self.runs)
        # the stopped run's four pairs are not pooled
        self.assertIn("runs 2 | first better 2, second better 1, same 5", out)
        self.assertIn("Same-vs-same: a firm pick 3 of 8 times (38%).", out)

    def test_tool_driven_flag_and_cap(self):
        write_run(self.runs, "20260105-000000-verdict-demo-skill", ["win"] * 12, [], tool_driven=7)
        _, out = run_script(self.runs)
        self.assertIn("tool-driven: 7 of 36 tested uses", out)
        self.assertIn("flag off", out)
        tmp2 = Path(self.tmp.name) / "runs2"
        write_run(tmp2, "20260105-000000-verdict-demo-skill", ["win"] * 12, [], tool_driven=7)
        _, out = run_script(tmp2)
        self.assertIn("tool-driven: 7 of 12 tested uses", out)
        self.assertIn("flag on", out)
        self.assertIn("(held at Plausible: tool-driven)", out)

    def test_single_run_line_prints_the_rule_before_the_cap(self):
        # review-final S7: 12 of 12 better is Probable by the rule; the tool flag holds it at Plausible
        tmp2 = Path(self.tmp.name) / "runs3"
        write_run(tmp2, "20260105-000000-verdict-demo-skill", ["win"] * 12, [], tool_driven=7)
        _, out = run_script(tmp2)
        run_line = next(x for x in out.splitlines() if "this run alone: " in x)
        self.assertIn("this run alone: Enlightened (Plausible)", run_line)
        self.assertIn("(rule before any cap: helps, strong)", run_line)

    def test_missing_runs_folder(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = load_script().main(["--runs", str(Path(self.tmp.name) / "nope"), "--no-state-db"])
        self.assertEqual(rc, 2)

    def test_script_names_no_private_paths(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for bad in ("/" + "Users/", "yxs" + "sxn", "plugin-data/skilluminati/runs/2026"):
            self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
