"""scripts/snapshot and the `--snapshot` flag of claims, frontier, holds and lead-route: one tracker read per reconcile,
one blocker rule, and `frontier --fix-drift`."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402
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
            {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
            issues_reply(nodes),
            {"args": ["api", "graphql"], "stdout": json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})},
            {"args": ["pr", "list", "--state", "open"], "stdout": json.dumps(list(prs))},
            {"args": ["pr", "list", "--state", "merged"], "stdout": "[]"},
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

    def test_a_missing_snapshot_file_could_not_run(self):
        r = self.env.run("frontier", "--snapshot", "nope.json", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_a_body_only_blocker_is_held_by_frontier_and_handed_back_by_lead_route(self):
        self.serve([node(1), node(5, claimed=True, body="Blocked by: #1")])
        self.env.run("snapshot", "--repo", ".")
        self.assertEqual(self.env.run("frontier", "--snapshot", FILE).stdout, "#1 title 1\n")
        route = self.env.run("lead-route", "--lead", "none", "--cap", "2", "--snapshot", FILE).stdout
        self.assertIn("HANDBACK #5 blocked-by #1", route.splitlines())

    def test_fix_drift_posts_one_edge_and_the_next_run_has_no_drift(self):
        self.env.set_gh([
            {"args": ["repo", "view"], "stdout": json.dumps(REPO)},
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
        self.env.set_gh([{"args": ["repo", "view"], "stdout": json.dumps(REPO)}, issues_reply([node(1), fixed])])
        again = self.env.run("frontier", "--all", "--fix-drift").stdout.splitlines()
        self.assertEqual(again[-2:], ["drift:", "none"])
        self.assertFalse([l for l in again if l.startswith("added")])


if __name__ == "__main__":
    unittest.main()
