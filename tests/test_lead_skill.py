"""Structural test for the lead skill's start-up prompt (#214).

The live launch and cross-session messaging aren't testable in CI; this checks the prompt
still names every branch of the start-up, so none is dropped by an edit.
"""
import pathlib
import unittest

SKILL = pathlib.Path(__file__).resolve().parent.parent / "skills/lead/SKILL.md"


class LeadSkill(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text()

    def check(self, needles):
        for needle in needles:
            with self.subTest(needle=needle):
                self.assertIn(needle.lower(), self.text.lower())

    def test_names_script_and_scout(self):
        self.check(["scripts/lead-brief", "scout"])

    def test_name_and_duplicate_check(self):
        self.check(["/rename lead", "another live session already has the name `lead`"])

    def test_launch_resume_and_live_branches(self):
        self.check(["claude agents --json --all", "SendMessage", "pid: null",
                    "claude --resume <sessionId> --bg", "Never resume a live row",
                    "claude --bg --name orchestrator --permission-mode",
                    '"/orchestrate:run --greedy"', "**Orchestrator** row", "**Models**",
                    "a live row wins"])

    def test_model_not_found_only_fallback(self):
        self.check(["model_not_found", 'state: "failed"', "`error` field", "exactly once",
                    "usage limit", "never triggers"])

    def test_staying_current(self):
        self.check(["needs-lead", "watermark", "outcomes only"])


if __name__ == "__main__":
    unittest.main()
