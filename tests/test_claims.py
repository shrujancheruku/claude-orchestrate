"""scripts/claims.py as a pure function on hand-built dicts, then the `claims` CLI on a real temporary repo with real
locked worktrees and the fake `gh` (ADR 0009's lock fixtures: own pid for live, a dead pid, an unparseable reason)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import ScriptEnv, git  # noqa: E402

import claims  # noqa: E402
import common  # noqa: E402


def own_start_reason(pid=None, shift=0):
    """A harness-format lock reason for a running process: its pid and its start time in UTC."""
    pid = pid or os.getpid()
    start = common.process_start(pid)
    assert start is not None
    shown = (start + timedelta(seconds=shift)).astimezone(timezone.utc).strftime("%a %b %e %H:%M:%S %Y")
    return f"claude agent agent-abc (pid {pid} start {shown})"


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def issue(n, claimed=True, labels=()):
    return {"number": n, "claimed": claimed, "labels": set(labels)}


WORKER = {"agent_id": "agent-a", "model": "claude-sonnet-4-6", "last_report": None, "touch_map": []}


class Decide(unittest.TestCase):
    """One case per row of the state table; no repository: worktrees and changes are handed in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def decide(self, issues, prs=(), merged=(), worktrees=(), state=None, changes=lambda p, b: (0, 0)):
        if state is not None:
            self.state.write_text(json.dumps({"tickets": state}))
        return claims.claims(self.tmp.name, self.state, list(issues), list(prs), list(merged), list(worktrees), changes)

    def pr(self, n, branch):
        return {"number": 100 + n, "headRefName": branch, "headRefOid": "a" * 40}

    def test_lead_ticket_without_a_pr_is_with_lead(self):
        c = self.decide([issue(1, labels=["lead"])])[1]
        self.assertEqual(c.state, "with-lead")

    def test_lead_ticket_with_a_pr_is_in_review(self):
        c = self.decide([issue(1, labels=["lead"])], prs=[self.pr(1, "1-x")])[1]
        self.assertEqual((c.state, c.pr, c.branch), ("in-review", 101, "1-x"))

    def test_open_pr_is_in_review_even_with_a_worker_record(self):
        c = self.decide([issue(2)], prs=[self.pr(2, "2-x")], state={"2": WORKER})[2]
        self.assertEqual(c.state, "in-review")

    def test_merged_pr_on_an_open_issue_is_done(self):
        c = self.decide([issue(3)], merged=[{"headRefName": "3-x"}])[3]
        self.assertEqual((c.state, c.branch), ("done", "3-x"))

    def test_state_file_ticket_absent_from_the_snapshot_is_done(self):
        found = self.decide([issue(9, claimed=False)], state={"4": WORKER})
        self.assertEqual({n: c.state for n, c in found.items()}, {4: "done"})

    def test_worker_record_is_building(self):
        c = self.decide([issue(5)], state={"5": WORKER})[5]
        self.assertEqual((c.state, c.worker), ("building", WORKER))

    def test_unclaimed_open_issue_is_not_listed(self):
        self.assertEqual(self.decide([issue(6, claimed=False)]), {})

    def wt(self, n, locked=None):
        return {"path": f"/wt/{n}", "branch": f"{n}-x", "locked": locked}

    def test_live_lock_without_a_record_is_elsewhere(self):
        c = self.decide([issue(7)], worktrees=[self.wt(7, own_start_reason())])[7]
        self.assertEqual((c.state, c.lock, c.worktree), ("elsewhere", ("live", os.getpid()), "/wt/7"))

    def test_unparseable_lock_is_live_with_its_reason(self):
        c = self.decide([issue(7)], worktrees=[self.wt(7, "held by hand")])[7]
        self.assertEqual((c.state, c.lock), ("elsewhere", ("live", None, "held by hand")))

    def test_dead_lock_is_stalled(self):
        reason = own_start_reason().replace(f"pid {os.getpid()}", f"pid {dead_pid()}")
        c = self.decide([issue(8)], worktrees=[self.wt(8, reason)])[8]
        self.assertEqual((c.state, c.lock[0]), ("stalled", "dead"))

    def test_no_lock_is_stalled(self):
        c = self.decide([issue(8)], worktrees=[self.wt(8)])[8]
        self.assertEqual((c.state, c.lock, c.worktree), ("stalled", None, "/wt/8"))

    def test_no_worktree_with_a_pr_less_branch_unknown_is_stalled_without_a_branch(self):
        c = self.decide([issue(8)])[8]
        self.assertEqual((c.state, c.branch, c.worktree, c.lock, c.changes), ("stalled", None, None, None, None))

    def test_a_record_beats_a_live_lock(self):
        c = self.decide([issue(5)], worktrees=[self.wt(5, own_start_reason())], state={"5": WORKER})[5]
        self.assertEqual(c.state, "building")

    def test_changes_only_with_a_worktree_and_no_pr(self):
        calls = []
        found = self.decide([issue(1), issue(2), issue(3)], prs=[self.pr(2, "2-x")],
                            worktrees=[self.wt(1), self.wt(2), self.wt(3)],
                            changes=lambda p, b: calls.append(p) or (2, 1))
        self.assertEqual([found[n].changes for n in (1, 2, 3)], [(2, 1), None, (2, 1)])
        self.assertEqual(sorted(calls), ["/wt/1", "/wt/3"])

    def test_missing_state_file_means_no_workers(self):
        self.assertIsNone(self.decide([issue(1)])[1].worker)

    def test_unreadable_state_file_raises(self):
        self.state.write_text("{nope")
        with self.assertRaises(ValueError):
            self.decide([issue(1)])
        self.state.write_text("[]")
        with self.assertRaises(ValueError):
            self.decide([issue(1)])


class Liveness(unittest.TestCase):
    """common.worker_liveness (ADR 0009's home), on a real repo with real `git worktree lock`."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.wt_dir.cleanup)

    def worktree(self, reason=None):
        path = Path(self.wt_dir.name) / "1-widget"
        git(self.env.repo, "worktree", "add", "-b", "1-widget", str(path))
        if reason is not None:
            git(self.env.repo, "worktree", "lock", "--reason", reason, str(path))
        return path.resolve()

    def liveness(self, branch="1-widget"):
        return common.worker_liveness(self.env.repo, branch)

    def test_live_for_a_running_pid_with_the_recorded_start_time(self):
        path = self.worktree(own_start_reason())
        state, pid, wt, _ = self.liveness()
        self.assertEqual((state, pid, Path(wt).resolve()), ("live", os.getpid(), path))

    def test_dead_for_a_reused_pid_number(self):
        self.worktree(own_start_reason(shift=-3600))
        self.assertEqual(self.liveness()[0], "dead")

    def test_live_when_the_reason_does_not_parse(self):
        self.worktree("held by hand")
        self.assertEqual(self.liveness()[:2], ("live", None))

    def test_none_without_a_lock_or_a_worktree(self):
        self.worktree()
        self.assertEqual(self.liveness()[0], "none")
        self.assertEqual(self.liveness("9-nothing"), ("none", None, None, None))


class Cli(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.wt_dir.cleanup)
        self.nodes = []
        self.prs = []
        self.merged = []

    def serve(self, **kw):
        nodes = [{"number": n, "labels": {"nodes": []}, "assignees": {"totalCount": 1 if claimed else 0}}
                 for n, claimed in self.nodes]
        graphql = {"data": {"repository": {"issues": {"nodes": nodes}}}}
        self.env.set_gh([
            {"args": ["repo", "view"], "stdout": json.dumps(
                {"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "main"}})},
            {"args": ["api", "graphql"], "stdout": json.dumps(graphql), **kw},
            {"args": ["pr", "list", "--state", "open"], "stdout": json.dumps(self.prs)},
            {"args": ["pr", "list", "--state", "merged"], "stdout": json.dumps(self.merged)},
        ])

    def worktree(self, branch="1-widget", reason=None):
        path = Path(self.wt_dir.name) / branch
        git(self.env.repo, "worktree", "add", "-b", branch, str(path))
        if reason is not None:
            git(self.env.repo, "worktree", "lock", "--reason", reason, str(path))
        return path.resolve()

    def cli(self, *args, **kw):
        return self.env.run("claims", *args, check=False, **kw)

    def line(self, *args):
        self.serve()
        r = self.cli(*args)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return r.stdout.strip()

    def test_live_lock_with_own_pid_is_elsewhere_and_clean(self):
        self.nodes = [(1, True)]
        path = self.worktree(reason=own_start_reason())
        self.assertEqual(self.line("1"), f"#1 elsewhere 1-widget pr none worker none worktree {path} "
                                         f"lock live {os.getpid()} clean")

    def test_dead_lock(self):
        self.nodes = [(1, True)]
        pid = dead_pid()
        path = self.worktree(reason=own_start_reason().replace(f"pid {os.getpid()}", f"pid {pid}"))
        self.assertEqual(self.line("1"), f"#1 stalled 1-widget pr none worker none worktree {path} "
                                         f"lock dead {pid} clean")

    def test_unparseable_reason_ends_the_line_with_it(self):
        self.nodes = [(1, True)]
        path = self.worktree(reason="held by hand")
        self.assertEqual(self.line("1"), f"#1 elsewhere 1-widget pr none worker none worktree {path} "
                                         f"lock live ? clean (lock: held by hand)")

    def test_dirty_and_unpushed_counts(self):
        self.nodes = [(1, True), (2, True), (3, True)]
        a = self.worktree("1-a")
        (a / "x.txt").write_text("x")
        (a / "y.txt").write_text("y")
        b = self.worktree("2-b")
        (b / "z.txt").write_text("z")
        git(b, "add", "z.txt")
        git(b, "commit", "-m", "wip")
        c = self.worktree("3-c")
        (c / "k.txt").write_text("k")
        git(c, "add", "k.txt")
        git(c, "commit", "-m", "wip")
        (c / "m.txt").write_text("m")
        out = self.line().splitlines()
        self.assertTrue(out[0].endswith("lock none dirty 2"), out[0])
        self.assertTrue(out[1].endswith("lock none unpushed 1"), out[1])
        self.assertTrue(out[2].endswith("lock none dirty 1 unpushed 1"), out[2])

    def test_pushed_branch_is_clean_and_pr_line_has_no_changes_field(self):
        self.nodes = [(1, True), (2, True)]
        a = self.worktree("1-a")
        git(a, "push", "-u", "origin", "1-a")
        self.worktree("2-b")
        self.prs = [{"number": 7, "headRefName": "2-b", "headRefOid": "a" * 40}]
        out = self.line().splitlines()
        self.assertTrue(out[0].endswith("lock none clean"), out[0])
        self.assertTrue(out[1].startswith("#2 in-review 2-b pr 7 worker none worktree "), out[1])
        self.assertTrue(out[1].endswith("lock none"), out[1])

    def test_worker_record_and_ticket_order(self):
        self.nodes = [(9, True), (3, True)]
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"tickets": {"3": {"agent_id": "agent-q", "model": "claude-haiku-4-5"}}}))
        out = self.line().splitlines()
        self.assertEqual(out[0], "#3 building no-branch pr none worker agent-q claude-haiku-4-5 worktree gone lock none")
        self.assertTrue(out[1].startswith("#9 stalled no-branch pr none worker none worktree gone"), out[1])

    def test_not_claimed(self):
        self.nodes = [(1, False)]
        self.assertEqual(self.line("1"), "#1 not claimed")
        self.assertEqual(self.line("5"), "#5 not claimed")

    def test_missing_state_file_prints_every_claim_with_worker_none(self):
        self.nodes = [(1, True), (2, True)]
        self.assertEqual([ln.split(" worker ")[1].split()[0] for ln in self.line().splitlines()], ["none", "none"])

    def test_unreadable_state_file_exits_2(self):
        self.nodes = [(1, True)]
        self.serve()
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text("{nope")
        r = self.cli()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_failed_gh_exits_2(self):
        self.serve(stderr="HTTP 500", exit=1)
        r = self.cli()
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))
        self.assertNotIn("Traceback", r.stderr)

    def test_no_gh_exits_2(self):
        r = self.cli(no_gh=True)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1))

    def test_failed_git_call_exits_2(self):
        self.nodes = [(1, True)]
        path = self.worktree()
        (path / ".git").write_text("gitdir: /nonexistent\n")  # git status fails in the worktree
        self.serve()
        r = self.cli()
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1), r.stderr)

    def test_exit_2_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.cli(cwd=d)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
