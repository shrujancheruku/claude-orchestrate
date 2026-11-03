"""Table tests for hooks/guard.py. Run: python3 -m unittest discover tests"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "hooks" / "guard.py"


def decide(agent_type, command):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    out = subprocess.run(
        [sys.executable, "-I", str(GUARD)], input=json.dumps(payload), capture_output=True, text=True, check=True
    ).stdout
    return "deny" if out and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny" else "allow"


CASES = [
    # (agent_type, command, expected)
    (None, "git push --force origin main", "allow"),  # main session: never touched
    ("scout", "rm -rf x", "allow"),  # someone else's agent named scout
    ("orchestrate:scout", "git log --oneline -5 && git diff main...HEAD", "allow"),
    ("orchestrate:scout", "git -C ../repo status", "allow"),
    ("orchestrate:scout", "gh pr checks 12 2>&1 | tail -5", "allow"),
    ("orchestrate:scout", "gh run view 1 --log-failed > /dev/null", "allow"),
    ("orchestrate:scout", "gh api repos/o/r/issues/5 --jq .id", "allow"),
    ("orchestrate:scout", "grep -rn 'git push' . | head", "allow"),
    ("orchestrate:scout", "grep -n \"a > b\" file.go", "allow"),
    ("orchestrate:scout", "git fetch origin pull/12/head", "allow"),
    ("orchestrate:scout", "git worktree list", "allow"),
    ("orchestrate:scout", "git commit -m x", "deny"),
    ("orchestrate:scout", "git -C /repo checkout main", "deny"),
    ("orchestrate:scout", "git worktree add ../x", "deny"),
    ("orchestrate:scout", "gh issue comment 5 --body hi", "deny"),
    ("orchestrate:scout", "gh api -X POST repos/o/r/issues", "deny"),
    ("orchestrate:scout", "gh api repos/o/r/issues -f title=x", "deny"),
    ("orchestrate:scout", "echo hi > notes.md", "deny"),
    ("orchestrate:scout", "cat a >> b", "deny"),
    ("orchestrate:scout", "ls; rm notes.md", "deny"),
    ("orchestrate:scout", "sed -i '' s/a/b/ f", "deny"),
    ("orchestrate:scout", "make test", "deny"),
    ("orchestrate:scout", "pnpm install", "deny"),
    ("orchestrate:reviewer", "gh pr diff 12", "allow"),
    ("orchestrate:reviewer", "git show origin/x:README.md", "allow"),
    ("orchestrate:reviewer", "gh pr review 12 --approve", "deny"),
    ("orchestrate:reviewer", "gh pr merge 12", "deny"),
    ("orchestrate:mechanic", "sed -i '' s/a/b/ f.go && go build ./...", "allow"),
    ("orchestrate:mechanic", "mv a.ts b.ts", "allow"),
    ("orchestrate:mechanic", "git mv a.ts b.ts", "deny"),
    ("orchestrate:mechanic", "git add -A", "deny"),
    ("orchestrate:mechanic", "git commit -m x", "deny"),
    ("orchestrate:worker", "git add src/a.ts && git commit -m 'fix: a'", "allow"),
    ("orchestrate:worker", "git push -u origin api/162-reasons", "allow"),
    ("orchestrate:worker", "gh pr create --title x --body y", "allow"),
    ("orchestrate:worker", "make lint test > /tmp/out.txt", "allow"),
    ("orchestrate:worker", "gh pr merge 12 --squash", "deny"),
    ("orchestrate:worker", "git push --force-with-lease", "deny"),
    ("orchestrate:worker", "git push -f origin x", "deny"),
    ("orchestrate:worker", "git push origin +x", "deny"),
    ("orchestrate:worker", "git push origin main", "deny"),
    ("orchestrate:worker", "git push origin HEAD:main", "deny"),
]


class GuardTest(unittest.TestCase):
    def test_cases(self):
        for agent_type, command, expected in CASES:
            with self.subTest(agent=agent_type, command=command):
                self.assertEqual(decide(agent_type, command), expected)


if __name__ == "__main__":
    unittest.main()
