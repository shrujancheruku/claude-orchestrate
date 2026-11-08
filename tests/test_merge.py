"""CLI tests for scripts/merge, driven through the harness with a scripted gh.

The sweep after a merge is in test_merge_sweep.py and test_merge_regenerate.py; the
--approved-head readiness checks are in test_merge_readiness.py.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402
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

    def test_no_session_refuses_before_the_merge(self):
        # The merged event is written after the merge; refusing then would exit 2 on a merge that happened.
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.called(list(PR_MERGE)), [])
        self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)

    def test_head_moved_is_a_refusal(self):
        self.seq({PR_VIEW: [pr("OPEN")], ISSUE_VIEW: OPEN_ISSUE,
                  PR_MERGE: [{"exit": 1, "stderr": "Head branch was modified. Review and try the merge again."}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("sha=abc123", self.called(list(PR_MERGE))[0])
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

    def test_unknown_default_deletes_nothing(self):
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
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.env.events()[0]["outcome"], "closed")

    def test_unreadable_pr(self):
        self.seq({PR_VIEW: [{"exit": 1, "stderr": "boom"}]})
        self.assertEqual(self.merge().returncode, 2)

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

    def scratch(self, name, *flags):
        wt = self.env.repo.parent / "scratch" / name
        git(self.env.repo, "worktree", "add", *flags, str(wt))
        return wt

    def test_merge_removes_the_prs_scratch_worktrees_and_only_those(self):
        mine = [self.scratch(n, "--detach") for n in ("pr-7", "pr-7b", "pr-7-r2")]
        other = self.scratch("pr-71", "--detach")      # PR 71's, not 7's
        on_branch = self.scratch("pr-7c", "-b", "other-br")   # a branch worktree is a worker's
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
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
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOT removed", p.stdout)
        self.assertTrue(wt.exists())

    def test_never_forces_worktree_removal(self):
        self.assertNotIn("--force", (Path(__file__).resolve().parent.parent / "scripts" / "repo.py").read_text()
                         .split("def remove_worktree")[1])

    def test_gh_missing_at_the_merge_call_is_could_not_run(self):
        self.seq({PR_VIEW: [pr("OPEN")], PR_MERGE: [{"exit": 127, "stderr": "gh: command not found"}]})
        p = self.env.run("merge", "7", check=False)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertNotIn("refused", p.stdout)
        self.assertIn("could not run gh", p.stderr)

    def test_missing_gh_is_could_not_run(self):
        p = self.env.run("merge", "7", check=False, no_gh=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, "")
        self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(self.env.events(), [])

    def test_reply_that_is_not_a_pr_object_is_could_not_run(self):
        for reply in ("[]", "<html>not json"):
            with self.subTest(reply=reply):
                self.seq({PR_VIEW: [{"stdout": reply}]})
                p = self.merge()
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertEqual(p.stdout, "")
                self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertEqual(self.called(list(PR_MERGE)), [])


if __name__ == "__main__":
    unittest.main()
