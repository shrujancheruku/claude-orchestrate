"""scripts/merge --also-approved: after a merge, other approved PRs that are behind are updated at once."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402

GREEN = [{"name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"}]


class AlsoApprovedTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def other(self, n, head, state="OPEN", merge_state="CLEAN", fork=False):
        view = {"state": state, "headRefName": f"br-{n}", "headRefOid": head, "isCrossRepository": fork,
                "mergeStateStatus": merge_state}
        return {"args": ["pr", "view", str(n)], "stdout": json.dumps(view)}

    def behind(self, n, k):
        return {"args": ["api", f"repos/{{owner}}/{{repo}}/compare/main...br-{n}", "--jq", ".behind_by"],
                "stdout": f"{k}\n"}

    def merge(self, others, *specs, update=None):
        pr = {"state": "OPEN", "headRefName": "br-7", "headRefOid": "a" * 40, "isCrossRepository": False,
              "closingIssuesReferences": [], "statusCheckRollup": GREEN}
        self.env.set_gh([
            {"args": ["pr", "view", "7"], "seq": [{"stdout": json.dumps(pr)}, {"stdout": json.dumps(dict(pr, state="MERGED"))}]},
            {"args": ["repo", "view"], "stdout": '{"defaultBranchRef":{"name":"main"}}'},
            {"args": ["pr", "merge"], "exit": 0},
            {"args": ["pr", "list"], "stdout": "[]"},
            {"args": ["api", "-X"], "exit": 0},
            {"args": ["pr", "update-branch"], **(update or {"exit": 0})},
            *others,
        ])
        args = [x for s in specs for x in ("--also-approved", s)]
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *args, check=False)

    def updated(self):
        return [c for c in self.env.gh_calls() if c[:2] == ["pr", "update-branch"]]

    def test_every_approved_behind_pr_is_updated_with_one_line_each(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 3), self.other(9, "c" * 40), self.behind(9, 1)],
                       "8=" + "b" * 40, "9=" + "c" * 40)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.updated(), [["pr", "update-branch", "8"], ["pr", "update-branch", "9"]])
        self.assertIn("PR #8 updated (behind by 3); wait for CI", p.stdout)
        self.assertIn("PR #9 updated (behind by 1); wait for CI", p.stdout)

    def test_a_pr_not_named_approved_is_left_alone(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 3)])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.updated(), [])

    def test_an_approved_pr_not_behind_is_not_updated(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 0)], "8=" + "b" * 40)
        self.assertEqual(self.updated(), [])
        self.assertNotIn("PR #8 updated", p.stdout)

    def test_a_dirty_pr_is_handed_back_not_updated(self):
        p = self.merge([self.other(8, "b" * 40, merge_state="DIRTY"), self.behind(8, 2)], "8=" + "b" * 40)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.updated(), [])
        self.assertIn("PR #8 is behind main by 2 and DIRTY; not updated: hand it back", p.stdout)

    def test_a_pr_pushed_to_since_its_approval_is_left_alone(self):
        p = self.merge([self.other(8, "d" * 40), self.behind(8, 2)], "8=" + "b" * 40)
        self.assertEqual(self.updated(), [])
        self.assertIn("PR #8 left alone", p.stdout)

    def test_a_failed_update_does_not_fail_the_merge(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 2)], "8=" + "b" * 40,
                       update={"exit": 1, "stderr": "boom"})
        self.assertEqual(p.returncode, 0)
        self.assertIn("PR #8 not updated", p.stdout)

    def test_a_malformed_spec_is_skipped(self):
        p = self.merge([], "nonsense")
        self.assertEqual(p.returncode, 0)
        self.assertIn("skipped: expected PR=SHA", p.stdout)

    def branch(self, name="br-8"):
        """An approved head on `name`, pushed; returns its sha. The repo is left on main."""
        repo = self.env.repo
        git(repo, "checkout", "-q", "-b", name)
        sha = self.env.commit("pr.txt", "one\n", "feat: pr")
        git(repo, "push", "-q", "origin", name)
        git(repo, "checkout", "-q", "main")
        return sha

    def test_a_pr_updated_from_main_still_matches_its_spec(self):
        approved = self.branch()
        repo = self.env.repo
        self.env.commit("other.txt", "x\n" * 50, "feat: main moves")
        git(repo, "push", "-q", "origin", "main")
        git(repo, "checkout", "-q", "br-8")
        git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch main", "main")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "push", "-q", "origin", "br-8")
        git(repo, "checkout", "-q", "main")
        p = self.merge([self.other(8, head), self.behind(8, 1)], "8=" + approved)
        self.assertEqual(self.updated(), [["pr", "update-branch", "8"]], p.stdout)

    def test_a_pr_with_its_own_push_after_the_approval_is_left_alone(self):
        approved = self.branch()
        repo = self.env.repo
        git(repo, "checkout", "-q", "br-8")
        head = self.env.commit("pr2.txt", "two\n", "fix: more")
        git(repo, "push", "-q", "origin", "br-8")
        git(repo, "checkout", "-q", "main")
        p = self.merge([self.other(8, head), self.behind(8, 1)], "8=" + approved)
        self.assertEqual(self.updated(), [])
        self.assertIn("PR #8 left alone", p.stdout)


if __name__ == "__main__":
    unittest.main()
