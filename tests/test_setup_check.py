"""CLI tests for scripts/setup-check."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402

PLAIN = "name: ci\non:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps: [{run: make test}]\n"
SKIPPING = (
    "name: ci\non:\n  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review]\njobs:\n  t:\n"
    "    if: github.event_name != 'pull_request' || github.event.pull_request.draft == false\n    runs-on: x\n"
)
PUSH_ONLY = "name: release\non:\n  push:\n    tags: ['v*']\njobs:\n  r:\n    runs-on: x\n"


class SetupCheckTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.flows = self.env.repo / ".github" / "workflows"

    def workflow(self, name, text):
        self.flows.mkdir(parents=True, exist_ok=True)
        (self.flows / name).write_text(text)

    def check(self):
        r = self.env.run("setup-check", "--repo", self.env.repo, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_pull_request_without_draft_condition_is_flagged(self):
        self.workflow("ci.yml", PLAIN)
        self.assertEqual(self.check(), ["flag .github/workflows/ci.yml", "next: advise"])

    def test_ready_for_review_or_draft_condition_is_not_flagged(self):
        self.workflow("ci.yml", SKIPPING)
        self.workflow("types-only.yaml", "on:\n  pull_request:\n    types: [opened, ready_for_review]\n")
        self.workflow("cond-only.yml", "on: pull_request\njobs:\n  t:\n    if: ${{ !github.event.pull_request.draft }}\n")
        self.assertEqual(self.check(), ["next: none"])

    def test_push_only_workflow_is_not_flagged(self):
        self.workflow("release.yml", PUSH_ONLY)
        self.assertEqual(self.check(), ["next: none"])

    def test_inline_trigger_forms_are_flagged(self):
        self.workflow("a.yml", "on: pull_request\n")
        self.workflow("b.yml", "on: [push, pull_request]\n")
        self.workflow("c.yml", "on: {pull_request: {branches: [main]}}\n")
        self.assertEqual(
            self.check(),
            ["flag .github/workflows/a.yml", "flag .github/workflows/b.yml", "flag .github/workflows/c.yml", "next: advise"],
        )

    def test_only_the_unprotected_file_is_listed_among_several(self):
        self.workflow("ci.yml", SKIPPING)
        self.workflow("lint.yml", PLAIN)
        self.workflow("release.yml", PUSH_ONLY)
        self.assertEqual(self.check(), ["flag .github/workflows/lint.yml", "next: advise"])

    def test_a_comment_neither_flags_nor_clears_a_file(self):
        self.workflow("commented.yml", "on: push  # not pull_request\n")
        self.workflow("noted.yml", "# TODO: skip draft PRs\non:\n  pull_request:\n")
        self.workflow("expr.yml", "on: push\njobs:\n  t:\n    steps: [{run: 'echo ${{ github.event.pull_request.number }}'}]\n")
        self.assertEqual(self.check(), ["flag .github/workflows/noted.yml", "next: advise"])

    def test_other_files_in_the_folder_are_ignored(self):
        self.workflow("README.md", "pull_request\n")
        self.workflow("ci.yml.disabled", PLAIN)
        self.assertEqual(self.check(), ["next: none"])

    def test_no_workflows_directory_is_next_none(self):
        self.assertFalse(self.flows.exists())
        self.assertEqual(self.check(), ["next: none"])

    def test_writes_nothing(self):
        self.workflow("ci.yml", PLAIN)
        self.check()
        self.assertEqual((self.flows / "ci.yml").read_text(), PLAIN)
        self.assertEqual([p.name for p in self.flows.iterdir()], ["ci.yml"])

    def test_missing_repo_is_exit_2(self):
        r = self.env.run("setup-check", "--repo", self.env.repo / "nope", check=False)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
