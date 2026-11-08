"""Structural checks: worker, reviewer and run define discovered work (#207)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINE = "- <what is missing> — <where: path or ticket> — <why it matters>"


def read(p):
    return (ROOT / p).read_text()


class DiscoveredWork(unittest.TestCase):
    def test_worker_defines_section_line_format_and_report_line(self):
        s = read("agents/worker.md")
        self.assertIn("## Discovered work", s)
        self.assertIn(LINE, s)
        self.assertRegex(s, r"(?m)^discovered: <n> — ")
        self.assertIn("block the PR", s)
        self.assertIn("stays a stop", s)

    def test_reviewer_defines_section_line_format_and_both_report_shapes(self):
        s = read("agents/reviewer.md")
        self.assertIn("## Discovered work", s)
        self.assertIn(LINE, s)
        self.assertEqual(len(re.findall(r"(?m)^\s*discovered: <n> — ", s)), 2)

    def test_reviewer_routes_out_of_scope_work_away_from_minor(self):
        s = read("agents/reviewer.md")
        self.assertRegex(s, r"never a `\[minor\]` finding")

    def test_reviewer_never_trial_writes_to_the_host(self):
        s = read("agents/reviewer.md")
        self.assertIn("**API questions.**", s)
        self.assertIn("Never send a write to the host to find out", s)
        self.assertIn("The only writes you ever make are under **Post**", s)
        self.assertIn("The review post (below) is the only write", s)
        self.assertIn("not even a trial of a payload shape", s)

    def test_run_summary_lists_discovered_work_prs(self):
        s = read("skills/run/SKILL.md")
        step = s[s.index("5. **Report and yield.**"):]
        self.assertIn("PR URLs that carry discovered work", step.split("\n")[0])


if __name__ == "__main__":
    unittest.main()
