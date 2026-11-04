"""Smoke test: a trivial CLI built on scripts/common.py, driven through the harness."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import SCRIPTS, ScriptEnv, git  # noqa: E402

TRIVIAL_CLI = f'''
import subprocess, sys
sys.path.insert(0, {str(SCRIPTS)!r})
import common

slots = common.config_section(".", "Slots")
out = subprocess.run(["gh", "pr", "view", sys.argv[1], "--json", "state"], capture_output=True, text=True).stdout
print("pr", sys.argv[1], common.one_line(out))
print(common.tail("\\n".join(f"line {{i}}" for i in range(100))).splitlines()[0])
print("slots:", common.one_line(slots or "none"))
common.append_event(".", "merge", "merged", ref="#" + sys.argv[1], session="s1")
'''


class HarnessSmoke(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def test_cli_through_harness(self):
        e = self.env
        e.commit(".claude/orchestration.md", "## Slots\n\n- **Slots:** 2.\n\n## Other\n\nx\n", "chore: config")
        e.set_gh([{"args": ["pr", "view", "7"], "stdout": '{"state": "OPEN"}'}])
        cli = e.repo.parent / "cli.py"
        cli.write_text(TRIVIAL_CLI)

        out = e.run(cli, 7).stdout.splitlines()

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


if __name__ == "__main__":
    unittest.main()
