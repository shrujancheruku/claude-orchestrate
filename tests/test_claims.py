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
from harness import ScriptEnv, gh_pr_list, gh_repo_rest, git, verdict_runs  # noqa: E402

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

    def log(self, *events):
        import runlog
        path = runlog.log_path(self.tmp.name, runlog.EVENTS_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in events))

    def test_a_haiku_workers_ticket_escalates_on_a_mechanical_trigger_and_prints_the_word(self):
        import importlib.machinery
        import importlib.util
        haiku = dict(WORKER, model="claude-haiku-4-5")
        fails = [{"kind": "ci", "outcome": "fail", "ref": 102, "head": h} for h in ("a", "b")]
        loader = importlib.machinery.SourceFileLoader("claims_cli", str(Path(claims.__file__).parent / "claims"))
        spec = importlib.util.spec_from_loader("claims_cli", loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        for events, want in (([], None), (fails[:1], None), (fails, "sonnet"),
                             ([{"kind": "review", "outcome": "request-changes", "ref": 102, "round": 2}], "sonnet")):
            with self.subTest(events=events):
                self.log(*events)
                c = self.decide([issue(2)], prs=[self.pr(2, "2-x")], state={"2": haiku})[2]
                self.assertEqual(c.escalate, want)
                self.assertEqual(cli.line(c).endswith(" escalate sonnet"), want is not None)

    def test_a_sonnet_worker_is_not_escalated(self):
        self.log(*[{"kind": "ci", "outcome": "fail", "ref": 102, "head": h} for h in ("a", "b")])
        self.assertIsNone(self.decide([issue(2)], prs=[self.pr(2, "2-x")], state={"2": WORKER})[2].escalate)

    def test_a_fork_pr_on_the_ticket_branch_is_not_the_tickets_pr(self):
        # ADR-0015: an outsider's PR named 1-widget does not put ticket 1 in review
        fork = dict(self.pr(1, "1-widget"), isCrossRepository=True)
        c = self.decide([issue(1)], prs=[fork])[1]
        self.assertNotEqual(c.state, "in-review")
        self.assertIsNone(c.pr)

    def test_lead_ticket_without_a_pr_is_with_lead(self):
        c = self.decide([issue(1, labels=["lead"])])[1]
        self.assertEqual(c.state, "with-lead")

    def test_lead_ticket_with_a_pr_is_in_review(self):
        c = self.decide([issue(1, labels=["lead"])], prs=[self.pr(1, "1-x")])[1]
        self.assertEqual((c.state, c.pr, c.branch), ("in-review", 101, "1-x"))

    def test_open_pr_is_in_review_even_with_a_worker_record(self):
        c = self.decide([issue(2)], prs=[self.pr(2, "2-x")], state={"2": WORKER})[2]
        self.assertEqual(c.state, "in-review")

    def test_a_draft_pr_is_building_not_in_review_and_is_still_linked(self):
        draft = dict(self.pr(2, "2-x"), isDraft=True)
        c = self.decide([issue(2)], prs=[draft], state={"2": WORKER})[2]
        self.assertEqual((c.state, c.pr, c.draft), ("building", 102, True))

    def test_a_draft_pr_does_not_stop_a_ticket_being_stalled_and_gets_its_changes_read(self):
        wt = {"path": self.tmp.name, "branch": "3-x", "locked": None}
        draft = dict(self.pr(3, "3-x"), isDraft=True)
        c = self.decide([issue(3)], prs=[draft], worktrees=[wt], changes=lambda p, b: (2, 1))[3]
        self.assertEqual((c.state, c.pr, c.draft, c.changes), ("stalled", 103, True, (2, 1)))

    def test_a_ready_pr_wins_over_a_draft_on_the_same_ticket(self):
        draft = dict(self.pr(4, "4-x"), isDraft=True)
        ready = dict(self.pr(4, "4-y"), number=900, isDraft=False)
        for prs in ([draft, ready], [ready, draft]):
            c = self.decide([issue(4)], prs=prs)[4]
            self.assertEqual((c.state, c.pr, c.draft), ("in-review", 900, False))

    def test_a_draft_beside_a_ready_ticket_leaves_the_ready_one_in_review(self):
        found = self.decide([issue(5), issue(6)], prs=[dict(self.pr(5, "5-x"), isDraft=True), self.pr(6, "6-x")])
        self.assertEqual((found[5].state, found[6].state), ("stalled", "in-review"))

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

    def test_own_live_lock_without_a_record_is_building_worker_unknown(self):
        # The test process is an ancestor of itself, as the session is of the worker it dispatched.
        c = self.decide([issue(7)], worktrees=[self.wt(7, own_start_reason())])[7]
        self.assertEqual((c.state, c.lock, c.worktree, c.worker), ("building", ("ours", os.getpid()), "/wt/7", None))

    def test_foreign_live_lock_without_a_record_is_elsewhere(self):
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        c = self.decide([issue(7)], worktrees=[self.wt(7, own_start_reason(other.pid))])[7]
        self.assertEqual((c.state, c.lock), ("elsewhere", ("foreign-live", other.pid)))

    def test_unparseable_lock_is_foreign_live_with_its_reason(self):
        c = self.decide([issue(7)], worktrees=[self.wt(7, "held by hand")])[7]
        self.assertEqual((c.state, c.lock), ("elsewhere", ("foreign-live", None, "held by hand")))

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

    def test_only_the_current_sessions_records_are_building(self):
        rec = lambda a: {"agent_id": a, "worktree": None, "model": "m", "last_report": "t", "touch_map": "none"}  # noqa: E731
        self.state.write_text(json.dumps({"session": "mine", "sessions": {
            "mine": {"tickets": {"1": rec("a1")}}, "other": {"tickets": {"2": rec("a2")}}}}))
        self.assertEqual(claims.read_state(self.state), {"1": rec("a1")})
        self.assertEqual(claims.read_other_state(self.state), {"2": rec("a2")})
        found = self.decide([issue(1, claimed=True), issue(2, claimed=True)])
        self.assertEqual(found[1].state, "building")
        self.assertEqual(found[2].state, "stalled")  # another session's record is not this session's worker
        self.assertIsNone(found[2].worker)

    def test_a_session_argument_names_the_current_session_not_the_last_rebuilder(self):
        rec = lambda a: {"agent_id": a, "worktree": None, "model": "m", "last_report": "t", "touch_map": "none"}  # noqa: E731
        self.state.write_text(json.dumps({"session": "other", "sessions": {
            "mine": {"tickets": {"1": rec("a1")}}, "other": {"tickets": {"2": rec("a2")}}}}))
        self.assertEqual(claims.read_state(self.state, "mine"), {"1": rec("a1")})
        self.assertEqual(claims.read_other_state(self.state, "mine"), {"2": rec("a2")})
        self.assertEqual(claims.read_state(self.state, "unknown"), {})  # a session with no entry has no workers

    def test_another_sessions_record_with_a_live_foreign_lock_is_elsewhere(self):
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.kill)
        self.state.write_text(json.dumps({"session": "mine", "sessions": {
            "mine": {"tickets": {}}, "other": {"tickets": {"2": {"worktree": "/wt/2"}}}}}))
        wt = {"path": "/wt/2", "branch": "worktree-agent-zz", "locked": own_start_reason(other.pid)}
        c = self.decide([issue(2, claimed=True)], worktrees=[wt])[2]
        self.assertEqual((c.state, c.lock, c.worker), ("elsewhere", ("foreign-live", other.pid), None))

    def test_an_old_shape_file_is_the_current_sessions(self):
        self.state.write_text(json.dumps({"session": "s", "tickets": {"1": {"agent_id": "a"}}}))
        self.assertEqual(claims.read_state(self.state), {"1": {"agent_id": "a"}})
        self.assertEqual(claims.read_other_state(self.state), {})

    def test_unreadable_state_file_raises(self):
        self.state.write_text("{nope")
        with self.assertRaises(ValueError):
            self.decide([issue(1)])
        self.state.write_text("[]")
        with self.assertRaises(ValueError):
            self.decide([issue(1)])


class WaitingAndIdle(unittest.TestCase):
    """`waiting` and `idle` (#501), from run-log and pass-log fixtures written under the repo the claims are read for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def pr(self, n, branch):
        return {"number": 100 + n, "headRefName": branch, "headRefOid": "a" * 40}

    def report(self, n, outcome="valid", ts=None, **extra):
        import runlog
        e = runlog.append_event(self.tmp.name, runlog.WORKER_REPORT, outcome, ref=str(n), session="s", **extra)
        if ts:  # rewrite the stamped time so a test can order events
            path = runlog.log_path(self.tmp.name, runlog.EVENTS_LOG)
            lines = path.read_text().splitlines()
            lines[-1] = json.dumps(dict(e, ts=ts), separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n")

    def nudge(self, n, ts):
        import runlog
        runlog.append_pass(self.tmp.name, "s", {"scope": runlog.NUDGE_SCOPE, "nudged": [n]})
        path = runlog.log_path(self.tmp.name, runlog.PASS_LOG)
        lines = path.read_text().splitlines()
        lines[-1] = json.dumps(dict(json.loads(lines[-1]), ts=ts), separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

    def one(self, n, state=None, prs=(), locked=None, labels=()):
        wt = {"path": f"/wt/{n}", "branch": f"{n}-x", "locked": locked}
        if state is not None:
            self.state.write_text(json.dumps({"tickets": state}))
        return claims.claims(self.tmp.name, self.state, [issue(n, labels=labels)], list(prs), [], [wt], lambda p, b: (0, 0))[n]

    def worker(self, dispatched="2026-01-01T10:00:00Z"):
        return dict(WORKER, dispatched=dispatched) if dispatched else dict(WORKER)

    def park(self, n, outcome="parked"):
        import runlog
        runlog.append_event(self.tmp.name, runlog.PARK, outcome, ref=100 + n, ticket=n, session="s")

    def test_a_parked_ticket_is_parked_not_building_or_stalled(self):
        self.park(1)
        self.assertEqual(self.one(1, state={"1": WORKER}).state, "parked")
        self.park(2)
        self.assertEqual(self.one(2).state, "parked")  # no record: not stalled, so never redispatched

    def test_a_parked_claim_is_listed_but_not_counted_against_the_cap(self):
        self.park(1)
        wts = [{"path": f"/wt/{n}", "branch": f"{n}-x", "locked": None} for n in (1, 2, 3)]
        self.state.write_text(json.dumps({"tickets": {"1": WORKER, "2": WORKER}}))
        found = claims.claims(self.tmp.name, self.state, [issue(1), issue(2), issue(3)], [self.pr(3, "3-x")], [], wts,
                              lambda p, b: (0, 0))
        self.assertEqual({n: c.state for n, c in found.items()}, {1: "parked", 2: "building", 3: "in-review"})
        self.assertEqual(claims.slot_use(found), (2, 1))  # the parked claim is the 1, and the cap sees only the other two
        self.assertEqual(claims.slots_line(found, 4), "slots: 2/4 building, 1 parked")
        self.assertEqual(claims.slots_line(found, None), "slots: 2/? building, 1 parked")

    def test_unparking_reopens_the_claim_and_an_open_pr_beats_parked(self):
        self.park(1)
        self.park(1, "unparked")
        self.assertEqual(self.one(1, state={"1": WORKER}).state, "building")
        self.park(3)
        self.assertEqual(self.one(3, prs=[self.pr(3, "3-x")]).state, "in-review")

    def test_a_park_event_over_a_still_open_draft_pr_is_not_parked(self):
        self.park(3)
        draft = dict(self.pr(3, "3-x"), isDraft=True)
        self.assertNotEqual(self.one(3, prs=[draft]).state, "parked")

    def test_w_permission_denied_replaces_building(self):
        self.report(1, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        c = self.one(1, state={"1": WORKER})
        self.assertEqual((c.state, c.reason), ("waiting", "permission-denied"))
        self.assertTrue(c.since.endswith("Z"))

    def test_w_permission_denied_beats_stalled(self):
        self.report(2, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        c = self.one(2)  # no record, no lock: stalled without the report
        self.assertEqual((c.state, c.reason), ("waiting", "permission-denied"))

    def test_w_an_exhausted_transient_stop_waits_and_a_retried_one_does_not(self):
        self.report(3, status="stopped", stop_reason="transient", exhausted=True)
        self.report(4, status="stopped", stop_reason="transient")
        self.assertEqual((self.one(3).state, self.one(3).reason), ("waiting", "exhausted"))
        self.assertEqual(self.one(4).state, "stalled")

    def test_w_a_ready_pr_beats_waiting_and_a_draft_does_not(self):
        self.report(5, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        self.assertEqual(self.one(5, prs=[self.pr(5, "5-x")]).state, "in-review")
        draft = dict(self.pr(5, "5-x"), isDraft=True)
        self.assertEqual(self.one(5, prs=[draft]).state, "waiting")

    def test_w_a_newer_report_ends_waiting_and_a_duplicate_does_not(self):
        self.report(6, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        self.report(6, "duplicate", status="stopped", stop_reason="permission-denied")
        self.assertEqual(self.one(6).state, "waiting")
        self.report(6, status="pr-opened")
        self.assertEqual(self.one(6).state, "stalled")

    def test_w_waiting_beats_idle(self):
        self.report(7, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        self.assertEqual(self.one(7, state={"7": self.worker()}).state, "waiting")

    # --- #657: paused, waiting gate / round-3, repeat and incomplete annotations, all from run-log events ---

    def event(self, kind, outcome, ref, **extra):
        import runlog
        runlog.append_event(self.tmp.name, kind, outcome, ref=ref, session="s", **extra)

    def test_p_a_limit_or_error_report_pauses_building_and_stalled(self):
        self.report(1, "limit")
        self.report(2, "error")
        c1, c2 = self.one(1, state={"1": WORKER}), self.one(2)
        self.assertEqual((c1.state, c1.reason, c2.state, c2.reason), ("paused", "limit", "paused", "error"))
        self.assertTrue(c1.since.endswith("Z"))

    def test_p_a_ready_pr_beats_paused_and_a_newer_report_or_a_draft_ends_it(self):
        self.report(3, "limit")
        self.assertEqual(self.one(3, prs=[self.pr(3, "3-x")]).state, "in-review")
        self.assertEqual(self.one(3, prs=[dict(self.pr(3, "3-x"), isDraft=True)]).state, "paused")
        self.report(3, "valid")
        self.assertEqual(self.one(3).state, "stalled")

    def test_p_paused_beats_idle_and_a_permission_denied_report_is_waiting_not_paused(self):
        self.report(4, "interim")
        self.report(4, "limit")
        self.assertEqual(self.one(4, state={"4": self.worker()}).state, "paused")
        self.report(5, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        self.assertEqual(self.one(5).state, "waiting")

    def test_p_a_resume_message_does_not_end_the_pause_so_the_skill_sends_it_once_per_since(self):
        # #747/#757: a SendMessage resume leaves no event `claims` reads; only the worker's newer report ends the pause.
        # `claims` keeps saying `paused limit since <ts>` with the same ts, and Running out of usage tells the orchestrator
        # to send one resume per `since` time instead of one per pass.
        self.report(6, "limit")
        first = self.one(6, state={"6": WORKER})
        self.event("ci", "pass", 106)  # other traffic in the log, even on this ticket
        again = self.one(6, state={"6": WORKER})
        self.assertEqual((again.state, again.reason, again.since), (first.state, first.reason, first.since))
        self.report(6, "valid")
        self.assertNotEqual(self.one(6, state={"6": WORKER}).state, "paused")

    def test_p_a_paused_claim_occupies_a_slot(self):
        self.report(6, "limit")
        found = {6: self.one(6, state={"6": WORKER})}
        self.assertEqual(claims.slot_use(found), (1, 0))

    def test_g_a_gate_hold_on_the_ready_pr_is_waiting_gate_and_a_newer_merge_event_ends_it(self):
        self.event("merge", "gate-hold", 107, gate="hooks/**")
        c = self.one(7, prs=[self.pr(7, "7-x")])
        self.assertEqual((c.state, c.reason), ("waiting", "gate"))
        self.assertTrue(c.since.endswith("Z"))
        self.event("merge", "timeout", 107)
        self.assertEqual(self.one(7, prs=[self.pr(7, "7-x")]).state, "in-review")

    def test_g_a_gate_hold_on_another_pr_or_with_no_ready_pr_changes_nothing(self):
        self.event("merge", "gate-hold", 199)
        self.assertEqual(self.one(7, prs=[self.pr(7, "7-x")]).state, "in-review")
        self.event("merge", "gate-hold", 107)
        self.assertEqual(self.one(7).state, "stalled")

    def test_t_each_transition_makes_its_waiting_word_while_its_label_is_on(self):
        for verb, reason, label in (("stop-for-lead", "question", "needs-lead"), ("adr-hold", "adr-conflict", "needs-lead"),
                                    ("to-info", "info", "needs-info")):
            with self.subTest(verb=verb):
                n = {"stop-for-lead": 21, "adr-hold": 22, "to-info": 23}[verb]
                self.event("transition", verb, n)
                self.assertNotEqual(self.one(n, state={str(n): WORKER}).state, "waiting")  # no label: answered
                c = self.one(n, state={str(n): WORKER}, labels=[label])
                self.assertEqual((c.state, c.reason), ("waiting", reason))
                self.assertTrue(c.since.endswith("Z"))
                self.assertEqual(self.one(n, labels=[label]).state, "waiting")  # a stalled claim too
                self.assertNotEqual(self.one(n, labels=["needs-info" if label == "needs-lead" else "needs-lead"]).state,
                                    "waiting")

    def test_t_the_newest_transition_decides_and_a_gate_on_a_ready_pr_still_wins(self):
        self.event("transition", "stop-for-lead", 24)
        self.event("transition", "adr-hold", 24)
        self.assertEqual(self.one(24, labels=["needs-lead"]).reason, "adr-conflict")
        self.event("transition", "stop-for-lead", 25)
        c = self.one(25, prs=[self.pr(25, "25-x")], labels=["needs-lead"])
        self.assertEqual((c.state, c.reason), ("waiting", "question"))  # a ready PR waits too
        self.event("merge", "gate-hold", 125)
        self.assertEqual(self.one(25, prs=[self.pr(25, "25-x")], labels=["needs-lead"]).reason, "gate")

    def test_r_a_round_two_request_for_changes_makes_the_third_round_wait(self):
        self.event("review", "request-changes", 108, round=1)
        self.assertEqual(self.one(8, prs=[self.pr(8, "8-x")]).state, "in-review")
        self.event("review", "request-changes", 108, round=2)
        c = self.one(8, prs=[self.pr(8, "8-x")])
        self.assertEqual((c.state, c.reason), ("waiting", "round-3"))
        self.event("review", "approve", 108, round=3)
        self.assertEqual(self.one(8, prs=[self.pr(8, "8-x")]).state, "in-review")

    def test_r_a_gate_beats_round_3(self):
        self.event("review", "request-changes", 109, round=2)
        self.event("merge", "gate-hold", 109)
        self.assertEqual(self.one(9, prs=[self.pr(9, "9-x")]).reason, "gate")

    def test_c_waiting_gate_is_the_same_in_a_fresh_session(self):  # a compaction keeps no context: only the log
        self.event("merge", "gate-hold", 107)
        first = self.one(7, prs=[self.pr(7, "7-x")])
        second = self.one(7, prs=[self.pr(7, "7-x")])
        self.assertEqual((first.state, first.reason, first.since), (second.state, second.reason, second.since))

    def test_n_a_second_not_a_stop_or_missing_report_none_is_a_repeat(self):
        self.report(1, "not-a-stop", status="done")
        self.assertEqual(self.one(1, state={"1": WORKER}).repeat, ())
        self.report(1, "not-a-stop", status="done")
        self.report(1, "missing-report")
        self.report(1, "missing-report", pr=101, head="a" * 40)
        self.assertEqual(self.one(1, state={"1": WORKER}).repeat, ("not-a-stop",))
        self.report(1, "missing-report")
        self.assertEqual(self.one(1, state={"1": WORKER}).repeat, ("not-a-stop", "missing-report-none"))

    def test_n_a_redispatch_starts_the_count_again_and_a_duplicate_is_not_counted(self):
        self.report(2, "not-a-stop", ts="2026-01-01T08:30:00+00:00")
        self.report(2, "not-a-stop", ts="2026-01-01T09:30:00+00:00")
        self.assertEqual(self.one(2, state={"2": self.worker("2026-01-01T08:00:00Z")}).repeat, ("not-a-stop",))
        self.assertEqual(self.one(2, state={"2": self.worker("2026-01-01T10:00:00Z")}).repeat, ())
        self.report(3, "not-a-stop")
        self.report(3, "duplicate")
        self.assertEqual(self.one(3).repeat, ())

    def test_n_a_reviewer_incomplete_in_a_row_is_counted_on_the_open_pr(self):
        pr = [self.pr(4, "4-x")]
        self.assertEqual(self.one(4, prs=pr).incomplete, 0)
        self.event("review", "incomplete", 104)
        self.assertEqual(self.one(4, prs=pr).incomplete, 1)
        self.event("review", "incomplete", 104)
        self.assertEqual(self.one(4, prs=pr).incomplete, 2)
        self.event("review", "approve", 104, round=1)
        self.assertEqual(self.one(4, prs=pr).incomplete, 0)

    def test_i_an_interim_after_dispatch_is_idle_though_the_lock_is_ours(self):
        self.report(8, "interim", ts="2026-01-01T10:05:00+00:00")
        c = self.one(8, state={"8": self.worker()}, locked=own_start_reason())
        self.assertEqual((c.state, c.since, c.nudged), ("idle", "2026-01-01T10:05:00Z", None))

    def test_i_a_newest_valid_report_means_not_idle(self):
        self.report(10, "interim", ts="2026-01-01T10:05:00+00:00")
        self.report(10, "valid", ts="2026-01-01T10:10:00+00:00", status="stopped")
        self.assertEqual(self.one(10, state={"10": self.worker()}).state, "building")

    def test_i_an_interim_older_than_the_dispatch_is_an_older_workers(self):
        self.report(11, "interim", ts="2026-01-01T09:55:00+00:00")
        self.assertEqual(self.one(11, state={"11": self.worker()}).state, "building")

    def test_i_no_dispatched_time_means_building(self):
        self.report(12, "interim", ts="2026-01-01T10:05:00+00:00")
        self.assertEqual(self.one(12, state={"12": self.worker(None)}).state, "building")

    def test_i_a_duplicate_is_ignored(self):
        self.report(16, "interim", ts="2026-01-01T10:05:00+00:00")
        self.report(16, "duplicate", ts="2026-01-01T10:10:00+00:00", status="pr-opened")
        c = self.one(16, state={"16": self.worker()})
        self.assertEqual((c.state, c.since), ("idle", "2026-01-01T10:05:00Z"))

    def test_i_the_pass_logs_nudge_shows_when_it_is_not_older_than_the_interim(self):
        self.report(13, "interim", ts="2026-01-01T10:05:00+00:00")
        self.nudge(13, "2026-01-01T10:30:00+00:00")
        c = self.one(13, state={"13": self.worker()})
        self.assertEqual((c.state, c.nudged), ("idle", "2026-01-01T10:30:00Z"))
        self.report(14, "interim", ts="2026-01-01T10:05:00+00:00")
        self.nudge(14, "2026-01-01T09:00:00+00:00")  # before this dispatch: an older worker's nudge
        self.assertIsNone(self.one(14, state={"14": self.worker()}).nudged)

    def test_i_a_second_interim_after_a_nudge_stays_nudged(self):  # #690
        self.report(17, "interim", ts="2026-01-01T10:05:00+00:00")
        self.nudge(17, "2026-01-01T10:10:00+00:00")
        self.report(17, "interim", ts="2026-01-01T10:20:00+00:00")
        c = self.one(17, state={"17": self.worker()})
        self.assertEqual((c.state, c.since, c.nudged), ("idle", "2026-01-01T10:20:00Z", "2026-01-01T10:10:00Z"))

    def test_i_a_completion_after_a_nudge_is_no_longer_idle(self):  # #690
        self.report(18, "interim", ts="2026-01-01T10:05:00+00:00")
        self.nudge(18, "2026-01-01T10:10:00+00:00")
        self.report(18, "valid", ts="2026-01-01T10:20:00+00:00", status="stopped")
        c = self.one(18, state={"18": self.worker()})
        self.assertEqual((c.state, c.nudged), ("building", None))

    def test_i_a_ticket_with_a_ready_pr_is_never_idle(self):
        self.report(15, "interim", ts="2026-01-01T10:05:00+00:00")
        self.assertEqual(self.one(15, state={"15": self.worker()}, prs=[self.pr(15, "15-x")]).state, "in-review")


class LongLog(WaitingAndIdle):
    """#683: a 20,000-line events log, mostly `gh_call` lines, gives the states a short log gives. Inherits the
    waiting and idle cases and runs each after the log is padded, as a long-lived project's log is."""

    def setUp(self):
        super().setUp()
        import runlog
        path = runlog.log_path(self.tmp.name, runlog.EVENTS_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        noise = json.dumps({"kind": "gh_call", "outcome": "ok", "ref": "pr", "session": "s", "ts": "2026-01-01T00:00:00+00:00",
                            "argv": ["pr", "list", "--json", "number"]}, separators=(",", ":"))
        path.write_text((noise + "\n") * 19_990 + "not json\n[]\n" + '{"kind":"worker-report","ref":' + "\n" * 2)

    def test_the_log_is_20000_lines_and_a_report_after_it_still_counts(self):
        import runlog
        self.assertGreaterEqual(len(runlog.log_path(self.tmp.name, runlog.EVENTS_LOG).read_text().splitlines()), 19_990)
        self.report(99, status="stopped", stop_reason="permission-denied", denied_command="gh pr create")
        self.assertEqual(self.one(99).state, "waiting")

    def test_read_kinds_parses_only_the_asked_kinds_whatever_the_spacing(self):
        import runlog
        path = runlog.log_path(self.tmp.name, runlog.EVENTS_LOG)
        with open(path, "a") as f:
            f.write('{"kind": "ci", "outcome": "fail", "ref": "7"}\n{"kind":"gh_call","note":"ci"}\n')
        self.assertEqual([e["kind"] for e in runlog.read_kinds(self.tmp.name, ("ci",))], ["ci"])


class Liveness(unittest.TestCase):
    """common.worker_liveness, the one reader of a lock (ADR 0009): lock text and pid state -> the four states."""

    def test_table(self):
        foreign = subprocess.Popen(["sleep", "60"])
        self.addCleanup(foreign.wait)
        self.addCleanup(foreign.kill)
        gone = dead_pid()
        rows = [
            (None, ("none", None)),
            (own_start_reason(), ("ours", os.getpid())),
            (own_start_reason(foreign.pid), ("foreign-live", foreign.pid)),
            (own_start_reason(shift=-3600), ("dead", os.getpid())),  # the pid number reused by another process
            (f"claude agent agent-abc (pid {gone} start Sat Nov  7 06:14:20 2026)", ("dead", gone)),
            ("held by hand", ("foreign-live", None)),
            ("", ("foreign-live", None)),
        ]
        for reason, want in rows:
            with self.subTest(reason=reason):
                self.assertEqual(common.worker_liveness(reason), want)


class Cli(unittest.TestCase):
    def setUp(self):
        self.env = verdict_runs(ScriptEnv(), "claims")  # claims ends in exactly one `next:` line, checked on every run
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
            gh_repo_rest({"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "main"}}),
            {"args": ["api", "graphql"], "stdout": json.dumps(graphql), **kw},
            gh_pr_list(self.prs, "--state", "open"),
            gh_pr_list(self.merged, "--state", "merged"),
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

    def test_slots_ends_the_listing_with_the_building_count_against_the_cap_and_parked_apart(self):
        import runlog
        self.nodes = [(1, True), (2, True)]
        self.worktree("1-widget", reason=own_start_reason())
        runlog.append_event(self.env.repo, runlog.PARK, "parked", ref=102, ticket=2, session="s")
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text("## Slots\n\n- **Slots:** 4. throttle\n")
        self.serve()
        r = self.cli("--slots")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines[1].startswith("#2 parked "), lines)
        self.assertEqual(lines[-1], "slots: 1/4 building, 1 parked")
        self.serve()  # a named ticket narrows the listing, never the count
        self.assertEqual(self.cli("2", "--slots").stdout.strip().splitlines()[-1], "slots: 1/4 building, 1 parked")
        self.serve()
        self.assertNotIn("slots:", self.cli().stdout)  # opt-in: the default listing is unchanged

    def test_own_live_lock_is_building_worker_unknown_and_clean(self):
        self.nodes = [(1, True)]
        path = self.worktree(reason=own_start_reason())
        self.assertEqual(self.line("1"), f"#1 building 1-widget pr none worker unknown worktree {path} "
                                         f"lock ours {os.getpid()} clean")

    def test_foreign_live_lock_is_elsewhere(self):
        self.nodes = [(1, True)]
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        path = self.worktree(reason=own_start_reason(other.pid))
        self.assertEqual(self.line("1"), f"#1 elsewhere 1-widget pr none worker none worktree {path} "
                                         f"lock foreign-live {other.pid} clean")

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
                                         f"lock foreign-live ? clean (lock: held by hand)")

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

    def two_session_state(self):
        """A state file whose `session` (the last rebuilder) is "other"; "mine" has ticket 3, "other" has ticket 9."""
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        rec = lambda a: {"agent_id": a, "model": "m"}  # noqa: E731
        state.write_text(json.dumps({"session": "other", "sessions": {
            "mine": {"tickets": {"3": rec("agent-q")}}, "other": {"tickets": {"9": rec("agent-z")}}}}))

    def test_session_flag_makes_that_session_current_not_the_last_rebuilder(self):
        self.nodes = [(9, True), (3, True)]
        self.two_session_state()
        out = self.line("--session", "mine").splitlines()
        self.assertEqual(out[0], "#3 building no-branch pr none worker agent-q m worktree gone lock none")
        self.assertTrue(out[1].startswith("#9 stalled no-branch pr none worker none"), out[1])
        out = self.line().splitlines()  # no flag: the last rebuilder ("other") is current, as before
        self.assertTrue(out[0].startswith("#3 stalled"), out[0])
        self.assertTrue(out[1].startswith("#9 building"), out[1])

    def test_session_flag_with_no_entry_reads_no_workers(self):
        self.nodes = [(3, True)]
        self.two_session_state()
        self.assertTrue(self.line("--session", "unknown").startswith("#3 stalled no-branch pr none worker none"))

    def test_session_flag_labels_gh_call_events_with_that_session_not_the_state_files(self):
        env = ScriptEnv(gh_log=True)
        self.addCleanup(env.close)
        self.env = env
        self.nodes = [(3, True)]
        self.two_session_state()
        self.serve()
        self.assertEqual(self.cli("--session", "mine").returncode, 0)
        sessions = {e["session"] for e in env.events(profiling=True) if e["kind"] == "gh_call"}
        self.assertEqual(sessions, {"mine"})

    def harness_worktree(self, agent="x1"):
        """A worktree where the harness puts it (`agent-<id>`), on its branch `worktree-agent-<id>`."""
        path = Path(self.wt_dir.name) / f"agent-{agent}"
        git(self.env.repo, "worktree", "add", "-b", f"worktree-agent-{agent}", str(path))
        return path.resolve()

    def record(self, ticket, agent):
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"tickets": {str(ticket): {"agent_id": agent, "model": "claude-sonnet-4-6"}}}))

    def test_a_worktree_on_the_harness_branch_is_found_by_the_agent_id_with_no_branch_yet(self):
        self.nodes = [(7, True)]
        path = self.harness_worktree("x1")
        (path / "a.txt").write_text("a")
        self.record(7, "x1")
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        data = json.loads(state.read_text())
        data["tickets"]["7"]["dispatched"] = "2026-01-01T00:00:00Z"
        state.write_text(json.dumps(data))
        import runlog
        runlog.append_event(self.env.repo, runlog.WORKER_REPORT, "interim", ref="7", session="s")  # after the dispatch: idle (#576)
        line = self.line("7")
        self.assertRegex(line, r"^#7 idle since \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ none-yet pr none ")
        self.assertTrue(line.endswith(f" worker x1 claude-sonnet-4-6 worktree {path} lock none dirty 1"), line)

    def test_a_waiting_ticket_prints_its_reason_and_time(self):
        self.nodes = [(7, True)]
        path = self.harness_worktree("x1")
        self.record(7, "x1")
        import runlog
        runlog.append_event(self.env.repo, runlog.WORKER_REPORT, "valid", ref="7", status="stopped",
                            stop_reason="permission-denied", denied_command="gh pr create", session="s")
        self.assertRegex(self.line("7"), r"^#7 waiting permission-denied since \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ none-yet pr none ")
        self.assertIn(f"worktree {path} ", self.line("7"))

    def test_a_paused_ticket_and_the_annotations_print_their_words(self):
        self.nodes = [(7, True)]
        self.harness_worktree("x1")
        self.record(7, "x1")
        import runlog
        for outcome in ("not-a-stop", "not-a-stop", "limit"):
            runlog.append_event(self.env.repo, runlog.WORKER_REPORT, outcome, ref="7", session="s")
        line = self.line("7")
        self.assertRegex(line, r"^#7 paused limit since \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ none-yet pr none ")
        self.assertTrue(line.endswith(" repeat not-a-stop"), line)

    def test_slots_reads_the_greedy_cap_back_from_the_run_log(self):
        import runlog
        self.nodes = [(1, True)]
        self.worktree("1-widget", reason=own_start_reason())
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text("## Slots\n\n- **Slots:** 2. throttle\n")
        self.serve()
        self.assertEqual(self.cli("--slots").stdout.strip().splitlines()[-1], "slots: 1/2 building, 0 parked")
        runlog.append_event(self.env.repo, runlog.GREEDY, "set", session="launch", cap=6)
        self.serve()
        self.assertEqual(self.cli("--slots").stdout.strip().splitlines()[-1], "slots: 1/6 building, 0 parked greedy")
        runlog.append_event(self.env.repo, runlog.GREEDY, "off", session="launch", cap=None)  # a later plain launch
        self.serve()
        self.assertEqual(self.cli("--slots").stdout.strip().splitlines()[-1], "slots: 1/2 building, 0 parked")

    def test_an_agent_id_with_the_agent_prefix_finds_it_too(self):
        self.nodes = [(7, True)]
        path = self.harness_worktree("x1")
        self.record(7, "agent-x1")
        self.assertIn(f"worktree {path} ", self.line("7"))

    def test_another_workers_harness_worktree_is_not_this_tickets(self):
        self.nodes = [(7, True)]
        self.harness_worktree("other")
        self.record(7, "x1")
        self.assertIn("worktree gone", self.line("7"))

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

    def test_a_worktree_whose_directory_is_gone_reads_as_gone(self):
        self.nodes = [(1, True)]
        path = self.worktree()
        import shutil
        shutil.rmtree(path)
        self.assertEqual(self.line("1"), "#1 stalled no-branch pr none worker none worktree gone lock none")

    def test_success_ends_next_done(self):
        self.nodes = [(1, True)]
        self.serve()
        r = self.cli()
        self.assertEqual((r.returncode, r.verdict), (0, "next: done"))

    def test_a_program_error_ends_could_not_run(self):
        self.serve(stderr="HTTP 500", exit=1)
        r = self.cli()
        self.assertEqual((r.returncode, r.stdout, r.verdict), (2, "", "next: could-not-run"))

    def test_a_rate_limit_ends_retry_after_with_the_reset_time(self):
        self.serve(stderr="gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n", exit=1)
        with tempfile.TemporaryDirectory() as cache:
            r = self.cli(extra_env={"XDG_CACHE_HOME": cache})
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertRegex(r.verdict, r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_exit_2_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.cli(cwd=d)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
