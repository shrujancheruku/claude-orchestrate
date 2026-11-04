"""Case 4 (#64): a PR one commit behind main is reported as behind, not as up to date."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import PROMPTS, ScriptEnv, git, REPO  # noqa: E402

PR = {
    "number": 5, "title": "feat: add feature.txt", "state": "OPEN", "isDraft": False, "baseRefName": "main",
    "headRefName": "5-feature", "headRefOid": "0" * 40, "files": [{"path": "feature.txt"}],
    # What GitHub says about a branch that is behind: mergeable, clean. Not evidence of being up to date.
    "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
    "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
}
COMPARE = "repos/example/demo/compare/main...5-feature"


def build():
    env = ScriptEnv()
    git(env.repo, "checkout", "-b", "5-feature")
    env.commit("feature.txt", "feature\n", "feat: add feature.txt")
    git(env.repo, "push", "origin", "5-feature")
    git(env.repo, "checkout", "main")
    env.commit("other.txt", "other\n", "feat: add other.txt")  # main moves on: the branch is 1 behind
    git(env.repo, "push", "origin", "main")
    pr = dict(PR, headRefOid=git(env.repo, "rev-parse", "5-feature"))
    env.set_gh([
        {"args": ["api", COMPARE, "--jq", ".behind_by"], "stdout": "1\n"},
        {"args": ["api", COMPARE], "stdout": json.dumps({"ahead_by": 1, "behind_by": 1, "status": "diverged"})},
        {"args": ["pr", "view"], "stdout": json.dumps(pr)},
        {"args": ["pr", "checks"], "stdout": "test\tpass\t1m\thttps://example.test/run/1\n"},
        {"args": ["pr", "diff"], "stdout": "feature.txt\n"},
        {"args": ["pr", "list"], "stdout": json.dumps([pr])},
        {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
    ])
    return env, "scout", (PROMPTS / "scout-behind.md").read_text(), False


def check(run):
    report = run.final_report
    line = re.search(r"(?m)^\W*3\W.*$", report)
    assert line, "no item 3 in the report: %r" % report[:500]
    text = line.group(0).lower()
    assert "pass" not in text and "up to date" not in text, "item 3 calls the branch up to date: %r" % line.group(0)
    assert re.search(r"fail|pending|behind", text), "item 3 neither fails nor says behind: %r" % line.group(0)


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class ScoutBehind(unittest.TestCase):
    def test_scout_reports_a_branch_behind_main_as_behind(self):
        scenario.run_and_report(self, "scout: PR 1 behind main", build, check)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
