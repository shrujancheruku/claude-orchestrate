"""scripts/park-pr through the CLI, with the fake `gh`: take an open PR out of the merge queue and put it back (#708)."""

import json
import unittest

from harness import ScriptEnv, gh_repo_view, rest_pull
import runlog  # tests/harness.py puts scripts/ on sys.path

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

    def test_an_unwritable_run_log_fails_the_park_before_the_pr_is_touched(self):
        # A closed PR with no `park` event reads as a stalled ticket and would be rescued (#661): the event goes first.
        self.gh()
        runlog.log_path(self.env.repo, runlog.EVENTS_LOG).mkdir(parents=True)  # a directory where the log file goes
        r = self.env.run("park-pr", 7, "--why", "x", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.splitlines()[-1], "next: could-not-run")
        self.assertIn("PR #7 is untouched", r.stderr)
        self.assertEqual(self.writes(), [])

    def test_an_unwritable_run_log_fails_the_unpark_too(self):
        self.gh(state="CLOSED")
        runlog.log_path(self.env.repo, runlog.EVENTS_LOG).mkdir(parents=True)
        r = self.env.run("park-pr", 7, "--unpark", check=False)
        self.assertEqual((r.returncode, self.writes()), (2, []))

    def test_a_failed_close_takes_the_event_back(self):
        self.env.set_gh([gh_repo_view(), {"args": PULL, "stdout": json.dumps(rest_pull(state="OPEN", headRefName="7-x"))},
                         {"args": PATCH, "stderr": "boom\n", "exit": 1}])
        r = self.env.run("park-pr", 7, "--why", "x", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual([e["outcome"] for e in self.park_events()], ["parked", "unparked"])  # the log agrees with the open PR

    def test_a_failed_undo_is_reported_on_stderr(self):
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        from pathlib import Path
        from unittest import mock
        import github
        loader = importlib.machinery.SourceFileLoader("park_pr_cli", str(Path(runlog.__file__).parent / "park-pr"))
        spec = importlib.util.spec_from_loader("park_pr_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        view = {"state": "OPEN", "headRefName": "7-x", "isCrossRepository": False}
        calls = []

        def append(*a, **k):
            calls.append(a[2])
            if len(calls) > 1:
                raise OSError(13, "Permission denied")

        err = io.StringIO()
        with mock.patch.object(github, "pr_view", return_value=view), \
                mock.patch.object(github, "set_pr_state", side_effect=github.GhError("other", "boom")), \
                mock.patch.object(runlog, "append_event", side_effect=append), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = mod.main(["7", "--why", "x"])
        self.assertEqual((code, calls), (2, ["parked", "unparked"]))
        self.assertIn("PR #7 did not change, but the `unparked` event that takes back `parked` could not be written",
                      err.getvalue())

    def test_the_reason_or_the_mode_is_required_and_exclusive(self):
        self.gh()
        for args in ((), ("--why", "  "), ("--why", "x", "--unpark"), ("--wh", "x")):
            with self.subTest(args=args):
                r = self.env.run("park-pr", 7, *args, check=False)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
