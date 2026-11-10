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
                                "tool_input": {"command": command},
                                **({} if agent else {"role": "orchestrator"})}) + "\n")  # as the hook logs the orchestrator (#642)

    def test_a_quiet_repo_is_clean(self):
        self.serve()
        self.assertEqual(self.sweep(), ["next: clean"])

    def test_red_main(self):
        self.serve(runs=[{"name": "test", "conclusion": "success"}, {"name": "lint", "conclusion": "failure"}])
        self.assertEqual(self.sweep(), ["red-main: lint on main concluded failure", "next: findings 1"])

    def test_stale_claim(self):
        self.serve(claimed=[4])
        self.assertEqual(self.sweep(), ["stale-claim: #4 stalled no-branch lock none", "next: findings 1"])

    def test_a_stalled_claim_holding_work_with_no_rescue_event_is_only_a_stale_claim(self):
        path = Path(self.wt.name) / "5-widget"
        git(self.env.repo, "worktree", "add", "-b", "5-widget", str(path))
        pid = dead_pid()
        git(self.env.repo, "worktree", "lock", "--reason", f"claude agent agent-abc (pid {pid} start Sat Nov  7 06:14:20 2026)", str(path))
        (path / "work.txt").write_text("unsaved\n")
        self.serve(claimed=[5])
        out = self.sweep()
        self.assertEqual(out[-1], "next: findings 1")
        self.assertTrue(out[0].startswith("stale-claim: #5 stalled"), out)

    def test_a_failed_rescue_is_read_from_the_event_alone(self):
        runlog.append_event(self.env.repo, runlog.RESCUE, "failed", ref=5, session="s1", branch="5-widget", reason="push of 5-widget failed")
        self.serve()  # no claim, no worktree: only the event
        self.assertEqual(self.sweep(), ["failed-rescue: #5 push of 5-widget failed", "next: findings 1"])

    def test_a_later_ok_rescue_clears_the_failure(self):
        for outcome in ("failed", "ok"):
            runlog.append_event(self.env.repo, runlog.RESCUE, outcome, ref=5, session="s1", branch="5-widget", reason="x")
        runlog.append_event(self.env.repo, runlog.RESCUE, "ok", ref=6, session="s1", branch="6-x", reason="x")
        runlog.append_event(self.env.repo, runlog.RESCUE, "failed", ref=7, session="s1", branch="7-x", reason="boom")
        self.serve()
        self.assertEqual(self.sweep(), ["failed-rescue: #7 boom", "next: findings 1"])

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

    def test_a_digest_mark_or_more_than_forty_prompts_hide_no_kind(self):
        self.prompt(command="gh pr list", time="2026-11-10T09:00:00+00:00")
        for i in range(45):  # the newest 40 lines of `prompts` would not reach the first kind
            self.prompt(command="git status", time=f"2026-11-10T10:{i:02d}:00+00:00")
        mark = self.env.repo / ".claude" / "orchestrate-prompts.mark"
        mark.write_text(json.dumps({"ts": "2026-11-10T11:00:00+00:00", "session": None}) + "\n")  # a digest mark after them all
        self.prompt(tool="Edit", command="/x/a.py", time="2026-11-10T12:00:00+00:00")
        self.prompt(tool="Edit", command="/x/b.py", time="2026-11-10T12:01:00+00:00")
        self.serve()
        self.assertEqual(self.sweep(), ["new-prompt: orchestrator Bash gh", "new-prompt: orchestrator Bash git",
                                        "new-prompt: orchestrator Edit", "next: findings 3"])

    def test_the_lead_with_no_state_file_session_still_writes_its_watermark(self):
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.serve()
        self.sweep()
        self.assertEqual([e["session"] for e in self.env.events() if e["kind"] == "sweep"], ["lead"])

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
