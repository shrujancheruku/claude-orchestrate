"""Structural checks: worker PR bodies go through the `:pr` skill when one is listed (#59)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class PrSkill(unittest.TestCase):
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
