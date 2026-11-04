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


def pr(state, issues=(5,), branch="feat-br"):
    return {"stdout": json.dumps({"state": state, "headRefName": branch,
                                  "closingIssuesReferences": [{"number": n} for n in issues]})}


OK = {"exit": 0}
OPEN_ISSUE = [{"stdout": '{"state":"OPEN"}'}]
CLOSED_ISSUE = [{"stdout": '{"state":"CLOSED"}'}]


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        (self.env.bin / "gh").write_text(SCRIPTED_GH)
        git(self.env.repo, "push", "origin", "main:feat-br")

    def seq(self, **steps):
        self.env.gh_state.with_name("gh-state.json.seq").write_text(
            json.dumps({k.replace("_", " "): v for k, v in steps.items()}))

    def merge(self, *extra):
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def remote_branch(self):
        return bool(git(self.env.repo, "ls-remote", "--heads", "origin", "feat-br"))

    def test_clean_merge_cleans_up(self):
        self.seq(pr_view=[pr("OPEN"), pr("MERGED")], pr_merge=[OK], issue_view=OPEN_ISSUE, issue_close=[OK])
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [["pr", "merge", "7", "--squash"]])
        self.assertFalse(self.remote_branch())
        self.assertEqual(self.called(["issue", "close"]), [["issue", "close", "5"]])
        e = self.env.events()
        self.assertEqual([(x["kind"], x["ref"], x["outcome"]) for x in e], [("merge", 7, "merged")])
        self.assertTrue(e[0]["branch_deleted"])

    def test_502_with_merge_landed(self):
        self.seq(pr_view=[pr("OPEN"), pr("MERGED")], pr_merge=[{"exit": 1, "stderr": "HTTP 502: Bad Gateway"}],
                 issue_view=CLOSED_ISSUE)
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("already closed", p.stdout)
        self.assertEqual(self.called(["issue", "close"]), [])

    def test_502_then_retry_in_progress_then_merged(self):
        self.seq(pr_view=[pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                 pr_merge=[{"exit": 1, "stderr": "HTTP 502"}, {"exit": 1, "stderr": "Merge already in progress"}],
                 issue_view=CLOSED_ISSUE)
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 2)  # not retried after "in progress"
        self.assertFalse(self.remote_branch())

    def test_branch_already_deleted_is_fine(self):
        git(self.env.repo, "push", "origin", "--delete", "feat-br")
        self.seq(pr_view=[pr("MERGED")], issue_view=CLOSED_ISSUE)
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])  # already merged: no merge command
        self.assertIn("already gone", p.stdout)

    def test_timeout_closes_nothing(self):
        self.seq(pr_view=[pr("OPEN")], pr_merge=[OK])
        p = self.env.run("merge", "7", "--interval", "0", "--timeout", "0", check=False)
        self.assertEqual(p.returncode, 3)
        self.assertTrue(self.remote_branch())
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertEqual(self.env.events()[0]["outcome"], "timeout")

    def test_refused_merge_fails(self):
        self.seq(pr_view=[pr("OPEN")], pr_merge=[{"exit": 1, "stderr": "Pull request is not mergeable"}])
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("not mergeable", p.stdout)
        self.assertTrue(self.remote_branch())
        self.assertEqual(self.env.events(), [])

    def test_closed_unmerged(self):
        self.seq(pr_view=[pr("CLOSED")])
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.env.events()[0]["outcome"], "closed")

    def test_unreadable_pr(self):
        self.seq(pr_view=[{"exit": 1, "stderr": "boom"}])
        self.assertEqual(self.merge().returncode, 2)

    def test_extra_ticket_and_missing_worktree(self):
        self.seq(pr_view=[pr("MERGED", issues=())], issue_view=OPEN_ISSUE, issue_close=[OK])
        p = self.merge("--ticket", "#9", "--worktree", str(self.env.repo.parent / "gone"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["issue", "close"]), [["issue", "close", "9"]])
        self.assertIn("worktree", p.stdout)  # removal trouble is reported, never a failure

    def test_method_flag(self):
        self.seq(pr_view=[pr("OPEN"), pr("MERGED")], pr_merge=[OK], issue_view=CLOSED_ISSUE)
        self.merge("--method", "rebase")
        self.assertEqual(self.called(["pr", "merge"]), [["pr", "merge", "7", "--rebase"]])


if __name__ == "__main__":
    unittest.main()
