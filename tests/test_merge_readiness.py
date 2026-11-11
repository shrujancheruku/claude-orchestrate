"""scripts/merge --approved-head: behind leads to exit 5, a change since the approved head to exit 6."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import PR_GRAPHQL, ScriptEnv, gh_repo_view, git, pr_graphql_reply, rest_pull  # noqa: E402
import github  # noqa: E402


READY_CONFIG = ("## Review\n\n- **Risk paths** (any PR touching them is reviewed as L): `auth/*`\n"
                "- **Tiers:** S is at most <10> changed lines in at most <2> files, none on a risk path; "
                "L touches a risk path or changes more than <400> lines; M is everything else.\n\n"
                "## Gates\n\n- **Merge gates:** PRs touching `hooks/**` or `gated.txt` are merged by the developer. "
                "release-please's `chore(main): release` PRs are never merged by the orchestrator.\n")

GREEN = [{"name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"},
         {"context": "lint", "state": "SUCCESS"}]

MERGE = ["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/merge"]
UPDATE = ["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/update-branch"]
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
    """A reviewThreads reply: each node is (isResolved, isOutdated, author, url[, authorAssociation: COLLABORATOR])."""
    return {"stdout": json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"id": f"T{i}", "isResolved": r, "isOutdated": o, "path": "a.py", "line": 1,
                   "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"author": {"login": a}, "authorAssociation": (assoc or ["COLLABORATOR"])[0],
                                           "body": "[blocking] x", "url": u}]}}
                  for i, (r, o, a, u, *assoc) in enumerate(nodes)]}}}}})}


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
              branch="feat-br", closing=(), ticket=9, threads=None, issues=None, workflows=None, fork=False, after_head=None,
              files=("a.txt",), title=None, state="OPEN", files_reply=None, more=(), merge_state=None):
        pr_json = {"state": "OPEN", "headRefName": branch, "headRefOid": head, "statusCheckRollup": checks,
                   "isCrossRepository": fork, "closingIssuesReferences": [{"number": n} for n in closing]}
        view = {"stdout": pr_graphql_reply(dict(pr_json, state=state))}
        merged = {"stdout": pr_graphql_reply(dict(pr_json, state="MERGED"))}
        self.env.set_gh([
            {"args": PR_GRAPHQL, "seq": [view, merged]},
            {"args": ["api", "~repos/{owner}/{repo}/pulls/7/files"],
             **(files_reply or {"stdout": json.dumps([{"filename": f} for f in files])})},
            *([{"args": ["api", "repos/{owner}/{repo}/pulls/7"], "stdout": json.dumps(rest_pull(title=title))}]
              if title and not after_head else []),
            *([{"args": ["api", "repos/{owner}/{repo}/pulls/7"],
                "seq": [{"stdout": json.dumps(rest_pull(headRefOid=h))}
                        for h in ([after_head] if isinstance(after_head, str) else after_head)]}] if after_head else []),
            gh_repo_view(),
            *([{"args": ["api", "repos/{owner}/{repo}/pulls/7"],
                "stdout": json.dumps(rest_pull(mergeStateStatus=merge_state))}] if merge_state else []),
            {"args": ["api", WF + head],
             **(workflows or {"exit": 1, "stderr": "gh: Not Found (HTTP 404)"})},
            {"args": ["api", "graphql", "-f", "query=" + github.ISSUES_QUERY],
             **(issues if issues is not None else issues_reply(ticket, blockers))},
            {"args": ["api", "graphql"], **(threads if threads is not None else THREADS_NONE)},
            {"args": ["api", f"repos/{{owner}}/{{repo}}/compare/main...{branch}", "--jq", ".behind_by"],
             "stdout": behind + "\n"},
            {"args": list(UPDATE), **(update or {"exit": 0})},
            {"args": list(MERGE), "exit": 0},
            {"args": ["api", "~repos/{owner}/{repo}/pulls?state=open"], "stdout": "[]"},
            {"args": ["api", "-X", "DELETE"], "exit": 0},
        ])
        extra = ["--approved-head", approved or self.approved] if flag else []
        return self.env.run("merge", "7", "--interval", "0", "--timeout", "5", *extra, *more, check=False)

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
        self.assertEqual(self.called(MERGE), [])
        self.assertEqual(p.stdout.splitlines()[-1], f"next: wait-ci 7 {head}")

    def test_an_upper_case_yml_workflow_name_counts_as_a_workflow_file(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head, workflows={"stdout": '[{"name": "ci.YML"}]'})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ci: pending", p.stdout)
        self.assertEqual(self.called(MERGE), [])

    def test_unreadable_workflows_exit_2(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=[], approved=head, workflows={"exit": 1, "stderr": "gh: Server Error (HTTP 500)"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: could-not-run")

    def test_non_empty_rollup_makes_no_contents_call(self):
        head = self.push({"b.txt": "x\n"})
        self.merge(head, approved=head)
        self.assertEqual(self.called(["api", WF + head]), [])

    def test_behind_updates_the_branch_and_exits_5(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])
        self.assertEqual(self.called(MERGE), [])
        # the head is still the old one right after update-branch: the host may not have applied it yet
        self.assertEqual(p.stdout.splitlines()[-1], "next: wait-ci 7 ?")

    def main_moves(self, path, text="m\n"):
        """main gains a commit (pushed) that the branch lacks: the branch is behind."""
        self.env.commit(path, text, f"feat: main changes {path}")
        git(self.repo, "push", "-q", "origin", "main")

    def test_behind_with_no_file_overlap_merges_without_an_update(self):
        head = self.push({"b.txt": "x\n"})
        self.main_moves("m.txt")
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PR #7 is behind main by 1, no file overlap; merging without an update", p.stdout)
        self.assertEqual(self.called(UPDATE), [])
        self.assertEqual(len(self.called(MERGE)), 1)
        self.assertIn(f"sha={head}", self.called(MERGE)[0])  # --match-head-commit stays
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_behind_with_an_overlapping_file_updates(self):
        head = self.push({"b.txt": "x\n"})
        self.main_moves("b.txt", "other\n")
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])
        self.assertEqual(self.called(MERGE), [])

    def test_behind_updates_when_the_default_branch_cannot_be_fetched(self):
        head = self.push({"b.txt": "x\n"})
        self.main_moves("m.txt")
        git(self.repo, "remote", "set-url", "origin", str(self.repo / "no-such-remote"))
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])

    def test_behind_with_a_risk_path_file_updates(self):
        head = self.push({"auth/y.py": "x\n"})
        self.main_moves("m.txt")
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN", flag=True)
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])

    def test_behind_when_the_config_requires_an_update_updates(self):
        (self.repo / ".claude" / "orchestration.md").write_text(READY_CONFIG + "\n## PRs and merging\n\n- **Merge:** up to date: required\n")
        head = self.push({"b.txt": "x\n"})
        self.main_moves("m.txt")
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])

    def test_behind_with_a_merge_state_other_than_clean_updates(self):
        head = self.push({"b.txt": "x\n"})
        self.main_moves("m.txt")
        p = self.merge(head, behind="1", approved=head, merge_state="BLOCKED")
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [UPDATE])

    def test_a_merge_line_that_says_when_overlapping_is_the_default(self):
        (self.repo / ".claude" / "orchestration.md").write_text(READY_CONFIG + "\n## PRs and merging\n\n- **Merge:** up to date: when overlapping\n")
        head = self.push({"b.txt": "x\n"})
        self.main_moves("m.txt")
        p = self.merge(head, behind="1", approved=head, merge_state="CLEAN")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.called(UPDATE), [])

    def test_behind_reports_the_new_head_once_the_host_moved_it(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2", after_head="f" * 40)
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: wait-ci 7 " + "f" * 40)

    def test_behind_reads_the_moved_head_on_the_second_poll(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2", after_head=[head, "e" * 40])
        self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: wait-ci 7 " + "e" * 40)
        self.assertEqual(len(self.called(["api", "repos/{owner}/{repo}/pulls/7"])), 2)

    def test_a_second_update_branch_is_refused_while_the_first_is_in_flight(self):
        head = self.push({"b.txt": "x\n"})
        first = self.merge(head, behind="2")  # the head never moves: the update is still in flight
        self.assertEqual(first.stdout.splitlines()[-1], "next: wait-ci 7 ?")
        second = self.merge(head, behind="2")
        self.assertEqual(second.returncode, 5, second.stdout + second.stderr)
        self.assertEqual(second.stdout.splitlines()[-1], "next: wait-ci 7 ?")
        self.assertIn("not asking again", second.stdout)
        self.assertEqual(len(self.called(UPDATE)), 1)  # the call log spans both runs

    def test_a_second_update_branch_goes_ahead_once_the_window_has_passed(self):
        head = self.push({"b.txt": "x\n"})
        self.merge(head, behind="2")
        state = self.repo / ".claude" / "orchestrate-update-branch.json"
        rec = json.loads(state.read_text())
        rec["7"]["at"] -= 600
        state.write_text(json.dumps(rec))
        self.merge(head, behind="2")
        self.assertEqual(len(self.called(UPDATE)), 2)

    def test_the_record_goes_when_the_pr_merges_and_old_entries_are_dropped(self):
        head = self.push({"b.txt": "x\n"})
        self.merge(head, behind="2")
        state = self.repo / ".claude" / "orchestrate-update-branch.json"
        rec = json.loads(state.read_text())
        self.assertIn("7", rec)
        rec["3"] = {"from": "x", "at": rec["7"]["at"] - 3600}  # another PR's entry, an hour old
        rec["7"]["at"] -= 600  # past the 120 s window, so the next run updates and records again
        state.write_text(json.dumps(rec))
        self.merge(head, behind="2")  # records PR 7 again; the stale entry is dropped
        self.assertEqual(sorted(json.loads(state.read_text())), ["7"])
        p = self.merge(head)  # behind 0: the PR merges
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(state.read_text()), {})

    def test_a_second_update_branch_goes_ahead_when_the_head_moved(self):
        head = self.push({"b.txt": "x\n"})
        self.merge(head, behind="2")
        newer = self.push({"c.txt": "y\n"})
        self.merge(newer, behind="2")
        self.assertEqual(len(self.called(UPDATE)), 2)

    def test_the_printed_wait_ci_hint_runs_through_watch_cis_parser(self):
        import importlib.machinery
        import importlib.util
        head = self.push({"b.txt": "x\n"})
        for after in ("f" * 40, None):  # a moved head, and the `?` hint
            hint = self.merge(head, behind="2", after_head=after).stdout.splitlines()[-1]
            self.assertTrue(hint.startswith("next: wait-ci 7 "), hint)
            loader = importlib.machinery.SourceFileLoader("watch_ci_for_hint", str(Path(__file__).resolve().parent.parent / "scripts" / "watch-ci"))
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(mod)
            prs, heads = mod.parse_targets(hint.split()[2:])
            self.assertEqual(prs, ["7"])
            self.assertEqual(heads, {"7": after} if after else {})
            (self.repo / ".claude" / "orchestrate-update-branch.json").unlink(missing_ok=True)

    def test_failing_update_of_a_fork_pr_asks_the_user(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="1", fork=True, update={"exit": 1, "stderr": "cannot push to fork"})
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout.splitlines()[-1], "next: ask-user PR #7 is a fork; its branch can't be updated")

    def test_a_rate_limit_in_update_branch_ends_retry_after_its_time(self):
        head = self.push({"b.txt": "x\n"})
        # The limit is recorded in a cache shared by every agent on the machine: keep this one in a temp dir.
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            p = self.merge(head, behind="1", update={
                "exit": 1, "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertRegex(p.stdout.splitlines()[-1], r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("send-back", p.stdout)
        self.assertEqual(self.called(MERGE), [])

    def test_failing_update_exits_1_with_the_output(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="1", update={"exit": 1, "stderr": "merge conflict between base and head"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("merge conflict between base and head", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: send-back 7")
        self.assertEqual(self.called(MERGE), [])

    def test_risk_path_change_since_the_approved_head_exits_6(self):
        head = self.push({"auth/login.py": "x\n"})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("auth/login.py", p.stdout)
        self.assertEqual(self.called(MERGE), [])
        self.assertEqual(p.stdout.splitlines()[-1], "next: re-review 7")

    def test_too_many_lines_exits_6(self):
        head = self.push({"c.txt": "x\n" * 11})
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertEqual(self.called(MERGE), [])

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
        self.assertEqual(len(self.called(MERGE)), 1)

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

    def test_a_file_the_pr_added_then_removed_after_approval_counts_for_nothing(self):
        # The #411 shape (a leaked 11-line file, removed after approval): the file was never on main, so nothing of it lands (#651).
        approved = self.on_br(lambda r: (r / "leak.txt").write_text("x\n" * 11))
        head = self.on_br(lambda r: (r / "leak.txt").unlink())
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_file_that_exists_on_main_deleted_after_approval_still_counts(self):
        self.env.commit("old.txt", "x\n" * 11, "feat: old on main")
        git(self.repo, "push", "-q", "origin", "main")
        self.on_br(lambda r: git(r, "merge", "-q", "main"))
        head = self.on_br(lambda r: (r / "old.txt").unlink())
        p = self.merge(head)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn("changed 11 line(s) in 1 file(s)", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: re-review 7")

    def test_a_whitespace_only_fix_and_a_pr_added_file_removed_on_a_risk_path_is_merged(self):
        # PR #625's round-3 shape: a space in a risk-path file plus deleting a file the PR itself added.
        approved = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "guard.py").write_text("if x:\n    y = 1\n"),
                                         (r / "probe_tmp.py").write_text("print(1)\n" * 12)))
        head = self.on_br(lambda r: ((r / "auth" / "guard.py").write_text("if x:\n    y  =  1\n\n"), (r / "probe_tmp.py").unlink()))
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_a_behaviour_change_on_a_risk_path_after_approval_names_the_delta_scope(self):
        approved = self.on_br(lambda r: ((r / "auth").mkdir(exist_ok=True), (r / "auth" / "guard.py").write_text("if x:\n    y = 1\n")))
        head = self.on_br(lambda r: (r / "auth" / "guard.py").write_text("if x:\n    y = 2\n"))
        p = self.merge(head, approved=approved)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        self.assertIn(f"re-review scope: delta {approved[:12]}..{head[:12]} (S)", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: re-review 7")

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
        # A file main already has, edited in the approved head and restored after: the patch changed back, so it counts.
        self.env.commit("auth/x.py", "0\n", "feat: x on main")
        git(self.repo, "push", "-q", "origin", "main")
        approved = self.on_br(lambda r: (git(r, "merge", "-q", "main"), (r / "auth" / "x.py").write_text("1\n")))
        head = self.on_br(lambda r: (r / "auth" / "x.py").write_text("0\n"))
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
        self.env.commit("auth/empty", "", "feat: empty on main")
        git(self.repo, "push", "-q", "origin", "main")
        approved = self.on_br(lambda r: git(r, "merge", "-q", "main"))
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
        self.assertEqual(self.called(UPDATE), [])
        self.assertEqual([c for c in self.env.gh_calls() if "compare" in " ".join(c)], [])

    def test_unknown_approved_head_needs_re_review(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, approved="f" * 40).returncode, 6)

    def refused(self, p, clause):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(clause, p.stdout)
        self.assertEqual(self.called(MERGE), [])
        self.assertEqual(self.called(UPDATE), [])

    def test_pending_ci_refuses_naming_the_check(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, checks=GREEN + [{"name": "slow", "status": "IN_PROGRESS", "conclusion": ""}])
        self.refused(p, "CI pending")
        self.assertIn("slow", p.stdout)
        self.assertIn(head[:12], p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], f"next: wait-ci 7 {head}")

    def test_failed_check_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, behind="2", checks=GREEN + [{"name": "unit", "status": "COMPLETED", "conclusion": "FAILURE"}])
        self.refused(p, "CI failed")
        self.assertIn("unit", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: send-back 7")

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

    def test_every_check_skipped_refuses_as_pending(self):
        # A draft head whose ready_for_review run never started: only the draft-era skips are in the rollup.
        head = self.push({"b.txt": "x\n"})
        checks = [{"name": "test", "status": "COMPLETED", "conclusion": "SKIPPED"},
                  {"name": "n", "status": "COMPLETED", "conclusion": "NEUTRAL"}]
        p = self.merge(head, checks=checks)
        self.refused(p, "CI pending")
        self.assertEqual(p.stdout.splitlines()[-1], f"next: wait-ci 7 {head}")

    def test_open_blocker_on_the_closing_reference_refuses(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], blockers=[(1, "OPEN"), (2, "OPEN")])
        self.refused(p, "ticket #9 has 2 open blocker(s)")
        self.assertEqual(p.stdout.splitlines()[-1], "next: ask-user ticket #9 has 2 open blocker(s)")

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
        self.assertEqual(self.called(MERGE), [])

    def test_closed_blockers_pass(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], blockers=[(8, "CLOSED")])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(MERGE)), 1)

    def test_no_ticket_passes_without_asking_for_blockers(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, blockers=[(3, "OPEN")]).returncode, 0)
        # no `api graphql` call carries the open-issues query (checked on what the fake gh recorded)
        sent = [a for c in self.env.gh_calls() if c[:2] == ["api", "graphql"] for a in c if a.startswith("query=")]
        self.assertFalse([q for q in sent if "issues(first: 100" in q], sent)

    def test_a_body_only_blocker_refuses_too(self):
        head = self.push({"b.txt": "x\n"})
        p = self.merge(head, closing=[9], issues=issues_reply(9, body="Blocked by: #5", open_others=[5]))
        self.refused(p, "ticket #9 has 1 open blocker(s): #5 (body)")
        self.assertEqual(self.called(MERGE), [])

    def test_without_the_flag_ci_is_not_checked(self):
        head = self.push({"b.txt": "x\n"})
        self.assertEqual(self.merge(head, checks=[], flag=False).returncode, 0)

    def test_unresolved_thread_refuses_with_count_author_and_url(self):
        p = self.merge(self.approved, threads=threads_reply((False, False, "rev", "https://x/1"), (True, False, "z", "https://x/2")))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines(), ["PR #7 is not ready: 1 unresolved review thread(s)", "  rev https://x/1",
                                                 "next: send-back 7"])
        self.assertEqual(self.called(MERGE), [])

    def test_an_untrusted_thread_still_blocks_and_is_named_for_the_developer(self):
        # ADR-0015: only an outsider's thread left: a worker will not address it, so ask the user, not send-back
        p = self.merge(self.approved, threads=threads_reply((False, False, "eve", "https://x/5", "NONE")))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[:2], ["PR #7 is not ready: 1 unresolved review thread(s)",
                         "  eve https://x/5 (untrusted author: the developer resolves or dismisses it)"])
        self.assertTrue(p.stdout.splitlines()[-1].startswith("next: ask-user"), p.stdout)
        self.assertEqual(self.called(MERGE), [])

    def test_a_trusted_thread_beside_an_untrusted_one_is_still_a_send_back(self):
        p = self.merge(self.approved, threads=threads_reply((False, False, "eve", "https://x/5", "NONE"),
                                                            (False, False, "rev", "https://x/8")))
        self.assertEqual(p.stdout.splitlines()[-1], "next: send-back 7")

    def test_outdated_unresolved_thread_refuses(self):
        p = self.merge(self.approved, threads=threads_reply((False, True, "rev", "https://x/1")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("1 unresolved review thread(s)", p.stdout)

    def test_a_humans_thread_refuses_like_a_reviewers(self):
        p = self.merge(self.approved, threads=threads_reply((False, False, "shrujan", "https://x/9"), (False, False, "rev", "https://x/8")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("2 unresolved", p.stdout)
        self.assertEqual(p.stdout.splitlines()[1:], ["  shrujan https://x/9", "  rev https://x/8", "next: send-back 7"])

    def test_all_resolved_passes(self):
        p = self.merge(self.approved, threads=threads_reply((True, False, "rev", "u"), (True, True, "rev", "u")))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.called(MERGE)), 1)

    def test_no_threads_passes(self):
        self.assertEqual(self.merge(self.approved).returncode, 0)

    def test_failed_threads_read_exits_2_and_merges_nothing(self):
        p = self.merge(self.approved, threads={"exit": 1, "stderr": "boom"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("could not read the review threads of PR #7", p.stdout + p.stderr)
        self.assertEqual(self.called(MERGE), [])

    def test_without_the_flag_threads_are_not_read(self):
        p = self.merge(self.approved, flag=False, threads=threads_reply((False, False, "rev", "u")))
        self.assertEqual(p.returncode, 0)
        self.assertEqual([c for c in self.called(["api", "graphql"]) if "reviewThreads" in c[3]], [])

    # Merge gates (#656): a PR touching one is never merged by a script.
    def gate_events(self):
        return [e for e in self.env.events() if e["kind"] == "merge" and e["outcome"] == "gate-hold"]

    def test_a_gated_pr_is_refused_logged_and_refused_again(self):
        for _ in range(2):  # a rerun gives the same answer and logs again
            p = self.merge(self.approved, files=("hooks/guard.py", "a.txt"))
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertEqual(p.stdout.splitlines()[-1], "next: ask-user gate hooks/**")
            self.assertEqual(self.called(MERGE), [])
        self.assertEqual([(e["ref"], e["gate"]) for e in self.gate_events()], [("7", "hooks/**")] * 2)

    def test_a_gated_pr_is_refused_without_the_approved_head_flag_too(self):
        p = self.merge(self.approved, flag=False, files=("gated.txt",))
        self.assertEqual((p.returncode, p.stdout.splitlines()[-1]), (1, "next: ask-user gate gated.txt"), p.stdout + p.stderr)
        self.assertEqual(self.called(MERGE), [])

    def test_a_rename_out_of_a_gated_path_is_gated(self):
        rows = [{"filename": "elsewhere/guard.py", "previous_filename": "hooks/guard.py", "status": "renamed"}]
        p = self.merge(self.approved, files_reply={"stdout": json.dumps(rows)})
        self.assertEqual((p.returncode, p.stdout.splitlines()[-1]), (1, "next: ask-user gate hooks/**"), p.stdout + p.stderr)
        self.assertEqual(self.called(MERGE), [])

    def test_a_release_pr_says_so(self):
        p = self.merge(self.approved, files=(".release-please-manifest.json", "hooks/x"), title="chore(main): release 1.2.3")
        self.assertIn("a release PR", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: ask-user gate hooks/**")

    def test_a_gated_pr_the_developer_merged_by_hand_is_only_cleaned_up(self):
        p = self.merge(self.approved, files=("hooks/guard.py",), state="MERGED")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")
        self.assertEqual((self.called(MERGE), self.gate_events()), ([], []))

    # A merged change to the guard does nothing for a run that loaded its plugin before it (#727).
    def snapshot_events(self):
        return [(e["outcome"], e["ref"], bool(e.get("sha"))) for e in self.env.events() if e["kind"] == "snapshot" and e["outcome"] == "stale"]

    def launched(self, source=None, sha="f" * 40):
        """A fresh launch's event: the snapshot `sha` it loaded, exported from the checkout `source` (default: this repo)."""
        import repo as repo_mod
        import runlog
        runlog.append_event(self.env.repo, runlog.SNAPSHOT, "launched", session="launch", sha=sha,
                            source=source or str(repo_mod.main_checkout(self.env.repo)))

    # A run that loaded its plugin through the stable link gets a merged guard fix at once (#728).
    def linked_launch(self):
        """A fresh launch as scripts/launch makes it: a snapshot of origin/main at <old>, the stable link pointing to it, the event.

        Returns (link, old sha). XDG_CACHE_HOME is a temp directory for the test and the merge subprocess alike."""
        import plugin_dir
        import repo as repo_mod
        import runlog
        cache = Path(self.env.repo).parent / "cache"
        patch = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)})
        patch.start()
        self.addCleanup(patch.stop)
        for d in plugin_dir.SNAPSHOT_DIRS:
            self.env.commit(f"{d}/f.txt", "v1\n", f"chore: {d}")
        git(self.repo, "push", "-q", "origin", "main")
        old = git(self.repo, "rev-parse", "origin/main")
        source = str(repo_mod.main_checkout(self.repo))
        link = plugin_dir.link_path(source)
        plugin_dir.point(link, plugin_dir.export(["git", "-C", str(self.repo)], source, old))
        runlog.append_event(self.repo, runlog.SNAPSHOT, "launched", session="launch", sha=old, source=source, link=str(link))
        return link, old

    def merged_main(self, *files):
        """What the merged PR left on origin/main: one commit touching `files`. Returns its sha."""
        for f in files:
            sha = self.env.commit(f, "v2\n", f"fix: {f}")
        git(self.repo, "push", "-q", "origin", "main")
        return sha

    def test_a_hooks_pr_flips_the_link_to_the_merged_main_and_a_hook_script_alone_stales_nothing(self):
        link, old = self.linked_launch()
        new = self.merged_main("hooks/guard.py")
        p = self.merge(self.approved, files=("hooks/guard.py",), state="MERGED")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(link.resolve().name, new)  # the default branch's commit, never the PR's head
        self.assertNotEqual(new, self.approved)
        self.assertEqual((link / "hooks" / "guard.py").read_text(), "v2\n")
        self.assertIn(f"snapshot: {old[:12]}..{new[:12]}", p.stdout)
        self.assertEqual(self.snapshot_events(), [])
        self.assertNotIn("snapshot-stale", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_a_flip_whose_diff_touches_skills_agents_or_the_hook_table_also_stales_the_run(self):
        for touched in ("skills/run/SKILL.md", "agents/worker.md", "hooks/hooks.json"):
            with self.subTest(touched):
                self.setUp()
                link, old = self.linked_launch()
                new = self.merged_main("hooks/guard.py", touched)
                p = self.merge(self.approved, files=("hooks/guard.py", touched), state="MERGED")
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(link.resolve().name, new)  # the guard fix applies now all the same
                self.assertIn(f"snapshot: {old[:12]}..{new[:12]}", p.stdout)
                self.assertEqual(self.snapshot_events(), [("stale", "7", True)])
                self.assertIn("snapshot-stale", p.stdout)

    def test_an_ungated_pr_leaves_the_link_alone(self):
        link, old = self.linked_launch()
        self.merged_main("docs/x.md")
        p = self.merge(self.approved, files=("docs/x.md",), state="MERGED")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((link.resolve().name, self.snapshot_events()), (old, []))
        self.assertNotIn("snapshot:", p.stdout)

    def test_a_link_that_cannot_move_falls_back_to_stale(self):
        link, old = self.linked_launch()
        self.merged_main("hooks/guard.py")
        link.parent.chmod(0o500)  # the rename cannot happen
        self.addCleanup(link.parent.chmod, 0o700)
        p = self.merge(self.approved, files=("hooks/guard.py",), state="MERGED")
        self.assertEqual(link.resolve().name, old)
        self.assertEqual(self.snapshot_events(), [("stale", "7", True)])
        self.assertIn("could not repoint", p.stdout)

    def test_no_snapshot_launch_means_nothing_to_be_behind(self):
        # a marketplace install, or a repo with its own hooks/ directory: no `launched` event, no stale record
        p = self.merge(self.approved, files=("hooks/pre-commit",), state="MERGED")
        self.assertEqual((p.returncode, self.snapshot_events()), (0, []), p.stdout + p.stderr)

    def test_a_repo_that_is_not_the_snapshots_source_is_left_alone(self):
        self.launched(source="/somewhere/else/claude-orchestrate")
        p = self.merge(self.approved, files=("hooks/pre-commit",), state="MERGED")
        self.assertEqual((p.returncode, self.snapshot_events()), (0, []), p.stdout + p.stderr)

    def test_a_pr_merged_before_the_relaunch_does_not_stale_the_new_run(self):
        tip = git(self.repo, "rev-parse", "origin/main")
        self.launched(sha=tip)  # the run loaded a snapshot that already holds origin/main's plugin tree
        p = self.merge(self.approved, files=("hooks/guard.py",), state="MERGED")
        self.assertEqual((p.returncode, self.snapshot_events()), (0, []), p.stdout + p.stderr)

    def test_a_hooks_change_merged_by_hand_logs_snapshot_stale(self):
        self.launched()
        p = self.merge(self.approved, files=("hooks/guard.py",), state="MERGED")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.snapshot_events(), [("stale", "7", True)])
        self.assertIn("snapshot-stale", p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "next: frontier")

    def test_a_plugin_manifest_change_logs_snapshot_stale(self):
        self.launched()
        self.merge(self.approved, files=(".claude-plugin/plugin.json",), state="MERGED")
        self.assertEqual(len(self.snapshot_events()), 1)

    def test_a_merge_that_leaves_hooks_and_the_manifest_alone_logs_nothing(self):
        self.launched()
        self.merge(self.approved, files=("a.txt", "scripts/merge", "skills/run/SKILL.md"))
        self.assertEqual(self.snapshot_events(), [])

    def test_unreadable_files_after_the_merge_log_nothing_and_say_so(self):
        self.launched()
        p = self.merge(self.approved, files_reply={"exit": 1, "stderr": "gh: boom"}, state="MERGED")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.snapshot_events(), [])
        self.assertIn("could not read the changed files", p.stdout)

    def test_an_ungated_pr_reads_the_files_and_merges(self):
        self.assertEqual(self.merge(self.approved, files=("a.txt", "docs/x.md")).returncode, 0)
        self.assertEqual(len(self.called(MERGE)), 1)

    def test_unreadable_files_do_not_merge(self):
        p = self.merge(self.approved, files_reply={"exit": 1, "stderr": "gh: boom"})
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("merge gate", p.stdout)
        self.assertEqual(self.called(MERGE), [])

    def test_the_gate_is_the_main_checkouts_not_the_prs(self):
        other = self.repo / "wt"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "orchestration.md").write_text("## Gates\n\n- **Merge gates:** none\n")
        p = self.merge(self.approved, files=("hooks/guard.py",), more=("--cwd", str(other)))
        self.assertEqual((p.returncode, p.stdout.splitlines()[-1]), (1, "next: ask-user gate hooks/**"), p.stdout + p.stderr)

    def test_no_configured_gate_reads_no_file_list(self):
        (self.repo / ".claude" / "orchestration.md").write_text(READY_CONFIG.split("## Gates")[0])
        self.assertEqual(self.merge(self.approved).returncode, 0)
        self.assertEqual([c for c in self.env.gh_calls() if any("/pulls/7/files" in a for a in c)], [])


if __name__ == "__main__":
    unittest.main()
