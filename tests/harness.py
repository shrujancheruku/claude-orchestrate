"""Test seam for the plugin scripts: a temp git repo, a bare `origin`, and a fake `gh`.

Scripts are driven only through their CLI. Assert on stdout, events.jsonl, the
repo and remote state, and the calls the fake `gh` recorded.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# Stands in for `gh`: records every call, then replies with the first canned
# response whose args are a prefix of the call (stdout, stderr, exit code). A response with a
# "seq" list replies with its Nth step (the last repeats) on the Nth call it matches.
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
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


class ScriptEnv:
    """Use as `env = ScriptEnv(); self.addCleanup(env.close)`."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        self.repo = root / "repo"
        self.bin = root / "bin"
        self.gh_state = root / "gh-state.json"
        git(root, "init", "--bare", "-b", "main", str(self.origin))
        git(root, "init", "-b", "main", str(self.repo))
        for k, v in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            git(self.repo, "config", k, v)
        git(self.repo, "remote", "add", "origin", str(self.origin))
        self.commit("README.md", "hello\n", "chore: init")
        git(self.repo, "push", "-u", "origin", "main")
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
        log = self.repo / ".claude" / "orchestrate-profiles" / "events.jsonl"
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
