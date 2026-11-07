"""Facts about the repo the scripts share: where the state file lives, the branch convention, timestamps.

Stdlib only, Python 3.9+. A library: a CLI imports it with `import repo` (ADR-0003). It sits above
`common` (the process helpers) and below the CLIs, so each fact has one home instead of a copy per script.
The worktree-list parser stays in `common.worktrees`, which the liveness block there needs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import common
from common import CONFIG  # the config file's path, defined beside `config_section` which reads it

STATE = Path(".claude") / "orchestrate-state.json"
BRANCH_TICKET = re.compile(r"(\d+)-")  # the branch convention `<number>-<slug>`


def main_checkout(repo) -> Path:
    """The main checkout that owns the run log for `repo`, which may be a subdirectory or a linked worktree.

    A linked worktree shares the main checkout's common dir, so its parent is the one root every
    worktree agrees on. Outside a checkout (or with the VCS tool missing) `repo` itself is the root.

    A `repo` that no longer exists (a worktree the harness removed, whose transcript still names it) is asked
    from its nearest parent that exists, which is the main checkout or its `.claude` dir, so it resolves the same.

    Known limit: a bare repository with linked worktrees has a common dir not named `.git`, so each
    worktree is its own root there.
    """
    repo = Path(repo)
    start = next((p for p in (repo, *repo.parents) if p.is_dir()), repo)
    code, out, _ = common.run("git", "rev-parse", "--git-common-dir", cwd=start)
    if code != 0 or not out.strip():
        return repo
    common_dir = Path(out.strip())
    if not common_dir.is_absolute():
        common_dir = start / common_dir
    common_dir = common_dir.resolve()
    if common_dir.name == ".git":
        return common_dir.parent
    # A submodule or a GIT_DIR checkout: the common dir isn't `.git`, so use this checkout's own toplevel.
    code, out, _ = common.run("git", "rev-parse", "--show-toplevel", cwd=start)
    return Path(out.strip()) if code == 0 and out.strip() else repo


def ticket_of_branch(name) -> int | None:
    """The ticket number a branch named `<number>-<slug>` carries, or None."""
    m = BRANCH_TICKET.match(str(name or ""))
    return int(m.group(1)) if m else None


def ticket_branch(root, ticket):
    """The branch `<ticket>-...` checked out in some worktree, or None."""
    for wt in common.worktrees(root):
        if ticket_of_branch(wt["branch"]) == int(ticket):
            return wt["branch"]
    return None


def parse_ts(value) -> datetime | None:
    """An aware UTC-comparable datetime from an ISO string (`Z`, an offset or naive, which counts as UTC) or from
    epoch seconds or milliseconds; None for empty or unparseable input."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive value would raise TypeError against aware bounds; treat it as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def first_line(text) -> str:
    """The first non-blank line of text, stripped; "" for none."""
    return (text or "").lstrip().split("\n", 1)[0].strip()


def worktree_changes(path, base: str) -> list:
    """Every path the worktree at `path` has changed against its merge-base with the default branch: committed (pushed
    or not), staged, unstaged and untracked. A diff against the merge-base covers the tracked ones; `ls-files --others`
    adds the untracked. A failed git call raises ValueError (could not run): an empty answer would hold nothing."""
    ref = f"origin/{base}"
    if common.run("git", "rev-parse", "--verify", "-q", ref, cwd=path).code != 0:
        ref = base  # no remote-tracking ref (a clone that never fetched it): the local default branch
    mb = common.run("git", "merge-base", "HEAD", ref, cwd=path)
    if mb.code != 0 or not mb.stdout.strip():
        raise ValueError(f"cannot find the merge-base of {path} with {ref}")
    changed = common.run("git", "diff", "--name-only", "-z", mb.stdout.strip(), cwd=path)
    untracked = common.run("git", "ls-files", "--others", "--exclude-standard", "-z", cwd=path)
    if changed.code != 0 or untracked.code != 0:
        raise ValueError(f"cannot list the changes in {path}")
    return sorted({p for out in (changed.stdout, untracked.stdout) for p in out.split("\0") if p})


def harness_lock(path, cwd):
    """The lock reason when `path` is locked by the harness (`claude agent <id> (pid <n> start <time>)`), else None.

    Reuses common.parse_lock_reason; the pid is not checked for liveness: the lock is the orchestrator's own
    session's (ADR 0009), so a live pid proves nothing about the worker."""
    for wt in common.worktrees(cwd):
        # git runs in `cwd`, so a relative path means relative to it (an absolute path overrides it)
        if Path(wt["path"]).resolve() == (Path(cwd) / path).resolve():
            reason = wt["locked"]
            if reason and reason.startswith("claude agent ") and common.parse_lock_reason(reason):
                return reason
    return None


def remove_worktree(path, cwd):
    """Remove the merged PR's worktree and say what happened; never raises, never forces.

    A harness-locked tree is unlocked first, then removed plainly, so a dirty tree is still refused;
    if that refusal comes after the unlock, the lock is put back so the tree is left as found."""
    w = common.run("git", "worktree", "remove", path, cwd=cwd)
    err = common.one_line(w.stderr)
    if w.code == 0:
        return f"worktree {path} removed"
    if "not a working tree" in err:
        return f"worktree {path} already removed"
    reason = harness_lock(path, cwd) if "locked" in err else None
    if reason is None:
        return f"worktree {path} NOT removed: {err}"
    u = common.run("git", "worktree", "unlock", path, cwd=cwd)
    if u.code != 0:
        return f"worktree {path} NOT removed: could not unlock: {common.one_line(u.stderr)}"
    w = common.run("git", "worktree", "remove", path, cwd=cwd)
    if w.code == 0:
        return f"worktree {path} unlocked (harness lock) and removed"
    refused = common.one_line(w.stderr)
    l = common.run("git", "worktree", "lock", "--reason", reason, path, cwd=cwd)
    state = "harness lock kept" if l.code == 0 else f"harness lock NOT restored: {common.one_line(l.stderr)}"
    return f"worktree {path} NOT removed: {refused} ({state})"
