"""Structural checks: worker PR bodies go through the `:pr` skill when one is listed (#59)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class PrSkill(unittest.TestCase):
    def test_worker_ship_loads_pr_skill(self):
        s = read("agents/worker.md")
        for needle in ("skill whose name ends in `:pr`", "mattpocock-skills:pr", "Skill tool",
                       "Before `gh pr create`", "with no such skill listed, write a plain body",
                       "closing reference", "with no attribution lines"):
            self.assertIn(needle, s)

    def test_brief_carries_pr_skill_line(self):
        s = read("skills/run/brief.md")
        self.assertIn('body written with the skill whose name ends in `:pr`', s)
        self.assertIn("else plain", s)

    def test_setup_reports_missing_pr_skill(self):
        s = read("skills/setup/SKILL.md")
        self.assertIn("If no skill whose name ends in `:pr` is listed, tell the user worker PR bodies will be plain", s)
        self.assertIn("setup does not fail", s)


if __name__ == "__main__":
    unittest.main()
