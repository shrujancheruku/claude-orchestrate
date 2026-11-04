"""CLI tests for scripts/gate, driven through the harness with a fake gh."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402


class GateTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def prime(self, title="feat(x): add y", mergeable="MERGEABLE", behind=0):
        view = {"title": title, "mergeable": mergeable, "baseRefName": "main", "headRefName": "br"}
        self.env.set_gh([
            {"args": ["pr", "view"], "stdout": json.dumps(view)},
            {"args": ["api"], "stdout": f"{behind}\n"},
        ])

    def config(self, line):
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"## Review\n\n- **Repo-specific checks:** {line}\n- **Post reviews:** no\n")

    def gate(self):
        return self.env.run("gate", "7", check=False)

    def event(self):
        (e,) = self.env.events()
        self.assertEqual((e["kind"], e["ref"]), ("gate", 7))
        return e

    def test_passing_gate(self):
        self.prime()
        self.config("codegen is current (run: `echo ok`), see `README.md`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(l.startswith("pass") for l in lines), lines)
        self.assertIn("behind_by 0", lines[1])
        e = self.event()
        self.assertEqual((e["outcome"], e["failed"], e["behind_by"]), ("pass", [], 0))
        # the compare call used the PR's base and head
        self.assertIn("repos/{owner}/{repo}/compare/main...br", self.env.gh_calls()[1])

    def test_title_convention_failure(self):
        self.prime(title="Add the thing")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.splitlines()[0].startswith("fail"))
        self.assertIn("Add the thing", r.stdout)
        e = self.event()
        self.assertEqual(e["outcome"], "fail")
        self.assertEqual(e["failed"], ["title follows Conventional Commits"])

    def test_conflicting_pr(self):
        self.prime(mergeable="CONFLICTING")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("conflicts with main", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")

    def test_behind_base_is_not_mergeable_and_is_reported(self):
        self.prime(behind=3)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("behind main by 3", r.stdout)
        self.assertEqual(self.event()["behind_by"], 3)

    def test_failing_repo_command(self):
        self.prime()
        self.config("codegen is up to date (run: `echo stale codegen; exit 4`)")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("(exit 4)", r.stdout)
        self.assertIn("stale codegen", r.stdout)
        e = self.event()
        self.assertEqual(e["failed"], ["echo stale codegen; exit 4"])

    def test_plain_backticks_are_never_run(self):
        self.prime()
        # This repo's own line (.claude/orchestration.md:66): `README.md` is prose, not a command.
        self.config("behaviour changes come with tests; Python 3.9 compatible; user-facing changes update `README.md`; "
                    "release-please files untouched; plugin features work out of the box, with no manual edits to the "
                    "user's config scripts (degrade instead).")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 2)  # title + mergeable only
        self.assertNotIn("README.md", r.stdout)

    def test_no_config_runs_builtin_checks_only(self):
        self.prime()
        r = self.gate()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
