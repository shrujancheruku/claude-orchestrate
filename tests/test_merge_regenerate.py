"""scripts/merge's sweep of generated files: regenerated on conflict, or handed back.

The rest of the sweep is in test_merge_sweep.py.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import git  # noqa: E402
from merge_support import CONFIG, SweepMixin  # noqa: E402


class GeneratedSweepTest(SweepMixin, unittest.TestCase):
    """Generated files in the sweep: regenerated when they conflict, handed back when that fails."""

    def generated_scenario(self, command, in_base=None):
        """pr-a and main both rebuild gen.txt (upper-case src.txt) after editing different lines of src.txt."""
        (self.env.repo / ".claude" / "orchestration.md").write_text(
            CONFIG.replace("- **Generated:** none", f"- **Generated:**\n  - `gen*.txt`: `{command}`"))
        self.env.commit("gen.txt", "ONE TWO\n", "chore: gen")
        for path, text in (in_base or {}).items():
            self.env.commit(path, text, f"chore: {path}")
        git(self.env.repo, "push", "origin", "main")
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.git_log.write_text("")
        self.pr_branch("pr-a", {"src.txt": "one\ntwo\nthree\n", "gen.txt": "ONE TWO THREE\n"})
        self.land({"src.txt": "zero\none\ntwo\n", "gen.txt": "ZERO ONE TWO\n"})

    def test_conflicting_generated_file_is_regenerated_and_committed(self):
        self.generated_scenario("tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt")
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "gen.txt"), "ZERO ONE TWO THREE\n")
        self.assertEqual(self.replays(), [("gen.txt", "regenerated")])
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])

    def test_regenerated_file_under_rebase(self):
        self.generated_scenario("tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt")
        p = self.sweep("rebase")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "gen.txt"), "ZERO ONE TWO THREE\n")
        self.assertEqual(self.replays(), [("gen.txt", "regenerated")])

    def test_failing_regenerate_command_is_handed_back_with_the_conflict(self):
        self.generated_scenario("echo no good >&2; exit 3")
        before = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("HANDBACK PR #8 pr-a: gen.txt (regenerate command failed: echo no good >&2; exit 3 (no good))",
                      p.stdout)
        git(self.env.repo, "fetch", "origin")
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a"), before)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [("gen.txt", "handed-back")])

    def test_files_the_command_also_writes_are_staged_when_generated(self):
        self.generated_scenario("tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt; echo x > gen2.txt")
        p = self.sweep("rebase")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "gen2.txt"), "x\n")

    def test_command_changing_a_file_outside_the_globs_is_handed_back(self):
        self.generated_scenario("tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt; echo x > other.txt")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("regenerate command failed", p.stdout)
        self.assertIn("changed other.txt, outside the generated globs", p.stdout)
        self.assertEqual(self.pushes(), [])

    def test_a_rename_on_main_is_not_mistaken_for_a_stray_change(self):
        self.generated_scenario("tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt",
                                in_base={"notes-old.txt": "some unrelated notes\nwith two lines\n"})
        git(self.env.repo, "mv", "notes-old.txt", "notes-new.txt")
        git(self.env.repo, "commit", "-q", "-m", "chore: rename notes")
        git(self.env.repo, "push", "origin", "main")
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "gen.txt"), "ZERO ONE TWO THREE\n")
        self.assertEqual(self.replays(), [("gen.txt", "regenerated")])

    def test_generated_commands_parse(self):
        import config
        cfg = config.Config.parse(
            "## Shared files\n\n- **Shared files:**\n  - `A.md`: x\n- **Generated:**\n  - `gen/**`: `make gen`\n  - `b.txt`: `sh b.sh`\n")
        self.assertEqual(cfg.shared_files, ("A.md",))
        self.assertEqual(cfg.generated, (("gen/**", "make gen"), ("b.txt", "sh b.sh")))
        none = config.Config.parse("## Shared files\n\n- **Generated:** none\n")
        self.assertEqual(none.generated, ())


if __name__ == "__main__":
    unittest.main()
