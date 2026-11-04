"""CLI tests for scripts/merge, driven through the harness with a scripted gh."""

import json
import os
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402

# A gh that answers each `<group> <verb>` (e.g. `pr merge`) from a queue: the Nth call gets reply
# min(N, last). Replies: {"stdout", "stderr", "exit"}. Calls are recorded in <state>.calls.
SCRIPTED_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
key = " ".join(args[:2])
n = sum(1 for l in open(state + ".calls") if " ".join(json.loads(l)[:2]) == key) - 1
steps = json.load(open(state + ".seq")).get(key) or [{"exit": 1, "stderr": "no reply for " + key}]
r = steps[min(n, len(steps) - 1)]
sys.stdout.write(r.get("stdout", ""))
sys.stderr.write(r.get("stderr", ""))
sys.exit(r.get("exit", 0))
'''


def pr(state, issues=(5,), branch="feat-br", cross=False):
    return {"stdout": json.dumps({"state": state, "headRefName": branch, "headRefOid": "abc123",
                                  "isCrossRepository": cross,
                                  "closingIssuesReferences": [{"number": n} for n in issues]})}


OK = {"exit": 0}
OPEN_ISSUE = [{"stdout": '{"state":"OPEN"}'}]
CLOSED_ISSUE = [{"stdout": '{"state":"CLOSED"}'}]
REPO = [{"stdout": '{"defaultBranchRef":{"name":"main"}}'}]
REF_GET = "api repos/{owner}/{repo}/git/ref/heads/feat-br"
DELETE = "api -X"


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        (self.env.bin / "gh").write_text(SCRIPTED_GH)
        git(self.env.repo, "branch", "feat-br")

    def seq(self, steps):
        steps = {"repo view": REPO, "api -X": [OK], **steps}
        self.env.gh_state.with_name("gh-state.json.seq").write_text(json.dumps(steps))

    def merge(self, *extra):
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def deletes(self):
        return self.called(["api", "-X", "DELETE"])

    def local_branch(self):
        return "feat-br" in git(self.env.repo, "branch", "--list", "feat-br")

    def test_clean_merge_cleans_up(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [OK], "issue view": OPEN_ISSUE,
                  "issue close": [OK], REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]),
                         [["pr", "merge", "7", "--squash", "--match-head-commit", "abc123"]])
        self.assertEqual(self.deletes(), [["api", "-X", "DELETE", "repos/{owner}/{repo}/git/refs/heads/feat-br"]])
        self.assertFalse(self.local_branch())
        self.assertEqual(self.called(["issue", "close"]), [["issue", "close", "5"]])
        e = self.env.events()
        self.assertEqual([(x["kind"], x["ref"], x["outcome"]) for x in e], [("merge", 7, "merged")])
        self.assertTrue(e[0]["branch_deleted"])

    def test_head_moved_is_a_refusal(self):
        self.seq({"pr view": [pr("OPEN")], "issue view": OPEN_ISSUE,
                  "pr merge": [{"exit": 1, "stderr": "Head branch was modified. Review and try the merge again."}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("--match-head-commit", self.called(["pr", "merge"])[0])
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertTrue(self.local_branch())
        self.assertEqual(self.env.events(), [])

    def test_502_with_merge_landed(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [{"exit": 1, "stderr": "HTTP 502: Bad Gateway"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("already closed", p.stdout)
        self.assertEqual(self.called(["issue", "close"]), [])

    def test_502_then_retry_in_progress_then_merged(self):
        self.seq({"pr view": [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  "pr merge": [{"exit": 1, "stderr": "HTTP 502"}, {"exit": 1, "stderr": "Merge already in progress"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 2)  # not retried after "in progress"
        self.assertEqual(len(self.deletes()), 1)

    def test_502_then_already_merged_waits_not_refuses(self):
        self.seq({"pr view": [pr("OPEN"), pr("OPEN"), pr("OPEN"), pr("MERGED")],
                  "pr merge": [{"exit": 1, "stderr": "HTTP 502"},
                               {"exit": 1, "stderr": "Pull request #7 was already merged"}],
                  "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_bare_in_progress_text_is_not_transient(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [{"exit": 1, "stderr": "check run in progress"}]})
        self.assertEqual(self.merge().returncode, 1)

    def test_branch_already_deleted_on_host(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE,
                  REF_GET: [{"exit": 1, "stderr": "gh: Not Found (HTTP 404)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])  # already merged: no merge command
        self.assertEqual(self.deletes(), [])
        self.assertIn("already gone", p.stdout)

    def test_delete_404_counts_as_gone(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "Reference does not exist (HTTP 422)"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.env.events()[0]["branch_deleted"])

    def test_delete_failure_is_reported(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "api -X": [{"exit": 1, "stderr": "HTTP 500"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOT deleted", p.stdout)
        self.assertFalse(self.env.events()[0]["branch_deleted"])

    def test_default_or_protected_branch_never_deleted(self):
        for branch, default in (("main", "main"), ("master", "trunk"), ("trunk", "trunk")):
            with self.subTest(branch=branch):
                self.seq({"pr view": [pr("MERGED", branch=branch)], "issue view": CLOSED_ISSUE,
                          "repo view": [{"stdout": json.dumps({"defaultBranchRef": {"name": default}})}]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.deletes(), [])
                self.assertIn("left alone", p.stdout)

    def test_special_characters_in_branch_are_quoted(self):
        for branch, quoted in (("feat#x", "feat%23x"), ("fix%2Fy", "fix%252Fy"), ("a/b#c%d", "a/b%23c%25d")):
            with self.subTest(branch=branch):
                self.seq({"pr view": [pr("MERGED", branch=branch)], "issue view": CLOSED_ISSUE,
                          f"api repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}": [OK]})
                p = self.merge()
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.called(["api"])[0], ["api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{quoted}"])
                self.assertEqual(self.deletes()[-1],
                                 ["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{quoted}"])
                Path(str(self.env.gh_state) + ".calls").unlink()

    def test_fork_branch_never_deleted(self):
        self.seq({"pr view": [pr("MERGED", cross=True)], "issue view": CLOSED_ISSUE})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.deletes(), [])
        self.assertTrue(self.local_branch())

    def test_unknown_default_deletes_nothing(self):
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, "repo view": [{"exit": 1}]})
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.deletes(), [])

    def test_checked_out_local_branch_kept(self):
        git(self.env.repo, "checkout", "feat-br")
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.local_branch())
        self.assertIn("kept", p.stdout)

    def test_timeout_closes_nothing(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [OK]})
        p = self.env.run("merge", "7", "--interval", "0", "--timeout", "0", check=False)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertEqual(self.env.events()[0]["outcome"], "timeout")

    def test_refused_merge_fails(self):
        self.seq({"pr view": [pr("OPEN")], "pr merge": [{"exit": 1, "stderr": "Pull request is not mergeable"}]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertIn("not mergeable", p.stdout)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.env.events(), [])

    def test_closed_unmerged(self):
        self.seq({"pr view": [pr("CLOSED")]})
        p = self.merge()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.env.events()[0]["outcome"], "closed")

    def test_unreadable_pr(self):
        self.seq({"pr view": [{"exit": 1, "stderr": "boom"}]})
        self.assertEqual(self.merge().returncode, 2)

    def test_unlinked_ticket_reported_not_closed(self):
        self.seq({"pr view": [pr("MERGED", issues=(5,))], "issue view": CLOSED_ISSUE, REF_GET: [OK]})
        p = self.merge("--ticket", "#9", "--worktree", str(self.env.repo.parent / "gone"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.called(["issue", "close"]), [])
        self.assertIn("#9 is not linked", p.stdout)
        self.assertIn("worktree", p.stdout)  # removal trouble is reported, never a failure

    def test_method_flag(self):
        self.seq({"pr view": [pr("OPEN"), pr("MERGED")], "pr merge": [OK], "issue view": CLOSED_ISSUE,
                  REF_GET: [OK]})
        self.merge("--method", "rebase")
        self.assertEqual(self.called(["pr", "merge"])[0][:4], ["pr", "merge", "7", "--rebase"])


# Logs every git call (to $GIT_LOG) before running the real one, so tests can see what was pushed.
GIT_WRAPPER = '''#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_LOG"
exec REAL_GIT "$@"
'''

CHANGELOG = "# Changes\n## Unreleased\n- old\n"
CONFIG = "## Shared files\n\n- **Shared files:**\n  - `CHANGELOG.md`: one line under Unreleased\n- **Generated:** none\n"


class SweepTest(unittest.TestCase):
    """After a merge, every in-flight branch that now conflicts is updated; nobody force-pushes but a rebase."""

    seq, merge, called = MergeTest.seq, MergeTest.merge, MergeTest.called

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        (self.env.bin / "gh").write_text(SCRIPTED_GH)
        git(self.env.repo, "branch", "feat-br")
        self.git_log = self.env.repo.parent / "git.log"
        wrapper = self.env.bin / "git"
        wrapper.write_text(GIT_WRAPPER.replace("REAL_GIT", shutil.which("git")))
        wrapper.chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"GIT_LOG": str(self.git_log)})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.env.repo / ".claude").mkdir()
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

    def sweep(self, method="squash", branches=("pr-a",)):
        listing = json.dumps([{"number": 8 + i, "headRefName": b, "isCrossRepository": False}
                              for i, b in enumerate(branches)])
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "pr list": [{"stdout": listing}]})
        return self.merge("--method", method)

    def remote(self, branch, path):
        git(self.env.repo, "fetch", "origin")
        return git(self.env.repo, "show", f"origin/{branch}:{path}") + "\n"

    def pushes(self):
        return [l for l in self.git_log.read_text().splitlines() if l.startswith("push")]

    def replays(self):
        return [(e["file"], e["outcome"]) for e in self.env.events() if e["kind"] == "replay"]

    def test_line_replay_keeps_both_sides_main_first(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "CHANGELOG.md"),
                         "# Changes\n## Unreleased\n- main item\n- pr item\n- old\n")
        self.assertEqual(self.replays(), [("CHANGELOG.md", "line-replay")])
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])  # a plain push

    def test_disjoint_word_merge(self):
        self.land({"CHANGELOG.md": CHANGELOG + "alpha beta gamma delta\n"})
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"CHANGELOG.md": CHANGELOG + "alpha beta gamma DELTA\n"})
        self.land({"CHANGELOG.md": CHANGELOG + "ALPHA beta gamma delta\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.remote("pr-a", "CHANGELOG.md"), CHANGELOG + "ALPHA beta gamma DELTA\n")
        self.assertEqual(self.replays(), [("CHANGELOG.md", "word-merge")])

    def test_overlapping_word_merge_is_handed_back(self):
        self.land({"CHANGELOG.md": CHANGELOG + "alpha beta gamma\n"})
        self.base = git(self.env.repo, "rev-parse", "HEAD")
        self.pr_branch("pr-a", {"CHANGELOG.md": CHANGELOG + "alpha PR gamma\n"})
        self.land({"CHANGELOG.md": CHANGELOG + "alpha MAIN gamma\n"})
        before = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("HANDBACK PR #8 pr-a: CHANGELOG.md", p.stdout)
        git(self.env.repo, "fetch", "origin")
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a"), before)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [("CHANGELOG.md", "handed-back")])

    def test_conflict_in_a_non_shared_file_aborts_the_update(self):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n",
                                "src.txt": "one\nPR\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n", "src.txt": "one\nMAIN\n"})
        before = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep()
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("HANDBACK PR #8 pr-a: src.txt (not a shared file)", p.stdout)
        git(self.env.repo, "fetch", "origin")
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a"), before)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [])  # the abandoned replay isn't logged

    def sweep_with(self, method):
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        old = git(self.env.repo, "rev-parse", "origin/pr-a")
        p = self.sweep(method)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("- main item\n- pr item", self.remote("pr-a", "CHANGELOG.md"))
        return old

    def test_sweep_under_squash_merges_main_without_force(self):
        old = self.sweep_with("squash")
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])
        git(self.env.repo, "merge-base", "--is-ancestor", old, "origin/pr-a")  # history kept

    def test_sweep_under_merge_merges_main_without_force(self):
        self.sweep_with("merge")
        self.assertEqual(self.pushes(), ["push origin HEAD:refs/heads/pr-a"])

    def test_sweep_under_rebase_uses_force_with_lease(self):
        old = self.sweep_with("rebase")
        self.assertEqual(self.pushes(),
                         [f"push --force-with-lease=refs/heads/pr-a:{old} origin HEAD:refs/heads/pr-a"])
        self.assertEqual(git(self.env.repo, "rev-parse", "origin/pr-a~1"), git(self.env.repo, "rev-parse", "origin/main"))

    def test_line_check_still_covers_clusters_that_did_not_word_merge(self):
        import replay
        base, main = ["a\n", "b\n"], ["a\n", "b\n"]
        pr = ["a\n", "PR\n"]
        merged_new, merged_pr = replay.Counter({"MERGED\n": 1}), replay.Counter({"PRW\n": 1})
        # A merged line is allowed; a foreign line elsewhere in the file is not.
        self.assertTrue(replay._only_pr_lines(base, main, pr, ["a\n", "PR\n", "MERGED\n"], merged_new, merged_pr))
        self.assertFalse(replay._only_pr_lines(base, main, pr, ["a\n", "PR\n", "MERGED\n", "FOREIGN\n"],
                                               merged_new, merged_pr))

    def test_an_exception_while_updating_one_branch_is_a_handback_not_a_crash(self):
        import importlib.machinery
        import importlib.util
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))  # the script's `import common`
        self.addCleanup(sys.path.pop, 0)
        loader = importlib.machinery.SourceFileLoader("merge_script", str(Path(__file__).resolve().parent.parent / "scripts" / "merge"))
        spec = importlib.util.spec_from_loader("merge_script", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.pr_branch("pr-a", {"CHANGELOG.md": "# Changes\n## Unreleased\n- pr item\n- old\n"})
        self.pr_branch("pr-b", {"CHANGELOG.md": "# Changes\n## Unreleased\n- other\n- old\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        listing = json.dumps([{"number": 8, "headRefName": "pr-a", "isCrossRepository": False},
                              {"number": 9, "headRefName": "pr-b", "isCrossRepository": False}])
        self.seq({"pr view": [pr("MERGED")], "issue view": CLOSED_ISSUE, REF_GET: [OK],
                  "pr list": [{"stdout": listing}]})
        real = mod.replay.update_branch
        calls = []

        def boom(repo, base, branch, method, patterns):
            calls.append(branch)
            if branch == "pr-a":
                raise RuntimeError("disk on fire")
            return real(repo, base, branch, method, patterns)

        env = {"PATH": f"{self.env.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.env.gh_state)}
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(mod.replay, "update_branch", boom), \
                unittest.mock.patch("builtins.print") as out:
            code = mod.main(["7", "--interval", "0", "--timeout", "5", "--cwd", str(self.env.repo)])
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list)
        self.assertEqual(code, 4)
        self.assertEqual(calls, ["pr-a", "pr-b"])  # went on to the next PR
        self.assertIn("HANDBACK PR #8 pr-a: (sweep) (RuntimeError: disk on fire)", printed)
        self.assertIn("- main item\n- other", self.remote("pr-b", "CHANGELOG.md"))

    def test_branch_that_is_only_behind_is_left_alone(self):
        self.pr_branch("pr-a", {"src.txt": "one\ntwo\nthree\n"})
        self.land({"CHANGELOG.md": "# Changes\n## Unreleased\n- main item\n- old\n"})
        p = self.sweep()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.replays(), [])


if __name__ == "__main__":
    unittest.main()
