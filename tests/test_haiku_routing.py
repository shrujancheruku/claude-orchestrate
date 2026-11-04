"""Structural checks: Haiku routing and escalation text stays in the skills (#42)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class HaikuRouting(unittest.TestCase):
    def test_run_skill_rules(self):
        s = read("skills/run/SKILL.md")
        for needle in ("size:S", "investigate", "model", "once", "second CI failure",
                       "round 2", "three-round cap", "rebuilt state file", "usage-limit"):
            self.assertIn(needle, s)

    def test_models_row(self):
        self.assertIn("sonnet; haiku for S tickets", read("skills/setup/config-template.md"))

    def test_setup_threshold(self):
        self.assertIn("Tiers", read("skills/setup/SKILL.md"))


if __name__ == "__main__":
    unittest.main()
