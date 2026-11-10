"""CLI tests for scripts/gate, driven through the harness with a fake gh."""

import json
import shutil
import tempfile
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
            {"args": ["api", "-H", "Accept: application/vnd.github.diff", PULL], "stdout": diff},
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

    def tiers(self, risk="`scripts/gate`"):
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("## Review\n\n"
                     f"- **Risk paths** (any PR touching them is reviewed as L): {risk}\n"
                     "- **Tiers:** S is at most 50 changed lines in at most 3 files, none on a risk path; "
                     "L touches a risk path or changes more than 400 lines; M is everything else.\n"
                     "- **Post reviews:** no\n")

    def tier_line(self, r):
        lines = r.stdout.splitlines()
        self.assertTrue(lines[-1].startswith("next:"), lines)
        return lines[-2]

    def test_tier_line_precedes_next(self):
        label = lambda name: [{"number": 270, "labels": {"nodes": [{"name": name}]}}]
        small = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        risky = small.replace("src/a.py", "scripts/gate")
        rows = [  # (diff, ticket labels, tier)
            (small, "", "tier S"),
            (risky, "", "tier L"),
            (small, "size:M", "tier M"),   # a label raises the computed tier
            (small, "size:L", "tier L"),
            (risky, "size:S", "tier L"),   # and never lowers it
        ]
        for diff, name, want in rows:
            with self.subTest(diff=diff[:30], label=name):
                self.tiers()
                self.prime(diff=diff, body="Closes #270", issues=label(name) if name else ())
                r = self.gate()
                self.assertEqual(self.tier_line(r), want, r.stdout + r.stderr)

    def test_send_back_carries_no_tier_line(self):
        self.tiers()
        self.prime(title="bad title")
        r = self.gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("tier ", r.stdout)
        self.assertEqual(r.stdout.splitlines()[-1], "next: send-back 7")

    def test_changed_lines_counts_inside_hunks_only(self):
        import importlib.machinery
        import importlib.util
        from pathlib import Path
        scripts = Path(__file__).resolve().parent.parent / "scripts"
        sys.path.insert(0, str(scripts))
        gate = importlib.machinery.SourceFileLoader("gate_mod", str(scripts / "gate"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("gate_mod", gate))
        gate.exec_module(mod)
        diff = ("diff --git a/x.md b/x.md\n--- a/x.md\n+++ b/x.md\n@@ -1,3 +1,3 @@\n context\n\n"
                "----\n+++x\n+new\n")  # a removed `---` rule and an added `++x`, plus +new; the empty line is context
        self.assertEqual(mod.changed_lines(diff), 3)

    def test_tier_counts_changed_lines_from_the_config(self):
        self.tiers()
        big = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n" + "+x\n" * 401
        self.prime(diff=big)
        self.assertEqual(self.tier_line(self.gate()), "tier L")

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

    def test_every_gh_call_runs_in_cwd_not_the_process_directory(self):
        # The fake gh is moved aside and a wrapper takes its place: it logs its directory, then hands over.
        real = Path(self.env.bin) / "gh"
        real.rename(real.with_name("gh-real"))
        log = Path(self.env.bin) / "cwds"
        real.write_text(f'#!/bin/sh\npwd -P >> "{log}"\nexec "{real.with_name("gh-real")}" "$@"\n')
        real.chmod(0o755)
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        self.prime(blockers=0)
        r = self.env.run("gate", "7", "--cwd", str(self.env.repo), "--retry-delay", "0", check=False, cwd=elsewhere)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        seen = set(log.read_text().split())
        self.assertEqual(seen, {str(self.env.repo.resolve())})

    def test_no_session_keeps_the_verdict_and_exit_code_and_says_so_on_stderr(self):
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.prime()
        self.config("codegen is current (run: `echo ok`), see `README.md`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], f"next: review {HEAD}")
        self.assertIn("runlog: no session, gate event not written", r.stderr)

    def test_passing_gate(self):
        self.prime()
        self.config("codegen is current (run: `echo ok`), see `README.md`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines.pop(), f"next: review {HEAD}")
        self.assertEqual(lines.pop(), "tier M")  # no Tiers in the config: neither S nor L is reachable
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

    def test_files_outside_the_touch_map_print_an_information_line(self):
        self.prime(head="270-thing", diff=CLEAN_DIFF + added_file("src/b.py"))
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"session": "test-session", "tickets": {"270": {"touch_map": "src/a.py"}}}))
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("touch map: +1 files outside the map", r.stdout)

    def test_no_touch_map_record_prints_no_outside_the_map_line(self):
        self.prime(head="270-thing", diff=CLEAN_DIFF + added_file("src/b.py"))
        r = self.gate()
        self.assertNotIn("outside the map", r.stdout)

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
        self.assertEqual(len(r.stdout.splitlines()), 9)  # the built-in checks only, then tier: and next:
        self.assertNotIn("README.md", r.stdout)

    def test_missing_cwd_exits_2_before_any_check(self):
        self.prime()
        r = self.gate("--cwd", str(self.env.repo / "gone"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--cwd", r.stderr)
        self.assertIn("does not exist", r.stderr)
        self.assertEqual(self.env.gh_calls(), [])
        self.assertEqual(self.env.events(), [])

    def worktree_copy(self, line):
        """A PR tree under the main checkout whose own copy of the config carries `line`."""
        other = self.env.repo / "wt"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "orchestration.md").write_text(f"## Review\n\n- **Repo-specific checks:** {line}\n")
        return other

    def test_cwd_is_where_repo_commands_run_and_the_main_checkout_holds_the_config(self):
        self.prime()
        self.config("where (run: `pwd`)")
        other = self.worktree_copy("none")
        r = self.gate("--cwd", str(other))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("pwd", r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 10)

    def test_a_config_edit_in_the_prs_worktree_does_not_change_the_gate(self):
        # The PR adds a failing `run:` command to its own copy of the config: the gate judges by the main checkout's.
        self.prime()
        self.config("none")
        other = self.worktree_copy("sneaky (run: `exit 4`)")
        r = self.gate("--cwd", str(other))
        self.assertEqual((r.returncode, self.next_lines(r)), (0, ["next: review " + HEAD]), r.stdout + r.stderr)
        self.assertNotIn("exit 4", r.stdout)

    def test_a_config_edit_in_the_prs_worktree_cannot_remove_a_check(self):
        self.prime()
        self.config("codegen (run: `exit 4`)")
        other = self.worktree_copy("none")
        r = self.gate("--cwd", str(other))
        self.assertEqual((r.returncode, self.next_lines(r)), (1, ["next: send-back 7"]), r.stdout + r.stderr)
        self.assertIn("exit 4", r.stdout)

    def test_the_tier_thresholds_come_from_the_main_checkout(self):
        self.prime()
        self.tiers(risk="`src/a.py`")  # the PR touches it: L
        other = self.worktree_copy("none")  # the copy has no Risk paths row
        r = self.gate("--cwd", str(other))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("tier L", r.stdout)

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
        self.assertEqual(len(r.stdout.splitlines()), 9)

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

    def building(self, touch_map="src/a.py"):
        node = {"number": 12, "labels": {"nodes": []}, "assignees": {"totalCount": 1}}
        self.prime(issues=[node])
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"session": "test-session", "tickets": {"12": {"touch_map": touch_map}}}))

    def test_an_overlap_with_a_building_ticket_is_a_hold_naming_it_not_a_send_back(self):
        # A ticket still building (no PR yet) holds src/a.py: the PR is fine and waits for it (#535).
        self.building()
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (3, ["next: wait-ticket 7 12"]), r.stdout + r.stderr)
        self.assertIn("waits for #12 (still building) to merge: src/a.py", r.stdout)
        self.assertEqual((self.event()["outcome"], self.event()["failed"]), ("unknown", []))

    def test_an_overlap_with_an_exclusive_hold_is_still_a_send_back(self):
        # An overlap the PR itself must fix: the path is held by no ticket that will merge.
        self.building()
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("## Slots\n\n- **Exclusive holds:** `src/a.py`\n")
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (1, ["next: send-back 7"]), r.stdout + r.stderr)
        self.assertIn("overlap: src/a.py", r.stdout)

    def test_the_younger_of_two_open_prs_on_one_path_waits_for_the_older(self):
        self.prime(others=[{"number": 5, "headRefName": "12-older"}])
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (3, ["next: wait-older 7 5"]), r.stdout + r.stderr)
        self.assertIn("waits for #12 (PR #5) to merge: src/a.py", r.stdout)
        self.assertNotIn("fail", r.stdout.replace("failed", ""))
        self.assertEqual((self.event()["outcome"], self.event()["failed"]), ("unknown", []))

    def test_the_older_of_two_open_prs_on_one_path_passes(self):
        self.prime(others=[{"number": 9, "headRefName": "other"}])
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pass", r.stdout)

    def test_waiting_with_another_failure_is_sent_back(self):
        self.prime(title="Add the thing", others=[{"number": 5, "headRefName": "12-older"}])
        r = self.gate()
        self.assertEqual((r.returncode, self.next_lines(r)), (1, ["next: send-back 7"]))
        self.assertIn("waits for #12 (PR #5)", r.stdout)

    def test_a_holds_library_error_is_could_not_run_not_an_overlap(self):
        from importlib.machinery import SourceFileLoader
        from unittest import mock
        from importlib.util import module_from_spec, spec_from_loader
        loader = SourceFileLoader("gate_under_test", str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))
        gate = module_from_spec(spec_from_loader("gate_under_test", loader))
        loader.exec_module(gate)
        snap = mock.Mock()
        with mock.patch.object(gate.holds, "find", side_effect=OSError("boom")), \
                mock.patch.object(gate.repo, "main_checkout", return_value=str(self.env.repo)):
            out = gate.overlap_check(7, str(self.env.repo), snap)
        self.assertIsInstance(out, str)
        self.assertIn("holds could not run: boom", out)

    def test_run_gate_exit_codes_are_pinned(self):
        """main() maps 0, 1 and 3 to a verdict and every other code to could-not-run: a new code must be added to
        both on purpose, so this fails when run_gate returns one the mapping doesn't know (#453)."""
        import re
        src = (Path(__file__).resolve().parent.parent / "scripts" / "gate").read_text()
        body = src.split("def run_gate", 1)[1]
        codes = set(map(int, re.findall(r"return (\d)\b", body))) | set(map(int, re.findall(r"else (\d)\b", body)))
        codes |= {0, 1, 3}  # the final `return 3 if undecided else 1 if failed else 0`
        self.assertEqual(codes, {0, 1, 2, 3})

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
                self.env.clear_events()

    def test_secret_lines_fail(self):
        for line in ("-----BEGIN RSA PRIVATE " + "KEY-----", "-----BEGIN PRIVATE " + "KEY-----", "k = AKIA" + "ABCDEFGHIJKLMNOP",
                     "t = ghp_" + "a" * 20, "t = github_pat" + "_11ABC", "t = xo" + "xb-123", "t = xo" + "xa-1", "t = xo" + "xp-1"):
            with self.subTest(line=line):
                self.prime(diff=added_file("src/ok.py", line))
                self.failed_only(self.gate(), "no secrets added")
                self.env.clear_events()

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
        r = self.run_from(wt)
        self.assertEqual((r.returncode, self.next_lines(r)), (3, ["next: wait-ticket 7 12"]), r.stdout + r.stderr)
        self.assertIn("#12 (still building)", r.stdout)

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
                self.env.clear_events()
                # the same lockfile with its manifest beside it passes
                self.prime(diff=added_file(f"web/{lock}", "{}") + added_file(f"web/{manifest}", "{}"))
                self.assertEqual(self.gate().returncode, 0)
                self.env.clear_events()

    def test_manifest_in_another_directory_does_not_count(self):
        self.prime(diff=added_file("a/package-lock.json", "{}") + added_file("b/package.json", "{}"))
        self.failed_only(self.gate(), "lockfile changes have a manifest change")


if __name__ == "__main__":
    unittest.main()
