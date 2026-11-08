"""Unit tests for scripts/repo.py: the branch convention, timestamps and the main checkout."""

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import repo  # noqa: E402

UTC = timezone.utc


def _vcs(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TicketOfBranch(unittest.TestCase):
    def test_number_slug(self):
        self.assertEqual(repo.ticket_of_branch("258-one-repo-module"), 258)

    def test_not_the_convention(self):
        for name in ("main", "feature-12", "", None, "-5-x", "12"):
            with self.subTest(name):
                self.assertIsNone(repo.ticket_of_branch(name))

    def test_first_line(self):
        self.assertEqual(repo.first_line("\n  head  \nrest"), "head")
        self.assertEqual(repo.first_line(None), "")


class ParseTs(unittest.TestCase):
    def test_z_suffix(self):
        self.assertEqual(repo.parse_ts("2026-11-07T10:00:00Z"), datetime(2026, 11, 7, 10, tzinfo=UTC))

    def test_offset(self):
        self.assertEqual(repo.parse_ts("2026-11-07T12:00:00+02:00"), datetime(2026, 11, 7, 10, tzinfo=UTC))

    def test_naive_counts_as_utc(self):
        self.assertEqual(repo.parse_ts("2026-11-07T10:00:00"), datetime(2026, 11, 7, 10, tzinfo=UTC))

    def test_epoch_seconds_and_milliseconds(self):
        want = datetime.fromtimestamp(1_700_000_000, UTC)
        self.assertEqual(repo.parse_ts(1_700_000_000), want)
        self.assertEqual(repo.parse_ts(1_700_000_000_000), want)

    def test_garbage_is_none(self):
        for v in ("garbage", "", None, "2026-13-45"):
            with self.subTest(v):
                self.assertIsNone(repo.parse_ts(v))

    def test_iso_round_trip(self):
        self.assertEqual(repo.iso(repo.parse_ts("2026-11-07T12:00:00+02:00")), "2026-11-07T10:00:00Z")
        self.assertIsNone(repo.iso(None))


class MainCheckout(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "repo"
        self.root.mkdir()
        _vcs(self.root, "init", "-q", "-b", "main")
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(self.root, "config", k, v)
        _vcs(self.root, "commit", "-q", "--allow-empty", "-m", "chore: init")

    def test_the_main_checkout_and_a_subdirectory(self):
        sub = self.root / "a" / "b"
        sub.mkdir(parents=True)
        self.assertEqual(repo.main_checkout(self.root), self.root)
        self.assertEqual(repo.main_checkout(sub), self.root)

    def test_a_linked_worktree_resolves_to_the_main_checkout(self):
        wt = self.root / ".claude" / "worktrees" / "w1"
        _vcs(self.root, "worktree", "add", "-q", "-b", "7-w", str(wt))
        self.assertEqual(repo.main_checkout(wt), self.root)
        self.assertEqual(repo.ticket_branch(self.root, 7), "7-w")
        self.assertIsNone(repo.ticket_branch(self.root, 8))


class WorktreeHelpers(unittest.TestCase):
    """worktree_changes and remove_worktree, on a temp repo with a linked worktree."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "repo"
        self.root.mkdir()
        _vcs(self.root, "init", "-q", "-b", "main")
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(self.root, "config", k, v)
        (self.root / "a.txt").write_text("a\n")
        _vcs(self.root, "add", "a.txt")
        _vcs(self.root, "commit", "-q", "-m", "init")
        self.wt = Path(tmp.name).resolve() / "wt"
        _vcs(self.root, "worktree", "add", "-q", "-b", "7-x", str(self.wt))

    def test_changes_cover_committed_staged_unstaged_and_untracked(self):
        (self.wt / "committed.txt").write_text("c\n")
        _vcs(self.wt, "add", "committed.txt")
        _vcs(self.wt, "commit", "-q", "-m", "c")
        (self.wt / "a.txt").write_text("changed\n")
        (self.wt / "staged.txt").write_text("s\n")
        _vcs(self.wt, "add", "staged.txt")
        (self.wt / "untracked.txt").write_text("u\n")
        self.assertEqual(repo.worktree_changes(self.wt, "main"),
                         ["a.txt", "committed.txt", "staged.txt", "untracked.txt"])

    def test_changes_of_a_clean_worktree_are_empty(self):
        self.assertEqual(repo.worktree_changes(self.wt, "main"), [])

    def test_changes_raise_when_git_cannot_say(self):
        with self.assertRaises(ValueError):
            repo.worktree_changes(self.wt, "no-such-branch")

    def test_remove_clean_worktree(self):
        self.assertIn("removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertFalse(self.wt.exists())
        self.assertIn("already removed", repo.remove_worktree(str(self.wt), self.root))

    def test_remove_dirty_worktree_is_refused_not_forced(self):
        (self.wt / "u.txt").write_text("u\n")
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_an_untracked_pr_body_alone_goes_with_the_worktree(self):
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("body\n")
        self.assertIn("removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertFalse(self.wt.exists())

    def test_an_untracked_pr_body_beside_another_change_still_blocks_removal(self):
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("body\n")
        (self.wt / "u.txt").write_text("u\n")
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_another_untracked_file_under_claude_still_blocks_removal(self):
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "notes.md").write_text("n\n")
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_harness_lock_is_lifted_for_a_remove_and_restored_when_refused(self):
        reason = "claude agent agent-abc (pid 1 start Sat Nov  7 06:14:20 2026)"
        _vcs(self.root, "worktree", "lock", "--reason", reason, str(self.wt))
        (self.wt / "u.txt").write_text("u\n")
        self.assertIn("harness lock kept", repo.remove_worktree(str(self.wt), self.root))
        listing = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=self.root,
                                 capture_output=True, text=True).stdout
        self.assertIn("locked", listing)
        (self.wt / "u.txt").unlink()
        self.assertIn("unlocked (harness lock) and removed", repo.remove_worktree(str(self.wt), self.root))

    def test_another_lock_reason_is_left_alone(self):
        _vcs(self.root, "worktree", "lock", "--reason", "keep me", str(self.wt))
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())


class ScratchWorktrees(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        _vcs(self.root, "init", "-q", "-b", "main")
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(self.root, "config", k, v)
        (self.root / "a.txt").write_text("a\n")
        _vcs(self.root, "add", "a.txt")
        _vcs(self.root, "commit", "-q", "-m", "init")

    def add(self, name, *flags):
        _vcs(self.root, "worktree", "add", "-q", *flags, str(self.base / name))

    def test_matches_the_names_the_ticket_lists_and_nothing_else(self):
        for n in ("pr-300", "wt/pr-300", "pr-300b", "pr-300-r2", "pr-300-head", "pr-3001", "pr-30", "xpr-300", "pr-300bb"):
            self.add(n, "--detach")
        self.add("pr-300-br", "-b", "300-x")  # on a branch: never touched
        got = sorted(Path(p).relative_to(self.base).as_posix() for p in repo.scratch_worktrees(300, self.root))
        self.assertEqual(got, ["pr-300", "pr-300-head", "pr-300-r2", "pr-300b", "wt/pr-300"])

    def test_remove_scratch_removes_them_and_reports_each(self):
        self.add("pr-5", "--detach")
        self.add("pr-5-r2", "--detach")
        out = repo.remove_scratch(5, repo.common.run("git", "rev-parse", "HEAD", cwd=self.root).stdout.strip(), self.root)
        self.assertEqual(len(out), 2)
        self.assertTrue(all("removed" in line for line in out))
        self.assertEqual(repo.scratch_worktrees(5, self.root), [])


if __name__ == "__main__":
    unittest.main()
