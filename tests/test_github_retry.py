"""Unit tests for the secondary-rate-limit retry in scripts/github.py `call`."""

import sys
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
    def run_call(self, *replies, clock=1000.0):
        fake, sleeps = FakeGh(*replies), []
        with unittest.mock.patch.object(common, "run", fake), \
                unittest.mock.patch.object(github, "sleep", sleeps.append), \
                unittest.mock.patch.object(github, "clock", lambda: clock), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            try:
                return fake, sleeps, github.call("api", "x"), None
            except github.GhError as e:
                return fake, sleeps, None, e

    def test_two_secondary_failures_then_success_sleeps_retry_after_twice(self):
        fake, sleeps, out, err = self.run_call(fail(SECONDARY), fail(SECONDARY), OK)
        self.assertEqual((out, err, sleeps, fake.calls), ("done\n", None, [1, 1], 3))

    def test_without_retry_after_backs_off_doubling_to_the_cap(self):
        msg = fail("gh: API rate limit already exceeded for user ID 1. (HTTP 403)")
        fake, sleeps, out, err = self.run_call(msg)
        self.assertEqual(sleeps, [2, 4, 8, 16])
        self.assertEqual((fake.calls, err.kind), (5, github.FAILED))

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

    def test_missing_gh_is_not_retried(self):
        fake, _, _, err = self.run_call(common.Run(common.NOT_FOUND, "", "gh: no such file"))
        self.assertEqual((fake.calls, err.kind), (1, github.MISSING))


if __name__ == "__main__":
    unittest.main()
