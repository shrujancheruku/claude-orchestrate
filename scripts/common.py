"""Shared helpers for the plugin scripts: config sections, bounded output.

Stdlib only, Python 3.9+. A sibling script imports it with `import common`
(its own directory is already on sys.path).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

EXIT_OK, EXIT_VERDICT, EXIT_COULD_NOT_RUN = 0, 1, 2
NOT_FOUND = 127  # the shell's "command not found"; what run() reports for a missing binary

CONFIG = Path(".claude") / "orchestration.md"
TAIL_LINES = 40  # failure tails
LINE_WIDTH = 200  # one line per item, never wider
PLUGIN = "orchestrate:"  # the prefix of every agent type the plugin ships (hooks/guard.py can't import this and keeps its own)
WORKER = PLUGIN + "worker"
TICKET = re.compile(r"#(\d+)")


def config_section(repo, name: str) -> str | None:
    """Body of the config's `## <name>` section (case-insensitive), or None.

    A missing or unreadable config is None too, so callers degrade rather than crash.
    """
    try:
        text = (Path(repo) / CONFIG).read_text()
    except OSError:
        return None
    return section_of(text, name)


def section_of(text: str, name: str) -> str | None:
    """Lines under `## name` up to the next `## ` heading; `###` subsections stay inside."""
    want = name.strip().lower()
    body = None
    for line in text.splitlines():
        m = re.match(r"##(?!#)\s+(.*?)\s*$", line)
        if m:
            if body is not None:
                break
            if m.group(1).lower() == want:
                body = []
            continue
        if body is not None:
            body.append(line)
    return None if body is None else "\n".join(body).strip("\n")


class Run(NamedTuple):
    """What run() returns: unpacks as (code, stdout, stderr); `returncode` is an alias of `code`."""
    code: int
    stdout: str
    stderr: str

    @property
    def returncode(self) -> int:
        return self.code


def run(*cmd, cwd=None, env=None, input=None, timeout=None) -> Run:
    """Run a command (no shell) and return (code, stdout, stderr), never raising for a missing tool.

    A binary that is missing or not executable (OSError) is code 127 with the reason in stderr, so a
    caller treats "gh is not installed" like any failed call instead of crashing.
    `env` replaces the whole environment (pass `dict(os.environ, K=V)` to add to it); `input` is text for stdin.
    `timeout` (seconds) bounds the call: a command still running then is killed and reported as NOT_FOUND with the
    reason in stderr, the same fallback a missing binary gets, so a hung tool cannot hang its caller.
    """
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, input=input,
                           capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return Run(NOT_FOUND, "", f"{cmd[0]}: timed out after {timeout}s")
    except OSError as e:
        return Run(NOT_FOUND, "", f"{cmd[0]}: {e.strerror or e}")
    return Run(p.returncode, p.stdout, p.stderr)


def pr_ref(s) -> tuple:
    """(pr, ref) from a PR argument: `#12` and `12` both give ("12", 12); a non-number keeps its text as ref."""
    pr = str(s).lstrip("#")
    return pr, int(pr) if pr.isdigit() else pr


# --- the verdict line (ADR-0014, GLOSSARY.md) -------------------------------------------------------------------
# The last line of an orchestrator-facing script is `next: <word> [<args>]`, the word one of VERDICT_WORDS; a script
# that touched other PRs first prints one `also: <word> <args>` line each. The orchestrator applies the words; exit
# codes keep their numbers for tests and the guard. This dict is the one list: word -> the arguments it carries.
VERDICT_WORDS = {
    # merge, and the gate's hand-offs
    "frontier": "", "cleaned": "<pr>", "wait-ci": "<pr> <head>", "re-review": "<pr>", "send-back": "<pr>",
    "ask-user": "<why>", "retry": "", "left": "<pr> <why>",
    # gate
    "review": "<head>", "rerun-after-ci": "", "wait-older": "<pr> <older>",
    "wait-ticket": "<pr> <ticket>",
    # watch-ci: one `also:` per PR that settled, then `settled`
    "settled": "", "ci-pass": "<pr> <head>", "ci-fail": "<pr> <head>", "no-run": "<pr> <head>",
    "stuck": "<pr> <run id> <job id>", "head-not-reached": "<pr> <sha>", "gave-up": "<pr> <why>",
    # ci-rerun
    "rerun": "<pr> <head>", "already-running": "<pr>",
    # park-pr
    "parked": "<pr>", "unparked": "<pr>", "refused": "<why>",
    # transition: the tracker writes were made, or the ticket already read that way
    "transitioned": "<verb> <ticket>", "unchanged": "<verb> <ticket>",
    # sweep and fold
    "clean": "", "findings": "<n>", "due": "<n>", "not-due": "<n>", "folded": "<from> <into>",
    # frontier, snapshot, threads, lead-route, rescue: the run happened; rescue's live lock held by another session
    "done": "", "elsewhere": "",
    # worker-report: the classification of a worker's or reviewer's report
    "valid": "pr-opened <pr> <head> | stopped <stop-reason> [exhausted]", "interim": "", "not-a-stop": "",
    "not-a-pr-opened": "", "duplicate": "<verdict>", "missing-report": "pr <pr> <head> | none",
    "limit": "[<reset time>]", "error": "<gist>", "incomplete": "<why>",
    # profile.py record
    "recorded": "",
    # every script: a rate limit, and a run that could not happen
    "retry-after": "<time>", "could-not-run": "[<why>]",
}
RATE_LIMIT_TIME = re.compile(r"(?:rate limit until|resets at)\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)")


def _verdict_line(word, args) -> str:
    if word not in VERDICT_WORDS:
        raise ValueError(f"unknown verdict word {word!r}; the list is common.VERDICT_WORDS")
    return " ".join([word, *(str(a) for a in args if str(a) != "")])


def verdict(word, *args, exit_code) -> int:
    """Print the run's one `next: <word> <args>` line and return `exit_code`, for `return common.verdict(...)`."""
    print("next: " + _verdict_line(word, args), flush=True)
    return exit_code


def finish(run, argv, words, on_error=None) -> int:
    """Run a CLI's `run(argv)` and end it with the one `next:` line (ADR-0014), returning its exit code unchanged.
    `words` maps an exit code to its verdict, `(word, *args)` or a callable returning that tuple (read after the run,
    for a word that carries what the run found). Exit 2 is `retry-after <time>` when this process gave up on a rate
    limit, else `could-not-run`, with `<Class>: <message>` after it when the run crashed (the traceback goes to stderr,
    unless `on_error(e)` is given: it reports the crash itself, cleans up and returns the exit code). An exit code
    `words` lacks is `could-not-run`."""
    import github  # here, not at the top: github imports this module
    github.rate_limited_until = None
    crash = ""
    try:
        code = run(argv)
    except SystemExit as e:  # die() or an argparse usage error (2); --help (0) prints no verdict
        if e.code in (0, None):
            raise
        if not isinstance(e.code, int):
            print(e.code, file=sys.stderr)
        code = e.code if isinstance(e.code, int) else 2
    except Exception as e:  # a program error is could-not-run, never a decision
        if on_error is None:  # a script with its own handler says it in its own words (rescue: one stderr line)
            import traceback
            traceback.print_exc()
        crash = f"{type(e).__name__}: {one_line(e)}"
        code = on_error(e) if on_error else 2
    if code == 2:
        until = github.rate_limit_verdict(code)
        if until:
            return verdict("retry-after", until, exit_code=code)
        return verdict("could-not-run", crash, exit_code=code)
    word = words.get(code)
    if callable(word):
        word = word()
    if word is None:
        return verdict("could-not-run", exit_code=code)
    return verdict(*word, exit_code=code)


def also(word, *args) -> None:
    """Print one `also: <word> <args>` line: a PR other than the primary one that the run touched."""
    print("also: " + _verdict_line(word, args), flush=True)


def rate_limit_time(text) -> str | None:
    """The time a rate-limit message says to retry after (`rate limit until <time>`), or None when it is no rate limit."""
    m = RATE_LIMIT_TIME.search(str(text or ""))
    return m.group(1) if m else None


def die(message: str) -> None:
    """Exit with "could not run" (2): message on stderr, no traceback."""
    print(message, file=sys.stderr)
    sys.exit(EXIT_COULD_NOT_RUN)


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def read_jsonl(path):
    """Yield each JSON-object line of a file; unreadable lines, non-objects and a missing file are skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):  # callers call .get; a stray `[]` or `3` would crash them
                    yield rec
    except FileNotFoundError:
        return


def one_line(text, width: int = LINE_WIDTH) -> str:
    """Collapse whitespace to a single line, cut with an ellipsis past width."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def tail(text: str, n: int = TAIL_LINES) -> str:
    """Last n lines of text, with a marker line saying how many were dropped."""
    lines = text.rstrip("\n").splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join([f"... {len(lines) - n} earlier lines omitted"] + lines[-n:])


def ticket_of(description) -> int | None:
    """The ticket a dispatch description names: its first `#<n>` wins (the run skill recommends `Build #<ticket>`).

    None when there is no `#<n>`. The one place this rule lives; profile.py and rebuild-state both ask it.
    """
    m = TICKET.search(str(description or ""))
    return int(m.group(1)) if m else None


def blocks(record) -> list:
    """Content blocks of a conversation turn (a `user` or `assistant` record); [] for anything else.

    Other record types (api-request-blob) echo earlier turns' blocks, so only conversation turns count.
    """
    if record.get("type") not in ("user", "assistant"):
        return []
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    # A stray string or number in the list is not a block; callers call .get on each one.
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def find_transcript(arg, projects_dir=None) -> Path | None:
    """The transcript for a session: `arg` as a `.jsonl` path, else a session id under `<projects_dir>/*/`.

    projects_dir defaults to the Claude config dir's `projects`. None when nothing matches; the caller
    words its own error, since each script exits differently.
    """
    p = Path(arg).expanduser()
    if p.suffix == ".jsonl" and p.is_file():
        return p
    root = Path(projects_dir).expanduser() if projects_dir else claude_dir() / "projects"
    hits = sorted(root.glob(f"*/{arg}.jsonl"))
    return hits[0] if hits else None


def subagent_metas(main_path) -> dict:
    """toolUseId -> (agent file stem, metadata) for every subagent the harness recorded beside a session transcript.

    The stem is `agent-<id>`, which names the agent's own `<stem>.jsonl`. Unreadable or non-object
    metadata files are skipped.
    """
    out = {}
    for meta_path in (Path(main_path).with_suffix("") / "subagents").glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            out[meta.get("toolUseId")] = (meta_path.name[: -len(".meta.json")], meta)
    return out


# --- worker liveness (ADR 0009) ---------------------------------------------------------------------------------
# A ticket's old worker is live when the worktree on its branch is locked and the lock's session pid is running with
# the start time the lock records. The harness writes the reason `claude agent <id> (pid <n> start <time>)`.
LOCK_REASON = re.compile(r"\(pid (\d+) start ([A-Za-z]{3} [A-Za-z]{3} +\d+ \d\d:\d\d:\d\d \d{4})\)")
LSTART_FORMAT = "%a %b %d %H:%M:%S %Y"
START_TOLERANCE = 2  # seconds: the lock records whole seconds, and `ps` rounds its own


def parse_lock_reason(reason):
    """(pid, start as an aware UTC datetime) from a harness lock reason, or None when it doesn't parse.

    The lock's time is UTC; `ps` prints local time, so callers compare through `process_start`, which normalises."""
    m = LOCK_REASON.search(reason or "")
    if not m:
        return None
    try:
        start = datetime.strptime(" ".join(m.group(2).split()), LSTART_FORMAT)
    except ValueError:
        return None
    return int(m.group(1)), start.replace(tzinfo=timezone.utc)


def process_start(pid):
    """The aware UTC start time of a running process, or None when it isn't running (or `ps` can't say)."""
    r = run("ps", "-p", str(pid), "-o", "lstart=", env=dict(os.environ, LC_ALL="C"))
    text = " ".join(r.stdout.split())
    if r.code != 0 or not text:
        return None
    try:
        return datetime.strptime(text, LSTART_FORMAT).astimezone(timezone.utc)  # naive local time -> UTC
    except ValueError:
        return None


def worktrees(root) -> list:
    """[{"path", "branch" (short name or None), "detached" (bool), "locked" (reason, "" for none given, None when unlocked)}] from
    `git worktree list --porcelain`; [] when git can't list them."""
    r = run("git", "worktree", "list", "--porcelain", cwd=root)
    if r.code != 0:
        return []
    found, cur = [], None
    for line in r.stdout.splitlines() + [""]:
        if not line:
            if cur:
                found.append(cur)
            cur = None
        elif line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None, "detached": False, "locked": None}
        elif cur is not None and line == "detached":
            cur["detached"] = True
        elif cur is not None and line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif cur is not None and (line == "locked" or line.startswith("locked ")):
            cur["locked"] = line[len("locked "):].strip()
    return found


def ancestor_pids() -> set:
    """The pids of this process and every ancestor, read through `ps`; what is known when `ps` can't say. A lock
    naming one of these belongs to the session running this script (ADR 0009), so it is not another worker's."""
    found, pid = set(), os.getpid()
    while pid > 1 and pid not in found:
        found.add(pid)
        r = run("ps", "-o", "ppid=", "-p", str(pid))
        pid = int(r.stdout.strip()) if r.code == 0 and r.stdout.strip().isdigit() else 0
    return found


def worker_liveness(reason):
    """(state, pid or None) for a worktree's lock reason (None: unlocked); the one reader of a lock (ADR 0009).
    `ours`: a live lock naming this process or an ancestor (this session's own worker). `foreign-live`: a live lock
    of another session. `dead`: the pid isn't running, or the number was reused. `none`: no lock. A reason that
    doesn't parse is foreign-live: a second worker costs more than a ticket left waiting, and the pass shows the
    reason so the user can unlock a stale one."""
    if reason is None:
        return "none", None
    parsed = parse_lock_reason(reason)
    if parsed is None:
        return "foreign-live", None
    pid, start = parsed
    actual = process_start(pid)
    if actual is None or abs((actual - start).total_seconds()) > START_TOLERANCE:
        return "dead", pid
    return ("ours" if pid in ancestor_pids() else "foreign-live"), pid
