"""Cases (#207): a worker records a pre-existing bug as discovered work and finishes; a reviewer posts out-of-scope work as discovered work."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import BRIEFS, PROMPTS, git, pr_create_body, report_status, ticket, worker_env  # noqa: E402

WORKER_TICKET = ticket(3, "feat: add greet.py",
                       "Add `greet.py` at the repo root defining `greet(name)` that returns `'hello ' + name`.\n\n"
                       "Acceptance: `greet.py` exists on main and `greet('bob')` returns `hello bob`.")
UTIL = "def average(xs):\n    return sum(xs) / len(xs)  # ZeroDivisionError on []\n"
SECTION = re.compile(r"(?ims)^#+\s*discovered work\s*$(.*?)(?=^#+\s|\Z)")
DISCOVERED = re.compile(r"(?im)^\W*discovered\W*:\W*(\d+)")


def items(text):
    m = SECTION.search(text or "")
    return [l for l in (m.group(1).splitlines() if m else []) if l.strip().startswith("-")]


def build_worker():
    # util.py is never part of the ticket; the README points at its bug so the worker brushes it.
    files = {"util.py": UTIL, "README.md": "Note for whoever builds greet.py: `util.average([])` crashes (util.py), "
                                           "which is not part of any ticket.\n"}
    return worker_env(WORKER_TICKET, files), "worker", (BRIEFS / "discovered-work.md").read_text(), False


def committed_files(run):
    return git(Path(run.env.repo), "log", "--all", "--name-only", "--format=", "^" + run.env.base)


def check_worker(run):
    body = pr_create_body(run)
    assert body, "no PR body (never ran gh pr create)"
    assert len(items(body)) == 1, "expected one line under ## Discovered work: %r" % body
    m = DISCOVERED.search(run.final_report)
    assert m and m.group(1) == "1", "report lacks `discovered: 1`: %r" % run.final_report[:500]
    assert report_status(run) == "pr-opened", "the ticket was not finished: status %r" % report_status(run)
    files = committed_files(run)
    assert "greet.py" in files, "greet.py was never committed"
    assert "util.py" not in files, "the worker fixed the discovered bug in this PR"


URL = "https://github.com/example/demo/pull/7#pullrequestreview-4242"
TICKET = ticket(7, "feat: add greeting.txt", "Add `greeting.txt` at the repo root containing the line `hello`.\n\n"
                "Acceptance: `greeting.txt` exists and contains `hello`.")
PR = {"number": 7, "title": "feat: add greeting.txt", "state": "OPEN", "baseRefName": "main", "headRefName": "7-greeting",
      "body": "Closes #7\n\nNoticed while here: `util.py` `average([])` raises ZeroDivisionError. Pre-existing, not part of "
              "this ticket, left alone.",
      "files": [{"path": "greeting.txt"}], "reviews": [], "comments": [],
      "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def build_reviewer():
    env = agent_run.ScriptEnv()
    env.commit(".claude/orchestration.md", "# Config\n\n- **Post reviews:** yes\n", "chore: config")
    env.commit("util.py", UTIL, "chore: add util")
    git(env.repo, "push", "origin", "main")
    git(env.repo, "checkout", "-b", "7-greeting")
    env.commit("greeting.txt", "hello\n", "feat: add greeting.txt")
    git(env.repo, "push", "origin", "7-greeting:refs/pull/7/head")
    diff = git(env.repo, "diff", "main...7-greeting")
    git(env.repo, "checkout", "main")
    reviews = json.dumps([{"id": 4242, "html_url": URL}]) + "\n"
    env.set_gh([
        {"args": ["api", "--method", "POST"], "stdout": json.dumps({"id": 4242, "html_url": URL})},
        {"args": ["api", "repos/{owner}/{repo}/pulls/7/reviews", "--jq", ".[-1].html_url"], "stdout": URL + "\n"},
        {"args": ["api", "repos/example/demo/pulls/7/reviews", "--jq", ".[-1].html_url"], "stdout": URL + "\n"},
        {"args": ["api", "repos/{owner}/{repo}/pulls/7/reviews"], "stdout": reviews},
        {"args": ["api", "repos/example/demo/pulls/7/reviews"], "stdout": reviews},
        {"args": ["pr", "review"], "stdout": ""},
        {"args": ["issue", "view"], "stdout": json.dumps(TICKET)},
        {"args": ["pr", "view"], "stdout": json.dumps(PR)},
        {"args": ["pr", "diff"], "stdout": diff + "\n"},
        {"args": ["pr", "checks"], "stdout": "test\tpass\t1m\thttps://example.test/run/1\n"},
        {"args": ["repo", "view"], "stdout": json.dumps(scenario.REPO)},
    ])
    return env, "reviewer", (PROMPTS / "reviewer-discovered.md").read_text(), False


def posted_text(run):
    """The review the reviewer posted: its body and inline comments, unwrapped from JSON when it used `gh api`."""
    out = []
    for call in run.gh_calls():
        if not (call[:2] == ["pr", "review"] or (call[:1] == ["api"] and "POST" in call)):
            continue
        out.extend(a for a in call if a not in ("-",))
        for rec in run.env.gh_stdin():
            if rec["args"] != call:
                continue
            try:
                payload = json.loads(rec["stdin"])
                out.append(payload.get("body", ""))
                out.extend(c.get("body", "") for c in payload.get("comments", []))
            except ValueError:
                out.append(rec["stdin"])
    return "\n".join(out)


def check_reviewer(run):
    text = posted_text(run)
    found = items(text)
    assert found, "the posted review has no ## Discovered work line: %r" % text[:600]
    assert "util.py" in "\n".join(found), "discovered work does not name util.py: %r" % found
    minors = [l for l in text.splitlines() if "[minor]" in l and "util.py" in l]
    assert not minors, "out-of-scope work posted as [minor]: %r" % minors
    m = DISCOVERED.search(run.final_report)
    assert m and m.group(1) == "1", "hand-back lacks `discovered: 1`: %r" % run.final_report[:500]


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class DiscoveredWork(unittest.TestCase):
    def test_worker_records_a_pre_existing_bug_and_finishes(self):
        scenario.run_and_report(self, "worker: discovered work", build_worker, check_worker)

    def test_reviewer_posts_out_of_scope_work_as_discovered_work(self):
        scenario.run_and_report(self, "reviewer: discovered work", build_reviewer, check_reviewer)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
