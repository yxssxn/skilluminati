"""In plain words: the judge guide says a judge model must be on the plugin's model allow-list
when one is kept (plugins.entries.skilluminati.llm.allowed_models), names the error Hermes gives
otherwise, and shows a YAML example where the judge model is on the list. Reads the READMEs only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, title: str) -> str:
    start = text.index(f"## {title}")
    end = text.find("\n## ", start + 3)
    return text[start:end if end > 0 else None]


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.S)


class JudgeAllowList(unittest.TestCase):
    def test_plugin_readme_choosing_a_judge(self):
        sec = _section((ROOT / "plugin" / "README.md").read_text(encoding="utf-8"), "Choosing a judge")
        self.assertIn("plugins.entries.skilluminati.llm.allowed_models", sec)
        self.assertIn("PluginLlmTrustError", sec)
        blocks = [b for b in _yaml_blocks(sec) if "allowed_models" in b]
        self.assertEqual(len(blocks), 1)
        b = blocks[0]
        judge = re.search(r"judge:\n\s+provider: (\S+)\n\s+model: (\S+)", b)
        listed = re.search(r"allowed_models:\n((?:\s+- \S+\n)+)", b)
        assert judge is not None and listed is not None, b
        self.assertIn(f"- {judge.group(2)}", listed.group(1))
        self.assertRegex(b, r"\n      llm:[^\n]*\n(?:        .*\n)*        allowed_models:")
        for word in ("\u2014", "\u2013"):
            self.assertNotIn(word, sec)

    def test_main_readme_points_at_it(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("allowed_models", text)


if __name__ == "__main__":
    unittest.main()
