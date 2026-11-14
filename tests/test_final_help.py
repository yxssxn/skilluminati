"""In plain words: `/skilluminati help` lists every command the handler accepts, including `last`.

Stdlib only, no network, no model calls."""

from __future__ import annotations

import unittest

from tests import test_run as R  # noqa: F401,E402  (puts plugin/ on sys.path)

from skilluminati import commands, copy as sb_copy  # noqa: E402


class HelpListsEveryCommand(unittest.TestCase):
    def test_help_names_last_stop_selftest_and_skill(self) -> None:
        for word in ("/skilluminati <skill>", "/skilluminati last", "/skilluminati stop", "/skilluminati selftest"):
            self.assertIn(word, sb_copy.HELP)

    def test_help_command_returns_it(self) -> None:
        self.assertEqual(commands.handle_skilluminati("help"), sb_copy.HELP)


if __name__ == "__main__":
    unittest.main()
