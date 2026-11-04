"""Table tests for hooks/guard.py. Run: python3 -m unittest discover tests"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "hooks" / "guard.py"


def decide(agent_type, command, cwd=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if cwd is not None:
        payload["cwd"] = str(cwd)
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
    ("orchestrate:scout", "grep -rn 'scripts/merge' . | head", "allow"),
    ("orchestrate:scout", "cat scripts/merge", "allow"),
    ("orchestrate:scout", "ls scripts/merge", "allow"),
    ("orchestrate:scout", "scripts/merge 12", "deny"),
    ("orchestrate:scout", '"${CLAUDE_SKILL_DIR}/../../scripts/merge" 12', "deny"),
    ("orchestrate:reviewer", '"${CLAUDE_SKILL_DIR}/../../scripts/merge" 12 --worktree /x', "deny"),
    ("orchestrate:reviewer", "cd /repo && python3 scripts/merge 12", "deny"),
    ("orchestrate:reviewer", "gh pr diff 12; /p/scripts/merge 12", "deny"),
    ("orchestrate:worker", "'/p/scripts/merge' 12", "deny"),
    ("orchestrate:worker", "bash scripts/merge 12", "deny"),
    ("orchestrate:worker", "sh -c 'scripts/merge 12'", "deny"),
    ("orchestrate:worker", 'bash -c "cd scripts && ./merge 12"', "deny"),
    ("orchestrate:worker", "python3 -I /p/scripts/merge 12", "deny"),
    ("orchestrate:worker", "/usr/bin/python3 /p/scripts/merge 12", "deny"),
    ("orchestrate:worker", "env FOO=1 scripts/merge 12", "deny"),
    ("orchestrate:worker", "FOO=1 scripts/merge 12", "deny"),
    ("orchestrate:worker", "timeout 60 scripts/merge 12", "deny"),
    ("orchestrate:worker", "nohup scripts/merge 12 &", "deny"),
    ("orchestrate:worker", "time scripts/merge 12", "deny"),
    ("orchestrate:worker", "cd scripts && ./merge 12", "deny"),
    ("orchestrate:worker", "cd /p/scripts && python3 ./merge 12", "deny"),
    ("orchestrate:worker", "ls scripts && ./merge-notes", "allow"),
    ("orchestrate:worker", "python3 scripts/merge.py", "allow"),
    ("orchestrate:worker", "grep -n x scripts/merge", "allow"),
    ("orchestrate:worker", "sh -c 'cat scripts/merge'", "allow"),
    (None, '"${CLAUDE_SKILL_DIR}/../../scripts/merge" 12', "allow"),  # the orchestrator's own session
    ("orchestrate:scout", "git -C /repo checkout main", "deny"),
    ("orchestrate:scout", "git worktree add ../x", "deny"),
    ("orchestrate:scout", "gh issue comment 5 --body hi", "deny"),
    ("orchestrate:scout", "gh api -X POST repos/o/r/issues", "deny"),
    ("orchestrate:scout", "gh api repos/o/r/pulls/12/reviews --method=POST", "deny"),
    ("orchestrate:scout", "gh api -XPOST repos/o/r/issues", "deny"),
    ("orchestrate:reviewer", "gh api repos/o/r/pulls/12/reviews --method=POST", "deny"),
    ("orchestrate:reviewer", "gh api -XPOST repos/o/r/pulls/12/reviews", "deny"),
    ("orchestrate:scout", 'gh "pr" review 12 --approve', "deny"),
    ("orchestrate:reviewer", "gh 'pr' merge 12", "deny"),
    ("orchestrate:worker", "gh 'pr' merge 12", "deny"),
    ("orchestrate:scout", 'git log --grep="gh pr merge"', "allow"),
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
    # Reviewer git: reads allowed (a PR head, history, branch listing, a detached scratch checkout),
    # every ref or tree write still refused. The scout keeps the stricter list.
    *[
        ("orchestrate:reviewer", c, "allow")
        for c in (
            "git fetch origin pull/71/head",
            "git fetch origin refs/pull/71/head && git log --oneline origin/main..FETCH_HEAD",
            "git fetch --prune origin",
            "git fetch git@github.com:o/r.git pull/71/head",
            "git log -p FETCH_HEAD",
            "git diff origin/main...FETCH_HEAD",
            "git show FETCH_HEAD:hooks/guard.py",
            "git branch",
            "git branch -a",
            "git branch -r",
            "git branch --list 'feat/*'",
            "git branch -vv",
            "git branch -r --contains FETCH_HEAD",
            "git worktree list",
            "git worktree add --detach /tmp/scratch/pr-71 FETCH_HEAD",
            "git worktree add --detach ~/scratch/pr-71",
            "git -C /repo worktree list",
            "git -C /tmp/scratch/pr-71 log --oneline",
        )
    ],
    *[
        ("orchestrate:reviewer", c, "deny")
        for c in (
            "git push origin HEAD",
            "git commit -m x",
            "git branch new-branch",
            "git branch new-branch FETCH_HEAD",
            "git branch -d old",
            "git branch -D old",
            "git branch -m old new",
            "git branch -f main FETCH_HEAD",
            "git branch --list foo -d bar",
            "git branch -a && git branch x",
            "git checkout FETCH_HEAD",
            "git switch -c x",
            "git fetch origin pull/71/head:pr-71",
            "git fetch origin +pull/71/head:refs/heads/pr-71",
            "git fetch origin main:main",
            "git fetch origin pull/71/head && git fetch origin x:y",
            "git update-ref refs/heads/main FETCH_HEAD",
            "git symbolic-ref HEAD refs/heads/x",
            "git worktree add /tmp/scratch/pr-71 FETCH_HEAD",
            "git worktree add -b x /tmp/scratch/pr-71",
            "git worktree add --detach -B x /tmp/scratch/pr-71",
            "git worktree add --detach ../pr-71 FETCH_HEAD",
            "git worktree add --detach pr-71",
            "git worktree add --detach '/tmp/scratch/pr-71'",
            "git worktree add --detach $HOME/pr-71",
            "git worktree add --detach /tmp/pr-71 FETCH_HEAD extra",
            "git -C /repo worktree add --detach /tmp/pr-71",
            f"git worktree add --detach {GUARD.parent.parent}/scratch/pr-71",
            f"git worktree add --detach {GUARD.parent.parent} FETCH_HEAD",
            "git worktree remove /tmp/scratch/pr-71",
            "git worktree prune",
            "git fetch origin pull/71/head > out.txt",  # a redirect stays refused, scratch dir or not
            "git log > /tmp/scratch/log.txt",
        )
    ],
    ("orchestrate:scout", "git branch new-branch", "deny"),
    ("orchestrate:scout", "git branch -a", "deny"),
    ("orchestrate:scout", "git worktree add --detach /tmp/scratch/pr-71", "deny"),
    ("orchestrate:worker", "git add src/a.ts && git commit -m 'fix: a'", "allow"),
    ("orchestrate:worker", "git push -u origin api/162-reasons", "allow"),
    ("orchestrate:worker", "gh pr create --title x --body y", "allow"),
    ("orchestrate:worker", "make lint test > /tmp/out.txt", "allow"),
    ("orchestrate:worker", 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify" "make lint" --then "make test"', "allow"),
    ("orchestrate:worker", "python3 /plugins/orchestrate/scripts/verify --cwd . 'python3 -m unittest discover tests'", "allow"),
    ("orchestrate:worker", "gh pr merge 12 --squash", "deny"),
    ("orchestrate:worker", "git push --force-with-lease", "deny"),
    ("orchestrate:worker", "git push -f origin x", "deny"),
    ("orchestrate:worker", "git push origin +x", "deny"),
    ("orchestrate:worker", "git push origin main", "deny"),
    ("orchestrate:worker", "git push origin HEAD:main", "deny"),
]


POST = "gh api --method POST repos/{owner}/{repo}/pulls/12/reviews --input - <<'EOF'\n"
COMMENT = '{"event": "COMMENT", "body": "Round 1 — verdict: changes", "comments": []}'

REVIEW_CASES = [
    # (command, expected), run with posting on
    (POST + COMMENT + "\nEOF", "allow"),
    (POST.replace("--method", "-X") + COMMENT + "\nEOF\n", "allow"),
    (POST.replace("'EOF'", '"EOF"') + COMMENT + "\nEOF", "allow"),
    (POST + '{"event": "COMMENT", "body": "rm -rf; git push > x"}\nEOF', "allow"),
    (POST + '{"event": "APPROVE", "body": "lgtm"}\nEOF', "deny"),
    (POST + '{"event": "REQUEST_CHANGES", "body": "x"}\nEOF', "deny"),
    (POST + '{"body": "no event"}\nEOF', "deny"),
    (POST + "not json\nEOF", "deny"),
    (POST + COMMENT, "deny"),  # heredoc never closes
    (POST + COMMENT + "\nEOF\ngh pr merge 12", "deny"),
    (POST.replace("'EOF'", "EOF") + COMMENT + "\nEOF", "deny"),  # unquoted: $(...) would expand
    (POST.replace("pulls/12/reviews", "pulls/12/merge") + COMMENT + "\nEOF", "deny"),
    ("echo ok && " + POST + COMMENT + "\nEOF", "deny"),
    ("cd /tmp/x && " + POST + COMMENT + "\nEOF", "allow"),
    ("cd '/tmp/my dir' && " + POST + COMMENT + "\nEOF", "allow"),
    ("cd /tmp/x && echo hi && " + POST + COMMENT + "\nEOF", "deny"),  # no chaining after cd
    ("cd /tmp/x; " + POST + COMMENT + "\nEOF", "deny"),
    ("cd $(rm -rf x) && " + POST + COMMENT + "\nEOF", "deny"),
    (POST.replace("{owner}/{repo}", "shrujancheruku/claude-orchestrate") + COMMENT + "\nEOF", "allow"),
    ("gh pr review 12 --comment --body 'x'", "allow"),
    ("gh pr review 12 --comment -b 'LGTM $(x)'", "allow"),
    ("gh pr review 12 --body 'x' --comment", "allow"),
    ('gh pr review 12 --comment --body "x"', "allow"),
    ('gh pr review 12 --comment --body "$(rm x)"', "deny"),
    ("gh pr review 12 --comment --body 'x'; rm x", "deny"),
    ("gh pr review 12 --comment --body 'x' && gh pr merge 12", "deny"),
    ("gh pr review 12 --comment --body-file - <<'EOF'\nbody $(x)\nEOF", "allow"),
    ("cd /tmp/x && gh pr review 12 --comment --body-file - <<'EOF'\nbody\nEOF\n", "allow"),
    ("gh pr review 12 --comment --body-file - <<'EOF'\nbody\nEOF\nrm x", "deny"),
    ("gh pr review 12 --comment --body-file - <<EOF\nbody\nEOF", "deny"),
    ("gh pr review 12 --approve --body x", "deny"),
    ("gh pr review 12 --request-changes --body x", "deny"),
    ("gh pr review 12 --approve", "deny"),
    ("gh pr review 12 -a", "deny"),
    ("gh pr review 12 -r --body 'x'", "deny"),
    ("gh pr review 12 --comment --body 'x' --approve", "deny"),
    ("gh pr review 12 --comment -F - <<'EOF'\nbody\nEOF", "allow"),
    ("gh pr review 12 --comment --body-file=- <<'EOF'\nbody\nEOF", "allow"),
    ('cd "$HOME/x" && ' + POST + COMMENT + "\nEOF", "deny"),
    ("cd /tmp/x && " + POST + '{"event": "APPROVE", "body": "x"}\nEOF', "deny"),
    ("gh api repos/o/r/pulls/12/reviews", "allow"),  # a GET
    ("gh api repos/o/r/pulls/12/reviews/5/comments --jq '.[].body'", "allow"),
    ('git log --grep="gh pr review"', "allow"),
    ("gh pr review 12 --comment --approve --body x", "deny"),
    ("gh pr comment 12 --body x", "deny"),
]


class GuardTest(unittest.TestCase):
    def test_cases(self):
        for agent_type, command, expected in CASES:
            with self.subTest(agent=agent_type, command=command):
                self.assertEqual(decide(agent_type, command), expected)

    def test_review_posts(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / ".claude" / "orchestration.md"
            config.parent.mkdir()
            config.write_text("## Review\n\n- **Post reviews:** yes\n")
            subdir = Path(root) / "src"
            subdir.mkdir()
            for command, expected in REVIEW_CASES:
                with self.subTest(command=command):
                    self.assertEqual(decide("orchestrate:reviewer", command, subdir), expected)
            # The same post goes through only for the reviewer.
            self.assertEqual(decide("orchestrate:scout", POST + COMMENT + "\nEOF", root), "deny")

    def test_near_miss_names_the_shape(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / ".claude" / "orchestration.md"
            config.parent.mkdir()
            config.write_text("- **Post reviews:** yes\n")
            for command in ["echo ok && " + POST + COMMENT + "\nEOF", "gh pr review 12 --approve"]:
                payload = {"tool_input": {"command": command}, "agent_type": "orchestrate:reviewer", "cwd": root}
                out = subprocess.run(
                    [sys.executable, "-I", str(GUARD)], input=json.dumps(payload), capture_output=True, text=True
                ).stdout
                reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("pulls/<n>/reviews", reason)
                self.assertIn("--comment --body-file -", reason)

    def test_other_api_write_keeps_generic_message(self):
        payload = {
            "tool_input": {"command": "gh api --method POST repos/o/r/issues/1/comments -f body=x"},
            "agent_type": "orchestrate:reviewer",
        }
        out = subprocess.run(
            [sys.executable, "-I", str(GUARD)], input=json.dumps(payload), capture_output=True, text=True
        ).stdout
        self.assertIn("writes through the GitHub API", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_posting_off(self):
        for post in [POST + COMMENT + "\nEOF", "gh pr review 12 --comment --body 'x'"]:
            self.check_off(post)

    def check_off(self, post):
        for text in ["- **Post reviews:** no\n", "- **Post reviews:** <yes | no>\n", "## Review\n", None]:
            with self.subTest(config=text), tempfile.TemporaryDirectory() as root:
                if text is not None:
                    config = Path(root) / ".claude" / "orchestration.md"
                    config.parent.mkdir()
                    config.write_text(text)
                self.assertEqual(decide("orchestrate:reviewer", post, root), "deny")


if __name__ == "__main__":
    unittest.main()
