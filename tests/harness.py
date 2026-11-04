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
# response whose args are a prefix of the call (stdout, stderr, exit code).
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
for r in json.load(open(state))["responses"]:
    if args[: len(r["args"])] == r["args"]:
        sys.stdout.write(r.get("stdout", ""))
        sys.stderr.write(r.get("stderr", ""))
        sys.exit(r.get("exit", 0))
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
        """Canned replies: [{"args": ["pr", "view"], "stdout": "...", "exit": 0}, ...]; first prefix match wins."""
        self.gh_state.write_text(json.dumps({"responses": responses}))

    def gh_calls(self):
        calls = Path(str(self.gh_state) + ".calls")
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def events(self):
        log = self.repo / ".claude" / "orchestrate-profiles" / "events.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def run(self, script, *args, check=True):
        """Run a CLI (absolute path, or a name under scripts/) in the repo with the fake gh first on PATH."""
        path = Path(script)
        if not path.is_absolute():
            path = SCRIPTS / script
        # No -I: it drops a script's own directory from sys.path and would break `import common`.
        # No PYTHONPATH either: scripts run here exactly as they do for real.
        env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            FAKE_GH_STATE=str(self.gh_state),
        )
        return subprocess.run(
            [sys.executable, str(path), *map(str, args)],
            cwd=self.repo, env=env, capture_output=True, text=True, check=check,
        )
