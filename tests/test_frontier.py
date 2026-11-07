"""scripts/frontier through its CLI, with a fake `gh` serving the open issues (two pages in one test)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_repo_view  # noqa: E402


def node(n, labels=("ready-for-agent",), claimed=False, blockers=(), body="", total=0, completed=0, parent=None):
    return {"number": n, "title": f"title {n}", "url": f"https://x/{n}", "body": body,
            "parent": {"number": parent} if parent else None,
            "subIssuesSummary": {"total": total, "completed": completed},
            "labels": {"nodes": [{"name": x} for x in labels]},
            "assignees": {"totalCount": 1 if claimed else 0},
            "blockedBy": {"nodes": [{"number": b, "state": s} for b, s in blockers]},
            "userContentEdits": {"nodes": []}, "comments": {"nodes": []}}


def page(nodes, cursor=None):
    info = {"hasNextPage": bool(cursor), "endCursor": cursor}
    return {"stdout": json.dumps({"data": {"repository": {"issues": {"pageInfo": info, "nodes": nodes}}}})}


class Frontier(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def serve(self, *pages, graphql_exit=0):
        graphql = {"args": ["api", "graphql"], "seq": list(pages)}
        if graphql_exit:
            graphql = {"args": ["api", "graphql"], "stderr": "HTTP 500", "exit": graphql_exit}
        self.env.set_gh([gh_repo_view(), graphql])

    def frontier(self, *args, check=True):
        return self.env.run("frontier", *args, check=check)

    def test_default_prints_one_line_per_takeable_ticket_ascending(self):
        self.serve(page([node(9), node(3), node(5, claimed=True), node(6, labels=("ready-for-human",))]))
        r = self.frontier()
        self.assertEqual(r.stdout, "#3 title 3\n#9 title 9\n")
        self.assertEqual(r.stderr, "")

    def test_two_pages_are_read_to_exhaustion_once_each(self):
        self.serve(page([node(2), node(4)], cursor="CUR1"), page([node(1), node(3)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1\n#2 title 2\n#3 title 3\n#4 title 4\n")
        calls = [c for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("after=CUR1", calls[0])
        self.assertIn("after=CUR1", calls[1])

    def test_a_repeated_cursor_is_malformed_not_a_loop(self):
        self.serve(page([node(1)], cursor="SAME"))
        r = self.frontier(check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))

    def test_all_prints_the_four_sections(self):
        self.serve(page([
            node(1), node(2, claimed=True), node(3, blockers=[(8, "OPEN")]), node(4, total=1, completed=1),
            node(5, body="Blocked by: #1"), node(6, labels=("ready-for-human",)),
            node(7, labels=("ready-for-agent", "lead")),
        ]))
        out = self.frontier("--all").stdout.splitlines()
        self.assertEqual(out, [
            "#1 title 1",
            "not-takeable:", "#2 claimed", "#3 blocked-by #8", "#4 parent", "#5 blocked-by #1 (body)", "#7 lead",
            "ready-for-human:", "#6 title 6",
            "parents-closed:", "#4 title 4",
            "drift:", "#5 body says blocked by #1, tracker does not",
        ])

    def test_all_says_none_for_each_empty_section(self):
        self.serve(page([node(1)]))
        self.assertEqual(self.frontier("--all").stdout.splitlines(),
                         ["#1 title 1", "not-takeable:", "none", "ready-for-human:", "none",
                          "parents-closed:", "none", "drift:", "none"])

    def test_parent_limits_every_section_to_its_sub_issues(self):
        self.serve(page([node(1, total=2), node(2, parent=1), node(3, parent=1, claimed=True), node(4)]))
        self.assertEqual(self.frontier("--parent", "1").stdout, "#2 title 2\n")
        out = self.frontier("--parent", "1", "--all").stdout.splitlines()
        self.assertEqual(out[:3], ["#2 title 2", "not-takeable:", "#3 claimed"])
        self.assertNotIn("#4 title 4", out)

    def test_ticket_prints_its_line_or_its_reason(self):
        self.serve(page([node(1), node(2, claimed=True), node(3, labels=())]))
        self.assertEqual(self.frontier("--ticket", "1").stdout, "#1 title 1\n")
        self.assertEqual(self.frontier("--ticket", "2").stdout, "#2 claimed\n")
        self.assertEqual(self.frontier("--ticket", "3").stdout, "#3 not ready-for-agent\n")
        self.assertEqual(self.frontier("--ticket", "99").stdout, "")

    def test_an_empty_frontier_exits_0_with_no_output(self):
        self.serve(page([node(1, claimed=True)]))
        r = self.frontier()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_the_label_string_comes_from_the_triage_labels_doc(self):
        doc = self.env.repo / "docs" / "agents" / "triage-labels.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("| `ready-for-agent` | `afk` | x |\n")
        self.serve(page([node(1, labels=("afk",)), node(2)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1\n")

    def test_it_logs_one_frontier_event_with_the_count(self):
        self.serve(page([node(1), node(2)]))
        self.frontier()
        events = [e for e in self.env.events() if e["kind"] == "frontier"]
        self.assertEqual([(e["outcome"], e["takeable"]) for e in events], [("ran", 2)])

    def test_a_failed_gh_exits_2_with_nothing_on_stdout(self):
        self.serve(graphql_exit=1)
        r = self.frontier(check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_a_malformed_reply_exits_2(self):
        self.serve({"stdout": json.dumps({"data": {}})})
        r = self.frontier(check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))

    def test_a_missing_gh_exits_2(self):
        r = self.env.run("frontier", check=False, no_gh=True)
        self.assertEqual((r.returncode, r.stdout), (2, ""))


if __name__ == "__main__":
    unittest.main()
