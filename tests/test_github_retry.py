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

    def test_a_502_waits_then_succeeds(self):
        fake, sleeps, out, err = self.run_call(fail("gh: HTTP 502"), OK)
        self.assertEqual((out, err, sleeps, fake.calls), ("done\n", None, [2], 2))

    def test_a_502_every_time_fails_after_the_tries_naming_the_5xx(self):
        fake, sleeps, _, err = self.run_call(fail("gh: HTTP 502"))
        self.assertEqual((fake.calls, sleeps, err.kind), (5, [2, 4, 8, 16], github.FAILED))
        self.assertIn("HTTP 502", err.message)

    def test_a_502_past_the_wait_budget_raises_naming_the_5xx(self):
        os.environ["ORCHESTRATE_GH_WAIT"] = "3"
        fake, sleeps, _, err = self.run_call(fail("gh: HTTP 502"))
        self.assertEqual((fake.calls, sleeps), (2, [2]))
        self.assertIn("HTTP 502", err.message)
        self.assertIn("rate limit until", err.message)

    def test_a_503_in_a_rest_reply_with_headers_is_retried(self):
        rest = common.Run(1, "HTTP/2.0 503 Service Unavailable\n\n{}", "gh: Service Unavailable (HTTP 503)")
        fake, sleeps, out, _ = self.run_call(rest, OK)
        self.assertEqual((out, sleeps), ("done\n", [2]))

    def test_a_502_in_a_graphql_call_is_retried(self):
        fake = FakeGh(fail("gh: HTTP 502"), OK)
        with unittest.mock.patch.object(common, "run", fake), \
                unittest.mock.patch.object(github, "sleep", lambda s: None), \
                unittest.mock.patch.object(github, "clock", lambda: 1000.0), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            out = github.call("api", "graphql", "-f", "query=query{viewer{login}}", cwd=self.dir.name)
        self.assertEqual((out, fake.calls), ("done\n", 2))

    def test_a_404_is_not_retried(self):
        fake, sleeps, _, err = self.run_call(fail("gh: Not Found (HTTP 404)"))
        self.assertEqual((fake.calls, sleeps, err.kind), (1, [], github.FAILED))

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

    def test_the_budget_is_per_call_so_a_second_hit_after_a_success_waits(self):
        # One process, as in a watch-ci loop: hit, wait, success, hit again. The second hit must wait, not exit 2.
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 60"
        fake, sleeps, now = FakeGh(fail(text), OK, OK, fail(text), OK), [], [1000.0]

        def sleep(seconds):  # time passes, so the reset the first hit recorded is over by the second
            sleeps.append(seconds)
            now[0] += seconds

        with unittest.mock.patch.object(common, "run", fake), \
                unittest.mock.patch.object(github, "sleep", sleep), \
                unittest.mock.patch.object(github, "clock", lambda: now[0]), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            outs = []
            for _ in range(3):
                outs.append(github.call("api", "x", cwd=self.dir.name))
                now[0] += 600  # a polling loop's interval
        self.assertEqual((outs, sleeps), (["done\n"] * 3, [60, 60]))

    def test_a_new_call_starts_with_a_fresh_budget_even_after_an_over_budget_one(self):
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 40"
        fake, sleeps, _, err = self.run_call(fail(text))  # waits 40, then the next 40 is over 75
        self.assertEqual((sleeps, err.kind), ([40], github.RATELIMITED))
        fake, sleeps, out, _ = self.run_call(fail(text), OK, clock=5000.0)  # later: the recorded reset is past
        self.assertEqual((out, sleeps), ("done\n", [40]))

    def test_the_over_budget_message_and_its_time_format_are_pinned(self):
        # skills/run/SKILL.md keys on `rate limit until <time>`
        self.assertEqual(github._utc(1300.0), "1970-01-01T00:21:40Z")
        text = "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300"
        _, _, _, err = self.run_call(fail(text))
        self.assertEqual(err.message, "rate limit until 1970-01-01T00:21:40Z")

    def test_quota_empty_with_a_reset_inside_the_budget_waits_not_raises(self):
        text = "gh: API rate limit exceeded (HTTP 403)\nx-ratelimit-remaining: 0\nx-ratelimit-reset: 1060"
        _, sleeps, out, err = self.run_call(fail(text), OK)
        self.assertEqual((out, err, sleeps), ("done\n", None, [60.0]))

    def test_quota_empty_with_a_far_reset_raises_without_waiting(self):
        text = "gh: API rate limit exceeded (HTTP 403)\nx-ratelimit-remaining: 0\nx-ratelimit-reset: 1121"
        _, sleeps, _, err = self.run_call(fail(text), OK)
        self.assertEqual((sleeps, err.kind), ([], github.RATELIMITED))
        self.assertIn("hourly quota used up", err.message)

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


def reply(status, body, **headers):
    """What `gh api --include` prints on stdout: a status line, headers, a blank line, then the body."""
    lines = [f"HTTP/2.0 {status} X"] + [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    return "\n".join(lines) + "\n\n" + body


class CallLogTest(unittest.TestCase):
    """Every gh attempt appends one `gh_call` event to the run's events log (and never changes the call)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name).resolve() / "repo"
        (self.root / ".git").mkdir(parents=True)
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "orchestrate-state.json").write_text(json.dumps({"session": "sess-9"}))
        self.log = self.root / ".claude" / "orchestrate-profiles" / "events.jsonl"
        env = unittest.mock.patch.dict("os.environ", {"XDG_CACHE_HOME": self.dir.name})
        env.start()
        self.addCleanup(env.stop)
        for k in ("GH_HOST", "ORCHESTRATE_GH_WAIT", "ORCHESTRATE_GH_LOG"):
            os.environ.pop(k, None)
        github._paths.clear()
        github._roots.clear()
        github.waited = 0.0
        self.addCleanup(github._paths.clear)
        self.addCleanup(github._roots.clear)

    def run_call(self, replies, *args):
        argvs, sleeps = [], []
        queue = list(replies)

        def fake(*cmd, **kw):
            argvs.append(cmd)
            return queue.pop(0) if len(queue) > 1 else queue[0]

        with unittest.mock.patch.object(common, "run", fake), \
                unittest.mock.patch.object(github, "sleep", sleeps.append), \
                unittest.mock.patch.object(github, "clock", lambda: 1000.0), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            try:
                return argvs, github.call(*args, cwd=str(self.root)), None
            except github.GhError as e:
                return argvs, None, e

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_a_canned_reply_writes_one_event_with_the_fields_and_returns_only_the_body(self):
        ok = common.Run(0, reply(200, '{"id": 1}', x_ratelimit_remaining=4321, x_ratelimit_used=679), "")
        argvs, out, err = self.run_call([ok], "api", "repos/{owner}/{repo}/pulls/7?per_page=1")
        self.assertEqual((out, err), ('{"id": 1}', None))
        self.assertEqual(argvs[0], ("gh", "api", "--include", "repos/{owner}/{repo}/pulls/7?per_page=1"))
        [e] = self.events()
        self.assertEqual((e["kind"], e["session"], e["outcome"], e["status"]), ("gh_call", "sess-9", "ok", 200))
        self.assertEqual((e["method"], e["write"], e["graphql"], e["path"]), ("GET", False, False, "repos/{owner}/{repo}/pulls/7"))
        self.assertEqual((e["remaining"], e["used"], e["retry_after"], e["attempt"]), (4321, 679, None, 1))
        self.assertIsInstance(e["ms"], int)
        self.assertTrue(e["script"])
        self.assertNotIn('"id"', self.log.read_text())  # no body

    def test_a_retried_call_writes_one_event_per_attempt_and_a_wait(self):
        limited = common.Run(1, reply(403, "{}", retry_after=2, x_ratelimit_remaining=0),
                             "gh: You have exceeded a secondary rate limit. (HTTP 403)")
        ok = common.Run(0, reply(200, "[]"), "")
        _, out, _ = self.run_call([limited, ok], "api", "x")
        self.assertEqual(out, "[]")
        first, wait, second = self.events()
        self.assertEqual((first["attempt"], first["outcome"], first["status"]), (1, "ratelimited", 403))
        self.assertEqual((wait["outcome"], wait["seconds"], wait["shared"]), ("wait", 2.0, False))
        self.assertEqual((second["attempt"], second["outcome"]), (2, "ok"))

    def test_a_403_with_retry_after_gives_status_and_retry_after_and_the_message_carries_them(self):
        limited = common.Run(1, reply(403, '{"message":"x"}', retry_after=7, x_ratelimit_remaining=12),
                             "gh: You have exceeded a secondary rate limit. (HTTP 403)")
        os.environ["ORCHESTRATE_GH_WAIT"] = "0"
        _, _, err = self.run_call([limited], "api", "--method", "POST", "x")
        events = self.events()
        self.assertEqual((events[0]["status"], events[0]["retry_after"], events[0]["remaining"]), (403, 7, 12))
        self.assertEqual((events[0]["method"], events[0]["write"]), ("POST", True))
        self.assertEqual(err.kind, github.RATELIMITED)
        self.assertIn("rate limit until", err.message)  # the wait is over the budget: the stable text

    def test_a_failed_api_call_reports_gh_stderr_not_the_headers(self):
        notfound = common.Run(1, reply(404, '{"message":"Not Found"}'), "gh: Not Found (HTTP 404)")
        _, _, err = self.run_call([notfound], "api", "x")
        self.assertEqual((err.kind, err.message), (github.FAILED, "gh: Not Found (HTTP 404)"))
        self.assertEqual((self.events()[0]["outcome"], self.events()[0]["status"]), ("failed", 404))

    def test_a_subcommand_failure_has_no_headers(self):
        fail_ = common.Run(1, "", "gh: You have exceeded a secondary rate limit. (HTTP 403)")
        os.environ["ORCHESTRATE_GH_WAIT"] = "0"
        argvs, _, err = self.run_call([fail_], "pr", "merge", "7", "--squash")
        self.assertEqual(argvs[0], ("gh", "pr", "merge", "7", "--squash"))  # no --include on a subcommand
        [e] = self.events()
        self.assertEqual((e["headers"], e["graphql"], e["write"], e["path"]), ("unavailable", True, True, "pr merge"))
        self.assertNotIn("remaining", e)

    def test_a_subcommand_rate_limit_message_says_headers_are_unavailable(self):
        fail_ = common.Run(1, "", "gh: You have exceeded a secondary rate limit. (HTTP 403)")
        os.environ["ORCHESTRATE_GH_WAIT"] = "1000"
        _, _, err = self.run_call([fail_], "pr", "view", "7")
        self.assertEqual(err.kind, github.RATELIMITED)
        self.assertIn("headers unavailable", err.message)

    def test_an_empty_body_after_the_headers_is_as_before(self):
        _, out, _ = self.run_call([common.Run(0, reply(204, ""), "")], "api", "-X", "DELETE", "x")
        self.assertEqual(out, "")
        self.assertEqual(self.events()[0]["method"], "DELETE")

    def test_output_without_headers_is_returned_untouched(self):
        _, out, _ = self.run_call([common.Run(0, "HTTP is a protocol\n\nbody", "")], "api", "x")
        self.assertEqual(out, "HTTP is a protocol\n\nbody")

    def test_paginated_calls_run_without_include(self):
        argvs, _, _ = self.run_call([common.Run(0, "a\n", "")], "api", "--paginate", "x", "--jq", ".[]")
        self.assertEqual(argvs[0], ("gh", "api", "--paginate", "x", "--jq", ".[]"))
        self.assertEqual(self.events()[0]["headers"], "unavailable")

    def test_project_listings_are_reads(self):
        self.assertFalse(github.describe(["project", "item-list", "1"])["write"])
        self.assertFalse(github.describe(["project", "field-list", "1"])["write"])
        self.assertTrue(github.describe(["project", "item-edit", "1"])["write"])

    def test_secondary_limit_with_quota_left_waits_a_minute_not_the_hourly_reset(self):
        text = common.Run(1, reply(403, "{}", x_ratelimit_remaining=4000, x_ratelimit_reset=1000 + 2400),
                          "gh: You have exceeded a secondary rate limit. (HTTP 403)")
        os.environ["ORCHESTRATE_GH_WAIT"] = "1000"
        sleeps = []
        with unittest.mock.patch.object(common, "run", FakeGh(text, common.Run(0, reply(200, "ok"), ""))), \
                unittest.mock.patch.object(github, "sleep", sleeps.append), \
                unittest.mock.patch.object(github, "clock", lambda: 1000.0), \
                unittest.mock.patch.object(sys, "stderr", unittest.mock.MagicMock()):
            github.call("api", "x", cwd=str(self.root))
        self.assertEqual(sleeps, [60])
        state = Path(self.dir.name) / "orchestrate" / "github-ratelimit-github.com.json"
        self.assertEqual(json.loads(state.read_text())["reset"], 1060.0)

    def test_graphql_reads_and_mutations(self):
        self.assertEqual(github.describe(["api", "graphql", "-f", "query=query { a }", "-F", "n=1"])["write"], False)
        mutation = github.describe(["api", "graphql", "-f", "query=mutation { resolve }"])
        self.assertEqual((mutation["graphql"], mutation["write"], mutation["path"]), (True, True, "graphql"))
        self.assertEqual(github.describe(["api", "-F", "issue_id=1", "x"])["method"], "POST")  # fields imply POST
        self.assertEqual(github.describe(["api", "x", "--jq", ".id"])["path"], "x")

    def test_a_log_write_failure_is_swallowed(self):
        (self.root / ".claude" / "orchestrate-profiles").write_text("not a directory")
        _, out, err = self.run_call([common.Run(0, reply(200, "ok"), "")], "api", "x")
        self.assertEqual((out, err), ("ok", None))

    def test_no_session_no_event(self):
        (self.root / ".claude" / "orchestrate-state.json").unlink()
        self.run_call([common.Run(0, reply(200, "ok"), "")], "api", "x")
        self.assertEqual(self.events(), [])

    def test_the_log_can_be_turned_off(self):
        os.environ["ORCHESTRATE_GH_LOG"] = "off"
        self.run_call([common.Run(0, reply(200, "ok"), "")], "api", "x")
        self.assertEqual(self.events(), [])

    def test_a_linked_worktree_logs_to_the_main_checkout(self):
        gitdir = self.root / ".git" / "worktrees" / "w"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        wt = self.root.parent / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {gitdir}\n")
        with unittest.mock.patch.object(common, "run", lambda *a, **k: common.Run(0, reply(200, "ok"), "")):
            github.call("api", "x", cwd=str(wt))
        self.assertEqual(len(self.events()), 1)


if __name__ == "__main__":
    unittest.main()
