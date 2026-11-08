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
from harness import PR_GRAPHQL, ScriptEnv, git, gh_pulls, pr_graphql_reply  # noqa: E402


def pr(state, issues=(5,), branch="feat-br", cross=False):
    """merge's PR read: the explicit GraphQL query (closing issues and checks have no REST form)."""
    return {"stdout": pr_graphql_reply({"state": state, "headRefName": branch, "headRefOid": "abc123",
                                        "isCrossRepository": cross,
                                        "closingIssuesReferences": [{"number": n} for n in issues]})}


# The calls a scripted gh answers, as the argument lists `gh` gets (a "~" arg matches by prefix).
PR_VIEW = tuple(PR_GRAPHQL)
PR_MERGE = ("api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/merge")
UPDATE_BRANCH = ("api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/update-branch")
ISSUE_VIEW = ("api", "~repos/{owner}/{repo}/issues/")
ISSUE_CLOSE = ("api", "-X", "PATCH", "~repos/{owner}/{repo}/issues/")
REPO_VIEW = ("api", "repos/{owner}/{repo}")
OPEN_PULLS = ("api", "~repos/{owner}/{repo}/pulls?state=open")

OK = {"exit": 0}
OPEN_ISSUE = [{"stdout": '{"state":"open"}'}]
CLOSED_ISSUE = [{"stdout": '{"state":"closed"}'}]
REPO = [{"stdout": '{"default_branch":"main"}'}]
REF_GET = "api repos/{owner}/{repo}/git/ref/heads/feat-br"


class MergeMixin:
    """The merge CLI through the harness with a scripted gh, for a TestCase whose setUp made self.env."""

    def seq(self, steps):
        # {PR_VIEW: [reply, ...]}: the Nth matching call gets reply min(N, last). A string key is split on spaces.
        steps = {**steps}
        steps.setdefault(REPO_VIEW, REPO)
        steps.setdefault(("api", "-X", "DELETE"), [OK])
        self.env.set_gh([{"args": list(key) if isinstance(key, tuple) else key.split(), "seq": replies}
                         for key, replies in steps.items()])

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
        (self.env.repo / ".claude").mkdir(exist_ok=True)  # the harness stamps a state file here
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

    def sweep(self, method="squash", branches=("pr-a",), drafts=()):
        listing = gh_pulls([{"number": 8 + i, "headRefName": b, "isDraft": b in drafts}
                            for i, b in enumerate(branches)], "open")["stdout"]
        self.seq({PR_VIEW: [pr("MERGED")], ISSUE_VIEW: CLOSED_ISSUE, REF_GET: [OK],
                  OPEN_PULLS: [{"stdout": listing}]})
        return self.merge("--method", method)

    def remote(self, branch, path):
        git(self.env.repo, "fetch", "origin")
        return git(self.env.repo, "show", f"origin/{branch}:{path}") + "\n"

    def pushes(self):
        return [l for l in self.git_log.read_text().splitlines() if l.startswith("push")]

    def replays(self):
        return [(e["file"], e["outcome"]) for e in self.env.events() if e["kind"] == "replay"]
