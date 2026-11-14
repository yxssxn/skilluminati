"""In plain words: where a skill lost, the report and the page say why, in the Owl's own one-line
reasons (wave 2, item 2; F12). The reasons are already stored in each run's pairs.jsonl (the
judge's order texts, JSON with "reason"); nothing new is asked of a model.

Rules: only tasks whose pooled outcome is Worse, only pairs where the judge agreed with itself, at
most 3 reasons, shortest first, each cut to 160 characters. None: nothing shown. The folder report
(report.md, the result tool) shows "Where it lost, the Owl said:" and the reasons; the chat
``/skilluminati last`` adds at most one line, "Why it lost: <reason>". The page shows them as plain
text. The fixture is built in a temp dir: synthetic task hashes and made-up reasons, no task text.
"""

from __future__ import annotations

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

report = importlib.import_module("plugin.skilluminati.report")
copy = importlib.import_module("plugin.skilluminati.copy")

SKILL = "s"
RUN_A = "20261114-100000-verdict-s"
RUN_B = "20261114-110000-verdict-s"
LONG = "The answer without the skill " + "kept to the point and " * 12 + "won."
SCRIPT = "Answer 2 was clearer <script>alert(1)</script> & shorter."


def _order(first, winner, reason):
    raw = "1" if (first == winner) else "2"
    return {"first": first, "ok": True, "raw_winner": raw, "winner": winner,
            "text": json.dumps({"winner": raw, "reason": reason})}


def _pair(task, outcome, reasons=("r1", "r2"), agreed=True, cell=SKILL):
    win = {"win": "x", "loss": "y", "tie": "tie"}[outcome]
    orders = [_order("x", win, reasons[0]), _order("y", win, reasons[1])]
    return {"cell": cell, "skill": SKILL, "task_hash": task, "outcome": outcome,
            "judge": {"outcome": win, "agreed": agreed, "orders": orders}}


def _write(d: Path, rows):
    d.mkdir(parents=True, exist_ok=True)
    with (d / "pairs.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _result(w, l, t):
    return {"per_skill": {SKILL: {"skill": SKILL, "combined": {"wins": w, "losses": l, "ties": t},
                                  "n_tasks": w + l + t, "user_label": "Gets in the way"}},
            "totals": {"calls": 10, "total_tokens": 100}}


def build_fixture(root: Path, *, pooled=True) -> Path:
    """Two runs of one skill. Pooled per task: t1 Worse (lost twice), t2 Same (lost, then won),
    t3 Worse but the judge split, t4 Worse with a long reason, t5 Worse with markup, t6 Better,
    t7 Worse with a short reason. The same-vs-same cell lost too and must never show."""
    runs = root / "runs"
    _write(runs / RUN_A, [
        _pair("t1", "loss", ("Without it the answer named the real cause first.", "Without it, the cause came first.")),
        _pair("t2", "loss", ("Lost once, won later.", "Lost once, won later, b.")),
    ])
    _write(runs / RUN_B, [
        _pair("t1", "loss", ("A tighter answer without the skill.", "The answer without the skill was more direct.")),
        _pair("t2", "win", ("x", "y")),
        _pair("t3", "loss", ("Split judge, never shown.", "Split judge, never shown either."), agreed=False),
        _pair("t4", "loss", (LONG, LONG + " Again.")),
        _pair("t5", "loss", (SCRIPT, SCRIPT + " Twice.")),
        _pair("t6", "win", ("Won.", "Won.")),
        _pair("t7", "loss", ("Shorter and right.", "Shorter, and right.")),
        _pair("a1", "loss", ("Same-vs-same noise.", "Same-vs-same noise."), cell="aa"),
    ])
    (runs / RUN_A / "result.json").write_text(json.dumps(_result(0, 2, 0)), encoding="utf-8")
    (runs / RUN_B / "result.json").write_text(json.dumps(_result(2, 5, 0)), encoding="utf-8")
    if pooled:
        pool_runs = []
        for run in (RUN_A, RUN_B):
            pairs = [json.loads(x) for x in (runs / run / "pairs.jsonl").read_text().splitlines()]
            pool_runs.append({"run": run, "pairs": [{"task_hash": p["task_hash"], "outcome": p["outcome"]}
                                                    for p in pairs if p["cell"] == SKILL]})
        (root / "pools").mkdir()
        (root / "pools" / f"{SKILL}.json").write_text(json.dumps(
            {"schema": 1, "skill": SKILL, "pools": [{"skill_sha256": "abc", "model": "m", "judge": "m", "runs": pool_runs}]}),
            encoding="utf-8")
    return runs / RUN_B


class LostReasons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pooled_losses_only_agreed_shortest_first_three(self):
        got = report.lost_reasons(build_fixture(self.root), SKILL)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0], "Shorter and right.")
        self.assertEqual(got, sorted(got, key=len))
        joined = " ".join(got)
        self.assertNotIn("Split judge", joined)       # the judge did not agree with itself
        self.assertNotIn("Lost once", joined)         # t2 is Same once pooled
        self.assertNotIn("Same-vs-same", joined)      # the noise cell is not the skill
        self.assertNotIn("Won.", joined)
        self.assertTrue(all(len(r) <= report.LOST_REASON_MAX for r in got))

    def test_a_long_reason_is_cut_to_160(self):
        d = build_fixture(self.root)
        got = report.lost_reasons(d, SKILL, limit=10)
        cut = [r for r in got if r.startswith("The answer without the skill kept")]
        self.assertEqual(len(cut), 1)
        self.assertLessEqual(len(cut[0]), 160)
        self.assertGreater(len(cut[0]), 150)
        self.assertTrue(cut[0].endswith("..."))

    def test_without_a_pool_the_run_alone_decides(self):
        d = build_fixture(self.root, pooled=False)
        got = report.lost_reasons(d, SKILL, limit=10)
        self.assertIn("A tighter answer without the skill.", got)
        self.assertNotIn("Lost once, won later.", got)  # not lost in this run

    def test_none_shows_nothing(self):
        runs = self.root / "runs"
        _write(runs / RUN_B, [_pair("t6", "win", ("Won.", "Won.")),
                              _pair("t3", "loss", ("Split.", "Split."), agreed=False)])
        (runs / RUN_B / "result.json").write_text(json.dumps(_result(1, 1, 0)), encoding="utf-8")
        self.assertEqual(report.lost_reasons(runs / RUN_B, SKILL), [])
        for plain in (False, True):
            text = report.build(runs / RUN_B, plain=plain)
            self.assertNotIn(copy.LOST_LABEL, text)
            self.assertNotIn("Why it lost", text)

    def test_unreadable_pairs_show_nothing(self):
        runs = self.root / "runs"
        (runs / RUN_B).mkdir(parents=True)
        (runs / RUN_B / "pairs.jsonl").write_text("not json\n{\"cell\": 3}\n", encoding="utf-8")
        (runs / RUN_B / "result.json").write_text(json.dumps(_result(0, 1, 0)), encoding="utf-8")
        self.assertEqual(report.lost_reasons(runs / RUN_B, SKILL), [])
        self.assertIsNotNone(report.build(runs / RUN_B))


class InTheReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = build_fixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_report_shows_the_label_and_three_reasons(self):
        lines = report.build(self.d).splitlines()
        at = lines.index(copy.LOST_LABEL)
        shown = [l[2:] for l in lines[at + 1:at + 4]]
        self.assertTrue(all(l.startswith("- ") for l in lines[at + 1:at + 4]))
        self.assertEqual(shown, report.lost_reasons(self.d, SKILL))
        self.assertFalse(lines[at + 4].startswith("- "))

    def test_chat_last_adds_one_line_only(self):
        text = report.terminal(self.d)
        self.assertNotIn(copy.LOST_LABEL, text)
        hits = [l for l in text.splitlines() if l.startswith("Why it lost: ")]
        self.assertEqual(hits, ["Why it lost: Shorter and right."])

    def test_page_card_carries_the_reasons(self):
        row = {"skill": SKILL, "run": RUN_B, "wins": 2, "losses": 5, "ties": 0}
        got = report.page_result(row, self.d.parent)
        self.assertEqual(got["working"]["lost_reasons"], report.lost_reasons(self.d, SKILL))

    def test_page_card_with_no_run_has_none(self):
        got = report.page_result({"skill": SKILL, "wins": 1, "losses": 0, "ties": 0})
        self.assertEqual(got["working"]["lost_reasons"], [])

    def test_label_is_in_the_page_copy(self):
        self.assertEqual(copy.page_copy()["lost_label"], "Where it lost, the Owl said:")


class EscapedOnThePage(unittest.TestCase):
    def test_markup_in_a_reason_stays_plain_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = build_fixture(Path(tmp))
            row = {"skill": SKILL, "run": RUN_B, "wins": 2, "losses": 5, "ties": 0}
            reasons = report.page_result(row, d.parent)["working"]["lost_reasons"] + \
                report.lost_reasons(d, SKILL, limit=10)
        hit = [r for r in reasons if "<script>" in r]
        self.assertTrue(hit, "the fixture reason with markup reaches the payload")
        # The payload is JSON data, not HTML: the page renders it as a React text child.
        payload = json.dumps({"lost_reasons": hit})
        self.assertEqual(json.loads(payload)["lost_reasons"], hit)
        js = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
        for bad in ("dangerouslySetInnerHTML", "innerHTML", "__html", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(bad, js)
        start = js.index("function Working(")
        body = js[start:js.find("\nfunction ", start + 1)]
        self.assertIn("w.lost_reasons", body)
        self.assertIn("c.lost_label", body)
        # each reason goes through str() into a text child, never into markup
        self.assertRegex(body, r"lost_reasons[^\n]*str\(")


if __name__ == "__main__":
    unittest.main()
