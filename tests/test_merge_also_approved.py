"""scripts/merge --also-approved: after a merge, other approved PRs that are behind are updated at once."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import PR_GRAPHQL, ScriptEnv, git, gh_pr_view, gh_pulls, gh_repo_view, pr_graphql_reply  # noqa: E402

GREEN = [{"name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"}]


class AlsoApprovedTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def other(self, n, head, state="OPEN", merge_state="CLEAN", fork=False):
        return gh_pr_view(n, {"state": state, "headRefName": f"br-{n}", "headRefOid": head,
                              "isCrossRepository": fork, "mergeStateStatus": merge_state})

    def behind(self, n, k):
        return {"args": ["api", f"repos/{{owner}}/{{repo}}/compare/main...br-{n}", "--jq", ".behind_by"],
                "stdout": f"{k}\n"}

    def merge(self, others, *specs, update=None):
        pr = {"state": "OPEN", "headRefName": "br-7", "headRefOid": "a" * 40, "isCrossRepository": False,
              "closingIssuesReferences": [], "statusCheckRollup": GREEN}
        self.env.set_gh([
            {"args": PR_GRAPHQL, "seq": [{"stdout": pr_graphql_reply(pr)}, {"stdout": pr_graphql_reply(dict(pr, state="MERGED"))}]},
            gh_repo_view(),
            {"args": ["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/merge"], "exit": 0},
            {"args": ["api", "~repos/{owner}/{repo}/pulls?state=open"], "stdout": "[]"},
            {"args": ["api", "-X", "DELETE"], "exit": 0},
            {"args": ["api", "-X", "PUT", "~repos/{owner}/{repo}/pulls/"], **(update or {"exit": 0})},
            *others,
        ])
        args = [x for s in specs for x in ("--also-approved", s)]
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *args, check=False)

    def updated(self):
        """The PR numbers update-branch was called for."""
        return [c[3].split("/")[4] for c in self.env.gh_calls() if c[:3] == ["api", "-X", "PUT"] and c[3].endswith("/update-branch")]

    def test_every_approved_behind_pr_is_updated_with_one_line_each(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 3), self.other(9, "c" * 40), self.behind(9, 1)],
                       "8=" + "b" * 40, "9=" + "c" * 40)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.updated(), ["8", "9"])
        self.assertIn("PR #8 updated (behind by 3); wait for CI", p.stdout)
        self.assertIn("PR #9 updated (behind by 1); wait for CI", p.stdout)
        lines = p.stdout.splitlines()
        self.assertRegex(lines[-3], r"^also: wait-ci 8 \S+$")
        self.assertRegex(lines[-2], r"^also: wait-ci 9 \S+$")
        self.assertEqual(lines[-1], "next: frontier")

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
        self.assertEqual(p.stdout.splitlines()[-2:], ["also: send-back 8", "next: frontier"])

    def test_a_pr_pushed_to_since_its_approval_is_left_alone(self):
        p = self.merge([self.other(8, "d" * 40), self.behind(8, 2)], "8=" + "b" * 40)
        self.assertEqual(self.updated(), [])
        self.assertIn("PR #8 left alone", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-2:], ["also: left 8 pushed to since the approval", "next: frontier"])

    def test_a_failed_update_does_not_fail_the_merge(self):
        p = self.merge([self.other(8, "b" * 40), self.behind(8, 2)], "8=" + "b" * 40,
                       update={"exit": 1, "stderr": "boom"})
        self.assertEqual(p.returncode, 0)
        self.assertIn("PR #8 not updated", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-2:], ["also: left 8 not updated: boom", "next: frontier"])

    def test_a_gated_pr_is_left_alone_even_when_behind(self):
        cfg = self.env.repo / ".claude" / "orchestration.md"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text("## Gates\n\n- **Merge gates:** PRs touching `hooks/**` are merged by the developer.\n")

        def files(n, *names):
            return {"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls/{n}/files"],
                    "stdout": json.dumps([{"filename": f} for f in names])}
        p = self.merge([files(7, "a.txt"), files(8, "hooks/guard.py"), files(9, "b.txt"),
                        self.other(8, "b" * 40), self.behind(8, 3), self.other(9, "c" * 40), self.behind(9, 1)],
                       "8=" + "b" * 40, "9=" + "c" * 40)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.updated(), ["9"])
        self.assertIn("PR #8 left alone: it touches the merge gate hooks/**", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-3:], ["also: left 8 gated by hooks/**", "also: wait-ci 9 " + p.stdout.split("also: wait-ci 9 ")[1].split()[0], "next: frontier"])

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
        # BLOCKED: the host wants the update, so the no-overlap skip does not apply and only the spec match is under test.
        p = self.merge([self.other(8, head, merge_state="BLOCKED"), self.behind(8, 1)], "8=" + approved)
        self.assertEqual(self.updated(), ["8"], p.stdout)

    def test_a_behind_pr_with_no_file_overlap_is_not_updated(self):
        approved = self.branch()
        self.env.commit("other.txt", "x\n", "feat: main moves")
        git(self.env.repo, "push", "-q", "origin", "main")
        p = self.merge([self.other(8, approved), self.behind(8, 1)], "8=" + approved)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.updated(), [])
        self.assertIn("PR #8 is behind main by 1, no file overlap; not updated", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_a_behind_pr_sharing_a_file_with_main_is_still_updated(self):
        approved = self.branch()
        self.env.commit("pr.txt", "main's\n", "feat: main edits the file")
        git(self.env.repo, "push", "-q", "origin", "main")
        p = self.merge([self.other(8, approved), self.behind(8, 1)], "8=" + approved)
        self.assertEqual(self.updated(), ["8"], p.stdout)

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
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")


if __name__ == "__main__":
    unittest.main()
