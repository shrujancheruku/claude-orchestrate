"""Shared builders for the behavioural cases: a temp repo with a canned ticket behind the fake gh."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
from agent_run import BRIEFS, ScriptEnv, git  # noqa: E402,F401

PROMPTS = Path(__file__).resolve().parents[1] / "fixtures" / "prompts"
STATUS = re.compile(r"(?im)^\W*status\W*:\W*(\S+)")
REPO = {"defaultBranchRef": {"name": "main"}, "nameWithOwner": "example/demo"}


def ticket(number, title, body, labels=()):
    return {"number": number, "title": title, "state": "OPEN", "body": body, "comments": [],
            "labels": [{"name": n} for n in labels]}


def gh_responses(tk):
    """The canned `gh` a worker needs: the ticket, the repo, no other PRs, and a URL back from `pr create`."""
    pr = tk["number"] + 100
    return [
        {"args": ["issue", "view"], "stdout": json.dumps(tk)},
        {"args": ["issue", "list"], "stdout": "[]"},
        {"args": ["pr", "list"], "stdout": "[]"},
        {"args": ["pr", "create"], "stdout": "https://github.com/example/demo/pull/%d\n" % pr},
        {"args": ["pr", "view"], "stdout": json.dumps({"number": pr, "state": "OPEN", "statusCheckRollup": []})},
        {"args": ["pr", "checks"], "stdout": ""},
        {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
    ]


def worker_env(tk, files=None):
    """A repo on main (pushed) holding `files` ({path: content}), the ticket behind the fake gh."""
    env = ScriptEnv()
    for path, content in (files or {}).items():
        env.commit(path, content, "chore: add " + path)
    git(env.repo, "push", "origin", "main")
    env.set_gh(gh_responses(tk))
    return env


def pr_create_body(run):
    """The body of the `gh pr create`, from --body, --body-file (stdin or a file) or None when none was made."""
    for args in run.gh_calls():
        if args[:2] != ["pr", "create"]:
            continue
        for i, a in enumerate(args):
            if a in ("--body", "-b") and i + 1 < len(args):
                return args[i + 1]
            if a in ("--body-file", "-F") and i + 1 < len(args):
                if args[i + 1] == "-":
                    for rec in run.env.gh_stdin():
                        if rec["args"] == args:
                            return rec["stdin"]
                else:
                    f = Path(run.env.repo) / args[i + 1]
                    return f.read_text() if f.exists() else Path(args[i + 1]).read_text()
        return ""
    return None


def report_status(run):
    m = STATUS.search(run.final_report)
    return m.group(1).strip("`*").lower() if m else None


def run_and_report(test, name, build, check):
    """Run a case over agent_run.RUNS runs; print failing runs; fail the test on a `fail` verdict."""
    v, passes, cost, failures = agent_run.run_case(name, build, check)
    if failures:
        print("\n%s: %s, failing runs:\n%s" % (name, v, "\n".join(failures)))
    if v == "fail":
        test.fail("%d/%d passed\n%s" % (passes, agent_run.RUNS, "\n".join(failures)))
