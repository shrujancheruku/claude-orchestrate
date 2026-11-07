"""The one GitHub adapter: every `gh` call in gate, merge and watch-ci goes through here.

Stdlib only, Python 3.9+. A sibling script imports it with `import github`.

Each function runs one `gh` call and returns parsed data or raises `GhError`, whose `kind` says why:
  missing    no `gh` on PATH (retrying cannot help)
  failed     `gh` exited non-zero (message = its stderr, else stdout, on one line)
             (one whose message says git has no repository or working directory counts as could not run)
  malformed  `gh` printed unparseable JSON, or JSON of the wrong shape for the call (a `[]` for a PR)
`could_not_run(e)` maps missing and malformed to each script's "could not run" exit (2).
"""

from __future__ import annotations

import json
import re
import urllib.parse

import common

MISSING, FAILED, MALFORMED = "missing", "failed", "malformed"
# A 404/422 on a branch ref: the host already removed it.
GONE = re.compile(r"\b(?:404|422)\b|not found|does not exist|reference.*not exist", re.I)


# git has no usable repository or working directory: retrying cannot clear it.
NO_REPO = re.compile(r"not a git repository|unable to read current working directory|"
                     r"cannot determine current working directory|getcwd", re.I)

ISSUES_QUERY = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $after, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        body
        parent { number }
        subIssuesSummary { total completed }
        labels(first: 20) { nodes { name } }
        assignees(first: 1) { totalCount }
        blockedBy(first: 20) { nodes { number state } }
        userContentEdits(first: 20) { nodes { editedAt } }
        comments(last: 100) { nodes { body createdAt userContentEdits(first: 20) { nodes { editedAt } } } }
      }
    }
  }
}"""


class GhError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


def could_not_run(e: GhError) -> bool:
    """True when the script should exit 2 ("could not run") rather than treat the call as one more failure."""
    return e.kind in (MISSING, MALFORMED) or (e.kind == FAILED and bool(NO_REPO.search(e.message)))


def call(*args, cwd=None) -> str:
    """Run `gh <args>` and return stdout; raise GhError (missing or failed)."""
    p = common.run("gh", *args, cwd=cwd)
    if p.returncode == common.NOT_FOUND:
        raise GhError(MISSING, common.one_line(p.stderr))
    if p.returncode != 0:
        raise GhError(FAILED, common.one_line(p.stderr or p.stdout))
    return p.stdout


def call_json(*args, shape=dict, cwd=None):
    """`call`, parsed as JSON of type `shape`; anything else is malformed."""
    try:
        data = json.loads(call(*args, cwd=cwd))
    except ValueError:
        raise GhError(MALFORMED, "gh returned malformed JSON") from None
    if not isinstance(data, shape):
        raise GhError(MALFORMED, f"gh returned JSON of the wrong shape (wanted {shape.__name__})")
    return data


def pr_view(pr, fields: str, cwd=None) -> dict:
    return call_json("pr", "view", str(pr), "--json", fields, cwd=cwd)


def behind_by(base: str, head: str, cwd=None) -> int:
    out = call("api", f"repos/{{owner}}/{{repo}}/compare/{base}...{head}", "--jq", ".behind_by", cwd=cwd)
    if not out.strip().isdigit():
        raise GhError(MALFORMED, f"behind_by is not a number: {common.one_line(out) or 'empty reply'}")
    return int(out.strip())


def default_branch(cwd=None) -> str:
    name = call_json("repo", "view", "--json", "defaultBranchRef", cwd=cwd).get("defaultBranchRef")
    if not isinstance(name, dict) or not isinstance(name.get("name"), str):
        raise GhError(MALFORMED, "gh returned no default branch")
    return name["name"]


def merge_pr(pr, method: str, head_oid: str = "", cwd=None) -> None:
    """Run the merge command. --match-head-commit makes a push after review a refusal, not a merge."""
    guard = ["--match-head-commit", head_oid] if head_oid else []
    call("pr", "merge", str(pr), f"--{method}", *guard, cwd=cwd)


def update_branch(pr, cwd=None) -> None:
    """Merge the base into the PR's branch on the host (`gh pr update-branch`)."""
    call("pr", "update-branch", str(pr), cwd=cwd)


def delete_branch(branch: str, cwd=None) -> str:
    """Delete refs/heads/<branch> over gh's HTTPS API (origin may be SSH, which can be denied).

    Returns "deleted" or "already gone" (a 404/422 means the host removed it); raises GhError otherwise.
    """
    # Git allows `#` and `%` in branch names; unquoted, `feat#x` would address `heads/feat`.
    quoted = urllib.parse.quote(branch, safe="/")
    try:
        call("api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}", cwd=cwd)
    except GhError as e:
        if e.kind == FAILED and GONE.search(e.message):
            return "already gone"
    try:
        call("api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{quoted}", cwd=cwd)
    except GhError as e:
        if e.kind == FAILED and GONE.search(e.message):
            return "already gone"
        raise
    return "deleted"


def issue_state(issue, cwd=None) -> str:
    return str(call_json("issue", "view", str(issue), "--json", "state", cwd=cwd).get("state"))


def open_blockers(issue, cwd=None) -> int:
    """How many open issues block `issue` (the tracker's own dependency summary)."""
    out = call("api", f"repos/{{owner}}/{{repo}}/issues/{issue}", "--jq", ".issue_dependencies_summary.blocked_by", cwd=cwd)
    if not out.strip().isdigit():
        raise GhError(MALFORMED, f"blocked_by is not a number: {common.one_line(out) or 'empty reply'}")
    return int(out.strip())


def issue_claim(issue, cwd=None) -> tuple:
    """(state, claimed) of an issue: claimed when it has an assignee (the claim, as the tracker doc says)."""
    data = call_json("issue", "view", str(issue), "--json", "state,assignees", cwd=cwd)
    return str(data.get("state")), bool(data.get("assignees"))


def close_issue(issue, cwd=None) -> None:
    call("issue", "close", str(issue), cwd=cwd)


def open_prs(base: str, cwd=None) -> list:
    return call_json("pr", "list", "--state", "open", "--base", base, "--json",
                     "number,headRefName,isCrossRepository", shape=list, cwd=cwd)


def open_prs_with_heads(cwd=None) -> list:
    """Every open PR (any base) with its number, branch and head commit: what a worker's report is checked against."""
    return call_json("pr", "list", "--state", "open", "--limit", "200", "--json",
                     "number,headRefName,headRefOid", shape=list, cwd=cwd)


def merged_prs(base: str, cwd=None) -> list:
    # The newest 200 only: an older merged PR is still caught by its closed issue (`Closes #n`).
    return call_json("pr", "list", "--state", "merged", "--base", base, "--limit", "200", "--json",
                     "number,headRefName", shape=list, cwd=cwd)


def pr_files(pr, cwd=None) -> list:
    """The paths a PR's diff changes."""
    files = pr_view(pr, "files", cwd=cwd).get("files")
    if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get("path"), str) for f in files):
        raise GhError(MALFORMED, "gh returned no file list for the PR")
    return [f["path"] for f in files]


def repo_info(cwd=None) -> dict:
    """The repository's `owner`, `name` and `defaultBranchRef`, from one `gh repo view`."""
    return call_json("repo", "view", "--json", "owner,name,defaultBranchRef", cwd=cwd)


def open_issues(repo: dict, cwd=None) -> list:
    """Every open issue (all pages, newest created first: a cursor over creation order holds still while issues are edited) as raw GraphQL nodes, the one home of the issues query:
    number, title, url, body, parent, sub-issue summary, labels, assignees (count), blockers, edits and comments.
    `repo` is `repo_info`'s (or `gh repo view`'s) owner/name dict."""
    try:
        owner, name = repo["owner"]["login"], repo["name"]
    except (KeyError, TypeError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    nodes, after, seen = [], None, set()
    while True:
        args = ["api", "graphql", "-f", f"query={ISSUES_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if after:
            args += ["-f", f"after={after}"]  # -f: a raw string, never coerced
        data = call_json(*args, cwd=cwd)
        try:
            issues = data["data"]["repository"]["issues"]
            page = issues["nodes"]
        except (KeyError, TypeError):
            raise GhError(MALFORMED, "gh returned an unexpected reply (issues)") from None
        if not isinstance(page, list):
            raise GhError(MALFORMED, "gh returned an unexpected reply (issues)")
        nodes += page
        info = issues.get("pageInfo") or {}
        after = info.get("endCursor") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("hasNextPage") and after):
            return nodes
        if after in seen:  # a cursor that does not advance would loop for ever
            raise GhError(MALFORMED, "gh returned a repeated page cursor (issues)")
        seen.add(after)
