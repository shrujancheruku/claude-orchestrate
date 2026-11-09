import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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
        """{"<pr>": [view, ...]}: the Nth `pr list` reply holds each PR's step min(N, last).
        "GH_ERROR" fails the whole call; "MISSING" leaves that PR out of the reply."""
        ticks = max(len(v) for v in steps.values())
        replies = []
        for n in range(ticks):
            picked = {pr: v[min(n, len(v) - 1)] for pr, v in steps.items()}
            if "GH_ERROR" in picked.values():
                replies.append({"stderr": "HTTP 502: bad gateway\n", "exit": 1})
            else:
                rows = [dict(v, number=int(pr)) for pr, v in picked.items() if v != "MISSING"]
                replies.append({"stdout": json.dumps(rows)})
        self.env.set_gh([{"args": ["pr", "list"], "seq": replies}])

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

    def load(self):
        loader = importlib.machinery.SourceFileLoader("watch_ci_parse", str(SCRIPTS / "watch-ci"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        return mod

    def test_parse_targets_takes_merges_hint_shape(self):
        parse = self.load().parse_targets
        sha = "5ba15c1" + "a" * 33
        self.assertEqual(parse(["504", sha]), (["504"], {"504": sha}))
        self.assertEqual(parse(["#7", "?", "8", "ABCDEF1"]), (["7", "8"], {"8": "abcdef1"}))
        self.assertEqual(parse(["1", "2", "3"]), (["1", "2", "3"], {}))
        for bad in (["zzz"], [sha], ["7", "not-a-head"], []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse(bad)

    def test_a_head_is_never_read_as_a_pr_number(self):
        sha = "5ba15c1" + "a" * 33
        self.seq({"504": [view("SUCCESS", head=sha)]})
        r = self.watch_any("504", sha)
        self.assertEqual((r.stdout.splitlines(), r.returncode), ([f"PR #504 pass CLEAN {sha[:7]}"], 0))
        self.assertNotIn("error", r.stdout)

    def test_a_bad_argument_is_could_not_run(self):
        r = self.watch_any("504", "nonsense")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("neither a PR number nor the head", r.stderr)

    def test_a_pinned_head_waits_for_the_pr_to_move_to_it(self):
        new = "5ba15c1" + "b" * 33
        old_green = view("SUCCESS", head="0000000aaa")
        self.seq({"504": [old_green, old_green, view("SUCCESS", head=new)]})
        r = self.watch_any("504", new)
        self.assertEqual(r.stdout.splitlines(), ["PR #504 pass CLEAN 5ba15c1"])
        self.assertGreaterEqual(len(self.env.gh_calls()), 3)  # the old head's green was not taken for the new one

    def test_a_question_mark_head_pins_nothing(self):
        self.seq({"504": [view("SUCCESS", head="1111111aaa")]})
        r = self.watch_any("504", "?")
        self.assertEqual(r.stdout.splitlines(), ["PR #504 pass CLEAN 1111111"])
        self.assertEqual(len(self.env.gh_calls()), 1)

    def test_a_head_with_no_check_run_ends_as_no_ci_run(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "UNKNOWN", "headRefOid": "5ba15c1" + "c" * 33, "state": "OPEN"}
        self.seq({"504": [empty]})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "504", check=False)
        self.assertEqual(r.stdout.splitlines(), ["PR #504 error no CI run for 5ba15c1, close-and-reopen fires one"])
        self.assertEqual(r.returncode, 3)

    def test_a_draft_with_no_check_is_not_a_missing_run(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "UNKNOWN", "headRefOid": "abc1234", "state": "OPEN", "isDraft": True}
        ready = view("SUCCESS", head="abc1234")
        self.seq({"7": [empty, empty, ready]})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "7", check=False)
        self.assertEqual(r.stdout.splitlines(), ["PR #7 pass CLEAN abc1234"])

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
        self.assertIn("gh failed: HTTP 502", r.stderr)
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
        for reply in ("{}","<html>not json"):
            with self.subTest(reply=reply):
                self.env.set_gh([{"args": ["pr", "list"], "stdout": reply}])
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

    def test_a_pr_missing_from_the_reply_is_retried_and_does_not_end_the_watch(self):
        self.seq({"6": ["MISSING", "MISSING", view("SUCCESS", head="6666666ccc")],
                  "7": [view(None), view(None), view(None), view("SUCCESS", head="7777777ddd")]})
        r = self.watch_any("6", "7")
        self.assertEqual(r.stdout.splitlines(), ["PR #6 pass CLEAN 6666666", "PR #7 pass CLEAN 7777777"])
        self.assertEqual(r.returncode, 0)

    def test_three_misses_end_in_an_error_and_the_others_carry_on(self):
        self.seq({"6": ["MISSING"], "7": [view(None), view(None), view("SUCCESS", head="7777777ccc")]})
        r = self.watch_any("6", "7")
        self.assertIn("PR #6 error not in gh's pr list", r.stdout)
        self.assertNotIn("PR #7 pass", r.stdout)  # a pass after an error in the same run is never printed
        self.assertIn("PR #7 error pass withheld", r.stdout)
        self.assertEqual(r.returncode, 3)

    def test_three_watched_prs_cost_one_gh_call_per_tick(self):
        self.seq({"1": [view(None), view(None), view("SUCCESS", head="1111111aaa")],
                  "2": [view(None), view("SUCCESS", head="2222222bbb")],
                  "3": [view(None), view(None), view("SUCCESS", head="3333333ccc")]})
        r = self.watch("1", "2", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 3)
        calls = self.env.gh_calls()
        self.assertEqual(len(calls), 3)  # three ticks, one request each
        self.assertTrue(all(c[:2] == ["pr", "list"] for c in calls))

    def test_default_interval_is_30_with_two_prs_and_60_with_three(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        loader = importlib.machinery.SourceFileLoader("watch_ci_under_test", str(SCRIPTS / "watch-ci"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertEqual([mod.interval_for(n) for n in (1, 2, 3, 5)], [30, 30, 60, 60])
        self.assertEqual([mod.interval_for(n, 7) for n in (2, 3)], [7, 7])
        self.assertEqual(mod.interval_for(3, 0), 0)
        cases = ((["1", "2"], [], 30), (["1", "2", "3"], [], 60), (["1", "2", "3"], ["--interval", "5"], 5))
        for prs, flags, want in cases:
            with self.subTest(prs=prs, flags=flags):
                self.seq({pr: [view(None), view("SUCCESS")] for pr in prs})
                slept = []
                env = dict(PATH=f"{self.env.bin}{os.pathsep}{os.environ['PATH']}", FAKE_GH_STATE=str(self.env.gh_state))
                old = os.getcwd()
                os.chdir(self.env.repo)
                try:
                    with mock.patch.dict(os.environ, env), mock.patch.object(mod.time, "sleep", slept.append):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(mod.main([*flags, *prs]), 0)
                finally:
                    os.chdir(old)
                self.assertEqual(slept, [want])

    def test_no_repository_failure_ends_at_once(self):
        self.env.set_gh([{"args": ["pr", "list"], "stderr": "fatal: not a git repository\n", "exit": 1}])
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


RUN_URL = "https://github.com/o/r/actions/runs/900/job/901"


def running(url=RUN_URL):
    v = view(None)
    v["statusCheckRollup"] = [{"status": "IN_PROGRESS", "conclusion": "", "detailsUrl": url}]
    return v


def jobs_reply(steps, status="in_progress"):
    job = {"id": 901, "name": "validate", "status": status, "steps": [{"status": s} for s in steps]}
    return {"args": ["api"], "stdout": json.dumps({"jobs": [job]})}


class StuckJob(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def stuck_watch(self, jobs, after, ticks=3, url=RUN_URL):
        rows = [dict(running(url), number=5)] * ticks + [dict(view("SUCCESS", head="5555555aaa"), number=5)]
        self.env.set_gh([{"args": ["pr", "list"], "seq": [{"stdout": json.dumps([r])} for r in rows]}, jobs])
        return self.env.run("watch-ci", "--interval", "0", "--stuck-after", str(after), "5")

    def test_a_job_with_all_steps_done_still_in_progress_is_reported_once(self):
        r = self.stuck_watch(jobs_reply(["completed", "completed"]), 0)
        self.assertEqual(r.stdout.splitlines(), [
            "PR #5: job validate stuck in_progress with all steps done; rerun with gh run rerun 900 --job 901",
            "PR #5 pass CLEAN 5555555"])
        self.assertIn(["api", "repos/{owner}/{repo}/actions/runs/900/jobs?per_page=100"],
                      self.env.gh_calls())

    def test_a_reported_run_is_not_read_again_until_its_checks_change(self):
        self.stuck_watch(jobs_reply(["completed"]), 0, ticks=4)
        reads = [c for c in self.env.gh_calls() if c[0] == "api"]
        self.assertEqual(reads, [["api", "repos/{owner}/{repo}/actions/runs/900/jobs?per_page=100"]])

    def test_a_sibling_job_that_sticks_on_a_later_tick_is_reported_too(self):
        def job(i, steps):
            return {"id": i, "name": f"job{i}", "status": "in_progress", "steps": [{"status": x} for x in steps]}
        urls = [f"https://github.com/o/r/actions/runs/900/job/{i}" for i in (901, 902)]
        pending = dict(view(None), number=5, statusCheckRollup=[
            {"status": "IN_PROGRESS", "conclusion": "", "detailsUrl": u} for u in urls])
        rows = [pending] * 4 + [dict(view("SUCCESS", head="5555555aaa"), number=5)]
        first = {"stdout": json.dumps({"jobs": [job(901, ["completed"]), job(902, ["completed", "in_progress"])]})}
        later = {"stdout": json.dumps({"jobs": [job(901, ["completed"]), job(902, ["completed", "completed"])]})}
        self.env.set_gh([{"args": ["pr", "list"], "seq": [{"stdout": json.dumps([r])} for r in rows]},
                         {"args": ["api"], "seq": [first, later]}])
        r = self.env.run("watch-ci", "--interval", "0", "--stuck-after", "0", "5")
        self.assertEqual(r.stdout.splitlines(), [
            "PR #5: job job901 stuck in_progress with all steps done; rerun with gh run rerun 900 --job 901",
            "PR #5: job job902 stuck in_progress with all steps done; rerun with gh run rerun 900 --job 902",
            "PR #5 pass CLEAN 5555555"])

    def test_a_check_without_a_run_id_makes_no_jobs_call(self):
        for url in ("", "https://ci.example.com/build/7"):
            self.stuck_watch(jobs_reply(["completed"]), 0, url=url)
            self.assertEqual([c for c in self.env.gh_calls() if c[0] == "api"], [])

    def test_a_settled_pr_makes_no_jobs_call(self):
        self.env.set_gh([{"args": ["pr", "list"], "stdout": json.dumps([dict(view("SUCCESS"), number=5)])}, jobs_reply(["completed"])])
        self.env.run("watch-ci", "--interval", "0", "5")
        self.assertEqual([c for c in self.env.gh_calls() if c[0] == "api"], [])

    def test_nothing_is_reported_before_the_wait_is_over(self):
        r = self.stuck_watch(jobs_reply(["completed"]), 3600)
        self.assertEqual(r.stdout.splitlines(), ["PR #5 pass CLEAN 5555555"])

    def test_a_job_with_a_step_still_running_is_not_stuck(self):
        r = self.stuck_watch(jobs_reply(["completed", "in_progress"]), 0)
        self.assertEqual(r.stdout.splitlines(), ["PR #5 pass CLEAN 5555555"])

    def test_a_failed_jobs_read_does_not_end_the_watch(self):
        r = self.stuck_watch({"args": ["api"], "stderr": "HTTP 502: bad gateway\n", "exit": 1}, 0)
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["PR #5 pass CLEAN 5555555"], 0), r.stderr)


if __name__ == "__main__":
    unittest.main()
