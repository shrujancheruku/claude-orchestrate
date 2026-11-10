"""Exit 2 (could not run) for unusable input in rebuild-state, guard-corpus and sandbox-git."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import SCRIPTS, ScriptEnv  # noqa: E402

sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402


class CouldNotRunTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def check(self, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_rebuild_state_unknown_session(self):
        self.check(self.env.run("rebuild-state", "no-such-session", check=False))

    def test_guard_corpus_unknown_session(self):
        projects = self.env.repo.parent / "projects"
        projects.mkdir()
        self.check(self.env.run("guard-corpus", "no-such-session", "--projects-dir", projects, check=False))

    def test_sandbox_git_bad_usage(self):
        self.check(self.env.run("sandbox-git", "--bogus", check=False))


class VerdictHelperTest(unittest.TestCase):
    """The verdict line (ADR-0014): `common.verdict` prints `next: <word> <args>`, returns the exit code, and is the one
    word list. Exit codes keep their numbers; a new meaning gets a new word."""

    TABLE = (  # (word, args, exit code), one row per word in the list
        ("merged", (), 0), ("cleaned", ("7",), 0), ("wait-ci", ("7", "abc"), 5), ("re-review", ("7",), 6),
        ("send-back", ("7",), 1), ("ask-user", ("an open blocker",), 1), ("retry", (), 3), ("review", ("abc",), 0),
        ("rerun-after-ci", (), 3), ("wait-older", ("7", "5"), 3), ("wait-ticket", ("7", "9"), 3),
        ("settled", (), 0),("retry-after", ("2026-01-01T00:00:00Z",), 2),
        ("could-not-run", (), 2), ("rerun", ("7", "abc"), 0), ("already-running", ("7",), 1),
        ("clean", (), 0), ("findings", ("3",), 0), ("due", ("10",), 0), ("not-due", ("4",), 0), ("folded", ("7", "9"), 0),
    )

    def run_verdict(self, word, args, code):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = common.verdict(word, *args, exit_code=code)
        return got, out.getvalue()

    def test_every_word_prints_its_line_and_returns_its_code(self):
        for word, args, code in self.TABLE:
            with self.subTest(word):
                got, out = self.run_verdict(word, args, code)
                self.assertEqual((got, out), (code, " ".join(["next:", word, *args]) + "\n"))

    def test_the_table_covers_the_gate_merge_and_watch_ci_words(self):
        # The watch-ci `also:` words are checked in test_watch_ci; the rest of the list is this table.
        also = {"left", "ci-pass", "ci-fail", "no-run", "stuck", "head-not-reached", "gave-up"}
        self.assertEqual(set(common.VERDICT_WORDS) - also, {w for w, _, _ in self.TABLE})

    def test_an_unknown_word_is_refused(self):
        with self.assertRaises(ValueError):
            common.verdict("merge-it", exit_code=0)

    def test_also_prints_an_also_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            common.also("ci-pass", "7", "abc")
        self.assertEqual(out.getvalue(), "also: ci-pass 7 abc\n")

    def test_rate_limit_time_reads_the_time_of_every_rate_limit_message(self):
        for text in ("rate limit until 2026-01-01T00:05:00Z", "boom (hourly quota used up; resets at 2026-01-01T00:05:00Z)"):
            self.assertEqual(common.rate_limit_time(text), "2026-01-01T00:05:00Z")
        self.assertIsNone(common.rate_limit_time("HTTP 502: bad gateway"))


if __name__ == "__main__":
    unittest.main()
