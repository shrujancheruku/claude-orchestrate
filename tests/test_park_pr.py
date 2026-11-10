"""scripts/park-pr through the CLI, with the fake `gh`: take an open PR out of the merge queue and put it back (#708)."""

import json
import unittest

from harness import ScriptEnv, gh_repo_view, rest_pull

PULL = ["api", "repos/{owner}/{repo}/pulls/7"]
PATCH = ["api", "--method", "PATCH", "repos/{owner}/{repo}/pulls/7"]
COMMENT = ["api", "--method", "POST", "repos/{owner}/{repo}/issues/7/comments"]


class ParkPr(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def gh(self, state="OPEN", fork=False, comment_exit=0):
        pull = rest_pull(state=state, headRefName="7-x", isCrossRepository=fork)
        self.env.set_gh([gh_repo_view(), {"args": PULL, "stdout": json.dumps(pull)},
                         {"args": PATCH, "stdout": "{}"},
                         {"args": COMMENT, "stdout": "{}", "exit": comment_exit, "stderr": "boom\n" if comment_exit else ""}])

    def writes(self):
        out = []
        for c in self.env.gh_calls():
            if c[:3] == ["api", "--method", "PATCH"]:
                out.append(["close" if "state=closed" in c else "reopen", c[3].rsplit("/", 1)[1]])
            elif c[:3] == ["api", "--method", "POST"]:
                out.append(["comment", next(a for a in c if a.startswith("body="))[5:]])
            else:
                self.assertNotIn("delete", " ".join(c))  # the branch is never deleted
        return out

    def park_events(self):
        return [e for e in self.env.events() if e["kind"] == "park"]

    def test_an_open_pr_is_closed_commented_and_logged_and_its_branch_kept(self):
        self.gh()
        r = self.env.run("park-pr", 7, "--why", "waits on the fix in #9")
        self.assertEqual(r.stdout.splitlines()[-1], "next: parked 7")
        self.assertEqual(self.writes(), [["close", "7"], ["comment", "Parked: waits on the fix in #9"]])
        (event,) = self.park_events()
        self.assertEqual((event["outcome"], event["ref"], event["ticket"], event["branch"], event["why"]),
                         ("parked", 7, 7, "7-x", "waits on the fix in #9"))

    def test_unpark_reopens_the_closed_pr_the_same_way(self):
        self.gh(state="CLOSED")
        r = self.env.run("park-pr", 7, "--unpark")
        self.assertEqual(r.stdout.splitlines()[-1], "next: unparked 7")
        self.assertEqual([w[0] for w in self.writes()], ["reopen", "comment"])
        (event,) = self.park_events()
        self.assertEqual((event["outcome"], event["ticket"]), ("unparked", 7))

    def test_a_closed_or_merged_pr_is_refused_with_a_one_line_reason_and_no_write(self):
        for state in ("CLOSED", "MERGED"):
            with self.subTest(state):
                self.gh(state=state)
                r = self.env.run("park-pr", 7, "--why", "x", check=False)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout.splitlines()[-1], f"next: refused PR #7 is {state.lower()}, not open")
                self.assertEqual(r.stderr.strip(), f"park-pr: PR #7 is {state.lower()}, not open")
                self.assertEqual(self.writes(), [])
                self.assertEqual(self.park_events(), [])

    def test_unparking_an_open_or_merged_pr_is_refused(self):
        for state in ("OPEN", "MERGED"):
            with self.subTest(state):
                self.gh(state=state)
                r = self.env.run("park-pr", 7, "--unpark", check=False)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout.splitlines()[-1], f"next: refused PR #7 is {state.lower()}, not closed")
                self.assertEqual(self.writes(), [])

    def test_a_fork_pr_is_refused(self):
        self.gh(fork=True)
        r = self.env.run("park-pr", 7, "--why", "x", check=False)
        self.assertEqual((r.returncode, self.writes()), (1, []))

    def test_a_failed_comment_keeps_the_close_and_still_logs_it(self):
        self.gh(comment_exit=1)
        r = self.env.run("park-pr", 7, "--why", "x")
        self.assertEqual(r.returncode, 0)
        self.assertIn("comment failed", r.stderr)
        self.assertEqual(len(self.park_events()), 1)

    def test_the_reason_or_the_mode_is_required_and_exclusive(self):
        self.gh()
        for args in ((), ("--why", "  "), ("--why", "x", "--unpark"), ("--wh", "x")):
            with self.subTest(args=args):
                r = self.env.run("park-pr", 7, *args, check=False)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
