"""Structural checks: worker PR bodies go through the `:pr` skill when one is listed (#59)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class PrSkill(unittest.TestCase):
    def test_brief_carries_pr_skill_line(self):
        s = read("skills/run/brief.md")
        line = next(l for l in s.splitlines() if "`:pr`" in l)
        self.assertIn("plain", line)  # the fallback sits on the same line as the skill

    def test_setup_reports_missing_pr_skill(self):
        s = read("skills/setup/SKILL.md")
        line = next(l for l in s.splitlines() if "`:pr`" in l)
        self.assertIn("plain", line)
        self.assertIn("not fail", line)


if __name__ == "__main__":
    unittest.main()
