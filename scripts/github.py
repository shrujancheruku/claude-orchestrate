"""The one GitHub adapter: every `gh` call in gate, merge and watch-ci goes through here.

Stdlib only, Python 3.9+. A sibling script imports it with `import github`.

Each function runs one `gh` call and returns parsed data or raises `GhError`, whose `kind` says why:
  missing    no `gh` on PATH (retrying cannot help)
  failed     `gh` exited non-zero (message = its stderr, else stdout, on one line)
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


class GhError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


def could_not_run(e: GhError) -> bool:
    """True when the script should exit 2 ("could not run") rather than treat the call as one more failure."""
    return e.kind in (MISSING, MALFORMED)


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


def close_issue(issue, cwd=None) -> None:
    call("issue", "close", str(issue), cwd=cwd)


def open_prs(base: str, cwd=None) -> list:
    return call_json("pr", "list", "--state", "open", "--base", base, "--json",
                     "number,headRefName,isCrossRepository", shape=list, cwd=cwd)
