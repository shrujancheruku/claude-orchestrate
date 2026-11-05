"""Shared-file replay and the post-merge sweep (used by `merge`; stdlib only, Python 3.9+).

A shared file is one many PRs edit in the same place (a changelog, an index). When two such edits
collide, a human resolving it adds nothing, so the script does it:

* replay: start from main's version of the file and replay the lines the PR added or changed. Two
  sides inserting at the same spot keep both, main's lines first.
* word merge: a line changed on both sides goes through `git merge-file` with one word per line. It is
  accepted only when git reports no conflict, i.e. the two sides' changes are disjoint.
* a conflicting generated file takes main's side, then the glob's regenerate command runs in the
  worktree; a failing command hands the file back.
* anything else is handed back, and so is a conflict in a file that is neither shared nor generated.
* afterwards the result's diff against main must contain only lines the PR added or changed.

The sweep runs after a merge: every other open PR whose branch now conflicts with the default branch
is updated in a throwaway worktree (merge main into it, or rebase it, per the config's merge method)
and pushed. Nothing is force-pushed except `--force-with-lease` after a rebase.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from typing import NamedTuple
from pathlib import Path

import checks
import common

LINE_REPLAY, WORD_MERGE, HANDED_BACK = "line-replay", "word-merge", "handed-back"
REGENERATED = "regenerated"  # a generated file rebuilt by its glob's command (profile.py counts this name)
MAX_REBASE_STEPS = 200
REGENERATE_TIMEOUT = 300  # seconds a regenerate command may run before it is handed back


def shared_patterns(section):
    """Paths (or globs) listed under the `**Shared files:**` item of the config's Shared files section.

    The item's nested bullets each start with the path in backticks: `- \\`CHANGELOG.md\\`: <rule>`.
    """
    patterns, inside = [], False
    for line in (section or "").splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            inside = bool(re.match(r"-\s+\*\*Shared files:?\*\*", line))
            continue
        m = re.match(r"\s+[-*]\s+`([^`]+)`", line)
        if inside and m:
            patterns.append(m.group(1))
    return patterns


def generated_commands(section):
    r"""(glob, regenerate command) pairs under the `**Generated:**` item of the Shared files section.

    Each nested bullet is `- \`<glob>\`: \`<command>\``. `none` or free text after the item gives no pairs.
    """
    pairs, inside = [], False
    for line in (section or "").splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            inside = bool(re.match(r"-\s+\*\*Generated:?\*\*", line))
            continue
        m = re.match(r"\s+[-*]\s+`([^`]+)`\s*:\s*`([^`]+)`", line)
        if inside and m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def is_shared(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def _changes(base, other, who):
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, who, other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _apply(base, changes, lo, hi):
    """base[lo:hi] with one side's changes (all inside [lo, hi]) applied."""
    out, pos = [], lo
    for i1, i2, _, lines in sorted(changes):
        out += base[pos:i1] + lines
        pos = i2
    return out + base[pos:hi]


def _words(lines):
    return re.findall(r"\s+|\S+", "".join(lines))


def _word_merge(base, main, pr):
    """Text of a clean word-level 3-way merge, or None. One word per line, JSON-quoted so a newline survives."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, lines in (("main", main), ("base", base), ("pr", pr)):
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write("".join(json.dumps(w) + "\n" for w in _words(lines)))
            paths.append(p)
        r = common.run("git", "merge-file", "-p", *paths)
    if r.returncode != 0:
        return None
    try:
        return "".join(json.loads(x) for x in r.stdout.splitlines())
    except ValueError:
        return None


def replay(base, main, pr):
    """Replay the PR's edits (base -> pr) onto main. Returns (text, outcome); text is None when handed back."""
    base_l, main_l, pr_l = (x.splitlines(keepends=True) for x in (base, main, pr))
    changes = sorted(_changes(base_l, main_l, "main") + _changes(base_l, pr_l, "pr"))
    clusters = []  # touching or overlapping changes travel together
    for c in changes:
        if clusters and c[0] <= clusters[-1]["hi"]:
            clusters[-1]["cs"].append(c)
            clusters[-1]["hi"] = max(clusters[-1]["hi"], c[1])
        else:
            clusters.append({"lo": c[0], "hi": c[1], "cs": [c]})
    out, pos, outcome = [], 0, LINE_REPLAY
    merged_new, merged_pr = Counter(), Counter()  # only word-merged clusters are exempt from the line check
    for cl in clusters:
        lo, hi, cs = cl["lo"], cl["hi"], cl["cs"]
        out += base_l[pos:lo]
        pos = hi
        m = [c for c in cs if c[2] == "main"]
        p = [c for c in cs if c[2] == "pr"]
        if not (m and p):
            out += _apply(base_l, cs, lo, hi)
            continue
        main_t, pr_t = _apply(base_l, m, lo, hi), _apply(base_l, p, lo, hi)
        if all(c[0] == c[1] for c in cs):  # both sides inserted at one point: keep both, main's first
            out += main_t if main_t == pr_t else main_t + pr_t
        elif main_t == pr_t:
            out += main_t
        else:
            merged = _word_merge(base_l[lo:hi], main_t, pr_t)
            if merged is None:
                return None, HANDED_BACK
            out += merged.splitlines(keepends=True)
            merged_new.update(merged.splitlines(keepends=True))
            merged_pr.update(pr_t)
            outcome = WORD_MERGE
    out += base_l[pos:]
    result = "".join(out)
    return (result, outcome) if _only_pr_lines(base_l, main_l, pr_l, result.splitlines(keepends=True),
                                                merged_new, merged_pr) else (None, HANDED_BACK)


def _only_pr_lines(base_l, main_l, pr_l, result_l, merged_new, merged_pr):
    """The result's diff against main holds only lines the PR added or changed, and all of them."""
    pr_added = Counter(l for _, _, _, ls in _changes(base_l, pr_l, "pr") for l in ls)
    added = Counter()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, main_l, result_l, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            added.update(result_l[j1:j2])
    # Word-merged clusters produce new text, so their lines are allowed and their PR lines not required.
    in_result = set(result_l)  # built once, not per PR line
    return not (added - pr_added - merged_new) and all(l in in_result for l in pr_added if l not in merged_pr)


# ---- git plumbing -------------------------------------------------------------------------------

def git(cwd, *args):
    return common.run("git", *args, cwd=cwd)


def _stage(cwd, n, path):
    # Stays on subprocess: it needs the blob as bytes, and common.run decodes with errors="replace",
    # which would hide the non-UTF-8 case this function maps to None.
    r = subprocess.run(["git", "show", f":{n}:{path}"], cwd=cwd, capture_output=True)
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _regenerate(wt, cmd, generated, resolved):
    """Run a regenerate command in wt. Returns None on success, else why it failed.

    Files it wrote (unstaged changes; the merge's own changes are already staged) that match a generated
    glob join `resolved` to be staged; a change anywhere else fails the command.
    """
    r = checks.run_shell(cmd, wt, REGENERATE_TIMEOUT)
    if r.timed_out:
        return f"timed out after {REGENERATE_TIMEOUT}s"
    if r.code != 0:
        return common.one_line(r.stderr or r.stdout) or f"exit {r.code}"
    fields = git(wt, "status", "--porcelain", "-z", "-uall").stdout.split("\0")
    extra, stray = [], []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        xy, path = entry[:2], entry[3:]
        if xy[:1] in ("R", "C"):
            i += 1  # a rename or copy carries its source path as a second field
        if not path or xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU") or xy[1] == " ":
            continue
        (extra if any(fnmatch.fnmatchcase(path, g) for g, _ in generated) else stray).append(path)
    if stray:
        return f"changed {', '.join(stray)}, outside the generated globs"
    resolved.update({path: None for path in extra})
    return None


def resolve_conflicts(wt, patterns, pr_stage, generated=()):
    """Resolve the shared files conflicted in wt. Returns (resolved paths, conflicts, events).

    pr_stage is the index stage holding the PR's side: 2 during a merge (the branch is "ours"), 3
    during a rebase (the PR's commit is "theirs"). Nothing is staged when any conflict is left.
    generated is a list of (glob, command): a conflicting file matching a glob gets main's side and
    then the command runs in wt. Its value in `resolved` is None (already on disk).
    """
    out = git(wt, "diff", "--name-only", "--diff-filter=U", "-z").stdout
    files = [f for f in out.split("\0") if f]
    main_stage = 5 - pr_stage
    resolved, conflicts, events = {}, [], []
    ran = {}  # command -> error text or None; a command shared by several globs runs once
    for path in files:
        if not is_shared(path, patterns):
            cmd = next((c for g, c in generated if fnmatch.fnmatchcase(path, g)), None)
            if cmd is None:
                conflicts.append((path, "not a shared file"))
                continue
            main = _stage(wt, main_stage, path)
            if main is None:
                events.append((path, HANDED_BACK))
                conflicts.append((path, "deleted or not text on one side"))
                continue
            Path(wt, path).write_text(main, encoding="utf-8")  # main's side, then rebuild it from the sources
            if cmd not in ran:
                ran[cmd] = _regenerate(wt, cmd, generated, resolved)
            if ran[cmd] is not None:
                events.append((path, HANDED_BACK))
                conflicts.append((path, f"regenerate command failed: {cmd} ({ran[cmd]})"))
            else:
                events.append((path, REGENERATED))
                resolved[path] = None
            continue
        base, main, pr = _stage(wt, 1, path), _stage(wt, main_stage, path), _stage(wt, pr_stage, path)
        if main is None or pr is None:  # deleted or binary on one side
            events.append((path, HANDED_BACK))
            conflicts.append((path, "deleted or not text on one side"))
            continue
        text, outcome = replay(base or "", main, pr)
        events.append((path, outcome))
        if text is None:
            conflicts.append((path, "changed on both sides in overlapping words"))
        else:
            resolved[path] = text
    return resolved, conflicts, events


def _finish(wt, resolved):
    for path, text in resolved.items():
        if text is not None:
            Path(wt, path).write_text(text, encoding="utf-8")
    if resolved:
        git(wt, "add", "--", *resolved)


def _update_branch(repo, base, branch, method, patterns, generated=()):
    """Bring origin/<branch> up to date with origin/<base> in a throwaway worktree and push it.

    Returns (status, conflicts, events): status "updated" or "handed-back"; conflicts is a list of
    (path, why); events is a list of (path, outcome) to log.
    """
    tmp = tempfile.mkdtemp(prefix="orchestrate-sweep-")
    wt = os.path.join(tmp, "wt")
    try:
        old = git(repo, "rev-parse", f"origin/{branch}").stdout.strip()
        if git(repo, "worktree", "add", "--detach", wt, f"origin/{branch}").returncode != 0:
            return "handed-back", [("(worktree)", "could not check out the branch")], []
        events = []
        if method == "rebase":
            p = git(wt, "rebase", f"origin/{base}")
            for _ in range(MAX_REBASE_STEPS):
                if p.returncode == 0:
                    break
                resolved, conflicts, ev = resolve_conflicts(wt, patterns, pr_stage=3, generated=generated)
                events += ev
                if conflicts or not resolved:
                    git(wt, "rebase", "--abort")
                    return "handed-back", conflicts or [("(rebase)", p.stderr.strip()[-200:])], _final(events)
                _finish(wt, resolved)
                p = common.run("git", "rebase", "--continue", cwd=wt, env=dict(os.environ, GIT_EDITOR="true"))
            else:
                git(wt, "rebase", "--abort")
                return "handed-back", [("(rebase)", "did not finish")], _final(events)
        else:
            p = git(wt, "merge", "--no-edit", f"origin/{base}")
            if p.returncode != 0:
                resolved, conflicts, events = resolve_conflicts(wt, patterns, pr_stage=2, generated=generated)
                if conflicts or not resolved:
                    git(wt, "merge", "--abort")
                    return "handed-back", conflicts or [("(merge)", p.stderr.strip()[-200:])], _final(events)
                _finish(wt, resolved)
                c = common.run("git", "commit", "--no-edit", cwd=wt, env=dict(os.environ, GIT_EDITOR="true"))
                if c.returncode != 0:
                    git(wt, "merge", "--abort")
                    return "handed-back", [("(merge)", c.stderr.strip()[-200:])], _final(events)
        # Plain push for a merge (a fast-forward); a rebase rewrites history, so it leases the old tip.
        force = [f"--force-with-lease=refs/heads/{branch}:{old}"] if method == "rebase" else []
        push = git(wt, "push", *force, "origin", f"HEAD:refs/heads/{branch}")
        if push.returncode != 0:
            # The replays happened and are worth logging even though nothing reached the remote.
            return "handed-back", [("(push)", common.one_line(push.stderr))], events
        return "updated", [], events
    finally:
        git(repo, "worktree", "remove", "--force", wt)
        shutil.rmtree(tmp, ignore_errors=True)


def _final(events):
    """On an abort only the hand-backs are worth logging: the other replays were thrown away."""
    return [e for e in events if e[1] == HANDED_BACK]


MERGE_TREE_GIT = (2, 38)  # `git merge-tree --write-tree` first shipped in git 2.38


def _git_version(repo):
    """git's (major, minor) as a tuple, or None when it can't be read."""
    r = git(repo, "--version")
    m = re.search(r"(\d+)\.(\d+)", r.stdout) if r.returncode == 0 else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def _conflicts_with(repo, base, branch):
    """True when origin/<branch> no longer merges cleanly into origin/<base>; None when git can't say."""
    r = git(repo, "merge-tree", "--write-tree", f"origin/{base}", f"origin/{branch}")
    return {0: False, 1: True}.get(r.returncode)


# ---- the sweep's entry point --------------------------------------------------------------------

class Rules(NamedTuple):
    """What the config's Shared files section says: which files replay may resolve, and how to rebuild generated ones."""
    patterns: list
    generated: list  # (glob, regenerate command) pairs

    @classmethod
    def from_section(cls, section):
        return cls(shared_patterns(section), generated_commands(section))


class SweepResult(NamedTuple):
    """What sweep_branch did to one branch.

    status: "updated" (pushed), "handed-back" (left as it was; `conflicts` says why), "clean" (does not
    conflict, so left alone), "unchecked" (git could not say, left alone) or "skipped" (git is too old
    to check; `note` says so, and no later branch can be checked either).
    events: (path, outcome) pairs to log as `replay` events, also when the push failed.
    """
    status: str
    events: tuple = ()
    conflicts: tuple = ()  # (path, why)
    note: str = ""

    def handback_lines(self, label):
        return [f"HANDBACK {label}: {path} ({why})" for path, why in self.conflicts]


def sweep_branch(repo, base, branch, method, rules):
    """Update origin/<branch> if it no longer merges cleanly into origin/<base>. Never raises.

    Hides the git version gate, the conflict check, the throwaway worktree, the resolution order and
    the push. The caller has fetched; `rules` is a Rules.
    """
    version = _git_version(repo)  # unreadable means try anyway
    if version is not None and version < MERGE_TREE_GIT:
        return SweepResult("skipped", note="sweep skipped: it needs git >= %d.%d (git merge-tree --write-tree); "
                           "this is git %d.%d" % (MERGE_TREE_GIT + version))
    state = _conflicts_with(repo, base, branch)
    if state is None:
        return SweepResult("unchecked")
    if not state:
        return SweepResult("clean")
    try:
        status, conflicts, events = _update_branch(repo, base, branch, method, rules.patterns, rules.generated)
    except Exception as e:  # the merged PR is already in: one branch's failure must not lose the exit code
        status, conflicts, events = "handed-back", [("(sweep)", f"{type(e).__name__}: {common.one_line(e)}")], []
    return SweepResult(status, events, conflicts)
