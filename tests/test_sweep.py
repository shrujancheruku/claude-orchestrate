"""scripts/sweep through its CLI: one fixture per check, a fake `gh`, real worktrees and the real run log."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import ScriptEnv, gh_pr_list, gh_repo_rest, git  # noqa: E402

import runlog  # noqa: E402

CHECKS = "~repos/{owner}/{repo}/commits/main/check-runs"


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class Sweep(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.wt = tempfile.TemporaryDirectory()
        self.addCleanup(self.wt.cleanup)

    def serve(self, claimed=(), prs=(), runs=()):
        nodes = [{"number": n, "labels": {"nodes": []}, "assignees": {"totalCount": 1}} for n in claimed]
        self.env.set_gh([
            gh_repo_rest({"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "main"}}),
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": nodes}}}})},
            gh_pr_list(list(prs), "--state", "open"),
            gh_pr_list([], "--state", "merged"),
            {"args": ["api", CHECKS], "stdout": json.dumps({"check_runs": list(runs)})},
        ])

    def sweep(self, *args):
        r = self.env.run("sweep", *args, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def prompt(self, tool="Bash", command="gh pr list", agent=None, session="s1", time="2026-11-10T10:00:00+00:00"):
        log = self.env.repo / ".claude" / "orchestrate-prompts.jsonl"
        with open(log, "a") as f:
            f.write(json.dumps({"time": time, "session_id": session, "agent_type": agent, "tool_name": tool,
                                "tool_input": {"command": command}}) + "\n")

    def test_a_quiet_repo_is_clean(self):
        self.serve()
        self.assertEqual(self.sweep(), ["next: clean"])

    def test_red_main(self):
        self.serve(runs=[{"name": "test", "conclusion": "success"}, {"name": "lint", "conclusion": "failure"}])
        self.assertEqual(self.sweep(), ["red-main: lint on main concluded failure", "next: findings 1"])

    def test_stale_claim(self):
        self.serve(claimed=[4])
        self.assertEqual(self.sweep(), ["stale-claim: #4 stalled no-branch lock none", "next: findings 1"])

    def test_failed_rescue_is_a_stalled_claim_whose_worktree_still_holds_work(self):
        path = Path(self.wt.name) / "5-widget"
        git(self.env.repo, "worktree", "add", "-b", "5-widget", str(path))
        pid = dead_pid()
        git(self.env.repo, "worktree", "lock", "--reason", f"claude agent agent-abc (pid {pid} start Sat Nov  7 06:14:20 2026)", str(path))
        (path / "work.txt").write_text("unsaved\n")
        self.serve(claimed=[5])
        self.assertEqual(self.sweep(), ["failed-rescue: #5 worktree holds dirty 1 unpushed 0", "next: findings 1"])

    def test_orphan_worktree_is_one_under_claude_worktrees_that_no_claim_names(self):
        stray = self.env.repo / ".claude" / "worktrees" / "stray"
        git(self.env.repo, "worktree", "add", "-b", "stray", str(stray))
        elsewhere = Path(self.wt.name) / "other"  # outside .claude/worktrees: not the harness's
        git(self.env.repo, "worktree", "add", "-b", "other", str(elsewhere))
        self.serve()
        self.assertEqual(self.sweep(), [f"orphan-worktree: {stray.resolve()} (no lock, no claim)", "next: findings 1"])

    def test_a_new_prompt_kind_is_reported_once_across_sweeps(self):
        self.prompt(command="gh pr list")
        self.prompt(command="/usr/bin/git status", agent="orchestrate:worker")
        self.serve()
        self.assertEqual(self.sweep(), ["new-prompt: orchestrator Bash gh", "new-prompt: worker Bash git", "next: findings 2"])
        self.prompt(command="gh issue view 3")
        self.prompt(command="rm -rf x")
        self.serve()
        self.assertEqual(self.sweep(), ["new-prompt: orchestrator Bash rm", "next: findings 1"])
        kinds = [e["ref"] for e in self.env.events() if e["kind"] == "sweep" and e["outcome"] == "prompt-kind"]
        self.assertEqual(kinds, ["orchestrator Bash gh", "worker Bash git", "orchestrator Bash rm"])

    def test_a_pr_in_its_third_review_round(self):
        for pr, rnd in ((12, 2), (12, 3), (13, 2)):
            runlog.append_event(self.env.repo, runlog.REVIEW, "request-changes", ref=pr, session="s1", round=rnd)
        prs = [{"number": n, "headRefName": f"{n}-x", "headRefOid": "a" * 40} for n in (12, 13)]
        self.serve(prs=prs)
        self.assertEqual(self.sweep(), ["rounds: PR #12 is in review round 3", "next: findings 1"])

    def merges(self, n):
        for i in range(n):
            runlog.append_event(self.env.repo, runlog.MERGE, "merged", ref=100 + i, session="s1")

    def test_due_counts_merges_since_the_last_sweep_from_the_log(self):
        self.merges(9)
        self.assertEqual(self.sweep("--due"), ["next: not-due 9"])
        self.merges(1)
        self.assertEqual(self.sweep("--due"), ["next: due 10"])  # a fresh process each call: the count is the log's
        self.serve()
        self.sweep()
        self.assertEqual(self.sweep("--due"), ["next: not-due 0"])
        self.merges(10)
        self.assertEqual(self.sweep("--due"), ["next: due 10"])


if __name__ == "__main__":
    unittest.main()
