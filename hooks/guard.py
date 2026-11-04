"""PreToolUse guard for the orchestrate plugin's agents.

Claude Code runs this before every Bash call while the plugin is enabled. It
acts only when the call comes from one of this plugin's agents (the hook input
names it in `agent_type`) and refuses the commands that agent's role must
leave to someone else. The one write it lets a read-only role make is a
reviewer's comment review on a PR, and only when the repo's config turns
posting on. It is a guardrail against mistakes, not a sandbox: it reads the
command text, so a determined command can get past it.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN = "orchestrate"

# git subcommands that change the repository, the index or the working tree.
GIT_WRITES = (
    "add|am|apply|branch|checkout|cherry-pick|clean|commit|merge|mv|pull|push|"
    "rebase|reset|restore|revert|rm|stash|switch|tag|worktree\\s+(?:add|remove|move|prune)"
)
# Allow git's global options (-C dir, -c key=val, --no-pager) before the subcommand.
# Not preceded by a word char, `.`, `@`, `:` or `-`, so the end of a remote URL
# (`o/r.git pull/71/head`) isn't read as a `git pull`.
GIT_PREFIX = r"(?<![\w.@:-])git\s+(?:(?:-[Cc]\s+\S+|--\S+)\s+)*"

GH_WRITE = re.compile(
    r"\bgh\s+(?:pr|issue|release|repo|label|workflow|run|secret|variable|project)\s+"
    r"(?:create|edit|merge|close|reopen|comment|review|ready|delete|lock|unlock|transfer|"
    r"pin|unpin|develop|rerun|cancel|enable|disable|set|archive|rename|fork|sync|"
    r"item-add|item-edit|item-delete)\b"
)
GH_API_WRITE = re.compile(
    r"\bgh\s+api\b.*(?:(?:-X|--method)(?:\s*|=)(?:POST|PATCH|PUT|DELETE)\b|\s(?:-f|-F|--field|--raw-field|--input)\b)",
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
# The plugin's merge script, run as a command: at the start of the command or after `;`, `&`, `|`
# or `(`, optionally behind python3 and in quotes (the script path is usually a quoted
# "${CLAUDE_SKILL_DIR}/../../scripts/merge"). Checked on the raw command, because strip_quotes
# would blank that quoted path; anchoring at command position keeps `grep scripts/merge` allowed.
# Wrappers that still run it: VAR=1 assignments, env/nohup/time/exec/command/timeout <n>, an
# interpreter (bash, sh, zsh, python3 [-I]) with its flags, and `sh -c '...'`. A bare `./merge`
# counts after `cd <...>scripts &&`. A renamed or copied script is out of scope (a guardrail).
_WRAP = (r"(?:(?:\w+=\S*|(?:\S*/)?(?:env|nohup|time|exec|command|timeout|bash|sh|zsh|python3?)"
         r"(?:\s+-[-\w=]+)*(?:\s+\d+[smhd]?)?)\s+)*")
_END = r"[\"']?(?=[\s\"';&|)]|$)"
MERGE_SCRIPT = re.compile(
    r"(?:^|[;&|(]|\s-c\s+[\"'])\s*(?:"
    + _WRAP + r"[\"']?[^\s\"']*\bscripts/merge" + _END
    + r"|cd\s+[^\s;&|]*scripts/?[\"']?\s*&&\s*" + _WRAP + r"[\"']?\./merge" + _END
    + r")"
)

# The writes a reviewer may make: a comment review posted through the API (its JSON on a
# heredoc) or through `gh pr review --comment`. The first line must be exactly one of these
# shapes; the payload is checked separately. A heredoc delimiter must be quoted, or the shell
# would expand `$(...)` inside the body, and an inline body must be free of `$`, backticks and
# backslashes for the same reason.
# An optional leading `cd <path> &&` is allowed, a plain cd and nothing chained after it.
CD_PREFIX = r"^\s*(?:cd\s+(?:'[^']*'|\"[^\"$`\\]*\"|[^\s;&|<>$`()\\'\"]+)\s*&&\s*)?"
HEREDOC = r"<<-?\s*(['\"])(\w+)\1\s*$"
REVIEW_POST = re.compile(
    CD_PREFIX + r"gh\s+api\s+(?:-X|--method)\s+POST\s+repos/[^/\s]+/[^/\s]+/pulls/\d+/reviews\s+"
    r"--input\s+-\s*" + HEREDOC
)
_BODY = r"(?:-b|--body)(?:\s+|=)(?:'[^']*'|\"[^\"$`\\]*\")"
_BODY_FILE = r"(?:--body-file|-F)(?:\s+|=)-"
PR_REVIEW_INLINE = re.compile(
    CD_PREFIX + r"gh\s+pr\s+review\s+\d+\s+(?:--comment\s+" + _BODY + r"|" + _BODY + r"\s+--comment)\s*$",
    re.DOTALL,
)
PR_REVIEW_HEREDOC = re.compile(
    CD_PREFIX + r"gh\s+pr\s+review\s+\d+\s+(?:--comment\s+" + _BODY_FILE + r"|" + _BODY_FILE
    + r"\s+--comment)\s*" + HEREDOC
)
# A command aimed at a review post, whether or not it has the right shape.
REVIEW_AIMED = re.compile(r"\bgh\s+api\b.*pulls/\d+/reviews|\bgh\s+pr\s+review\b", re.DOTALL)
SHAPES = (
    "Post exactly one of these (an optional leading `cd <path> &&` is fine, nothing else chained, nothing after the heredoc):\n"
    "gh api --method POST repos/{owner}/{repo}/pulls/<n>/reviews --input - <<'EOF'\n"
    '{"event": "COMMENT", "body": "<body>", "comments": []}\nEOF\n'
    "gh pr review <n> --comment --body-file - <<'EOF'\n<body>\nEOF\n"
    "gh pr review <n> --comment --body '<body, no single quotes>'"
)
# The config line that turns posting on, e.g. `- **Post reviews:** yes`. Leaving `<` out of
# the separator keeps the template's `<yes | no>` placeholder from reading as yes.
POST_FLAG = re.compile(r"^[-*\s]*Post reviews[:*\s]*(yes|no)\b", re.IGNORECASE | re.MULTILINE)
CONFIG = Path(".claude") / "orchestration.md"


GH_WORDS = re.compile(r"\bgh((?:\s+(?:\"[\w-]+\"|'[\w-]+'|[\w-]+)){1,3})")


def strip_quotes(cmd: str) -> str:
    """Blank out quoted text so patterns inside search strings don't match.

    First unquote a plain word where a `gh` subcommand goes (gh "pr" merge), which the
    shell reads as an unquoted one; blanking it would hide the command."""
    cmd = GH_WORDS.sub(lambda m: "gh" + re.sub(r"[\"']", "", m.group(1)), cmd)
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", cmd)


def git_write(subcommands: str) -> re.Pattern:
    return re.compile(GIT_PREFIX + r"(?:" + subcommands + r")\b")


# The reviewer reads a PR head with git, so `branch` (listing) and `worktree add --detach`
# (a scratch checkout) are judged by reviewer_git below instead of being refused outright.
# update-ref and symbolic-ref move refs without any of the other verbs.
REVIEWER_GIT_WRITES = (
    GIT_WRITES.replace("branch|", "").replace("|worktree\\s+(?:add|remove|move|prune)", "")
    + "|update-ref|symbolic-ref|worktree\\s+(?:remove|move|prune|lock|unlock|repair)"
)
GIT_CALL = re.compile(r"(" + GIT_PREFIX + r")(branch|fetch|worktree)\b([^;&|\n)]*)")
BRANCH_LIST = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l", "--show-current"}
BRANCH_REF_ARG = {"--contains", "--merged", "--no-merged", "--points-at"}
WORKTREE_FLAGS = {"--detach", "-q", "--quiet", "--no-checkout"}


def repo_root(cwd: str) -> Path:
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def branch_lists_only(args: list[str]) -> bool:
    """True for `git branch` arguments that only list: listing flags, a ref after
    --contains and the like, and name patterns only after --list."""
    patterns_ok = "--list" in args or "-l" in args
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in BRANCH_REF_ARG:
            skip = True
        elif arg not in BRANCH_LIST and (arg.startswith("-") or not patterns_ok):
            return False
    return True


def reviewer_git(text: str, cwd: str) -> str | None:
    """What a reviewer's `git branch`, `git fetch` or `git worktree` call does wrong, or None.

    Listing branches, fetching into FETCH_HEAD and adding a detached scratch worktree outside
    the repo are the reads a reviewer needs; everything else those verbs do changes state."""
    for m in GIT_CALL.finditer(text):
        prefix, verb, rest = m.groups()
        args = rest.split()
        if verb == "branch":
            if not branch_lists_only(args):
                return "creates, deletes or renames a branch (only listing is allowed)"
        elif verb == "fetch":
            # `fetch origin x:y` writes a local ref. A URL remote has colons of its own.
            for arg in args:
                if ":" in arg and not re.match(r"^(?:\w+://|[\w.-]+@[\w.-]+:)", arg):
                    return "fetches into a named local ref (fetch only into FETCH_HEAD)"
        elif args[:1] == ["add"]:
            flags = [a for a in args[1:] if a.startswith("-")]
            names = [a for a in args[1:] if not a.startswith("-")]
            if "--detach" not in flags or any(f not in WORKTREE_FLAGS for f in flags):
                return "adds a worktree that isn't a plain `--detach` one"
            if "-C" in prefix or not 1 <= len(names) <= 2:
                return ("adds a worktree in a form that can't be checked "
                        "(use `git worktree add --detach <dir> <ref>` without `-C`)")
            target = names[0]
            if not (target.startswith("/") or target.startswith("~/")) or re.search(r"[$`\\]|^''$", target):
                return "adds a worktree at a path that isn't a plain absolute path"
            path = Path(target).expanduser().resolve()
            root = repo_root(cwd)
            if path == root or root in path.parents:
                return "adds a worktree inside the repo (use a scratch dir outside it)"
        elif args[:1] != ["list"]:
            return "changes worktrees"
    return None


READ_ONLY = [
    (git_write(GIT_WRITES), "changes git state"),
    (GH_WRITE, "writes to the git host"),
    (GH_API_WRITE, "writes through the GitHub API"),
    (FS_WRITE, "writes files or runs a build"),
    (REDIRECT, "redirects output into a file"),
]

RULES = {
    "scout": READ_ONLY,
    "reviewer": [(git_write(REVIEWER_GIT_WRITES), "changes git state"), *READ_ONLY[1:]],
    "worker": [
        (GH_MERGE, "merges a PR (merging belongs to the orchestrator)"),
        (FORCE_PUSH, "force-pushes"),
        (PUSH_DEFAULT, "pushes to the default branch"),
    ],
}

NEXT_STEP = {
    "scout": "Report what you found and let your caller act on it.",
    "reviewer": "Read code with `gh pr diff`, `git show <ref>:<path>` or a scratch checkout "
                "(`git worktree add --detach <absolute dir outside the repo> FETCH_HEAD`) and put the change in your findings.",
    "worker": "Push your own branch normally and report the PR; the orchestrator merges.",
}


def role_of(agent_type: str) -> str | None:
    plugin, _, role = agent_type.partition(":")
    if plugin == PLUGIN and role in RULES:
        return role
    return None


def review_post(command: str) -> str | None:
    """Check a reviewer's review post. None if the command isn't aimed at one; otherwise
    "" if it's well formed, or what's wrong with it."""
    # Reads of earlier reviews (a GET) and quoted text that only mentions these commands
    # don't count as aimed at a post.
    unquoted = strip_quotes(command)
    aimed = REVIEW_AIMED.search(unquoted)
    if not aimed or (aimed.group(0).startswith("gh api") and not GH_API_WRITE.search(unquoted)):
        return None
    if PR_REVIEW_INLINE.match(command):
        return ""
    lines = command.split("\n")
    api = REVIEW_POST.match(lines[0])
    pr = PR_REVIEW_HEREDOC.match(lines[0])
    head = api or pr
    if not head:
        return "it isn't in the accepted shape"
    delimiter = head.group(2)
    try:
        end = next(i for i, line in enumerate(lines) if i > 0 and line.strip() == delimiter)
    except StopIteration:
        return "its heredoc never closes"
    # Anything after the heredoc would run unchecked.
    if any(line.strip() for line in lines[end + 1 :]):
        return "it runs more after the review"
    if pr:
        return ""
    try:
        payload = json.loads("\n".join(lines[1:end]))
    except json.JSONDecodeError:
        return "its payload isn't JSON"
    if not isinstance(payload, dict) or payload.get("event") != "COMMENT":
        return 'its event isn\'t "COMMENT" (approving or requesting changes stays with the orchestrator)'
    return ""


def posting_enabled(cwd: str) -> bool:
    """True when the nearest config up from cwd says `Post reviews: yes`."""
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        config = directory / CONFIG
        if config.is_file():
            flag = POST_FLAG.search(config.read_text(encoding="utf-8", errors="replace"))
            return bool(flag) and flag.group(1).lower() == "yes"
    return False


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"orchestrate guard: {reason}",
            }
        },
        sys.stdout,
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    role = role_of(data.get("agent_type") or "")
    command = (data.get("tool_input") or {}).get("command") or ""
    if role is None or not command:
        return
    if role == "reviewer":
        problem = review_post(command)
        if problem == "":
            if posting_enabled(data.get("cwd") or ""):
                return
            deny("the config doesn't say `Post reviews: yes`, so return your verdict without posting it.")
            return
        if problem:
            deny(f"this review post is refused because {problem}. {SHAPES}")
            return
    if MERGE_SCRIPT.search(command):
        deny(f"the {role} role can't run a command that merges a PR (merging belongs to the orchestrator). "
             f"{NEXT_STEP[role]}")
        return
    text = strip_quotes(command)
    if role == "reviewer":
        what = reviewer_git(text, data.get("cwd") or "")
        if what:
            deny(f"the reviewer role can't run a command that {what}. {NEXT_STEP[role]}")
            return
    for pattern, what in RULES[role]:
        if pattern.search(text):
            deny(f"the {role} role can't run a command that {what}. {NEXT_STEP[role]}")
            return


if __name__ == "__main__":
    main()
