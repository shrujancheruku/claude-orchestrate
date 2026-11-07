"""scripts/merge --approved-head: behind leads to exit 5, a change since the approved head to exit 6."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402


READY_CONFIG = ("## Review\n\n- **Risk paths** (any PR touching them is reviewed as L): `auth/*`\n"
                "- **Tiers:** S is at most <10> changed lines in at most <2> files, none on a risk path; "
                "L touches a risk path or changes more than <400> lines; M is everything else.\n")


class ReadinessTest(unittest.TestCase):
    """--approved-head: behind leads to exit 5, a change since the approved head needing re-review to exit 6."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.repo = self.env.repo
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "orchestration.md").write_text(READY_CONFIG)
        git(self.repo, "checkout", "-q", "-b", "feat-br")
        self.approved = self.env.commit("a.txt", "one\n", "feat: a")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")

    def push(self, files, message="feat: more"):
        git(self.repo, "checkout", "-q", "feat-br")
        for path, text in files.items():
            sha = self.env.commit(path, text, message)
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        return sha

    def merge(self, head, behind="0", update=None, approved="", flag=True):
        pr_json = {"state": "OPEN", "headRefName": "feat-br", "headRefOid": head,
                   "isCrossRepository": False, "closingIssuesReferences": []}
        view = {"stdout": json.dumps(pr_json)}
        merged = {"stdout": json.dumps(dict(pr_json, state="MERGED"))}
        self.env.set_gh([
            {"args": ["pr", "view"], "seq": [view, merged]},
            {"args": ["repo", "view"], "stdout": '{"defaultBranchRef":{"name":"main"}}'},
            {"args": ["api", "repos/{owner}/{repo}/compare/main...feat-br", "--jq", ".behind_by"],
             "stdout": behind + "\n"},
            {"args": ["pr", "update-branch"], **(update or {"exit": 0})},
            {"args": ["pr", "merge"], "exit": 0},
            {"args": ["pr", "list"], "stdout": "[]"},
            {"args": ["api", "-X"], "exit": 0},
        ])
        extra = ["--approved-head", approved or self.approved] if flag else []
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def test_behind_updates_the_branch_and_exits_5(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "update-branch"]), [["pr", "update-branch", "7"]])
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_failing_update_exits_1_with_the_output(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="1", update={"exit": 1, "stderr": "merge conflict between base and head"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("merge conflict between base and head", p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_risk_path_change_since_the_approved_head_exits_6(self):
        head = self.push({"auth/login.py": "x\n"})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/login.py", p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_too_many_lines_exits_6(self):
        head = self.push({"c.txt": "x\n" * 11})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_too_many_files_exits_6(self):
        head = self.push({"e.txt": "1\n", "f.txt": "2\n", "g.txt": "3\n"})
        self.assertEqual(self.merge(head).returncode, 6)

    def test_several_small_pushes_add_up(self):
        self.push({"c.txt": "x\n" * 6}, "feat: c")
        head = self.push({"d.txt": "x\n" * 6}, "feat: d")
        self.assertEqual(self.merge(head).returncode, 6)

    def test_only_default_branch_merges_since_the_approved_head_merge(self):
        # main moves on (a risk path, many lines), and the host merges it into the branch.
        self.env.commit("auth/core.py", "x\n" * 50, "feat: main moves")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "feat-br")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge branch main", "main")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        p = self.merge(head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 1)

    def test_small_change_within_thresholds_merges(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head).returncode, 0)

    def test_no_flag_keeps_todays_behaviour(self):
        head = self.push({"auth/login.py": "x\n" * 50})
        p = self.merge(head, behind="3", flag=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "update-branch"]), [])
        self.assertEqual([c for c in self.env.gh_calls() if "compare" in " ".join(c)], [])

    def test_unknown_approved_head_needs_re_review(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, approved="f" * 40).returncode, 6)


if __name__ == "__main__":
    unittest.main()
