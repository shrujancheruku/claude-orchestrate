"""scripts/rescue: commit, push, unlock and remove a ticket's worktree, through the CLI on a temp repo with a bare origin.

`holds` runs for real (a subprocess of `rescue`) against the fake `gh`; `rescue` itself must never call `gh`.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import ScriptEnv, gh_pr_list, gh_repo_view, git, rest_pull
from test_holds import dead_pid, live_reason

BRANCH = "42-the-ticket"
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rescue"


def graphql(nodes=()):
    return {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": list(nodes)}}}})}


class Rescue(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(wt_dir.cleanup)
        self.wt = Path(wt_dir.name).resolve() / "wt"
        self.nothing_held()

    def nothing_held(self):
        self.env.set_gh([gh_repo_view(), gh_pr_list([], "--state", "open"), graphql(),
                         gh_pr_list([], "--state", "merged")])

    def worktree(self, push=False):
        git(self.env.repo, "worktree", "add", "-b", BRANCH, str(self.wt), "main")
        if push:
            git(self.wt, "push", "-u", "origin", BRANCH)

    def commit_in_wt(self, name="committed.txt"):
        (self.wt / name).write_text("x\n")
        git(self.wt, "add", name)
        git(self.wt, "commit", "-m", f"feat: {name}")

    def lock(self, reason):
        git(self.env.repo, "worktree", "lock", "--reason", reason, str(self.wt))

    def rescue(self, *args):
        return self.env.run("rescue", BRANCH, *args, check=False)

    def remote_log(self):
        return git(self.env.origin, "log", "--format=%s", f"main..{BRANCH}")

    def locked(self):
        return "locked" in git(self.env.repo, "worktree", "list", "--porcelain")

    def test_dirty_worktree_is_committed_pushed_and_removed(self):
        self.worktree()
        (self.wt / "tracked.txt").write_text("a\n")
        (self.wt / "sub").mkdir()
        (self.wt / "sub" / "new.txt").write_text("b\n")
        r = self.rescue("--title", "Fix the thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[1], "changed 2 paths")
        self.assertRegex(lines[2], r"^committed 2 paths [0-9a-f]+$")
        self.assertEqual(lines[3], "pushed 1 commits")
        self.assertEqual(lines[4], f"removed {self.wt}")
        self.assertEqual(lines[5], "overlap: none")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.remote_log().splitlines()[0], "chore(wip): Fix the thing")
        self.assertIn(BRANCH, git(self.env.repo, "branch", "--list", BRANCH))  # the local branch is kept

    def test_without_title_the_message_names_the_ticket(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_log().splitlines()[0], "chore(wip): #42 work in progress")

    def test_clean_pushed_worktree_is_only_removed(self):
        self.worktree(push=True)
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("committed 0 paths", r.stdout)
        self.assertIn("pushed 0 commits", r.stdout)
        self.assertIn(f"removed {self.wt}", r.stdout)
        self.assertEqual(self.remote_log().splitlines(), [])  # nothing new on the branch beyond main
        self.assertFalse(self.wt.exists())

    def test_unpushed_commits_only_are_pushed_then_removed(self):
        self.worktree(push=True)
        self.commit_in_wt("one.txt")
        self.commit_in_wt("two.txt")
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("committed 0 paths", r.stdout)
        self.assertIn("pushed 2 commits", r.stdout)
        self.assertEqual(self.remote_log().splitlines(), ["feat: two.txt", "feat: one.txt"])
        self.assertFalse(self.wt.exists())

    def test_branch_never_pushed_is_pushed(self):
        self.worktree()
        self.commit_in_wt()
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_log().splitlines(), ["feat: committed.txt"])

    def test_dead_lock_is_unlocked_and_rescued(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        self.lock(f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)")
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lock: dead", r.stdout)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.locked())

    def test_lock_of_this_session_is_unlocked_and_rescued(self):
        # The test process is an ancestor of the script it runs, as the orchestrator's session is of its own rescue.
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        self.lock(live_reason(os.getpid()))
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lock: this session", r.stdout)
        self.assertFalse(self.wt.exists())

    def test_live_foreign_pid_stops_and_touches_nothing(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        self.lock(live_reason(other.pid))
        r = self.rescue()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), f"live {other.pid} {self.wt}")
        self.assertTrue((self.wt / "a.txt").exists())
        self.assertTrue(self.locked())
        self.assertEqual(git(self.wt, "log", "--format=%s", "-1"), "chore: init")
        self.assertEqual(git(self.env.origin, "branch", "--list", BRANCH), "")

    def test_unparseable_lock_reason_stops_with_the_reason(self):
        self.worktree()
        self.lock("keep me")
        r = self.rescue()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), f"live ? {self.wt} (lock: keep me)")
        self.assertTrue(self.wt.exists())
        self.assertTrue(self.locked())

    def test_no_worktree_on_the_branch_is_nothing_to_do(self):
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"nothing to rescue: no worktree on {BRANCH}")

    def test_rejected_push_keeps_the_worktree(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        hook = self.env.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'no pushes today' >&2\nexit 1\n")
        hook.chmod(0o755)
        r = self.rescue()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("push of", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertTrue(self.wt.exists())
        self.assertNotIn("removed", r.stdout)

    def test_a_failure_after_the_unlock_puts_the_lock_back(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        reason = f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)"
        self.lock(reason)
        hook = self.env.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        r = self.rescue()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.wt.exists())
        self.assertIn(reason, git(self.env.repo, "worktree", "list", "--porcelain"))

    def test_a_branch_without_a_ticket_number_is_exit_2(self):
        git(self.env.repo, "worktree", "add", "-b", "scratch", str(self.wt), "main")
        r = self.env.run("rescue", "scratch", check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("<number>-<slug>", r.stderr)
        self.assertTrue(self.wt.exists())

    def test_failed_commit_keeps_the_worktree(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        git(self.env.repo, "config", "commit.gpgsign", "true")
        git(self.env.repo, "config", "gpg.program", "/nonexistent/gpg")
        r = self.rescue()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("commit failed", r.stderr)
        self.assertTrue((self.wt / "a.txt").exists())
        self.assertNotIn("removed", r.stdout)

    def test_overlap_lines_are_passed_on_with_exit_1(self):
        self.worktree()
        (self.wt / "shared.txt").write_text("a\n")
        self.env.set_gh([
            gh_repo_view(),
            gh_pr_list([{"number": 9, "headRefName": "b9", "isCrossRepository": False}], "--state", "open"),
            graphql(),
            {"args": ["api", "~repos/{owner}/{repo}/pulls/9/files"], "stdout": json.dumps([{"filename": "shared.txt"}])},
            {"args": ["api", "repos/{owner}/{repo}/pulls/9"], "stdout": json.dumps(rest_pull(headRefName="b9"))},
            gh_pr_list([], "--state", "merged"),
        ])
        r = self.rescue()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("overlap: shared.txt (held by open PR #9)", r.stdout.splitlines())
        self.assertFalse(self.wt.exists())  # saved all the same

    def test_holds_that_cannot_run_is_an_overlap_that_could_not_be_checked(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        self.env.set_gh([])
        r = self.rescue()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout.splitlines()[-1], r"^overlap: could not check \(.+\)$")
        self.assertFalse(self.wt.exists())

    def test_clean_worktree_skips_the_overlap_check(self):
        self.worktree(push=True)
        self.env.set_gh([])
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], "overlap: none")
        self.assertEqual(self.env.gh_calls(), [])

    def test_rescue_never_calls_gh_itself_or_forces(self):
        src = SCRIPT.read_text()
        for banned in ("--force", '"gh"', "'gh'"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    sys.exit(unittest.main())
