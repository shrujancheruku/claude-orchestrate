"""scripts/merge's sweep after a merge: other open PRs that now conflict are updated.

Generated files are in test_merge_regenerate.py.
"""

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import git  # noqa: E402
from merge_support import CHANGELOG, SweepMixin  # noqa: E402


class SweepTest(SweepMixin, unittest.TestCase):
    """After a merge, every in-flight branch that now conflicts is updated; nobody force-pushes but a rebase."""

    def test_line_replay_keeps_both_sides_main_first(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "CHANGELOG.md"),
                         "# Changes\n## Unreleased\n- main item\n- pr item\n- old\n")
        self.assertEqual(self.replays(), [("CHANGELOG.md", "line-replay")])
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])  # a plain push

    def test_disjoint_word_merge(self):
        self.land({"CHANGELOG.md": CHANGELOG + "alpha beta gamma delta\n"})
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"CHANGELOG.md": CHANGELOG + "alpha beta gamma DELTA\n"})
        self.land({"CHANGELOG.md": CHANGELOG + "ALPHA beta gamma delta\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "CHANGELOG.md"), CHANGELOG + "ALPHA beta gamma DELTA\n")
        self.assertEqual(self.replays(), [("CHANGELOG.md", "word-merge")])

    def test_overlapping_word_merge_is_handed_back(self):
        self.land({"CHANGELOG.md": CHANGELOG + "alpha beta gamma\n"})
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"CHANGELOG.md": CHANGELOG + "alpha PR gamma\n"})
        self.land({"CHANGELOG.md": CHANGELOG + "alpha MAIN gamma\n"})
        before = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("HANDBACK PR #8 pr-a: CHANGELOG.md", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-2:], ["also: send-back 8", "next: merged"])
        git(self.env.repo, "fetch", "origin")
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a"), before)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [("CHANGELOG.md", "handed-back")])

    def test_conflict_in_a_non_shared_file_aborts_the_update(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n",
                                "src.txt": "one\nPR\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n", "src.txt": "one\nMAIN\n"})
        before = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("HANDBACK PR #8 pr-a: src.txt (not a shared file)", p.stdout)
        git(self.env.repo, "fetch", "origin")
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a"), before)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [])  # the abandoned replay isn't logged

    def test_a_draft_is_skipped_and_a_ready_pr_beside_it_is_still_swept(self):
        conflicting = {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n", "src.txt": "one\nPR\n"}
        self.pr_branch("pr-a", conflicting)
        self.pr_branch("pr-b", {"other.txt": "x\n", "src.txt": "one\nPR-B\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n", "src.txt": "one\nMAIN\n"})
        p = self.sweep(branches=("pr-a", "pr-b"), drafts=("pr-a",))
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertNotIn("pr-a", p.stdout)
        self.assertIn("HANDBACK PR #9 pr-b: src.txt (not a shared file)", p.stdout)

    def sweep_with(self, method):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        old = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep(method)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("- main item\n- pr item", self.remote("pr-a", "CHANGELOG.md"))
        return old

    def test_sweep_under_squash_merges_main_without_force(self):
        old = self.sweep_with("squash")
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])
        git(self.env.repo, "merge-base", "--is-ancestor", old, "origin/pr-a")  # history kept

    def test_sweep_under_merge_merges_main_without_force(self):
        self.sweep_with("merge")
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])

    def test_sweep_under_rebase_uses_force_with_lease(self):
        old = self.sweep_with("rebase")
        self.assertEqual(self.pushes(),
                         [f"push --force-with-lease=refs/heads/pr-a:{old} origin HEAD:refs/heads/pr-a"])
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a~1"), git(self.env.repo, "rev-parse", "origin/main"))

    def test_old_git_skips_the_sweep_with_one_line_and_leaves_prs_alone(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.pr_branch("pr-b", {"src.txt": "one\nPR\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n", "src.txt": "one\nMAIN\n"})
        with unittest.mock.patch.dict(os.environ, {"FAKE_GIT_VERSION": "2.37.9"}):
            p = self.sweep(branches=("pr-a", "pr-b"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("git >= 2.38"), 1, p.stdout)
        self.assertIn("this is git 2.37", p.stdout)
        self.assertNotIn("could not check", p.stdout)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [])
        self.assertFalse([l for l in self.git_log.read_text().splitlines() if l.startswith("merge-tree")])

    def test_git_2_38_still_sweeps(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        with unittest.mock.patch.dict(os.environ, {"FAKE_GIT_VERSION": "2.38.0"}):
            p = self.sweep()
        self.assertEqual(self.replays(), [("CHANGELOG.md", "line-replay")], p.stdout + p.stderr)

    def test_branch_that_is_only_behind_is_left_alone(self):
        self.pr_branch("pr-a", {"src.txt": "one\ntwo\nthree\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [])


if __name__ == "__main__":
    unittest.main()
