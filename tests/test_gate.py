"""CLI tests for scripts/gate, driven through the harness with a fake gh."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_pulls, gh_repo_view, rest_pull  # noqa: E402
import runlog  # noqa: E402

HEAD = "a1b2c3d4" * 5
PULL = "repos/{owner}/{repo}/pulls/7"
CLEAN_DIFF = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def added_file(path, line="x = 1"):
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{line}\n")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def prime(self, title="feat(x): add y", mergeable="MERGEABLE", behind=0, head="br", base="main", body="",
              diff=CLEAN_DIFF, blockers=0, ticket=270, others=(), behind_reply=None, view_reply=None, issues=(),
              ticket_body="", body_blocked_by=()):
        """Canned gh replies for the gate and for the `holds --pr` subprocess it runs: one PR view serves both."""
        view = {"title": title, "mergeable": mergeable, "baseRefName": base, "headRefName": head, "headRefOid": HEAD,
                "body": body}
        # The ticket's own node: `blockers` native open edges (900+i), `body_blocked_by` open issues named on its body line.
        nodes = list(issues)
        if blockers or ticket_body or body_blocked_by:
            nodes.append({"number": ticket, "body": ticket_body,
                          "blockedBy": {"nodes": [{"number": 900 + i, "state": "OPEN"} for i in range(blockers)]}})
            nodes += [{"number": n} for n in body_blocked_by]
        self.env.set_gh([
            view_reply or {"args": ["api", PULL], "stdout": json.dumps(rest_pull(**view))},
            {"args": ["api", "~" + PULL + "/files"], "stdout": json.dumps([{"filename": "src/a.py"}])},
            *[{"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls/{o['number']}/files"],
               "stdout": json.dumps([{"filename": "src/a.py"}])} for o in others],
            {"args": ["pr", "diff"], "stdout": diff},
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": nodes}}}})},
            gh_pulls(list(others), "open"),
            gh_pulls([], "closed"),
            gh_repo_view(),
            behind_reply or {"args": ["api"], "stdout": f"{behind}\n"},
        ])

    def config(self, line):
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"## Review\n\n- **Repo-specific checks:** {line}\n- **Post reviews:** no\n")

    def gate(self, *extra):
        return self.env.run("gate", "7", "--retry-delay", "0", *extra, check=False)

    def event(self):
        (e,) = self.env.events()
        self.assertEqual((e["kind"], e["ref"]), ("gate", 7))
        return e

    def next_lines(self, r):
        return [l for l in r.stdout.splitlines() if l.startswith("next:")]

    def test_a_failed_check_ends_send_back(self):
        self.prime(title="Add the thing")
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (1, ["next: send-back 7"]))
        self.assertEqual(r.stdout.splitlines()[-1], "next: send-back 7")

    def test_unknown_mergeability_ends_rerun_after_ci(self):
        self.prime(mergeable="UNKNOWN")
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (3, ["next: rerun-after-ci"]))

    def test_every_could_not_run_path_ends_could_not_run(self):
        self.prime()
        paths = {"missing cwd": ("--cwd", str(self.env.repo / "gone")), "bad timeout": ("--timeout", "0")}
        for name, flags in paths.items():
            with self.subTest(name):
                r = self.gate(*flags)
                self.assertEqual((r.returncode, self.next_lines(r)), (2, ["next: could-not-run"]))
        self.env.set_gh([{"args": ["pr", "view"], "stderr": "boom", "exit": 1}])
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (2, ["next: could-not-run"]))
        self.assertNotIn("next:", r.stderr)

    def test_help_prints_no_verdict(self):
        r = self.env.run("gate", "--help", check=False)
        self.assertEqual((r.returncode, "next:" in r.stdout.split("\n\n", 1)[0]), (0, False))
        self.assertFalse(r.stdout.rstrip().splitlines()[-1].startswith("next:"))

    def test_one_gate_run_reads_the_issues_once(self):
        # #367: the gate hands its snapshot to `holds --snapshot`, so the issues are fetched by the gate alone.
        self.prime(blockers=0, ticket_body="x")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        issue_reads = [c for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        self.assertEqual(len(issue_reads), 1, issue_reads)
        self.assertEqual(self.env.gh_unmatched(), [])

    def test_passing_gate(self):
        self.prime()
        self.config("codegen is current (run: `echo ok`), see `README.md`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines.pop(), f"next: review {HEAD}")
        self.assertEqual(lines[0], f"PR #7 head {HEAD}")
        self.assertEqual(len(lines), 8)  # head, title, mergeable, overlap, dependencies, secrets, lockfiles, run:
        self.assertTrue(all(l.startswith("pass") for l in lines[1:]), lines)
        self.assertIn("behind_by 0", lines[2])
        e = self.event()
        self.assertEqual((e["outcome"], e["failed"], e["behind_by"], e["head"]), ("pass", [], 0, HEAD))
        # the compare call used the PR's base and head
        self.assertIn(["api", "repos/{owner}/{repo}/compare/main...br", "--jq", ".behind_by"], self.env.gh_calls())

    def run_from(self, cwd):
        return self.env.run("gate", "7", "--retry-delay", "0", cwd=cwd, check=False)

    def test_event_from_a_subdirectory_lands_in_the_checkout_log(self):
        self.prime()
        sub = self.env.repo / "pkg" / "deep"
        sub.mkdir(parents=True)
        self.assertEqual(self.run_from(sub).returncode, 0)
        self.event()
        self.assertFalse((sub / ".claude").exists())

    def test_event_from_a_worktree_lands_in_the_main_checkout_log(self):
        self.prime()
        wt = self.env.repo / ".claude" / "worktrees" / "w1"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "w1", str(wt)], cwd=self.env.repo, check=True)
        self.assertEqual(self.run_from(wt).returncode, 0)
        self.event()
        self.assertFalse((wt / runlog.PROFILES).exists())

    def test_title_convention_failure(self):
        self.prime(title="Add the thing")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.splitlines()[1].startswith("fail"))
        self.assertIn("Add the thing", r.stdout)
        e = self.event()
        self.assertEqual(e["outcome"], "fail")
        self.assertEqual(e["failed"], ["title follows Conventional Commits"])

    def test_conflicting_pr(self):
        self.prime(mergeable="CONFLICTING")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("conflict", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")

    def test_behind_but_mergeable_passes_and_reports_behind_by(self):
        self.prime(behind=3)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("behind_by 3", r.stdout)
        e = self.event()
        self.assertEqual((e["outcome"], e["behind_by"]), ("pass", 3))

    def test_failing_repo_command(self):
        self.prime()
        self.config("codegen is up to date (run: `echo stale codegen; exit 4`)")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("exit 4", r.stdout)
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
        self.assertEqual(len(r.stdout.splitlines()), 8)  # the built-in checks only, then next:
        self.assertNotIn("README.md", r.stdout)

    def test_missing_cwd_exits_2_before_any_check(self):
        self.prime()
        r = self.gate("--cwd", str(self.env.repo / "gone"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--cwd", r.stderr)
        self.assertIn("does not exist", r.stderr)
        self.assertEqual(self.env.gh_calls(), [])
        self.assertEqual(self.env.events(), [])

    def test_cwd_is_where_repo_commands_and_config_live(self):
        self.prime()
        other = self.env.repo / "wt"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "orchestration.md").write_text("## Review\n\n- **Repo-specific checks:** where (run: `pwd`)\n")
        r = self.gate("--cwd", str(other))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("pwd", r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 9)

    def test_several_markers_in_one_item_all_run(self):
        self.prime()
        self.config("a (run: `echo one > one.txt`) and b (run: `echo two > two.txt`)")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((self.env.repo / "one.txt").exists() and (self.env.repo / "two.txt").exists())

    def test_dry_run_is_not_a_marker(self):
        self.prime()
        self.config("dry-run: `touch nope.txt`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.env.repo / "nope.txt").exists())

    def test_malformed_markers_fail(self):
        for item in ("run: ``echo x``", "run:", "run: `` nothing", "must run: the tests"):
            with self.subTest(item=item):
                self.prime()
                self.config(item)
                r = self.gate()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("malformed", r.stdout)

    def test_only_the_repo_specific_checks_label_counts(self):
        self.prime()
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("## Review\n\n- Tiers: see Repo-specific checks (run: `touch nope.txt`)\n- **Repo-specific checks:** none\n")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.env.repo / "nope.txt").exists())

    def test_scoped_and_breaking_titles_pass(self):
        for title in ("feat(gate)!: drop x", "fix!: y", "chore(deps-dev): bump"):
            with self.subTest(title=title):
                self.prime(title=title)
                self.assertEqual(self.gate().returncode, 0)

    def test_bad_titles_fail(self):
        for title in ("feat: ", "feat(): x", "Feat: x", "feat x"):
            with self.subTest(title=title):
                self.prime(title=title)
                self.assertEqual(self.gate().returncode, 1)

    def test_unknown_mergeability_retries_then_exits_3(self):
        self.prime(mergeable="UNKNOWN")
        r = self.gate()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("again", r.stdout)
        views = [c for c in self.env.gh_calls() if c[:2] == ["api", PULL]]
        self.assertGreater(len(views), 1)
        self.assertEqual(self.env.events()[-1]["outcome"], "unknown")

    def test_unknown_with_another_failure_is_a_failure(self):
        self.prime(title="bad title", mergeable="UNKNOWN")
        self.assertEqual(self.gate().returncode, 1)

    def test_unknown_behind_base_alone_exits_3(self):
        self.prime(mergeable="UNKNOWN", behind=3)
        r = self.gate()
        self.assertEqual(r.returncode, 3, r.stdout)
        e = self.event()
        self.assertEqual((e["outcome"], e["behind_by"]), ("unknown", 3))

    def test_unreadable_behind_by_on_mergeable_passes_with_null(self):
        self.prime(behind_reply={"args": ["api"], "stdout": "null\n"})
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("behind_by ?", r.stdout)
        self.assertIsNone(self.event()["behind_by"])

    def test_unknown_with_unreadable_behind_alone_exits_3(self):
        self.prime(mergeable="UNKNOWN", behind_reply={"args": ["api"], "stderr": "HTTP 502", "exit": 1})
        r = self.gate()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertEqual(self.event()["outcome"], "unknown")

    def test_unknown_settles_to_mergeable_on_retry(self):
        views = [json.dumps(rest_pull(title="feat(x): add y", mergeable=m, baseRefName="main", headRefName="br",
                                      headRefOid=HEAD, body=""))
                 for m in ("UNKNOWN", "MERGEABLE")]
        self.prime(view_reply={"args": ["api", PULL], "seq": [{"stdout": views[0]}, {"stdout": views[1]},
                                                              {"stdout": views[1]}, {"stdout": views[1]}]})
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.env.gh_calls()[1][:2], ["api", PULL])
        self.assertEqual(self.event()["outcome"], "pass")

    def test_gh_failure_exits_2(self):
        self.env.set_gh([{"args": ["api", PULL], "stderr": "boom", "exit": 1}])
        r = self.gate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_no_config_runs_builtin_checks_only(self):
        self.prime()
        r = self.gate()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 8)

    def test_missing_gh_is_could_not_run(self):
        r = self.env.run("gate", "7", "--retry-delay", "0", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("gh", r.stderr)
        self.assertEqual(self.env.events(), [])

    def test_malformed_gh_json_is_could_not_run(self):
        self.env.set_gh([{"args": ["api", PULL], "stdout": "<html>not json"}])
        r = self.gate()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("malformed", r.stderr)

    def test_json_of_the_wrong_shape_is_could_not_run(self):
        self.env.set_gh([{"args": ["api", PULL], "stdout": "[1, 2]"}])
        self.assertEqual(self.gate().returncode, 2)

    def test_non_positive_timeout_is_could_not_run(self):
        self.prime()
        self.assertEqual(self.gate("--timeout", "0").returncode, 2)

    def test_hung_repo_check_is_killed_and_fails(self):
        self.prime()
        self.config("slow (run: `sleep 30`)")
        r = self.gate("--timeout", "0.5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timed out", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")

    # --- the checks before review (#270) ---

    def failed_only(self, r, name):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.event()["failed"], [name])
        return r.stdout

    def test_overlap_with_a_held_path_fails_with_holds_output(self):
        self.prime(others=[{"number": 9, "headRefName": "other"}])
        out = self.failed_only(self.gate(), "no overlap with held paths")
        self.assertIn("overlap: src/a.py (held by open PR #9)", out)

    def test_holds_that_cannot_run_is_gate_exit_2_not_a_failure(self):
        self.prime()
        self.env.set_gh([{"args": ["api", "~repos/{owner}/{repo}/pulls?state=open"], "stderr": "HTTP 502", "exit": 1}] + json.loads(self.env.gh_state.read_text())["responses"])
        r = self.gate()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("holds could not run", r.stderr)
        self.assertEqual(self.env.events(), [])

    def test_open_blocker_fails_dependencies(self):
        self.prime(head="270-slug", blockers=2)
        out = self.failed_only(self.gate(), "dependencies merged")
        self.assertIn("#270 has 2 open blocker", out)

    def test_ticket_with_no_open_blocker_passes(self):
        self.prime(head="270-slug", blockers=0)
        self.assertEqual(self.gate().returncode, 0)

    def test_a_malformed_issues_reply_is_gate_exit_2_not_a_failure(self):
        self.prime(head="270-slug")
        # The gate reads the issues once (#367) and gets a bad node.
        def reply(nodes):
            return {"stdout": json.dumps({"data": {"repository": {"issues": {"nodes": nodes}}}})}
        responses = json.loads(self.env.gh_state.read_text())["responses"]
        self.env.set_gh([{"args": ["api", "graphql"], "seq": [reply([{"title": "no number"}])]}] + responses)
        r = self.gate()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("malformed gh reply", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_body_only_blocker_fails_dependencies(self):
        self.prime(head="270-slug", ticket_body="Blocked by: #5", body_blocked_by=[5])
        out = self.failed_only(self.gate(), "dependencies merged")
        self.assertIn("#270 has 1 open blocker(s): #5 (body)", out)

    def test_ticket_comes_from_the_closing_reference_without_a_ticket_branch(self):
        self.prime(body="Closes #41\n", ticket=41, blockers=1)
        self.failed_only(self.gate(), "dependencies merged")

    def test_base_that_is_not_the_default_branch_fails_dependencies(self):
        self.prime(head="270-slug", base="feature-x")
        out = self.failed_only(self.gate(), "dependencies merged")
        self.assertIn("based on feature-x, not main", out)

    def test_no_ticket_passes_dependencies(self):
        self.prime()
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("no ticket", r.stdout)

    def test_secret_paths_fail(self):
        for path in (".env", "cfg/.env.local", "k.pem", "a/b.key", "c.p12", "d.pfx", "e.keystore", "id_rsa", "id_rsa.pub",
                     "id_ed25519", "gcp-credentials.json", "credentials.json"):
            with self.subTest(path=path):
                self.prime(diff=added_file(path))
                out = self.failed_only(self.gate(), "no secrets added")
                self.assertIn(f"path {path}", out)
                runlog.log_path(self.env.repo, runlog.EVENTS_LOG).unlink()

    def test_secret_lines_fail(self):
        for line in ("-----BEGIN RSA PRIVATE " + "KEY-----", "-----BEGIN PRIVATE " + "KEY-----", "k = AKIA" + "ABCDEFGHIJKLMNOP",
                     "t = ghp_" + "a" * 20, "t = github_pat" + "_11ABC", "t = xo" + "xb-123", "t = xo" + "xa-1", "t = xo" + "xp-1"):
            with self.subTest(line=line):
                self.prime(diff=added_file("src/ok.py", line))
                self.failed_only(self.gate(), "no secrets added")
                runlog.log_path(self.env.repo, runlog.EVENTS_LOG).unlink()

    def test_a_removed_secret_line_or_deleted_secret_file_passes(self):
        diff = ("diff --git a/.env b/.env\ndeleted file mode 100644\n--- a/.env\n+++ /dev/null\n@@ -1 +0,0 @@\n-K=v\n"
                "diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n-t = ghp_" + "a" * 20 + "\n+t = 1\n")
        self.prime(diff=diff)
        self.assertEqual(self.gate().returncode, 0)

    def test_editing_an_existing_env_example_or_credentials_file_passes(self):
        # Only an added or renamed path counts (#270): the worker cannot clear a file that was already tracked.
        for path in (".env.example", "config/credentials.json"):
            with self.subTest(path=path):
                self.prime(diff=CLEAN_DIFF.replace("src/a.py", path))
                self.assertEqual(self.gate().returncode, 0)

    def test_building_tickets_are_held_when_run_from_a_worktree(self):
        # The state file lives only in the main checkout; the gate runs from the worker's linked worktree.
        self.prime(issues=[{"number": 12, "labels": {"nodes": []}, "assignees": {"totalCount": 1}}])
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"session": "test-session", "tickets": {"12": {"touch_map": ["src/a.py:5"]}}}))
        wt = self.env.repo / ".claude" / "worktrees" / "w1"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "w1", str(wt)], cwd=self.env.repo, check=True)
        self.assertFalse((wt / ".claude" / "orchestrate-state.json").exists())
        out = self.failed_only(self.run_from(wt), "no overlap with held paths")
        self.assertIn("building #12", out)

    def test_a_renamed_to_secret_path_fails(self):
        diff = "diff --git a/old.txt b/.env\nsimilarity index 100%\nrename from old.txt\nrename to .env\n"
        self.prime(diff=diff)
        self.failed_only(self.gate(), "no secrets added")

    def test_lockfile_without_its_manifest_fails(self):
        pairs = {"package-lock.json": "package.json", "yarn.lock": "package.json", "pnpm-lock.yaml": "package.json",
                 "poetry.lock": "pyproject.toml", "uv.lock": "pyproject.toml", "Cargo.lock": "Cargo.toml",
                 "Gemfile.lock": "Gemfile", "go.sum": "go.mod", "composer.lock": "composer.json"}
        for lock, manifest in pairs.items():
            with self.subTest(lock=lock):
                self.prime(diff=added_file(f"web/{lock}", "{}"))
                out = self.failed_only(self.gate(), "lockfile changes have a manifest change")
                self.assertIn(f"web/{lock} changed but {manifest} did not", out)
                runlog.log_path(self.env.repo, runlog.EVENTS_LOG).unlink()
                # the same lockfile with its manifest beside it passes
                self.prime(diff=added_file(f"web/{lock}", "{}") + added_file(f"web/{manifest}", "{}"))
                self.assertEqual(self.gate().returncode, 0)
                runlog.log_path(self.env.repo, runlog.EVENTS_LOG).unlink()

    def test_manifest_in_another_directory_does_not_count(self):
        self.prime(diff=added_file("a/package-lock.json", "{}") + added_file("b/package.json", "{}"))
        self.failed_only(self.gate(), "lockfile changes have a manifest change")


if __name__ == "__main__":
    unittest.main()
