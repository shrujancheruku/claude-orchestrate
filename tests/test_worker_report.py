"""scripts/worker-report through its CLI, with the fake `gh` serving the open PRs; and the prompt side (ADR-0006)."""

import json
import re
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HEAD = "7e38ea0aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD2 = "9f00000bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BRANCH = "260-worker-report"
NOTICE = "<task-id>a1</task-id>\n<status>completed</status>\n<summary>Agent completed</summary>\n"


def prs(*items):
    return [{"args": ["pr", "list"], "stdout": json.dumps(
        [{"number": n, "headRefName": b, "headRefOid": h} for n, b, h in items])}]


class WorkerReport(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.env.set_gh(prs((12, BRANCH, HEAD)))

    def report(self, text, ticket="260", **kw):
        return self.env.run("worker-report", ticket, "--branch", BRANCH, "--repo", ".", input_=text, **kw)

    def verdict(self, text, **kw):
        r = self.report(text, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_pr_opened_takes_the_pr_and_head_from_the_host(self):
        out = self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/12\nbranch: x\n")
        self.assertEqual(out, f"valid pr-opened 12 {HEAD}")

    def test_pr_opened_without_a_pr_line_finds_the_branch(self):
        self.assertEqual(self.verdict("status: pr-opened\npr: none\n"), f"valid pr-opened 12 {HEAD}")

    def test_pr_url_naming_no_open_pr_and_no_branch_pr_is_not_a_pr_opened(self):
        self.env.set_gh(prs((30, "other", HEAD)))
        self.assertEqual(self.verdict("status: pr-opened\npr: https://github.com/o/r/pull/99\n"), "not-a-pr-opened")

    def test_stopped_with_a_listed_reason_is_valid(self):
        for reason in ("held-path", "no-spec", "ambiguous", "adr-conflict", "human-only",
                       "verify-outside-ticket", "already-done", "no-primary-source"):
            with self.subTest(reason):
                self.assertEqual(self.verdict(f"status: stopped\nstop-reason: {reason}\n", ticket=reason),
                                 f"valid stopped {reason}")

    def test_stopped_without_or_with_an_unlisted_reason_is_not_a_stop(self):
        self.assertEqual(self.verdict("status: stopped\nstopped-because: not finished\n"), "not-a-stop")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: tired\n"), "not-a-stop")

    def test_no_status_line_is_interim(self):
        self.assertEqual(self.verdict(NOTICE + "Verify is running in the background; waiting.\n"), "interim")

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

    def test_a_repeat_is_a_duplicate_and_a_new_head_is_valid(self):
        text = "status: pr-opened\n"
        self.assertEqual(self.verdict(text), f"valid pr-opened 12 {HEAD}")
        self.assertEqual(self.verdict(text), f"duplicate valid pr-opened 12 {HEAD}")
        self.assertEqual(self.verdict(text), f"duplicate valid pr-opened 12 {HEAD}")
        self.env.set_gh(prs((12, BRANCH, HEAD2)))
        self.assertEqual(self.verdict(text), f"valid pr-opened 12 {HEAD2}")
        self.assertEqual([e["outcome"] for e in self.env.events()],
                         ["valid", "duplicate", "duplicate", "valid"])

    def test_a_repeated_stop_is_a_duplicate_only_with_the_same_reason(self):
        self.assertEqual(self.verdict("status: stopped\nstop-reason: ambiguous\n"), "valid stopped ambiguous")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: ambiguous\n"), "duplicate valid stopped ambiguous")
        self.assertEqual(self.verdict("status: stopped\nstop-reason: human-only\n"), "valid stopped human-only")

    def test_exit_2_without_gh(self):
        r = self.report("status: pr-opened\n", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(r.stdout, "")

    def test_exit_2_on_a_failed_pr_lookup(self):
        self.env.set_gh([{"args": ["pr", "list"], "stderr": "HTTP 502", "exit": 1}])
        r = self.report("status: pr-opened\n", check=False)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))
        self.assertEqual(self.env.events(), [])

    def test_exit_2_on_an_unwritable_run_log(self):
        log = self.env.repo / ".claude" / "orchestrate-profiles"
        log.mkdir(parents=True)
        log.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(log.chmod, stat.S_IRWXU)
        r = self.report("working", check=False)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))
        self.assertEqual(r.stdout, "")


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
