"""Exit 2 (could not run) for unusable input in rebuild-state, guard-corpus and sandbox-git."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402


class CouldNotRunTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def check(self, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_rebuild_state_unknown_session(self):
        self.check(self.env.run("rebuild-state", "no-such-session", check=False))

    def test_guard_corpus_unknown_session(self):
        projects = self.env.repo.parent / "projects"
        projects.mkdir()
        self.check(self.env.run("guard-corpus", "no-such-session", "--projects-dir", projects, check=False))

    def test_sandbox_git_bad_usage(self):
        self.check(self.env.run("sandbox-git", "--bogus", check=False))


if __name__ == "__main__":
    unittest.main()
