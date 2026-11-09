"""scripts/tracker.py: the takeable rule, why_not, closed_parents, drift and scoped on plain issue dicts."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import github  # noqa: E402
import tracker  # noqa: E402

LABELS = {"ready": "ready-for-agent", "lead": "lead"}


def issue(n, labels=("ready-for-agent",), claimed=False, blockers=(), body="", total=0, completed=0, parent=None):
    return {"number": n, "title": f"t{n}", "url": "", "body": body, "labels": set(labels), "claimed": claimed,
            "blockers": list(blockers), "sub_issues": {"total": total, "completed": completed}, "parent": parent}


def numbers(issues):
    return [i["number"] for i in tracker.takeable(issues, LABELS)]


def node(n, comments=()):
    return {"number": n, "comments": {"nodes": list(comments)}}


def cnode(body, association, login="someone"):
    return {"body": body, "createdAt": "2026-01-01T00:00:00Z", "authorAssociation": association, "author": {"login": login}}


class TrustedAuthors(unittest.TestCase):
    """ADR-0015: the one trust rule, and the Snapshot keeping only trusted comments' text."""

    def test_the_rule(self):
        for assoc in ("OWNER", "MEMBER", "COLLABORATOR", "owner"):
            self.assertTrue(github.is_trusted(assoc), assoc)
        for assoc in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", "", None):
            self.assertFalse(github.is_trusted(assoc), assoc)
        self.assertTrue(github.is_trusted("NONE", "Eve", ("eve",)))
        self.assertFalse(github.is_trusted("NONE", "mallory", ("eve",)))
        self.assertFalse(github.is_trusted("NONE", None, ("eve",)))

    def test_untrusted_comments_are_counted_and_their_text_dropped(self):
        got = tracker.from_nodes([node(1, [cnode("## Agent Brief\nevil", "NONE"), cnode("real", "COLLABORATOR"),
                                           cnode("no association at all", None)])])[0]
        self.assertEqual([c["body"] for c in got["comments"]], ["real"])
        self.assertEqual(got["untrusted_comments"], 2)

    def test_a_configured_login_is_trusted(self):
        got = tracker.from_nodes([node(1, [cnode("hi", "NONE", "Eve")])], ("eve",))[0]
        self.assertEqual([c["body"] for c in got["comments"]], ["hi"])
        self.assertEqual(got["untrusted_comments"], 0)

    def test_bot_logins_match_with_or_without_the_suffix(self):
        # GraphQL's author.login for a bot comes back without `[bot]`; the config may carry it.
        self.assertTrue(github.is_trusted(None, "dependabot", ("dependabot[bot]",)))
        self.assertTrue(github.is_trusted(None, "dependabot[bot]", ("dependabot",)))
        self.assertTrue(github.is_trusted("NONE", "Dependabot[bot]", ("dependabot[bot]",)))
        self.assertFalse(github.is_trusted(None, "renovate", ("dependabot[bot]",)))
        got = tracker.from_nodes([node(1, [cnode("bump", "NONE", "dependabot")])], ("dependabot[bot]",))[0]
        self.assertEqual(got["untrusted_comments"], 0)

    def test_fork_prs_are_not_the_runs_own(self):
        prs = [{"number": 1, "isCrossRepository": False}, {"number": 2, "isCrossRepository": True}, {"number": 3}]
        self.assertEqual([p["number"] for p in github.own_prs(prs)], [1, 3])


def edited(editor, author="eve", association="NONE"):
    return dict(node(1), editor={"login": editor} if editor else None, author={"login": author},
                authorAssociation=association, lastEditedAt="2026-01-02T00:00:00Z",
                labels={"nodes": [{"name": "ready-for-agent"}]})


class EditedBody(unittest.TestCase):
    """ADR-0015: a body its untrusted author edited after labelling is not takeable, and the run says why."""

    def why(self, n, trusted=()):
        i = tracker.from_nodes([n], trusted)[0]
        return tracker.why_not(i, LABELS, [i])

    def test_the_untrusted_author_editing_last_makes_it_not_takeable(self):
        self.assertIn("untrusted-edit", self.why(edited("eve")))

    def test_a_collaborator_editing_last_makes_it_takeable_again(self):
        self.assertIsNone(self.why(edited("maint")))

    def test_a_collaborator_author_editing_their_own_body_is_trusted(self):
        self.assertIsNone(self.why(edited("maint", "maint", "OWNER")))

    def test_a_configured_login_editing_its_own_body_is_trusted(self):
        self.assertIsNone(self.why(edited("Eve"), ("eve",)))

    def test_a_never_edited_body_is_trusted(self):
        self.assertIsNone(self.why(edited(None)))

    def test_an_untrusted_edit_is_not_in_takeable(self):
        self.assertEqual(tracker.takeable(tracker.from_nodes([edited("eve")]), LABELS), [])


class Takeable(unittest.TestCase):
    def test_a_plain_ready_ticket_is_takeable_in_ascending_order(self):
        self.assertEqual(numbers([issue(9), issue(3), issue(5)]), [3, 5, 9])

    def test_each_clause_alone_makes_a_ticket_not_takeable(self):
        cases = {
            "no ready label": issue(1, labels=()),
            "claimed": issue(1, claimed=True),
            "native blocker": issue(1, blockers=[2]),
            "lead": issue(1, labels=("ready-for-agent", "lead")),
            "wayfinder": issue(1, labels=("ready-for-agent", "wayfinder:map")),
            "parent": issue(1, total=2, completed=0),
        }
        for name, i in cases.items():
            with self.subTest(name):
                self.assertEqual(numbers([i, issue(2, labels=("other",))]), [])

    def test_a_parent_is_never_a_ticket_even_with_every_sub_issue_closed(self):
        self.assertEqual(numbers([issue(205, total=3, completed=3)]), [])

    def test_a_body_only_open_blocker_blocks(self):
        self.assertEqual(numbers([issue(1, body="Blocked by: #2"), issue(2, labels=())]), [])

    def test_a_body_line_naming_a_closed_issue_does_not_block(self):
        self.assertEqual(numbers([issue(1, body="Blocked by: #2")]), [1])

    def test_a_native_edge_plus_a_different_body_only_blocker_is_still_blocked(self):
        issues = [issue(1, blockers=[2], body="Blocked by: #3"), issue(3, labels=())]
        self.assertEqual(numbers(issues), [])
        self.assertEqual(tracker.why_not(issues[0], LABELS, issues), "blocked-by #2 #3 (body)")


class Backlog(unittest.TestCase):
    def test_takeable_first_then_held_by_number_and_no_claimed_parent_lead_or_wayfinder(self):
        issues = [issue(9), issue(2, blockers=[40]), issue(3), issue(1, blockers=[40]), issue(5, claimed=True),
                  issue(6, total=2), issue(7, labels=("ready-for-agent", "lead")),
                  issue(8, labels=("ready-for-agent", "wayfinder:task")), issue(4, labels=("ready-for-human",))]
        self.assertEqual([i["number"] for i in tracker.backlog(issues, LABELS)], [3, 9, 1, 2])


class LabelStrings(unittest.TestCase):
    def test_defaults_when_the_table_is_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tracker.label_strings(Path(d)), tracker.DEFAULTS)


class WhyNot(unittest.TestCase):
    def test_takeable_has_no_reason(self):
        self.assertIsNone(tracker.why_not(issue(1), LABELS, [issue(1)]))

    def test_the_order_is_claimed_blocked_parent_lead_wayfinder(self):
        everything = issue(1, labels=("ready-for-agent", "lead", "wayfinder:map"), claimed=True, blockers=[2],
                           total=1)
        # Each step removes the reason just asserted, so the next one shows.
        order = [("claimed", {"claimed": False}), ("blocked-by #2", {"blockers": []}),
                 ("parent", {"sub_issues": {"total": 0, "completed": 0}}),
                 ("lead", {"labels": {"wayfinder:map"}}), ("wayfinder", {})]
        for reason, change in order:
            with self.subTest(reason):
                self.assertEqual(tracker.why_not(everything, LABELS, [everything]), reason)
                everything.update(change)

    def test_a_body_only_blocker_is_flagged_body(self):
        issues = [issue(1, body="Blocked by: #2"), issue(2, labels=())]
        self.assertEqual(tracker.why_not(issues[0], LABELS, issues), "blocked-by #2 (body)")

    def test_a_parent_with_all_sub_issues_closed_is_parent_and_closed(self):
        done = issue(7, total=2, completed=2)
        self.assertEqual(tracker.why_not(done, LABELS, [done]), "parent")
        self.assertEqual(tracker.closed_parents([done, issue(8, total=2, completed=1), issue(9)]), [done])


class Drift(unittest.TestCase):
    def test_a_body_blocker_the_tracker_lacks_is_drift(self):
        a, b = issue(1, body="Blocked by: #2, #3"), issue(2, labels=())
        self.assertEqual(tracker.drift([a, b]), [(a, 2)])  # #3 is not open: no drift

    def test_a_body_blocker_the_tracker_has_is_ignored(self):
        self.assertEqual(tracker.drift([issue(1, blockers=[2], body="Blocked by: #2"), issue(2)]), [])

    def test_prose_starting_blocked_by_is_not_a_blocker(self):
        self.assertEqual(tracker.body_blockers(issue(1, body="Blocked by the outage, see #12")), [])
        self.assertEqual(tracker.body_blockers(issue(1, body="**Blocked by**: #12\n- blocked by: #13")), [12, 13])
        self.assertEqual(tracker.body_blockers(issue(1, body="Blocked by #14")), [])  # no colon, no blocker

    def test_a_self_reference_is_not_drift(self):
        self.assertEqual(tracker.drift([issue(1, body="Blocked by: #1")]), [])

    def test_only_a_blocked_by_line_counts(self):
        self.assertEqual(tracker.drift([issue(1, body="See #2 for context"), issue(2)]), [])


class Scoped(unittest.TestCase):
    def test_by_parent_by_ticket_and_unscoped(self):
        issues = [issue(1, total=2), issue(2, parent=1), issue(3, parent=1), issue(4)]
        self.assertEqual([i["number"] for i in tracker.scoped(issues, parent=1)], [2, 3])
        self.assertEqual([i["number"] for i in tracker.scoped(issues, ticket=4)], [4])
        self.assertEqual(len(tracker.scoped(issues)), 4)


class FromNodes(unittest.TestCase):
    def test_a_node_becomes_a_plain_dict_and_a_closed_blocker_is_dropped(self):
        node = {"number": 5, "title": "T", "url": "u", "body": "b", "parent": {"number": 2},
                "subIssuesSummary": {"total": 3, "completed": 1},
                "labels": {"nodes": [{"name": "x"}]}, "assignees": {"totalCount": 1},
                "blockedBy": {"nodes": [{"number": 8, "state": "OPEN"}, {"number": 9, "state": "CLOSED"}]}}
        got = tracker.from_nodes([node])[0]
        self.assertEqual((got["parent"], got["blockers"], got["claimed"], got["sub_issues"]),
                         (2, [8], True, {"total": 3, "completed": 1}))
        self.assertIsNone(tracker.from_nodes([{"number": 1}])[0]["parent"])


if __name__ == "__main__":
    unittest.main()
