"""scripts/merge --approved-head: behind leads to exit 5, a change since the approved head to exit 6."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402
import github  # noqa: E402


READY_CONFIG = ("## Review\n\n- **Risk paths** (any PR touching them is reviewed as L): `auth/*`\n"
                "- **Tiers:** S is at most <10> changed lines in at most <2> files, none on a risk path; "
                "L touches a risk path or changes more than <400> lines; M is everything else.\n")

GREEN = [{"name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"},
         {"context": "lint", "state": "SUCCESS"}]

WF = "repos/{owner}/{repo}/contents/.github/workflows?ref="

THREADS_NONE = {"stdout": json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}})}


def issues_reply(ticket, blockers=(), body="", open_others=()):
    """The open-issues reply: the ticket (native `blockers` as (number, state), `body`) and other open issues."""
    nodes = [{"number": ticket, "body": body, "blockedBy": {"nodes": [{"number": n, "state": st} for n, st in blockers]}}]
    nodes += [{"number": n} for n in open_others]
    return {"stdout": json.dumps({"data": {"repository": {"issues": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})}


def threads_reply(*nodes):
    """A reviewThreads reply: each node is (isResolved, isOutdated, author, url)."""
    return {"stdout": json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"id": f"T{i}", "isResolved": r, "isOutdated": o, "path": "a.py", "line": 1,
                   "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"author": {"login": a}, "body": "[blocking] x", "url": u}]}}
                  for i, (r, o, a, u) in enumerate(nodes)]}}}}})}


class ReadinessTest(unittest.TestCase):
    """--approved-head: behind leads to exit 5, a change since the approved head needing re-review to exit 6."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.repo = self.env.repo
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "orchestration.md").write_text(READY_CONFIG)
        git(self.repo, "checkout", "-q", "-b", "feat-br")
        self.approved = self.env.commit("a.txt", "one\n", "feat: a")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")

    def push(self, files, message="feat: more"):
        git(self.repo, "checkout", "-q", "feat-br")
        for path, text in files.items():
            sha = self.env.commit(path, text, message)
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        return sha

    def merge(self, head, behind="0", update=None, approved="", flag=True, checks=GREEN, blockers=(),
              branch="feat-br", closing=(), ticket=9, threads=None, issues=None, workflows=None):
        pr_json = {"state": "OPEN", "headRefName": branch, "headRefOid": head, "statusCheckRollup": checks,
                   "isCrossRepository": False, "closingIssuesReferences": [{"number": n} for n in closing]}
        view = {"stdout": json.dumps(pr_json)}
        merged = {"stdout": json.dumps(dict(pr_json, state="MERGED"))}
        self.env.set_gh([
            {"args": ["pr", "view"], "seq": [view, merged]},
            {"args": ["repo", "view"], "stdout": '{"defaultBranchRef":{"name":"main"},"owner":{"login":"o"},"name":"r"}'},
            {"args": ["api", WF + head],
             **(workflows or {"exit": 1, "stderr": "gh: Not Found (HTTP 404)"})},
            {"args": ["api", "graphql", "-f", "query=" + github.ISSUES_QUERY],
             **(issues if issues is not None else issues_reply(ticket, blockers))},
            {"args": ["api", "graphql"], **(threads if threads is not None else THREADS_NONE)},
            {"args": ["api", f"repos/{{owner}}/{{repo}}/compare/main...{branch}", "--jq", ".behind_by"],
             "stdout": behind + "\n"},
            {"args": ["pr", "update-branch"], **(update or {"exit": 0})},
            {"args": ["pr", "merge"], "exit": 0},
            {"args": ["pr", "list"], "stdout": "[]"},
            {"args": ["api", "-X"], "exit": 0},
        ])
        extra = ["--approved-head", approved or self.approved] if flag else []
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, check=False)

    def called(self, prefix):
        return [c for c in self.env.gh_calls() if c[: len(prefix)] == prefix]

    def test_empty_rollup_without_workflow_files_is_green(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ci: none configured", p.stdout)
        self.assertEqual(len(self.called(["api", WF + head])), 1)

    def test_empty_rollup_with_a_listing_but_no_workflow_is_green(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head, workflows={"stdout": '[{"name": "README.md"}]'})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ci: none configured", p.stdout)

    def test_empty_rollup_with_a_workflow_file_is_pending(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head, workflows={"stdout": '[{"name": "ci.yml"}]'})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ci: pending", p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_unreadable_workflows_exit_2(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head, workflows={"exit": 1, "stderr": "gh: Server Error (HTTP 500)"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_non_empty_rollup_makes_no_contents_call(self):
        head = self.push({"b.txt": "x\n"})
        self.merge(head, approved=head)
        self.assertEqual(self.called(["api", WF + head]), [])

    def test_behind_updates_the_branch_and_exits_5(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "update-branch"]), [["pr", "update-branch", "7"]])
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_failing_update_exits_1_with_the_output(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="1", update={"exit": 1, "stderr": "merge conflict between base and head"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("merge conflict between base and head", p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_risk_path_change_since_the_approved_head_exits_6(self):
        head = self.push({"auth/login.py": "x\n"})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/login.py", p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_too_many_lines_exits_6(self):
        head = self.push({"c.txt": "x\n" * 11})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_too_many_files_exits_6(self):
        head = self.push({"e.txt": "1\n", "f.txt": "2\n", "g.txt": "3\n"})
        self.assertEqual(self.merge(head).returncode, 6)

    def test_several_small_pushes_add_up(self):
        self.push({"c.txt": "x\n" * 6}, "feat: c")
        head = self.push({"d.txt": "x\n" * 6}, "feat: d")
        self.assertEqual(self.merge(head).returncode, 6)

    def test_only_default_branch_merges_since_the_approved_head_merge(self):
        # main moves on (a risk path, many lines), and the host merges it into the branch.
        self.env.commit("auth/core.py", "x\n" * 50, "feat: main moves")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "feat-br")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge branch main", "main")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        p = self.merge(head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 1)

    def update_from_main(self, main_path, text):
        """main gains a commit, then the host merges it into feat-br (update-branch); the new head."""
        self.env.commit(main_path, text, "feat: main moves")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "feat-br")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge branch main", "main")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        return head

    def on_br(self, edit, message="fix: edit"):
        """Run `edit(repo)` on feat-br, commit everything and push; the new head."""
        git(self.repo, "checkout", "-q", "feat-br")
        edit(self.repo)
        git(self.repo, "add", "-A", "--", ".", ":!.claude")  # not the harness's state file
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        return sha

    def test_a_file_added_then_removed_after_approval_is_in_the_delta(self):
        # The real #411 shape: a leaked 11-line file in the approved head, removed after.
        approved = self.on_br(lambda r: (r / "leak.txt").write_text("x\n" * 11))
        head = self.on_br(lambda r: (r / "leak.txt").unlink())
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("changed 11 line(s) in 1 file(s)", p.stdout)

    def test_a_file_added_and_removed_after_approval_plus_one_line_fix_is_one_line(self):
        # The #411 shape after a main update: the leak never reaches the head, so only the fix is left.
        self.update_from_main("big.txt", "x\n" * 50)
        self.on_br(lambda r: (r / "leak.txt").write_text("x\n" * 60))
        self.on_br(lambda r: (r / "leak.txt").unlink())
        head = self.on_br(lambda r: (r / "c.txt").write_text("y\n"))
        p = self.merge(head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_risk_path_is_caught_with_diff_noprefix_set(self):
        git(self.repo, "config", "diff.noprefix", "true")
        head = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "login.py").write_text("x\n")))
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/login.py", p.stdout)

    def moved_block(self):
        """A block of added lines in a risk-path file of main, moved after approval; the merge result."""
        body = [f"line{i}\n" for i in range(40)]
        block = [f"new{i}\n" for i in range(12)]
        self.env.commit("auth/big.txt", "".join(body), "feat: big on main")
        git(self.repo, "push", "-q", "origin", "main")

        def insert_at(n):
            def edit(r):
                git(r, "merge", "-q", "main")
                (r / "auth" / "big.txt").write_text("".join(body[:n] + block + body[n:]))
            return edit
        approved = self.on_br(insert_at(5))
        head = self.on_br(lambda r: (r / "auth" / "big.txt").write_text("".join(body[:30] + block + body[30:])))
        return self.merge(head, approved=approved)

    def test_a_moved_block_is_a_non_zero_delta(self):
        p = self.moved_block()
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/big.txt", p.stdout)

    def test_a_moved_block_is_still_counted_with_diff_context_zero(self):
        git(self.repo, "config", "diff.context", "0")
        p = self.moved_block()
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)

    def test_a_revert_is_in_the_delta(self):
        approved = self.on_br(lambda r: (r / "auth" / "x.py").parent.mkdir(exist_ok=True) or (r / "auth" / "x.py").write_text("1\n"))
        head = self.on_br(lambda r: (r / "auth" / "x.py").unlink())
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/x.py", p.stdout)

    def test_a_line_move_on_a_risk_path_is_in_the_delta(self):
        text = "".join(f"line{i}\n" for i in range(10))
        moved = "".join(f"line{i}\n" for i in (1, 0, 2, 3, 4, 5, 6, 7, 8, 9))
        approved = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "m.py").write_text(text)))
        head = self.on_br(lambda r: (r / "auth" / "m.py").write_text(moved))
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/m.py", p.stdout)

    def test_a_mode_only_change_on_a_risk_path_is_in_the_delta(self):
        approved = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "run.sh").write_text("echo\n")))
        head = self.on_br(lambda r: (r / "auth" / "run.sh").chmod(0o755))
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/run.sh", p.stdout)

    def test_an_empty_file_added_on_a_risk_path_is_in_the_delta(self):
        head = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "empty").write_text("")))
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/empty", p.stdout)

    def test_an_empty_file_removed_counts_as_a_file(self):
        approved = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "empty").write_text("")))
        head = self.on_br(lambda r: (r / "auth" / "empty").unlink())
        self.assertEqual(self.merge(head, approved=approved).returncode, 6)

    def test_main_editing_the_prs_own_file_elsewhere_is_not_in_the_delta(self):
        body = "".join(f"line{i}\n" for i in range(40))
        self.env.commit("big.txt", body, "feat: big on main")
        git(self.repo, "push", "-q", "origin", "main")
        def edit_near_top(r):
            git(r, "merge", "-q", "main")
            (r / "big.txt").write_text(body.replace("line2\n", "line2 PR\n"))
        approved = self.on_br(edit_near_top)
        # main now edits the far end of the same file, and the host merges it into the branch.
        self.env.commit("big.txt", body.replace("line37\n", "line37 main\n"), "feat: main edits big")
        git(self.repo, "push", "-q", "origin", "main")
        head = self.on_br(lambda r: git(r, "merge", "-q", "--no-ff", "-m", "Merge main", "main"))
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_main_update_alone_is_an_empty_delta(self):
        head = self.update_from_main("big.txt", "x\n" * 50)
        p = self.merge(head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_delta_after_a_main_update_is_the_prs_own_lines_only(self):
        self.update_from_main("big.txt", "x\n" * 50)
        git(self.repo, "checkout", "-q", "feat-br")
        head = self.env.commit("c.txt", "y\n" * 11, "fix: more")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("changed 11 line(s) in 1 file(s)", p.stdout)

    def test_one_pr_line_after_a_main_update_is_within_the_thresholds(self):
        self.update_from_main("big.txt", "x\n" * 50)
        git(self.repo, "checkout", "-q", "feat-br")
        head = self.env.commit("c.txt", "y\n", "fix: one line")
        git(self.repo, "push", "-q", "origin", "feat-br")
        git(self.repo, "checkout", "-q", "main")
        self.assertEqual(self.merge(head).returncode, 0)

    def test_small_change_within_thresholds_merges(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head).returncode, 0)

    def test_no_flag_keeps_todays_behaviour(self):
        head = self.push({"auth/login.py": "x\n" * 50})
        p = self.merge(head, behind="3", flag=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "update-branch"]), [])
        self.assertEqual([c for c in self.env.gh_calls() if "compare" in " ".join(c)], [])

    def test_unknown_approved_head_needs_re_review(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, approved="f" * 40).returncode, 6)

    def refused(self, p, clause):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(clause, p.stdout)
        self.assertEqual(self.called(["pr", "merge"]), [])
        self.assertEqual(self.called(["pr", "update-branch"]), [])

    def test_pending_ci_refuses_naming_the_check(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=GREEN + [{"name": "slow", "status": "IN_PROGRESS", "conclusion": ""}])
        self.refused(p, "CI pending")
        self.assertIn("slow", p.stdout)
        self.assertIn(head[:12], p.stdout)

    def test_failed_check_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2", checks=GREEN + [{"name": "unit", "status": "COMPLETED", "conclusion": "FAILURE"}])
        self.refused(p, "CI failed")
        self.assertIn("unit", p.stdout)

    def test_cancelled_check_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[{"name": "unit", "status": "COMPLETED", "conclusion": "CANCELLED"}])
        self.refused(p, "CI failed")

    def test_no_checks_refuses_as_pending(self):
        head = self.push({"b.txt": "x\n"})
        self.refused(self.merge(head, checks=[], workflows={"stdout": "[{\"name\": \"ci.yaml\"}]"}), "ci: pending")

    def test_skipped_and_neutral_checks_pass(self):
        head = self.push({"b.txt": "x\n"})
        checks = GREEN + [{"name": "opt", "status": "COMPLETED", "conclusion": "SKIPPED"},
                          {"name": "n", "status": "COMPLETED", "conclusion": "NEUTRAL"}]
        self.assertEqual(self.merge(head, checks=checks).returncode, 0)

    def test_open_blocker_on_the_closing_reference_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], blockers=[(1, "OPEN"), (2, "OPEN")])
        self.refused(p, "ticket #9 has 2 open blocker(s)")

    def test_open_blocker_on_the_branch_number_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, branch="9-feat", blockers=[(1, "OPEN")])
        self.refused(p, "ticket #9 has 1 open blocker(s)")

    def test_branch_number_wins_over_a_closing_reference(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, branch="9-feat", closing=[4], blockers=[(1, "OPEN")])
        self.refused(p, "ticket #9")

    def test_unreadable_blockers_exit_2_and_merge_nothing(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], issues={"stdout": "lots"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("blockers of #9", p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_closed_blockers_pass(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], blockers=[(8, "CLOSED")])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 1)

    def test_no_ticket_passes_without_asking_for_blockers(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, blockers=[(3, "OPEN")]).returncode, 0)
        self.assertEqual([c for c in self.env.gh_calls() if "query=" + github.ISSUES_QUERY in c], [])

    def test_a_body_only_blocker_refuses_too(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], issues=issues_reply(9, body="Blocked by: #5", open_others=[5]))
        self.refused(p, "ticket #9 has 1 open blocker(s): #5 (body)")
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_without_the_flag_ci_is_not_checked(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, checks=[], flag=False).returncode, 0)

    def test_unresolved_thread_refuses_with_count_author_and_url(self):
        p = self.merge(self.approved, threads=threads_reply((False, False, "rev", "https://x/1"), (True, False, "z", "https://x/2")))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines(), ["PR #7 is not ready: 1 unresolved review thread(s)", "  rev https://x/1"])
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_outdated_unresolved_thread_refuses(self):
        p = self.merge(self.approved, threads=threads_reply((False, True, "rev", "https://x/1")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("1 unresolved review thread(s)", p.stdout)

    def test_a_humans_thread_refuses_like_a_reviewers(self):
        p = self.merge(self.approved, threads=threads_reply((False, False, "shrujan", "https://x/9"), (False, False, "rev", "https://x/8")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("2 unresolved", p.stdout)
        self.assertEqual(p.stdout.splitlines()[1:], ["  shrujan https://x/9", "  rev https://x/8"])

    def test_all_resolved_passes(self):
        p = self.merge(self.approved, threads=threads_reply((True, False, "rev", "u"), (True, True, "rev", "u")))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(["pr", "merge"])), 1)

    def test_no_threads_passes(self):
        self.assertEqual(self.merge(self.approved).returncode, 0)

    def test_failed_threads_read_exits_2_and_merges_nothing(self):
        p = self.merge(self.approved, threads={"exit": 1, "stderr": "boom"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("could not read the review threads of PR #7", p.stdout + p.stderr)
        self.assertEqual(self.called(["pr", "merge"]), [])

    def test_without_the_flag_threads_are_not_read(self):
        p = self.merge(self.approved, flag=False, threads=threads_reply((False, False, "rev", "u")))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.called(["api", "graphql"]), [])


if __name__ == "__main__":
    unittest.main()
