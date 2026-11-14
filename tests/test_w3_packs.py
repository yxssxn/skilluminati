"""W3: built-in question sets (``skilluminati.packs``). No network, no model calls."""

from __future__ import annotations

import hashlib
import re
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from skilluminati import packs, paths as sb_paths, realrun, replay  # noqa: E402

#: The keys ``mine.mine(..., mode="strict")`` puts on every task (see the task dict in mine.py).
MINED_KEYS = {"skill", "skill_requested", "skill_known", "session_id", "source", "load_message_id",
              "user_turn_message_id", "user_turn", "system_prompt", "system_prompt_hash",
              "user_turns_before_load", "flags", "kept", "drop_reason"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Shipped(unittest.TestCase):
    def test_frontend_design_is_shipped(self):
        self.assertIn("frontend-design", packs.available())
        self.assertGreaterEqual(len(packs.available()), 3)

    def test_each_pack_folder_is_complete(self):
        for s in packs.available():
            with self.subTest(s):
                d = packs.PACKS_DIR / s
                for name in ("README.md", "tasks.jsonl", "SKILL.md", "SHA256SUMS"):
                    self.assertTrue((d / name).is_file(), name)
                self.assertTrue((d / "LICENSE").is_file() or (d / "LICENSE.txt").is_file())
                readme = (d / "README.md").read_text()
                for heading in ("What the skill is", "Where it came from", "Licence", "What the questions test"):
                    self.assertIn(heading, readme)
                fm, _ = replay.parse_frontmatter(packs.skill_text(s))
                self.assertEqual(fm.get("name"), s)

    def test_frontend_design_keeps_apache_notice(self):
        d = packs.PACKS_DIR / "frontend-design"
        self.assertIn("Apache License", (d / "LICENSE.txt").read_text())
        self.assertIn("Anthropic", (d / "NOTICE").read_text())

    def test_tasks_have_the_mined_shape(self):
        for s in packs.available():
            with self.subTest(s):
                tasks = packs.tasks_for(s)
                self.assertTrue(6 <= len(tasks) <= 12)
                for t in tasks:
                    self.assertLessEqual(MINED_KEYS, set(t))
                    self.assertEqual((t["source"], t["skill"], t["kept"]), ("pack", s, True))
                    self.assertNotIn("frozen_messages", t)
                    self.assertTrue(replay.index_has_entry(t["system_prompt"], s))
                    self.assertFalse(replay.index_has_entry(replay.remove_index_entry(t["system_prompt"], s), s))
                self.assertEqual(len({realrun.task_hash(t) for t in tasks}), len(tasks))

    def test_no_home_paths_in_tasks(self):
        # The named private markers are checked in test_w3_packs_private.py (dev checkout only).
        for s in packs.available():
            text = (packs.PACKS_DIR / s / "tasks.jsonl").read_text()
            self.assertIsNone(re.search(r"/(?:Users)/", text), s)  # same check as the old literal
            self.assertNotIn("~/.hermes", text, s)

    def test_answer_limits(self):
        self.assertEqual(packs.answer_limits("systematic-debugging"), {"subject_max_tokens": 6000})
        fd = packs.answer_limits("frontend-design")
        self.assertEqual(fd["runaway_max_chars"], 60_000)
        cfg_fields = set(realrun.RealRunConfig.__dataclass_fields__)
        self.assertLessEqual(set(fd) - {"seconds_per_call"}, cfg_fields)  # that one is only for the estimate

    def test_unknown_skill(self):
        self.assertIsNone(packs.tasks_for("no-such-skill"))
        self.assertIsNone(packs.tasks_for("../frontend-design"))


class Tamper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "packs"
        self.skill = packs.available()[0]
        shutil.copytree(packs.PACKS_DIR / self.skill, self.root / self.skill)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_edited_tasks_file_is_refused(self):
        p = self.root / self.skill / "tasks.jsonl"
        p.write_text(p.read_text() + json.dumps({"id": "x", "user_turn": "y"}) + "\n")
        with self.assertRaises(packs.PackError):
            packs.tasks_for(self.skill, root=self.root)

    def test_edited_skill_is_refused(self):
        p = self.root / self.skill / "SKILL.md"
        p.write_text(p.read_text() + "\nextra line\n")
        with self.assertRaises(packs.PackError):
            packs.skill_text(self.skill, root=self.root)

    def test_falls_back_to_installed_copy(self):
        (self.root / self.skill / "SKILL.md").unlink()
        mine = self.tmp / "skills" / "some-category" / "any-folder"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text(f"---\nname: {self.skill}\ndescription: \"My copy.\"\n---\nbody\n")
        self.assertIn("My copy.", packs.skill_text(self.skill, root=self.root, skills_dir=self.tmp / "skills"))
        self.assertIsNone(packs.skill_text(self.skill, root=self.root, skills_dir=self.tmp / "nothing"))
        tasks = packs.tasks_for(self.skill, root=self.root, skills_dir=self.tmp / "skills")
        self.assertIn(f"{self.skill}: My copy.", tasks[0]["system_prompt"])


class ThroughTheRunner(unittest.TestCase):
    """Pack tasks go through ``realrun.RealRunner`` (the verdict path) with a fake model."""

    def test_runner_accepts_pack_tasks(self):
        skill = "frontend-design"
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "skills" / skill).mkdir(parents=True)
            (tmp / "skills" / skill / "SKILL.md").write_text(packs.skill_text(skill))
            (tmp / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in packs.tasks_for(skill)[:3]))
            (tmp / "frozen.jsonl").write_text("")
            calls = []

            class Fake:
                def complete(self, messages, **kw):
                    calls.append(kw)
                    judge = messages[0]["content"].startswith("# Purpose-based")
                    text = '{"winner": "1", "reason": "Answer 1 is better."}' if judge else "an answer"
                    return SimpleNamespace(text=text, provider=kw.get("provider"), model=kw.get("model") or "m",
                                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15,
                                                                 cache_read_tokens=0))

            cfg = realrun.RealRunConfig(skills=(skill,), skills_dir=tmp / "skills", fixtures_dir=sb_paths.FIXTURES_DIR,
                                        strict_path=tmp / "tasks.jsonl", frozen_path=tmp / "frozen.jsonl",
                                        names_path=None, tracked_dir=tmp / "out", private_dir=tmp / "out",
                                        provider="p", subject_model="m", judge_model="m", cap=12, aa_tasks=0,
                                        disabled_skills=(), workers=1)
            res = realrun.RealRunner(Fake(), cfg, log=lambda s: None).run()
            c = res["per_skill"][skill]["combined"]
            self.assertEqual(c["wins"] + c["losses"] + c["ties"], 3)
            self.assertEqual(len(calls), 12)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
