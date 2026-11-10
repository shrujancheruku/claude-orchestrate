"""scripts/rescue: commit, push, unlock and remove a ticket's worktree, through the CLI on a temp repo with a bare origin.

`holds` runs for real (a subprocess of `rescue`) against the fake `gh`; `rescue` itself must never call `gh`.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from harness import ScriptEnv, gh_pr_list, gh_repo_view, git, rest_pull, verdict_runs
import github  # noqa: E402  (harness puts scripts/ on sys.path)
from test_holds import dead_pid, live_reason

BRANCH = "42-the-ticket"
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rescue"


def graphql(nodes=()):
    return {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": list(nodes)}}}})}


class Rescue(unittest.TestCase):
    def setUp(self):
        self.env = verdict_runs(ScriptEnv())  # every run is checked for exactly one closing `next:` line
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

    def test_nothing_under_claude_is_committed_and_it_is_reported_as_left_behind(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("body\n")
        r = self.rescue("--title", "Fix the thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"committed 1 paths [0-9a-f]+")
        self.assertIn("left behind (not committed): 1 paths under .claude/: .claude/pr-body.md", r.stdout)
        files = git(self.env.origin, "diff", "--name-only", f"main..{BRANCH}").split()
        self.assertEqual(files, ["a.txt"])
        self.assertFalse(self.wt.exists())  # a lone pr-body.md goes with the tree

    def test_a_pr_body_at_the_worktree_root_is_not_committed_and_goes_with_the_tree(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        (self.wt / "pr-body.md").write_text("body\n")
        r = self.rescue("--title", "Fix the thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"committed 1 paths [0-9a-f]+")
        files = git(self.env.origin, "diff", "--name-only", f"main..{BRANCH}").split()
        self.assertEqual(files, ["a.txt"])
        self.assertFalse(self.wt.exists())

    def test_a_worktree_holding_only_a_root_pr_body_is_removed(self):
        self.worktree()
        (self.wt / "pr-body.md").write_text("body\n")
        r = self.rescue("--title", "Fix the thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.wt.exists())

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

    def scratch(self, name, *flags):
        tree = self.wt.parent / name
        git(self.env.repo, "worktree", "add", "--detach", *flags, str(tree), BRANCH)
        return tree

    def test_with_pr_the_prs_scratch_worktrees_are_removed_and_only_those(self):
        self.worktree()
        self.commit_in_wt()
        mine = [self.scratch("pr-9"), self.scratch("pr-9-r2")]
        other = self.scratch("pr-91")
        r = self.rescue("--pr", "9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("scratch worktree"), 2)
        for tree in mine:
            self.assertFalse(tree.exists(), tree)
        self.assertTrue(other.exists())

    def test_without_pr_no_scratch_worktree_is_touched(self):
        self.worktree()
        tree = self.scratch("pr-9")
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("scratch", r.stdout)
        self.assertTrue(tree.exists())

    def test_a_dirty_scratch_worktree_is_kept_with_the_line_and_the_rescue_still_succeeds(self):
        self.worktree()
        tree = self.scratch("pr-9")
        (tree / "x.txt").write_text("untracked")
        r = self.rescue("--pr", "9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("scratch worktree ", r.stdout)
        self.assertIn(f"{tree.name} NOT removed", r.stdout)
        self.assertTrue(tree.exists())
        self.assertFalse(self.wt.exists())

    def test_a_scratch_worktree_outside_the_branch_or_locked_by_a_live_session_is_kept(self):
        self.worktree()
        elsewhere = self.wt.parent / "pr-9"
        git(self.env.repo, "worktree", "add", "--detach", str(elsewhere), "main")
        (elsewhere / "n.txt").write_text("x")
        git(elsewhere, "add", "n.txt")
        git(elsewhere, "commit", "-m", "not in the branch")
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.kill)
        locked = self.scratch("pr-9b")
        git(self.env.repo, "worktree", "lock", "--reason", live_reason(live.pid), str(locked))
        r = self.rescue("--pr", "9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT removed: its HEAD is not the PR head or an ancestor of it", r.stdout)
        self.assertIn("NOT removed: its lock is held by another live session", r.stdout)
        self.assertTrue(elsewhere.exists() and locked.exists())

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

    def test_an_uncaught_exception_after_the_unlock_puts_the_lock_back_and_exits_2(self):
        import contextlib
        import io
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader

        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        reason = f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)"
        self.lock(reason)
        loader = SourceFileLoader("rescue_under_test", str(SCRIPT))
        mod = module_from_spec(spec_from_loader("rescue_under_test", loader))
        loader.exec_module(mod)
        err = io.StringIO()
        with unittest.mock.patch.object(mod.repo, "worktree_changes", side_effect=OSError("boom")), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = mod.main([BRANCH, "--repo", str(self.env.repo)])
        self.assertEqual(code, 2)
        self.assertIn("boom", err.getvalue())
        self.assertEqual(mod.LIFTED, [])
        self.assertTrue(self.wt.exists())
        self.assertIn(reason, git(self.env.repo, "worktree", "list", "--porcelain"))

    def test_a_multi_line_exception_message_still_gives_one_stderr_line_and_exit_2(self):
        import contextlib
        import io

        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        for patched, raised in (("worktree_changes", OSError("first\nsecond\n  third")),
                                ("worktree_changes", ValueError("cannot find\nthe merge-base"))):
            with self.subTest(raised=type(raised).__name__):
                mod = self.load_script()
                err = io.StringIO()
                with unittest.mock.patch.object(mod.repo, patched, side_effect=raised), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = mod.main([BRANCH, "--repo", str(self.env.repo)])
                self.assertEqual(code, 2)
                self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
                self.assertIn("first second third" if isinstance(raised, OSError) else "the merge-base", err.getvalue())

    def load_script(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        loader = SourceFileLoader("rescue_under_test", str(SCRIPT))
        mod = module_from_spec(spec_from_loader("rescue_under_test", loader))
        loader.exec_module(mod)
        return mod

    def run_main(self, mod, *args):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = mod.main([BRANCH, "--repo", str(self.env.repo), *args])
        return code, err.getvalue()

    def test_an_exception_after_the_removal_leaves_it_removed_and_does_not_relock(self):
        self.worktree()
        reason = f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)"
        self.lock(reason)
        mod = self.load_script()
        real = mod.common.run
        locks = []

        def spy(*a, **k):
            if a[:3] == ("git", "worktree", "lock"):
                locks.append(a)
            return real(*a, **k)

        with unittest.mock.patch.object(mod.common, "run", spy), \
                unittest.mock.patch.object(mod.repo, "remove_scratch", side_effect=OSError("late")):
            code, err = self.run_main(mod, "--pr", "9")
        self.assertEqual(code, 2)
        self.assertIn("late", err)
        self.assertFalse(self.wt.exists())
        self.assertEqual(locks, [])
        self.assertEqual(mod.LIFTED, [])

    def test_a_lock_that_cannot_be_put_back_is_reported(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        reason = f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)"
        self.lock(reason)
        mod = self.load_script()
        real = mod.common.run

        def run(*a, **k):
            if a[:3] == ("git", "worktree", "lock"):
                return unittest.mock.Mock(code=1, stdout="", stderr="no lock for you")
            return real(*a, **k)

        with unittest.mock.patch.object(mod.common, "run", run), \
                unittest.mock.patch.object(mod.repo, "worktree_changes", side_effect=OSError("boom")):
            code, err = self.run_main(mod)
        self.assertEqual(code, 2)
        self.assertIn("boom", err)
        self.assertIn(f"could not put the lock back on {self.wt}: no lock for you", err)

    def test_a_tree_dirty_only_under_claude_says_nothing_to_commit_outside_it(self):
        self.worktree(push=True)
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "pr-body.md").write_text("x\n")
        r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to commit outside .claude/", r.stdout)
        self.assertNotIn("tree clean", r.stdout)

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
        self.assertEqual(r.verdict, "next: ask-user overlap")
        self.assertFalse(self.wt.exists())  # saved all the same

    def test_every_exit_ends_one_next_line(self):
        # saved, nothing to overlap: done
        self.worktree(push=True)
        self.env.set_gh([])
        done = self.rescue()
        self.assertEqual((done.returncode, done.verdict), (0, "next: done"))
        # nothing to rescue is done as well
        nothing = self.rescue()
        self.assertEqual((nothing.returncode, nothing.verdict), (0, "next: done"))
        # a program error
        bad = self.env.run("rescue", "not-a-ticket", check=False)
        self.assertEqual((bad.returncode, bad.verdict), (2, "next: could-not-run"))

    def test_a_live_foreign_lock_ends_elsewhere(self):
        self.worktree()
        self.lock("keep me")
        r = self.rescue()
        self.assertEqual((r.returncode, r.verdict), (3, "next: elsewhere"))

    def test_a_crash_ends_could_not_run_with_its_class_and_a_rate_limit_ends_retry_after(self):
        import contextlib
        import io

        mod = self.load_script()
        def limited(argv):  # raised inside the run, as a limited gh call would
            raise github.GhError(github.RATELIMITED, "rate limit until 2026-01-01T00:05:00Z")

        def crashed(argv):
            raise OSError("boom")

        for run, want in ((crashed, "next: could-not-run OSError: boom"), (limited, "next: retry-after 2026-01-01T00:05:00Z")):
            out = io.StringIO()
            with unittest.mock.patch.object(mod, "run", run), \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(out):
                code = mod.main([BRANCH, "--repo", str(self.env.repo)])
            self.assertEqual((code, out.getvalue().strip()), (2, want))

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

    def harness_worktree(self, agent="x1"):
        """What a worker that stopped before Orient's branch step leaves: the harness's branch and path."""
        self.wt = self.wt.parent / f"agent-{agent}"
        git(self.env.repo, "worktree", "add", "-b", f"worktree-agent-{agent}", str(self.wt), "main")
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.write_text(json.dumps({"tickets": {"42": {"agent_id": agent, "model": "m"}}}))

    def remote_log_of(self, branch):
        return git(self.env.origin, "log", "--format=%s", f"main..{branch}")

    def test_a_ticket_number_rescues_a_worktree_with_no_ticket_branch_yet(self):
        self.harness_worktree()
        (self.wt / "new.txt").write_text("n\n")
        r = self.env.run("rescue", "42", "--title", "The ticket!", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertIn("branch: created 42-the-ticket", lines)
        self.assertEqual(self.remote_log_of("42-the-ticket").splitlines(), ["chore(wip): rescue #42"])
        self.assertFalse(self.wt.exists())
        self.assertEqual(lines[-1], "overlap: none")
        # the harness branch is left alone
        self.assertIn("worktree-agent-x1", git(self.env.repo, "branch", "--list", "worktree-agent-x1"))

    def rescue_n(self, *args):
        return self.env.run("rescue", "42", *args, check=False)

    def test_a_second_rescue_reuses_the_first_branch_whatever_the_title(self):
        for agent, title in (("x1", ["--title", "The ticket"]), ("x2", ["--title", "The ticket"]),
                             ("x3", ["--title", "A different title"]), ("x4", [])):
            self.harness_worktree(agent)
            (self.wt / f"{agent}.txt").write_text("n\n")
            r = self.rescue_n(*title)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("branch: created 42-the-ticket" if agent == "x1" else "branch: reused 42-the-ticket",
                          r.stdout.splitlines())
        self.assertEqual(self.remote_log_of("42-the-ticket").splitlines(), ["chore(wip): rescue #42"] * 4)
        self.assertEqual(git(self.env.repo, "branch", "--list", "42-*").split(), ["42-the-ticket"])

    def test_a_branch_that_exists_only_on_origin_is_reused(self):
        git(self.env.repo, "branch", "42-remote-only", "main")
        git(self.env.repo, "push", "origin", "42-remote-only")
        git(self.env.repo, "branch", "-D", "42-remote-only")
        self.harness_worktree()
        (self.wt / "n.txt").write_text("n\n")
        r = self.rescue_n("--title", "Other")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("branch: reused 42-remote-only", r.stdout.splitlines())
        self.assertEqual(self.remote_log_of("42-remote-only").splitlines(), ["chore(wip): rescue #42"])
        self.assertEqual(git(self.env.repo, "branch", "--list", "42-*").split(), ["42-remote-only"])

    def test_two_branches_for_the_ticket_exit_2_naming_both_and_touch_nothing(self):
        git(self.env.repo, "branch", "42-a", "main")
        git(self.env.repo, "branch", "42-b", "main")
        self.harness_worktree()
        (self.wt / "n.txt").write_text("n\n")
        r = self.rescue_n()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("42-a", r.stderr)
        self.assertIn("42-b", r.stderr)
        self.assertTrue((self.wt / "n.txt").exists())
        self.assertIn("worktree-agent-x1", git(self.wt, "branch", "--show-current"))

    def lock_dead(self):
        self.lock(f"claude agent agent-abc (pid {dead_pid()} start Sat Nov  7 06:14:20 2026)")

    def test_a_branch_checked_out_in_another_worktree_exits_2_before_unlocking(self):
        other = self.wt.parent / "other"
        git(self.env.repo, "worktree", "add", "-b", "42-busy", str(other), "main")
        self.harness_worktree()
        self.lock_dead()
        r = self.rescue_n()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(str(other), r.stderr)
        self.assertTrue(self.locked())
        self.assertNotIn("lock:", r.stdout)  # liveness never ran
        self.assertTrue(self.wt.exists())

    def test_a_failed_switch_puts_the_lock_back(self):
        git(self.env.repo, "switch", "-c", "42-conflict", "main")
        (self.env.repo / "clash.txt").write_text("on the branch\n")
        git(self.env.repo, "add", "clash.txt")
        git(self.env.repo, "commit", "-m", "feat: clash")
        git(self.env.repo, "switch", "main")
        self.harness_worktree()
        (self.wt / "clash.txt").write_text("untracked here\n")  # the switch would overwrite it
        self.lock_dead()
        r = self.rescue_n()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("cannot switch", r.stderr)
        self.assertTrue(self.locked())
        self.assertTrue((self.wt / "clash.txt").exists())

    def test_a_worktree_on_another_branch_is_refused_and_untouched(self):
        self.harness_worktree()
        git(self.wt, "switch", "-c", "7-other")
        (self.wt / "n.txt").write_text("n\n")
        r = self.rescue_n()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("7-other", r.stderr)
        self.assertEqual(git(self.wt, "branch", "--show-current").strip(), "7-other")
        self.assertEqual(git(self.env.repo, "branch", "--list", "42-*").split(), [])

    def test_a_detached_head_is_refused_and_untouched(self):
        self.harness_worktree()
        git(self.wt, "switch", "--detach")
        r = self.rescue_n()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("detached", r.stderr)
        self.assertTrue(self.wt.exists())

    def test_a_ticket_number_without_a_title_uses_a_plain_slug(self):
        self.harness_worktree()
        (self.wt / "new.txt").write_text("n\n")
        r = self.env.run("rescue", "42", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("branch: created 42-rescue", r.stdout.splitlines())

    def test_a_ticket_number_finds_the_worktree_on_the_ticket_branch(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        r = self.env.run("rescue", "42", "--title", "T", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("branch: created", r.stdout)
        self.assertEqual(self.remote_log().splitlines()[0], "chore(wip): T")

    def test_a_ticket_number_with_no_worktree_has_nothing_to_rescue(self):
        r = self.env.run("rescue", "42", check=False)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "nothing to rescue: no worktree on #42"))

    def test_a_rate_limit_over_the_budget_leaves_the_overlap_unchecked_and_exits_0(self):
        self.worktree()
        (self.wt / "a.txt").write_text("a\n")
        self.env.set_gh([{"args": ["api"], "exit": 1,
                          "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}])
        with tempfile.TemporaryDirectory() as cache, unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            r = self.rescue()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1],
                         "overlap: unchecked (rate limited; re-run holds before dispatching into these files)")
        self.assertFalse(self.wt.exists())

    def load_rescue(self):
        import importlib.machinery
        import importlib.util
        sys.path.insert(0, str(SCRIPT.parent))
        loader = importlib.machinery.SourceFileLoader("rescue_mod", str(SCRIPT))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("rescue_mod", loader))
        loader.exec_module(mod)
        return mod

    def test_the_overlap_check_runs_in_process_with_no_holds_subprocess(self):
        mod = self.load_rescue()
        self.assertFalse(hasattr(mod, "HOLDS"))
        with unittest.mock.patch.object(mod.tracker.Snapshot, "open", side_effect=mod.github.GhError(
                mod.github.RATELIMITED, "rate limit until 2026-01-01T00:00:00Z")), \
                unittest.mock.patch.object(mod, "say") as out:
            code = mod.overlap(Path("."), ["a.py"], 42)
        self.assertEqual(code, 0)
        out.assert_called_with("overlap: unchecked (rate limited; re-run holds before dispatching into these files)")

    def test_an_unlisted_holds_exception_is_an_overlap_that_could_not_be_checked(self):
        mod = self.load_rescue()
        with unittest.mock.patch.object(mod.tracker.Snapshot, "open", side_effect=RuntimeError("boom")), \
                unittest.mock.patch.object(mod, "say") as out:
            code = mod.overlap(Path("."), ["a.py"], 42)
        self.assertEqual(code, 1)
        out.assert_called_with("overlap: could not check (RuntimeError: boom)")

    def test_a_failure_after_the_branch_was_created_switches_back_and_deletes_it(self):
        self.harness_worktree()
        git(self.env.repo, "remote", "set-url", "origin", str(self.env.repo.parent / "no-such-origin"))
        r = self.rescue_n("--title", "The ticket")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(git(self.wt, "branch", "--show-current").strip(), "worktree-agent-x1")
        self.assertEqual(git(self.env.repo, "branch", "--list", "42-*").strip(), "")

    def test_a_failure_after_work_was_committed_keeps_the_new_branch(self):
        self.harness_worktree()
        (self.wt / "n.txt").write_text("n\n")
        git(self.env.repo, "remote", "set-url", "origin", str(self.env.repo.parent / "no-such-origin"))
        r = self.rescue_n("--title", "The ticket")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("kept", r.stderr)
        self.assertEqual(git(self.wt, "branch", "--show-current").strip(), "42-the-ticket")

    def test_rescue_never_calls_gh_itself_or_forces(self):
        src = SCRIPT.read_text()
        for banned in ("--force", '"gh"', "'gh'"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    sys.exit(unittest.main())
