import json
import subprocess
import unittest

from harness import ScriptEnv

def view(result, merge="CLEAN", head="abcdef1234567", state="OPEN"):
    if result is None:
        checks = [{"status": "IN_PROGRESS", "conclusion": ""}]
    else:
        checks = [{"status": "COMPLETED", "conclusion": result}]
    return {"statusCheckRollup": checks, "mergeStateStatus": merge, "headRefOid": head, "state": state}


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


if __name__ == "__main__":
    unittest.main()
