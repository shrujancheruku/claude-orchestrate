"""Structural checks: setup inference and advice, run batching (#46)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class SetupInference(unittest.TestCase):
    def test_setup_rules(self):
        s = read("skills/setup/SKILL.md")
        for needle in ("gh pr list --state merged --limit 50", "at least 25%", ".gitattributes",
                       "orchestrator-owned", "more than half would be L", "merge gates",
                       "autoCompactWindow", "200000", "--autocompact 150000", "first file that sets it",
                       "never edits user config", "couldn't be found"):
            self.assertIn(needle, s)

    def test_run_rules(self):
        s = read("skills/run/SKILL.md")
        for needle in ("Orchestrator-owned edits", "one PR for them", "for the second time", "Shared files"):
            self.assertIn(needle, s)
        self.assertNotIn("make the status update the config assigns", s)


if __name__ == "__main__":
    unittest.main()
