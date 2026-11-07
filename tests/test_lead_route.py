"""scripts/lead-route through its CLI, with a fake `gh` serving the open issues and a fake clock (--now).

Also the prompt side: `run` calls lead-route and restates neither the 30-minute nor the resume rule (ADR-0006).
"""

import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "run" / "SKILL.md"
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MARKER = "> *Stopped for the lead.*"


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(minutes, body, edits=()):
    return {"body": body, "createdAt": iso(minutes), "userContentEdits": {"nodes": [{"editedAt": iso(e)} for e in edits]}}


def issue(number, labels=(), claimed=False, comments=(), body_edits=()):
    return {"number": number,
            "labels": {"nodes": [{"name": name} for name in labels]},
            "assignees": {"totalCount": 1 if claimed else 0},
            "userContentEdits": {"nodes": [{"editedAt": iso(e)} for e in body_edits]},
            "comments": {"nodes": list(comments)}}


def stop(minutes, question="Which value?"):
    return comment(minutes, f"{MARKER}\n\n{question}")


class LeadRoute(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def serve(self, issues=(), gh_exit=0, open_branches=()):
        if gh_exit:
            graphql = {"args": ["api", "graphql"], "stderr": "HTTP 500", "exit": gh_exit}
        else:
            body = {"data": {"repository": {"issues": {"nodes": list(issues)}}}}
            graphql = {"args": ["api", "graphql"], "stdout": json.dumps(body)}
        prs = [{"headRefName": b} for b in open_branches]
        self.env.set_gh([{"args": ["repo", "view"], "stdout": json.dumps({"owner": {"login": "o"}, "name": "r"})},
                         graphql,
                         {"args": ["pr", "list"], "stdout": json.dumps(prs)}])

    def state(self, tickets):
        path = self.env.repo / ".claude" / "orchestrate-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session": "s", "tickets": tickets}))

    def worker(self, agent="agent-a", model="claude-sonnet-4-6", last=5):
        return {"agent_id": agent, "worktree": None, "model": model, "last_report": iso(last), "touch_map": []}

    def route(self, lead="live", cap=2, now=60):
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

    def test_lead_none_with_nothing_to_route_prints_only_the_summary(self):
        self.serve([issue(1, labels=["lead"], comments=[])])
        self.state({})
        self.assertEqual(self.route(lead="none", cap=3), (0, ["counted: 0 of 3"]))

    def test_counted_is_the_claimed_tickets_that_hold_a_slot(self):
        self.serve([issue(1, claimed=True), issue(2, claimed=True), issue(3)])
        self.state({})
        self.assertEqual(self.route(), (0, ["counted: 2 of 2"]))

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

    def test_the_prompt_writes_the_marker_the_script_reads(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("lead_route", str(ROOT / "scripts" / "lead-route"))
        spec = importlib.util.spec_from_loader("lead_route", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertIn(mod.STOP_MARKER, SKILL.read_text())


if __name__ == "__main__":
    unittest.main()
