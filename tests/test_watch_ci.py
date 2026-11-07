import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from harness import SCRIPTS, ScriptEnv

def view(result, merge="CLEAN", head="abcdef1234567", state="OPEN", draft=False):
    if result is None:
        checks = [{"status": "IN_PROGRESS", "conclusion": ""}]
    else:
        checks = [{"status": "COMPLETED", "conclusion": result}]
    return {"statusCheckRollup": checks, "mergeStateStatus": merge, "headRefOid": head, "state": state,
            "isDraft": draft}


class WatchCi(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def seq(self, steps):
        """{"<pr>": [view, ...]}: the Nth `pr view <pr>` gets step min(N, last); "GH_ERROR" is a failed call."""
        def reply(step):
            if step == "GH_ERROR":
                return {"stderr": "HTTP 502: bad gateway\n", "exit": 1}
            return {"stdout": json.dumps(step)}
        self.env.set_gh([{"args": ["pr", "view", pr], "seq": [reply(s) for s in pr_steps]}
                         for pr, pr_steps in steps.items()])

    def watch(self, *prs):
        return self.env.run("watch-ci", "--interval", "0", *prs)

    def test_settle_order_and_events(self):
        self.seq({
            "1": [view(None), view(None), view(None), view("SUCCESS", head="1111111aaa")],
            "2": [view(None), view("FAILURE", merge="BLOCKED", head="2222222bbb")],
            "3": [view("SUCCESS", head="3333333ccc")],
        })
        out = self.watch("1", "2", "3").stdout.splitlines()
        self.assertEqual(out, [
            "PR #3 pass CLEAN 3333333",
            "PR #2 fail BLOCKED 2222222",
            "PR #1 pass CLEAN 1111111",
        ])
        events = self.env.events()
        self.assertEqual([(e["kind"], e["ref"], e["outcome"]) for e in events],
                         [("ci", 3, "pass"), ("ci", 2, "fail"), ("ci", 1, "pass")])
        self.assertEqual(events[1]["merge_state"], "BLOCKED")
        self.assertEqual(events[1]["head"], "2222222")

    def test_events_from_a_subdirectory_and_a_worktree_land_in_the_main_log(self):
        self.seq({"1": [view("SUCCESS", head="1111111aaa")]})
        sub = self.env.repo / "pkg"
        sub.mkdir()
        wt = self.env.repo / ".claude" / "worktrees" / "w1"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "w1", str(wt)], cwd=self.env.repo, check=True)
        for cwd in (sub, wt):
            self.assertEqual(self.env.run("watch-ci", "--interval", "0", "1", cwd=cwd, check=False).returncode, 0)
        self.assertEqual(len(self.env.events()), 2)
        self.assertFalse((sub / ".claude").exists())
        self.assertFalse((wt / ".claude" / "orchestrate-profiles").exists())

    def test_a_draft_never_settles_until_it_is_marked_ready(self):
        # PR 1 is green but still a draft (building): it settles only on the first ready view. PR 2 is ready and settles.
        self.seq({"1": [view("SUCCESS", draft=True), view("SUCCESS", draft=True), view("SUCCESS", head="1111111aaa")],
                  "2": [view("SUCCESS", head="2222222bbb")]})
        r = self.watch("1", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["PR #2 pass CLEAN 2222222", "PR #1 pass CLEAN 1111111"])

    def test_empty_rollup_is_pending_until_checks_appear(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"7": [empty, view("SUCCESS", head="abc1234")]})
        self.assertEqual(self.watch("7").stdout.splitlines(), ["PR #7 pass CLEAN abc1234"])

    def test_status_context_failure(self):
        v = {"statusCheckRollup": [{"state": "ERROR"}], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"8": [v]})
        self.assertEqual(self.watch("8").stdout.splitlines(), ["PR #8 fail CLEAN abc1234"])

    def test_merged_settles_as_pass_and_closed_as_fail(self):
        self.seq({"4": [view(None, state="MERGED", head="4444444aaa")],
                  "5": [view(None, state="CLOSED", merge="DIRTY", head="5555555bbb")]})
        out = self.watch("4", "5").stdout.splitlines()
        self.assertEqual(out, ["PR #4 pass CLEAN 4444444", "PR #5 fail DIRTY 5555555"])
        self.assertEqual([(e["ref"], e["outcome"]) for e in self.env.events()], [(4, "pass"), (5, "fail")])

    def test_gh_error_is_retried(self):
        self.seq({"6": ["GH_ERROR", "GH_ERROR", view("SUCCESS", head="6666666ccc")]})
        r = self.watch("6")
        self.assertEqual(r.stdout.splitlines(), ["PR #6 pass CLEAN 6666666"])
        self.assertIn("gh failed for #6", r.stderr)
        self.assertEqual(len(self.env.gh_calls()), 3)

    def test_unwritable_event_log_still_prints_every_line(self):
        # A file where the profiles directory should be makes append_event raise OSError.
        claude = self.env.repo / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "orchestrate-profiles").write_text("not a directory")
        self.seq({"1": [view("SUCCESS", head="1111111aaa")], "2": [view("FAILURE", head="2222222bbb")]})
        r = self.watch("1", "2")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), ["PR #1 pass CLEAN 1111111", "PR #2 fail CLEAN 2222222"])
        self.assertIn("could not write event", r.stderr)

    def test_missing_gh_exits_could_not_run_instead_of_waiting(self):
        r = self.env.run("watch-ci", "--interval", "0", "7", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_reply_that_is_not_a_pr_object_is_could_not_run(self):
        for reply in ("[]", "<html>not json"):
            with self.subTest(reply=reply):
                self.env.set_gh([{"args": ["pr", "view"], "stdout": reply}])
                r = self.env.run("watch-ci", "--interval", "0", "7", check=False)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertNotIn("Traceback", r.stderr)


    def watch_any(self, *prs, **kw):
        return self.env.run("watch-ci", "--interval", "0", *prs, check=False, **kw)

    def test_repeated_failures_end_in_an_error_line_and_exit_3(self):
        self.seq({"6": ["GH_ERROR"]})
        r = self.watch_any("6")
        self.assertEqual(r.stdout.splitlines(), ["PR #6 error HTTP 502: bad gateway"])
        self.assertEqual(r.returncode, 3)
        self.assertEqual(len(self.env.gh_calls()), 5)

    def test_failures_under_the_bound_still_settle(self):
        self.seq({"6": ["GH_ERROR"] * 4 + [view("SUCCESS", head="6666666ccc")]})
        r = self.watch_any("6")
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["PR #6 pass CLEAN 6666666"], 0))

    def test_a_success_resets_the_failure_count(self):
        ok = view(None)
        self.seq({"6": ["GH_ERROR"] * 4 + [ok] + ["GH_ERROR"] * 4 + [view("SUCCESS", head="6666666ccc")]})
        r = self.watch_any("6")
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["PR #6 pass CLEAN 6666666"], 0))

    def test_one_pr_erroring_does_not_stop_the_others(self):
        self.seq({"6": ["GH_ERROR"], "7": [view(None), view(None), view("SUCCESS", head="7777777ccc")]})
        r = self.watch_any("6", "7")
        self.assertIn("PR #6 error HTTP 502: bad gateway", r.stdout)
        self.assertIn("PR #7 pass CLEAN 7777777", r.stdout)
        self.assertEqual(r.returncode, 3)

    def test_no_repository_failure_ends_at_once(self):
        self.env.set_gh([{"args": ["pr", "view"], "stderr": "fatal: not a git repository\n", "exit": 1}])
        r = self.watch_any("6")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(self.env.gh_calls()), 1)

    def test_started_outside_a_repository_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.watch_any("6", cwd=d)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertEqual(self.env.gh_calls(), [])

    def test_removed_working_directory_after_startup_still_settles(self):
        wt = self.env.repo / ".claude" / "worktrees" / "w2"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "w2", str(wt)], cwd=self.env.repo, check=True)
        self.seq({"6": [view(None)]})
        p = subprocess.Popen([sys.executable, str(SCRIPTS / "watch-ci"), "--interval", "0.05", "6"], cwd=wt,
                             env=dict(os.environ, PATH=f"{self.env.bin}{os.pathsep}{os.environ['PATH']}",
                                      FAKE_GH_STATE=str(self.env.gh_state)),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        while not self.env.gh_calls():
            time.sleep(0.02)
        shutil.rmtree(wt)
        self.seq({"6": [view("SUCCESS", head="6666666ccc")]})
        out, err = p.communicate(timeout=20)
        self.assertEqual((out.splitlines(), p.returncode), (["PR #6 pass CLEAN 6666666"], 0), err)


if __name__ == "__main__":
    unittest.main()
