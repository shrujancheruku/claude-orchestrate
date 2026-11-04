"""Case 5 (#51, #66): the reviewer posts one review in a shape the guard accepts and hands back its URL."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import PROMPTS, REPO, ScriptEnv, git, ticket  # noqa: E402

URL = "https://github.com/example/demo/pull/7#pullrequestreview-4242"
TICKET = ticket(7, "feat: add greeting.txt", "Add `greeting.txt` at the repo root containing the line `hello`.\n\n"
                "Acceptance: `greeting.txt` exists and contains `hello`.")
PR = {"number": 7, "title": "feat: add greeting.txt", "state": "OPEN", "baseRefName": "main", "headRefName": "7-greeting",
      "body": "Closes #7", "files": [{"path": "greeting.txt"}], "reviews": [], "comments": [],
      "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def build():
    env = ScriptEnv()
    # The reviewer's guard reads this line before it lets a review post through.
    env.commit(".claude/orchestration.md", "# Config\n\n- **Post reviews:** yes\n", "chore: config")
    git(env.repo, "push", "origin", "main")
    git(env.repo, "checkout", "-b", "7-greeting")
    env.commit("greeting.txt", "hello\n", "feat: add greeting.txt")
    git(env.repo, "push", "origin", "7-greeting:refs/pull/7/head")
    diff = git(env.repo, "diff", "main...7-greeting")
    git(env.repo, "checkout", "main")
    env.set_gh([
        {"args": ["api", "--method", "POST"], "stdout": json.dumps({"id": 4242, "html_url": URL})},
        {"args": ["api", "repos/example/demo/pulls/7/reviews"], "stdout": json.dumps([{"id": 4242, "html_url": URL}]) + "\n"},
        {"args": ["pr", "review"], "stdout": ""},
        {"args": ["issue", "view"], "stdout": json.dumps(TICKET)},
        {"args": ["pr", "view"], "stdout": json.dumps(PR)},
        {"args": ["pr", "diff"], "stdout": diff + "\n"},
        {"args": ["pr", "checks"], "stdout": "test\tpass\t1m\thttps://example.test/run/1\n"},
        {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
    ])
    return env, "reviewer", (PROMPTS / "reviewer-post.md").read_text(), False


def posts(run):
    return [c for c in run.gh_calls()
            if c[:2] == ["pr", "review"] or (c[:1] == ["api"] and "POST" in c and any("/reviews" in a for a in c))]


def check(run):
    made = posts(run)
    assert len(made) == 1, "expected exactly one review post, got %d: %r" % (len(made), made)
    post = made[0]
    assert "--approve" not in post and "--request-changes" not in post, "posted a non-comment review: %r" % post
    if post[0] == "api":
        payloads = [r for r in run.env.gh_stdin() if r["args"] == post]
        assert payloads and json.loads(payloads[0]["stdin"]).get("event") == "COMMENT", "payload is not a COMMENT review"
    assert URL in run.final_report, "hand-back lacks the review URL: %r" % run.final_report[:500]
    assert re.search(r"(?im)^\W*verdict\W*:\W*(approve|changes)", run.final_report), "hand-back lacks a verdict"


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class ReviewerPost(unittest.TestCase):
    def test_reviewer_posts_once_and_returns_the_url(self):
        scenario.run_and_report(self, "reviewer: posts, returns URL", build, check)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
