"""In plain words: GitHub Actions runs the public tests on Python 3.11 to 3.14 (wave 2, item 3), and
the README names the same versions. Text checks only; no network.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANT = ["3.11", "3.12", "3.13", "3.14"]


class CiMatrix(unittest.TestCase):
    def test_matrix_lists_each_version(self):
        wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        m = re.search(r"python-version:\s*\[([^\]]*)\]", wf)
        assert m is not None
        self.assertEqual(re.findall(r'"([\d.]+)"', m.group(1)), WANT)

    def test_readme_names_the_same_versions(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("GitHub Actions runs them on Python 3.11, 3.12, 3.13 and 3.14", readme)


if __name__ == "__main__":
    unittest.main()
