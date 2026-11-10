"""The one GitHub adapter: every `gh` call in gate, merge and watch-ci goes through here.

Stdlib only, Python 3.9+. A sibling script imports it with `import github`.

Each function runs one `gh` call and returns parsed data or raises `GhError`, whose `kind` says why:
  missing    no `gh` on PATH (retrying cannot help)
  failed     `gh` exited non-zero (message = its stderr, else stdout, on one line)
             (one whose message says git has no repository or working directory counts as could not run)
             (a secondary rate limit is retried with backoff first; primary-quota exhaustion is not)
  ratelimited a rate limit that retrying here did not clear (hourly quota, a wait over the budget, or every try used)
  servererror a 5xx (or a dropped connection) that outlasted the retries or the wait budget: no reset is coming
  malformed  `gh` printed unparseable JSON, or JSON of the wrong shape for the call (a `[]` for a PR)
The wait budget is per process: every long-running loop must call `reset_wait_budget()` each tick, as merge and
watch-ci do, or its first rate-limit wait uses up the budget for every later tick.
`could_not_run(e)` maps missing, malformed, ratelimited and servererror to each script's "could not run" exit (2).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import common
import config
import runlog
from repo import STATE

MISSING, FAILED, MALFORMED, RATELIMITED, SERVER_ERROR = "missing", "failed", "malformed", "ratelimited", "servererror"
# A 404/422 on a branch ref: the host already removed it.
GONE = re.compile(r"\b(?:404|422)\b|not found|does not exist|reference.*not exist", re.I)


# git has no usable repository or working directory: retrying cannot clear it.
NO_REPO = re.compile(r"not a git repository|unable to read current working directory|"
                     r"cannot determine current working directory|getcwd", re.I)

# GitHub bills a GraphQL call as (1 + the nodes each connection is asked under) / 100 points: a connection inside a
# 100-node page costs 100 requests, one inside those 100 more costs 10,000. So the comments' edit history
# (`userContentEdits`, 100 x 100 nodes) made this query ~105 points (#498). The scripts read only whether something
# was edited after a time, which `lastEditedAt` answers with no connection: 1 + 4 x 100 requests, ~5 points.
ISSUES_QUERY = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $after, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}) {
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
        editor { login }
        author { login }
        authorAssociation
        lastEditedAt
        comments(last: 100) { nodes { body createdAt lastEditedAt authorAssociation author { login } } }
      }
    }
  }
}"""


# One issue, open or closed (`scripts/issue`, #545): the fields tracker.from_nodes reads, plus the state and the comment
# count (the 100 newest comments come back; the count says how many older ones are not shown).
ISSUE_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      url
      state
      body
      labels(first: 30) { nodes { name } }
      editor { login }
      author { login }
      authorAssociation
      lastEditedAt
      comments(last: 100) { totalCount nodes { body createdAt lastEditedAt authorAssociation author { login } } }
    }
  }
}"""


def issue_node(number, cwd=None) -> dict:
    """The raw GraphQL node of one issue (`ISSUE_QUERY`), open or closed; a GhError when the repository has no such issue."""
    info = repo_info(cwd=cwd)
    try:
        owner, name = info["owner"]["login"], info["name"]
        n = int(str(number).lstrip("#"))
    except (KeyError, TypeError, ValueError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    data = call_json("api", "graphql", "-f", f"query={ISSUE_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}",
                     "-F", f"number={n}", cwd=cwd, idempotent=True)
    try:
        node = data["data"]["repository"]["issue"]
    except (KeyError, TypeError):
        raise GhError(MALFORMED, "gh returned an unexpected reply (issue)") from None
    if node is None:
        raise GhError(FAILED, f"issue #{n} does not exist")
    return node


# The Projects board in one read (#447): the owner (a user or an organization), the project's id, its `Stage` field
# with every option, and the items a page at a time (first page carries the field; `after` pages the items). The
# field is named in the query, so it must match board.FIELD (a test holds them together).
PROJECT_QUERY = """query($owner: String!, $number: Int!, $after: String) {
  repositoryOwner(login: $owner) {
    __typename
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        field(name: "Stage") { ... on ProjectV2SingleSelectField { id options { id name color description } } }
        items(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            content {
              __typename
              ... on Issue { number title repository { nameWithOwner } }
              ... on DraftIssue { title }
            }
            stage: fieldValueByName(name: "Stage") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
          }
        }
      }
    }
  }
}"""

# Closed `wontfix` tickets that still have an assignee: run unassigns on ABANDON, so each is routed once.
# The filter keeps only the assigned ones (`assignee: "*"` is any assignee), so a grooming pass that closes a dozen
# tickets `wontfix` cannot push an assigned one past the page (#476); `after` pages the unlikely overflow.
ABANDON_QUERY = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 30, after: $after, states: CLOSED, labels: ["wontfix"], filterBy: {assignee: "*"},
           orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
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
            nodes { author { login } authorAssociation body url }
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
        nodes { author { login } authorAssociation body url }
      }
    }
  }
}"""


# ADR-0015: the one rule for whose tracker text may steer a run. Every reader of a comment, review or review thread
# asks `is_trusted`; nothing else in scripts/ compares an association or a login.
TRUSTED_ASSOCIATIONS = frozenset(("OWNER", "MEMBER", "COLLABORATOR"))


def is_trusted(association, login=None, extra=()) -> bool:
    """True for a trusted author: an `author_association` of OWNER, MEMBER or COLLABORATOR, or a login in `extra` (the
    config's `Trusted authors:` line, lowercase). A missing association is untrusted (fail closed). GraphQL's
    `author.login` for a bot drops the `[bot]` suffix that the REST login and the config carry, so both sides are
    compared without it."""
    if str(association or "").upper() in TRUSTED_ASSOCIATIONS:
        return True
    return bool(login) and _bare(login) in {_bare(x) for x in extra}


def _bare(login) -> str:
    """A login lowercased and without a trailing `[bot]`."""
    s = str(login).lower()
    return s[:-5] if s.endswith("[bot]") else s


def trusted_logins(cwd=None) -> tuple:
    """The config's extra trusted logins (lowercase), read from `cwd`'s repository; none when there is no config."""
    return config.Config.load(cwd or ".").trusted_authors


def own_prs(prs) -> list:
    """The PRs of the run: a fork PR (`isCrossRepository`) is never a ticket's PR and holds no path (ADR-0015)."""
    return [p for p in prs if not (isinstance(p, dict) and p.get("isCrossRepository"))]


class GhError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message
        if kind == RATELIMITED:
            # A CLI's main() reads this to end `next: retry-after <time>` however deep the call that was limited.
            global rate_limited_until
            rate_limited_until = common.rate_limit_time(message) or _utc(clock() + DEFAULT_PAUSE)


rate_limited_until = None  # the retry time of the last rate limit this process gave up on, or None


def rate_limit_verdict(code: int):
    """`retry-after <time>` when `code` is a could-not-run exit and this process gave up on a rate limit, else None."""
    return rate_limited_until if code == 2 else None


def could_not_run(e: GhError) -> bool:
    """True when the script should exit 2 ("could not run") rather than treat the call as one more failure."""
    return e.kind in (MISSING, MALFORMED, RATELIMITED, SERVER_ERROR) or (e.kind == FAILED and bool(NO_REPO.search(e.message)))


# GitHub's secondary rate limit (too many concurrent or back-to-back requests), and the "API rate limit
# (already) exceeded" wording it shares with the hourly quota. gh prints these on stderr (with `-i`, the headers too).
RATE_LIMITED = re.compile(r"secondary rate limit|abuse detection|API rate limit (?:already )?exceeded", re.I)
RETRY_AFTER = re.compile(r"retry-after:?\s*(\d+)", re.I)
QUOTA_EMPTY = re.compile(r"x-ratelimit-remaining:?\s*0\b", re.I)
QUOTA_RESET = re.compile(r"x-ratelimit-reset:?\s*(\d+)", re.I)
# A GitHub 5xx (500-504) is transient: retried like a secondary limit, on a short ladder (seconds) since no
# reset is involved. A 4xx other than a rate limit is never retried.
SERVER_ERROR_TEXT = re.compile(r"\bHTTP 50[0-4]\b")
# Failures with no status line that are transient too, for a read only: a truncated reply (a 5xx whose body gh
# could not parse) and a dropped connection.
TRANSIENT_TEXT = re.compile(r"unexpected end of JSON input|connection reset|ECONNRESET", re.I)
SERVER_ERROR_PAUSES = (2, 4, 8, 16)


def server_error_tries() -> int:
    """ORCHESTRATE_GH_5XX_TRIES when it is a positive integer, else TRIES; the test harness sets 1 so a fake gh's
    5xx fails at once instead of sleeping through the ladder."""
    try:
        value = int(os.environ.get("ORCHESTRATE_GH_5XX_TRIES", ""))
    except ValueError:
        return TRIES
    return min(value, TRIES) if value > 0 else TRIES


TRIES = 5
# A reset further off than this is the hourly quota running out: waiting it out here would stall the script.
PRIMARY_RESET_AFTER = 120

# Injectable so tests need not wait.
sleep = time.sleep
clock = time.time

# Written on a rate-limit reply so every other process waits too. The limit is per GitHub user, so the file is
# machine-wide per host: $XDG_CACHE_HOME/orchestrate/github-ratelimit-<host>.json (~/.cache when unset).
# Where that can't be written it falls back to <git common dir>/orchestrate-ratelimit.json.
FALLBACK_FILE = "orchestrate-ratelimit.json"
DEFAULT_PAUSE = 60  # no retry-after or reset header: assume a minute
# The most one script run waits in total, across every call, the shared-file wait and every retry (#491). The
# orchestrator runs these scripts through Bash (two-minute default timeout) and neither `merge --timeout` nor the
# watch-ci deadline sees a sleep, so a longer wait is returned as `rate limit until <time>` (exit 2) for the caller
# to retry later. The budget is per process, not per call: `waited` adds up every sleep, so a later call that would
# sleep past it fails at once. A loop that runs for hours (watch-ci, merge) calls `reset_wait_budget()` each tick so
# every tick gets its own.
DEFAULT_WAIT_BUDGET = 75
waited = 0.0  # seconds this process has slept in rate-limit and 5xx waits since the last reset_wait_budget()


def wait_budget() -> float:
    """ORCHESTRATE_GH_WAIT seconds when it is a non-negative number, else DEFAULT_WAIT_BUDGET."""
    try:
        value = float(os.environ.get("ORCHESTRATE_GH_WAIT", ""))
    except ValueError:
        return DEFAULT_WAIT_BUDGET
    return value if value >= 0 else DEFAULT_WAIT_BUDGET


def reset_wait_budget() -> None:
    """Start a fresh wait budget: a long-lived loop calls this at the top of each tick."""
    global waited
    waited = 0.0


def _wait_until(reset: float, cwd=None, shared: bool = False) -> None:
    """Sleep until `reset`, or raise `ratelimited` with the time when that would pass the process's wait budget.

    `shared` marks a wait for a reset another process recorded, not this process's own hit; it is logged so the
    profile shows how much waiting one hit multiplied across scripts."""
    global waited
    seconds = max(reset - clock(), 0)
    if waited + seconds > wait_budget():
        raise GhError(RATELIMITED, f"rate limit until {_utc(reset)}")
    waited += seconds
    _log({"outcome": "wait", "seconds": round(seconds, 1), "shared": shared}, cwd)
    sleep(seconds)
HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")


def _utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


_paths = {}  # (cwd, cache dir, host) -> the file, resolved once so the fallback is announced once


def _state_path(cwd) -> Path:
    key = (str(cwd), os.environ.get("XDG_CACHE_HOME"), os.environ.get("GH_HOST"))
    if key not in _paths:
        _paths[key] = _resolve_state_path(cwd)
    return _paths[key]


def _resolve_state_path(cwd) -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    host = os.environ.get("GH_HOST") or "github.com"
    path = Path(cache) / "orchestrate" / f"github-ratelimit-{host}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.access(path.parent, os.W_OK):
            return path
    except OSError:
        pass
    p = common.run("git", "rev-parse", "--git-common-dir", cwd=cwd)
    if p.returncode != 0 or not p.stdout.strip():
        return path  # no repository either: reads find nothing and writes are dropped
    fallback = Path(cwd or ".") / p.stdout.strip() / FALLBACK_FILE
    print(f"github: {path.parent} is not writable; sharing the rate-limit backoff through {fallback}", file=sys.stderr)
    return fallback


def _shared_reset(cwd) -> float:
    """The reset epoch another process recorded, or 0 when there is none or it has passed (the file is then removed)."""
    path = _state_path(cwd)
    try:
        reset = float(json.loads(path.read_text())["reset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if reset > clock():
        return reset
    try:
        path.unlink()
    except OSError:
        pass
    return 0


def _record_reset(cwd, reset: float) -> None:
    """Keep the later of the recorded reset and `reset`; best effort, a write failure only loses the sharing."""
    path = _state_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"reset": max(reset, _shared_reset(cwd))}))
        os.replace(tmp, path)
    except OSError:
        pass


def _wait_for_shared_reset(cwd) -> None:
    reset = _shared_reset(cwd)
    if not reset:
        return
    _wait_until(reset, cwd, shared=True)


def _rate_limit(text: str):
    """None when `text` is no rate limit; else (retry-after seconds or None, reset epoch or None, quota empty)."""
    if not RATE_LIMITED.search(text):
        return None
    after, reset = RETRY_AFTER.search(text), QUOTA_RESET.search(text)
    return (int(after.group(1)) if after else None, int(reset.group(1)) if reset else None,
            bool(QUOTA_EMPTY.search(text)))


def _evidence(text: str) -> str:
    """Which limit fired: the HTTP status, the rate-limit headers gh showed, then the first line of the body."""
    status = HTTP_STATUS.search(text)
    parts = [f"HTTP {status.group(1)}"] if status else []
    for name in ("retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"):
        m = re.search(rf"{name}:?\s*(\d+)", text, re.I)
        if m:
            parts.append(f"{name}: {m.group(1)}")
    first = next((ln.strip() for ln in text.splitlines()
                  if ln.strip() and not re.match(r"(retry-after|x-ratelimit-\S+):", ln.strip(), re.I)), "")
    return f"{first} [{', '.join(parts)}]" if parts else first


# --- The call log ---------------------------------------------------------------------------------------------
# Every gh attempt appends one `gh_call` event to the run's events log, so a rate-limit hit can be traced to the
# script and call that spent the budget. An event never holds a body, a query or a token. Logging is best effort:
# any failure is swallowed and it never changes what `call` returns or raises. It reads the state file and appends
# with plain file I/O (no `git` subprocess). ORCHESTRATE_GH_LOG=off turns it off.

HEADER_BLOCK = re.compile(r"\r?\n\r?\n")
STATUS_LINE = re.compile(r"HTTP/\S+\s+(\d{3})")
# `gh api` flags that take a value, so the endpoint is the first argument that is neither a flag nor a value.
API_VALUE_FLAGS = {"-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--jq", "-q",
                   "--template", "-t", "--hostname", "--input", "--cache", "-p", "--preview"}
READ_VERBS = {"view", "list", "status", "diff", "checks", "field-list", "item-list"}
_roots = {}  # cwd -> the main checkout that owns the run log, or None


def split_headers(out: str) -> tuple:
    """(status or None, {lowercase header: value}, body) from `gh api --include` output; text that does not open
    with a status line is all body. Exactly one header block is removed."""
    m = STATUS_LINE.match(out)
    if not m:
        return None, {}, out
    parts = HEADER_BLOCK.split(out, 1)
    headers = {}
    for line in parts[0].splitlines()[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return int(m.group(1)), headers, parts[1] if len(parts) > 1 else ""


def _log_root(cwd):
    """The main checkout for `cwd`, found by walking up to `.git` (a linked worktree's `.git` file leads to the
    common dir), or None. File reads only: `repo.main_checkout` runs git, which a test's fake `gh` would count."""
    start = Path(cwd or os.getcwd()).resolve()
    if start not in _roots:
        root = None
        for d in (start, *start.parents):
            git = d / ".git"
            if git.is_dir():
                root = d
            elif git.is_file():
                root = d
                text = git.read_text(encoding="utf-8").strip()
                if text.startswith("gitdir:"):
                    gitdir = (d / text[7:].strip()).resolve()
                    try:
                        gitdir = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
                    except OSError:
                        pass
                    if gitdir.name == ".git":
                        root = gitdir.parent
            else:
                continue
            break
        _roots[start] = root
    return _roots[start]


def _int(headers: dict, name: str):
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def describe(args) -> dict:
    """What a call is, without its content: `method` (GET or the HTTP method; WRITE for a changing subcommand),
    `write`, `path` (the endpoint without its query string, `graphql`, or `pr view`) and `graphql`."""
    args = [str(a) for a in args]
    if args[:1] != ["api"]:
        write = not (len(args) > 1 and args[1] in READ_VERBS)
        # Subcommands (pr, issue, repo) run on GraphQL underneath.
        return {"method": "WRITE" if write else "GET", "write": write, "path": " ".join(args[:2]), "graphql": True}
    method, fields, path, i = None, False, None, 1
    while i < len(args):
        a = args[i]
        if a in ("-X", "--method") and i + 1 < len(args):
            method, i = args[i + 1].upper(), i + 2
        elif a in ("-f", "--raw-field", "-F", "--field") and i + 1 < len(args):
            fields, i = True, i + 2
        elif a in API_VALUE_FLAGS:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            path, i = path or a, i + 1
    path = (path or "").split("?", 1)[0]
    if path == "graphql":
        # A query is a read; only a mutation changes anything.
        mutation = any(a.startswith("query=") and a[6:].lstrip().startswith("mutation") for a in args)
        return {"method": "POST", "write": mutation, "path": path, "graphql": True}
    method = method or ("POST" if fields else "GET")
    return {"method": method, "write": method != "GET", "path": path, "graphql": False}


def _log(event: dict, cwd=None, args=None) -> None:
    """Append one `gh_call` event; any failure is swallowed (a log must never fail or slow a gh call)."""
    try:
        if os.environ.get("ORCHESTRATE_GH_LOG", "").lower() in ("off", "0", "false", "no"):
            return
        root = _log_root(cwd)
        if root is None:
            return
        session = os.environ.get(runlog.SESSION_ENV) or json.loads((root / STATE).read_text(encoding="utf-8")).get("session")
        if not isinstance(session, str) or not session:
            return
        record = {"ts": runlog._now(), "session": session, "kind": runlog.GH_CALL, "ref": None,
                  "script": Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""}
        if args is not None:
            record.update(describe(args))
        record.update(event)
        path = root / runlog.PROFILES / runlog.EVENTS_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:  # noqa: BLE001 - best effort by design
        pass


_last_used: dict = {}  # x-ratelimit-resource -> the `used` this process last saw, for the per-call delta


def _graphql_cost(stdout, headers):
    """Points one GraphQL call cost: `rateLimit.cost` when the query asked for it, else how far the bucket's
    `used` counter rose since this process's previous call (None for the first call, or without headers)."""
    try:
        cost = json.loads(stdout)["data"]["rateLimit"]["cost"]
        if isinstance(cost, int):
            return cost
    except (ValueError, KeyError, TypeError):
        pass
    used = _int(headers or {}, "x-ratelimit-used")
    resource = (headers or {}).get("x-ratelimit-resource")
    if used is None or resource is None:
        return None
    before, _last_used[resource] = _last_used.get(resource), used
    return used - before if before is not None and used >= before else None


def _log_attempt(args, cwd, attempt, ms, outcome, status, headers, stdout="") -> None:
    event = {"attempt": attempt, "outcome": outcome, "ms": ms, "status": status}
    if outcome == "ok" and args and args[0] == "api" and "graphql" in args:
        cost = _graphql_cost(stdout, headers)
        if cost is not None:
            event["cost"] = cost
    if headers is None:
        event["headers"] = "unavailable"  # a subcommand, or a paginated api call: gh shows none
    else:
        event.update(remaining=_int(headers, "x-ratelimit-remaining"), used=_int(headers, "x-ratelimit-used"),
                     retry_after=_int(headers, "retry-after"))
    _log(event, cwd, args)


def call(*args, cwd=None, idempotent: bool = False) -> str:
    """Run `gh <args>` and return stdout; raise GhError (missing, failed or ratelimited).

    Before the first try it waits out a reset another process recorded in STATE_FILE (or raises `ratelimited`
    when that would pass the wait budget). A secondary rate limit is then retried up to TRIES times, sleeping the
    `retry-after` seconds when gh shows them, else until the reset it just recorded (DEFAULT_PAUSE), and recording its reset in
    STATE_FILE for the others. The hourly quota (remaining 0, reset far off) raises `ratelimited` at once with the
    reset time, since waiting for it would stall the caller. A rate-limit message carries the HTTP status and headers.

    `gh api` runs with --include (paginated calls excepted): the headers are split off before the body is returned,
    and each attempt, with its status and rate-limit headers, is logged (see the call log above).

    A 5xx (HTTP 500-504) is retried on a short ladder only for a read or a write the caller marks `idempotent`
    (repeating it leaves the same state): a 5xx can come after GitHub applied a write, and repeating a creating
    POST, a merge or a delete-style batch could then act twice. An unmarked write fails at once on a 5xx and the
    caller decides (merge re-reads the state first). A rate limit is retried for every call: nothing ran.
    """
    _wait_for_shared_reset(cwd)
    # `gh api` prints the response headers only with --include; they carry the status and the rate-limit evidence.
    # A paginated reply holds one header block per page, so that one runs without them.
    api = bool(args) and args[0] == "api"
    include = api and not {"-i", "--include", "--paginate"} & set(args)
    argv = ("api", "--include", *args[1:]) if include else args
    for attempt in range(1, TRIES + 1):
        started = time.monotonic()
        p = common.run("gh", *argv, cwd=cwd)
        ms = int((time.monotonic() - started) * 1000)
        stdout, status, headers = p.stdout, None, None
        if include:
            status, headers, stdout = split_headers(p.stdout)
        if status is None:
            m = HTTP_STATUS.search(p.stderr or "")
            status = int(m.group(1)) if m else None
        if p.returncode == common.NOT_FOUND:
            raise GhError(MISSING, common.one_line(p.stderr))
        if p.returncode == 0:
            _log_attempt(args, cwd, attempt, ms, "ok", status, headers, stdout)
            return stdout
        text = f"{p.stderr}\n{p.stdout}"
        limit = _rate_limit(text)
        if limit is None:
            # The status line is the truth; the text is searched only when gh showed none, so a 4xx whose body
            # echoes "HTTP 502" is not retried.
            read = not describe(args)["write"]
            server = status in (500, 501, 502, 503, 504) if status is not None else bool(SERVER_ERROR_TEXT.search(text))
            server = server and (idempotent or read)
            # A truncated reply or a dropped connection shows no status; only a read repeats safely on it.
            server = server or (status is None and read and bool(TRANSIENT_TEXT.search(text)))
            _log_attempt(args, cwd, attempt, ms, "failed", status, headers)
            if not server:
                raise GhError(FAILED, common.one_line(p.stderr or stdout))
            message = common.one_line(_evidence(text))
            if attempt >= server_error_tries():
                raise GhError(SERVER_ERROR, message)
            pause = SERVER_ERROR_PAUSES[min(attempt, len(SERVER_ERROR_PAUSES)) - 1]
            what = f"HTTP {status or 502}" if status or SERVER_ERROR_TEXT.search(text) else "transient failure"
            print(f"github: {what}, retrying in {pause}s (try {attempt} of {TRIES})", file=sys.stderr)
            try:
                _wait_until(clock() + pause, cwd)
            except GhError as e:  # over the wait budget: say what failed, not only when to retry
                raise GhError(SERVER_ERROR, f"{message}; {e.message}")
            continue
        _log_attempt(args, cwd, attempt, ms, "ratelimited", status, headers)
        message = common.one_line(_evidence(text))
        if not api and not (RETRY_AFTER.search(text) or QUOTA_EMPTY.search(text) or QUOTA_RESET.search(text)):
            message += " [headers unavailable: gh shows none for this command]"
        after, reset, empty = limit
        if empty and reset is not None and reset - clock() > PRIMARY_RESET_AFTER:
            raise GhError(RATELIMITED, f"{message} (hourly quota used up; resets at {_utc(reset)})")
        # gh shows no headers on a plain failure, so a bare rate limit waits out the recorded reset (a minute),
        # not a short ladder that would burn all the tries inside one limit window.
        # The reset header names the hourly bucket unless the quota is empty: a secondary limit with quota left and no
        # retry-after waits a minute, never the (up to an hour away) reset.
        pause = after if after is not None else (reset - clock() if empty and reset is not None and reset > clock() else DEFAULT_PAUSE)
        _record_reset(cwd, clock() + pause)
        if attempt == TRIES:
            raise GhError(RATELIMITED, f"{message} (rate limit until {_utc(clock() + pause)})")
        # The reset is recorded above, so a wait over the budget is returned, not slept; the caller retries after it.
        print(f"github: rate limited, retrying in {pause}s (try {attempt} of {TRIES})", file=sys.stderr)
        _wait_until(clock() + pause, cwd)


def call_json(*args, shape=dict, cwd=None, idempotent: bool = False):
    """`call`, parsed as JSON of type `shape`; anything else is malformed."""
    try:
        data = json.loads(call(*args, cwd=cwd, idempotent=idempotent))
    except ValueError:
        raise GhError(MALFORMED, "gh returned malformed JSON") from None
    if not isinstance(data, shape):
        raise GhError(MALFORMED, f"gh returned JSON of the wrong shape (wanted {shape.__name__})")
    return data


# `gh pr view --json` field -> how the REST pull object answers it. Every `gh pr`, `gh issue` and `gh repo`
# subcommand runs on GraphQL underneath, so the plugin asks the REST endpoint for these instead (#449): a separate
# quota, and a `gh api` call is retried, logged and header-read like the rest.
# GitHub's `mergeable_state` (lower case) -> the GraphQL `mergeStateStatus` the scripts branch on (`merge` on DIRTY).
MERGE_STATE = {"clean": "CLEAN", "dirty": "DIRTY", "unstable": "UNSTABLE", "blocked": "BLOCKED", "behind": "BEHIND",
               "draft": "DRAFT", "has_hooks": "HAS_HOOKS", "unknown": "UNKNOWN"}


def _merge_state(pull: dict) -> str:
    return MERGE_STATE.get(str(pull.get("mergeable_state") or "unknown").lower(), "UNKNOWN")


def _mergeable(pull: dict) -> str:
    """MERGEABLE | CONFLICTING | UNKNOWN: REST's `mergeable` is true, false or null (still being computed)."""
    m = pull.get("mergeable")
    return "UNKNOWN" if m is None else ("MERGEABLE" if m else "CONFLICTING")


def _pr_state(pull: dict) -> str:
    return "MERGED" if pull.get("merged_at") else ("OPEN" if pull.get("state") == "open" else "CLOSED")


def _cross_repo(pull: dict) -> bool:
    """True for a fork's PR (a deleted fork leaves no head repo)."""
    head, base = (pull.get("head") or {}).get("repo"), (pull.get("base") or {}).get("repo")
    if not isinstance(head, dict):
        return True
    if isinstance(base, dict) and base.get("full_name"):
        return head.get("full_name") != base.get("full_name")
    return bool(head.get("fork"))


PR_FIELDS = {
    "title": lambda p: p.get("title"),
    "body": lambda p: p.get("body") or "",
    "state": _pr_state,
    "mergeable": _mergeable,
    "mergeStateStatus": _merge_state,
    "mergedAt": lambda p: p.get("merged_at"),
    "baseRefName": lambda p: (p.get("base") or {}).get("ref"),
    "headRefName": lambda p: (p.get("head") or {}).get("ref"),
    "headRefOid": lambda p: (p.get("head") or {}).get("sha"),
    "isCrossRepository": _cross_repo,
}
# These two have no REST form (the closing issues and the check rollup are GraphQL-only): one explicit query.
# The check contexts come 100 a page and are paged on `hasNextPage` (`$after`), up to PR_CHECK_PAGES pages: a CI
# verdict read from a partial list could pass a failing check, so a PR past the cap raises instead (fail closed,
# #461). A PR closing more than 100 issues raises the same way. The `detailsUrl` of a check run is carried so a
# reader (`watch-ci` stuck-job detection) sees the same fields as `gh pr list --json statusCheckRollup`.
GRAPHQL_PR_FIELDS = {"closingIssuesReferences", "statusCheckRollup"}
PR_CHECK_PAGES = 10
PR_QUERY = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    state headRefName headRefOid isCrossRepository
    closingIssuesReferences(first: 100) { pageInfo { hasNextPage } nodes { number } }
    commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor } nodes {
      __typename ... on CheckRun { name status conclusion detailsUrl } ... on StatusContext { context state } } } } } } }
  } }
}"""


def _pr_number(pr) -> str:
    return str(pr).lstrip("#")


def pr_view(pr, fields: str, cwd=None) -> dict:
    """The `gh pr view --json <fields>` dict of a PR, in gh's names and values, from one REST call (`pulls/{n}`);
    a field set that holds closingIssuesReferences or statusCheckRollup comes from one GraphQL query instead."""
    wanted = [f for f in fields.split(",") if f]
    if GRAPHQL_PR_FIELDS & set(wanted):
        return _pr_view_graphql(pr, wanted, cwd)
    unknown = [f for f in wanted if f not in PR_FIELDS]
    if unknown:
        raise ValueError(f"pr_view: no REST form for {', '.join(unknown)}")
    pull = call_json("api", f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}", cwd=cwd)
    return {f: PR_FIELDS[f](pull) for f in wanted}


def _pr_view_graphql(pr, wanted, cwd=None) -> dict:
    checks, after, seen = [], None, set()
    for _ in range(PR_CHECK_PAGES):
        args = ["api", "graphql", "-f", f"query={PR_QUERY}", "-F", "owner={owner}", "-F", "name={repo}",
                "-F", f"number={_pr_number(pr)}"]
        if after:
            args += ["-f", f"after={after}"]
        data = call_json(*args, cwd=cwd)
        try:
            node = data["data"]["repository"]["pullRequest"]
            if not isinstance(node, dict):
                raise TypeError
            commits = (node.get("commits") or {}).get("nodes") or []
            rollup = ((commits[0].get("commit") or {}).get("statusCheckRollup") or {}) if commits else {}
            contexts = rollup.get("contexts") or {}
            checks += [n for n in contexts.get("nodes") or [] if isinstance(n, dict) and n.get("__typename")]
            info = contexts.get("pageInfo") or {}
            after = info.get("endCursor") if info.get("hasNextPage") else None
        except (KeyError, TypeError, IndexError, AttributeError):
            raise GhError(MALFORMED, "gh returned an unexpected reply (pull request)") from None
        if "statusCheckRollup" not in wanted or not after:
            break
        if after in seen:
            raise GhError(MALFORMED, "gh returned a repeated page cursor (checks)")
        seen.add(after)
    else:
        if after:
            raise GhError(MALFORMED, f"the pull request has more than {PR_CHECK_PAGES * 100} checks; "
                                     "CI cannot be judged from a partial list")
    closing = node.get("closingIssuesReferences") or {}
    if "closingIssuesReferences" in wanted and (closing.get("pageInfo") or {}).get("hasNextPage"):
        raise GhError(MALFORMED, "the pull request closes more than 100 issues; the list is partial")
    full = dict(node, closingIssuesReferences=[n for n in closing.get("nodes") or [] if isinstance(n, dict)],
                statusCheckRollup=checks)
    unknown = [f for f in wanted if f not in full]
    if unknown:
        raise ValueError(f"pr_view: no GraphQL form for {', '.join(unknown)}")
    return {f: full[f] for f in wanted}


def _pages(path: str, cwd=None, max_pages: int = 10) -> list:
    """Every row of a REST list (`per_page=100` in `path`), page by page: each page is one logged, header-read call."""
    rows = []
    for page in range(1, max_pages + 1):
        got = call_json("api", f"{path}{'&' if '?' in path else '?'}page={page}", shape=list, cwd=cwd)
        rows += got
        if len(got) < 100:
            break
    return rows


def conversation_comments(pr, cwd=None) -> list:
    """The comments on a PR's conversation (REST `issues/{n}/comments`, the `gh pr comment` ones), as the host's rows
    (`body`, `user.login`, `author_association`), up to 1,000. A reader keeps a trusted author's only (ADR-0015)."""
    return [c for c in _pages(f"repos/{{owner}}/{{repo}}/issues/{_pr_number(pr)}/comments?per_page=100", cwd)
            if isinstance(c, dict)]


def recent_open_issues(cwd=None, pages: int = 3) -> list:
    """The newest open issues (REST rows with `number`, `title`, `body`, `author_association`; pull requests left out),
    up to `pages` x 100: where a follow-up ticket filed for a PR is looked for before another is created."""
    rows = _pages("repos/{owner}/{repo}/issues?state=open&sort=created&direction=desc&per_page=100", cwd, max_pages=pages)
    return [r for r in rows if isinstance(r, dict) and "pull_request" not in r]


def create_issue(title: str, body: str, labels, cwd=None) -> int:
    """File an issue (REST `POST issues`) and return its number. Not retried on a 5xx: a repeat could file it twice."""
    args = ["api", "--method", "POST", "repos/{owner}/{repo}/issues", "-f", f"title={title}", "-f", f"body={body}"]
    for label in labels:
        args += ["-f", f"labels[]={label}"]
    number = call_json(*args, cwd=cwd).get("number")
    if not isinstance(number, int):
        raise GhError(MALFORMED, "gh returned no issue number for the new issue")
    return number


def set_body(kind: str, number, body: str, cwd=None) -> None:
    """Replace the body of an issue (`kind` "issues") or a pull request (`kind` "pulls"), one REST PATCH."""
    call("api", "--method", "PATCH", f"repos/{{owner}}/{{repo}}/{kind}/{_pr_number(number)}", "-f", f"body={body}",
         cwd=cwd, idempotent=True)


def pr_diff(pr, cwd=None) -> str:
    """The unified diff of a PR (`pulls/{n}` with the diff media type)."""
    return call("api", "-H", "Accept: application/vnd.github.diff", f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}",
                cwd=cwd)


def current_pr_number(cwd=None) -> str:
    """The number of the PR for the checked-out branch ("" when there is none): the open one when there is one, else
    the newest closed or merged one (a worker resolving threads after its PR closed still needs it).

    The `head=<owner>:<branch>` filter finds a PR whose head is in the base repository. A fork's PR has the fork's
    owner there, so when the filter finds nothing the newest pulls are searched for the local HEAD commit, which
    a fork head shares. Only fork heads count: a same-repo PR at the same commit (a branch cut from the tip of
    another PR's branch) is that other PR's, not this branch's."""
    rows = call_json("api", "repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all&per_page=100", shape=list,
                     cwd=cwd)
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        p = common.run("git", "rev-parse", "HEAD", cwd=cwd)
        sha = p.stdout.strip() if p.returncode == 0 else ""
        if sha:
            rows = [r for r in call_json("api", "repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc"
                                                "&per_page=100", shape=list, cwd=cwd)
                    if isinstance(r, dict) and (r.get("head") or {}).get("sha") == sha and _cross_repo(r)]
    rows.sort(key=lambda r: r.get("state") != "open")  # stable: open first, then the host's order
    return str(rows[0].get("number", "")) if rows else ""


def behind_by(base: str, head: str, cwd=None) -> int:
    out = call("api", f"repos/{{owner}}/{{repo}}/compare/{base}...{head}", "--jq", ".behind_by", cwd=cwd)
    if not out.strip().isdigit():
        raise GhError(MALFORMED, f"behind_by is not a number: {common.one_line(out) or 'empty reply'}")
    return int(out.strip())


def default_branch(cwd=None) -> str:
    name = call_json("api", "repos/{owner}/{repo}", cwd=cwd).get("default_branch")
    if not isinstance(name, str) or not name:
        raise GhError(MALFORMED, "gh returned no default branch")
    return name


def merge_pr(pr, method: str, head_oid: str = "", cwd=None) -> None:
    """Merge the PR (`PUT pulls/{n}/merge`). `sha` makes a push after review a refusal (409), not a merge."""
    guard = ["-f", f"sha={head_oid}"] if head_oid else []
    try:
        call("api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}/merge", "-f", f"merge_method={method}",
             *guard, cwd=cwd)
    except GhError as e:
        # `gh pr merge` used to pre-check this. A retry after a 5xx that landed gets 405 "not mergeable" from REST:
        # the PR's own state is the truth, and MERGED is success.
        if e.kind == FAILED and re.search(r"\b405\b", e.message):
            try:
                if pr_view(pr, "state", cwd).get("state") == "MERGED":
                    return
            except GhError:
                pass
        raise


def update_branch(pr, cwd=None) -> None:
    """Merge the base into the PR's branch on the host (`PUT pulls/{n}/update-branch`)."""
    call("api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}/update-branch", cwd=cwd)


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
        # Idempotent: a repeat after a delete that landed reads as GONE below.
        call("api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{quoted}", cwd=cwd, idempotent=True)
    except GhError as e:
        if e.kind == FAILED and GONE.search(e.message):
            return "already gone"
        raise
    return "deleted"


def issue_state(issue, cwd=None) -> str:
    """OPEN or CLOSED (REST says `open` / `closed`)."""
    return str(call_json("api", f"repos/{{owner}}/{{repo}}/issues/{_pr_number(issue)}", cwd=cwd).get("state")).upper()


# Check results that block a merge: failed ones, and ones still going (a missing result counts as going).
CI_BAD = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE"}
CI_PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}


def ci_verdict(checks) -> tuple:
    """("pass" | "pending" | "failed", [names of the checks that decide it]) from a statusCheckRollup list.

    A PR with no checks at all is pending here, as `watch-ci` counts it; `scripts/merge` turns that into a pass
    when the head has no workflow files (`has_workflows`: a repo that cannot report checks is green). Unlike
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


def has_workflows(head: str, cwd=None) -> bool:
    """True when the tree at `head` holds a `.yml`/`.yaml` file under .github/workflows (one contents call).

    A 404 (no such directory), a file at the path or a listing with no workflow file is False; an error
    object or any other failure raises GhError."""
    try:
        out = call("api", f"repos/{{owner}}/{{repo}}/contents/.github/workflows?ref={head}", cwd=cwd)
    except GhError as e:
        if e.kind == FAILED and re.search(r"\(HTTP 404\)", e.message):
            return False
        raise
    try:
        entries = json.loads(out)
    except ValueError:
        raise GhError(MALFORMED, "gh returned malformed JSON") from None
    if isinstance(entries, dict):
        # An error object (rate limit, auth, 5xx) must not read as "no workflows": merge would then call an empty
        # check rollup green. Only a file at the path is a clean "none configured".
        if entries.get("type") == "file":
            print("github: .github/workflows is a file, not a directory", file=sys.stderr)
            return False
        raise GhError(MALFORMED, "contents reply: "
                      + common.one_line(str(entries.get("message") or "not a directory listing")))
    if not isinstance(entries, list):
        raise GhError(MALFORMED, "contents reply: not a directory listing")
    return any(isinstance(e, dict) and str(e.get("name", "")).lower().endswith((".yml", ".yaml")) for e in entries)


def issue_claim(issue, cwd=None) -> tuple:
    """(state, claimed) of an issue: claimed when it has an assignee (the claim, as the tracker doc says)."""
    data = call_json("api", f"repos/{{owner}}/{{repo}}/issues/{_pr_number(issue)}", cwd=cwd)
    return str(data.get("state")).upper(), bool(data.get("assignees"))


def close_issue(issue, cwd=None) -> None:
    call("api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/{_pr_number(issue)}", "-f", "state=closed",
         "-f", "state_reason=completed", cwd=cwd, idempotent=True)  # closing a closed issue changes nothing


def _pull(p: dict) -> dict:
    """A REST pull row in the names `gh pr list --json` used: what the callers read."""
    return {"number": p.get("number"), "headRefName": (p.get("head") or {}).get("ref"),
            "headRefOid": (p.get("head") or {}).get("sha"), "baseRefName": (p.get("base") or {}).get("ref"),
            "isDraft": bool(p.get("draft")), "isCrossRepository": _cross_repo(p), "mergedAt": p.get("merged_at")}


def open_prs(base: str, cwd=None) -> list:
    rows = _pages(f"repos/{{owner}}/{{repo}}/pulls?state=open&base={urllib.parse.quote(base, safe='')}&per_page=100",
                  cwd, max_pages=2)
    return [_pull(p) for p in rows if isinstance(p, dict)]


def open_prs_with_heads(cwd=None) -> list:
    """Every open PR (any base) with its number, branch and head commit: what a worker's report is checked against."""
    return [_pull(p) for p in _pages("repos/{owner}/{repo}/pulls?state=open&per_page=100", cwd, max_pages=2)
            if isinstance(p, dict)]


MERGED_PAGES = 10  # closed pages read at most (1,000 PRs), however many of them closed unmerged


def merged_prs(base, cwd=None, limit: int = 200) -> list:
    """The newest `limit` merged PRs (on `base`, or any base when it is None) by last update. Closed-unmerged PRs
    share the closed list, so it is read page by page until `limit` merged ones are held (or MERGED_PAGES pages are
    read, or the list ends): they cannot use up the window and push a merged PR out of it. An older merged PR is
    still caught by its closed issue (`Closes #n`)."""
    where = f"&base={urllib.parse.quote(base, safe='')}" if base else ""
    path = f"repos/{{owner}}/{{repo}}/pulls?state=closed{where}&sort=updated&direction=desc&per_page=100"
    merged = []
    for page in range(1, MERGED_PAGES + 1):
        got = call_json("api", f"{path}&page={page}", shape=list, cwd=cwd)
        merged += [_pull(p) for p in got if isinstance(p, dict) and p.get("merged_at")]
        if len(merged) >= limit or len(got) < 100:
            break
    return merged[:limit]


def pr_files(pr, cwd=None) -> list:
    """The paths a PR's diff changes."""
    rows = _pages(f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}/files?per_page=100", cwd, max_pages=30)
    if not all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in rows):
        raise GhError(MALFORMED, "gh returned no file list for the PR")
    return [f["filename"] for f in rows]


def repo_info(cwd=None) -> dict:
    """The repository's `owner`, `name` and `defaultBranchRef`, from one `GET repos/{owner}/{repo}`."""
    data = call_json("api", "repos/{owner}/{repo}", cwd=cwd)
    return {"owner": {"login": (data.get("owner") or {}).get("login")}, "name": data.get("name"),
            "defaultBranchRef": {"name": data.get("default_branch")}}


def open_issues(repo: dict, cwd=None) -> list:
    """Every open issue (all pages, oldest created first: creation time never changes on edit, and an issue created mid-read appends past the cursor, so no page shifts; an issue seen twice is kept once) as raw GraphQL nodes, the one home of the issues query:
    number, title, url, body, creation time, parent, sub-issue summary, labels, assignees (count), blockers, edits and comments.
    `repo` is `repo_info`'s (or `gh repo view`'s) owner/name dict."""
    try:
        owner, name = repo["owner"]["login"], repo["name"]
    except (KeyError, TypeError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    nodes, after, seen, numbers = [], None, set(), set()
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
        nodes += [n for n in page if not (isinstance(n, dict) and n.get("number") in numbers)]
        numbers.update(n["number"] for n in page if isinstance(n, dict) and "number" in n)
        info = issues.get("pageInfo") or {}
        after = info.get("endCursor") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("hasNextPage") and after):
            return nodes
        if after in seen:  # a cursor that does not advance would loop for ever
            raise GhError(MALFORMED, "gh returned a repeated page cursor (issues)")
        seen.add(after)


def _connection(query, variables, path, after=None, cwd=None, what: str = "review threads") -> list:
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
            raise GhError(MALFORMED, f"gh returned an unexpected reply ({what})") from None
        if not isinstance(page, list):
            raise GhError(MALFORMED, f"gh returned an unexpected reply ({what})")
        nodes += page
        info = conn.get("pageInfo") or {}
        after = info.get("endCursor") if info.get("hasNextPage") else None
        if not after:
            return nodes
        if after in seen:  # a cursor that does not advance would loop for ever
            raise GhError(MALFORMED, f"gh returned a repeated page cursor ({what})")
        seen.add(after)


def _comment(c: dict) -> dict:
    return {"author": (c.get("author") or {}).get("login") or "ghost", "body": str(c.get("body") or ""),
            "url": c.get("url"), "association": c.get("authorAssociation") or ""}


def review_threads(pr, cwd=None, trusted=None) -> list:
    """Every review thread of a PR, past 100 threads and past 50 comments in a thread:
    [{id, resolved, outdated, trusted, path, line (the diff line, else the original one for an outdated thread; None on a file-level thread), comments: [{author, association, body, url}]}].
    A thread is `trusted` when its first comment's author is (ADR-0015); `trusted` is the extra logins, read from the
    config when omitted. The one home of the threads read (`threads` and `merge` both use it)."""
    extra = trusted_logins(cwd) if trusted is None else trusted
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
        rows = [_comment(c) for c in comments if isinstance(c, dict)]
        for r in rows:  # per comment too: a stranger's reply on a trusted thread is not read either (ADR-0015)
            r["trusted"] = is_trusted(r["association"], r["author"], extra)
        out.append({"id": tid, "resolved": bool(n.get("isResolved")), "outdated": bool(n.get("isOutdated")),
                    "trusted": bool(rows) and is_trusted(rows[0]["association"], rows[0]["author"], extra),
                    "path": n.get("path") or "", "line": n.get("line") or n.get("originalLine"),
                    "comments": rows})
    return out


def pr_commits(pr, cwd=None) -> list:
    """[{"oid", "committedDate"}] for the commits on a PR's branch (what `gh pr view --json commits` listed), paged."""
    rows = _pages(f"repos/{{owner}}/{{repo}}/pulls/{_pr_number(pr)}/commits?per_page=100", cwd, max_pages=10)
    return [{"oid": c.get("sha"), "committedDate": ((c.get("commit") or {}).get("committer") or {}).get("date")}
            for c in rows if isinstance(c, dict)]


def pr_commit_shas(pr, cwd=None) -> list:
    """The commit shas on a PR's branch at the remote (the host's own list, paged)."""
    out = call("api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{str(pr).lstrip('#')}/commits",
               "--jq", ".[].sha", cwd=cwd)
    return out.split()


def abandoned_tickets(repo: dict, cwd=None) -> list:
    """Numbers of closed `wontfix` tickets that are still assigned (the query filters on the assignee, every page)."""
    try:
        owner, name = repo["owner"]["login"], repo["name"]
    except (KeyError, TypeError):
        raise GhError(MALFORMED, "gh returned no owner and name for the repository") from None
    nodes = _connection(ABANDON_QUERY, {"owner": owner, "name": name}, ("repository", "issues"), cwd=cwd,
                        what="closed issues")
    try:
        return sorted({int(n["number"]) for n in nodes if ((n.get("assignees") or {}).get("totalCount") or 0) > 0})
    except (KeyError, TypeError, ValueError, AttributeError):
        raise GhError(MALFORMED, "gh returned an unexpected reply (closed issues)") from None


def add_blocked_by(child, blocker, cwd=None) -> None:
    """Add the native `blocked_by` edge: issue `child` is blocked by issue `blocker` (the tracker doc's Blocking command)."""
    out = call("api", f"repos/{{owner}}/{{repo}}/issues/{blocker}", "--jq", ".id", cwd=cwd).strip()
    if not out.isdigit():
        raise GhError(MALFORMED, f"issue id is not a number: {common.one_line(out) or 'empty reply'}")
    call("api", "--method", "POST", f"repos/{{owner}}/{{repo}}/issues/{child}/dependencies/blocked_by",
         "-F", f"issue_id={out}", cwd=cwd)
