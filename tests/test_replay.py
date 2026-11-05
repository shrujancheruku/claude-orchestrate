"""replay() and sweep_branch() driven directly, with real git in a temp repo."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import ScriptEnv, git  # noqa: E402

import replay  # noqa: E402

HEAD = "# Changes\n## Unreleased\n"


class ReplayTable(unittest.TestCase):
    CASES = [
        ("line replay keeps both inserts, main first",
         HEAD + "- old\n", HEAD + "- main\n- old\n", HEAD + "- pr\n- old\n",
         HEAD + "- main\n- pr\n- old\n", replay.LINE_REPLAY),
        ("a PR edit far from main's edit is replayed",
         "a\nb\nc\nd\n", "A\nb\nc\nd\n", "a\nb\nc\nD\n", "A\nb\nc\nD\n", replay.LINE_REPLAY),
        ("a word merge of disjoint words is accepted",
         "alpha beta gamma delta\n", "ALPHA beta gamma delta\n", "alpha beta gamma DELTA\n",
         "ALPHA beta gamma DELTA\n", replay.WORD_MERGE),
        ("a word merge of overlapping words is refused",
         "alpha beta gamma\n", "alpha MAIN gamma\n", "alpha PR gamma\n", None, replay.HANDED_BACK),
        ("a PR line that main also changed, differently, is refused",
         "one\ntwo\n", "one\nMAIN\n", "one\nPR\n", None, replay.HANDED_BACK),
        ("a PR line that main changed the same way is kept once",
         "one\ntwo\n", "one\nSAME\n", "one\nSAME\n", "one\nSAME\n", replay.LINE_REPLAY),
    ]

    def test_table(self):
        for name, base, main, pr, want, outcome in self.CASES:
            with self.subTest(name):
                self.assertEqual(replay.replay(base, main, pr), (want, outcome))


class SweepBranchTest(unittest.TestCase):
    RULES = replay.Rules(["CHANGELOG.md"], [])
    OLD = HEAD + "- old\n"

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.repo = self.env.repo
        self.env.commit("CHANGELOG.md", self.OLD, "chore: changelog")
        git(self.repo, "push", "origin", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def pr_branch(self, name, files):
        git(self.repo, "checkout", "-q", "-b", name, self.base)
        for path, text in files.items():
            self.env.commit(path, text, f"feat: {name} {path}")
        git(self.repo, "push", "origin", name)
        git(self.repo, "checkout", "-q", "main")

    def land(self, files):
        for path, text in files.items():
            self.env.commit(path, text, f"feat: landed {path}")
        git(self.repo, "push", "origin", "main")

    def sweep(self, branch="pr-a", method="squash", rules=None):
        git(self.repo, "fetch", "--prune", "origin")
        return replay.sweep_branch(str(self.repo), "main", branch, method, rules or self.RULES)

    def remote(self, branch, path):
        git(self.repo, "fetch", "origin")
        return git(self.repo, "show", f"origin/{branch}:{path}") + "\n"

    def conflicting(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": HEAD + "- pr\n- old\n"})
        self.land({"CHANGELOG.md": HEAD + "- main\n- old\n"})

    def test_a_conflicting_shared_file_is_replayed_and_pushed(self):
        self.conflicting()
        r = self.sweep()
        self.assertEqual((r.status, r.events, r.conflicts), ("updated", [("CHANGELOG.md", "line-replay")], []))
        self.assertEqual(self.remote("pr-a", "CHANGELOG.md"), HEAD + "- main\n- pr\n- old\n")

    def test_rebase_pushes_a_rebased_branch(self):
        self.conflicting()
        r = self.sweep(method="rebase")
        self.assertEqual(r.status, "updated")
        self.assertEqual(git(self.repo, "rev-parse", "origin/pr-a~1"), git(self.repo, "rev-parse", "origin/main"))

    def test_a_failed_push_is_handed_back_and_still_returns_its_events(self):
        self.conflicting()
        hook = Path(self.env.origin, "hooks", "pre-receive")
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
        hook.chmod(0o755)
        r = self.sweep()
        self.assertEqual(r.status, "handed-back")
        self.assertEqual(r.events, [("CHANGELOG.md", "line-replay")])
        self.assertEqual([p for p, _ in r.conflicts], ["(push)"])
        self.assertTrue(r.handback_lines("PR #8 pr-a")[0].startswith("HANDBACK PR #8 pr-a: (push) ("))

    def test_a_conflict_in_a_file_that_is_not_shared_is_handed_back_untouched(self):
        self.env.commit("src.txt", "one\ntwo\n", "chore: src")
        git(self.repo, "push", "origin", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"src.txt": "one\nPR\n"})
        self.land({"src.txt": "one\nMAIN\n"})
        before = git(self.repo, "rev-parse", "origin/pr-a")
        r = self.sweep()
        self.assertEqual((r.status, r.events), ("handed-back", []))
        self.assertEqual(r.handback_lines("PR #8 pr-a"), ["HANDBACK PR #8 pr-a: src.txt (not a shared file)"])
        self.assertEqual(git(self.repo, "rev-parse", "origin/pr-a"), before)

    def test_a_branch_that_is_only_behind_is_clean(self):
        self.pr_branch("pr-a", {"src.txt": "x\n"})
        self.land({"CHANGELOG.md": HEAD + "- main\n- old\n"})
        self.assertEqual(self.sweep().status, "clean")

    def test_git_older_than_merge_tree_is_skipped_with_a_note(self):
        real = replay._git_version
        self.addCleanup(setattr, replay, "_git_version", real)
        replay._git_version = lambda repo: (2, 37)
        r = self.sweep()
        self.assertEqual((r.status, r.events, r.conflicts), ("skipped", (), ()))
        self.assertIn("needs git >= 2.38", r.note)
        self.assertIn("this is git 2.37", r.note)

    def test_the_results_defaults_are_not_shared_lists(self):
        self.assertEqual(replay.SweepResult("clean").events, ())

    def test_a_generated_file_is_regenerated(self):
        rules = replay.Rules(["CHANGELOG.md"], [("gen.txt", "tr a-z A-Z < src.txt | paste -sd' ' - > gen.txt")])
        self.env.commit("src.txt", "one\ntwo\n", "chore: src")
        self.env.commit("gen.txt", "ONE TWO\n", "chore: gen")
        git(self.repo, "push", "origin", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"src.txt": "one\ntwo\nthree\n", "gen.txt": "ONE TWO THREE\n"})
        self.land({"src.txt": "zero\none\ntwo\n", "gen.txt": "ZERO ONE TWO\n"})
        r = self.sweep(rules=rules)
        self.assertEqual((r.status, r.events), ("updated", [("gen.txt", "regenerated")]))
        self.assertEqual(self.remote("pr-a", "gen.txt"), "ZERO ONE TWO THREE\n")

    def test_a_regenerate_command_that_hangs_is_handed_back(self):
        self.addCleanup(setattr, replay, "REGENERATE_TIMEOUT", replay.REGENERATE_TIMEOUT)
        replay.REGENERATE_TIMEOUT = 0.5
        rules = replay.Rules([], [("gen.txt", "sh -c 'sleep 20 & wait'")])
        self.env.commit("gen.txt", "a\nb\n", "chore: gen")
        git(self.repo, "push", "origin", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"gen.txt": "a\nPR\n"})
        self.land({"gen.txt": "a\nMAIN\n"})
        r = self.sweep(rules=rules)
        self.assertEqual(r.status, "handed-back")
        self.assertIn("timed out", r.conflicts[0][1])

    def test_an_exception_while_updating_is_a_handback_not_a_crash(self):
        self.conflicting()
        real = replay.resolve_conflicts
        self.addCleanup(setattr, replay, "resolve_conflicts", real)

        def boom(*a, **k):
            raise RuntimeError("disk on fire")

        replay.resolve_conflicts = boom
        r = self.sweep()
        self.assertEqual((r.status, r.conflicts), ("handed-back", [("(sweep)", "RuntimeError: disk on fire")]))


if __name__ == "__main__":
    unittest.main()
