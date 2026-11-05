"""Prompts read a ticket through the tracker doc's command, never `gh issue view --comments` (#152)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPTS = sorted(p for d in ("agents", "skills") for p in (ROOT / d).rglob("*.md"))


class TicketRead(unittest.TestCase):
    def test_no_prompt_reads_a_ticket_with_comments_flag(self):
        for p in PROMPTS:
            for line in p.read_text().splitlines():
                self.assertNotRegex(line, r"gh issue view[^`\n]*--comments", p.name)

    def test_worker_and_reviewer_point_to_tracker_read_command(self):
        for name in ("worker", "reviewer"):
            s = (ROOT / "agents" / f"{name}.md").read_text()
            self.assertIn("Read an issue", s, name)
            self.assertIn("docs/agents/issue-tracker.md", s, name)

    def test_worker_stops_on_empty_ticket_but_not_with_agent_brief(self):
        s = (ROOT / "agents/worker.md").read_text()
        stop = s[s.index("## Stop and report"):]
        bullet = next(l for l in stop.splitlines() if "Agent Brief" in l)
        self.assertRegex(bullet, r"(?i)empty")
        self.assertRegex(bullet, r"(?i)not a stop")


if __name__ == "__main__":
    unittest.main()
