"""CLI tests for scripts/sandbox-git."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402

CMDS = ["git fetch *", "git pull *", "git push *"]


class SandboxGitTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.settings = self.env.repo / ".claude" / "settings.local.json"

    def ssh(self, url="git@github.com:o/r.git"):
        git(self.env.repo, "remote", "set-url", "origin", url)

    def run_script(self):
        return self.env.run("sandbox-git", "--repo", self.env.repo, check=False)

    def test_creates_file_for_ssh_remote(self):
        self.ssh()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(self.settings.read_text())
        self.assertEqual(d["sandbox"]["excludedCommands"], CMDS)
        self.assertEqual(d["sandbox"]["network"]["allowedDomains"], ["github.com"])
        self.assertIn(".claude/settings.local.json", (self.env.repo / ".gitignore").read_text())

    def test_ssh_url_form(self):
        self.ssh("ssh://git@example.com:2222/o/r.git")
        self.run_script()
        d = json.loads(self.settings.read_text())
        self.assertEqual(d["sandbox"]["network"]["allowedDomains"], ["example.com"])

    def test_union_without_duplicates_and_keeps_other_keys(self):
        self.ssh()
        self.settings.parent.mkdir(exist_ok=True)
        self.settings.write_text(json.dumps({
            "model": "x",
            "sandbox": {"enabled": True, "excludedCommands": ["docker *", "git push *"],
                        "network": {"allowedDomains": ["github.com", "npmjs.org"]}},
        }))
        self.run_script()
        self.run_script()  # idempotent
        d = json.loads(self.settings.read_text())
        self.assertEqual(d["model"], "x")
        self.assertTrue(d["sandbox"]["enabled"])
        self.assertEqual(d["sandbox"]["excludedCommands"], ["docker *", "git push *", "git fetch *", "git pull *"])
        self.assertEqual(d["sandbox"]["network"]["allowedDomains"], ["github.com", "npmjs.org"])
        ignore = (self.env.repo / ".gitignore").read_text().splitlines()
        self.assertEqual(ignore.count(".claude/settings.local.json"), 1)

    def test_invalid_json_refused_and_untouched(self):
        self.ssh()
        self.settings.parent.mkdir(exist_ok=True)
        self.settings.write_text("{not json")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.settings.read_text(), "{not json")

    def test_non_ssh_remote_changes_nothing(self):
        self.ssh("https://github.com/o/r.git")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.env.repo / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
