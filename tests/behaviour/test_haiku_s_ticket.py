"""Case 6 (#88): an S ticket is routed to a haiku worker.

Runs the orchestrator's own skill (`/orchestrate:run <ticket>`) in the main session on a repo with one
`size:S` ticket, and reads the dispatch of `orchestrate:worker` from the stream: the Agent call's `model`.
The run ends at the dispatch (the budget cap, or the end of the pass); what the worker does is not the
case's business.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import REPO, ScriptEnv, git, ticket  # noqa: E402

TICKET = ticket(7, "feat: add greeting.txt", "Add `greeting.txt` at the repo root containing the line `hello`.\n\n"
                "Touch map: greeting.txt (one new file).", labels=("ready-for-agent", "size:S"))

CONFIG = """# Orchestration config

## Backlog

- **Tracker docs:** `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`. Takeable tickets are open, `ready-for-agent`, unassigned and unblocked. Claim = assign to the user.
- **Domain docs:** `docs/agents/domain.md`.

## Slots

- **Slots:** 1.
- **Exclusive holds:** none

## Verify

`test -f greeting.txt`

## Shared files

- **Shared files:** none
- **Generated:** none
- **Orchestrator-owned edits:** none

## PRs and merging

- **Branch:** `<number>-<slug>`
- **Commits:** Conventional Commits.
- **PR title:** Conventional Commits. **Body:** "Closes #<n>"
- **Method:** squash
- **Orchestrator merges:** no.

## Gates

- **Start gates:** none
- **Merge gates:** none

## Human-only steps

Merging release PRs; repo settings.

## Environment

None.

## Models

| Role | Model | Effort |
|---|---|---|
| Orchestrator | the session's model | |
| Worker | sonnet; haiku for S tickets (at most 3 files, the **Tiers** file limit) | medium |
| Scout | haiku | low |
| Reviewer | S haiku · M sonnet · L sonnet (high effort) | medium |

## Review

- **Instruction files:** none
- **Risk paths:** none
- **Tiers:** S is at most 50 changed lines in at most 3 files, none on a risk path; L touches a risk path or changes more than 400 lines; M is everything else. A `size:S|M|L` label on the ticket overrides.
- **Repo-specific checks:** none
- **Post reviews:** yes.
"""

DOCS = {
    "docs/agents/issue-tracker.md": "# Issue tracker\n\nGitHub Issues via `gh`. List takeable tickets with `gh issue list --label ready-for-agent "
                                    "--state open --json number,title,labels,assignees`; read one with `gh issue view <n> --json "
                                    "number,title,body,labels,assignees,comments`; claim with `gh issue edit <n> --add-assignee @me`.\n",
    "docs/agents/triage-labels.md": "# Triage labels\n\n`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.\n",
    "docs/agents/domain.md": "# Domain docs\n\nNo glossary or ADRs yet.\n",
}


def build():
    env = ScriptEnv()
    env.commit(".claude/orchestration.md", CONFIG, "chore: config")
    for path, text in DOCS.items():
        env.commit(path, text, "docs: " + path)
    git(env.repo, "push", "origin", "main")
    listed = [{"number": 7, "title": TICKET["title"], "labels": TICKET["labels"], "assignees": []}]
    env.set_gh([
        {"args": ["issue", "list"], "stdout": json.dumps(listed)},
        {"args": ["issue", "view"], "stdout": json.dumps(TICKET)},
        {"args": ["issue", "edit"], "stdout": ""},
        {"args": ["pr", "list"], "stdout": "[]"},
        {"args": ["pr", "create"], "stdout": "https://github.com/example/demo/pull/107\n"},
        {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
        {"args": ["api", "user"], "stdout": json.dumps({"login": "demo-user"}) + "\n"},
    ])
    # Preflight, before any paid run: the main session has no guard, so the fake gh must be what `gh` resolves to
    # in the child env, or `issue edit` would reach the real GitHub.
    found = shutil.which("gh", path=agent_run.child_env(env)["PATH"])
    if found != str(env.bin / "gh"):
        env.close()
        raise RuntimeError("gh resolves to %r, not the fake in %s" % (found, env.bin))
    return env, None, "/orchestrate:run 7", False, "Bash,Read,Grep,Glob,Agent,Skill"


def worker_dispatches(run):
    return [i for n, i in run.tool_calls() if n in ("Agent", "Task") and str(i.get("subagent_type", "")).endswith("worker")]


def check(run):
    dispatched = worker_dispatches(run)
    assert dispatched, "never dispatched orchestrate:worker"
    model = str(dispatched[0].get("model", ""))
    assert "haiku" in model, "S ticket dispatched with model %r" % model


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class HaikuSTicket(unittest.TestCase):
    def test_s_ticket_goes_to_a_haiku_worker(self):
        scenario.run_and_report(self, "orchestrator: S ticket -> haiku", build, check)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
