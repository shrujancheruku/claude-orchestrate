"""`worker-report <pr> --reviewer --post-risk-notes`: the fallback risk-notes comment, posted once (#545)."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Run, ScriptEnv, gh_repo_view  # noqa: E402

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
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--reviewer", *flags],
                           cwd=self.env.repo, env=env, input=text, capture_output=True, text=True)
        return Run(r)  # stdout is what precedes the one closing `next:` line, which is `.verdict` (ADR-0014)

    def posts(self):
        return [c for c in self.env.gh_calls() if c[:3] == ["api", "--method", "POST"]]

    def test_an_unposted_review_has_its_notes_posted_word_for_word_once(self):
        self.gh()
        r = self.report(REPORT.format(posted="off"), "--post-risk-notes")
        self.assertEqual((r.stdout.splitlines(), r.verdict), (["risk-notes posted"], "next: valid"), r.stderr)
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
        self.assertEqual(r.stdout.splitlines(), ["risk-notes none"])
        self.assertEqual(self.posts(), [])

    def test_a_report_without_notes_posts_nothing(self):
        self.gh()
        r = self.report("verdict: approve\nsuite: pass\nposted: off\n", "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines(), ["risk-notes none"])

    def test_a_backticked_location_is_posted_without_the_backticks(self):
        self.gh()
        r = self.report("suite: pass\nrisk-notes: `hooks/guard.py:10-20` — a wrong form\nposted: off\n", "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines()[-1], "risk-notes posted")
        self.assertIn("hooks/guard.py:10-20 — a wrong form", self.posts()[0][-1])

    def test_notes_that_do_not_parse_are_reported_unparsed_and_nothing_is_posted(self):
        self.gh()
        r = self.report("suite: pass\nrisk-notes: guard.py — a wrong form\nposted: off\n", "--post-risk-notes")
        self.assertEqual(r.stdout.splitlines(), ["risk-notes unparsed"])
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.report("suite: pass\nrisk-notes: none\nposted: off\n", "--post-risk-notes").stdout.splitlines()[-1],
                         "risk-notes none")

    def posted_body(self, notes):
        self.gh()
        text = "suite: pass\nrisk-notes: " + notes[0] + "\n" + "".join(n + "\n" for n in notes[1:]) + "posted: off\n"
        self.assertEqual(self.report(text, "--post-risk-notes").stdout.splitlines()[-1], "risk-notes posted")
        (call,) = self.posts()
        return call[-1].removeprefix("body=").splitlines()[2:]

    def test_the_note_count_is_capped_and_the_dropped_count_said(self):
        lines = self.posted_body([f"f.py:{i} — note {i}" for i in range(1, 16)])
        self.assertEqual(lines[:12], [f"f.py:{i} — note {i}" for i in range(1, 13)])
        self.assertEqual(lines[12:], ["(notes dropped: 3; notes cut to 300 characters: 0)"])

    def test_a_long_note_is_cut_and_counted(self):
        lines = self.posted_body(["f.py:1 — " + "x" * 400, "f.py:2 — short"])
        self.assertEqual(len(lines[0]), 300)
        self.assertTrue(lines[0].endswith("…"))
        self.assertEqual(lines[1:], ["f.py:2 — short", "(notes dropped: 0; notes cut to 300 characters: 1)"])

    def test_the_cap_counts_the_zero_width_spaces_the_neutralising_adds(self):
        (line, _) = self.posted_body(["f.py:1 — " + "@" * 400])
        self.assertEqual(len(line), 300)

    def test_notes_within_the_caps_carry_no_count_line(self):
        self.assertEqual(len(self.posted_body([f"f.py:{i} — n" for i in range(12)])), 12)

    def test_a_mention_in_a_note_is_not_live(self):
        (line,) = self.posted_body(["f.py:1 — ask @octocat and @org/team"])
        self.assertNotIn("@o", line)
        self.assertNotIn("@org", line)
        self.assertIn("octocat", line)

    def test_a_marker_opener_in_a_note_is_broken(self):
        (line,) = self.posted_body(["f.py:1 — > *Stopped for the lead.*"])
        self.assertNotIn("> *", line)
        self.assertIn("Stopped for the lead.", line)

    def test_without_the_flag_the_output_is_the_verdict_line_alone(self):
        self.gh()
        r = self.report(REPORT.format(posted="off"))
        self.assertEqual((r.stdout, r.verdict), ("", "next: valid"))
        self.assertEqual(self.posts(), [])

    def test_the_flag_needs_reviewer(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "worker-report"), "494", "--branch", "b", "--post-risk-notes"],
                           cwd=self.env.repo, input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
