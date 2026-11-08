"""Unit tests for the secondary-rate-limit retry in scripts/github.py `call`."""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402
import github  # noqa: E402

SECONDARY = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 1"


class FakeGh:
    """Replies to `common.run("gh", ...)` from a canned list, recording the calls."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, *cmd, **kw):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def fail(stderr):
    return common.Run(1, "", stderr)


OK = common.Run(0, "done\n", "")


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name) / "orchestrate" / "github-ratelimit-github.com.json"
        env = unittest.mock.patch.dict("os.environ", {"XDG_CACHE_HOME": self.dir.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GH_HOST", None)
        github._paths.clear()
        self.addCleanup(github._paths.clear)
        os.environ.pop("ORCHESTRATE_GH_WAIT", None)
        github.waited = 0.0

    def run_call(self, *replies, clock=1000.0):
        fake, sleeps = FakeGh(*replies), []
        with unittest.mock.patch.object(common, "run", fake), \
                unittest.mock.patch.object(github, "sleep", sleeps.append), \
                unittest.mock.patch.object(github, "clock", lambda: clock), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            try:
                return fake, sleeps, github.call("api", "x", cwd=self.dir.name), None
            except github.GhError as e:
                return fake, sleeps, None, e

    def test_two_secondary_failures_then_success_sleeps_retry_after_twice(self):
        fake, sleeps, out, err = self.run_call(fail(SECONDARY), fail(SECONDARY), OK)
        self.assertEqual((out, err, sleeps, fake.calls), ("done\n", None, [1, 1], 3))

    def test_bare_rate_limit_without_headers_waits_the_recorded_minute(self):
        msg = fail("gh: API rate limit already exceeded for user ID 1. (HTTP 403)")
        os.environ["ORCHESTRATE_GH_WAIT"] = "1000"
        fake, sleeps, out, err = self.run_call(msg)
        self.assertEqual(sleeps, [60, 60, 60, 60])  # tens of seconds, not a short ladder
        self.assertEqual((fake.calls, err.kind), (5, github.RATELIMITED))
        self.assertTrue(github.could_not_run(err))

    def test_gives_up_after_five_tries_with_ghs_message(self):
        _, _, _, err = self.run_call(fail(SECONDARY))
        self.assertIn("secondary rate limit", err.message)

    def test_primary_quota_fails_at_once_with_reset_time(self):
        reset = 1000 + 3000  # 1970-01-01T01:06:40Z
        text = f"gh: API rate limit exceeded (HTTP 403)\nx-ratelimit-remaining: 0\nx-ratelimit-reset: {reset}"
        fake, sleeps, _, err = self.run_call(fail(text))
        self.assertEqual((fake.calls, sleeps), (1, []))
        self.assertIn("1970-01-01T01:06:40Z", err.message)

    def test_quota_resetting_in_seconds_is_retried(self):
        text = "gh: API rate limit exceeded (HTTP 403)\nx-ratelimit-remaining: 0\nx-ratelimit-reset: 1005"
        fake, sleeps, out, _ = self.run_call(fail(text), OK)
        self.assertEqual((out, len(sleeps)), ("done\n", 1))

    def test_other_failures_are_not_retried(self):
        fake, sleeps, _, err = self.run_call(fail("gh: Not Found (HTTP 404)"))
        self.assertEqual((fake.calls, sleeps, err.kind), (1, [], github.FAILED))

    def test_error_carries_status_headers_and_first_body_line(self):
        _, _, _, err = self.run_call(fail("gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 1\nmore"))
        self.assertIn("HTTP 403", err.message)
        self.assertIn("retry-after: 1", err.message)
        self.assertIn("secondary rate limit", err.message)

    def test_rate_limit_records_reset_for_other_processes(self):
        self.run_call(fail(SECONDARY), OK, clock=1000.0)
        self.assertEqual(json.loads(self.state.read_text())["reset"], 1001.0)

    def test_no_header_records_a_minute(self):
        self.run_call(fail("gh: API rate limit already exceeded for user ID 1. (HTTP 403)"), OK, clock=1000.0)
        self.assertEqual(json.loads(self.state.read_text())["reset"], 1060.0)

    def test_two_calls_wait_for_a_future_reset_before_calling_gh(self):
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"reset": 1030.0}))
        now = [1000.0]
        order = []
        fake = FakeGh(OK)

        def run(*a, **k):
            order.append(("gh", now[0]))
            return fake(*a, **k)

        def nap(s):
            order.append(("sleep", s))
            now[0] += s

        with unittest.mock.patch.object(common, "run", run), unittest.mock.patch.object(github, "sleep", nap), \
                unittest.mock.patch.object(github, "clock", lambda: now[0]):
            github.call("api", "x", cwd=self.dir.name)
            github.call("api", "y", cwd=tempfile.gettempdir())  # another cwd, same file
        self.assertEqual(order, [("sleep", 30.0), ("gh", 1030.0), ("gh", 1030.0)])
        self.assertFalse(self.state.exists())  # past reset: removed

    def test_wait_over_budget_is_exit_two_without_calling_gh(self):
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"reset": 1000 + 3600.0}))
        fake, sleeps, _, err = self.run_call(OK)
        self.assertEqual((fake.calls, sleeps, err.kind), (0, [], github.RATELIMITED))
        self.assertIn("rate limit until 1970-01-01T01:16:40Z", err.message)
        self.assertTrue(github.could_not_run(err))

    def test_retry_after_over_the_budget_is_returned_not_slept(self):
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300"
        fake, sleeps, _, err = self.run_call(fail(text), OK)
        self.assertEqual((fake.calls, sleeps, err.kind), (1, [], github.RATELIMITED))
        self.assertIn("rate limit until 1970-01-01T00:21:40Z", err.message)
        self.assertEqual(json.loads(self.state.read_text())["reset"], 1300.0)  # the others still wait
        self.assertTrue(github.could_not_run(err))

    def test_retry_after_within_the_budget_is_slept_and_retried(self):
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 20"
        fake, sleeps, out, _ = self.run_call(fail(text), OK)
        self.assertEqual((out, sleeps, fake.calls), ("done\n", [20], 2))

    def test_the_budget_is_total_across_retries(self):
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 40"
        fake, sleeps, _, err = self.run_call(fail(text))
        self.assertEqual((sleeps, fake.calls, err.kind), ([40], 2, github.RATELIMITED))

    def test_the_budget_is_shared_by_the_file_wait_and_the_retries(self):
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"reset": 1040.0}))
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 40"
        fake, sleeps, _, err = self.run_call(fail(text), OK)  # the fake clock stands still, so each wait is 40 s
        self.assertEqual((sleeps, fake.calls, err.kind), ([40.0], 1, github.RATELIMITED))

    def test_env_overrides_the_budget(self):
        os.environ["ORCHESTRATE_GH_WAIT"] = "5"
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 20"
        _, sleeps, _, err = self.run_call(fail(text), OK)
        self.assertEqual((sleeps, err.kind), ([], github.RATELIMITED))

    def test_unwritable_cache_falls_back_to_the_repo_common_dir(self):
        with tempfile.TemporaryDirectory() as repo:
            common.run("git", "init", "-q", repo)
            blocker = Path(self.dir.name) / "blocked"
            blocker.write_text("a file, so mkdir under it fails")
            with unittest.mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(blocker)}), \
                    unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
                path = github._state_path(repo)
            self.assertEqual(path, Path(repo) / ".git" / "orchestrate-ratelimit.json")

    def test_fallback_is_announced_once_however_many_calls(self):
        with tempfile.TemporaryDirectory() as repo:
            common.run("git", "init", "-q", repo)
            blocker = Path(self.dir.name) / "blocked"
            blocker.write_text("a file")
            err = unittest.mock.MagicMock()
            with unittest.mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(blocker)}), \
                    unittest.mock.patch.object(sys, "stderr", err):
                for _ in range(3):
                    github._state_path(repo)
            self.assertEqual(sum("not writable" in "".join(c.args) for c in err.write.call_args_list), 1)

    def test_host_names_the_file(self):
        with unittest.mock.patch.dict("os.environ", {"GH_HOST": "ghe.example"}):
            self.assertEqual(github._state_path(None).name, "github-ratelimit-ghe.example.json")

    def test_garbage_state_file_is_ignored(self):
        self.state.parent.mkdir()
        self.state.write_text("not json")
        fake, sleeps, out, _ = self.run_call(OK)
        self.assertEqual((out, sleeps), ("done\n", []))

    def test_a_write_is_retried_only_on_a_rate_limit(self):
        for text in ("gh: Validation Failed (HTTP 422)", "gh: Server Error (HTTP 502)", "gh: Forbidden (HTTP 403)"):
            fake, sleeps = FakeGh(fail(text)), []
            with unittest.mock.patch.object(common, "run", fake), unittest.mock.patch.object(github, "sleep", sleeps.append):
                with self.assertRaises(github.GhError):
                    github.call("api", "--method", "POST", "x", cwd=self.dir.name)
            self.assertEqual((fake.calls, sleeps), (1, []), text)

    def test_missing_gh_is_not_retried(self):
        fake, _, _, err = self.run_call(common.Run(common.NOT_FOUND, "", "gh: no such file"))
        self.assertEqual((fake.calls, err.kind), (1, github.MISSING))


if __name__ == "__main__":
    unittest.main()
