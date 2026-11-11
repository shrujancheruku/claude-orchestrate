"""CLI tests for scripts/merge, driven through the harness with a scripted gh.

The sweep after a merge is in test_merge_sweep.py and test_merge_regenerate.py; the
--approved-head readiness checks are in test_merge_readiness.py.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402
import common  # noqa: E402
from test_holds import live_reason  # noqa: E402
from harness import rest_pull  # noqa: E402
from merge_support import (CLOSED_ISSUE, ISSUE_CLOSE, ISSUE_VIEW, MergeMixin, OK, OPEN_ISSUE, PR_MERGE,  # noqa: E402
                           PR_VIEW, REF_GET, REPO_VIEW, pr)  # noqa: E402


class MergeTest(MergeMixin, unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        git(self.env.repo, "branch", "feat-br")

    def test_clean_merge_cleans_up(self):
        self.seq({PR_VIEW: [pr("OPEN"), pr("MERGED")], PR_MERGE: [OK], ISSUE_VIEW: OPEN_ISSUE,
                  ISSUE_CLOSE: [OK], REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(list(PR_MERGE)),
                         [[*PR_MERGE, "-f", "merge_method=squash", "-f", "sha=abc123"]])
        self.assertEqual(self.deletes(), [["api", "-X", "DELETE", "repos/{owner}/{repo}/git/refs/heads/feat-br"]])
        self.assertFalse(self.local_branch())
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [["api", "-X", "PATCH", "repos/{owner}/{repo}/issues/5", "-f", "state=closed",
                                                         "-f", "state_reason=completed"]])
        e = self.env.events()
        self.assertEqual([(x["kind"], x["ref"], x["outcome"]) for x in e], [("merge", 7, "merged")])
        self.assertTrue(e[0]["branch_deleted"])
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")
        self.assertEqual(p.stdout.count("next:"), 1)

    def test_the_session_flag_labels_the_merge_event_not_the_last_rebuilder(self):
        # Two sessions share the state file: the one that rebuilt last is `test-session`; `--session mine` is the caller.
        for flag, want in ((None, "test-session"), ("mine", "mine")):
            with self.subTest(session=flag):
                self.env.clear_events()
                self.seq({PR_VIEW: [pr("OPEN"), pr("MERGED")], PR_MERGE: [OK], ISSUE_VIEW: OPEN_ISSUE,
                          ISSUE_CLOSE: [OK], REF_GET: [OK]})
                p = self.merge(*(["--session", flag] if flag else []))
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual([e["session"] for e in self.env.events()], [want])
                git(self.env.repo, "branch", "-f", "feat-br")

    def test_no_session_refuses_before_the_merge(self):
        # The merged event is written after the merge; refusing then would exit 2 on a merge that happened.
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.called(list(PR_MERGE)), [])
        self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
        self.assertEqual(p.stdout, "next: could-not-run\n")

    def test_head_moved_is_a_refusal(self):
        self.seq({PR_VIEW: [pr("OPEN")], ISSUE_VIEW: OPEN_ISSUE,
                  PR_MERGE: [{"exit": 1, "stderr": "Head branch was modified. Review and try the merge again."}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("sha=abc123", self.called(list(PR_MERGE))[0])
        self.assertRegex(p.stdout.splitlines()[-1], r"^next: ask-user merge refused for PR #7: ")
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [])
        self.assertTrue(self.local_branch())
        self.assertEqual(self.env.events(), [])

    def test_502_with_merge_landed(self):
        self.seq({PR_VIEW: [pr("OPEN"), pr("MERGED")], PR_MERGE: [{"exit": 1, "stderr": "HTTP 502: Bad Gateway"}],
                  ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("already closed", p.stdout)
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [])

    def test_502_then_retry_in_progress_then_merged(self):
        self.seq({PR_VIEW: [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  PR_MERGE: [{"exit": 1, "stderr": "HTTP 502"}, {"exit": 1, "stderr": "Merge already in progress"}],
                  ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.called(list(PR_MERGE))), 2)  # not retried after "in progress"
        self.assertEqual(len(self.deletes()), 1)

    def test_unparseable_issue_view_is_could_not_read_not_already_closed(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: [{"stdout": "<html>"}], REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ticket #5: could not read", p.stdout)
        self.assertNotIn("already closed", p.stdout)
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [])

    def test_502_then_already_merged_waits_not_refuses(self):
        self.seq({PR_VIEW: [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  PR_MERGE: [{"exit": 1, "stderr": "HTTP 502"},
                               {"exit": 1, "stderr": "Pull request #7 was already merged"}],
                  ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_405_after_the_merge_landed_is_success(self):
        # a retry after a 5xx that landed: REST answers 405, the PR's own state says merged
        self.seq({PR_VIEW: [pr("OPEN"), pr("MERGED")], PR_MERGE: [{"exit": 1, "stderr": "Pull Request is not mergeable (HTTP 405)"}],
                  ("api", "repos/{owner}/{repo}/pulls/7"): [{"stdout": json.dumps(rest_pull(merged=True))}],
                  ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_405_on_an_open_pr_is_a_refusal(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [{"exit": 1, "stderr": "Pull Request is not mergeable (HTTP 405)"}],
                  ("api", "repos/{owner}/{repo}/pulls/7"): [{"stdout": json.dumps(rest_pull())}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout.splitlines()[-1], r"^next: ask-user merge refused for PR #7: ")

    def test_rest_error_texts_against_the_retry_patterns(self):
        # GitHub's docs give only the statuses (405 cannot be performed, 409 sha mismatch); these texts are what the API sends.
        merge_script = (Path(common.__file__).parent / "merge").read_text()
        ns = {}
        import re as _re
        for name in ("IN_PROGRESS", "TRANSIENT"):
            line = merge_script[merge_script.index(name + " = "):]
            exec("import re\n" + line[: line.index("\n\n")], ns)
        cases = [("Pull Request is not mergeable (HTTP 405)", False, False),
                 ("Head branch was modified. Review and try the merge again. (HTTP 409)", False, False),
                 ("Base branch was modified. Review and try the merge again. (HTTP 405)", False, True),
                 ("Merge already in progress (HTTP 405)", True, False),
                 ("Server Error (HTTP 502)", False, True),
                 ("PR 512 is not mergeable", False, False),
                 ("Pull request #509 cannot be merged: 5 files, abc500d", False, False)]
        for text, progress, transient in cases:
            with self.subTest(text=text):
                self.assertEqual(bool(ns["IN_PROGRESS"].search(text)), progress)
                self.assertEqual(bool(ns["TRANSIENT"].search(text)), transient)

    def test_bare_in_progress_text_is_not_transient(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [{"exit": 1, "stderr": "check run in progress"}]})
        self.assertEqual(self.merge().returncode, 1)

    def test_branch_already_deleted_on_host(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE,
                  REF_GET: [{"exit": 1, "stderr": "gh: Not Found (HTTP 404)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(list(PR_MERGE)), [])  # already merged: no merge command
        self.assertEqual(self.deletes(), [])
        self.assertIn("already gone", p.stdout)

    def test_delete_404_counts_as_gone(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "Reference does not exist (HTTP 422)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.env.events()[0]["branch_deleted"])

    def test_delete_failure_is_reported(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "HTTP 500"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOT deleted", p.stdout)
        self.assertFalse(self.env.events()[0]["branch_deleted"])

    def test_default_or_protected_branch_never_deleted(self):
        for branch, default in (("main", "main"), ("master", "trunk"), ("trunk", "trunk")):
            with self.subTest(branch=branch):
                # git answers first (#546): origin/HEAD names the default; the host is asked only when git has none.
                git(self.env.repo, "update-ref", f"refs/remotes/origin/{default}", "HEAD")
                git(self.env.repo, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{default}")
                self.seq({PR_VIEW: [pr("MERGED", branch=branch)], ISSUE_VIEW: CLOSED_ISSUE,
                          REPO_VIEW: [{"stdout": json.dumps({"default_branch": default})}]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.deletes(), [])
                self.assertIn("left alone", p.stdout)

    def test_special_characters_in_branch_are_quoted(self):
        for branch, quoted in (("feat#x", "feat%23x"), ("fix%2Fy", "fix%252Fy"), ("a/b#c%d", "a/b%23c%25d")):
            with self.subTest(branch=branch):
                self.seq({PR_VIEW: [pr("MERGED", branch=branch)], ISSUE_VIEW: CLOSED_ISSUE,
                          f"api repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}": [OK]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.called(["api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}"]),
                                 [["api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}"]])
                self.assertEqual(self.deletes()[-1],
                                 ["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{quoted}"])
                Path(str(self.env.gh_state) + ".calls").unlink()

    def test_fork_branch_never_deleted(self):
        self.seq({PR_VIEW: [pr("MERGED", cross=True)], ISSUE_VIEW: CLOSED_ISSUE})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.deletes(), [])
        self.assertTrue(self.local_branch())

    def test_the_default_branch_is_read_from_git_without_a_host_call(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.called(list(REPO_VIEW)), [])  # the test repo has `main`: git says so, gh is not asked

    def test_unknown_default_deletes_nothing(self):
        # Neither git (no origin/HEAD, no main or master) nor the host can say.
        git(self.env.repo, "branch", "-m", "main", "elsewhere")
        git(self.env.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REPO_VIEW: [{"exit": 1}]})
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.deletes(), [])

    def test_checked_out_local_branch_kept(self):
        git(self.env.repo, "checkout", "feat-br")
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.local_branch())
        self.assertIn("kept", p.stdout)

    def test_timeout_closes_nothing(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [OK]})
        p = self.env.run("merge", "7", "--interval", "0", "--timeout", "0", check=False)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [])
        self.assertEqual(self.env.events()[0]["outcome"], "timeout")
        self.assertEqual(p.stdout.splitlines()[-1], "next: retry")

    def test_refused_merge_fails(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [{"exit": 1, "stderr": "Pull request is not mergeable"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("not mergeable", p.stdout)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.env.events(), [])

    def test_closed_unmerged(self):
        self.seq({PR_VIEW: [pr("CLOSED")]})
        p = self.merge()
        self.assertEqual(p.returncode, 0)  # a clean end: ABANDON reads the verdict instead of ignoring an error
        self.assertEqual(self.env.events()[0]["outcome"], "closed")
        self.assertEqual(p.stdout.splitlines()[-1], "next: cleaned 7")

    def test_an_exception_still_ends_with_a_next_line(self):
        # merge is loaded in-process so run_merge can be made to raise after the merge would have landed
        import importlib.machinery
        import importlib.util
        import io
        import contextlib
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("merge_under_test", str(Path(common.__file__).parent / "merge"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        out, err = io.StringIO(), io.StringIO()

        def boom(argv=None):
            mod.VERDICT["pr"] = "7"
            raise ValueError("headRefOid\nsecond line")

        with mock.patch.object(mod, "run_merge", boom), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(["7"])
        # A program error is not a merge refusal: could-not-run (exit 2), never ask-user.
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue().splitlines()[-1], "next: could-not-run ValueError: headRefOid second line")
        self.assertIn("Traceback", err.getvalue())

    def test_a_rate_limit_ends_retry_after_its_time(self):
        import importlib.machinery
        import importlib.util
        import io
        import contextlib
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("merge_under_test2", str(Path(common.__file__).parent / "merge"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)

        def limited(argv=None):
            mod.github.GhError(mod.github.RATELIMITED, "rate limit until 2026-01-01T00:05:00Z")
            return 2

        out = io.StringIO()
        with mock.patch.object(mod, "run_merge", limited), contextlib.redirect_stdout(out):
            code = mod.main(["7"])
        self.assertEqual((code, out.getvalue().splitlines()[-1]), (2, "next: retry-after 2026-01-01T00:05:00Z"))

    def test_a_rate_limit_in_merge_pr_ends_retry_after_its_time(self):
        self.seq({PR_VIEW: [pr("OPEN")],
                  PR_MERGE: [{"exit": 1, "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}]})
        # The limit is recorded in a cache shared by every agent on the machine: keep this one in a temp dir.
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            p = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertRegex(p.stdout.splitlines()[-1], r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("ask-user", p.stdout)

    def test_a_rate_limit_seen_in_an_earlier_poll_leaves_no_retry_after_on_another_exit_2(self):
        import importlib.machinery
        import importlib.util
        import io
        import contextlib
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("merge_under_test3", str(Path(common.__file__).parent / "merge"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        calls = []

        def merge_pr(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                mod.github.rate_limited_until = "2026-01-01T00:05:00Z"  # a limit an earlier poll saw and got past
                raise mod.github.GhError(mod.github.FAILED, "HTTP 502: Bad Gateway")
            raise mod.github.GhError(mod.github.MISSING, "gh not found")

        open_pr = {"state": "OPEN", "headRefName": "feat-br", "headRefOid": "abc123"}
        out = io.StringIO()
        with mock.patch.object(mod, "view", return_value=(open_pr, None)), \
                mock.patch.object(mod.github, "merge_pr", merge_pr), mock.patch.object(mod.time, "sleep"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = mod.main(["7", "--cwd", str(self.env.repo), "--interval", "0"])
        self.assertEqual((code, len(calls)), (2, 2))
        self.assertEqual(out.getvalue().splitlines()[-1], "next: could-not-run")

    def test_unreadable_pr(self):
        self.seq({PR_VIEW: [{"exit": 1, "stderr": "boom"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, "next: could-not-run\n")

    def test_unlinked_ticket_reported_not_closed(self):
        self.seq({PR_VIEW: [pr("MERGED", issues=(5,))], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--ticket", "#9", "--worktree", str(self.env.repo.parent / "gone"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(list(ISSUE_CLOSE)[:3]), [])
        self.assertIn("#9 is not linked", p.stdout)
        self.assertIn("worktree", p.stdout)  # removal trouble is reported, never a failure

    def test_method_flag(self):
        self.seq({PR_VIEW: [pr("OPEN"), pr("MERGED")], PR_MERGE: [OK], ISSUE_VIEW: CLOSED_ISSUE,
                  REF_GET: [OK]})
        self.merge("--method", "rebase")
        self.assertIn("merge_method=rebase", self.called(list(PR_MERGE))[0])

    # A locked worktree holding the merged branch: the harness lock reason names the orchestrator's own live pid.
    HARNESS_LOCK = "claude agent agent-abc123 (pid 1 start Sat Nov  7 06:14:20 2026)"

    def locked_worktree(self, reason):
        wt = self.env.repo.parent / "wt-locked"
        git(self.env.repo, "worktree", "add", str(wt), "feat-br")
        git(self.env.repo, "worktree", "lock", "--reason", reason, str(wt))
        return wt

    def merged(self, wt):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--worktree", str(wt))
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def test_harness_locked_clean_worktree_is_unlocked_and_removed(self):
        wt = self.locked_worktree(self.HARNESS_LOCK)
        p = self.merged(wt)
        self.assertIn("unlocked (harness lock) and removed", p.stdout)
        self.assertFalse(wt.exists())
        self.assertFalse(self.local_branch())

    def test_harness_locked_dirty_worktree_is_kept_and_stays_locked(self):
        wt = self.locked_worktree(self.HARNESS_LOCK)
        (wt / "scratch.txt").write_text("untracked")
        p = self.merged(wt)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())
        self.assertTrue(self.local_branch())
        self.assertIn("locked " + self.HARNESS_LOCK, git(self.env.repo, "worktree", "list", "--porcelain"))

    def test_other_lock_reason_is_not_unlocked(self):
        wt = self.locked_worktree("keep me")
        p = self.merged(wt)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())
        self.assertTrue(self.local_branch())

    def test_worktree_on_another_branch_is_not_unlocked_or_removed(self):
        wt = self.env.repo.parent / "wt-other"
        git(self.env.repo, "worktree", "add", "-b", "someone-else", str(wt))
        git(self.env.repo, "worktree", "lock", "--reason", self.HARNESS_LOCK, str(wt))
        p = self.merged(wt)
        self.assertIn("NOT removed: it is on someone-else", p.stdout)
        self.assertTrue(wt.exists())
        self.assertIn("locked " + self.HARNESS_LOCK, git(self.env.repo, "worktree", "list", "--porcelain"))

    def test_main_checkout_is_never_removed(self):
        p = self.merged(self.env.repo)
        self.assertIn("NOT removed: it is the main checkout", p.stdout)
        self.assertTrue((self.env.repo / ".git").exists())

    def merged_at(self, wt, sha, *extra):
        reply = pr("MERGED")
        reply = {"stdout": reply["stdout"].replace("abc123", sha)}
        self.seq({PR_VIEW: [reply], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--worktree", str(wt), *extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def agent_tree(self, branch="worktree-agent-abc123", name="agent-abc123"):
        wt = self.env.repo.parent / name
        git(self.env.repo, "worktree", "add", "-b", branch, str(wt))
        return wt

    def test_worker_branch_at_the_pr_head_is_removed(self):
        wt = self.agent_tree()
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(wt.exists())

    def test_worker_branch_behind_the_pr_head_is_removed(self):
        wt = self.agent_tree()
        (self.env.repo / "later.txt").write_text("x")
        git(self.env.repo, "add", "later.txt")
        git(self.env.repo, "commit", "-m", "later")
        p = self.merged_at(wt, git(self.env.repo, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(wt.exists())

    def test_worker_branch_not_in_the_pr_head_is_kept(self):
        wt = self.agent_tree()
        base = git(wt, "rev-parse", "HEAD")
        (wt / "mine.txt").write_text("x")
        git(wt, "add", "mine.txt")
        git(wt, "commit", "-m", "unmerged work")
        p = self.merged_at(wt, base)
        self.assertIn("NOT removed: its HEAD is not the PR head", p.stdout)
        self.assertTrue(wt.exists())

    def test_an_untracked_pr_body_alone_does_not_block_the_removal(self):
        wt = self.agent_tree()
        (wt / ".claude").mkdir()
        (wt / ".claude" / "pr-body.md").write_text("body\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(wt.exists())

    def test_a_pr_body_at_the_worktree_root_alone_does_not_block_the_removal(self):
        wt = self.agent_tree()
        (wt / "pr-body.md").write_text("body\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(wt.exists())

    def test_a_pr_body_and_a_mode_only_change_do_not_block_the_removal(self):
        wt = self.agent_tree()
        (wt / "pr-body.md").write_text("body\n")
        (wt / "README.md").chmod(0o755)
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(wt.exists())

    def test_a_real_edit_beside_the_pr_body_and_a_mode_change_is_named_and_kept(self):
        wt = self.agent_tree()
        (wt / "pr-body.md").write_text("body\n")
        (wt / "README.md").chmod(0o755)
        (wt / "probe.py").write_text("x\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertIn("NOT removed", p.stdout)
        self.assertIn("probe.py", p.stdout)
        self.assertTrue(wt.exists())

    def test_a_real_edit_to_a_tracked_file_keeps_the_worktree(self):
        wt = self.agent_tree()
        (wt / "README.md").write_text("edited\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_another_untracked_file_is_named_when_it_blocks_the_removal(self):
        wt = self.agent_tree()
        (wt / "pr-body.md").write_text("body\n")
        (wt / "probe.py").write_text("x\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertIn("NOT removed", p.stdout)
        self.assertIn("probe.py", p.stdout)
        self.assertTrue(wt.exists())

    def test_another_untracked_file_beside_the_pr_body_still_blocks_the_removal(self):
        wt = self.agent_tree()
        (wt / ".claude").mkdir()
        (wt / ".claude" / "pr-body.md").write_text("body\n")
        (wt / "other.txt").write_text("x\n")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_worker_branch_of_a_different_directory_is_kept(self):
        wt = self.agent_tree("worktree-agent-zzz")
        p = self.merged_at(wt, git(wt, "rev-parse", "HEAD"))
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_detached_worktree_is_removed_only_at_the_pr_head(self):
        at = self.env.repo.parent / "lead-at"
        git(self.env.repo, "worktree", "add", "--detach", str(at))
        p = self.merged_at(at, git(at, "rev-parse", "HEAD"))
        self.assertNotIn("NOT removed", p.stdout)
        self.assertFalse(at.exists())
        away = self.env.repo.parent / "lead-away"
        git(self.env.repo, "worktree", "add", "--detach", str(away))
        p = self.merged_at(away, "f" * 40)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(away.exists())

    def test_a_lock_held_by_another_live_session_is_not_unlocked(self):
        sleeper = subprocess.Popen(["sleep", "60"])   # live, and not an ancestor of merge
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        start = common.process_start(sleeper.pid).strftime(common.LSTART_FORMAT)
        wt = self.locked_worktree(f"claude agent agent-other (pid {sleeper.pid} start {start})")
        p = self.merged(wt)
        self.assertIn("NOT removed: its lock is held by another live session", p.stdout)
        self.assertTrue(wt.exists())
        self.assertIn("locked claude agent", git(self.env.repo, "worktree", "list", "--porcelain"))

    def test_path_forms_of_the_same_worktree_all_match(self):
        parent = self.env.repo.parent
        for form in ("relative", "symlink", "slash"):
            git(self.env.repo, "worktree", "add", "-b", f"br-{form}", str(parent / f"wt-{form}"))
        (parent / "link-to-wt").symlink_to(parent / "wt-symlink")
        forms = (("relative", os.path.relpath(parent / "wt-relative", self.env.repo)),
                 ("symlink", str(parent / "link-to-wt")),
                 ("slash", str(parent / "wt-slash") + "/"))
        for form, arg in forms:
            sha = git(parent / f"wt-{form}", "rev-parse", "HEAD")
            reply = {"stdout": pr("MERGED", branch=f"br-{form}")["stdout"].replace("abc123", sha)}
            self.seq({PR_VIEW: [reply], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
            p = self.merge("--worktree", arg)
            self.assertNotIn("NOT removed", p.stdout, form)
            self.assertFalse((parent / f"wt-{form}").exists(), form)

    def test_another_repos_path_is_refused(self):
        other = self.env.repo.parent / "other-repo"
        other.mkdir()
        git(other, "init", "-q", "-b", "feat-br")
        git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        p = self.merged(other)
        self.assertIn("NOT removed: it is not a worktree of this repo", p.stdout)
        self.assertTrue(other.exists())

    def scratch(self, name, *flags):
        wt = self.env.repo.parent / "scratch" / name
        git(self.env.repo, "worktree", "add", *flags, str(wt))
        return wt

    def pr_at(self, state, sha=None):
        """The PR view with its head at `sha` (default: the main checkout's HEAD, where scratch trees are cut)."""
        sha = sha or git(self.env.repo, "rev-parse", "HEAD")
        return {"stdout": pr(state)["stdout"].replace("abc123", sha)}

    def test_merge_removes_the_prs_scratch_worktrees_and_only_those(self):
        mine = [self.scratch(n, "--detach") for n in ("pr-7", "pr-7b", "pr-7-r2")]
        other = self.scratch("pr-71", "--detach")      # PR 71's, not 7's
        on_branch = self.scratch("pr-7c", "-b", "other-br")   # a branch worktree is a worker's
        self.seq({PR_VIEW: [self.pr_at("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count("scratch worktree"), 3)
        for wt in mine:
            self.assertFalse(wt.exists(), wt)
        self.assertTrue(other.exists())
        self.assertTrue(on_branch.exists())
        listed = git(self.env.repo, "worktree", "list", "--porcelain")
        self.assertNotIn("/pr-7b\n", listed + "\n")
        self.assertIn("/pr-71\n", listed + "\n")

    def test_a_dirty_scratch_worktree_is_kept_and_reported(self):
        wt = self.scratch("pr-7", "--detach")
        (wt / "x.txt").write_text("untracked")
        self.seq({PR_VIEW: [self.pr_at("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_a_worktree_that_no_longer_exists_is_already_gone(self):
        gone = self.env.repo.parent / "gone-tree"
        self.seq({PR_VIEW: [self.pr_at("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--worktree", str(gone))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"worktree {gone} already gone\n", p.stdout)
        self.assertNotIn("NOT removed", p.stdout)

    def test_a_detached_tree_behind_the_pr_head_is_removed(self):
        # a lead tree a commit behind the head after an exit-5 update-branch still counts as the PR's tree
        wt = self.scratch("lead-tree", "--detach")
        self.env.commit("later.txt", "x", "feat: later")
        head = git(self.env.repo, "rev-parse", "HEAD")
        self.seq({PR_VIEW: [self.pr_at("MERGED", head)], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--worktree", str(wt))
        self.assertIn(f"worktree {wt} removed", p.stdout)
        self.assertFalse(wt.exists())

    def closed(self):
        self.seq({PR_VIEW: [self.pr_at("CLOSED")]})
        return self.merge()

    def test_a_pr_closed_unmerged_has_its_scratch_worktrees_removed(self):
        mine = [self.scratch(n, "--detach") for n in ("pr-7", "pr-7-r2")]
        other = self.scratch("pr-8", "--detach")
        p = self.closed()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("scratch worktree"), 2)
        for wt in mine:
            self.assertFalse(wt.exists(), wt)
        self.assertTrue(other.exists())
        self.assertEqual(p.stdout.splitlines()[-1], "next: cleaned 7")

    def test_a_dirty_scratch_worktree_of_a_closed_pr_is_kept_with_the_line(self):
        wt = self.scratch("pr-7", "--detach")
        (wt / "x.txt").write_text("untracked")
        p = self.closed()
        self.assertIn(f"scratch worktree ", p.stdout)
        self.assertIn(f"{wt.name} NOT removed", p.stdout)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_a_scratch_worktree_outside_the_pr_is_kept(self):
        outside = self.scratch("pr-7", "--detach")
        (outside / "new.txt").write_text("x")
        git(outside, "add", "new.txt")
        git(outside, "commit", "-m", "not in the PR")
        p = self.closed()
        self.assertIn("NOT removed: its HEAD is not the PR head or an ancestor of it", p.stdout)
        self.assertTrue(outside.exists())

    def test_a_scratch_worktree_locked_by_another_live_session_is_kept(self):
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.kill)
        wt = self.scratch("pr-7", "--detach")
        git(self.env.repo, "worktree", "lock", "--reason", live_reason(live.pid), str(wt))
        p = self.closed()
        self.assertIn("NOT removed: its lock is held by another live session", p.stdout)
        self.assertTrue(wt.exists())

    def test_forces_worktree_removal_only_past_a_lone_pr_body(self):
        body = (Path(__file__).resolve().parent.parent / "scripts" / "repo.py").read_text().split("def remove_worktree")[1]
        self.assertEqual(body.count("--force"), 1)
        guarded = body.split("only_leftovers(path):")[1].split("return w")[0]
        self.assertIn("--force", guarded)

    def test_gh_missing_at_the_merge_call_is_could_not_run(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [{"exit": 127, "stderr": "gh: command not found"}]})
        p = self.env.run("merge", "7", check=False)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertNotIn("refused", p.stdout)
        self.assertIn("could not run gh", p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: could-not-run")

    def test_missing_gh_is_could_not_run(self):
        p = self.env.run("merge", "7", check=False, no_gh=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, "next: could-not-run\n")
        self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(self.env.events(), [])

    def test_reply_that_is_not_a_pr_object_is_could_not_run(self):
        for reply in ("[]", "<html>not json"):
            with self.subTest(reply=reply):
                self.seq({PR_VIEW: [{"stdout": reply}]})
                p = self.merge()
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertEqual(p.stdout, "next: could-not-run\n")
                self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertEqual(self.called(list(PR_MERGE)), [])

    def behind_main(self):
        """The main checkout one commit behind origin/main; returns (old head, origin's head)."""
        old = git(self.env.repo, "rev-parse", "HEAD")
        new = self.env.commit("landed.txt", "x\n", "feat: landed")
        git(self.env.repo, "push", "origin", "main")
        git(self.env.repo, "reset", "-q", "--hard", old)
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        return old, new

    def test_clean_main_checkout_is_fast_forwarded(self):
        old, new = self.behind_main()
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), new)
        self.assertIn(f"main checkout fast-forwarded to {new[:7]}", p.stdout)

    def fast_forward_only(self):
        self.seq({REPO_VIEW: [{"stdout": '{"default_branch":"main"}'}]})
        return self.env.run("merge", "--fast-forward-only", check=False)

    def test_fast_forward_only_moves_a_stale_checkout(self):
        _, new = self.behind_main()
        p = self.fast_forward_only()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), new)
        self.assertIn(f"main checkout fast-forwarded to {new[:7]}", p.stdout)
        self.assertEqual(self.called(list(PR_MERGE)), [])

    def test_fast_forward_only_refuses_a_dirty_tree_with_exit_1(self):
        old, _ = self.behind_main()
        (self.env.repo / "README.md").write_text("local edit\n")
        p = self.fast_forward_only()
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), old)
        self.assertEqual((self.env.repo / "README.md").read_text(), "local edit\n")
        self.assertEqual(p.stdout.strip(), "main checkout not updated (uncommitted changes)")

    def test_fast_forward_only_on_a_current_checkout_exits_0(self):
        p = self.fast_forward_only()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("main checkout already up to date", p.stdout)

    def test_dirty_main_checkout_is_untouched(self):
        old, _ = self.behind_main()
        (self.env.repo / "README.md").write_text("local edit\n")
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), old)
        self.assertEqual((self.env.repo / "README.md").read_text(), "local edit\n")
        self.assertIn("main checkout not updated (uncommitted changes)", p.stdout)

    def test_main_checkout_on_another_branch_is_untouched(self):
        self.behind_main()
        git(self.env.repo, "checkout", "-q", "feat-br")
        head = git(self.env.repo, "rev-parse", "HEAD")
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), head)
        self.assertIn("main checkout not updated (on feat-br, not main)", p.stdout)

    def test_diverged_main_checkout_is_untouched(self):
        self.behind_main()
        mine = self.env.commit("mine.txt", "y\n", "feat: local only")
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), mine)
        self.assertIn("main checkout not updated (fatal: Not possible to fast-forward, aborting.)", p.stdout)

    def test_untracked_file_in_the_way_reports_gits_reason(self):
        old, _ = self.behind_main()
        (self.env.repo / "landed.txt").write_text("mine\n")
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), old)
        self.assertEqual((self.env.repo / "landed.txt").read_text(), "mine\n")
        self.assertRegex(p.stdout, r"main checkout not updated \(error: .*overwritten by merge")

    def test_up_to_date_main_checkout_says_so(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertIn("main checkout already up to date", p.stdout)
        self.assertNotIn("fast-forwarded", p.stdout)

    def test_main_checkout_ahead_of_origin_is_untouched(self):
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        mine = self.env.commit("mine.txt", "y\n", "feat: local only")
        p = self.merge()
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), mine)
        self.assertIn("main checkout not updated (ahead of origin)", p.stdout)

    def test_merge_in_progress_is_untouched(self):
        old, _ = self.behind_main()
        (self.env.repo / ".git" / "MERGE_HEAD").write_text(old + "\n")
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(git(self.env.repo, "rev-parse", "HEAD"), old)
        self.assertIn("main checkout not updated (a merge or rebase is in progress)", p.stdout)

    def test_help_prints_no_verdict(self):
        p = self.env.run("merge", "--help", check=False)
        self.assertEqual(p.returncode, 0)
        self.assertNotIn("\nnext: ", p.stdout)


if __name__ == "__main__":
    unittest.main()
