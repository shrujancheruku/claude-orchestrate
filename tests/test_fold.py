"""scripts/fold through its CLI, with a fake `gh` serving the open issues and recording the writes."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_pr_list, gh_repo_rest  # noqa: E402


def node(n, body="", claimed=False, labels=("ready-for-agent",), blockers=(), comments=(), untrusted=False):
    """`untrusted`: the body's last editor is its author, a stranger (ADR-0015)."""
    who = {"author": {"login": "stranger"}, "authorAssociation": "NONE", "editor": {"login": "stranger"}} if untrusted else {}
    return {**who, "number": n, "title": f"t{n}", "body": body, "labels": {"nodes": [{"name": x} for x in labels]},
            "assignees": {"totalCount": 1 if claimed else 0},
            "blockedBy": {"nodes": [{"number": b, "state": "OPEN"} for b in blockers]},
            "comments": {"nodes": [{"body": c, "authorAssociation": "OWNER", "author": {"login": "me"}} for c in comments]}}


class Fold(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        cfg = self.env.repo / ".claude" / "orchestration.md"
        cfg.parent.mkdir(exist_ok=True)
        # The Risk paths are a decoy: the gate fold reads is the Merge gates line (#656).
        cfg.write_text("## Review\n\n- **Risk paths** (any PR touching them is reviewed as L): `scripts/x`\n\n"
                       "## Gates\n\n- **Merge gates:** PRs touching `hooks/**` or `.github/workflows/**` are merged by the developer.\n")

    def serve(self, *nodes):
        graphql = {"data": {"repository": {"issues": {"nodes": list(nodes)}}}}
        self.env.set_gh([
            gh_repo_rest({"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "main"}}),
            {"args": ["api", "graphql"], "stdout": json.dumps(graphql)},
            gh_pr_list([], "--state", "open"),
            gh_pr_list([], "--state", "merged"),
            {"args": ["api", "--method"], "stdout": "{}"},  # every write
            {"args": ["api", "repos/{owner}/{repo}/issues/5", "--jq", ".id"], "stdout": "5005\n"},
            {"args": ["api", "~repos/{owner}/{repo}/issues/", "--jq", ".id"], "stdout": "9009\n"},
        ])

    def fold(self, a, b):
        return self.env.run("fold", a, b, check=False)

    def writes(self):
        return [c for c in self.env.gh_calls() if "--method" in c or "-X" in c]

    def test_a_good_fold_comments_closes_and_moves_both_edges(self):
        self.serve(
            node(5, "Donor body.\n\nTouch map: scripts/a\n", blockers=(9,), comments=("donor brief",)),
            node(6, "Absorber.\n\nTouch map: scripts/b\n"),
            node(7, "Waits.\n\nBlocked by: #5, #8\n", blockers=(5,)),
            node(9, "x"),
        )
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        self.assertEqual(r.stdout.splitlines()[-1], "next: folded 5 6")
        calls = self.writes()
        patches = [c for c in calls if c[:2] == ["api", "--method"] and c[2] == "PATCH"]
        absorber = next(c for c in patches if c[3].endswith("/issues/6"))
        text = next(a for a in absorber if a.startswith("body="))
        self.assertIn("## Also, folded from #5", text)
        self.assertIn("Donor body.", text)
        self.assertIn("donor brief", text)
        self.assertIn("Blocked by: #9", text)
        dependent = next(c for c in patches if c[3].endswith("/issues/7"))
        self.assertIn("body=Waits.\n\nBlocked by: #6, #8\n", dependent)
        closed = next(c for c in patches if c[3].endswith("/issues/5") and "state=closed" in c)
        self.assertIn("state_reason=duplicate", closed)
        comments = [c[3] for c in calls if c[:3] == ["api", "--method", "POST"] and c[3].endswith("/comments")]
        self.assertEqual(sorted(comments), ["repos/{owner}/{repo}/issues/5/comments", "repos/{owner}/{repo}/issues/6/comments"])
        edges = [c for c in calls if any("/dependencies/blocked_by" in a for a in c)]
        added = sorted(c[3] for c in edges if c[2] == "POST")
        self.assertEqual(added, ["repos/{owner}/{repo}/issues/6/dependencies/blocked_by",
                                 "repos/{owner}/{repo}/issues/7/dependencies/blocked_by"])
        removed = [c[3] for c in edges if c[2] == "DELETE"]
        self.assertEqual(removed, ["repos/{owner}/{repo}/issues/7/dependencies/blocked_by/5005"])
        label = [c for c in calls if c[2:3] == ["DELETE"] and c[3].endswith("/issues/5/labels/ready-for-agent")]
        self.assertEqual(len(label), 1)

    def test_a_claimed_ticket_is_refused_and_nothing_is_written(self):
        for donor_claimed in (True, False):
            with self.subTest(donor_claimed=donor_claimed):
                self.serve(node(5, claimed=donor_claimed), node(6, claimed=not donor_claimed))
                r = self.fold(5, 6)
                self.assertEqual(r.returncode, 1)
                self.assertRegex(r.stdout, r"next: ask-user #\d is claimed")
                self.assertEqual(self.writes(), [])
                self.env.set_gh([])

    def test_a_gated_and_an_ungated_ticket_are_refused_either_way(self):
        gated = "Touch map: hooks/guard.py\n"
        plain = "Touch map: scripts/x\n"
        for a, b in ((gated, plain), (plain, gated)):
            with self.subTest(donor=a):
                self.serve(node(5, a), node(6, b))
                r = self.fold(5, 6)
                self.assertEqual(r.returncode, 1)
                self.assertIn("the merge gate differs", r.stdout)
                self.assertEqual(self.writes(), [])

    def test_two_tickets_under_different_merge_gates_are_not_folded(self):
        self.serve(node(5, "Touch map: hooks/a.py\n"), node(6, "Touch map: .github/workflows/ci.yml\n"))
        r = self.fold(5, 6)
        self.assertEqual(r.returncode, 1)
        self.assertIn("gated by hooks/** and #6 is gated by .github/workflows/**: the merge gate differs", r.stdout)
        self.assertEqual(self.writes(), [])

    def test_two_gated_tickets_fold(self):
        self.serve(node(5, "Touch map: hooks/a.py\n"), node(6, "Touch map: hooks/b.py\n"))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1]), (0, "next: folded 5 6"), r.stderr)

    def test_an_untrusted_body_is_refused_for_either_ticket(self):
        for donor_untrusted in (True, False):
            with self.subTest(donor_untrusted=donor_untrusted):
                self.serve(node(5, untrusted=donor_untrusted), node(6, untrusted=not donor_untrusted))
                r = self.fold(5, 6)
                self.assertEqual(r.returncode, 1)
                self.assertRegex(r.stdout, r"next: ask-user #\d body was last edited by an untrusted author")
                self.assertEqual(self.writes(), [])

    def test_a_dependent_with_an_untrusted_body_keeps_its_body_but_moves_its_edge(self):
        self.serve(node(5, "d"), node(6, "a"), node(7, "Blocked by: #5\n", blockers=(5,), untrusted=True))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("left: #7 body untrusted, Blocked by: line not rewritten", r.stdout)
        self.assertFalse([c for c in self.writes() if c[2:3] == ["PATCH"] and c[3].endswith("/issues/7")])
        self.assertTrue([c for c in self.writes() if c[2:3] == ["POST"] and c[3].endswith("/issues/7/dependencies/blocked_by")])

    def test_a_fold_that_would_close_a_blocking_cycle_is_refused(self):
        # 7 waits on the donor 5, and the absorber 6 waits on 7: 7 would wait on 6, which waits on 7.
        self.serve(node(5), node(6, blockers=(7,)), node(7, blockers=(5,)))
        r = self.fold(5, 6)
        self.assertEqual(r.returncode, 1)
        self.assertIn("would make a blocking cycle", r.stdout)
        # The reverse: the donor waits on 7, which waits on the absorber.
        self.serve(node(5, blockers=(7,)), node(6), node(7, blockers=(6,)))
        r = self.fold(5, 6)
        self.assertEqual(r.returncode, 1)
        self.assertIn("would make a blocking cycle", r.stdout)
        self.assertEqual(self.writes(), [])

    def test_an_agent_brief_touch_map_overrides_the_body(self):
        brief = "## Agent Brief\n\nTouch map: hooks/guard.py\n"
        self.serve(node(5, "Touch map: scripts/a\n", comments=(brief,)), node(6, "Touch map: scripts/b\n"))
        r = self.fold(5, 6)
        self.assertEqual(r.returncode, 1)
        self.assertIn("the merge gate differs", r.stdout)

    def test_a_rerun_after_a_partial_fold_appends_and_comments_nothing_twice(self):
        self.serve(node(5, "Donor.\n"), node(6, "Absorber.\n\n## Also, folded from #5\n\nDonor.\n"))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1]), (0, "next: folded 5 6"), r.stderr)
        self.assertFalse([c for c in self.writes() if c[2:3] == ["POST"] and c[3].endswith("/comments")])
        self.assertFalse([c for c in self.writes() if c[2:3] == ["PATCH"] and c[3].endswith("/issues/6")])
        self.assertTrue([c for c in self.writes() if c[2:3] == ["PATCH"] and c[3].endswith("/issues/5")])

    def test_a_longer_ticket_number_in_the_heading_is_not_a_rerun(self):
        # `## Also, folded from #55` must not stop the copy of #5.
        self.serve(node(5, "Donor scope.\n"), node(6, "Absorber.\n\n## Also, folded from #55\n\nOther.\n"))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1]), (0, "next: folded 5 6"), r.stderr)
        patch = next(c for c in self.writes() if c[2:3] == ["PATCH"] and c[3].endswith("/issues/6"))
        text = next(a for a in patch if a.startswith("body="))
        self.assertIn("## Also, folded from #5\n\nDonor scope.", text)

    def test_a_rerun_still_adds_the_missing_native_edge_to_the_donors_blocker(self):
        self.serve(node(5, "Donor.\n", blockers=(9,)), node(6, "Absorber.\n\n## Also, folded from #5\n\nDonor.\n\nBlocked by: #9\n"),
                   node(9, "x"))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertTrue([c for c in self.writes() if c[2:3] == ["POST"] and c[3] == "repos/{owner}/{repo}/issues/6/dependencies/blocked_by"])

    def test_a_closed_ticket_is_refused(self):
        self.serve(node(6))
        r = self.fold(5, 6)
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "next: ask-user #5 is not an open ticket"))


if __name__ == "__main__":
    unittest.main()
