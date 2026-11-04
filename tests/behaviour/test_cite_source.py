"""Case 2 (#65): a ticket that needs a current price makes the worker fetch it and cite the URL in Evidence."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import BRIEFS, pr_create_body, ticket, worker_env  # noqa: E402

TICKET = ticket(2, "feat: record the current Copilot Pro price",
                "Add `PRICE.md` at the repo root with the current monthly price in USD of the GitHub Copilot Pro plan, "
                "as one line, e.g. `Copilot Pro: $<price>/month`.\n\nAcceptance: `PRICE.md` exists on main with the price.")


def build():
    return worker_env(TICKET), "worker", (BRIEFS / "cite-source.md").read_text(), False, "Bash,WebFetch,WebSearch"  # no stub: its body has no room for Evidence


def check(run):
    fetched = [n for n, _ in run.tool_calls() if n in ("WebFetch", "WebSearch")]
    assert fetched, "never called WebFetch or WebSearch"
    body = pr_create_body(run)
    assert body, "no PR body (never ran gh pr create)"
    evidence = re.search(r"(?is)evidence.*", body)
    urls = re.findall(r"https?://[^\s)>\]]+", evidence.group(0)) if evidence else []
    assert urls, "no URL under Evidence in the PR body: %r" % body
    # The cited URL must be one the worker fetched or searched up, not one from memory.
    seen = " ".join(str(i) for n, i in run.tool_calls() if n == "WebFetch")
    seen += " ".join(json.dumps(e) for e in run.events if e.get("type") == "user" and "WebSearch" in json.dumps(e))
    assert any(u.rstrip("/.,") in seen for u in urls), "cited URLs %r are in no WebFetch input or WebSearch result" % urls


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class CiteSource(unittest.TestCase):
    def test_worker_cites_a_source_for_an_external_fact(self):
        scenario.run_and_report(self, "worker: cites a source", build, check)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
