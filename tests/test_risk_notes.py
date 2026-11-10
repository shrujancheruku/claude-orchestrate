"""`worker-report <pr> --reviewer --post-risk-notes`: the fallback risk-notes comment, posted once (#545)."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_repo_view  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARKER = "> *Risk notes (review not posted).*"
COMMENTS = "repos/{owner}/{repo}/issues/494/comments"
REPORT = ("verdict: approve\nround: 1\nhead: a1\nsuite: pass\nblocking: 0\nnon-blocking: 0\n"
          "risk-notes: hooks/guard.py:10-20 — a wrong form approves a write\n"
          "- scripts/merge:5 — the fast-forward\n"
          "posted: {posted}\n")


class RiskNotes(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def gh(self, existing=()):
        rows = [dict(body=b, user={"login": u}, author_association=a) for b, u, a in existing]
        self.env.set_gh([gh_repo_view(), {"args": ["api", "~" + COMMENTS], "stdout": json.dumps(rows)},
                         {"args": ["api", "--method", "POST", COMMENTS], "stdout": "{}"}])

    def report(self, text, *flags):
        env = dict(os.environ, PATH=f"{self.env.bin}{os.pathsep}{os.environ['PATH']}", FAKE_GH_STATE=str(self.env.gh_state),
                   ORCHESTRATE_GH_LOG="off", ORCHESTRATE_GH_5XX_TRIES="1")
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--reviewer", *flags],
                              cwd=self.env.repo, env=env, input=text, capture_output=True, text=True)

    def posts(self):
        return [c for c in self.env.gh_calls() if c[:3] == ["api", "--method", "POST"]]

    def test_an_unposted_review_has_its_notes_posted_word_for_word_once(self):
        self.gh()
        r = self.report(REPORT.format(posted="off"), "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines(), ["valid", "risk-notes posted"], r.stderr)
        (call,) = self.posts()
        self.assertEqual(call[-2:], ["-f", "body=" + "\n".join([MARKER, "", "hooks/guard.py:10-20 — a wrong form approves a write",
                                                              "scripts/merge:5 — the fast-forward"])])

    def test_a_failed_post_counts_as_not_posted(self):
        self.gh()
        self.assertEqual(self.report(REPORT.format(posted="failed: HTTP 422"), "--post-risk-notes").stdout.splitlines()[-1],
                         "risk-notes posted")

    def test_a_second_run_posts_nothing_when_a_trusted_comment_has_the_marker(self):
        self.gh(existing=[(MARKER + "\n\nx", "owner", "OWNER")])
        r = self.report(REPORT.format(posted="off"), "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines()[-1], "risk-notes exists")
        self.assertEqual(self.posts(), [])

    def test_a_strangers_marker_comment_does_not_suppress_the_post(self):
        self.gh(existing=[(MARKER, "eve", "NONE")])
        self.assertEqual(self.report(REPORT.format(posted="off"), "--post-risk-notes").stdout.splitlines()[-1], "risk-notes posted")

    def test_a_posted_review_carries_the_notes_so_nothing_is_posted(self):
        self.gh()
        r = self.report(REPORT.format(posted="https://github.com/o/r/pull/494#pullrequestreview-1"), "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines(), ["valid", "risk-notes none"])
        self.assertEqual(self.posts(), [])

    def test_a_report_without_notes_posts_nothing(self):
        self.gh()
        r = self.report("verdict: approve\nsuite: pass\nposted: off\n", "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines(), ["valid", "risk-notes none"])

    def test_without_the_flag_the_output_is_the_verdict_line_alone(self):
        self.gh()
        r = self.report(REPORT.format(posted="off"))
        self.assertEqual(r.stdout.splitlines(), ["valid"])
        self.assertEqual(self.posts(), [])

    def test_the_flag_needs_reviewer(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--branch", "b", "--post-risk-notes"],
                           cwd=self.env.repo, input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
