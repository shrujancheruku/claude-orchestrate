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

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
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
    if args[: len(r["args"])] == r["args"]:
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
        git(root, "init", "--bare", "-b", "main", str(origin))
        git(root, "init", "-b", "main", str(repo))
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


# Named canned `gh` replies, for ScriptEnv.set_gh.
def gh_repo_view(owner="o", name="r"):
    """`gh repo view --json owner,name`."""
    return {"args": ["repo", "view"], "stdout": json.dumps({"owner": {"login": owner}, "name": name})}


def gh_default_branch(name="main"):
    """`gh repo view --json defaultBranchRef`."""
    return {"args": ["repo", "view"], "stdout": json.dumps({"defaultBranchRef": {"name": name}})}


def gh_pr_list(prs, *prefix):
    """`gh pr list` (optionally only calls starting with `prefix`, e.g. "--state", "open") replying with `prs`."""
    return {"args": ["pr", "list", *prefix], "stdout": json.dumps(prs)}


class ScriptEnv:
    """Use as `env = ScriptEnv(); self.addCleanup(env.close)`."""

    def __init__(self):
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
        self.set_gh([])

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

    def events(self):
        log = runlog.log_path(self.repo, runlog.EVENTS_LOG)
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def _tmp_empty(self):
        empty = Path(self._tmp.name) / "empty-bin"
        empty.mkdir(exist_ok=True)
        return empty

    def run(self, script, *args, check=True, no_gh=False, cwd=None):
        """Run a CLI (absolute path, or a name under scripts/) in `cwd` (default: the repo) with the fake gh first on PATH."""
        path = Path(script)
        if not path.is_absolute():
            path = SCRIPTS / script
        # No -I: it drops a script's own directory from sys.path and would break `import common`.
        # No PYTHONPATH either: scripts run here exactly as they do for real.
        # no_gh: a PATH that holds no `gh` at all (not even a real one), to test a missing tool.
        search_path = str(self._tmp_empty()) if no_gh else f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env = dict(
            os.environ,
            PATH=search_path,
            FAKE_GH_STATE=str(self.gh_state),
        )
        return subprocess.run(
            [sys.executable, str(path), *map(str, args)],
            cwd=cwd or self.repo, env=env, capture_output=True, text=True, check=check,
        )
