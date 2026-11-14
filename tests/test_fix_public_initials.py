"""In plain words: the public test files carry no author initials (fix batch S10). The pattern is
built from parts so this file passes its own check. Stdlib only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INITIALS = re.compile(r"\b" + "Y" + "G" + r"\b")


class NoInitials(unittest.TestCase):
    def test_public_files_have_none(self):
        listed = [ln.strip() for ln in (ROOT / "tests" / "PUBLIC.txt").read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
        self.assertTrue(listed)
        hits = [f"{name}:{i}" for name in listed for i, line in
                enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1) if INITIALS.search(line)]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
