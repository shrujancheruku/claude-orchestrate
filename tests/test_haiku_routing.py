"""Structural checks: Haiku routing and escalation text stays in the skills (#42)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class HaikuRouting(unittest.TestCase):
    def test_run_skill_escalation_rules(self):
        s = read("skills/run/SKILL.md")
        for needle in ("escalates once", "never escalated again", "second CI failure", "contains `haiku`"):
            self.assertIn(needle, s)

    def test_review_step_5_mentions_escalation(self):
        s = read("skills/run/SKILL.md")
        step = next(l for l in s.splitlines() if l.startswith("5. **Changes requested**"))
        self.assertIn("escalates instead", step)

    def test_models_row(self):
        self.assertIn("haiku for S tickets", read("skills/setup/config-template.md"))


if __name__ == "__main__":
    unittest.main()
