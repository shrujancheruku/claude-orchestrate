"""CLI tests for scripts/sandbox-git."""

import json
import stat
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
        # The developer's global excludes may already cover the path.
        git(self.env.repo, "config", "core.excludesFile", "/dev/null")
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
        self.assertEqual(d["sandbox"]["excludedCommands"], CMDS)
        self.assertNotIn("2222", json.dumps(d))
        self.assertNotIn("git@", json.dumps(d))

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

    def assert_refused(self, content):
        self.ssh()
        self.settings.parent.mkdir(exist_ok=True)
        self.settings.write_text(content)
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.settings.read_text(), content)

    def test_refuses_non_object_root(self):
        self.assert_refused("[1, 2]")

    def test_refuses_non_object_sandbox(self):
        self.assert_refused('{"sandbox": "on"}')

    def test_refuses_non_list_excluded_commands(self):
        self.assert_refused('{"sandbox": {"excludedCommands": "git"}}')

    def test_no_origin_exits_nonzero_with_the_git_error(self):
        git(self.env.repo, "remote", "remove", "origin")
        r = self.run_script()
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read origin", r.stderr)
        self.assertFalse(self.settings.exists())

    def test_outside_a_repo_exits_nonzero(self):
        r = self.env.run("sandbox-git", "--repo", self.env.repo.parent, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read origin", r.stderr)

    def test_unknown_argument_is_bad_usage(self):
        r = self.env.run("sandbox-git", "--bogus", check=False)
        self.assertEqual(r.returncode, 2)

    def test_non_ssh_origin_changes_nothing(self):
        self.ssh("https://github.com/o/r.git")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.settings.exists())

    def test_non_utf8_settings_file_is_refused_without_a_traceback(self):
        self.ssh()
        self.settings.parent.mkdir(exist_ok=True)  # the harness stamps a state file in .claude
        self.settings.write_bytes(b"\xff\xfe not utf-8")
        r = self.run_script()
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1)

    def domains(self):
        return json.loads(self.settings.read_text())["sandbox"]["network"]["allowedDomains"]

    def test_ipv6_hosts_lose_brackets(self):
        for url in ("ssh://u@[::1]:22/p", "git@[::1]:o/r"):
            self.settings.unlink() if self.settings.exists() else None
            self.ssh(url)
            self.assertEqual(self.run_script().returncode, 0)
            self.assertEqual(self.domains(), ["::1"], url)

    def test_scp_like_without_user(self):
        self.ssh("github.com:o/r")
        self.run_script()
        self.assertEqual(self.domains(), ["github.com"])

    def test_gitignore_already_covering_is_left_alone(self):
        self.ssh()
        (self.env.repo / ".gitignore").write_text(".claude/\n")
        self.run_script()
        self.assertEqual((self.env.repo / ".gitignore").read_text(), ".claude/\n")

    def test_gitignore_without_trailing_newline(self):
        self.ssh()
        (self.env.repo / ".gitignore").write_text("dist")
        self.run_script()
        self.assertEqual((self.env.repo / ".gitignore").read_text().splitlines(), ["dist", ".claude/settings.local.json"])

    def test_refuses_non_object_network(self):
        self.assert_refused('{"sandbox": {"network": []}}')

    def test_refuses_non_list_allowed_domains(self):
        self.assert_refused('{"sandbox": {"network": {"allowedDomains": "github.com"}}}')

    def test_existing_gitignore_keeps_its_mode(self):
        self.ssh()
        ignore = self.env.repo / ".gitignore"
        ignore.write_text("dist\n")
        ignore.chmod(0o644)
        self.run_script()
        self.assertEqual(stat.S_IMODE(ignore.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o644)

    def test_non_ssh_remote_changes_nothing(self):
        self.ssh("https://github.com/o/r.git")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.env.repo / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
