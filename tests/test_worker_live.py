"""common.worker_liveness and scripts/worker-live (ADR 0009), on a real temporary repo with real `git worktree lock`."""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import ScriptEnv, git  # noqa: E402

import common  # noqa: E402


def own_start_reason(pid=None, shift=0):
    """A harness-format lock reason for a running process: its pid and its start time in UTC."""
    pid = pid or os.getpid()
    start = common.process_start(pid)
    assert start is not None
    from datetime import timedelta
    shown = (start + timedelta(seconds=shift)).astimezone(timezone.utc).strftime("%a %b %e %H:%M:%S %Y")
    return f"claude agent agent-abc (pid {pid} start {shown})"


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class WorkerLiveness(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.wt_dir.cleanup)

    def worktree(self, branch="1-widget", reason=None):
        path = Path(self.wt_dir.name) / branch
        git(self.env.repo, "worktree", "add", "-b", branch, str(path))
        if reason is not None:
            git(self.env.repo, "worktree", "lock", "--reason", reason, str(path))
        return path.resolve()

    def liveness(self, branch="1-widget"):
        return common.worker_liveness(self.env.repo, branch)

    def test_live_for_a_running_pid_with_the_recorded_start_time(self):
        path = self.worktree(reason=own_start_reason())
        state, pid, wt, _ = self.liveness()
        self.assertEqual((state, pid, Path(wt).resolve()), ("live", os.getpid(), path))

    def test_dead_for_a_pid_that_is_not_running(self):
        self.worktree(reason=own_start_reason().replace(f"pid {os.getpid()}", f"pid {dead_pid()}"))
        self.assertEqual(self.liveness()[0], "dead")

    def test_dead_for_a_running_pid_with_another_start_time(self):
        self.worktree(reason=own_start_reason(shift=-3600))  # the pid number was reused
        self.assertEqual(self.liveness()[0], "dead")

    def test_live_when_the_reason_does_not_parse(self):
        self.worktree(reason="held by hand")
        self.assertEqual(self.liveness()[:2], ("live", None))

    def test_none_without_a_lock(self):
        self.worktree()
        self.assertEqual(self.liveness()[0], "none")

    def test_none_without_a_worktree_on_the_branch(self):
        self.assertEqual(self.liveness("9-nothing"), ("none", None, None, None))

    def test_the_lock_time_is_utc_not_local(self):
        reason = own_start_reason()
        env = dict(os.environ, TZ="Asia/Kolkata")
        # A process that starts under another local zone still compares equal once both sides are normalised.
        r = subprocess.run([sys.executable, "-c",
                            "import sys, os; sys.path.insert(0, sys.argv[1]); import common, time; time.tzset(); "
                            "print(common.lock_state(sys.argv[2])[0])",
                            str(Path(common.__file__).parent), reason.replace(f"pid {os.getpid()}", "pid 1")],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "dead")  # pid 1 is not this process: a different start time
        self.assertEqual(common.lock_state(reason), ("live", os.getpid()))


class WorkerLiveCli(WorkerLiveness):
    def cli(self, *args, **kw):
        return self.env.run("worker-live", *args, check=False, **kw)

    def test_live_line(self):
        path = self.worktree(reason=own_start_reason())
        r = self.cli("1-widget")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.split(), ["live", str(os.getpid()), str(path)])

    def test_dead_line(self):
        path = self.worktree(reason=own_start_reason().replace(f"pid {os.getpid()}", f"pid {dead_pid()}"))
        self.assertEqual(self.cli("1-widget").stdout.split(), ["dead", str(path)])

    def test_unparseable_lock_is_live_and_shows_the_reason(self):
        self.worktree(reason="held by hand")
        self.assertIn("(lock: held by hand)", self.cli("1-widget").stdout)

    def test_none_line(self):
        self.assertEqual(self.cli("1-widget").stdout.strip(), "none")

    def test_exit_2_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.cli("1-widget", cwd=d)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
