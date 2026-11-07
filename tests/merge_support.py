"""Fixtures for the scripts/merge tests, shared by the test_merge*.py modules.

Not a test module: MergeMixin drives the merge CLI through the harness with a scripted gh;
SweepMixin adds a bare origin, a git that logs its calls, and the helpers the sweep tests use.
Mixins come first in a TestCase's bases so their setUp and helpers apply.
"""

import json
import os
import shutil
import sys
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402


def pr(state, issues=(5,), branch="feat-br", cross=False):
    return {"stdout": json.dumps({"state": state, "headRefName": branch, "headRefOid": "abc123",
                                  "isCrossRepository": cross,
                                  "closingIssuesReferences": [{"number": n} for n in issues]})}


OK = {"exit": 0}
OPEN_ISSUE = [{"stdout": '{"state":"OPEN"}'}]
CLOSED_ISSUE = [{"stdout": '{"state":"CLOSED"}'}]
REPO = [{"stdout": '{"defaultBranchRef":{"name":"main"}}'}]
REF_GET = "api repos/{owner}/{repo}/git/ref/heads/feat-br"


class MergeMixin:
    """The merge CLI through the harness with a scripted gh, for a TestCase whose setUp made self.env."""

    def seq(self, steps):
        steps = {"repo view": REPO, "api -X": [OK], **steps}
        # {"pr view": [reply, ...]}: the Nth matching call gets reply min(N, last).
        self.env.set_gh([{"args": key.split(), "seq": replies} for key, replies in steps.items()])

    def merge(self, *extra):
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def deletes(self):
        return self.called(["api", "-X", "DELETE"])

    def local_branch(self):
        return "feat-br" in git(self.env.repo, "branch", "--list", "feat-br")


# Logs every git call (to $GIT_LOG) before running the real one, so tests can see what was pushed.
GIT_WRAPPER = '''#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_LOG"
if [ "$1" = "--version" ] && [ -n "$FAKE_GIT_VERSION" ]; then echo "git version $FAKE_GIT_VERSION"; exit 0; fi
exec REAL_GIT "$@"
'''

CHANGELOG = "# Changes\n## Unreleased\n- old\n"
CONFIG = "## Shared files\n\n- **Shared files:**\n  - `CHANGELOG.md`: one line under Unreleased\n- **Generated:** none\n"


class SweepMixin(MergeMixin):
    """A bare origin and a logging git, plus the helpers the sweep tests share."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        git(self.env.repo, "branch", "feat-br")
        self.git_log = self.env.repo.parent / "git.log"
        wrapper = self.env.bin / "git"
        wrapper.write_text(GIT_WRAPPER.replace("REAL_GIT", shutil.which("git")))
        wrapper.chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"GIT_LOG": str(self.git_log)})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.env.repo / ".claude").mkdir()
        (self.env.repo / ".claude" / "orchestration.md").write_text(CONFIG)
        self.env.commit("CHANGELOG.md", CHANGELOG, "chore: changelog")
        self.env.commit("src.txt", "one\ntwo\n", "chore: src")
        git(self.env.repo, "push", "origin", "main")
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.git_log.write_text("")

    def pr_branch(self, name, files):
        git(self.env.repo, "checkout", "-q", "-b", name, self.base)
        for path, text in files.items():
            self.env.commit(path, text, f"feat: {name} {path}")
        git(self.env.repo, "push", "origin", name)
        git(self.env.repo, "checkout", "-q", "main")

    def land(self, files):
        """What the merged PR did to main."""
        for path, text in files.items():
            self.env.commit(path, text, f"feat: landed {path}")
        git(self.env.repo, "push", "origin", "main")

    def sweep(self, method="squash", branches=("pr-a",)):
        listing = json.dumps([{"number": 8 + i, "headRefName": b, "isCrossRepository": False}
                              for i, b in enumerate(branches)])
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "pr list": [{"stdout": listing}]})
        return self.merge("--method", method)

    def remote(self, branch, path):
        git(self.env.repo, "fetch", "origin")
        return git(self.env.repo, "show", f"origin/{branch}:{path}") + "\n"

    def pushes(self):
        return [l for l in self.git_log.read_text().splitlines() if l.startswith("push")]

    def replays(self):
        return [(e["file"], e["outcome"]) for e in self.env.events() if e["kind"] == "replay"]
