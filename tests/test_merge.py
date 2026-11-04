"""CLI tests for scripts/merge, driven through the harness with a scripted gh."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402

# A gh that answers each `<group> <verb>` (e.g. `pr merge`) from a queue: the Nth call gets reply
# min(N, last). Replies: {"stdout", "stderr", "exit"}. Calls are recorded in <state>.calls.
SCRIPTED_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
key = " ".join(args[:2])
n = sum(1 for l in open(state + ".calls") if " ".join(json.loads(l)[:2]) == key) - 1
steps = json.load(open(state + ".seq")).get(key) or [{"exit": 1, "stderr": "no reply for " + key}]
r = steps[min(n, len(steps) - 1)]
sys.stdout.write(r.get("stdout", ""))
sys.stderr.write(r.get("stderr", ""))
sys.exit(r.get("exit", 0))
'''


def pr(state, issues=(5,), branch="feat-br", cross=False):
    return {"stdout": json.dumps({"state": state, "headRefName": branch, "headRefOid": "abc123",
                                  "isCrossRepository": cross,
                                  "closingIssuesReferences": [{"number": n} for n in issues]})}


OK = {"exit": 0}
OPEN_ISSUE = [{"stdout": '{"state":"OPEN"}'}]
CLOSED_ISSUE = [{"stdout": '{"state":"CLOSED"}'}]
REPO = [{"stdout": '{"defaultBranchRef":{"name":"main"}}'}]
REF_GET = "api repos/{owner}/{repo}/git/ref/heads/feat-br"
DELETE = "api -X"


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        (self.env.bin / "gh").write_text(SCRIPTED_GH)
        git(self.env.repo, "branch", "feat-br")

    def seq(self, steps):
        steps = {"repo view": REPO, "api -X": [OK], **steps}
        self.env.gh_state.with_name("gh-state.json.seq").write_text(json.dumps(steps))

    def merge(self, *extra):
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def deletes(self):
        return self.called(["api", "-X", "DELETE"])

    def local_branch(self):
        return "feat-br" in git(self.env.repo, "branch", "--list", "feat-br")

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


if __name__ == "__main__":
    unittest.main()
