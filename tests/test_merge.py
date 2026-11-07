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
from merge_support import CLOSED_ISSUE, MergeMixin, OK, OPEN_ISSUE, REF_GET, pr  # noqa: E402


class MergeTest(MergeMixin, unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        git(self.env.repo, "branch", "feat-br")

    def test_clean_merge_cleans_up(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [OK], "issue view": OPEN_ISSUE,
                  "issue close": [OK], REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]),
                         [["pr", "merge", "7", "--squash", "--match-head-commit", "abc123"]])
        self.assertEqual(self.deletes(), [["api", "-X", "DELETE", "repos/{owner}/{repo}/git/refs/heads/feat-br"]])
        self.assertFalse(self.local_branch())
        self.assertEqual(self.called(["issue", "close"]), [["issue", "close", "5"]])
        e = self.env.events()
        self.assertEqual([(x["kind"], x["ref"], x["outcome"]) for x in e], [("merge", 7, "merged")])
        self.assertTrue(e[0]["branch_deleted"])

    def test_head_moved_is_a_refusal(self):
        self.seq({"pr view": [pr("OPEN")], "issue view": OPEN_ISSUE,
                  "pr merge": [{"exit": 1, "stderr": "Head branch was modified. Review and try the merge again."}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("--match-head-commit", self.called(["pr", "merge"])[0])
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertTrue(self.local_branch())
        self.assertEqual(self.env.events(), [])

    def test_502_with_merge_landed(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [{"exit": 1, "stderr": "HTTP 502: Bad Gateway"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("already closed", p.stdout)
        self.assertEqual(self.called(["issue", "close"]), [])

    def test_502_then_retry_in_progress_then_merged(self):
        self.seq({"pr view": [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  "pr merge": [{"exit": 1, "stderr": "HTTP 502"}, {"exit": 1, "stderr": "Merge already in progress"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 2)  # not retried after "in progress"
        self.assertEqual(len(self.deletes()), 1)

    def test_unparseable_issue_view_is_could_not_read_not_already_closed(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": [{"stdout": "<html>"}], REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ticket #5: could not read", p.stdout)
        self.assertNotIn("already closed", p.stdout)
        self.assertEqual(self.called(["issue", "close"]), [])

    def test_502_then_already_merged_waits_not_refuses(self):
        self.seq({"pr view": [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  "pr merge": [{"exit": 1, "stderr": "HTTP 502"},
                               {"exit": 1, "stderr": "Pull request #7 was already merged"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_bare_in_progress_text_is_not_transient(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [{"exit": 1, "stderr": "check run in progress"}]})
        self.assertEqual(self.merge().returncode, 1)

    def test_branch_already_deleted_on_host(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE,
                  REF_GET: [{"exit": 1, "stderr": "gh: Not Found (HTTP 404)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])  # already merged: no merge command
        self.assertEqual(self.deletes(), [])
        self.assertIn("already gone", p.stdout)

    def test_delete_404_counts_as_gone(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "Reference does not exist (HTTP 422)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.env.events()[0]["branch_deleted"])

    def test_delete_failure_is_reported(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "HTTP 500"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOT deleted", p.stdout)
        self.assertFalse(self.env.events()[0]["branch_deleted"])

    def test_default_or_protected_branch_never_deleted(self):
        for branch, default in (("main", "main"), ("master", "trunk"), ("trunk", "trunk")):
            with self.subTest(branch=branch):
                self.seq({"pr view": [pr("MERGED", branch=branch)], "issue view": CLOSED_ISSUE,
                          "repo view": [{"stdout": json.dumps({"defaultBranchRef": {"name": default}})}]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.deletes(), [])
                self.assertIn("left alone", p.stdout)

    def test_special_characters_in_branch_are_quoted(self):
        for branch, quoted in (("feat#x", "feat%23x"), ("fix%2Fy", "fix%252Fy"), ("a/b#c%d", "a/b%23c%25d")):
            with self.subTest(branch=branch):
                self.seq({"pr view": [pr("MERGED", branch=branch)], "issue view": CLOSED_ISSUE,
                          f"api repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}": [OK]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.called(["api"])[0], ["api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}"])
                self.assertEqual(self.deletes()[-1],
                                 ["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{quoted}"])
                Path(str(self.env.gh_state) + ".calls").unlink()

    def test_fork_branch_never_deleted(self):
        self.seq({"pr view": [pr("MERGED", cross=True)], "issue view": CLOSED_ISSUE})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.deletes(), [])
        self.assertTrue(self.local_branch())

    def test_unknown_default_deletes_nothing(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, "repo view": [{"exit": 1}]})
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.deletes(), [])

    def test_checked_out_local_branch_kept(self):
        git(self.env.repo, "checkout", "feat-br")
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.local_branch())
        self.assertIn("kept", p.stdout)

    def test_timeout_closes_nothing(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [OK]})
        p = self.env.run("merge", "7", "--interval", "0", "--timeout", "0", check=False)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertEqual(self.env.events()[0]["outcome"], "timeout")

    def test_refused_merge_fails(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [{"exit": 1, "stderr": "Pull request is not mergeable"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("not mergeable", p.stdout)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.env.events(), [])

    def test_closed_unmerged(self):
        self.seq({"pr view": [pr("CLOSED")]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.env.events()[0]["outcome"], "closed")

    def test_unreadable_pr(self):
        self.seq({"pr view": [{"exit": 1, "stderr": "boom"}]})
        self.assertEqual(self.merge().returncode, 2)

    def test_unlinked_ticket_reported_not_closed(self):
        self.seq({"pr view": [pr("MERGED", issues=(5,))], "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--ticket", "#9", "--worktree", str(self.env.repo.parent / "gone"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertIn("#9 is not linked", p.stdout)
        self.assertIn("worktree", p.stdout)  # removal trouble is reported, never a failure

    def test_method_flag(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [OK], "issue view": CLOSED_ISSUE,
                  REF_GET: [OK]})
        self.merge("--method", "rebase")
        self.assertEqual(self.called(["pr", "merge"])[0][:4], ["pr", "merge", "7", "--rebase"])

    # A locked worktree holding the merged branch: the harness lock reason names the orchestrator's own live pid.
    HARNESS_LOCK = "claude agent agent-abc123 (pid 1 start Sat Nov  7 06:14:20 2026)"

    def locked_worktree(self, reason):
        wt = self.env.repo.parent / "wt-locked"
        git(self.env.repo, "worktree", "add", str(wt), "feat-br")
        git(self.env.repo, "worktree", "lock", "--reason", reason, str(wt))
        return wt

    def merged(self, wt):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK]})
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

    def test_never_forces_worktree_removal(self):
        self.assertNotIn("--force", (Path(__file__).resolve().parent.parent / "scripts" / "repo.py").read_text()
                         .split("def remove_worktree")[1])

    def test_gh_missing_at_the_merge_call_is_could_not_run(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [{"exit": 127, "stderr": "gh: command not found"}]})
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
                self.seq({"pr view": [{"stdout": reply}]})
                p = self.merge()
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertEqual(p.stdout, "")
                self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertEqual(self.called(["pr", "merge"]), [])


if __name__ == "__main__":
    unittest.main()
