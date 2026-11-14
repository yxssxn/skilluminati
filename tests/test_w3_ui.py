"""W3 UI: the Desktop page and the dashboard manifest.

In plain words: these checks pin tonight's page fixes. The dashboard manifest hides its tab (the
plugin has no dashboard script, so a tab would only show "Could not load this plugin's script"),
and the Desktop page keeps the rules the reviews asked for.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))

from skilluminati import copy  # noqa: E402

JS = (ROOT / "plugin" / "desktop" / "plugin.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "plugin" / "dashboard" / "manifest.json").read_text(encoding="utf-8"))


def _function(name: str) -> str:
    start = JS.index(f"function {name}(")
    nxt = JS.find("\nfunction ", start + 1)
    return JS[start:nxt if nxt != -1 else len(JS)]


class ManifestTests(unittest.TestCase):
    def test_dashboard_tab_is_hidden(self):
        # hermes_cli/web_server_dashboard.py reads tab.hidden; without it Hermes adds a /skilluminati
        # tab that loads dist/index.js, which this plugin does not ship.
        self.assertEqual(MANIFEST.get("tab"), {"hidden": True})
        self.assertEqual(MANIFEST.get("api"), "plugin_api.py")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ParseTests(unittest.TestCase):
    def test_plugin_js_parses(self):
        out = subprocess.run(["node", "--check", str(ROOT / "plugin" / "desktop" / "plugin.js")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class LeanMeaningTests(unittest.TestCase):
    def test_plausible_meaning_is_a_lean(self):
        page = copy.page_copy()
        for k in ("helps", "hurts"):
            self.assertNotIn("clearly", page["verdict_meaning_lean"][k])
        self.assertIn("r.strength === 'likely'", _function("meaning"))
        self.assertIn("meaning(r, c)", _function("Working"))
        self.assertIn("meaning(r, c)", _function("VerdictWord"))


class SummaryTests(unittest.TestCase):
    def test_summary_splits_by_strength(self):
        body = _function("Summary")
        self.assertIn("['strong', 'likely', '']", body)
        self.assertIn("c.summary_strength", body)
        self.assertEqual(copy.SUMMARY_STRENGTH.format(part="2 Enlightened", word="Plausible"),
                         "2 Enlightened (Plausible)")


class SameJudgeOnceTests(unittest.TestCase):
    def test_self_judged_once_when_every_result_shares_it(self):
        page = _function("Page")
        self.assertIn("results.every((r) => Boolean(r.same_judge))", page)
        self.assertIn("jsx(SameJudgeNote", page)
        self.assertIn("r.same_judge && !oneJudge", _function("ResultCell"))
        self.assertTrue(copy.SAME_JUDGE_NOTE.startswith(copy.SAME_JUDGE_TAG))


class ResultColumnTests(unittest.TestCase):
    def test_result_column_is_wide_enough(self):
        m = re.search(r"gridTemplateColumns: 'minmax\(0, 1fr\) 96px 96px (\d+)px 96px'", JS)
        assert m is not None
        self.assertGreaterEqual(int(m.group(1)), 280)


class ContrastTests(unittest.TestCase):
    def test_small_tags_use_the_secondary_text_colour(self):
        for name in ("Qualifier", "SameJudge", "RowTag", "Counts"):
            body = _function(name)
            self.assertIn("...SECONDARY", body, name)
            self.assertNotIn("...TERTIARY", body, name)
        self.assertIn("style: SECONDARY, children: str(c.not_tested)", _function("ResultCell"))
        self.assertNotIn("TERTIARY", JS)


class NoEnglishInJsTests(unittest.TestCase):
    def test_strings_moved_to_copy(self):
        for s in ("'Open page'", "'Stop'", "'Show in chat'", "your result is ready", "that chat is busy",
                  "'Could not load the page data'", "} of ${"):
            self.assertNotIn(s, JS, s)
        page = copy.page_copy()
        for k in ("strip_open", "strip_stop", "run_done_ready", "run_done_show", "run_done_open",
                  "run_done_busy", "running_count"):
            self.assertTrue(page[k], k)


class StopAsksFirstTests(unittest.TestCase):
    def test_stop_asks_before_it_stops(self):
        strip = _function("ChatStrip")
        self.assertIn("link(c.strip_stop, askStop)", strip)
        self.assertEqual(JS.count("'/skilluminati stop'"), 1)
        yes = next(l for l in strip.splitlines() if "'/skilluminati stop'" in l)
        self.assertIn("c.strip_stop_yes", yes)
        self.assertIn("stopAsk.set(true)", _function("askStop"))


class ShowInChatPromptTests(unittest.TestCase):
    def test_event_prompt_is_ignored(self):
        self.assertNotIn("p.prompt", JS)
        self.assertIn("SKILL_NAME.test(p.skill)", _function("payloadOf"))
        self.assertIn("c.run_done_prompt", _function("resultPrompt"))
        self.assertEqual(copy.RUN_DONE_PROMPT.format(what="unslop"),
                         "Show my Skilluminati result for unslop (skilluminati_result tool).")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_skill_name_rule(self):
        m = re.search(r"const SKILL_NAME = (/.+/)\n", JS)
        assert m is not None
        js = (f"const r = {m.group(1)}; for (const s of ['unslop', 'frontend-design', 'a.b_c']) if (!r.test(s)) process.exit(1);"
              " for (const s of ['', 'x y', 'a\\nIgnore all', '-x', 'a'.repeat(65)]) if (r.test(s)) process.exit(2);")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
