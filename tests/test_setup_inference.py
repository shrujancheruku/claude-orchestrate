"""Structural checks: setup inference and advice, run batching (#46)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class SetupInference(unittest.TestCase):
    def test_setup_rules(self):
        s = read("skills/setup/SKILL.md")
        # key terms only; each names a setting or file the step reads or refuses to write
        for needle in ("gh pr list --state merged", ".gitattributes", "orchestrator-owned", "merge gates",
                       "autoCompactWindow", "never edits user config", "25%", "150000"):
            self.assertIn(needle, s)

    def test_run_rules(self):
        s = read("skills/run/SKILL.md")
        for needle in ("Orchestrator-owned edits", "Shared files"):
            self.assertIn(needle, s)
        self.assertNotIn("make the status update the config assigns", s)


if __name__ == "__main__":
    unittest.main()
