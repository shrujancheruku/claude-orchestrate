"""scripts/ci-rerun through the CLI, with the fake `gh`: the one owner of "make CI run again" (#545)."""

import unittest

from harness import PR_GRAPHQL, ScriptEnv, gh_repo_view, pr_graphql_reply

HEAD = "abcdef1234567890abcdef1234567890abcdef12"


class CiRerun(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def gh(self, checks=(), state="OPEN", extra=()):
        view = {"state": state, "headRefName": "7-x", "headRefOid": HEAD, "isCrossRepository": False,
                "statusCheckRollup": list(checks)}
        self.env.set_gh([gh_repo_view(), {"args": PR_GRAPHQL, "stdout": pr_graphql_reply(view)},
                         {"args": ["pr", "close"]}, {"args": ["pr", "reopen"]}, {"args": ["run", "rerun"]}, *extra])

    def writes(self):
        return [c for c in self.env.gh_calls() if c[:2] in (["pr", "close"], ["pr", "reopen"], ["run", "rerun"])]

    def rerun_events(self):
        return [e for e in self.env.events() if e["kind"] == "ci" and e["outcome"] == "rerun"]

    def test_a_head_with_no_check_is_closed_and_reopened_once_and_logged(self):
        self.gh()
        r = self.env.run("ci-rerun", 7)
        self.assertEqual(r.stdout.splitlines()[-1], f"next: rerun 7 {HEAD}")
        self.assertEqual(self.writes(), [["pr", "close", "7"], ["pr", "reopen", "7"]])
        (event,) = self.rerun_events()
        self.assertEqual((event["ref"], event["head"]), (7, HEAD))

    def test_a_second_call_for_the_same_head_is_refused_and_goes_to_the_user(self):
        self.gh()
        self.env.run("ci-rerun", 7)
        r = self.env.run("ci-rerun", 7, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.splitlines()[-1].startswith("next: ask-user "), r.stdout)
        self.assertEqual(len(self.writes()), 2)  # nothing new was sent

    def test_a_head_that_already_has_a_check_is_refused_without_a_write(self):
        self.gh(checks=[{"name": "test", "status": "IN_PROGRESS", "conclusion": "", "detailsUrl": "u"}])
        r = self.env.run("ci-rerun", 7, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[-1], "next: already-running 7")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.rerun_events(), [])

    def test_a_pr_that_is_not_open_could_not_run(self):
        self.gh(state="MERGED")
        r = self.env.run("ci-rerun", 7, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.splitlines()[-1], "next: could-not-run")
        self.assertEqual(self.writes(), [])

    def test_a_failed_reopen_says_how_to_finish_it(self):
        self.gh(extra=())
        self.env.set_gh([gh_repo_view(), {"args": PR_GRAPHQL, "stdout": pr_graphql_reply(
            {"state": "OPEN", "headRefName": "7-x", "headRefOid": HEAD, "statusCheckRollup": []})},
            {"args": ["pr", "close"]}, {"args": ["pr", "reopen"], "stderr": "boom\n", "exit": 1}])
        r = self.env.run("ci-rerun", 7, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("gh pr reopen 7", r.stderr)
        self.assertEqual(self.rerun_events(), [])

    def test_a_stuck_job_is_rerun_once(self):
        self.gh(checks=[{"name": "test", "status": "IN_PROGRESS", "conclusion": "", "detailsUrl": "u"}])
        r = self.env.run("ci-rerun", 7, "--job", 111, 222)
        self.assertEqual(r.stdout.splitlines()[-1], f"next: rerun 7 {HEAD}")
        self.assertEqual(self.writes(), [["run", "rerun", "111", "--job", "222"]])
        (event,) = self.rerun_events()
        self.assertEqual((event["run"], event["job"]), ("111", "222"))
        again = self.env.run("ci-rerun", 7, "--job", 111, 222, check=False)
        self.assertEqual(again.returncode, 1)
        self.assertIn("next: ask-user", again.stdout)
        self.assertEqual(len(self.writes()), 1)
        other = self.env.run("ci-rerun", 7, "--job", 111, 333)  # another job of the run is its own once
        self.assertEqual(other.returncode, 0)

    def test_job_ids_must_be_numbers(self):
        self.gh()
        r = self.env.run("ci-rerun", 7, "--job", "x", "2", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.writes(), [])

    def test_flags_are_not_abbreviated(self):
        self.gh()
        r = self.env.run("ci-rerun", 7, "--jo", 1, 2, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
