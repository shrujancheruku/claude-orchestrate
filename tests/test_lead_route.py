"""scripts/lead-route through its CLI, with a fake `gh` serving the open issues and a fake clock (--now).

Also the prompt side: `run` calls lead-route and restates neither the 30-minute nor the resume rule (ADR-0006).
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_pr_list, gh_repo_view, git  # noqa: E402
from test_worker_live import own_start_reason  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "run" / "SKILL.md"
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MARKER = "> *Stopped for the lead.*"
ADR_MARKER = "> *ADR conflict held for the lead.*"
UNDELIVERED = "> *Waiting on lead (not delivered).*"
RESENT = "> *Resent to lead.*"
HEAD = "f" * 40  # the PR's current head commit, unless a test names another


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(minutes, body, edits=()):
    return {"body": body, "createdAt": iso(minutes), "userContentEdits": {"nodes": [{"editedAt": iso(e)} for e in edits]}}


def issue(number, labels=(), claimed=False, comments=(), body_edits=(), blocked_by=()):
    return {"number": number,
            "blockedBy": {"nodes": [{"number": b, "state": st} for b, st in blocked_by]},
            "labels": {"nodes": [{"name": name} for name in labels]},
            "assignees": {"totalCount": 1 if claimed else 0},
            "userContentEdits": {"nodes": [{"editedAt": iso(e)} for e in body_edits]},
            "comments": {"nodes": list(comments)}}


def stop(minutes, question="Which value?"):
    return comment(minutes, f"{MARKER}\n\n{question}")


def failure(minutes, marker, sha):
    """A run comment naming the head commit its failure was about, as run writes it."""
    return comment(minutes, f"{marker}\n\nhead {sha}\n\nci: test failed")


class LeadRoute(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def serve(self, issues=(), gh_exit=0, open_branches=(), wontfix=(), heads=None):
        if gh_exit:
            graphql = {"args": ["api", "graphql"], "stderr": "HTTP 500", "exit": gh_exit}
        else:
            body = {"data": {"repository": {"issues": {"nodes": list(issues)}}}}
            closed = {"data": {"repository": {"issues": {"nodes": [
                {"number": n, "assignees": {"totalCount": a}} for n, a in wontfix]}}}}
            graphql = {"args": ["api", "graphql"], "seq": [{"stdout": json.dumps(body)}, {"stdout": json.dumps(closed)}]}
        prs = [{"headRefName": b, "headRefOid": (heads or {}).get(b, HEAD), "number": 100 + i}
               for i, b in enumerate(open_branches)]
        self.responses = [gh_repo_view(),
                          graphql,
                          gh_pr_list(prs)]
        self.env.set_gh(self.responses)

    def state(self, tickets):
        path = self.env.repo / ".claude" / "orchestrate-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session": "s", "tickets": tickets}))

    def worker(self, agent="agent-a", model="claude-sonnet-4-6", last=5):
        return {"agent_id": agent, "worktree": None, "model": model, "last_report": iso(last), "touch_map": []}

    def route(self, lead="live", cap=2, now=60):
        self.env.set_gh(self.responses)  # the graphql reply is a sequence: start it over for each run
        r = self.env.run("lead-route", "--lead", lead, "--cap", str(cap), "--now", iso(now), check=False)
        return r.returncode, r.stdout.splitlines()

    def test_resume_when_the_stop_is_newer_than_the_workers_last_report(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({"1": self.worker(last=5)})
        self.assertEqual(self.route(), (0, ["RESUME #1 agent-a", "counted: 1 of 2"]))

    def test_no_verdict_when_the_worker_reported_after_the_stop(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({"1": self.worker(last=20)})
        self.assertEqual(self.route(), (0, ["counted: 1 of 2"]))

    def test_no_verdict_for_a_stop_that_is_not_the_marker_line(self):
        self.serve([issue(1, claimed=True, comments=[comment(10, "> *Stopped for the lead.* but not first")])])
        self.state({"1": self.worker(last=5)})
        self.assertEqual(self.route(), (0, ["counted: 1 of 2"]))

    def test_escalate_a_haiku_worker_whose_body_was_edited_after_the_stop(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)], body_edits=[20])])
        self.state({"1": self.worker(model="claude-haiku-4-5", last=5)})
        self.assertEqual(self.route()[1][0], "ESCALATE #1")

    def test_escalate_a_haiku_worker_when_an_agent_brief_was_added_or_edited_after_the_stop(self):
        brief_added = comment(20, "## Agent Brief\n\nnew")
        self.serve([issue(1, claimed=True, comments=[stop(10), brief_added])])
        self.state({"1": self.worker(model="claude-haiku-4-5", last=5)})
        self.assertEqual(self.route()[1][0], "ESCALATE #1")
        brief_edited = comment(5, "## Agent Brief\n\nold", edits=[25])
        self.serve([issue(1, claimed=True, comments=[stop(10), brief_edited])])
        self.assertEqual(self.route()[1][0], "ESCALATE #1")

    def test_a_haiku_worker_continues_when_nothing_changed_after_the_stop(self):
        old_brief = comment(5, "## Agent Brief\n\nold", edits=[8])  # edited before the stop: no escalation
        self.serve([issue(1, claimed=True, comments=[old_brief, stop(10)], body_edits=[9])])
        self.state({"1": self.worker(model="claude-haiku-4-5", last=5)})
        self.assertEqual(self.route()[1][0], "RESUME #1 agent-a")

    def test_a_sonnet_worker_is_never_escalated(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)], body_edits=[20])])
        self.state({"1": self.worker(model="claude-sonnet-4-6", last=5)})
        self.assertEqual(self.route()[1][0], "RESUME #1 agent-a")

    def test_redispatch_when_the_worker_is_not_in_the_state_file(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({})
        self.assertEqual(self.route(), (0, ["REDISPATCH #1", "counted: 1 of 2"]))

    def lock_worktree(self, reason, branch="1-widget"):
        """A real worktree on `branch`, locked with `reason`: another session's worker, as the harness leaves it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / branch
        git(self.env.repo, "worktree", "add", "-b", branch, str(path))
        git(self.env.repo, "worktree", "lock", "--reason", reason, str(path))

    def test_elsewhere_when_another_sessions_worker_is_live_and_it_still_counts(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({})
        self.lock_worktree(own_start_reason())
        self.assertEqual(self.route(), (0, [f"ELSEWHERE #1 pid {os.getpid()}", "counted: 1 of 2"]))

    def test_elsewhere_for_a_lock_that_does_not_parse(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({})
        self.lock_worktree("held by hand")
        self.assertEqual(self.route(), (0, ["ELSEWHERE #1 pid ?", "counted: 1 of 2"]))

    def test_redispatch_when_the_lock_is_dead_or_there_is_none(self):
        self.serve([issue(1, claimed=True, comments=[stop(10)])])
        self.state({})
        self.lock_worktree(own_start_reason(shift=-3600))  # the pid was reused: a dead lock
        self.assertEqual(self.route(), (0, ["REDISPATCH #1", "counted: 1 of 2"]))
        self.serve([issue(2, claimed=True, comments=[stop(10)])])
        self.assertEqual(self.route(), (0, ["REDISPATCH #2", "counted: 1 of 2"]))  # no worktree on 2-*

    def test_no_redispatch_when_the_ticket_already_has_an_open_pr(self):
        # A finished worker from an earlier session is not in this session's state file, but its PR is open.
        self.serve([issue(1, claimed=True, comments=[stop(10)])], open_branches=["1-widget"])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 1 of 2"]))
        self.serve([issue(1, claimed=True, comments=[stop(10)])], open_branches=["10-other"])
        self.state({})
        self.assertEqual(self.route()[1][0], "REDISPATCH #1")

    def test_fallback_hands_a_needs_lead_ticket_back_when_the_lead_is_gone(self):
        self.serve([issue(1, labels=["needs-lead", "ready-for-agent"], claimed=True, comments=[stop(10)])])
        self.state({"1": self.worker(last=5)})
        self.assertEqual(self.route(lead="none"), (0, ["FALLBACK #1", "counted: 0 of 2"]))

    def test_uncap_only_past_thirty_minutes_with_a_live_lead(self):
        self.serve([issue(1, labels=["needs-lead", "ready-for-agent"], claimed=True, comments=[stop(0)])])
        self.state({"1": self.worker(last=-5)})
        self.assertEqual(self.route(now=31), (0, ["UNCAP #1", "counted: 0 of 2"]))
        self.assertEqual(self.route(now=29), (0, ["counted: 1 of 2"]))

    def test_a_needs_lead_ticket_waits_on_a_live_lead(self):
        self.serve([issue(1, labels=["needs-lead", "ready-for-agent"], claimed=True, comments=[stop(10)])])
        self.state({"1": self.worker(last=5)})
        self.assertEqual(self.route(now=20), (0, ["counted: 1 of 2"]))

    def test_an_unclaimed_adr_conflict_needs_lead_ticket_has_no_verdict_with_a_lead(self):
        self.serve([issue(1, labels=["needs-lead"])])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 0 of 2"]))

    def test_handback_a_claimed_ticket_with_an_open_blocker(self):
        self.serve([issue(1, claimed=True, blocked_by=[(7, "OPEN"), (8, "CLOSED")]), issue(2, claimed=True)])
        self.state({})
        self.assertEqual(self.route(), (0, ["HANDBACK #1 blocked-by #7", "counted: 1 of 2"]))

    def test_no_handback_when_every_blocker_is_closed(self):
        self.serve([issue(1, claimed=True, blocked_by=[(8, "CLOSED")])])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 1 of 2"]))

    def test_abandon_a_wontfix_close_that_is_still_assigned(self):
        self.serve([], wontfix=[(4, 1), (5, 0)])
        self.state({})
        self.assertEqual(self.route(), (0, ["ABANDON #4", "counted: 0 of 2"]))

    def test_hold_pr_for_an_adr_conflict_with_a_live_lead_at_any_age(self):
        self.serve([issue(3, labels=["needs-lead"], claimed=True, comments=[comment(0, ADR_MARKER + "\n\nPR 103")])],
                   open_branches=["1-x", "2-y", "3-z"])
        self.state({"3": self.worker(last=5)})
        self.assertEqual(self.route(now=600), (0, ["HOLD-PR #3 102", "counted: 1 of 2"]))

    def test_ask_dev_for_an_adr_conflict_with_no_lead(self):
        self.serve([issue(3, labels=["needs-lead"], claimed=True, comments=[comment(0, ADR_MARKER)])],
                   open_branches=["3-z"])
        self.state({})
        self.assertEqual(self.route(lead="none"), (0, ["ASK-DEV #3 100", "counted: 1 of 2"]))

    def test_the_hold_ends_when_needs_lead_is_removed(self):
        self.serve([issue(3, labels=["ready-for-agent"], claimed=True, comments=[comment(0, ADR_MARKER)])],
                   open_branches=["3-z"])
        self.state({"3": self.worker(last=5)})
        self.assertEqual(self.route(), (0, ["counted: 1 of 2"]))

    def test_lead_none_with_nothing_to_route_prints_only_the_summary(self):
        self.serve([issue(1, labels=["lead"], comments=[])])
        self.state({})
        self.assertEqual(self.route(lead="none", cap=3), (0, ["counted: 0 of 3"]))

    def test_counted_is_the_claimed_tickets_that_hold_a_slot(self):
        self.serve([issue(1, claimed=True), issue(2, claimed=True), issue(3)])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 2 of 2"]))

    def test_a_claimed_lead_ticket_without_a_pr_is_the_leads_work_and_holds_no_slot(self):
        self.serve([issue(1, labels=["lead"], claimed=True, comments=[stop(10)]), issue(2, claimed=True)])
        self.state({})
        self.assertEqual(self.route(), (0, ["LEAD-TICKET #1", "counted: 1 of 2"]))

    def test_a_claimed_lead_ticket_with_its_pr_joins_the_pipeline(self):
        self.serve([issue(1, labels=["lead"], claimed=True)], open_branches=["1-decision"])
        self.state({})
        self.assertEqual(self.route(), (0, ["LEAD-PR #1 100", "counted: 0 of 2"]))

    def test_an_unclaimed_lead_ticket_has_no_verdict(self):
        self.serve([issue(1, labels=["lead"])], open_branches=["1-decision"])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 0 of 2"]))

    def test_a_lead_pr_failure_not_delivered_is_resent_once_per_head(self):
        self.serve([issue(1, labels=["lead"], claimed=True, comments=[failure(10, UNDELIVERED, HEAD)])],
                   open_branches=["1-decision"])
        self.state({})
        self.assertEqual(self.route(), (0, ["LEAD-PR #1 100", "RESEND #1 100", "counted: 0 of 2"]))
        self.serve([issue(1, labels=["lead"], claimed=True,
                          comments=[failure(10, UNDELIVERED, HEAD), failure(11, RESENT, HEAD)])],
                   open_branches=["1-decision"])
        self.assertEqual(self.route(), (0, ["LEAD-PR #1 100", "counted: 0 of 2"]))

    def test_no_resend_once_the_pr_has_a_new_head_since_the_failure(self):
        old = "e" * 40
        self.serve([issue(1, labels=["lead"], claimed=True, comments=[failure(10, UNDELIVERED, old)])],
                   open_branches=["1-decision"])
        self.state({})
        self.assertEqual(self.route(), (0, ["LEAD-PR #1 100", "counted: 0 of 2"]))

    def test_no_resend_without_a_live_lead(self):
        self.serve([issue(1, labels=["lead"], claimed=True, comments=[failure(10, UNDELIVERED, HEAD)])],
                   open_branches=["1-decision"])
        self.state({})
        self.assertEqual(self.route(lead="none"), (0, ["LEAD-PR #1 100", "counted: 0 of 2"]))

    def test_exit_2_when_gh_fails(self):
        self.serve(gh_exit=1)
        self.state({})
        r = self.env.run("lead-route", "--lead", "live", "--cap", "2", "--now", iso(0), check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("cannot run", r.stderr)

    def test_exit_2_when_gh_is_missing(self):
        r = self.env.run("lead-route", "--lead", "live", "--cap", "2", "--now", iso(0), check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)


class PromptSide(unittest.TestCase):
    def test_run_calls_lead_route_and_does_not_restate_its_rules(self):
        text = SKILL.read_text()
        self.assertIn('scripts/lead-route"', text)
        self.assertIsNone(re.search(r"30[- ]minute", text), "the 30-minute rule lives in scripts/lead-route")
        self.assertNotIn("newer than", text, "the resume comparison lives in scripts/lead-route")

    def test_held_paths_sentence_names_elsewhere_worktrees_and_defers_to_holds(self):
        # #253: scripts/holds holds an Elsewhere ticket's live worktree changes (#226); the run prompt must say so.
        text = SKILL.read_text()
        sentence = next(l for l in text.splitlines() if "**held paths**" in l)
        self.assertIn("**Elsewhere**", sentence)
        self.assertIn("worktree", sentence)
        self.assertIn("scripts/holds", sentence)
        self.assertIn("never compare paths yourself", sentence)

    def test_the_prompt_writes_the_marker_the_script_reads(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("lead_route", str(ROOT / "scripts" / "lead-route"))
        spec = importlib.util.spec_from_loader("lead_route", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertIn(mod.STOP_MARKER, SKILL.read_text())
        self.assertIn(mod.ADR_MARKER, SKILL.read_text())
        self.assertIn(mod.UNDELIVERED_MARKER, SKILL.read_text())
        self.assertIn(mod.RESENT_MARKER, SKILL.read_text())

    def test_run_applies_each_lead_pipeline_verb_and_never_dispatches_a_lead_ticket(self):
        text = SKILL.read_text()
        for verb in ("LEAD-TICKET", "LEAD-PR", "RESEND"):
            self.assertIn(verb, text)
        step4 = text[text.index("4. **Fill free slots.**"):text.index("5. **Report and yield.**")]
        self.assertIn("`lead`", step4, "step 4 must skip tickets labelled `lead`")

    def test_run_redispatches_through_the_rescue_and_skips_elsewhere_tickets(self):
        text = SKILL.read_text()
        verdicts = text[text.index("`ELSEWHERE #<n> pid <p>`"):text.index("`FALLBACK #<n>`")]
        self.assertIn("do nothing", verdicts)
        self.assertRegex(verdicts, r"REDISPATCH #<n>`: resume the ticket as stalled, so the rescue .* runs first")
        rescue = text[text.index("- **Resume a stalled ticket**"):text.index("- **While a limit holds**")]
        self.assertIn("skip an Elsewhere ticket", rescue)
        self.assertIn("found by branch with `git worktree list` when the state file has none", rescue)
        handover = text[text.index("2. Hand over cleanly."):text.index("3. Dispatch the new worker")]
        self.assertIn("skip an Elsewhere ticket", handover)
        self.assertIn("scripts/worker-live", text)

    def test_the_worker_reports_adr_conflicts_and_never_writes_adrs_in_lead_mode(self):
        text = (ROOT / "agents" / "worker.md").read_text()
        self.assertRegex(text, r"(?i)lead mode[^\n]*never writes? (an )?ADR")
        self.assertRegex(text, r"(?i)lead mode[^\n]*adr-conflicts")


if __name__ == "__main__":
    unittest.main()
