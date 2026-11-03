"""PreToolUse guard for the orchestrate plugin's agents.

Claude Code runs this before every Bash call while the plugin is enabled. It
acts only when the call comes from one of this plugin's agents (the hook input
names it in `agent_type`) and refuses the commands that agent's role must
leave to someone else. It is a guardrail against mistakes, not a sandbox: it
reads the command text, so a determined command can get past it.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import json
import re
import sys

PLUGIN = "orchestrate"

# git subcommands that change the repository, the index or the working tree.
GIT_WRITES = (
    "add|am|apply|branch|checkout|cherry-pick|clean|commit|merge|mv|pull|push|"
    "rebase|reset|restore|revert|rm|stash|switch|tag|worktree\\s+(?:add|remove|move|prune)"
)
# Allow git's global options (-C dir, -c key=val, --no-pager) before the subcommand.
GIT_PREFIX = r"\bgit\s+(?:(?:-[Cc]\s+\S+|--\S+)\s+)*"

GH_WRITE = re.compile(
    r"\bgh\s+(?:pr|issue|release|repo|label|workflow|run|secret|variable|project)\s+"
    r"(?:create|edit|merge|close|reopen|comment|review|ready|delete|lock|unlock|transfer|"
    r"pin|unpin|develop|rerun|cancel|enable|disable|set|archive|rename|fork|sync|"
    r"item-add|item-edit|item-delete)\b"
)
GH_API_WRITE = re.compile(
    r"\bgh\s+api\b.*(?:(?:-X|--method)\s*(?:POST|PATCH|PUT|DELETE)\b|\s(?:-f|-F|--field|--raw-field|--input)\b)",
    re.IGNORECASE,
)
FS_WRITE = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:rm|rmdir|mv|cp|mkdir|touch|chmod|chown|ln|tee|truncate|dd|make|"
    r"(?:npm|pnpm|yarn|bun|pip3?|brew|cargo|go)\s+(?:install|add|remove|uninstall|get|i)\b)\b"
    r"|\bsed\s+(?:-\w*i|--in-place)|\bperl\s+-\w*i"
)
# A redirect into a file: `>` or `>>` not part of `2>&1`, `&>` or `-> ` and not to /dev/null.
REDIRECT = re.compile(r"(?<![0-9&\-=>])>>?(?!>|\s*(?:&|/dev/null))")

FORCE_PUSH = re.compile(GIT_PREFIX + r"push\b.*(?:\s-f\b|--force|\s\+\S)")
PUSH_DEFAULT = re.compile(GIT_PREFIX + r"push\b.*\s(?:\S+:)?(?:main|master)\s*$")
GH_MERGE = re.compile(r"\bgh\s+pr\s+merge\b")


def strip_quotes(cmd: str) -> str:
    """Blank out quoted text so patterns inside search strings don't match."""
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", cmd)


def git_write(subcommands: str) -> re.Pattern:
    return re.compile(GIT_PREFIX + r"(?:" + subcommands + r")\b")


READ_ONLY = [
    (git_write(GIT_WRITES), "changes git state"),
    (GH_WRITE, "writes to the git host"),
    (GH_API_WRITE, "writes through the GitHub API"),
    (FS_WRITE, "writes files or runs a build"),
    (REDIRECT, "redirects output into a file"),
]

RULES = {
    "scout": READ_ONLY,
    "reviewer": READ_ONLY,
    "mechanic": [
        (git_write(GIT_WRITES), "changes git state (the worker stages and commits)"),
        (GH_WRITE, "writes to the git host"),
        (GH_API_WRITE, "writes through the GitHub API"),
    ],
    "worker": [
        (GH_MERGE, "merges a PR (merging belongs to the orchestrator)"),
        (FORCE_PUSH, "force-pushes"),
        (PUSH_DEFAULT, "pushes to the default branch"),
    ],
}

NEXT_STEP = {
    "scout": "Report what you found and let your caller act on it.",
    "reviewer": "Read code with `gh pr diff` or `git show <ref>:<path>` and put the change in your findings.",
    "mechanic": "Leave staging and committing to the worker; report the files you changed.",
    "worker": "Push your own branch normally and report the PR; the orchestrator merges.",
}


def role_of(agent_type: str) -> str | None:
    plugin, _, role = agent_type.partition(":")
    if plugin == PLUGIN and role in RULES:
        return role
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    role = role_of(data.get("agent_type") or "")
    command = (data.get("tool_input") or {}).get("command") or ""
    if role is None or not command:
        return
    text = strip_quotes(command)
    for pattern, what in RULES[role]:
        if pattern.search(text):
            reason = f"orchestrate guard: the {role} role can't run a command that {what}. {NEXT_STEP[role]}"
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                },
                sys.stdout,
            )
            return


if __name__ == "__main__":
    main()
