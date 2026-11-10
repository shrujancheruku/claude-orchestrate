"""scripts/scratch: the one way a scratch worktree is added (#545)."""

import tempfile
import unittest
from pathlib import Path

from harness import ScriptEnv, git


class Scratch(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.pad = tempfile.TemporaryDirectory()
        self.addCleanup(self.pad.cleanup)
        self.under = Path(self.pad.name).resolve()
        self.head = git(self.env.repo, "rev-parse", "HEAD")

    def scratch(self, *args, check=False):
        return self.env.run("scratch", *args, check=check)

    def test_adds_a_detached_worktree_named_for_the_pr_and_prints_its_path(self):
        r = self.scratch(12, self.head, "--under", self.under)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = Path(r.stdout.strip())
        self.assertEqual(path, self.under / "pr-12")
        self.assertEqual(git(path, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(path, "branch", "--show-current"), "")  # detached

    def test_a_taken_name_gets_a_letter(self):
        first = Path(self.scratch(12, self.head, "--under", self.under).stdout.strip())
        second = Path(self.scratch(12, self.head[:10], "--under", self.under).stdout.strip())
        self.assertEqual((first.name, second.name), ("pr-12", "pr-12b"))

    def test_refuses_a_directory_inside_the_repo_or_one_of_its_worktrees(self):
        r = self.scratch(12, self.head, "--under", self.env.repo / "scratch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("inside a checkout", r.stderr)
        self.assertFalse((self.env.repo / "scratch").exists())
        wt = self.under / "wt"
        git(self.env.repo, "worktree", "add", "-b", "other", str(wt))
        r = self.scratch(12, self.head, "--under", wt / "sub")
        self.assertEqual(r.returncode, 1)

    def test_refuses_a_relative_directory_and_a_head_that_is_not_a_known_hash(self):
        self.assertEqual(self.scratch(12, self.head, "--under", "rel").returncode, 1)
        self.assertEqual(self.scratch(12, "main", "--under", self.under).returncode, 1)
        self.assertEqual(self.scratch(12, "0" * 40, "--under", self.under).returncode, 1)
        self.assertEqual(list(self.under.iterdir()), [])

    def test_the_under_flag_is_required_and_not_abbreviated(self):
        self.assertEqual(self.scratch(12, self.head).returncode, 2)
        self.assertEqual(self.scratch(12, self.head, "--und", self.under).returncode, 2)


if __name__ == "__main__":
    unittest.main()
