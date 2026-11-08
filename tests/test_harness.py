"""Smoke tests: trivial CLIs built on scripts/common.py, driven through the harness."""

import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import SCRIPTS, ScriptEnv, git # noqa: E402

TRIVIAL_CLI = '''
import subprocess, sys
import common, runlog

slots = common.config_section(".", "Slots")
out = subprocess.run(["gh", "pr", "view", sys.argv[1], "--json", "state"], capture_output=True, text=True).stdout
print("pr", sys.argv[1], common.one_line(out))
print(common.tail("\\n".join("line %d" % i for i in range(100))).splitlines()[0])
print("slots:", common.one_line(slots or "none"))
runlog.append_event(".", "merge", "merged", ref="#" + sys.argv[1], session="s1")
'''

SECTION_CLI = '''
import sys
import common, runlog

print(common.config_section(".", sys.argv[1]))
runlog.append_event(".", "gate", "pass")
'''


class HarnessSmoke(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def cli(self, name, source):
        # A temp scripts/ dir holding a copy of common.py beside the fixture CLI, so the
        # CLI's plain `import common` only works if the script's own directory is on sys.path.
        d = self.env.repo.parent / "scripts"
        d.mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "common.py", d / "common.py")
        shutil.copy(SCRIPTS / "runlog.py", d / "runlog.py")
        shutil.copy(SCRIPTS / "repo.py", d / "repo.py")
        path = d / name
        path.write_text(source)
        return path

    def test_cli_through_harness(self):
        e = self.env
        e.commit(".claude/orchestration.md", "## Slots\n\n- **Slots:** 2.\n\n## Other\n\nx\n", "chore: config")
        e.set_gh([{"args": ["pr", "view", "7"], "stdout": '{"state": "OPEN"}'}])

        out = e.run(self.cli("cli.py", TRIVIAL_CLI), 7).stdout.splitlines()

        self.assertEqual(out[0], 'pr 7 {"state": "OPEN"}')
        self.assertEqual(out[1], "... 60 earlier lines omitted")
        self.assertEqual(out[2], "slots: - **Slots:** 2.")
        self.assertEqual(e.gh_calls(), [["pr", "view", "7", "--json", "state"]])
        (event,) = e.events()
        self.assertEqual(
            {k: event[k] for k in ("session", "kind", "ref", "outcome")},
            {"session": "s1", "kind": "merge", "ref": "#7", "outcome": "merged"},
        )
        self.assertIn("ts", event)
        self.assertEqual(git(e.repo, "ls-remote", "--heads", "origin").split()[1], "refs/heads/main")

    def test_section_boundaries(self):
        e = self.env
        e.commit(".claude/orchestration.md", "## A\n\none\n### sub\ntwo\n\n## B\n\nthree\n", "chore: config")
        cli = self.cli("section.py", SECTION_CLI)
        self.assertEqual(e.run(cli, "A").stdout, "one\n### sub\ntwo\n")
        self.assertEqual(e.run(cli, "b").stdout, "three\n")
        self.assertEqual(e.run(cli, "C").stdout, "None\n")

    def test_session_default_from_the_state_file_not_the_environment(self):
        e = self.env
        cli = self.cli("section.py", SECTION_CLI)
        saved = os.environ.get("CLAUDE_SESSION_ID")
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_SESSION_ID", saved) if saved else os.environ.pop("CLAUDE_SESSION_ID", None))
        os.environ["CLAUDE_SESSION_ID"] = "abc"
        e.run(cli, "A")
        self.assertEqual([x["session"] for x in e.events()], ["test-session"])

    def test_no_state_file_and_no_session_is_refused_in_one_line(self):
        e = self.env
        (e.repo / ".claude" / "orchestrate-state.json").unlink()
        cli = self.cli("section.py", SECTION_CLI)
        res = e.run(cli, "A", check=False)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertEqual(e.events(), [])


if __name__ == "__main__":
    unittest.main()
