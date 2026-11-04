"""Structural checks: the worker has no Agent tool, and the mechanic is retired (#43)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class WorkerTurnRules(unittest.TestCase):
    def test_worker_has_no_agent_tool(self):
        fm = (ROOT / "agents/worker.md").read_text().split("---")[1]
        self.assertRegex(fm, r"(?m)^disallowedTools:.*\bAgent\b")
        self.assertNotRegex(fm, r"(?m)^tools:.*\bAgent\b")

    def test_worker_has_turn_rules_and_short_report(self):
        s = (ROOT / "agents/worker.md").read_text()
        self.assertIn("## Turn rules", s)
        for phrase in ("Make independent tool calls in one turn",
                       "Chain verify steps in one command",
                       "Find with `grep -n`, then read the line range",
                       "Don't re-read a file you haven't changed",
                       "Bound Bash output"):
            self.assertIn(phrase, s)
        report = s[s.index("## Report"):]
        self.assertIn("status: pr-opened | stopped", report)
        self.assertNotIn("changed:", report)

    def test_mechanic_is_gone(self):
        self.assertFalse((ROOT / "agents/mechanic.md").exists())

    def test_nothing_mentions_the_mechanic(self):
        files = [ROOT / "README.md", ROOT / "skills/setup/config-template.md"]
        for d in ("skills", "agents", "hooks"):
            files += [p for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        for p in files:
            self.assertNotRegex(p.read_text(errors="ignore"), r"(?i)\bmechanic\b", str(p))


if __name__ == "__main__":
    unittest.main()
