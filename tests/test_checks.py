"""Unit tests for scripts/checks.py: the timeout-killing shell runner and the stage runner."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import checks  # noqa: E402


class RunShellTest(unittest.TestCase):
    def test_captures_streams_separately(self):
        r = checks.run_shell("echo out; echo err >&2; exit 4")
        self.assertEqual((r.code, r.stdout, r.stderr, r.timed_out), (4, "out\n", "err\n", False))

    def test_merge_stderr(self):
        r = checks.run_shell("echo out; echo err >&2", merge_stderr=True)
        self.assertEqual(r.stderr, "")
        self.assertEqual(sorted(r.stdout.split()), ["err", "out"])

    def test_timeout_kills_and_keeps_partial_output(self):
        start = time.monotonic()
        r = checks.run_shell("echo started; sleep 30", timeout=0.5)
        self.assertTrue(r.timed_out)
        self.assertIsNone(r.code)
        self.assertIn("started", r.stdout)
        self.assertLess(time.monotonic() - start, 10)

    def test_timeout_kills_the_process_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "pid")
            # A background child that holds the pipes open: only a group kill ends the run promptly.
            # The timeout is generous enough that a slow runner has written the pid first.
            r = checks.run_shell(f"sleep 30 & echo $! > {pidfile}; wait", timeout=2)
            self.assertTrue(r.timed_out)
            for _ in range(50):  # bounded wait for the pidfile, ~5s
                if Path(pidfile).exists() and Path(pidfile).read_text().strip():
                    break
                time.sleep(0.1)
            pid = int(Path(pidfile).read_text())
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
            else:
                self.fail("the child outlived the killed group")

    def test_a_grandchild_that_left_the_session_cannot_hang_the_run(self):
        # os.setsid() puts the child beyond the group kill; it keeps the stdout pipe open for 30s.
        child = "import os, time; os.setsid(); time.sleep(30)"
        start = time.monotonic()
        r = checks.run_shell(f"{sys.executable} -c '{child}' & echo started; wait", timeout=0.5)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - start, 20)

    def test_stdin_is_closed(self):
        self.assertEqual(checks.run_shell("cat", timeout=5).code, 0)


class RunStagesTest(unittest.TestCase):
    def test_timed_out_command_fails_and_blocks_later_stages(self):
        results = checks.run_stages([["sleep 30"], ["true"]], timeout=0.5)
        self.assertEqual([r.status for r in results], ["fail", "skip"])
        self.assertIsNone(results[0].code)
        self.assertIn("timed out after 0.5s", results[0].output)
        self.assertFalse(checks.ok(results))
        text = checks.format_results(results)
        self.assertIn("(timed out)", text)
        self.assertIn("timed out after 0.5s", text)

    def test_fast_command_is_unaffected(self):
        (r,) = checks.run_stages([["echo hi"]], timeout=5)
        self.assertEqual((r.status, r.code), ("pass", 0))


if __name__ == "__main__":
    unittest.main()
