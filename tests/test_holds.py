"""scripts/holds: held paths and overlap, through the CLI with a fake `gh`."""

import json
import unittest

from harness import ScriptEnv

CONFIG = """## Slots

- **Slots:** 2.
- **Exclusive holds:** {holds}

## Shared files

- **Shared files:**
  - `CHANGELOG.md`: one line per PR
"""


class Holds(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def gh(self, prs=None, branches=None, issues=None, merged=()):
        """prs: {number: [paths]}; branches: {number: head branch}; issues: {ticket: state} (OPEN for
        any other ticket in the state file); merged: head branches of merged PRs."""
        prs, branches = prs or {}, branches or {}
        listing = [{"number": n, "headRefName": branches.get(n, f"b{n}"), "isCrossRepository": False} for n in prs]
        responses = [
            {"args": ["repo", "view"], "stdout": json.dumps({"defaultBranchRef": {"name": "main"}})},
            {"args": ["pr", "list", "--state", "open"], "stdout": json.dumps(listing)},
        ]
        for n, files in prs.items():
            responses.append({"args": ["pr", "view", str(n), "--json", "files"],
                              "stdout": json.dumps({"files": [{"path": p} for p in files]})})
            responses.append({"args": ["pr", "view", str(n), "--json", "headRefName"],
                              "stdout": json.dumps({"headRefName": branches.get(n, f"b{n}")})})
        for n, st in (issues or {}).items():
            responses.append({"args": ["issue", "view", str(n)], "stdout": json.dumps({"state": st})})
        responses.append({"args": ["issue", "view"], "stdout": json.dumps({"state": "OPEN"})})
        responses.append({"args": ["pr", "list", "--state", "merged"],
                          "stdout": json.dumps([{"number": 1, "headRefName": b} for b in merged])})
        self.env.set_gh(responses)

    def state(self, tickets):
        d = self.env.repo / ".claude"
        d.mkdir(exist_ok=True)
        (d / "orchestrate-state.json").write_text(json.dumps(
            {"tickets": {str(n): {"touch_map": tm} for n, tm in tickets.items()}}))

    def config(self, holds="none"):
        d = self.env.repo / ".claude"
        d.mkdir(exist_ok=True)
        (d / "orchestration.md").write_text(CONFIG.format(holds=holds))

    def holds(self, *args):
        return self.env.run("holds", *args, check=False)

    def test_nothing_held(self):
        self.gh()
        r = self.holds("--touch-map", "a.py:3", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_line_and_range_stripped_and_other_section_overlaps(self):
        self.gh({9: ["config.txt"]})
        for entry in ("config.txt:40", "config.txt:10-20", "./config.txt"):
            with self.subTest(entry):
                r = self.holds("--touch-map", entry, "--ticket", "7")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(r.stdout.splitlines(), [
                    "config.txt (held by open PR #9)", "overlap: config.txt (held by open PR #9)"])

    def test_building_ticket_touch_map_is_stripped(self):
        self.gh()
        self.state({12: ["src/x.py:5", "y.py:1-9"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by building #12)", "y.py (held by building #12)",
            "overlap: src/x.py (held by building #12)"])

    def test_shared_file_overlap_is_reported_not_counted(self):
        self.gh({9: ["CHANGELOG.md"]})
        self.config()
        r = self.holds("--touch-map", "CHANGELOG.md:3", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("shared (not counted): CHANGELOG.md (held by open PR #9)", r.stdout)
        self.assertIn("overlap: none", r.stdout)

    def test_exclusive_hold(self):
        self.gh()
        self.config("while #42 is open, nothing else touches `db/`.")
        r = self.holds("--touch-map", "db/schema.sql:1", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[0], "db/ (held by an exclusive hold)")
        self.assertIn("overlap: db/schema.sql (held by an exclusive hold)", r.stdout)

    def test_own_pr_and_touch_map_left_out(self):
        self.gh({9: ["a.py"], 10: ["b.py"]}, branches={9: "7-my-ticket", 10: "8-other"})
        self.state({7: ["a.py:1"], 11: ["c.py:1"]})
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.splitlines(), [
            "b.py (held by open PR #10)", "c.py (held by building #11)", "overlap: none"])

    def test_pre_merge_check_uses_the_prs_diff(self):
        self.gh({9: ["a.py", "b.py"], 10: ["b.py"]}, branches={9: "7-mine", 10: "8-other"})
        self.state({7: ["a.py"], 11: ["z.py"]})
        r = self.holds("--pr", "9")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "b.py (held by open PR #10)", "z.py (held by building #11)",
            "overlap: b.py (held by open PR #10)"])

    def test_closed_ticket_holds_nothing(self):
        self.gh(issues={12: "CLOSED"})
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_ticket_with_merged_pr_holds_nothing(self):
        self.gh(merged=["12-the-fix"])
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_open_ticket_with_open_pr_is_held_by_the_pr_only(self):
        self.gh({9: ["src/x.py"]}, branches={9: "12-the-fix"})
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by open PR #9)", "overlap: src/x.py (held by open PR #9)"])
        self.assertEqual(r.returncode, 1)

    def test_ticket_check_gh_failure_could_not_run(self):
        self.state({12: ["a.py"]})
        self.env.set_gh([
            {"args": ["repo", "view"], "stdout": json.dumps({"defaultBranchRef": {"name": "main"}})},
            {"args": ["pr", "list"], "stdout": "[]"},
            {"args": ["issue", "view"], "stderr": "HTTP 502\n", "exit": 1}])
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 2)
        self.assertIn("holds: gh:", r.stderr)

    def test_missing_state_file_means_no_building_tickets(self):
        self.gh({9: ["a.py"]})
        r = self.holds("--touch-map", "b.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (0, "a.py (held by open PR #9)"))

    def test_unreadable_state_file_could_not_run(self):
        self.gh()
        (self.env.repo / ".claude").mkdir()
        (self.env.repo / ".claude/orchestrate-state.json").write_text("{nope")
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_gh_unavailable_could_not_run(self):
        r = self.env.run("holds", "--touch-map", "a.py", "--ticket", "7", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_gh_failure_could_not_run(self):
        self.env.set_gh([{"args": ["repo", "view"], "stderr": "HTTP 502\n", "exit": 1}])
        self.assertEqual(self.holds("--touch-map", "a.py", "--ticket", "7").returncode, 2)

    def test_usage(self):
        self.assertEqual(self.holds().returncode, 2)
        self.assertEqual(self.holds("--touch-map", "a.py").returncode, 2)


if __name__ == "__main__":
    unittest.main()
