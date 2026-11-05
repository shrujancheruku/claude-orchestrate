"""Structural checks: Haiku routing and escalation text stays in the skills (#42)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class HaikuRouting(unittest.TestCase):
    def test_run_skill_escalation_rules(self):
        s = read("skills/run/SKILL.md")
        # a "Choosing the worker model" section that names haiku and escalation
        section = s[s.index("## Choosing the worker model"):s.index("## The review loop")]
        self.assertIn("haiku", section)
        self.assertIn("escalat", section)
        # the CI-failure step (review loop step 2) routes a haiku worker to escalation
        step = next(l for l in s.splitlines() if l.startswith("2. **CI failed**"))
        self.assertIn("haiku", step)
        self.assertIn("escalate", step)

    def test_review_step_5_mentions_escalation(self):
        s = read("skills/run/SKILL.md")
        step = next(l for l in s.splitlines() if l.startswith("5. **Changes requested**"))
        self.assertIn("escalate", step)

    def test_models_row(self):
        row = next(l for l in read("skills/setup/config-template.md").splitlines() if "haiku" in l)
        self.assertIn("S", row)


if __name__ == "__main__":
    unittest.main()
