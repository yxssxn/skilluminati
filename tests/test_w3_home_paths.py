"""In plain words: a path inside someone's home folder names them (/Users/alice/...). Before any
task text is sent to a model or saved in a run folder, the user part of a home path becomes "~",
so the path still makes sense but the account name is gone.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import unittest

from tests import test_run as R  # noqa: F401,E402  (puts plugin/ on sys.path)

from skilluminati import redact  # noqa: E402


class HomePaths(unittest.TestCase):
    def check(self, raw: str, want: str) -> None:
        out, counts = redact.redact(raw, names=[])
        self.assertEqual(out, want)
        self.assertGreaterEqual(counts["PATH"], 1)

    def test_mac(self) -> None:
        self.check("see /Users/alice/dev/app/main.py now", "see ~/dev/app/main.py now")

    def test_linux(self) -> None:
        self.check("cd /home/bob.smith/.hermes && ls", "cd ~/.hermes && ls")

    def test_windows(self) -> None:
        self.check(r"open C:\Users\Carol\Documents\x.txt", r"open ~\Documents\x.txt")

    def test_bare_home(self) -> None:
        self.check("HOME=/Users/alice", "HOME=~")

    def test_shared_folders_untouched(self) -> None:
        for raw in ("/Users/Shared/data", "/home/linuxbrew/bin", "/usr/local/bin", "/Users/"):
            out, counts = redact.redact(raw, names=[])
            self.assertEqual(out, raw)
            self.assertEqual(counts["PATH"], 0)

    def test_gate2_edges(self) -> None:
        self.check("cwd=/Users/alice]", "cwd=~]")
        self.check("<cwd>/Users/alice</cwd>", "<cwd>~</cwd>")
        self.check("{/Users/alice}", "{~}")
        self.check("/Users/alice|x", "~|x")
        self.check("/Users/José/x", "~/x")

    def test_urls_untouched(self) -> None:
        for raw in ("https://example.com/home/dashboard/x", "see example.com/Users/bob/x"):
            out, counts = redact.redact(raw, names=[])
            self.assertEqual(out, raw)
            self.assertEqual(counts["PATH"], 0)

    def test_counts_merge(self) -> None:
        self.assertIn("PATH", redact.PLACEHOLDERS)


if __name__ == "__main__":
    unittest.main()
