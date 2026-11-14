"""Live test 12 Nov: a skill that ships with Hermes (not in ~/.hermes/skills) failed with
FileNotFoundError. skill_home must search every folder Hermes lists, in Hermes's order."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import test_run as R  # noqa: F401  (puts plugin/ on sys.path)
from skilluminati import realrun  # noqa: E402


def _skill(root: Path, cat: str, name: str) -> None:
    d = root / cat / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\nbody\n", encoding="utf-8")


class SkillHomeTests(unittest.TestCase):
    def test_finds_a_skill_that_ships_with_hermes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            mine_dir, bundled = Path(a), Path(b)
            _skill(mine_dir, "notes", "mine-only")
            _skill(bundled, "software-development", "shipped")
            fake = types.ModuleType("agent.skill_utils")
            fake.get_all_skills_dirs = lambda: [mine_dir, bundled]
            with mock.patch.dict(sys.modules, {"agent": types.ModuleType("agent"), "agent.skill_utils": fake}):
                self.assertEqual(realrun.skill_home("shipped"), bundled)
                self.assertEqual(realrun.skill_home("mine-only"), mine_dir)
                self.assertIsNotNone(realrun.find_skill_file(realrun.skill_home("shipped"), "shipped"))


if __name__ == "__main__":
    unittest.main()


class NewestFinishedRunTests(unittest.TestCase):
    """Live test 12 Nov: a run stopped by a plugin reload hid the finished verdict before it."""

    def test_by_name_skips_a_stopped_run(self):
        import json
        from skilluminati import report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "20261112-023124-verdict-systematic-debugging"
            stopped = root / "20261112-032212-verdict-systematic-debugging"
            for d, res in ((good, {}), (stopped, {"stopped_early": {"reason": "unloaded"}})):
                d.mkdir()
                (d / "result.json").write_text(json.dumps(res))
            self.assertEqual(report.find_run(skill="systematic-debugging", runs_dir=root), good)
            self.assertEqual(report.find_run(run=stopped.name, runs_dir=root), stopped, "by id still works")
            (good / "result.json").unlink()
            self.assertEqual(report.find_run(skill="systematic-debugging", runs_dir=root), stopped)
