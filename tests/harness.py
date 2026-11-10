"""Test seam for the plugin scripts: a temp git repo, a bare `origin`, and a fake `gh`.

Scripts are driven only through their CLI. Assert on stdout, the run log (through runlog), the
repo and remote state, and the calls the fake `gh` recorded.
"""

import json
import os
import atexit
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The scripts of the tree this file sits in, never another checkout's: a test run from a scratch worktree at an
# older commit must meet that commit's scripts (#480). ScriptEnv.run also drops the variables that could point
# elsewhere (PYTHONPATH, CLAUDE_PLUGIN_ROOT).
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LEAKY_ENV = ("PYTHONPATH", "CLAUDE_PLUGIN_ROOT")
sys.path.insert(0, str(SCRIPTS))
import runlog  # noqa: E402

# Stands in for `gh`: records every call, then replies with the first canned
# response whose args are a prefix of the call (stdout, stderr, exit code). A response with a
# "seq" list replies with its Nth step (the last repeats) on the Nth call it matches.
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
# github.py adds --include to every `gh api` call to read the response headers; a canned reply has none, and a
# test names the call without it.
if args[:2] == ["api", "--include"]:
    args = args[:1] + args[2:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
if "-" in args:  # a body or payload on stdin (`--body-file -`, `--input -`): keep it, tests assert on it
    with open(state + ".stdin", "a") as f:
        f.write(json.dumps({"args": args, "stdin": sys.stdin.read()}) + "\\n")
for i, r in enumerate(json.load(open(state))["responses"]):
    # A canned arg that starts with "~" matches any call arg that starts with the rest (a REST path with its query).
    want = r["args"]
    if len(args) >= len(want) and all(a.startswith(w[1:]) if w.startswith("~") else a == w for a, w in zip(args, want)):
        if "seq" in r:
            try:
                counts = json.load(open(state + ".counts"))
            except OSError:
                counts = {}
            n = counts.get(str(i), 0)
            counts[str(i)] = n + 1
            json.dump(counts, open(state + ".counts", "w"))
            r = r["seq"][min(n, len(r["seq"]) - 1)]
        sys.stdout.write(r.get("stdout", ""))
        sys.stderr.write(r.get("stderr", ""))
        sys.exit(r.get("exit", 0))
with open(state + ".unmatched", "a") as f:
    f.write(json.dumps(args) + "\\n")
sys.stderr.write("fake gh: no canned response for %r\\n" % (args,))
sys.exit(1)
'''


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


_TEMPLATE = None


def _template():
    """A repo with one pushed commit and its bare origin, built once per process and copied per test.

    Building it (two `git init`s, a commit, a push) cost more than most tests; a copy is a few files.
    """
    global _TEMPLATE
    if _TEMPLATE is None:
        tmp = tempfile.TemporaryDirectory()
        atexit.register(tmp.cleanup)
        root = Path(tmp.name)
        origin, repo = root / "origin.git", root / "repo"
        # Git's auto maintenance (after a commit or push) detaches and holds `objects/maintenance.lock` for a
        # moment; a copy of the template racing it fails with ENOENT (#415). Never start it: the -c flags cover
        # the build, and the config below keeps it off in every copy, so no test's git command starts it either.
        off = ("-c", "maintenance.auto=false", "-c", "gc.auto=0")
        git(root, *off, "init", "--bare", "-b", "main", str(origin))
        git(root, *off, "init", "-b", "main", str(repo))
        for d in (origin, repo):
            git(d, "config", "maintenance.auto", "false")
            git(d, "config", "gc.auto", "0")
        for k, v in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            git(repo, "config", k, v)
        git(repo, "remote", "add", "origin", str(origin))
        (repo / "README.md").write_text("hello\n")
        git(repo, "add", "README.md")
        git(repo, "commit", "-m", "chore: init")
        git(repo, "push", "-u", "origin", "main")
        # Every script a test runs stamps its events with the state file's session (#341), so each repo has one.
        # Gitignored, so it never shows in the repo's status.
        (repo / ".claude").mkdir()
        (repo / ".claude" / "orchestrate-state.json").write_text(json.dumps({"session": "test-session", "tickets": {}}))
        for d in (origin, repo / ".git"):  # sample hooks: dozens of files copied per test, never run
            shutil.rmtree(d / "hooks", ignore_errors=True)
            (d / "hooks").mkdir()  # tests install their own hooks here
        _TEMPLATE = root
    return _TEMPLATE


# Named canned `gh` replies, for ScriptEnv.set_gh. The plugin reads GitHub through REST (`gh api`, #449): these
# take the field names `gh pr view/list --json` used and answer in REST's.
def rest_pull(merged=False, **v):
    """A REST pull object from gh-style fields (state, mergeable, mergeStateStatus, headRefName, headRefOid,
    baseRefName, isDraft, isCrossRepository, mergedAt, title, body, number)."""
    state = v.get("state", "MERGED" if merged else "OPEN")
    cross = v.get("isCrossRepository", False)
    return {"number": v.get("number"), "title": v.get("title"), "body": v.get("body"),
            "state": "open" if state == "OPEN" else "closed",
            "merged_at": v.get("mergedAt") or ("2026-01-01T00:00:00Z" if state == "MERGED" else None),
            "draft": bool(v.get("isDraft", False)),
            "mergeable": {"MERGEABLE": True, "CONFLICTING": False}.get(v.get("mergeable", "UNKNOWN")),
            "mergeable_state": str(v.get("mergeStateStatus", "UNKNOWN")).lower(),
            "head": {"ref": v.get("headRefName"), "sha": v.get("headRefOid"),
                     "repo": {"full_name": "x/r" if cross else "o/r", "fork": cross}},
            "base": {"ref": v.get("baseRefName", "main"), "repo": {"full_name": "o/r"}}}


def gh_pulls(prs, state, **kw):
    """`GET pulls?state=<open|closed>` replying with `prs` (gh-style rows). Closed rows are merged ones."""
    rows = [rest_pull(merged=state == "closed", **p) for p in prs]
    return {"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls?state={state}"], "stdout": json.dumps(rows), **kw}


def gh_pr_view(n, view, **kw):
    """`GET pulls/<n>` replying with the REST form of the gh-style `view` fields."""
    return {"args": ["api", f"repos/{{owner}}/{{repo}}/pulls/{n}"], "stdout": json.dumps(rest_pull(**view)), **kw}


def pr_graphql_reply(view):
    """The explicit GraphQL pull-request query's reply for gh-style `view` fields (closingIssuesReferences, statusCheckRollup)."""
    node = {k: view[k] for k in ("state", "headRefName", "headRefOid", "isCrossRepository") if k in view}
    node["closingIssuesReferences"] = {"nodes": view.get("closingIssuesReferences") or []}
    contexts = [dict(c, __typename="CheckRun" if "status" in c else "StatusContext")
                for c in view.get("statusCheckRollup") or []]
    node["commits"] = {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]}
    return json.dumps({"data": {"repository": {"pullRequest": node}}})


# Prefix of PR_QUERY (the check-rollup read); THREADS_QUERY opens with the same variables, so the body is part of it.
PR_GRAPHQL = ["api", "graphql", "-f", "~query=query($owner: String!, $name: String!, $number: Int!, $after: String) {\n"
              "  repository(owner: $owner, name: $name) { pullRequest"]


def gh_repo_view(owner="o", name="r", default="main"):
    """`GET repos/{owner}/{repo}`: owner, name and default branch."""
    return {"args": ["api", "repos/{owner}/{repo}"],
            "stdout": json.dumps({"owner": {"login": owner}, "name": name, "default_branch": default})}


def gh_repo_rest(repo):
    """`GET repos/{owner}/{repo}` replying with the REST form of a gh-style `repo view` dict."""
    return {"args": ["api", "repos/{owner}/{repo}"], "stdout": json.dumps(
        {"owner": repo.get("owner"), "name": repo.get("name"),
         "default_branch": (repo.get("defaultBranchRef") or {}).get("name", "main")})}


def gh_default_branch(name="main"):
    """`GET repos/{owner}/{repo}` for the default branch."""
    return gh_repo_view(default=name)


def gh_pr_list(prs, *prefix):
    """The open (prefix "--state", "open") or merged pulls list replying with gh-style `prs`."""
    return gh_pulls(prs, "open" if "open" in prefix else "closed")


class Run:
    """A script run split at its verdict: `stdout` is what precedes the one closing `next:` line (ADR-0014), which is
    `verdict`. A run whose last line is no `next:` line, or that prints a second one, fails here, so a test that wraps
    `env.run` in it checks the line on every call."""

    def __init__(self, r):
        lines = r.stdout.splitlines(keepends=True)
        if not lines or not lines[-1].startswith("next: ") or any(l.startswith("next: ") for l in lines[:-1]):
            raise AssertionError(f"not exactly one closing next: line: {r.stdout!r}")
        self.returncode, self.stderr = r.returncode, r.stderr
        self.stdout, self.verdict = "".join(lines[:-1]), lines[-1].strip()


def verdict_runs(env, *scripts):
    """Make `env.run` return a Run (stdout without the closing `next:` line, plus `.verdict`) for the named scripts, or
    for every script when none is named."""
    run = env.run
    env.run = lambda script, *a, **k: Run(run(script, *a, **k)) if not scripts or script in scripts else run(script, *a, **k)
    return env


class ScriptEnv:
    """Use as `env = ScriptEnv(); self.addCleanup(env.close)`.

    Scripts run with ORCHESTRATE_GH_LOG=off, so the event log holds only what a test provokes; pass
    `gh_log=True` for a test of the `gh_call` profiling events themselves.
    """

    def __init__(self, gh_log=False):
        self.gh_log = gh_log
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        self.repo = root / "repo"
        self.bin = root / "bin"
        self.gh_state = root / "gh-state.json"
        template = _template()
        shutil.copytree(template / "origin.git", self.origin, symlinks=True)
        shutil.copytree(template / "repo", self.repo, symlinks=True)
        # The clone's config names the template's origin by absolute path: point it at this copy.
        cfg = self.repo / ".git" / "config"
        cfg.write_text(cfg.read_text().replace(str(template / "origin.git"), str(self.origin)))
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        # A `claude` that answers `agents --json [--all]` with the rows set_agents wrote, and fails when none were: a
        # script that asks which sessions exist never reaches the real CLI, and prunes nothing it can't see (#532).
        self.agents_file = root / "agents.json"
        claude = self.bin / "claude"
        claude.write_text(f'#!/bin/sh\nif [ "$1" = agents ] && [ -f "{self.agents_file}" ]; then cat "{self.agents_file}"; exit 0; fi\nexit 1\n')
        claude.chmod(0o755)
        self.set_gh([])

    def set_agents(self, *session_ids):
        """What `claude agents --json --all` lists: one row per session id."""
        self.agents_file.write_text(json.dumps([{"sessionId": i, "name": "x"} for i in session_ids]))

    def close(self):
        self._tmp.cleanup()

    def commit(self, path, content, message):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        git(self.repo, "add", path)
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def set_gh(self, responses):
        """Canned replies: [{"args": ["pr", "view"], "stdout": "...", "exit": 0}, ...]; first prefix match wins.

        {"args": [...], "seq": [{"stdout": ...}, ...]} replies with one step per matching call instead.
        """
        Path(str(self.gh_state) + ".counts").unlink(missing_ok=True)
        self.gh_state.write_text(json.dumps({"responses": responses}))

    def gh_calls(self):
        calls = Path(str(self.gh_state) + ".calls")
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def gh_unmatched(self):
        """Argument lists of gh calls no canned response matched (the fake exited 1 on them)."""
        f = Path(str(self.gh_state) + ".unmatched")
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def gh_stdin(self):
        """[{"args": [...], "stdin": "..."}] for each gh call that read stdin (`-` among its args)."""
        f = Path(str(self.gh_state) + ".stdin")
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def events(self, profiling=False):
        """The event log's rows; `gh_call` profiling rows are left out unless `profiling=True`."""
        log = runlog.log_path(self.repo, runlog.EVENTS_LOG)
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return rows if profiling else [r for r in rows if r.get("kind") != runlog.GH_CALL]

    def clear_events(self):
        """Forget the events so far (a test that runs a script in a loop, one fresh log per pass)."""
        runlog.log_path(self.repo, runlog.EVENTS_LOG).unlink(missing_ok=True)

    def _tmp_empty(self):
        empty = Path(self._tmp.name) / "empty-bin"
        empty.mkdir(exist_ok=True)
        return empty

    def run(self, script, *args, check=True, no_gh=False, cwd=None, extra_env=None):
        """Run a CLI (absolute path, or a name under scripts/) in `cwd` (default: the repo) with the fake gh first on PATH."""
        path = Path(script)
        if not path.is_absolute():
            path = SCRIPTS / script
        # No -I: it drops a script's own directory from sys.path and would break `import common`.
        # No PYTHONPATH either: scripts run here exactly as they do for real.
        # no_gh: a PATH that holds no `gh` at all (not even a real one), to test a missing tool.
        search_path = str(self._tmp_empty()) if no_gh else f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env = dict(
            {k: v for k, v in os.environ.items() if k not in LEAKY_ENV},
            PATH=search_path,
            FAKE_GH_STATE=str(self.gh_state),
            ORCHESTRATE_GH_5XX_TRIES="1",  # a fake gh's 5xx is a plain failure here; github.call's retry is tested in test_github_retry
        )
        if self.gh_log:
            env.pop("ORCHESTRATE_GH_LOG", None)
        else:
            env["ORCHESTRATE_GH_LOG"] = "off"
        env.update(extra_env or {})  # e.g. ORCHESTRATE_GH_5XX_TRIES=2 for a test of a retry
        return subprocess.run(
            [sys.executable, str(path), *map(str, args)],
            cwd=cwd or self.repo, env=env, capture_output=True, text=True, check=check,
        )
