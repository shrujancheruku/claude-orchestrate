"""The one GitHub adapter: every `gh` call in gate, merge and watch-ci goes through here.

Stdlib only, Python 3.9+. A sibling script imports it with `import github`.

Each function runs one `gh` call and returns parsed data or raises `GhError`, whose `kind` says why:
  missing    no `gh` on PATH (retrying cannot help)
  failed     `gh` exited non-zero (message = its stderr, else stdout, on one line)
             (one whose message says git has no repository or working directory counts as could not run)
             (a secondary rate limit is retried with backoff first; primary-quota exhaustion is not)
  malformed  `gh` printed unparseable JSON, or JSON of the wrong shape for the call (a `[]` for a PR)
`could_not_run(e)` maps missing and malformed to each script's "could not run" exit (2).
"""

from __future__ import annotations

import json
import re
import sys
import time
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
        createdAt
        parent { number }
        subIssuesSummary { total completed }
        labels(first: 30) { nodes { name } }
        assignees(first: 1) { totalCount }
        blockedBy(first: 20) { nodes { number state } }
        userContentEdits(first: 20) { nodes { editedAt } }
        comments(last: 100) { nodes { body createdAt userContentEdits(first: 20) { nodes { editedAt } } } }
      }
    }
  }
}"""


# Closed `wontfix` tickets that still have an assignee: run unassigns on ABANDON, so each is routed once.
ABANDON_QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 30, states: CLOSED, labels: ["wontfix"], orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { number assignees(first: 1) { totalCount } }
    }
  }
}"""

THREADS_QUERY = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first: 50) {
            pageInfo { hasNextPage endCursor }
            nodes { author { login } body url }
          }
        }
      }
    }
  }
}"""

# The rest of one thread's comments, when it has more than the 50 the threads query returns.
THREAD_COMMENTS_QUERY = """query($id: ID!, $after: String) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      comments(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { author { login } body url }
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


# GitHub's secondary rate limit (too many concurrent or back-to-back requests), and the "API rate limit
# (already) exceeded" wording it shares with the hourly quota. gh prints these on stderr (with `-i`, the headers too).
RATE_LIMITED = re.compile(r"secondary rate limit|abuse detection|API rate limit (?:already )?exceeded", re.I)
RETRY_AFTER = re.compile(r"retry-after:?\s*(\d+)", re.I)
QUOTA_EMPTY = re.compile(r"x-ratelimit-remaining:?\s*0\b", re.I)
QUOTA_RESET = re.compile(r"x-ratelimit-reset:?\s*(\d+)", re.I)
TRIES, FIRST_WAIT, MAX_WAIT = 5, 2, 60
# A reset further off than this is the hourly quota running out: waiting it out here would stall the script.
PRIMARY_RESET_AFTER = 120

# Injectable so tests need not wait.
sleep = time.sleep
clock = time.time


def _rate_limit(text: str):
    """None when `text` is no rate limit; else (retry-after seconds or None, reset epoch or None, quota empty)."""
    if not RATE_LIMITED.search(text):
        return None
    after, reset = RETRY_AFTER.search(text), QUOTA_RESET.search(text)
    return (int(after.group(1)) if after else None, int(reset.group(1)) if reset else None,
            bool(QUOTA_EMPTY.search(text)))


def call(*args, cwd=None) -> str:
    """Run `gh <args>` and return stdout; raise GhError (missing or failed).

    A secondary rate limit is retried up to TRIES times, sleeping the `retry-after` seconds when gh shows them,
    else FIRST_WAIT doubling to MAX_WAIT. The hourly quota (remaining 0, reset far off) fails at once with the
    reset time, since waiting for it would stall the caller.
    """
    wait = FIRST_WAIT
    for attempt in range(1, TRIES + 1):
        p = common.run("gh", *args, cwd=cwd)
        if p.returncode == common.NOT_FOUND:
            raise GhError(MISSING, common.one_line(p.stderr))
        if p.returncode == 0:
            return p.stdout
        message = common.one_line(p.stderr or p.stdout)
        limit = _rate_limit(f"{p.stderr}\n{p.stdout}")
        if limit is None:
            raise GhError(FAILED, message)
        after, reset, empty = limit
        if empty and reset is not None and reset - clock() > PRIMARY_RESET_AFTER:
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset))
            raise GhError(FAILED, f"{message} (hourly quota used up; resets at {at})")
        if attempt == TRIES:
            raise GhError(FAILED, message)
        pause = after if after is not None else wait
        print(f"github: rate limited, retrying in {pause}s (try {attempt} of {TRIES})", file=sys.stderr)
        sleep(pause)
        wait = min(wait * 2, MAX_WAIT)


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


# Check results that block a merge: failed ones, and ones still going (a missing result counts as going).
CI_BAD = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE"}
CI_PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}


def ci_verdict(checks) -> tuple:
    """("pass" | "pending" | "failed", [names of the checks that decide it]) from a statusCheckRollup list.

    A PR with no checks at all is pending, as `watch-ci` counts it (so a repo with no CI never passes). Unlike
    `watch-ci`, which waits while any check is pending, a failed check outranks a pending one here, so a
    refusal names the real reason."""
    if not checks:
        return "pending", []
    bad, pending = [], []
    for c in checks:
        name = str(c.get("name") or c.get("context") or "?")
        # A CheckRun has status+conclusion; a commit StatusContext has only state.
        r = (c.get("conclusion") or c.get("state") or "").upper()
        if ("status" in c and c.get("status") != "COMPLETED") or r in CI_PENDING or not r:
            pending.append(name)
        elif r in CI_BAD:
            bad.append(name)
    if bad:
        return "failed", bad
    return ("pending", pending) if pending else ("pass", [])


def issue_claim(issue, cwd=None) -> tuple:
    """(state, claimed) of an issue: claimed when it has an assignee (the claim, as the tracker doc says)."""
    data = call_json("issue", "view", str(issue), "--json", "state,assignees", cwd=cwd)
    return str(data.get("state")), bool(data.get("assignees"))


def close_issue(issue, cwd=None) -> None:
    call("issue", "close", str(issue), cwd=cwd)


def open_prs(base: str, cwd=None) -> list:
    return call_json("pr", "list", "--state", "open", "--base", base, "--limit", "200", "--json",
                     "number,headRefName,isCrossRepository,isDraft", shape=list, cwd=cwd)


def open_prs_with_heads(cwd=None) -> list:
    """Every open PR (any base) with its number, branch and head commit: what a worker's report is checked against."""
    return call_json("pr", "list", "--state", "open", "--limit", "200", "--json",
                     "number,headRefName,headRefOid,isDraft,baseRefName", shape=list, cwd=cwd)


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
    number, title, url, body, creation time, parent, sub-issue summary, labels, assignees (count), blockers, edits and comments.
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


def _connection(query, variables, path, after=None, cwd=None) -> list:
    """Every node of one GraphQL connection, all pages. `path` runs from the reply's `data` to the connection;
    `variables` are passed as -F (numbers) or -f (strings, never coerced); `after` starts past a known cursor."""
    nodes, seen = [], set()
    while True:
        args = ["api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
        if after:
            args += ["-f", f"after={after}"]
        conn = call_json(*args, cwd=cwd)
        try:
            conn = conn["data"]
            for key in path:
                conn = conn[key]
            page = conn["nodes"]
        except (KeyError, TypeError):
            raise GhError(MALFORMED, "gh returned an unexpected reply (review threads)") from None
        if not isinstance(page, list):
            raise GhError(MALFORMED, "gh returned an unexpected reply (review threads)")
        nodes += page
        info = conn.get("pageInfo") or {}
        after = info.get("endCursor") if info.get("hasNextPage") else None
        if not after:
            return nodes
        if after in seen:  # a cursor that does not advance would loop for ever
            raise GhError(MALFORMED, "gh returned a repeated page cursor (review threads)")
        seen.add(after)


def _comment(c: dict) -> dict:
    return {"author": (c.get("author") or {}).get("login") or "ghost", "body": str(c.get("body") or ""),
            "url": c.get("url")}


def review_threads(pr, cwd=None) -> list:
    """Every review thread of a PR, past 100 threads and past 50 comments in a thread:
    [{id, resolved, outdated, path, line (the diff line, else the original one for an outdated thread; None on a file-level thread), comments: [{author, body, url}]}].
    The one home of the threads read (`threads` and `merge` both use it)."""
    info = repo_info(cwd=cwd)
    try:
        owner, name = info["owner"]["login"], info["name"]
        number = int(str(pr).lstrip("#"))
    except (KeyError, TypeError, ValueError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    nodes = _connection(THREADS_QUERY, {"owner": owner, "name": name, "number": number},
                        ("repository", "pullRequest", "reviewThreads"), cwd=cwd)
    out = []
    for n in nodes:
        try:
            comments = list(n["comments"]["nodes"])
            page = n["comments"].get("pageInfo") or {}
            tid = n["id"]
        except (KeyError, TypeError, AttributeError):
            raise GhError(MALFORMED, "gh returned an unexpected reply (review threads)") from None
        if page.get("hasNextPage") and page.get("endCursor"):
            comments += _connection(THREAD_COMMENTS_QUERY, {"id": tid}, ("node", "comments"),
                                    after=page["endCursor"], cwd=cwd)
        out.append({"id": tid, "resolved": bool(n.get("isResolved")), "outdated": bool(n.get("isOutdated")),
                    "path": n.get("path") or "", "line": n.get("line") or n.get("originalLine"),
                    "comments": [_comment(c) for c in comments if isinstance(c, dict)]})
    return out


def pr_commit_shas(pr, cwd=None) -> list:
    """The commit shas on a PR's branch at the remote (the host's own list, paged)."""
    out = call("api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{str(pr).lstrip('#')}/commits",
               "--jq", ".[].sha", cwd=cwd)
    return out.split()


def abandoned_tickets(repo: dict, cwd=None) -> list:
    """Numbers of closed `wontfix` tickets that are still assigned (the newest 30 closed ones)."""
    try:
        owner, name = repo["owner"]["login"], repo["name"]
    except (KeyError, TypeError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    data = call_json("api", "graphql", "-f", f"query={ABANDON_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}", cwd=cwd)
    try:
        nodes = data["data"]["repository"]["issues"]["nodes"]
        return sorted(int(n["number"]) for n in nodes if ((n.get("assignees") or {}).get("totalCount") or 0) > 0)
    except (KeyError, TypeError, ValueError):
        raise GhError(MALFORMED, "gh returned an unexpected reply (closed issues)") from None


def add_blocked_by(child, blocker, cwd=None) -> None:
    """Add the native `blocked_by` edge: issue `child` is blocked by issue `blocker` (the tracker doc's Blocking command)."""
    out = call("api", f"repos/{{owner}}/{{repo}}/issues/{blocker}", "--jq", ".id", cwd=cwd).strip()
    if not out.isdigit():
        raise GhError(MALFORMED, f"issue id is not a number: {common.one_line(out) or 'empty reply'}")
    call("api", "--method", "POST", f"repos/{{owner}}/{{repo}}/issues/{child}/dependencies/blocked_by",
         "-F", f"issue_id={out}", cwd=cwd)
