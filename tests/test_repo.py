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


if __name__ == "__main__":
    unittest.main()
