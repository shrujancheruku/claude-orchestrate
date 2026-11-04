"""Trivial end-to-end case: a ticket already done on the default branch makes the worker stop."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
from agent_run import BRIEFS, ScriptEnv, git  # noqa: E402

TICKET = {
    "number": 1,
    "title": "feat: add hello.txt",
    "state": "OPEN",
    "body": "Add a file `hello.txt` at the repo root containing the line `hello`.\n\nAcceptance: `hello.txt` exists on main.",
    "comments": [],
}


def build():
    env = ScriptEnv()
    env.commit("hello.txt", "hello\n", "feat: add hello.txt")  # the ticket is already done on main
    git(env.repo, "push", "origin", "main")
    env.set_gh([
        {"args": ["issue", "view"], "stdout": json.dumps(TICKET)},
        {"args": ["issue", "list"], "stdout": "[]"},
        {"args": ["pr", "list"], "stdout": "[]"},
        {"args": ["repo", "view"], "stdout": json.dumps({"defaultBranchRef": {"name": "main"}, "nameWithOwner": "example/demo"})},
    ])
    return env, "worker", (BRIEFS / "already-done.md").read_text(), True


def check(run):
    report = run.final_report
    # Tolerates markdown around the field: `**status:** stopped`, `status: `stopped``.
    assert re.search(r"(?im)^\W*status\W*:\W*stopped", report), "report lacks 'status: stopped'"
    made = [c for c in run.gh_calls() if c[:2] == ["pr", "create"]]
    assert not made, "opened a PR: %r" % made


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class AlreadyDone(unittest.TestCase):
    def test_worker_stops_when_ticket_is_done(self):
        v, passes, cost, failures = agent_run.run_case("worker: ticket already done", build, check)
        if failures:
            print("\nworker: ticket already done: %s, failing runs:\n%s" % (v, "\n".join(failures)))
        if v == "fail":
            self.fail("%d/%d passed\n%s" % (passes, agent_run.RUNS, "\n".join(failures)))


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
