import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from harness import SCRIPTS, ScriptEnv, pr_graphql_reply, rest_pull

ROLLUPS_PREFIX = "query($owner: String!, $name: String!) {\n  repository(owner: $owner, name: $name) {"
SETTLED = "next: settled"
OPEN_LIST = ["api", "~repos/{owner}/{repo}/pulls?state=open"]


def canned(steps):
    """The `gh` replies for watch-ci's reads (#546). steps is {"<pr>": [view, ...]}: tick N shows each PR's step
    min(N, last). "GH_ERROR" fails the open-PR list for that tick; "MISSING" leaves a PR out of the list and answers
    its own read 404. An open, ready PR is answered by its rollup query in the order of its ready steps; a merged or
    closed one (or one not listed) by its pull read, and an open one settling by the pull read that gives its merge
    state."""
    ticks = max(len(v) for v in steps.values())
    lists, graphql, pulls = [], [], {pr: [] for pr in steps}
    for n in range(ticks):
        picked = {pr: v[min(n, len(v) - 1)] for pr, v in steps.items()}
        if "GH_ERROR" in picked.values():
            lists.append({"stderr": "HTTP 502: bad gateway\n", "exit": 1})
            continue
        rows, nodes = [], {}
        for pr, v in picked.items():
            if v == "MISSING":
                pulls[pr].append({"stderr": "gh: Not Found (HTTP 404)", "exit": 1})
            elif v.get("state", "OPEN") != "OPEN":
                pulls[pr].append({"stdout": json.dumps(rest_pull(number=int(pr), **v))})
            else:
                rows.append(rest_pull(number=int(pr), **v))
                if not v.get("isDraft"):
                    nodes[f"pr{pr}"] = json.loads(pr_graphql_reply(dict(v, state="OPEN")))["data"]["repository"]["pullRequest"]
        lists.append({"stdout": json.dumps(rows)})
        if nodes:  # one aliased query answers every ready PR of the tick
            graphql.append({"stdout": json.dumps({"data": {"repository": nodes}})})
    responses = [{"args": OPEN_LIST, "seq": lists}]
    for pr, v in steps.items():
        last = v[-1]
        if last not in ("MISSING", "GH_ERROR") and last.get("state", "OPEN") == "OPEN":
            pulls[pr].append({"stdout": json.dumps(rest_pull(number=int(pr), **last))})  # the settle read of the merge state
        if pulls[pr]:
            responses.append({"args": ["api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"], "seq": pulls[pr]})
    if graphql:
        responses.append({"args": ["api", "graphql", "-f", "~query=" + ROLLUPS_PREFIX, "-F", "owner={owner}",
                                   "-F", "name={repo}"], "seq": graphql})
    return responses


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
        self.env.set_gh(canned(steps))

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
            "also: ci-pass 3 3333333ccc",
            "also: ci-fail 2 2222222bbb",
            "also: ci-pass 1 1111111aaa",
            SETTLED,
        ])
        events = self.env.events()
        self.assertEqual([(e["kind"], e["ref"], e["outcome"]) for e in events],
                         [("ci", 3, "pass"), ("ci", 2, "fail"), ("ci", 1, "pass")])
        self.assertEqual(events[1]["merge_state"], "BLOCKED")
        self.assertEqual(events[1]["head"], "2222222")

    def test_no_session_keeps_the_settle_line_and_exit_code_and_says_so_on_stderr(self):
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.seq({"1": [view("SUCCESS", head="1111111aaa")]})
        r = self.watch("1")
        self.assertEqual((r.returncode, r.stdout), (0, "also: ci-pass 1 1111111aaa\nnext: settled\n"))
        self.assertIn("runlog: no session, ci event not written", r.stderr)

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
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 2 2222222bbb", "also: ci-pass 1 1111111aaa", SETTLED])

    def test_empty_rollup_is_pending_until_checks_appear(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"7": [empty, view("SUCCESS", head="abc1234")]})
        self.assertEqual(self.watch("7").stdout.splitlines(), ["also: ci-pass 7 abc1234", SETTLED])

    def load(self):
        loader = importlib.machinery.SourceFileLoader("watch_ci_parse", str(SCRIPTS / "watch-ci"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        return mod

    def test_a_pr_missing_from_the_rollups_is_a_gh_error_not_a_key_error(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        mod = self.load()
        row = {"number": 5, "isDraft": False, "headRefOid": "abc"}
        with mock.patch.object(mod.github, "open_prs_with_heads", return_value=[row]), \
                mock.patch.object(mod.github, "pr_rollups", return_value={}), \
                mock.patch.object(mod.github, "could_not_run", return_value=True):
            with self.assertRaises(mod.github.GhError) as caught:
                mod.view_prs(".", ["5"])
        self.assertEqual(caught.exception.kind, mod.github.MALFORMED)

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
        self.assertEqual((r.stdout.splitlines(), r.returncode), ([f"also: ci-pass 504 {sha}", SETTLED], 0))
        self.assertNotIn("gave-up", r.stdout)

    def test_a_bad_argument_is_could_not_run(self):
        r = self.watch_any("504", "nonsense")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "next: could-not-run\n")
        self.assertIn("neither a PR number nor the head", r.stderr)

    def test_a_pinned_head_waits_for_the_pr_to_move_to_it(self):
        new = "5ba15c1" + "b" * 33
        old_green = view("SUCCESS", head="0000000aaa")
        self.seq({"504": [old_green, old_green, view("SUCCESS", head=new)]})
        r = self.watch_any("504", new)
        self.assertEqual(r.stdout.splitlines(), [f"also: ci-pass 504 {new}", SETTLED])
        self.assertGreaterEqual(len(self.env.gh_calls()), 3)  # the old head's green was not taken for the new one

    def test_a_question_mark_head_pins_nothing(self):
        self.seq({"504": [view("SUCCESS", head="1111111aaa")]})
        r = self.watch_any("504", "?")
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 504 1111111aaa", SETTLED])
        self.assertEqual(len(self.env.gh_calls()), 3)  # the list, the rollup query, the merge state at settle

    WF = ["api", "~repos/{owner}/{repo}/contents/.github/workflows?ref="]

    def seq_with_workflows(self, steps, listing):
        """seq, plus the contents call that lists the head's workflow files (`listing`: a gh reply)."""
        self.seq(steps)
        state = json.loads(self.env.gh_state.read_text())
        state["responses"].append({"args": self.WF, **listing})
        self.env.gh_state.write_text(json.dumps(state))

    def test_a_head_with_no_check_run_ends_as_no_ci_run(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "UNKNOWN", "headRefOid": "5ba15c1" + "c" * 33, "state": "OPEN"}
        self.seq_with_workflows({"504": [empty]}, {"stdout": '[{"name": "ci.yml"}]'})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "504", check=False)
        self.assertEqual(r.stdout.splitlines(), ["also: no-run 504 " + "5ba15c1" + "c" * 33, SETTLED])
        self.assertEqual(r.returncode, 3)

    def test_an_all_skipped_rollup_is_pending_then_no_run_never_a_pass(self):
        # a draft head skipped its jobs; marked ready, GitHub never adds the ready_for_review run
        sha = "5ba15c1" + "c" * 33
        self.seq_with_workflows({"504": [view("SKIPPED", head=sha, draft=True), view("SKIPPED", head=sha)]},
                                {"stdout": '[{"name": "ci.yml"}]'})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "504", check=False)
        self.assertEqual(r.stdout.splitlines(), ["also: no-run 504 " + sha, SETTLED])
        self.assertEqual(r.returncode, 3)

    def test_skipped_then_the_real_run_arrives_passes(self):
        self.seq({"7": [view("SKIPPED", head="abc1234"), view("NEUTRAL", head="abc1234"), view("SUCCESS", head="abc1234")]})
        self.assertEqual(self.watch("7").stdout.splitlines(), ["also: ci-pass 7 abc1234", SETTLED])

    def test_success_beside_skipped_is_a_pass_and_a_bad_conclusion_a_fail(self):
        def mixed(*results):
            v = view("SUCCESS", head="abc1234")
            v["statusCheckRollup"] = [{"status": "COMPLETED", "conclusion": r} for r in results]
            return v
        self.seq({"7": [mixed("SKIPPED", "SUCCESS")], "8": [mixed("SKIPPED", "FAILURE")]})
        self.assertEqual(self.watch("7", "8").stdout.splitlines(),
                         ["also: ci-pass 7 abc1234", "also: ci-fail 8 abc1234", SETTLED])

    def test_a_head_without_workflow_files_is_not_a_missing_run(self):
        # ADR-0012: no workflow files means no run is expected; the watch keeps waiting and settles once checks appear
        empty = {"statusCheckRollup": [], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq_with_workflows({"7": [empty, empty, view("SUCCESS", head="abc1234")]},
                                {"exit": 1, "stderr": "gh: Not Found (HTTP 404)"})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "7", check=False)
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["also: ci-pass 7 abc1234", SETTLED], 0))
        wf = [c for c in self.env.gh_calls() if c[:2] == ["api", self.WF[1][1:].split("?")[0] + "?ref=abc1234"]]
        self.assertEqual(len(wf), 1)  # asked once for the head, then cached

    def test_an_unreadable_workflow_listing_keeps_the_watch_waiting(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq_with_workflows({"7": [empty, empty, view("SUCCESS", head="abc1234")]},
                                {"exit": 1, "stderr": "gh: Server Error (HTTP 500)"})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "7", check=False)
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["also: ci-pass 7 abc1234", SETTLED], 0))

    def test_a_pinned_head_the_pr_never_reaches_ends_the_watch(self):
        stuck = view("SUCCESS", head="0000000aaa")
        self.seq({"504": [stuck]})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "504", "5ba15c1" + "d" * 33, check=False)
        self.assertEqual(r.stdout.splitlines(), ["also: head-not-reached 504 " + "5ba15c1" + "d" * 33, SETTLED])
        self.assertEqual(r.returncode, 3)

    def test_a_draft_with_no_check_is_not_a_missing_run(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "UNKNOWN", "headRefOid": "abc1234", "state": "OPEN", "isDraft": True}
        ready = view("SUCCESS", head="abc1234")
        self.seq({"7": [empty, empty, ready]})
        r = self.env.run("watch-ci", "--interval", "0", "--no-run-after", "0", "7", check=False)
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 7 abc1234", SETTLED])

    def test_status_context_failure(self):
        v = {"statusCheckRollup": [{"state": "ERROR"}], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"8": [v]})
        self.assertEqual(self.watch("8").stdout.splitlines(), ["also: ci-fail 8 abc1234", SETTLED])

    def test_merged_settles_as_pass_and_closed_as_fail(self):
        self.seq({"4": [view(None, state="MERGED", head="4444444aaa")],
                  "5": [view(None, state="CLOSED", merge="DIRTY", head="5555555bbb")]})
        out = self.watch("4", "5").stdout.splitlines()
        self.assertEqual(out, ["also: ci-pass 4 4444444aaa", "also: ci-fail 5 5555555bbb", SETTLED])
        self.assertEqual([(e["ref"], e["outcome"]) for e in self.env.events()], [(4, "pass"), (5, "fail")])

    def test_gh_error_is_retried(self):
        self.seq({"6": ["GH_ERROR", "GH_ERROR", view("SUCCESS", head="6666666ccc")]})
        r = self.watch("6")
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 6 6666666ccc", SETTLED])
        self.assertIn("gh failed: HTTP 502", r.stderr)
        self.assertEqual(len([c for c in self.env.gh_calls() if c[1].startswith("repos/{owner}/{repo}/pulls?state=open")]), 3)

    def test_unwritable_event_log_still_prints_every_line(self):
        # A file where the profiles directory should be makes append_event raise OSError.
        claude = self.env.repo / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "orchestrate-profiles").write_text("not a directory")
        self.seq({"1": [view("SUCCESS", head="1111111aaa")], "2": [view("FAILURE", head="2222222bbb")]})
        r = self.watch("1", "2")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 1 1111111aaa", "also: ci-fail 2 2222222bbb", SETTLED])
        self.assertIn("could not write event", r.stderr)

    def test_missing_gh_exits_could_not_run_instead_of_waiting(self):
        r = self.env.run("watch-ci", "--interval", "0", "7", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_reply_that_is_not_a_pr_object_is_could_not_run(self):
        for reply in ("{}","<html>not json"):
            with self.subTest(reply=reply):
                self.env.set_gh([{"args": ["api"], "stdout": reply}])
                r = self.env.run("watch-ci", "--interval", "0", "7", check=False)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertNotIn("Traceback", r.stderr)


    def watch_any(self, *prs, **kw):
        return self.env.run("watch-ci", "--interval", "0", *prs, check=False, **kw)

    def test_repeated_failures_end_in_an_error_line_and_exit_3(self):
        self.seq({"6": ["GH_ERROR"]})
        r = self.watch_any("6")
        self.assertEqual(r.stdout.splitlines(), ["also: gave-up 6 HTTP 502: bad gateway", SETTLED])
        self.assertEqual(r.returncode, 3)
        self.assertEqual(len(self.env.gh_calls()), 5)

    def test_failures_under_the_bound_still_settle(self):
        self.seq({"6": ["GH_ERROR"] * 4 + [view("SUCCESS", head="6666666ccc")]})
        r = self.watch_any("6")
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["also: ci-pass 6 6666666ccc", SETTLED], 0))

    def test_a_success_resets_the_failure_count(self):
        ok = view(None)
        self.seq({"6": ["GH_ERROR"] * 4 + [ok] + ["GH_ERROR"] * 4 + [view("SUCCESS", head="6666666ccc")]})
        r = self.watch_any("6")
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["also: ci-pass 6 6666666ccc", SETTLED], 0))

    def test_a_pr_missing_from_the_reply_is_retried_and_does_not_end_the_watch(self):
        self.seq({"6": ["MISSING", "MISSING", view("SUCCESS", head="6666666ccc")],
                  "7": [view(None), view(None), view(None), view("SUCCESS", head="7777777ddd")]})
        r = self.watch_any("6", "7")
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 6 6666666ccc", "also: ci-pass 7 7777777ddd", SETTLED])
        self.assertEqual(r.returncode, 0)

    def test_three_misses_end_in_an_error_and_the_others_carry_on(self):
        self.seq({"6": ["MISSING"], "7": [view(None), view(None), view("SUCCESS", head="7777777ccc")]})
        r = self.watch_any("6", "7")
        self.assertIn("also: gave-up 6 not in gh's pr list", r.stdout)
        self.assertNotIn("ci-pass 7", r.stdout)  # a pass after an error in the same run is never printed
        self.assertIn("also: gave-up 7 pass withheld", r.stdout)
        self.assertEqual(r.returncode, 3)

    def test_three_watched_prs_cost_one_list_read_and_one_rollup_query_per_tick(self):
        self.seq({"1": [view(None), view(None), view("SUCCESS", head="1111111aaa")],
                  "2": [view(None), view("SUCCESS", head="2222222bbb")],
                  "3": [view(None), view(None), view("SUCCESS", head="3333333ccc")]})
        r = self.watch("1", "2", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 4)  # three settles and the next: line
        calls = self.env.gh_calls()
        # One list read and one aliased rollup query per tick however many PRs are watched (#770): 3 + 3 calls,
        # plus one merge-state read per settle.
        lists = [c for c in calls if c[1].startswith("repos/{owner}/{repo}/pulls?state=open")]
        self.assertEqual(len(lists), 3)
        self.assertEqual(len([c for c in calls if c[1] == "graphql"]), 3)
        self.assertEqual(len(calls), 3 + 3 + 3)
        self.assertEqual(self.env.gh_unmatched(), [])

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
        self.env.set_gh([{"args": ["api"], "stderr": "fatal: not a git repository\n", "exit": 1}])
        r = self.watch_any("6")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "next: could-not-run\n")
        self.assertEqual(len(self.env.gh_calls()), 1)

    def test_a_rate_limit_over_the_wait_budget_ends_retry_after_its_time(self):
        self.env.set_gh([{"args": ["api"], "exit": 1,
                          "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}])
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            r = self.watch_any("6")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip(), r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

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
        self.assertEqual((out.splitlines(), p.returncode), (["also: ci-pass 6 6666666ccc", SETTLED], 0), err)


RUN_URL = "https://github.com/o/r/actions/runs/900/job/901"


def running(url=RUN_URL):
    v = view(None)
    v["statusCheckRollup"] = [{"status": "IN_PROGRESS", "conclusion": "", "detailsUrl": url}]
    return v


def jobs_reply(steps, status="in_progress"):
    job = {"id": 901, "name": "validate", "status": status, "steps": [{"status": s} for s in steps]}
    return {"args": ["api"], "stdout": json.dumps({"jobs": [job]})}


class PrRollups(unittest.TestCase):
    """github.pr_rollups (#770): one aliased query, each PR's result equal to the unbatched pr_view."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        import github
        self.github = github

    @staticmethod
    def node(checks=None, rollup=True, more=False):
        ctx = [dict(c, __typename="CheckRun") for c in checks or []]
        commit = {"statusCheckRollup": {"contexts": {"nodes": ctx, "pageInfo": {"hasNextPage": more}}}} if rollup else {}
        return {"state": "OPEN", "headRefOid": "abc", "commits": {"nodes": [{"commit": commit}]}}

    def test_n_prs_make_one_call_and_match_the_unbatched_result(self):
        skipped = [{"name": "test", "status": "COMPLETED", "conclusion": "SKIPPED"}]
        done = [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        reply = {"data": {"repository": {"pr1": self.node(done), "pr2": self.node(skipped), "pr3": self.node(rollup=False)}}}
        with mock.patch.object(self.github, "call_json", return_value=reply) as call:
            got = self.github.pr_rollups(["1", "#2", 3])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(got["1"]["statusCheckRollup"], [dict(done[0], __typename="CheckRun")])
        self.assertEqual(got["2"]["statusCheckRollup"], [dict(skipped[0], __typename="CheckRun")])
        self.assertEqual(got["3"], {"state": "OPEN", "headRefOid": "abc", "statusCheckRollup": []})
        for n in ("1", "2", "3"):  # the unbatched read of the same node gives the same dict
            with mock.patch.object(self.github, "call_json", return_value={"data": {"repository": {"pullRequest": reply["data"]["repository"][f"pr{n}"]}}}):
                self.assertEqual(self.github.pr_view(n, "state,headRefOid,statusCheckRollup"), got[n])

    def test_a_pr_with_more_checks_or_no_node_is_read_on_its_own(self):
        reply = {"data": {"repository": {"pr1": self.node(more=True), "pr2": None}}}
        with mock.patch.object(self.github, "call_json", return_value=reply), \
                mock.patch.object(self.github, "pr_view", return_value={"own": 1}) as view:
            got = self.github.pr_rollups(["1", "2"])
        self.assertEqual(got, {"1": {"own": 1}, "2": {"own": 1}})
        self.assertEqual(view.call_count, 2)

    def test_a_large_batch_splits_into_chunks_within_the_cost_budget(self):
        """(1 + 2N)/100 points per query (#774): no query holds more than ROLLUP_CHUNK aliases, so none costs over 1 point."""
        n = 3 * self.github.ROLLUP_CHUNK + 7
        sizes = []

        def reply(*args, **kw):
            query = next(a for a in args if a.startswith("query="))
            aliases = [int(m) for m in re.findall(r"pr(\d+): pullRequest", query)]
            sizes.append(len(aliases))
            return {"data": {"repository": {f"pr{i}": self.node() for i in aliases}}}

        with mock.patch.object(self.github, "call_json", side_effect=reply):
            got = self.github.pr_rollups([str(i) for i in range(1, n + 1)] + [1])  # a repeat is read once
        self.assertEqual(sorted(got), sorted(str(i) for i in range(1, n + 1)))
        self.assertEqual(sizes, [self.github.ROLLUP_CHUNK] * 3 + [7])
        self.assertTrue(all((1 + 2 * s) / 100 <= 1 for s in sizes))

    def test_no_ready_pr_makes_no_call(self):
        with mock.patch.object(self.github, "call_json") as call:
            self.assertEqual(self.github.pr_rollups([]), {})
        call.assert_not_called()


class StuckJob(unittest.TestCase):
    def jobs_reads(self):
        return [c for c in self.env.gh_calls() if "/actions/runs/" in c[1]]

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def stuck_watch(self, jobs, after, ticks=3, url=RUN_URL):
        self.env.set_gh([*canned({"5": [running(url)] * ticks + [view("SUCCESS", head="5555555aaa")]}), jobs])
        return self.env.run("watch-ci", "--interval", "0", "--stuck-after", str(after), "5")

    def test_a_job_with_all_steps_done_still_in_progress_is_reported_once(self):
        r = self.stuck_watch(jobs_reply(["completed", "completed"]), 0)
        self.assertEqual(r.stdout.splitlines(), ["also: stuck 5 900 901", "also: ci-pass 5 5555555aaa", SETTLED])
        self.assertIn(["api", "repos/{owner}/{repo}/actions/runs/900/jobs?per_page=100"],
                      self.env.gh_calls())

    def test_a_reported_run_is_not_read_again_until_its_checks_change(self):
        self.stuck_watch(jobs_reply(["completed"]), 0, ticks=4)
        reads = self.jobs_reads()
        self.assertEqual(reads, [["api", "repos/{owner}/{repo}/actions/runs/900/jobs?per_page=100"]])

    def test_a_sibling_job_that_sticks_on_a_later_tick_is_reported_too(self):
        def job(i, steps):
            return {"id": i, "name": f"job{i}", "status": "in_progress", "steps": [{"status": x} for x in steps]}
        urls = [f"https://github.com/o/r/actions/runs/900/job/{i}" for i in (901, 902)]
        pending = dict(view(None), statusCheckRollup=[
            {"status": "IN_PROGRESS", "conclusion": "", "detailsUrl": u} for u in urls])
        first = {"stdout": json.dumps({"jobs": [job(901, ["completed"]), job(902, ["completed", "in_progress"])]})}
        later = {"stdout": json.dumps({"jobs": [job(901, ["completed"]), job(902, ["completed", "completed"])]})}
        self.env.set_gh([*canned({"5": [pending] * 4 + [view("SUCCESS", head="5555555aaa")]}),
                         {"args": ["api"], "seq": [first, later]}])
        r = self.env.run("watch-ci", "--interval", "0", "--stuck-after", "0", "5")
        self.assertEqual(r.stdout.splitlines(), [
            "also: stuck 5 900 901", "also: stuck 5 900 902", "also: ci-pass 5 5555555aaa", SETTLED])

    def test_a_check_without_a_run_id_makes_no_jobs_call(self):
        for url in ("", "https://ci.example.com/build/7"):
            self.stuck_watch(jobs_reply(["completed"]), 0, url=url)
            self.assertEqual(self.jobs_reads(), [])

    def test_a_settled_pr_makes_no_jobs_call(self):
        self.env.set_gh([*canned({"5": [view("SUCCESS")]}), jobs_reply(["completed"])])
        self.env.run("watch-ci", "--interval", "0", "5")
        self.assertEqual(self.jobs_reads(), [])

    def test_nothing_is_reported_before_the_wait_is_over(self):
        r = self.stuck_watch(jobs_reply(["completed"]), 3600)
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 5 5555555aaa", SETTLED])

    def test_a_job_with_a_step_still_running_is_not_stuck(self):
        r = self.stuck_watch(jobs_reply(["completed", "in_progress"]), 0)
        self.assertEqual(r.stdout.splitlines(), ["also: ci-pass 5 5555555aaa", SETTLED])

    def test_a_failed_jobs_read_does_not_end_the_watch(self):
        r = self.stuck_watch({"args": ["api"], "stderr": "HTTP 502: bad gateway\n", "exit": 1}, 0)
        self.assertEqual((r.stdout.splitlines(), r.returncode), (["also: ci-pass 5 5555555aaa", SETTLED], 0), r.stderr)


if __name__ == "__main__":
    unittest.main()
