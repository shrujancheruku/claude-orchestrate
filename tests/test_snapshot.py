"""scripts/snapshot and the `--snapshot` flag of claims, frontier, holds and lead-route: one tracker read per reconcile,
one blocker rule, and `frontier --fix-drift`."""

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_pr_list, gh_repo_rest  # noqa: E402
import github  # noqa: E402
import tracker  # noqa: E402

FILE = ".claude/orchestrate-snapshot.json"
REPO = {"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "main"}}


def node(n, labels=("ready-for-agent",), claimed=False, blockers=(), body=""):
    return {"number": n, "title": f"title {n}", "url": f"https://x/{n}", "body": body, "parent": None,
            "subIssuesSummary": {"total": 0, "completed": 0},
            "labels": {"nodes": [{"name": x} for x in labels]},
            "assignees": {"totalCount": 1 if claimed else 0},
            "blockedBy": {"nodes": [{"number": b, "state": s} for b, s in blockers]},
            "userContentEdits": {"nodes": []}, "comments": {"nodes": []}}


def issues_reply(nodes):
    return {"args": ["api", "graphql", "-f", "query=" + github.ISSUES_QUERY], "stdout": json.dumps(
        {"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})}


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def serve(self, nodes, prs=()):
        self.env.set_gh([
            gh_repo_rest(REPO),
            issues_reply(nodes),
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})},
            gh_pr_list(list(prs), "--state", "open"),
            gh_pr_list([], "--state", "merged"),
        ])

    def issue_reads(self):
        return [c for c in self.env.gh_calls() if c[:4] == ["api", "graphql", "-f", "query=" + github.ISSUES_QUERY]]

    def test_one_tracker_read_serves_every_reader(self):
        self.serve([node(1), node(2, claimed=True), node(3, body="Blocked by: #1")])
        self.env.run("snapshot", "--repo", ".")
        after_snapshot = len(self.env.gh_calls())
        self.assertEqual(len(self.issue_reads()), 1)
        for script, args in (("frontier", ["--all"]), ("claims", []), ("lead-route", ["--lead", "none", "--cap", "2"]),
                             ("holds", ["--touch-map", "a.py", "--ticket", "9"])):
            r = self.env.run(script, *args, "--snapshot", FILE, check=False)
            self.assertIn(r.returncode, (0, 1), (script, r.stdout, r.stderr))
        self.assertEqual(len(self.env.gh_calls()), after_snapshot, "a reader fetched instead of reading the file")

    def test_snapshot_file_round_trips_the_issues(self):
        self.serve([node(1, labels=("a", "b"), blockers=[(2, "OPEN")]), node(2)])
        self.env.run("snapshot", "--repo", ".")
        snap = tracker.Snapshot.read(self.env.repo / FILE)
        self.assertEqual(snap.issues[0]["labels"], {"a", "b"})
        self.assertEqual(snap.blockers(1), [(2, False)])
        self.assertEqual(snap.default_branch, "main")

    def test_a_truncated_snapshot_file_gives_a_one_line_error_naming_it(self):
        (self.env.repo / FILE).write_text('{"issues": [', encoding="utf-8")
        for script in ("claims", "frontier"):
            r = self.env.run(script, "--snapshot", FILE, check=False)
            self.assertNotEqual(r.returncode, 0, script)
            self.assertIn(f"cannot read snapshot {FILE}", r.stderr, script)
            self.assertNotIn("Traceback", r.stderr, script)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self):
        target = self.env.repo / FILE
        target.write_text("old", encoding="utf-8")
        # Every part given, so `write` reads nothing from gh (the real gh has no token in CI).
        snap = tracker.Snapshot(None, repo={}, issues=[], prs=[], merged=[], abandoned=[], pr_files={})
        with unittest.mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                snap.write(target)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def files_reply(self, n, paths):
        return {"args": ["api", f"~repos/{{owner}}/{{repo}}/pulls/{n}/files"], "stdout": json.dumps([{"filename": p} for p in paths])}

    def files_reads(self):
        return [c for c in self.env.gh_calls() if len(c) > 1 and "/files" in c[1]]

    def test_the_open_prs_file_lists_are_cached_once_and_holds_reads_them(self):
        rp = "release-please--branches--main--components--orchestrate"
        prs = [{"number": 5, "headRefName": "3-a", "headRefOid": "aaa"}, {"number": 6, "headRefName": "4-b", "headRefOid": "bbb"},
               {"number": 7, "headRefName": rp, "headRefOid": "ccc"}]
        self.env.set_gh([gh_repo_rest(REPO), issues_reply([node(1)]),
                         {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})},
                         gh_pr_list(prs, "--state", "open"),
                         gh_pr_list([], "--state", "merged"),
                         self.files_reply(5, ["scripts/a"]), self.files_reply(6, ["scripts/b"])])
        r = self.env.run("snapshot", "--repo", ".", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.files_reads()), 2, "one read per PR, none for release-please's")
        self.assertEqual(json.loads((self.env.repo / FILE).read_text())["pr_files"],
                         {"5": {"sha": "aaa", "files": ["scripts/a"]}, "6": {"sha": "bbb", "files": ["scripts/b"]}})
        before = len(self.env.gh_calls())
        for ticket in (11, 12, 13, 14, 15):  # five candidates in one pass
            r = self.env.run("holds", "--touch-map", "scripts/a", "--ticket", str(ticket), "--snapshot", FILE, check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(len(self.env.gh_calls()), before, "holds fetched instead of reading the cache")

    def test_a_pushed_pr_refreshes_its_one_entry(self):
        snap = tracker.Snapshot(None, pr_files={"5": {"sha": "old", "files": ["old"]}, "6": {"sha": "x", "files": ["keep"]}})
        with unittest.mock.patch.object(github, "pr_files", return_value=["new"]) as fetch:
            self.assertEqual(snap.pr_files({"number": 6, "headRefOid": "x"}), ["keep"])
            self.assertEqual(snap.pr_files({"number": 5, "headRefOid": "new"}), ["new"])
            self.assertEqual(snap.pr_files({"number": 5, "headRefOid": "new"}), ["new"])  # now cached
        self.assertEqual(fetch.call_count, 1)

    def test_a_missing_snapshot_file_could_not_run(self):
        r = self.env.run("frontier", "--snapshot", "nope.json", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_a_body_only_blocker_is_held_by_frontier_and_handed_back_by_lead_route(self):
        self.serve([node(1), node(5, claimed=True, body="Blocked by: #1")])
        self.env.run("snapshot", "--repo", ".")
        self.assertEqual(self.env.run("frontier", "--snapshot", FILE).stdout, "#1 title 1 model sonnet\n")
        route = self.env.run("lead-route", "--lead", "none", "--cap", "2", "--snapshot", FILE).stdout
        self.assertIn("HANDBACK #5 blocked-by #1", route.splitlines())

    def test_fix_drift_posts_one_edge_and_the_next_run_has_no_drift(self):
        self.env.set_gh([
            gh_repo_rest(REPO),
            issues_reply([node(1), node(5, body="Blocked by: #1")]),
            {"args": ["api", "repos/{owner}/{repo}/issues/1", "--jq", ".id"], "stdout": "4242\n"},
            {"args": ["api", "--method", "POST"], "stdout": "{}"},
        ])
        out = self.env.run("frontier", "--all", "--fix-drift").stdout.splitlines()
        self.assertEqual(out[0], "added #5 blocked by #1")
        self.assertEqual(out[-2:], ["drift:", "none"])
        posts = [c for c in self.env.gh_calls() if c[:3] == ["api", "--method", "POST"]]
        self.assertEqual(posts, [["api", "--method", "POST", "repos/{owner}/{repo}/issues/5/dependencies/blocked_by",
                                  "-F", "issue_id=4242"]])
        # The next run reads the edge from the tracker: no drift, nothing more to add.
        fixed = node(5, body="Blocked by: #1", blockers=[(1, "OPEN")])
        self.env.set_gh([gh_repo_rest(REPO), issues_reply([node(1), fixed])])
        again = self.env.run("frontier", "--all", "--fix-drift").stdout.splitlines()
        self.assertEqual(again[-2:], ["drift:", "none"])
        self.assertFalse([l for l in again if l.startswith("added")])


if __name__ == "__main__":
    unittest.main()
