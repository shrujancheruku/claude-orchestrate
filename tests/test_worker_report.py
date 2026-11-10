"""scripts/worker-report through its CLI, with the fake `gh` serving the open PRs; and the prompt side (ADR-0006)."""

import json
import re
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Run, ScriptEnv, gh_pulls  # noqa: E402


def one_verdict(r) -> str:
    """The words after `next:` in a run's output, which must hold that one line and nothing else (ADR-0014)."""
    run = Run(r)
    assert run.stdout == "", run.stdout
    return run.verdict[len("next: "):]

ROOT = Path(__file__).resolve().parent.parent
HEAD = "7e38ea0aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD2 = "9f00000bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BRANCH = "260-worker-report"
NOTICE = "<task-id>a1</task-id>\n<status>completed</status>\n<summary>Agent completed</summary>\n"


def prs(*items, draft=()):
    return [gh_pulls([{"number": n, "headRefName": b, "headRefOid": h, "isDraft": n in draft} for n, b, h in items], "open")]


class WorkerReport(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.env.set_gh(prs((12, BRANCH, HEAD)))

    def report(self, text, ticket="260", check=True, no_gh=False, cache=None):
        # ScriptEnv.run has no stdin: run the CLI the way it does (fake gh first on PATH), with the completion piped in.
        if no_gh:
            empty = tempfile.mkdtemp()
            self.addCleanup(os.rmdir, empty)
            path = empty
        else:
            path = f"{self.env.bin}{os.pathsep}{os.environ['PATH']}"
        env = dict(os.environ, PATH=path, FAKE_GH_STATE=str(self.env.gh_state), ORCHESTRATE_GH_5XX_TRIES="1")
        if cache:
            env["XDG_CACHE_HOME"] = cache  # the rate-limit pause is cached here, never in the developer's cache
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "worker-report"), ticket, "--branch", BRANCH, "--repo", "."],
            cwd=self.env.repo, env=env, input=text, capture_output=True, text=True, check=check)

    def reviewer(self, text):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--reviewer"],
                           cwd=self.env.repo, input=text, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return one_verdict(r)

    def test_reviewer_report_with_a_suite_line_is_valid(self):
        for suite in ("pass", "fail 2", "fail", "not run (S tier)", "`pass`",
                      "pass (py_compile, run_parallel, plugin validate)", "fail (run_parallel: 2 modules)"):
            with self.subTest(suite=suite):
                self.assertEqual(self.reviewer(f"verdict: approve\nround: 1\nsuite: {suite}\nblocking: 0\n"), "valid")

    def test_reviewer_report_without_a_suite_line_is_incomplete(self):
        self.assertEqual(self.reviewer("verdict: approve\nround: 1\nblocking: 0\n"), "incomplete suite-missing")

    def test_reviewer_report_with_not_run_and_no_reason_is_incomplete(self):
        for suite in ("not run", "not run ()"):
            with self.subTest(suite=suite):
                self.assertEqual(self.reviewer(f"suite: {suite}\n"), "incomplete suite-not-run-without-reason")

    def test_reviewer_report_with_another_suite_value_is_incomplete(self):
        self.assertEqual(self.reviewer("suite: probably fine\n"), "incomplete suite-unrecognised")
        self.assertEqual(self.reviewer("suite: passing\n"), "incomplete suite-unrecognised")

    def test_reviewer_report_with_an_empty_suite_value_is_unrecognised(self):
        self.assertEqual(self.reviewer("suite:\n"), "incomplete suite-unrecognised")
        self.assertEqual(self.reviewer("suite:   \n"), "incomplete suite-unrecognised")

    REVIEW = ("verdict: changes\nround: 2\nhead: abc123\nsuite: fail 1\nblocking: 2\nnon-blocking: 3\n"
              "posted: https://github.com/o/r/pull/494#pullrequestreview-9\n")

    def reviews(self):
        return [e for e in self.env.events() if e["kind"] == "review"]

    def test_reviewer_mode_needs_no_branch_and_writes_a_review_event(self):
        self.assertEqual(self.reviewer(self.REVIEW), "valid")
        (e,) = self.reviews()
        self.assertEqual((e["ref"], e["outcome"], e["round"], e["head"], e["suite"], e["blocking"], e["non_blocking"]),
                         (494, "request-changes", 2, "abc123", "fail 1", 2, 3))
        self.assertEqual(e["posted"], "https://github.com/o/r/pull/494#pullrequestreview-9")
        self.assertEqual([e for e in self.env.events() if e["kind"] == "worker-report"], [])

    def test_review_event_outcomes(self):
        self.reviewer("verdict: approve\nround: 1\nhead: a1\nsuite: pass\nblocking: 0\nnon-blocking: 0\nposted: off\n")
        self.reviewer("verdict: approve\nround: 1\nhead: a1\n")  # no suite: incomplete
        self.assertEqual([e["outcome"] for e in self.reviews()], ["approve", "incomplete"])

    def test_a_resent_review_report_writes_one_event(self):
        self.reviewer(self.REVIEW)
        self.reviewer(self.REVIEW)
        self.assertEqual(len(self.reviews()), 1)
        self.reviewer(self.REVIEW.replace("round: 2", "round: 3"))
        self.reviewer(self.REVIEW.replace("abc123", "def456"))
        self.assertEqual(len(self.reviews()), 3)

    def verdict(self, text, **kw):
        r = self.report(text, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return one_verdict(r)

    def test_pr_opened_takes_the_pr_and_head_from_the_host(self):
        out = self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/12\nbranch: x\n")
        self.assertEqual(out, f"valid pr-opened 12 {HEAD}")

    def test_no_session_keeps_the_verdict_and_exit_code_and_says_so_on_stderr(self):
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        r = self.report("status: pr-opened\npr: https://github.com/o/r/pull/12\nbranch: x\n")
        self.assertEqual((r.returncode, one_verdict(r)), (0, f"valid pr-opened 12 {HEAD}"))
        self.assertEqual(r.stderr, "runlog: no session, worker-report event not written\n")

    def test_pr_opened_without_a_pr_line_finds_the_branch(self):
        self.assertEqual(self.verdict("status: pr-opened\npr: none\n"), f"valid pr-opened 12 {HEAD}")

    def test_pr_url_naming_no_open_pr_and_no_branch_pr_is_not_a_pr_opened(self):
        self.env.set_gh(prs((30, "other", HEAD)))
        self.assertEqual(self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/99\n"), "not-a-pr-opened")

    def test_a_fork_pr_on_the_workers_branch_name_is_not_its_pr(self):
        # ADR-0015: an outsider's PR from a fork named like the branch does not make a pr-opened
        self.env.set_gh([gh_pulls([{"number": 30, "headRefName": BRANCH, "headRefOid": HEAD, "isCrossRepository": True}], "open")])
        self.assertEqual(self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/30\n"), "not-a-pr-opened")

    def test_stopped_with_a_listed_reason_is_valid(self):
        for reason in ("held-path", "no-spec", "ambiguous", "adr-conflict", "human-only",
                       "verify-outside-ticket", "already-done", "no-primary-source", "worktree-gone"):
            with self.subTest(reason):
                self.assertEqual(self.verdict(f"status: stopped\nstop-reason: {reason}\n", ticket=reason),
                                 f"valid stopped {reason}")

    def test_permission_denied_with_the_command_is_valid_and_the_event_carries_it(self):
        cmd = 'gh pr create --title "feat: x" --body-file /tmp/b.md --base main --head 260-x'
        self.assertEqual(self.verdict(f"status: stopped\nstop-reason: permission-denied\ndenied-command: {cmd}\n"),
                         "valid stopped permission-denied")
        self.assertEqual(self.env.events()[-1]["denied_command"], cmd)

    def test_a_second_permission_denied_with_another_command_is_not_a_duplicate(self):
        a = "status: stopped\nstop-reason: permission-denied\ndenied-command: gh pr create --head x\n"
        self.assertEqual(self.verdict(a), "valid stopped permission-denied")
        self.assertEqual(self.verdict(a), "duplicate valid stopped permission-denied")
        self.assertEqual(self.verdict(a.replace("--head x", "--head y")), "valid stopped permission-denied")

    def test_permission_denied_without_the_command_is_not_a_stop(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: permission-denied\n"), "not-a-stop")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: permission-denied\ndenied-command:   \n"), "not-a-stop")

    def test_stopped_without_or_with_an_unlisted_reason_is_not_a_stop(self):
        self.assertEqual(self.verdict("status: stopped\nstopped-because: not finished\n"), "not-a-stop")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: tired\n"), "not-a-stop")

    def test_no_status_line_is_interim(self):
        self.assertEqual(self.verdict(NOTICE + "Verify is running in the background; waiting.\n"), "interim")

    def test_a_draft_is_not_a_pr_opened_and_a_ready_one_beside_it_is(self):
        self.env.set_gh(prs((12, BRANCH, HEAD), draft={12}))
        self.assertEqual(self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/12\n"), "not-a-pr-opened")
        self.assertEqual(self.verdict(NOTICE), "missing-report none")
        self.env.set_gh(prs((12, BRANCH, HEAD), (13, "other", "e" * 40), draft={12}))
        self.assertEqual(self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/13\n"),
                         f"valid pr-opened 13 {'e' * 40}")

    def test_bare_notice_finds_the_branch_pr_or_none(self):
        self.assertEqual(self.verdict(NOTICE), f"missing-report pr 12 {HEAD}")
        self.env.set_gh(prs())
        self.assertEqual(self.verdict(NOTICE), "missing-report none")

    def test_usage_limit_with_and_without_a_reset_time(self):
        self.assertEqual(self.verdict("You've hit your usage limit. Your limit resets at 5:30pm (Europe/London)\n"),
                         "limit 5:30pm (Europe/London)")
        self.assertEqual(self.verdict("Claude usage limit reached"), "limit")

    def test_other_api_error_is_an_error_with_a_gist(self):
        out = self.verdict(NOTICE + "API Error: 529 overloaded_error\n")
        self.assertEqual(out, "error API Error: 529 overloaded_error")

    def test_a_report_beats_limit_wording(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\nstopped-because: rate limit on a secret\n"),
                         "valid stopped human-only")

    def test_events_are_written(self):
        self.verdict("status: pr-opened\n")
        self.verdict("status: stopped\nstop-reason: ambiguous\n", ticket="261")
        self.verdict("working on it", ticket="262")
        ev = [e for e in self.env.events() if e["kind"] == "worker-report"]
        self.assertEqual([(e["ref"], e["outcome"]) for e in ev], [(260, "valid"), (261, "valid"), (262, "interim")])
        self.assertEqual((ev[0]["status"], ev[0]["head"], ev[0]["pr"]), ("pr-opened", HEAD, 12))
        self.assertEqual((ev[1]["status"], ev[1]["stop_reason"]), ("stopped", "ambiguous"))
        self.assertNotIn("status", ev[2])

    def refused_of(self, text, ticket="260"):
        self.verdict(text, ticket=ticket)
        return [e for e in self.env.events() if e["kind"] == "worker-report"][-1].get("refused", "absent")

    def test_refused_lines_ride_in_the_event(self):
        a = "Bash — git push origin x — denied by the guard"
        b = "Edit — /a/b.py — permission denied"
        self.assertEqual(self.refused_of(f"status: pr-opened\nrefused: {a}\nrefused: {b}\n"), [a, b])

    def test_refused_none_is_an_empty_list_and_no_line_leaves_the_field_out(self):
        self.assertEqual(self.refused_of("status: pr-opened\nrefused: none\n"), [])
        self.assertEqual(self.refused_of("status: stopped\nstop-reason: ambiguous\n", ticket="261"), "absent")
        self.assertEqual(self.verdict("status: pr-opened\n", ticket="262"), "valid pr-opened 12 " + HEAD)

    def test_refused_lines_do_not_change_the_verdict(self):
        self.assertEqual(self.verdict("status: pr-opened\nrefused: Bash — x — y\n"), "valid pr-opened 12 " + HEAD)

    def test_followup_needed_rides_in_the_event_and_does_not_change_the_verdict(self):
        text = "status: pr-opened\nfollowup-needed: 2 minors left on a.py:3 and b.py:9\n"
        self.assertEqual(self.verdict(text), "valid pr-opened 12 " + HEAD)
        ev = [e for e in self.env.events() if e["kind"] == "worker-report"][-1]
        self.assertEqual(ev["followup_needed"], "2 minors left on a.py:3 and b.py:9")

    def test_a_repeat_is_a_duplicate_and_a_new_head_is_valid(self):
        text = "status: pr-opened\n"
        self.assertEqual(self.verdict(text), f"valid pr-opened 12 {HEAD}")
        self.assertEqual(self.verdict(text), f"duplicate valid pr-opened 12 {HEAD}")
        self.assertEqual(self.verdict(text), f"duplicate valid pr-opened 12 {HEAD}")
        self.env.set_gh(prs((12, BRANCH, HEAD2)))
        self.assertEqual(self.verdict(text), f"valid pr-opened 12 {HEAD2}")
        self.assertEqual([e["outcome"] for e in self.env.events() if e["kind"] == "worker-report"],
                         ["valid", "duplicate", "duplicate", "valid"])

    def test_a_repeated_stop_is_a_duplicate_only_with_the_same_reason(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: ambiguous\n"), "valid stopped ambiguous")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: ambiguous\n"), "duplicate valid stopped ambiguous")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\n"), "valid stopped human-only")

    def test_transient_carries_the_retry_fields(self):
        text = "status: stopped\nstop-reason: transient\nfailed-command: gh pr ready 12\nretry-after: 300\n"
        self.assertEqual(self.verdict(text), "valid stopped transient")
        e = [e for e in self.env.events() if e["kind"] == "worker-report"][-1]
        self.assertEqual((e["stop_reason"], e["failed_command"], e["retry_after"]), ("transient", "gh pr ready 12", "300"))

    def test_a_mislabelled_rate_limit_or_wait_is_reclassified_transient(self):
        out = self.verdict("status: stopped\nstop-reason: permission-denied\ndenied-command: ! gh pr ready 12\n"
                           "stopped-because: GraphQL: API rate limit exceeded\n")
        self.assertEqual(out, "valid stopped transient")
        e = [e for e in self.env.events() if e["kind"] == "worker-report"][-1]
        self.assertEqual(e["failed_command"], "! gh pr ready 12")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\nstopped-because: waiting until 14:05 UTC\n"),
                         "valid stopped transient")
        # A plain permission denial and a plain human-only stop are unchanged.
        self.assertEqual(self.verdict("status: stopped\nstop-reason: permission-denied\ndenied-command: gh pr create\n"),
                         "valid stopped permission-denied")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\nstopped-because: merge the release PR\n"),
                         "valid stopped human-only")

    def test_transient_is_retried_twice_then_exhausted(self):
        text = "status: stopped\nstop-reason: transient\nfailed-command: gh pr ready 12\n"
        self.assertEqual(self.verdict(text), "valid stopped transient")
        self.assertEqual(self.verdict(text), "valid stopped transient")
        self.assertEqual(self.verdict(text), "valid stopped transient exhausted")
        events = [e for e in self.env.events() if e["kind"] == "worker-report"]
        self.assertEqual([e.get("exhausted") for e in events], [None, None, True])
        # A different command starts a new count; another verdict in between resets it.
        other = "status: stopped\nstop-reason: transient\nfailed-command: gh pr create\n"
        self.assertEqual(self.verdict(other), "valid stopped transient")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: ambiguous\n"), "valid stopped ambiguous")
        self.assertEqual(self.verdict(other), "valid stopped transient")

    def test_the_bound_ignores_interim_and_a_bang_prefix(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: transient\nfailed-command: gh pr ready 12\n"),
                         "valid stopped transient")
        self.assertEqual(self.verdict("working on it"), "interim")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: transient\nfailed-command: ! gh  pr ready 12\n"),
                         "valid stopped transient")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: transient\nfailed-command: gh pr ready 12\n"),
                         "valid stopped transient exhausted")

    def test_waiting_on_a_person_is_not_a_wait_until_a_time(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\nstopped-because: waiting until you confirm\n"),
                         "valid stopped human-only")

    def test_exit_2_without_gh(self):
        r = self.report("status: pr-opened\n", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(Run(r).verdict, "next: could-not-run")

    def test_exit_2_on_a_failed_pr_lookup(self):
        self.env.set_gh([{"args": ["api", "~repos/{owner}/{repo}/pulls"], "stderr": "HTTP 502", "exit": 1}])
        r = self.report("status: pr-opened\n", check=False)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))
        self.assertEqual(Run(r).verdict, "next: could-not-run")
        self.assertEqual([e for e in self.env.events() if e["kind"] == "worker-report"], [])

    def test_a_rate_limit_ends_retry_after_with_the_reset_time(self):
        self.env.set_gh([{"args": ["api", "~repos/{owner}/{repo}/pulls"], "exit": 1,
                          "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}])
        with tempfile.TemporaryDirectory() as cache:
            r = self.report("status: pr-opened\n", check=False, cache=cache)
        self.assertEqual((r.returncode, Run(r).stdout), (2, ""))
        self.assertRegex(Run(r).verdict, r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_a_program_error_in_a_reviewer_report_ends_could_not_run(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--reviewer", "--post-risk-notes", "--bogus"],
                           cwd=self.env.repo, input="", capture_output=True, text=True)
        self.assertEqual((r.returncode, Run(r).verdict), (2, "next: could-not-run"))

    def test_help_prints_no_verdict_line(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("\nnext: ", "\n" + r.stdout)

    def test_post_risk_notes_line_comes_before_the_one_next_line(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--reviewer", "--post-risk-notes"],
                           cwd=self.env.repo, input="verdict: approve\nround: 1\nsuite: pass\nblocking: 0\nposted: on\n",
                           capture_output=True, text=True)
        run = Run(r)
        self.assertEqual((r.returncode, run.stdout, run.verdict), (0, "risk-notes none\n", "next: valid"))

    def test_exit_2_on_an_unwritable_run_log(self):
        log = self.env.repo / ".claude" / "orchestrate-profiles"
        log.mkdir(parents=True)
        log.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(log.chmod, stat.S_IRWXU)
        r = self.report("working", check=False)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))
        self.assertEqual(Run(r).verdict, "next: could-not-run")


class PromptSide(unittest.TestCase):
    def test_run_skill_calls_worker_report_and_does_not_restate_its_rules(self):
        text = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn('scripts/worker-report"', text)
        section = text[text.index("## Worker reports"):text.index("## Choosing the worker model")]
        for word in ("valid pr-opened", "valid stopped", "interim", "duplicate", "not-a-stop", "not-a-pr-opened",
                     "missing-report pr", "missing-report none", "limit", "error"):
            self.assertIn(word, section)
        self.assertNotIn("is interim", text, "what makes a report interim lives in scripts/worker-report")
        self.assertIsNone(re.search(r"no `status:` line", section), "the missing-status rule lives in the script")
        self.assertNotIn("same head", section, "the duplicate rule lives in the script")
        self.assertNotIn("only reason is unfinished", section, "the not-a-stop rule lives in the script")


if __name__ == "__main__":
    unittest.main()
