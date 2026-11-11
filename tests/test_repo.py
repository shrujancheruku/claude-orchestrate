"""Unit tests for scripts/repo.py: the branch convention, timestamps and the main checkout."""

import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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


class DefaultBranchAndUnpushed(unittest.TestCase):
    """The one default-branch and unpushed-count source `rescue`, `claims`, `merge` and `holds` share (#546)."""

    def repo_with_commit(self, branch):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _vcs(root, "init", "-q", "-b", branch)
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(root, "config", k, v)
        (root / "a").write_text("a")
        _vcs(root, "add", "a")
        _vcs(root, "commit", "-q", "-m", "init")
        return root

    def test_origin_head_wins_and_the_fallback_is_not_asked(self):
        root = self.repo_with_commit("main")
        _vcs(root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
        _vcs(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        asked = []
        self.assertEqual(repo.default_branch(root, fallback=lambda: asked.append(1) or "other"), "trunk")
        self.assertEqual(asked, [])

    def test_without_origin_head_main_or_master_is_found_by_git(self):
        for name in ("main", "master"):
            with self.subTest(name):
                self.assertEqual(repo.default_branch(self.repo_with_commit(name), fallback=lambda: "x"), name)

    def test_a_repo_without_origin_head_main_or_master_asks_the_fallback_then_none(self):
        root = self.repo_with_commit("trunk")
        self.assertEqual(repo.default_branch(root, fallback=lambda: "trunk-from-host"), "trunk-from-host")
        self.assertIsNone(repo.default_branch(root))

    def test_unpushed_counts_commits_past_the_remote_branch_or_every_unremoted_commit(self):
        root = self.repo_with_commit("main")
        self.assertEqual(repo.unpushed(root, "main"), 1)  # never pushed: the one commit no remote has
        _vcs(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(repo.unpushed(root, "main"), 0)
        (root / "b").write_text("b")
        _vcs(root, "add", "b")
        _vcs(root, "commit", "-q", "-m", "two")
        self.assertEqual(repo.unpushed(root, "main"), 1)

    def test_unpushed_is_none_when_git_cannot_say(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(repo.unpushed(d, "main"))


class LeadSession(unittest.TestCase):
    """`lead_session` returns the live lead's session id, and says why when it has none (#634, #546)."""

    def ask(self, rows, here):
        import common
        real = common.run

        def fake(*cmd, **kw):
            if cmd[:2] == ("claude", "agents"):
                return common.Run(0, rows if isinstance(rows, str) else __import__("json").dumps(rows), "")
            return real(*cmd, **kw)

        with unittest.mock.patch.object(repo.shutil, "which", return_value="/bin/claude"), \
                unittest.mock.patch.object(common, "run", fake):
            return repo.lead_session(here)

    def test_the_live_lead_in_the_main_checkout_gives_its_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            here = Path(d).resolve()
            _vcs(here, "init", "-q")
            rows = [{"name": "other", "pid": 1, "cwd": str(here), "sessionId": "x"},
                    {"name": "lead", "pid": 7, "cwd": str(here), "sessionId": "sess-1"}]
            self.assertEqual(self.ask(rows, here), ("sess-1", "one live lead in this repo's main checkout"))

    def test_a_lead_row_without_a_session_id_is_not_a_session(self):
        with tempfile.TemporaryDirectory() as d:
            here = Path(d).resolve()
            _vcs(here, "init", "-q")
            for row in ({"name": "lead", "pid": 7, "cwd": str(here)}, {"name": "lead", "pid": 7, "cwd": str(here), "sessionId": ""}):
                with self.subTest(row=row):
                    self.assertEqual(self.ask([row], here), (None, "the live lead row has no sessionId"))


class PrTicket(unittest.TestCase):
    """The PR's ticket is its body's closing reference; the branch's number breaks a tie among several (#751)."""

    def test_a_body_mentioning_another_ticket_first_still_picks_the_branch_number(self):
        body = "closes #123 was the old behaviour\n\nCloses #751"
        self.assertEqual(repo.pr_ticket("751-pr-ticket-prefers-branch-closes", body), (751, 751))

    def test_only_matches_that_differ_from_the_branch_return_the_first_one(self):
        body = "Closes #123\n\nCloses #456"
        self.assertEqual(repo.pr_ticket("751-x", body), (123, 751))

    def test_no_match_falls_back_to_the_branch_number(self):
        self.assertEqual(repo.pr_ticket("751-x", "no reference here"), (751, 751))
        self.assertEqual(repo.pr_ticket("751-x", None), (751, 751))

    def test_a_branch_without_a_number_keeps_the_first_match(self):
        self.assertEqual(repo.pr_ticket("main", "closes #9\n\ncloses #4"), (9, None))
        self.assertEqual(repo.pr_ticket("main", "nothing"), (None, None))


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

    def test_a_pr_body_at_the_worktree_root_alone_goes_with_the_worktree(self):
        (self.wt / "pr-body.md").write_text("body\n")
        self.assertIn("removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertFalse(self.wt.exists())

    def test_both_pr_body_spots_together_go_with_the_worktree(self):
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("body\n")
        (self.wt / "pr-body.md").write_text("body\n")
        self.assertIn("removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertFalse(self.wt.exists())

    def test_a_mode_only_change_goes_with_the_worktree(self):
        (self.wt / "a.txt").chmod(0o755)
        (self.wt / "pr-body.md").write_text("body\n")
        self.assertIn("removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertFalse(self.wt.exists())

    def test_a_mode_change_plus_a_content_edit_keeps_the_worktree(self):
        (self.wt / "a.txt").write_text("changed\n")
        (self.wt / "a.txt").chmod(0o755)
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_a_mode_change_beside_an_untracked_file_keeps_the_worktree(self):
        (self.wt / "a.txt").chmod(0o755)
        (self.wt / "u.txt").write_text("u\n")
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_another_untracked_file_is_named_in_the_refusal(self):
        (self.wt / "pr-body.md").write_text("body\n")
        (self.wt / "probe.py").write_text("x\n")
        out = repo.remove_worktree(str(self.wt), self.root)
        self.assertIn("NOT removed", out)
        self.assertIn("untracked: ", out)
        self.assertIn("probe.py", out)
        self.assertTrue(self.wt.exists())

    def test_an_untracked_pr_body_beside_another_change_still_blocks_removal(self):
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("body\n")
        (self.wt / "u.txt").write_text("u\n")
        self.assertIn("NOT removed", repo.remove_worktree(str(self.wt), self.root))
        self.assertTrue(self.wt.exists())

    def test_git_text_that_is_parsed_is_asked_for_in_english_whatever_the_locale(self):
        # remove_worktree reads "untracked", "not a working tree" and "locked" out of git's messages
        (self.wt / "u.txt").write_text("u\n")
        seen = []
        real = repo.common.run

        def spy(*cmd, **kw):
            seen.append((cmd, kw.get("env")))
            return real(*cmd, **kw)

        with unittest.mock.patch.dict("os.environ", {"LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8", "LC_MESSAGES": "de_DE.UTF-8"}), \
                unittest.mock.patch.object(repo.common, "run", spy):
            repo.remove_worktree(str(self.wt), self.root)
        removes = [env for cmd, env in seen if cmd[:3] == ("git", "worktree", "remove")]
        self.assertTrue(removes)
        for env in removes:
            self.assertEqual(env["LC_ALL"], "C")

    def test_head_in_fetches_a_head_the_repo_does_not_have_yet(self):
        origin = self.root.parent / "origin.git"
        _vcs(self.root, "init", "-q", "--bare", str(origin))
        _vcs(self.root, "remote", "add", "origin", str(origin))
        _vcs(self.root, "push", "-q", "origin", "main")
        other = self.root.parent / "other"
        _vcs(self.root.parent, "clone", "-q", "-b", "main", str(origin), str(other))
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(other, "config", k, v)
        (other / "n.txt").write_text("n\n")
        _vcs(other, "add", "n.txt")
        _vcs(other, "commit", "-q", "-m", "ahead")
        _vcs(other, "push", "-q", "origin", "HEAD:refs/heads/pr-branch")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=other, capture_output=True, text=True).stdout.strip()
        tree = self.root.parent / "detached"
        _vcs(self.root, "worktree", "add", "-q", "--detach", str(tree))
        # the tree sits at an ancestor of the PR head, a commit this repo has never fetched
        self.assertTrue(repo.head_in(str(tree), head, self.root, exact=False))
        self.assertFalse(repo.head_in(str(tree), head, self.root, exact=True))

    def test_worktree_gone_is_true_only_for_a_path_nothing_registers(self):
        self.assertFalse(repo.worktree_gone(str(self.wt), self.root))
        self.assertTrue(repo.worktree_gone(str(self.root.parent / "never"), self.root))
        repo.remove_worktree(str(self.wt), self.root)
        self.assertTrue(repo.worktree_gone(str(self.wt), self.root))

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
