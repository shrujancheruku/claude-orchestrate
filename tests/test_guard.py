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
    # an orchestrate role the guard has no rules for is held to the read-only rules
    ("orchestrate:mechanic", "git push --force", "deny"),
    ("orchestrate:mechanic", "git commit -m x", "deny"),
    ("orchestrate:mechanic", "gh pr merge 12", "deny"),
    ("orchestrate:mechanic", "touch x", "deny"),
    ("orchestrate:", "touch x", "deny"),
    ("orchestrate:mechanic", "git log", "allow"),
    ("orchestrate:mechanic", "gh pr view 12", "allow"),
    ("other-plugin:worker", "git push --force", "allow"),
    ("", "git push --force", "allow"),
    (" orchestrate:worker", "git push --force", "deny"),
    ("ORCHESTRATE:worker", "git push --force", "deny"),
    ("orchestrate:Worker", "touch x", "deny"),  # role is exact: unknown, so read-only
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
    # Reading the posted review back (a GET) is how a reviewer finds the review URL.
    ("orchestrate:reviewer", "gh api repos/o/r/pulls/5/reviews --jq '.[-1].html_url'", "allow"),
    # Targeted run: a detached scratch checkout, then one narrow test command in it.
    ("orchestrate:reviewer", "git worktree add --detach /tmp/scratch/pr-5 FETCH_HEAD && cd /tmp/scratch/pr-5 && python3 -m unittest tests.test_guard", "allow"),
    ("orchestrate:reviewer", "cd /tmp/scratch/pr-5 && npm install", "deny"),
    ("orchestrate:reviewer", "cd /tmp/scratch/pr-5 && make", "deny"),
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
            "git branch --show-current",
            "git -C /repo fetch origin pull/71/head",
            'grep -rn "git branch new" docs',
            "gh pr diff 103 | grep 'git fetch origin x:y'",
            "git log --grep='git branch x' FETCH_HEAD",
            "git fetch https://x/y.git pull/71/head",
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
            "/usr/bin/git push origin HEAD",
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
            "git fetch origin 'pull/1/head:main'",
            'git fetch origin "+refs/pull/71/head:refs/heads/x"',
            "git fetch origin pull/$N/head",
            "git fetch origin `echo main:main`",
            "git -c core.hooksPath=/x fetch origin pull/71/head",
            "git --git-dir=/x/.git fetch origin pull/71/head",
            "git update-ref -d x",
            "git worktree add --detach /tmp/x HEAD; git push",
            "git -c core.hooksPath=/x worktree add --detach /tmp/x",
            "git --git-dir=/x worktree add --detach /tmp/x",
            "git --work-tree=/x worktree add --detach /tmp/x",
            "git --exec-path=/x worktree add --detach /tmp/x",
            "git worktree add --detach /tmp/$X FETCH_HEAD",
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
    ("orchestrate:worker", "git push origin HEAD:refs/heads/main", "deny"),
    ("orchestrate:worker", "git push origin refs/heads/master", "deny"),
    ("orchestrate:worker", "git push origin api/1-x:refs/heads/master", "deny"),
    ("orchestrate:worker", "git push origin HEAD:refs/heads/main-fix", "allow"),
    ("orchestrate:worker", "git push origin HEAD:refs/heads/api/main", "allow"),
    ("orchestrate:scout", "git fetch origin pull/1/head:pr-1", "deny"),
    ("orchestrate:scout", "git update-ref refs/heads/x HEAD", "deny"),
    ("orchestrate:scout", "git symbolic-ref HEAD refs/heads/x", "deny"),
    ("orchestrate:scout", "git config user.name x", "deny"),
    ("orchestrate:scout", "git -C /r remote add o http://x", "deny"),
    ("orchestrate:scout", "git gc", "deny"),
    ("orchestrate:scout", "git init /tmp/x", "deny"),
    ("orchestrate:scout", "git clone http://x /tmp/x", "deny"),
    ("orchestrate:scout", "git update-index --assume-unchanged f", "deny"),
    ("orchestrate:scout", "git submodule update --init", "deny"),
    ("orchestrate:scout", "git submodule status", "allow"),
    ("orchestrate:scout", "git reflog expire --all", "deny"),
    ("orchestrate:scout", "git reflog", "allow"),
    ("orchestrate:reviewer", "git config user.name x", "deny"),
    ("orchestrate:reviewer", "git remote add o http://x", "deny"),
    ("orchestrate:reviewer", "git gc", "deny"),
    ("orchestrate:reviewer", "git init x", "deny"),
    ("orchestrate:reviewer", "git clone http://x /tmp/x", "deny"),
    ("orchestrate:reviewer", "git update-index --refresh", "deny"),
    ("orchestrate:reviewer", "git submodule update", "deny"),
    ("orchestrate:reviewer", "git reflog delete HEAD@{0}", "deny"),
    ("orchestrate:reviewer", "git reflog show", "allow"),
]

# Both read-only roles must refuse every one of these (#83, #84, #85, #104, #105).
HIDDEN_WRITES = [
    "gh api repos/o/r/issues \\\n  -f title=x",
    "gh api repos/o/r/issues \\\n --method POST",
    "gh api repos/o/r/issues \\\n-X POST",
    "gh pr \\\n merge 12",
    "gh pr \\\n review 12 --approve",
    'gh api repos/o/r/issues -X "POST"',
    "gh api repos/o/r/issues -X 'POST'",
    'gh api repos/o/r/issues --method "POST"',
    'gh api repos/o/r/issues --method="POST"',
    'gh api repos/o/r/issues -X"POST"',
    "gh api repos/o/r/issues -ftitle=x",
    "gh api repos/o/r/issues -Ftitle=x",
    "gh api repos/o/r/issues -iXPOST",
    "gh api repos/o/r/issues --raw-field title=x",
    "gh api repos/o/r/issues --input body.json",
    '"gh" pr merge 12',
    'gh p"r" merge 12',
    "gh pr m\\erge 12",
    "gh \\pr merge 12",
    'gh pr"merge" 12',
    "gh $(echo pr) merge 12",
    "gh pr -R o/r merge 12",
    "gh --repo o/r pr merge 12",
    "git --git-dir /x push",
    "git --work-tree /x commit -m x",
    "git --git-dir /x --work-tree /y push",
    "git --namespace x push",
    "git --exec-path /x push",
    "git --super-prefix x push",
    "git --git-dir /x branch new",
    "git -C /x -c a=b push",
    "echo \\'; git push; echo \\'",
    "echo \\'; git branch new; echo \\'",
    'echo \\"; git branch new; echo \\" x"',
    "$x pr merge 12",
    "gh $x merge 12",
    "gh pr $verb 12",
    "git $sub origin x",
    "${GH} pr merge 12",
    "`echo gh` pr merge 12",
    "env git push",
    "xargs git push",
    "echo 'unbalanced",
]
CASES += [("orchestrate:" + role, c, "deny") for role in ("scout", "reviewer") for c in HIDDEN_WRITES]
# The worker may do everything but merge, so only the merge forms are refused for it.
CASES += [
    ("orchestrate:worker", c, "deny")
    for c in (
        "gh pr \\\n merge 12",
        '"gh" pr merge 12',
        'gh p"r" merge 12',
        "gh pr m\\erge 12",
        "gh \\pr merge 12",
        'gh pr"merge" 12',
        "gh $(echo pr) merge 12",
        "gh pr -R o/r merge 12",
        "git --git-dir /x push -f",
        "git --git-dir /x push origin main",
        "git -c a=b push --force",
    )
]
# Reads that stay allowed for both read-only roles.
CASES += [
    ("orchestrate:" + role, c, "allow")
    for role in ("scout", "reviewer")
    for c in (
        "gh api repos/o/r/issues \\\n  --jq '.[].title'",
        "gh api repos/o/r/issues \\\n  -H 'Accept: x' \\\n  --paginate",
        "gh api -XGET repos/o/r/issues",
        'gh api --method "GET" repos/o/r/issues',
        "gh api --method=get repos/o/r/issues",
        "gh api repos/o/r/pulls/5/reviews --jq '.[-1].html_url'",
        'gh search issues "gh pr merge"',
        "gh pr view 12 --json title # gh pr merge",
        "grep -n 'git push' hooks/guard.py",
        'grep -n "POST" hooks/guard.py',
        "git -C /repo log --oneline",
        "git --no-pager diff",
        "git -c color.ui=never log -1",
        "git log --grep=\"git push\" && git log --author='a b'",
        "echo \\'; git log; echo \\'",
        "gh pr view $N --json title",
        "gh api repos/o/r/issues/$N --jq .title",
        "git log $(git merge-base HEAD origin/main)..HEAD",
        'git diff "$BASE"...HEAD',
        "FOO=$(date) git log -1",
        "gh pr checks 12 2>&1 | tail -5",
    )
]
# Ordinary worker commands stay allowed.
CASES += [
    ("orchestrate:worker", c, "allow")
    for c in (
        "git commit -m \"$(cat <<'EOF'\nfix: it's done\nEOF\n)\"",
        'gh pr create --title x --body "$(cat body.md)"',
        'git push -u origin "$BRANCH"',
        "gh pr view 12 --json state | jq .state",
    )
]
# The guard can't tokenise these, so a guarded role is refused rather than waved through.
UNPARSEABLE = ["echo 'x", 'echo "x', "echo $(date", "echo `date", "echo ${x", "git log >",
               "$(" * 3000 + "ls", "`" * 5 + "ls"]
# A command named wholly by an expansion: refused for the read-only roles, fine for the worker.
CASES += [("orchestrate:" + role, c, "deny") for role in ("scout", "reviewer")
          for c in ("$PYTHON x.py", '"$SCRIPT" a', "$(which python3) x.py")]
CASES += [("orchestrate:worker", c, "allow") for c in ("$PYTHON x.py", '"$SCRIPT" a', "$(which python3) x.py")]
CASES += [("orchestrate:worker", c, "deny") for c in ("$(echo g)it push -f", "g$x push -f", "${X}h pr merge 12")]


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
    ("gh pr review 12 --comment --body-file - <<'EOF'\nbody\nEOF", "allow"),
    ("gh api --method POST repos/o/r/pulls/12/reviews --input - <<'EOF'\n" + COMMENT + "\nEOF", "allow"),
    ("gh api --method POST repos/o/r/pulls/12/reviews -f event=APPROVE", "deny"),
    ("gh api --method POST repos/o/r/pulls/12/reviews --input - -f event=APPROVE <<'EOF'\n" + COMMENT + "\nEOF", "deny"),
    ("gh api -X PUT repos/o/r/pulls/12/reviews --input - <<'EOF'\n" + COMMENT + "\nEOF", "deny"),
    ("gh api --method POST repos/o/r/pulls/12/reviews --input - <<'EOF' > out\n" + COMMENT + "\nEOF", "deny"),
    ("gh api --method POST repos/o/r/pulls/12/reviews --input - <<'EOF' && rm x\n" + COMMENT + "\nEOF", "deny"),
    ("gh api --method POST repos/o/r/pulls/$N/reviews --input - <<'EOF'\n" + COMMENT + "\nEOF", "deny"),
    ("gh api repos/o/r/pulls/12/reviews --jq '.[-1].html_url' && gh pr merge 12", "deny"),
    ('gh pr review 12 --comment --body "$(rm x)"', "deny"),
    ("gh pr review 12 --comment --body 'x' \\\n  --approve", "deny"),
]


class GuardTest(unittest.TestCase):
    def test_cases(self):
        for agent_type, command, expected in CASES:
            with self.subTest(agent=agent_type, command=command):
                self.assertEqual(decide(agent_type, command), expected)

    def run_guard(self, payload):
        out = subprocess.run(
            [sys.executable, "-I", str(GUARD)], input=json.dumps(payload), capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None

    def test_unknown_role_refusal_names_the_role(self):
        reason = self.run_guard({"agent_type": "orchestrate:mechanic", "tool_input": {"command": "git commit -m x"}})
        self.assertIn("mechanic", reason)
        self.assertIn("doesn't know", reason)
        self.assertIn("read-only", reason)
        self.assertIn("changes git state", reason)

    def test_known_role_refusal_is_unchanged(self):
        reason = self.run_guard({"agent_type": "orchestrate:scout", "tool_input": {"command": "git commit -m x"}})
        self.assertTrue(reason.startswith("orchestrate guard: the scout role can't run a command that"))

    def test_non_object_input_never_crashes_and_is_allowed(self):
        for payload in ([1], "x", 3, None, {"agent_type": "orchestrate:scout", "tool_input": "rm x"},
                        {"agent_type": "orchestrate:scout", "tool_input": [1]},
                        {"agent_type": ["orchestrate:scout"], "tool_input": {"command": "rm x"}},
                        {"agent_type": "orchestrate:scout", "tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertIsNone(self.run_guard(payload))

    def test_non_string_command_from_our_agent_is_denied(self):
        for command in (["rm", "x"], 5, {"a": 1}):
            with self.subTest(command=command):
                reason = self.run_guard({"agent_type": "orchestrate:scout", "tool_input": {"command": command}})
                self.assertIn("couldn't parse or check", reason)
        self.assertIsNone(self.run_guard({"agent_type": "other:x", "tool_input": {"command": 5}}))

    def test_reviewer_worktree_inside_main_repo_from_a_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp).resolve() / "main"
            linked = Path(tmp).resolve() / "linked"
            (main / ".git" / "worktrees" / "linked").mkdir(parents=True)
            (main / ".git" / "worktrees" / "linked" / "commondir").write_text("../..\n")
            linked.mkdir()
            (linked / ".git").write_text(f"gitdir: {main}/.git/worktrees/linked\n")
            add = "git worktree add --detach {} FETCH_HEAD"
            for target, expected in ((main / "sub", "deny"), (linked / "sub", "deny"), (Path(tmp).resolve() / "scratch", "allow")):
                with self.subTest(target=target):
                    self.assertEqual(decide("orchestrate:reviewer", add.format(target), cwd=linked), expected)

    def test_reviewer_worktree_add_fails_closed_on_an_unreadable_git_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp).resolve() / "broken"
            broken.mkdir()
            (broken / ".git").write_text("not a gitdir line\n")
            command = f"git worktree add --detach {Path(tmp).resolve()}/scratch FETCH_HEAD"
            self.assertEqual(decide("orchestrate:reviewer", command, cwd=broken), "deny")
            missing = Path(tmp).resolve() / "missing"
            missing.mkdir()
            (missing / ".git").write_text(f"gitdir: {tmp}/nowhere\n")
            self.assertEqual(decide("orchestrate:reviewer", command, cwd=missing), "deny")
            nocommon = Path(tmp).resolve() / "nocommon"
            (nocommon / "x").mkdir(parents=True)
            (Path(tmp) / "g" / "worktrees" / "w").mkdir(parents=True)
            (nocommon / ".git").write_text(f"gitdir: {tmp}/g/worktrees/w\n")
            self.assertEqual(decide("orchestrate:reviewer", command, cwd=nocommon), "deny")

    def test_reviewer_writes_list_is_git_writes_minus_branch(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("guard", GUARD)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertIn("branch", guard.GIT_WRITES)
        self.assertNotIn("branch", guard.REVIEWER_GIT_WRITES)
        for verb in guard.GIT_WRITE_VERBS:
            if verb != "branch":
                self.assertIn(verb, guard.REVIEWER_GIT_WRITES)

    def test_push_to_the_repos_own_default_branch_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / ".git" / "refs" / "remotes" / "origin"
            ref.mkdir(parents=True)
            (ref / "HEAD").write_text("ref: refs/remotes/origin/trunk\n")
            for command in ("git push origin trunk", "git push origin HEAD:refs/heads/trunk"):
                self.assertEqual(decide("orchestrate:worker", command, cwd=tmp), "deny")
            self.assertEqual(decide("orchestrate:worker", "git push origin HEAD:trunk2", cwd=tmp), "allow")
            self.assertEqual(decide("orchestrate:worker", "git push origin main", cwd=tmp), "deny")

    def test_unparseable_commands_are_refused_with_a_reason(self):
        for role in ("scout", "reviewer", "worker"):
            for command in UNPARSEABLE:
                with self.subTest(role=role, command=command):
                    payload = {"tool_input": {"command": command}, "agent_type": "orchestrate:" + role}
                    out = subprocess.run(
                        [sys.executable, "-I", str(GUARD)], input=json.dumps(payload), capture_output=True, text=True
                    ).stdout
                    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
                    self.assertIn("couldn't parse", reason)

    def test_a_crash_inside_a_rule_denies(self):
        import importlib.util
        import io
        from unittest import mock
        spec = importlib.util.spec_from_file_location("guard_crash", GUARD)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)

        def boom(parsed, cwd):
            raise RuntimeError("boom")

        for role in ("scout", "reviewer", "worker"):
            payload = {"tool_input": {"command": "ls"}, "agent_type": "orchestrate:" + role}
            out = io.StringIO()
            with mock.patch.dict(guard.RULES, {role: [boom]}), mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                    mock.patch("sys.stdout", out):
                guard.main()
            self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny", role)

    def test_no_second_quote_blanking_helper(self):
        text = GUARD.read_text()
        for gone in ("strip_quotes", "GH_WORDS", "GIT_PREFIX", "GH_API_WRITE"):
            self.assertNotIn(gone, text)

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
