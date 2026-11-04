"""Case 1 (#59): the worker writes the PR body with the `:pr` skill, loaded before `gh pr create`."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import BRIEFS, pr_create_body, ticket, worker_env  # noqa: E402

MARKER = "STUB-PR-BODY-MARKER"
TICKET = ticket(1, "feat: add hello.txt", "Add a file `hello.txt` at the repo root containing the line `hello`.\n\n"
                "Acceptance: `hello.txt` exists on main.")


def build(with_stub):
    def make():
        return worker_env(TICKET), "worker", (BRIEFS / "pr-body.md").read_text(), with_stub
    return make


def first_index(run, pred):
    for i, (name, inp) in enumerate(run.tool_calls()):
        if pred(name, inp):
            return i
    return None


def creates_pr(name, inp):
    return name == "Bash" and "gh pr create" in inp.get("command", "")


def no_attribution(body):
    low = body.lower()
    assert "generated with" not in low and "co-authored-by" not in low, "PR body has an attribution line: %r" % body


def check_stub(run):
    skill = first_index(run, lambda n, i: n == "Skill" and str(i.get("skill", "")).endswith(":pr"))
    create = first_index(run, creates_pr)
    assert create is not None, "never ran gh pr create"
    assert skill is not None, "never called the :pr skill"
    assert skill < create, "the :pr skill was called after gh pr create"
    body = pr_create_body(run)
    assert body and MARKER in body, "PR body lacks the stub's marker: %r" % body
    no_attribution(body)


def check_plain(run):
    assert first_index(run, creates_pr) is not None, "never ran gh pr create"
    body = pr_create_body(run)
    assert body and body.strip(), "PR body is empty"
    assert "Closes #1" in body, "PR body lacks the closing reference: %r" % body
    assert MARKER not in body, "a plain body carries the stub's marker"
    no_attribution(body)


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class PrBody(unittest.TestCase):
    def test_body_written_with_the_pr_skill(self):
        scenario.run_and_report(self, "worker: PR body via :pr skill", build(True), check_stub)

    def test_plain_body_without_the_skill(self):
        scenario.run_and_report(self, "worker: plain PR body", build(False), check_plain)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
