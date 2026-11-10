"""scripts/holds: held paths and overlap, through the CLI with a fake `gh`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import timedelta, timezone
from pathlib import Path

from harness import ScriptEnv, gh_pr_list, gh_repo_view, git, rest_pull

import common

CONFIG = """## Slots

- **Slots:** 2.
- **Exclusive holds:** {holds}

## Shared files

- **Shared files:**
  - `CHANGELOG.md`: one line per PR
"""


def live_reason(pid=None, shift=0):
    """A harness-format lock reason for a running process: its pid and its start time in UTC, shifted by `shift` seconds."""
    pid = pid or os.getpid()
    start = common.process_start(pid)
    assert start is not None
    shown = (start + timedelta(seconds=shift)).astimezone(timezone.utc).strftime("%a %b %e %H:%M:%S %Y")
    return f"claude agent agent-abc (pid {pid} start {shown})"


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class Holds(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.wt_dir.cleanup)

    def gh(self, prs=None, branches=None, issues=None, merged=(), unclaimed=(), open_tickets=(), lead=(), drafts=(), shas=None, forks=()):
        """prs: {number: [paths]}; branches: {number: head branch}; issues: {ticket: state}, where CLOSED leaves the
        ticket out of the open issues and OPEN lists it; merged: head branches of merged PRs; unclaimed: open tickets with
        no assignee; open_tickets: open tickets assigned to the user (as is every other open ticket listed here);
        lead: open tickets labelled `lead`."""
        prs, branches = prs or {}, branches or {}
        listing = [{"number": n, "headRefName": branches.get(n, f"b{n}"), "isCrossRepository": n in forks,
                    "isDraft": n in drafts, "headRefOid": (shas or {}).get(n)} for n in prs]
        open_nums = set(open_tickets) | set(unclaimed) | set(lead) | {n for n, st in (issues or {}).items() if st == "OPEN"}
        nodes = [{"number": n, "labels": {"nodes": [{"name": "lead"}] if n in lead else []},
                  "assignees": {"totalCount": 0 if n in unclaimed else 1}}
                 for n in sorted(open_nums)]
        responses = [
            gh_repo_view(),
            gh_pr_list(listing, "--state", "open"),
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": nodes}}}})},
        ]
        for n, files in prs.items():
            responses.append({"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls/{n}/files"],
                              "stdout": json.dumps([{"filename": p} for p in files])})
            responses.append({"args": ["api", f"repos/{{owner}}/{{repo}}/pulls/{n}"],
                              "stdout": json.dumps(rest_pull(headRefName=branches.get(n, f"b{n}")))})
        responses.append(gh_pr_list([{"number": 1, "headRefName": b} for b in merged], "--state", "merged"))
        self.env.set_gh(responses)

    def state(self, tickets):
        d = self.env.repo / ".claude"
        d.mkdir(exist_ok=True)
        (d / "orchestrate-state.json").write_text(json.dumps(
            {"tickets": {str(n): {"touch_map": tm} for n, tm in tickets.items()}}))

    def config(self, holds="none"):
        d = self.env.repo / ".claude"
        d.mkdir(exist_ok=True)
        (d / "orchestration.md").write_text(CONFIG.format(holds=holds))

    def holds(self, *args):
        return self.env.run("holds", *args, check=False)

    def test_nothing_held(self):
        self.gh()
        r = self.holds("--touch-map", "a.py:3", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_line_and_range_stripped_and_other_section_overlaps(self):
        self.gh({9: ["config.txt"]})
        for entry in ("config.txt:40", "config.txt:10-20", "./config.txt"):
            with self.subTest(entry):
                r = self.holds("--touch-map", entry, "--ticket", "7")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(r.stdout.splitlines(), [
                    "config.txt (held by open PR #9)", "overlap: config.txt (held by open PR #9)"])

    def test_building_ticket_touch_map_is_stripped(self):
        self.gh(open_tickets=[12])
        self.state({12: ["src/x.py:5", "y.py:1-9"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by building #12)", "y.py (held by building #12)",
            "overlap: src/x.py (held by building #12)"])

    def test_building_ticket_note_is_stripped_and_matches(self):
        self.gh(open_tickets=[12])
        self.state({12: ["scripts/gate:74 (main, new checks)"]})
        r = self.holds("--touch-map", "scripts/gate", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "scripts/gate (held by building #12)", "overlap: scripts/gate (held by building #12)"])

    def test_candidate_note_is_stripped_and_matches(self):
        self.gh(open_tickets=[12])
        self.state({12: ["scripts/gate"]})
        r = self.holds("--touch-map", "scripts/gate:3 (x)", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("overlap: scripts/gate (held by building #12)", r.stdout)

    def test_shared_file_overlap_is_reported_not_counted(self):
        self.gh({9: ["CHANGELOG.md"]})
        self.config()
        r = self.holds("--touch-map", "CHANGELOG.md:3", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("shared (not counted): CHANGELOG.md (held by open PR #9)", r.stdout)
        self.assertIn("overlap: none", r.stdout)

    def test_a_directory_candidate_counts_a_held_file_and_not_a_shared_one(self):
        self.gh({9: ["db/schema.sql"]})
        self.config()
        r = self.holds("--touch-map", "db/", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("overlap: db/ (held by open PR #9)", r.stdout)
        (self.env.repo / ".claude" / "orchestration.md").write_text(
            CONFIG.format(holds="none").replace("`CHANGELOG.md`", "`db/shared.sql`"))
        self.gh({9: ["db/shared.sql"]})
        r = self.holds("--touch-map", "db/", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("shared (not counted): db/ (held by open PR #9)", r.stdout)
        self.assertIn("overlap: none", r.stdout)

    def test_exclusive_hold(self):
        self.gh()
        self.config("while #42 is open, nothing else touches `db/`.")
        r = self.holds("--touch-map", "db/schema.sql:1", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[0], "db/ (held by an exclusive hold)")
        self.assertIn("overlap: db/schema.sql (held by an exclusive hold)", r.stdout)

    def test_own_pr_and_touch_map_left_out(self):
        self.gh({9: ["a.py"], 10: ["b.py"]}, branches={9: "7-my-ticket", 10: "8-other"}, open_tickets=[7, 11])
        self.state({7: ["a.py:1"], 11: ["c.py:1"]})
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.splitlines(), [
            "b.py (held by open PR #10)", "c.py (held by building #11)", "overlap: none"])

    def test_pre_merge_check_uses_the_prs_diff(self):
        # PR 9 is mine; building #11 holds b.py too, so the overlap is real whatever the PR order.
        self.gh({9: ["a.py", "b.py"], 10: ["b.py"]}, branches={9: "7-mine", 10: "8-other"}, open_tickets=[7, 11])
        self.state({7: ["a.py"], 11: ["b.py"]})
        r = self.holds("--pr", "9")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "b.py (held by open PR #10, building #11)", "overlap: b.py (held by open PR #10, building #11)"])

    def test_the_younger_of_two_open_prs_waits_for_the_older_and_exits_3(self):
        self.gh({9: ["a.py", "b.py"], 5: ["b.py", "c.py"]}, branches={9: "7-mine", 5: "4-older"})
        r = self.holds("--pr", "9")
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1]), (3, "waits for #4 (PR #5) to merge: b.py"), r.stdout + r.stderr)
        self.assertNotIn("overlap:", r.stdout)

    def test_the_older_of_two_open_prs_passes_and_names_the_younger(self):
        self.gh({9: ["b.py"], 10: ["b.py"]}, branches={9: "7-mine", 10: "8-other"})
        r = self.holds("--pr", "9")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.splitlines()[-2:], ["younger PR (not counted): b.py (held by open PR #10)", "overlap: none"])

    def test_waiting_on_a_pr_while_a_building_ticket_holds_the_path_is_an_overlap(self):
        self.gh({9: ["b.py"], 5: ["b.py"]}, branches={9: "7-mine", 5: "4-older"}, open_tickets=[11])
        self.state({11: ["b.py"]})
        r = self.holds("--pr", "9")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("waits for", r.stdout)

    def test_dispatch_time_check_still_counts_an_open_pr_holder(self):
        self.gh({5: ["b.py"]}, branches={5: "4-older"})
        self.assertEqual(self.holds("--touch-map", "b.py", "--ticket", "7").returncode, 1)

    def test_a_release_please_pr_holds_nothing_and_a_human_pr_on_its_files_is_held(self):
        rp = "release-please--branches--main--components--orchestrate"
        self.gh({5: ["CHANGELOG.md", "version.txt", ".release-please-manifest.json", ".claude-plugin/plugin.json"],
                 6: ["scripts/x"], 8: ["CHANGELOG.md"]}, branches={5: rp, 6: "9-worker", 8: "12-human"})
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["scripts/x (held by open PR #6)", "CHANGELOG.md (held by open PR #8)", "overlap: none"])

    def test_a_fork_prs_files_are_not_held_and_its_diff_is_not_read(self):
        # ADR-0015: an outsider's PR touching scripts/x holds nothing
        self.gh({5: ["scripts/x"], 6: ["scripts/y"]}, branches={5: "9-fork", 6: "10-own"}, forks=(5,))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["scripts/y (held by open PR #6)", "overlap: none"])
        self.assertEqual(self.files_reads(), [c for c in self.files_reads() if "/pulls/6/" in c[1]])

    CACHE = ".claude/orchestrate-snapshot.json"

    def files_reads(self):
        return [c for c in self.env.gh_calls() if len(c) > 1 and "/files" in c[1]]

    def test_the_cache_serves_a_pr_whose_head_matches_without_snapshot_and_misses_are_written_back(self):
        self.gh({5: ["scripts/b"], 6: ["scripts/c"]}, branches={5: "3-a", 6: "4-b"}, shas={5: "aaa", 6: "bbb"})
        cache = self.env.repo / self.CACHE
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"pr_files": {"5": {"sha": "aaa", "files": ["scripts/a"]}}}))
        r = self.holds("--touch-map", "scripts/a", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("scripts/a (held by open PR #5)", r.stdout)  # the cached list, not the stub's
        self.assertEqual(len(self.files_reads()), 1, "only PR 6 (no cached entry) is fetched")
        self.assertEqual(json.loads(cache.read_text())["pr_files"]["6"], {"sha": "bbb", "files": ["scripts/c"]})
        self.gh({5: ["scripts/b"], 6: ["scripts/x"]}, branches={5: "3-a", 6: "4-b"}, shas={5: "aaa", 6: "bbb"})
        before = len(self.files_reads())
        self.holds("--touch-map", "scripts/a", "--ticket", "7")
        self.assertEqual(len(self.files_reads()) - before, 0, "the next call in the pass hits the written-back entry")

    def test_a_changed_head_refetches_and_a_corrupt_cache_fetches_everything(self):
        self.gh({5: ["scripts/b"]}, branches={5: "3-a"}, shas={5: "new"})
        cache = self.env.repo / self.CACHE
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"pr_files": {"5": {"sha": "old", "files": ["scripts/a"]}}}))
        r = self.holds("--touch-map", "scripts/b", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(len(self.files_reads()), 1)
        self.assertEqual(json.loads(cache.read_text())["pr_files"]["5"], {"sha": "new", "files": ["scripts/b"]})
        cache.write_text("{not json")
        self.gh({5: ["scripts/b"]}, branches={5: "3-a"}, shas={5: "new"})
        before = len(self.files_reads())
        r = self.holds("--touch-map", "scripts/b", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(len(self.files_reads()) - before, 1)

    def test_closed_ticket_holds_nothing(self):
        self.gh(issues={12: "CLOSED"})
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_ticket_with_merged_pr_holds_nothing(self):
        self.gh(merged=["12-the-fix"], open_tickets=[12])
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_open_ticket_with_open_pr_holds_its_touch_map_joined_with_the_prs_files(self):
        self.gh({9: ["src/x.py"]}, branches={9: "12-the-fix"}, open_tickets=[12])
        self.state({12: ["src/x.py", "src/y.py"]})
        r = self.holds("--touch-map", "src/x.py", "src/y.py", "--ticket", "7")
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by open PR #9)", "src/y.py (held by open PR #9)",
            "overlap: src/x.py (held by open PR #9)", "overlap: src/y.py (held by open PR #9)"])
        self.assertEqual(r.returncode, 1)

    def test_a_pr_file_outside_the_touch_map_is_named_as_a_pr_file(self):
        self.gh({9: ["tests/test_other.py"]}, branches={9: "12-the-fix"}, open_tickets=[12])
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "tests/test_other.py", "--ticket", "7")
        self.assertIn("overlap: tests/test_other.py (held by open PR #9 file)", r.stdout.splitlines())
        self.assertEqual(r.returncode, 1)

    def test_an_empty_start_pr_still_holds_its_declared_touch_map(self):
        self.gh({9: []}, branches={9: "12-the-fix"}, open_tickets=[12])
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by open PR #9)", "overlap: src/x.py (held by open PR #9)"])
        self.assertEqual(r.returncode, 1)

    def test_a_building_ticket_holds_an_unpushed_edit_outside_its_touch_map(self):
        self.gh(open_tickets=[12])
        self.state({12: ["src/x.py"]})
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("overlap: a.py (held by building #12)", r.stdout.splitlines())

    def test_a_building_ticket_with_a_draft_holds_its_touch_map_and_the_drafts_diff(self):
        self.gh({9: ["src/drafted.py"]}, branches={9: "12-the-fix"}, open_tickets=[12], drafts={9})
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "src/drafted.py", "--ticket", "7")
        self.assertEqual(r.stdout.splitlines(), [
            "src/drafted.py (held by open PR #9 file)", "src/x.py (held by open PR #9, building #12)",
            "overlap: src/x.py (held by open PR #9, building #12)",
            "overlap: src/drafted.py (held by open PR #9 file)"])
        self.assertEqual(r.returncode, 1)

    def test_a_draft_pr_joins_its_tickets_touch_map_like_any_open_pr(self):
        self.gh({9: []}, branches={9: "12-the-fix"}, open_tickets=[12], drafts={9})
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertIn("src/x.py (held by open PR #9, building #12)", r.stdout.splitlines())
        self.assertEqual(r.returncode, 1)

    def test_ticket_check_gh_failure_could_not_run(self):
        self.state({12: ["a.py"]})
        self.env.set_gh([
            gh_repo_view(),
            {"args": ["api", "~repos/{owner}/{repo}/pulls?state=open"], "stdout": "[]"},
            {"args": ["api", "graphql"], "stderr": "HTTP 502\n", "exit": 1}])
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 2)
        self.assertIn("holds: gh:", r.stderr)

    def test_rate_limit_over_the_wait_budget_exits_two_with_the_reset_time(self):
        # retry-after 300 is over the 75 s budget: the script must not sleep, and prints the time to retry.
        self.env.set_gh([{"args": ["api", "repos/{owner}/{repo}"], "exit": 1,
                          "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}])
        with tempfile.TemporaryDirectory() as cache, unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            r = self.holds("--touch-map", "a.py", "--ticket", "7")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertRegex(r.stderr, r"holds: gh: rate limit until \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
            self.assertRegex(r.stdout.strip(), r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertTrue(list(Path(cache).glob("orchestrate/github-ratelimit-*.json")))  # the others wait too
        self.assertEqual(len(self.env.gh_calls()), 1)

    def test_missing_state_file_means_no_building_tickets(self):
        self.gh({9: ["a.py"]})
        r = self.holds("--touch-map", "b.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (0, "a.py (held by open PR #9)"))

    def test_unreadable_state_file_could_not_run(self):
        self.gh()
        (self.env.repo / ".claude").mkdir(exist_ok=True)  # the harness stamps a state file here
        (self.env.repo / ".claude/orchestrate-state.json").write_text("{nope")
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_an_error_nothing_maps_is_exit_2_with_one_line_not_a_traceback(self):
        # An OSError from claims.claims is outside the mapped set: exit 2, never Python's exit 1 (#424).
        import io
        from contextlib import redirect_stderr
        from importlib.machinery import SourceFileLoader
        from unittest import mock
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        from importlib.util import module_from_spec, spec_from_loader
        loader = SourceFileLoader("holds_under_test", f"{scripts}/holds")
        holds = module_from_spec(spec_from_loader("holds_under_test", loader))
        loader.exec_module(holds)
        err = io.StringIO()
        with mock.patch.object(holds.claims, "claims", side_effect=PermissionError("denied\nreading it")), \
                mock.patch.object(holds.tracker.Snapshot, "open", return_value=mock.MagicMock(prs=[])), redirect_stderr(err):
            code = holds.main(["--touch-map", "a.py", "--ticket", "7", "--repo", str(self.env.repo)])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue().strip(), "holds: could not run: PermissionError: denied reading it")

    def test_gh_unavailable_could_not_run(self):
        r = self.env.run("holds", "--touch-map", "a.py", "--ticket", "7", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_gh_failure_could_not_run(self):
        self.env.set_gh([{"args": ["api", "repos/{owner}/{repo}"], "stderr": "HTTP 502\n", "exit": 1}])
        self.assertEqual(self.holds("--touch-map", "a.py", "--ticket", "7").returncode, 2)

    def test_comma_separated_touch_map_is_split_into_entries(self):
        for arg in ("scripts/session.py:252, scripts/rebuild-state:74", "scripts/session.py:252,scripts/rebuild-state:74"):
            with self.subTest(arg):
                self.gh(open_tickets=[341])
                self.state({341: ["scripts/rebuild-state:10"]})
                r = self.holds("--touch-map", arg, "--ticket", "338")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("overlap: scripts/rebuild-state (held by building #341)", r.stdout)

    def test_separate_touch_map_arguments_overlap(self):
        self.gh(open_tickets=[341])
        self.state({341: ["scripts/rebuild-state:10"]})
        r = self.holds("--touch-map", "scripts/session.py:252", "scripts/rebuild-state:74", "--ticket", "338")
        self.assertEqual(r.returncode, 1)

    def test_a_note_with_a_comma_stays_in_its_entry(self):
        self.gh(open_tickets=[12])
        self.state({12: "a, b"})
        r = self.holds("--touch-map", "a:1, b:2 (note, with comma)", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[-2:], [
            "overlap: a (held by building #12)", "overlap: b (held by building #12)"])

    def test_an_entry_off_the_grammar_is_exit_two(self):
        for arg in ("(just a note)", "a.py:10, 20", "a.py (deleted):12", "a.py:L10-L20"):
            with self.subTest(arg):
                r = self.holds("--touch-map", arg, "--ticket", "7")
                self.assertEqual((r.returncode, r.stdout), (2, ""))
                self.assertIn("bad touch-map entry", r.stderr)

    def test_an_empty_argument_is_ignored(self):
        self.gh()
        r = self.holds("--touch-map", "", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))
        self.assertEqual(r.stderr.splitlines(), ["ignored: "])

    def test_backticked_path_with_a_note_matches(self):
        self.gh({9: ["src/a.py"]})
        r = self.holds("--touch-map", "`src/a.py` (new)", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_an_entry_after_a_sentence_end_is_kept(self):
        self.gh({9: ["src/b.py"]})
        r = self.holds("--touch-map", "src/a.py (see note). src/b.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_none_is_an_empty_touch_map(self):
        for args in (("none",), ("None",), ("none", "none")):
            with self.subTest(args):
                self.gh()
                r = self.holds("--touch-map", *args, "--ticket", "7")
                self.assertEqual((r.returncode, r.stdout.splitlines(), r.stderr), (0, ["none", "overlap: none"], ""))

    def test_literal_arguments_are_not_split(self):
        self.gh({12: ["a,b.py"]})
        r = self.holds("--literal", "--touch-map", "a,b.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("overlap: a,b.py (held by open PR #12)", r.stdout)

    def test_usage(self):
        self.assertEqual(self.holds().returncode, 2)
        self.assertEqual(self.holds("--touch-map", "a.py").returncode, 2)

    def test_tracker_is_read_once_and_no_issue_view(self):
        self.gh({9: ["a.py"]}, open_tickets=[12])
        self.state({12: ["src/x.py"], 13: ["y.py"]})
        self.holds("--touch-map", "b.py", "--ticket", "7")
        calls = self.env.gh_calls()
        self.assertFalse([c for c in calls if c[:2] == ["issue", "view"]], calls)
        self.assertEqual(sum(1 for c in calls if c[:2] == ["api", "graphql"]), 1, calls)

    # Elsewhere: a live worker in another session holds the changes in its worktree (ADR 0009). Real worktrees
    # and real `git worktree lock`, with `gh` stubbed.

    def worktree(self, branch, reason=None):
        """A worktree on a new branch off main, locked with `reason` when given. Returns its path."""
        path = Path(self.wt_dir.name) / branch
        git(self.env.repo, "worktree", "add", "-b", branch, str(path))
        if reason is not None:
            git(self.env.repo, "worktree", "lock", "--reason", reason, str(path))
        return path

    def dirty(self, path):
        """A committed change never pushed, an unstaged edit to a tracked file, and an untracked file."""
        (path / "a.py").write_text("x\n")
        git(path, "add", "a.py")
        git(path, "commit", "-m", "feat: a")
        (path / "README.md").write_text("changed\n")
        (path / "c.txt").write_text("new\n")

    def foreign_reason(self):
        """A lock reason naming a live process that is not an ancestor of the test: another session's worker."""
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        return live_reason(other.pid)

    def test_own_unrecorded_worker_holds_its_changes_as_building(self):
        self.gh(open_tickets=[12])
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "README.md (held by building #12)", "a.py (held by building #12)", "c.txt (held by building #12)",
            "overlap: a.py (held by building #12)"])

    def test_lead_ticket_with_own_live_worktree_holds_its_changes(self):
        self.gh(open_tickets=[12], lead=[12])
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a.py (held by elsewhere #12)", r.stdout.splitlines())

    def test_live_elsewhere_holds_committed_unstaged_and_untracked(self):
        self.gh(open_tickets=[12])
        self.dirty(self.worktree("12-widget", reason=self.foreign_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "README.md (held by elsewhere #12)", "a.py (held by elsewhere #12)", "c.txt (held by elsewhere #12)",
            "overlap: a.py (held by elsewhere #12)"])

    def test_dead_lock_holds_nothing(self):
        self.gh(open_tickets=[12])
        reason = live_reason().replace(f"pid {os.getpid()}", f"pid {dead_pid()}")
        self.dirty(self.worktree("12-widget", reason=reason))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_reused_pid_lock_holds_nothing(self):
        self.gh(open_tickets=[12])
        self.dirty(self.worktree("12-widget", reason=live_reason(shift=-3600)))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_no_lock_holds_nothing(self):
        self.gh(open_tickets=[12])
        self.dirty(self.worktree("12-widget"))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_unparseable_lock_holds_the_changed_paths(self):
        self.gh(open_tickets=[12])
        self.dirty(self.worktree("12-widget", reason="held by hand"))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a.py (held by elsewhere #12)", r.stdout.splitlines())

    def test_live_worktree_with_no_changes_holds_nothing(self):
        self.gh(open_tickets=[12])
        self.worktree("12-widget", reason=live_reason())
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_open_pr_ticket_is_held_through_its_pr(self):
        self.gh({9: ["src/x.py"]}, branches={9: "12-the-fix"}, open_tickets=[12])
        self.dirty(self.worktree("12-the-fix", reason=live_reason()))
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by open PR #9)", "overlap: src/x.py (held by open PR #9)"])

    def test_building_ticket_holds_its_map_and_its_worktree_changes_once_each(self):
        self.gh(open_tickets=[12])
        self.state({12: ["src/x.py"]})
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual(r.stdout.splitlines(), [
            "src/x.py (held by building #12)", "README.md (held by building #12)",
            "a.py (held by building #12)", "c.txt (held by building #12)",
            "overlap: src/x.py (held by building #12)"])

    def test_own_ticket_worktree_is_left_out(self):
        self.gh(open_tickets=[7])
        self.dirty(self.worktree("7-mine", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_unclaimed_ticket_holds_nothing(self):
        self.gh(unclaimed=[12])
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    # Behaviour decided by the claims rules (#262), pinned so a change to either is a visible test edit: an unclaimed
    # state-file ticket is not Building.

    def test_unclaimed_building_record_holds_nothing(self):
        self.gh(unclaimed=[12])
        self.state({12: ["src/x.py"]})
        r = self.holds("--touch-map", "src/x.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_lead_ticket_with_live_worktree_holds_its_changes(self):
        # Kept from before the claims module: a `lead` ticket is with-lead in claims, but its live worktree still holds.
        self.gh(open_tickets=[12], lead=[12])
        self.dirty(self.worktree("12-widget", reason=self.foreign_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "README.md (held by elsewhere #12)", "a.py (held by elsewhere #12)", "c.txt (held by elsewhere #12)",
            "overlap: a.py (held by elsewhere #12)"])

    def test_closed_ticket_holds_nothing_elsewhere(self):
        self.gh(issues={12: "CLOSED"})
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["none", "overlap: none"]))

    def test_merged_pr_read_failure_could_not_run(self):
        self.env.set_gh([
            gh_repo_view(),
            {"args": ["api", "~repos/{owner}/{repo}/pulls?state=open"], "stdout": "[]"},
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": [
                {"number": 12, "labels": {"nodes": []}, "assignees": {"totalCount": 1}}]}}}})},
            {"args": ["api", "~repos/{owner}/{repo}/pulls?state=closed"], "stderr": "HTTP 502\n", "exit": 1}])
        self.dirty(self.worktree("12-widget", reason=live_reason()))
        r = self.holds("--touch-map", "a.py", "--ticket", "7")
        self.assertEqual(r.returncode, 2)
        self.assertIn("holds: gh:", r.stderr)


    def test_a_map_the_grammar_rejects_at_dispatch_is_exit_2_naming_the_entry(self):
        self.gh()
        for bad in ("scripts/a.py scripts/b.py", "see scripts/x.py", "a.py\nb.py"):
            with self.subTest(bad):
                r = self.holds("--touch-map", bad, "--ticket", "7")
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("bad touch-map entry", r.stderr)

    def test_an_unparsed_stored_map_holds_every_path_and_names_the_ticket(self):
        self.gh(open_tickets=[12])
        for stored in ("unparsed:see scripts/x.py", "a.py:10, 20"):  # rebuild-state's marker, and a hand edit
            self.state({12: stored})
            with self.subTest(stored):
                r = self.holds("--touch-map", "anything/else.py", "--ticket", "7")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("overlap: anything/else.py (held by building #12)", r.stdout)
                self.assertIn("building #12 has an unparsed touch map", r.stderr)


class HoldsLibrary(unittest.TestCase):
    """scripts/holds.py on plain data: no subprocess, no fake gh."""

    class Cfg:
        def __init__(self, shared=(), exclusive=()):
            self.shared, self.exclusive_holds = shared, list(exclusive)

        def is_shared(self, path):
            return path in self.shared

    def setUp(self):
        import holds
        self.holds = holds

    def held(self, cfg=None, **kw):
        return self.holds.held_paths(cfg or self.Cfg(), kw.get("prs", {}), kw.get("tickets", {}), kw.get("others", {}), kw.get("off_map"))

    def test_held_rows_name_every_holder_in_a_stable_order(self):
        rows = self.held(self.Cfg(exclusive=["x.py"]), prs={9: ["a.py"], 3: ["a.py"]}, tickets={12: ["a.py", "x.py"]},
                         others={13: ["a.py"]}, off_map={9: {"a.py"}})
        self.assertEqual(rows[0], self.holds.Held("a.py", ("open PR #3", "open PR #9 file", "building #12", "elsewhere #13")))
        self.assertEqual(rows[1].holders, ("building #12", "an exclusive hold"))
        self.assertEqual(rows[1].render(), "x.py (held by building #12, an exclusive hold)")

    def test_a_candidate_on_a_building_holder_is_counted(self):
        held = self.held(tickets={12: ["a.py"]})
        got = self.holds.overlaps(held, ["a.py", "b.py"], self.Cfg())
        self.assertEqual(got, [self.holds.Overlap("counted", "a.py", ("building #12",))])
        self.assertEqual(got[0].render(), "overlap: a.py (held by building #12)")

    def test_a_shared_file_is_shared_not_counted(self):
        held = self.held(prs={9: ["README.md"]})
        got = self.holds.overlaps(held, ["README.md"], self.Cfg(shared=("README.md",)))
        self.assertEqual([o.kind for o in got], ["shared"])
        self.assertEqual(self.holds.Result(held, got).code, 0)

    def test_the_younger_pr_waits_for_the_oldest_older_one_and_the_older_one_passes(self):
        held = self.held(prs={5: ["a.py"], 7: ["a.py"], 12: ["a.py"]})
        got = self.holds.overlaps(held, ["a.py"], self.Cfg(), own_pr=9, pr_tickets={5: 50, 7: 70})
        self.assertEqual(got, [self.holds.Overlap("waits-for", "a.py", ("open PR #5", "open PR #7", "open PR #12"), pr=5, ticket=50)])
        result = self.holds.Result(held, got)
        self.assertEqual((result.code, result.wait_lines()), (3, ["waits for #50 (PR #5) to merge: a.py"]))
        older = self.holds.overlaps(self.held(prs={12: ["a.py"]}), ["a.py"], self.Cfg(), own_pr=9)
        self.assertEqual([o.kind for o in older], ["younger"])
        self.assertEqual(self.holds.Result([], older).code, 0)

    def test_a_non_pr_holder_makes_it_a_real_overlap_whatever_the_prs(self):
        held = self.held(prs={5: ["a.py"]}, tickets={12: ["a.py"]})
        got = self.holds.overlaps(held, ["a.py"], self.Cfg(), own_pr=9)
        self.assertEqual([o.kind for o in got], ["counted"])
        self.assertEqual(self.holds.Result(held, got).code, 1)

    def test_a_directory_candidate_covers_the_held_file_under_it(self):
        got = self.holds.overlaps(self.held(prs={9: ["db/a.sql"]}), ["db/"], self.Cfg())
        self.assertEqual([(o.kind, o.path) for o in got], [("counted", "db/")])

    def test_lines_render_the_held_paths_then_the_verdict(self):
        r = self.holds.Result()
        self.assertEqual(r.lines(), ["none", "overlap: none"])
        held = self.held(tickets={12: ["a.py"]})
        r = self.holds.Result(held, self.holds.overlaps(held, ["a.py"], self.Cfg()))
        self.assertEqual(r.lines(), ["a.py (held by building #12)", "overlap: a.py (held by building #12)"])

    def test_candidate_paths_report_empty_arguments_instead_of_printing(self):
        self.assertEqual(self.holds.candidate_paths(["a.py, b.py", "none", " "]), (["a.py", "b.py"], [" "]))
        self.assertEqual(self.holds.candidate_paths(["a,b.py"], literal=True), (["a,b.py"], []))

    def test_an_unparsed_stored_map_holds_every_path(self):
        from touchmap import HOLDS_ALL
        self.assertEqual(self.holds.state_paths("unparsed:see x"), [HOLDS_ALL])
        self.assertEqual(self.holds.state_paths(["a.py:10", "b.py"]), ["a.py", "b.py"])
        self.assertEqual(self.holds.state_paths(None), [])

    def test_pr_paths_join_the_declared_map_and_name_files_outside_it(self):
        paths, off = self.holds.pr_paths(["a.py", "z.py"], {"touch_map": "a.py, b.py"})
        self.assertEqual((paths, off), (["a.py", "z.py", "b.py"], {"z.py"}))


if __name__ == "__main__":
    unittest.main()
