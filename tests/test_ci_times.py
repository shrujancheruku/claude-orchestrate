"""scripts/ci-times through its CLI, with a fake `gh` serving merged PRs, their commits and check runs."""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(name, queued, running, push=0, conclusion="SUCCESS"):
    """A check run that started `queued` minutes after the push and ran for `running` minutes."""
    return {"name": name, "status": "completed", "conclusion": conclusion.lower(),
            "started_at": iso(push + queued), "completed_at": iso(push + queued + running)}


class CiTimes(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def serve(self, prs):
        """prs: {number: (merged at minute, [(sha, push minute, [check runs])])}."""
        listing = [{"number": n, "mergedAt": iso(m)} for n, (m, _) in prs.items()]
        responses = [{"args": ["api", "~repos/{owner}/{repo}/pulls?state=closed"], "stdout": json.dumps(
            [{"number": r["number"], "merged_at": r["mergedAt"], "state": "closed"} for r in listing])}]
        for n, (m, heads) in prs.items():
            commits = [{"sha": sha, "commit": {"committer": {"date": iso(push)}}} for sha, push, _ in heads]
            responses.append({"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls/{n}/commits"],
                              "stdout": json.dumps(commits)})
            responses.append({"args": ["api", f"repos/{{owner}}/{{repo}}/pulls/{n}"],
                              "stdout": json.dumps({"merged_at": iso(m), "state": "closed"})})
            for sha, _, runs in heads:
                responses.append({"args": ["api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100"],
                                  "stdout": json.dumps({"check_runs": runs})})
        self.env.set_gh(responses)

    def config(self, line):
        path = self.env.repo / ".claude" / "orchestration.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"## Review\n\n{line}\n")

    def times(self, *args, check=True):
        r = self.env.run("ci-times", *args, check=check)
        return r.stdout.splitlines()

    def test_median_p90_and_the_over_line(self):
        # Ten green heads at 1..10 minutes: median 5.5, nearest-rank p90 9.
        prs = {n: (100 + n, [(f"s{n}", 100 + n, [check("test", 0, n, push=100 + n)])]) for n in range(1, 11)}
        self.serve(prs)
        out = self.times()
        self.assertEqual(out[0], "heads: 10 green over 10 PRs (0 never green)")
        self.assertIn("median: 5m30s", out)
        self.assertIn("p90: 9m00s", out)
        self.assertEqual(out[-1], "over: no")
        self.assertNotIn("cause", "\n".join(out))

    def test_the_gap_from_ci_done_to_merged_is_flagged_over_two_minutes(self):
        # Checks finish at minute 101; the merge lands at 110 (9 minutes later) or 102 (1 minute later).
        for merged, flag, gap in ((110, "over", "9m00s"), (102, "ok", "1m00s")):
            with self.subTest(merged=merged):
                self.serve({1: (merged, [("s1", 100, [check("test", 0, 1, push=100)])])})
                out = self.times()
                self.assertIn(f"ci done -> merged: median {gap} over 1 PRs", out)
                self.assertIn(f"wait: {flag}", out)
                self.assertEqual(out[-1], "over: no")  # the push-to-green verdict is its own

    def test_abbreviated_flags_are_rejected(self):
        self.assertNotEqual(self.env.run("ci-times", "--sin", "1", check=False).returncode, 0)

    def test_over_with_a_slow_job_and_with_too_few_runners(self):
        self.serve({1: (50, [("a", 0, [check("build", 1, 2), check("test", 1, 12)])])})
        out = self.times()
        self.assertIn("  test: 1m00s / 12m00s", out)
        self.assertIn("cause: slow job (test)", out)
        self.assertEqual(out[-1], "over: yes")
        self.serve({1: (50, [("a", 0, [check("build", 1, 2), check("test", 11, 2)])])})
        out = self.times()
        self.assertIn("cause: runners (test)", out)
        self.assertEqual(out[-1], "over: yes")

    def test_slowest_checks_are_ordered_and_capped(self):
        runs = [check("a", 0, 1), check("b", 0, 5), check("c", 0, 3), check("d", 0, 2)]
        self.serve({1: (50, [("a", 0, runs)])})
        out = self.times()
        start = out.index("slowest checks (queued / running):")
        self.assertEqual(out[start + 1:start + 4], ["  b: 0m00s / 5m00s", "  c: 0m00s / 3m00s", "  d: 0m00s / 2m00s"])
        self.assertTrue(out[start + 4].startswith("limit:"))

    def test_never_green_heads_are_left_out_and_the_fix_push_counts_alone(self):
        self.serve({1: (80, [
            ("red", 0, [check("test", 0, 30, conclusion="FAILURE")]),  # never green: not counted
            ("fix", 40, [check("test", 1, 4, push=40)]),  # the fix push is its own head: 5 minutes
            ("bare", 41, []),  # nothing ran on it
        ])})
        out = self.times()
        self.assertEqual(out[0], "heads: 1 green over 1 PRs (1 never green)")
        self.assertIn("median: 5m00s", out)
        self.assertEqual(out[-1], "over: no")

    def test_a_head_with_a_pending_check_never_went_green(self):
        pending = {"name": "test", "status": "in_progress", "conclusion": None, "started_at": iso(1)}
        self.serve({1: (80, [("a", 0, [check("build", 0, 2), pending])])})
        self.assertEqual(self.times()[0], "heads: 0 green over 1 PRs (1 never green)")

    def test_no_green_head_is_not_over(self):
        self.serve({})
        self.assertEqual(self.times(), ["heads: 0 green over 0 PRs (0 never green)", "limit: 10m (default)", "over: no"])

    def test_default_window_is_the_last_ten_merged_prs(self):
        prs = {n: (n, [(f"s{n}", n, [check("test", 0, 1, push=n)])]) for n in range(1, 13)}
        self.serve(prs)
        self.assertEqual(self.times()[0], "heads: 10 green over 10 PRs (0 never green)")
        viewed = {c[1].split("/")[4] for c in self.env.gh_calls() if c[0] == "api" and c[1].endswith("/commits?per_page=100&page=1")}
        self.assertEqual(viewed, {str(n) for n in range(3, 13)})

    def test_call_count_is_one_listing_plus_two_per_pr(self):
        prs = {n: (n, [(f"s{n}", n, [check("test", 0, 1, push=n)])]) for n in range(1, 11)}
        self.serve(prs)
        self.times()
        self.assertEqual(len(self.env.gh_calls()), 1 + 2 * 10)

    def test_since_a_pr_in_the_listing_costs_no_lookup_of_that_pr(self):
        prs = {n: (n * 10, [(f"s{n}", n * 10, [check("test", 0, 1, push=n * 10)])]) for n in range(1, 6)}
        self.serve(prs)
        self.times("--since", "3")
        calls = [c[1] for c in self.env.gh_calls()]
        self.assertFalse([c for c in calls if c.endswith("/pulls/3")], calls)
        self.assertEqual(len(calls), 1 + 2 * 2)

    def test_a_head_shared_by_two_prs_is_fetched_once_and_counted_per_pr(self):
        shared = [("same", 5, [check("test", 0, 2, push=5)])]
        self.serve({1: (5, shared), 2: (6, shared)})
        out = self.times()
        self.assertEqual(out[0], "heads: 2 green over 2 PRs (0 never green)")
        fetches = [c for c in self.env.gh_calls() if "/commits/same/check-runs" in c[1]]
        self.assertEqual(len(fetches), 1)

    def test_since_a_time_takes_every_pr_merged_from_then(self):
        prs = {n: (n * 10, [(f"s{n}", n * 10, [check("test", 0, 1, push=n * 10)])]) for n in range(1, 13)}
        self.serve(prs)
        out = self.times("--since", iso(30))  # PRs 3..12 merged at or after minute 30
        self.assertEqual(out[0], "heads: 10 green over 10 PRs (0 never green)")
        out = self.times("--since", iso(100))  # a window of three, not capped at ten
        self.assertEqual(out[0], "heads: 3 green over 3 PRs (0 never green)")

    def test_since_a_pr_takes_those_merged_after_it(self):
        prs = {n: (n * 10, [(f"s{n}", n * 10, [check("test", 0, 1, push=n * 10)])]) for n in range(1, 6)}
        self.serve(prs)
        for arg in ("3", "#3"):
            self.assertEqual(self.times("--since", arg)[0], "heads: 2 green over 2 PRs (0 never green)")

    def test_default_limit_versus_the_config_limit(self):
        # One head at 12 minutes: over the default 10, under a configured 15, over a configured 5 and 11.
        self.serve({1: (50, [("a", 0, [check("test", 0, 12)])])})
        out = self.times()
        self.assertIn("limit: 10m (default)", out)
        self.assertEqual(out[-1], "over: yes")
        self.config("- **CI wait limit:** 15")
        out = self.times()
        self.assertIn("limit: 15m (config)", out)
        self.assertEqual(out[-1], "over: no")
        self.config("- **CI wait limit:** 11")
        self.assertEqual(self.times()[-1], "over: yes")

    def test_a_bad_since_and_a_failing_gh_could_not_run(self):
        self.serve({})
        r = self.env.run("ci-times", "--since", "yesterday-ish", check=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.env.set_gh([{"args": ["api", "~repos/{owner}/{repo}/pulls"], "stderr": "HTTP 502\n", "exit": 1}])
        r = self.env.run("ci-times", check=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = self.env.run("ci-times", no_gh=True, check=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
