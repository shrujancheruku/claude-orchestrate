"""scripts/board through its CLI, with a fake `gh` serving the project, the tickets and the PRs.

Also the prompt side: setup offers the board and writes the `Board:` line, and `run` syncs it in step 5.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_pr_list, gh_repo_rest, git  # noqa: E402

import common  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/orgs/acme/projects/7"
OPTIONS = ["Backlog", "Building", "In review", "Needs lead", "Waiting on you", "Done"]
FIELD = {"id": "F1", "name": "Stage", "options": [{"id": "o" + str(i), "name": n} for i, n in enumerate(OPTIONS)]}
OPT = {n: "o" + str(i) for i, n in enumerate(OPTIONS)}
REPO = {"owner": {"login": "acme"}, "name": "r", "defaultBranchRef": {"name": "main"}}
# A board made before Backlog existed: the old five options, with colors the upgrade must keep.
OLD_OPTIONS = [{"id": "old" + str(i), "name": n, "color": "BLUE", "description": f"d{i}"}
               for i, n in enumerate(OPTIONS[1:])]
OLD_FIELD = {"id": "F1", "name": "Stage", "options": OLD_OPTIONS}


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket(n, labels=(), claimed=False, blocked_by=()):
    """An open issue as the tracker's GraphQL query returns it."""
    return {"number": n, "title": f"t{n}", "url": f"https://github.com/acme/r/issues/{n}",
            "labels": {"nodes": [{"name": x} for x in labels]}, "assignees": {"totalCount": 1 if claimed else 0},
            "blockedBy": {"nodes": [{"number": b, "state": "OPEN"} for b in blocked_by]}}


def card(n, stage=None, item="I", repository="acme/r", type="Issue"):
    c = {"id": f"{item}{n}", "content": {"type": type, "number": n, "repository": repository, "title": f"t{n}"}}
    if stage:
        c["stage"] = stage
    return c


def out(data):
    return {"stdout": json.dumps(data)}


def live_reason():
    """A harness-format lock reason for this running process (its pid and start time), so the lock is live."""
    start = common.process_start(os.getpid())
    shown = start.astimezone(timezone.utc).strftime("%a %b %e %H:%M:%S %Y")
    return f"claude agent agent-abc (pid {os.getpid()} start {shown})"


class Board(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def config(self, line=f"- **Board:** {URL}\n"):
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text("# Orchestration config\n\n## Backlog\n\n" + line)

    ISSUES_Q = "~query=query($owner: String!, $name: String!"
    PROJECT_Q = "~query=query($owner: String!, $number: Int!"
    LOOKUP_Q = "~query=query {"
    MUTATION_Q = "~query=mutation {"

    def project_reply(self, cards, fields, owner="User"):
        nodes = []
        for c in cards:
            content = c["content"]
            node_content = {"__typename": content["type"], "title": content.get("title")}
            if content["type"] == "Issue":
                node_content.update(number=content["number"], repository={"nameWithOwner": content["repository"]})
            nodes.append({"id": c["id"], "content": node_content, "stage": {"name": c["stage"]} if c.get("stage") else None})
        project = {"id": "P1", "field": fields[0] if fields else None,
                   "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}
        return out({"data": {"repositoryOwner": {"__typename": owner, "projectV2": project}}})

    def gh(self, tickets=(), prs=(), cards=(), fields=(FIELD,), extra=(), new_ids=("NEW",), graphql=(), owner="User",
           project=None):
        """Canned gh. Each GraphQL call is told by its query: the tracker's issues, the one project read, the
        content ids of new cards, and the mutations. `graphql` are the replies to a field create or upgrade, the
        first mutation; the next is the adds' reply (the item ids in `new_ids`), then an empty one for the rest."""
        issues = out({"data": {"repository": {"issues": {"nodes": list(tickets)}}}})
        created = [] if fields else [out({"data": {"createProjectV2Field": {"projectV2Field": FIELD}}})]
        adds = out({"data": {f"m{k}": {"item": {"id": i}} for k, i in enumerate(new_ids)}})
        lookup = out({"data": {"repository": {f"i{n}": {"id": f"C{n}"} for n in range(1, 200)}}})
        self.env.set_gh([
            *extra,
            gh_repo_rest(REPO),
            {"args": ["api", "graphql", "-f", self.ISSUES_Q], **issues},
            {"args": ["api", "graphql", "-f", self.PROJECT_Q], **(project or self.project_reply(cards, fields, owner))},
            {"args": ["api", "graphql", "-f", self.LOOKUP_Q], **lookup},
            {"args": ["api", "graphql", "-f", self.MUTATION_Q], "seq": [*created, *graphql, adds, out({"data": {}})]},
            gh_pr_list([p for p in prs if p.get("state") == "OPEN"], "--state", "open"),
            gh_pr_list([p for p in prs if p.get("state") == "MERGED"], "--state", "merged"),
        ])

    def queries(self):
        """Every GraphQL query text sent through `gh api graphql`, in order."""
        return [a[len("query="):] for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]
                for a in c if a.startswith("query=")]

    def mutations(self):
        """The mutation requests sent, in order."""
        return [q for q in self.queries() if q.startswith("mutation")]

    def fragments(self, name=None):
        """The aliased fields of the card-write mutations (`m0: ...`), in order; those called `name` when given."""
        found = []
        for q in self.mutations():
            if re.match(r"mutation \{ m\d+: ", q):
                body = q[len("mutation { "):-len(" }")]
                found += [f.strip() for f in re.split(r"(?:^|\s)m\d+: ", body) if f.strip()]
        return [f for f in found if name is None or f.startswith(name + "(")]

    def field_writes(self):
        """The field create and upgrade mutations (one field call each, not aliased)."""
        return [q for q in self.mutations() if not re.match(r"mutation \{ m\d+: ", q)]

    def graphql_writes(self):
        """The position moves, as their mutation fields."""
        return self.fragments("updateProjectV2ItemPosition")

    def deletes(self):
        return [re.search(r'itemId: "([^"]+)"', f).group(1) for f in self.fragments("deleteProjectV2Item")]

    def archives(self):
        return [re.search(r'itemId: "([^"]+)"', f).group(1) for f in self.fragments("archiveProjectV2Item")]

    def adds(self):
        """The issue numbers of the cards added (the content ids are C<number> in the fake)."""
        return [int(re.search(r'contentId: "C(\d+)"', f).group(1)) for f in self.fragments("addProjectV2ItemById")]

    def edits(self, names=None):
        """{item id: stage name} for each stage write."""
        names = names or {v: k for k, v in OPT.items()}
        found = {}
        for f in self.fragments("updateProjectV2ItemFieldValue"):
            found[re.search(r'itemId: "([^"]+)"', f).group(1)] = names[re.search(r'singleSelectOptionId: "([^"]+)"', f).group(1)]
        return found

    def writes(self):
        return self.fragments() + self.field_writes()

    def test_no_board_line_is_silent_and_calls_nothing(self):
        self.config("- **Slots:** 2\n")
        self.gh()
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.env.gh_calls(), [])

    def test_each_tracker_state_lands_in_its_column(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(2, claimed=True), ticket(3, ["needs-lead"], True),
                         ticket(4, ["ready-for-human"]), ticket(5, ["needs-info"], True), ticket(6)],
                prs=[{"number": 12, "headRefName": "2-thing", "state": "OPEN", "mergedAt": None}],
                cards=[card(1), card(2), card(3), card(4), card(5)])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.edits(), {"I1": "Building", "I2": "In review", "I3": "Needs lead",
                                        "I4": "Waiting on you", "I5": "Waiting on you"})

    def test_a_draft_pr_stages_as_building_beside_a_ready_one_in_review(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(2, claimed=True)],
                prs=[{"number": 11, "headRefName": "1-thing", "state": "OPEN", "mergedAt": None, "isDraft": True},
                     {"number": 12, "headRefName": "2-thing", "state": "OPEN", "mergedAt": None, "isDraft": False}],
                cards=[card(1, "In review"), card(2)])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.edits(), {"I1": "Building", "I2": "In review"})

    def test_a_permission_denied_ticket_waits_on_you_until_a_pr_exists(self):
        import runlog
        self.config()
        runlog.append_event(self.env.repo, runlog.WORKER_REPORT, "valid", ref="1", status="stopped",
                            stop_reason="permission-denied", denied_command="gh pr create")
        runlog.append_event(self.env.repo, runlog.WORKER_REPORT, "valid", ref="2", status="stopped",
                            stop_reason="permission-denied", denied_command="gh pr create")
        self.gh(tickets=[ticket(1, claimed=True), ticket(2, claimed=True)],
                prs=[{"number": 12, "headRefName": "2-thing", "state": "OPEN", "mergedAt": None}],
                cards=[card(1), card(2)])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        # #2's PR now exists (the user ran the command): it is in review, not waiting.
        self.assertEqual(self.edits(), {"I1": "Waiting on you", "I2": "In review"})

    def test_a_card_is_added_when_a_ticket_is_first_claimed_and_not_for_an_unclaimed_one(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(6, ["ready-for-human"])])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.adds(), [1])
        self.assertEqual(self.edits(), {"NEW": "Building"})

    def test_an_elsewhere_claim_and_a_stalled_claim_both_stage_as_building(self):
        self.config()
        wt_dir = tempfile.TemporaryDirectory()
        self.addCleanup(wt_dir.cleanup)
        path = Path(wt_dir.name) / "5-widget"
        git(self.env.repo, "worktree", "add", "-b", "5-widget", str(path))
        git(self.env.repo, "worktree", "lock", "--reason", live_reason(), str(path))  # live lock: elsewhere
        self.gh(tickets=[ticket(5, claimed=True), ticket(6, claimed=True)],
                cards=[card(5, "In review"), card(6, "In review")])  # no record, no PR: 6 is stalled
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.edits(), {"I5": "Building", "I6": "Building"})

    def test_a_second_run_changes_nothing(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(3, ["needs-lead"], True)],
                prs=[{"number": 11, "headRefName": "1-x", "state": "OPEN", "mergedAt": None}],
                cards=[card(1, "In review"), card(3, "Needs lead"), card(9, "Done")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.writes(), [])

    def test_done_is_set_after_a_merge_and_archived_after_a_day(self):
        self.config()
        self.gh(prs=[{"headRefName": "8-a", "state": "MERGED", "mergedAt": ago(hours=2)},
                     {"headRefName": "9-b", "state": "MERGED", "mergedAt": ago(hours=30)}],
                cards=[card(8, "In review"), card(9, "Done")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.edits(), {"I8": "Done"})
        self.assertEqual(self.archives(), ["I9"])

    def test_the_stage_field_is_created_when_missing(self):
        self.config()
        self.gh(fields=[])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        create = self.field_writes()
        self.assertEqual(len(create), 1)
        self.assertIn("createProjectV2Field", create[0])
        self.assertEqual(re.findall(r'name: "([^"]+)", color: GRAY', create[0]), OPTIONS)

    def synced(self, cards, *flags):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(3, ["needs-lead"], True)],
                prs=[{"number": 11, "headRefName": "1-x", "state": "OPEN", "mergedAt": None}], cards=cards)
        return self.env.run("board", *flags, check=False)

    def test_a_board_that_matches_makes_zero_writes_and_verbose_says_so(self):
        r = self.synced([card(1, "In review"), card(3, "Needs lead")], "--verbose")
        self.assertEqual((r.returncode, r.stderr.strip()), (0, "writes: 0"))
        self.assertEqual(self.writes(), [])

    def test_one_moved_card_is_exactly_one_write(self):
        r = self.synced([card(1, "Building"), card(3, "Needs lead")], "--verbose")
        self.assertEqual(r.stderr.strip(), "writes: 1")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.edits(), {"I1": "In review"})

    def test_one_removed_card_is_exactly_one_deletion(self):
        r = self.synced([card(1, "In review"), card(3, "Needs lead"), card(9, "Backlog")], "--verbose")
        self.assertEqual(r.stderr.strip(), "writes: 1")
        self.assertEqual(self.deletes(), ["I9"])
        self.assertEqual(len(self.writes()), 1)

    def test_dry_run_ends_with_the_count_of_planned_writes(self):
        r = self.synced([card(1, "Building"), card(3, "Needs lead"), card(9, "Backlog")], "--dry-run", "--verbose")
        self.assertEqual(r.stdout.splitlines()[-1], "2 writes planned")
        self.assertEqual(r.stderr.strip(), "writes: 2")
        self.assertEqual(self.writes(), [])

    # The Backlog fixture: 10, 11, 12 takeable; 15 and 20 held by the open blocker 21; 1 building; 8 merged.
    BACKLOG_TICKETS = [ticket(10, ["ready-for-agent"]), ticket(11, ["ready-for-agent"]), ticket(12, ["ready-for-agent"]),
                       ticket(15, ["ready-for-agent"], blocked_by=[21]), ticket(20, ["ready-for-agent"], blocked_by=[21]),
                       ticket(21), ticket(1, ["ready-for-agent"], claimed=True)]
    MERGED = [{"number": 9, "headRefName": "8-a", "state": "MERGED", "mergedAt": ago(hours=2)}]

    def test_backlog_cards_follow_frontier_order_then_held_by_number_one_write_per_misplaced_card(self):
        self.config()
        self.gh(tickets=self.BACKLOG_TICKETS, prs=self.MERGED, new_ids=["N11", "N20"],
                cards=[card(12, "Backlog"), card(10, "Backlog"), card(15, "Backlog"), card(1, "Building"),
                       card(8, "In review")])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.adds(), [11, 20])
        self.assertEqual(self.edits(), {"N11": "Backlog", "N20": "Backlog", "I8": "Done"})
        # Items sit I12 I10 I15 N11 N20; wanted I10 N11 I12 I15 N20: I10 goes first, N11 after it, nothing else.
        moves = self.graphql_writes()
        self.assertEqual(len(moves), 2)
        self.assertIn('itemId: "I10"', moves[0])
        self.assertNotIn("afterId", moves[0])
        self.assertIn('itemId: "N11"', moves[1])
        self.assertIn('afterId: "I10"', moves[1])
        self.assertTrue(all("updateProjectV2ItemPosition" in m and 'projectId: "P1"' in m for m in moves))

    def test_an_ordered_backlog_makes_no_write_on_a_second_run(self):
        self.config()
        self.gh(tickets=self.BACKLOG_TICKETS, prs=self.MERGED,
                cards=[card(10, "Backlog"), card(11, "Backlog"), card(12, "Backlog"), card(15, "Backlog"),
                       card(20, "Backlog"), card(1, "Building"), card(8, "Done")])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.writes(), [])

    def test_parents_wayfinder_and_lead_tickets_are_not_in_backlog(self):
        self.config()
        self.gh(tickets=[ticket(30, ["ready-for-agent", "wayfinder:task"]), ticket(31, ["ready-for-agent", "lead"]),
                         ticket(32, ["ready-for-agent"])], new_ids=["N31", "N32"])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        # 31 is the lead's: it gets a card as a lead ticket (Building), never as Backlog.
        self.assertEqual(self.adds(), [31, 32])

    def test_a_backlog_card_that_stops_qualifying_is_deleted_and_a_claimed_one_moves(self):
        self.config()
        self.gh(tickets=[ticket(7), ticket(8, ["ready-for-agent"], claimed=True), ticket(5, ["ready-for-human"])],
                cards=[card(7, "Backlog"), card(8, "Backlog"), card(9, "Backlog"), card(5, "Backlog")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        # 7 lost its label, 5 became human work (unclaimed), 9 is closed: all leave. 8 was claimed: Building.
        self.assertEqual(sorted(self.deletes()), ["I5", "I7", "I9"])
        self.assertEqual(self.edits(), {"I8": "Building"})

    def test_lead_tickets_get_cards_building_without_a_pr_and_in_review_with_a_ready_one(self):
        self.config()
        self.gh(tickets=[ticket(30, ["lead"]), ticket(31, ["lead"]), ticket(32, ["lead"])],
                prs=[{"number": 55, "headRefName": "31-x", "state": "OPEN", "isDraft": False},
                     {"number": 56, "headRefName": "32-y", "state": "OPEN", "isDraft": True}],
                new_ids=["N30", "N31", "N32"])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.edits(), {"N30": "Building", "N31": "In review", "N32": "Building"})

    def test_cards_that_are_not_this_repos_issues_are_left_alone(self):
        self.config()
        # 7 is unlabelled here, but its Backlog card is another repository's #7; 8 is a draft card; 5's card in the
        # other repository must not be moved: this repository's #5 gets a card of its own.
        self.gh(tickets=[ticket(5, claimed=True)],
                cards=[card(7, "Backlog", repository="acme/other"), card(8, "Backlog", type="DraftIssue"),
                       card(9, "Building", repository="acme/other"), card(5, "Done", repository="acme/other")])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.edits(), {"NEW": "Building"})
        self.assertEqual(self.archives(), [])

    def test_a_card_whose_ticket_closed_without_a_pr_is_archived_but_an_open_ticket_keeps_its_card(self):
        self.config()
        self.gh(tickets=[ticket(31)], cards=[card(30, "Building"), card(31, "Building"), card(32, "Done")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.archives(), ["I30"])

    def test_an_open_lead_pr_beyond_the_first_hundred_is_still_a_card_in_review(self):
        self.config()
        others = [{"number": 1000 + n, "headRefName": f"{500 + n}-x", "state": "OPEN", "isDraft": False}
                  for n in range(149)]
        lead_pr = {"number": 2000, "headRefName": "31-x", "state": "OPEN", "isDraft": False}
        self.gh(tickets=[ticket(31, ["lead"])], prs=others + [lead_pr], new_ids=["N31"])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.edits(), {"N31": "In review"})
        # More than one page of open PRs is read, not the first hundred only.
        pages = [c[1] for c in self.env.gh_calls() if c[0] == "api" and c[1].startswith("repos/{owner}/{repo}/pulls?state=open")]
        self.assertTrue(any(p.endswith("page=2") for p in pages), pages)

    def test_a_failed_lead_route_skips_lead_cards_with_one_stderr_line_and_exits_0(self):
        self.config()
        scripts = Path(self.env._tmp.name) / "scripts-copy"
        shutil.copytree(ROOT / "scripts", scripts)
        (scripts / "lead-route").write_text("import sys\nprint('lead-route: cannot run: boom', file=sys.stderr)\nsys.exit(2)\n")
        self.gh(tickets=[ticket(30, ["lead"]), ticket(1, claimed=True)], cards=[card(1)])
        r = self.env.run(scripts / "board", check=False)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn("lead cards skipped", r.stderr)
        self.assertIn("boom", r.stderr)
        # The worker's card still syncs; the lead ticket gets none.
        self.assertEqual(self.edits(), {"I1": "Building"})
        self.assertEqual(self.adds(), [])

    def test_dry_run_prints_each_planned_write_and_each_card_left_alone_and_writes_nothing(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(10, ["ready-for-agent"])], fields=[OLD_FIELD],
                cards=[card(1, "Done"), card(7, "Backlog", repository="acme/other")],
                )
        r = self.env.run("board", "--dry-run", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        lines = r.stdout.splitlines()
        self.assertTrue(any(l.startswith("would update field Stage options to Backlog, Building") for l in lines), lines)
        self.assertIn("would add a card for #10", lines)
        self.assertIn("would move card I1 to Building", lines)
        self.assertTrue(any(l.startswith("left alone: Issue acme/other#7") for l in lines), lines)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.graphql_writes(), [])

    def test_an_old_board_gets_backlog_in_one_field_write_keeping_ids_and_colors(self):
        self.config()
        new = [{"id": "oB", "name": "Backlog"}] + [{"id": o["id"], "name": o["name"]} for o in OLD_OPTIONS]
        mutated = out({"data": {"updateProjectV2Field": {"projectV2Field": {"options": new}}}})
        self.gh(tickets=[ticket(1, claimed=True)], fields=[OLD_FIELD], cards=[card(1, "Done")], graphql=[mutated])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        writes = self.field_writes()
        self.assertEqual(len(writes), 1)  # the options are read with the project: no second read for them
        q = writes[0]
        self.assertIn("updateProjectV2Field", q)
        self.assertLess(q.index('name: "Backlog"'), q.index('id: "old0"'))
        for o in OLD_OPTIONS:
            self.assertIn(f'id: "{o["id"]}", name: "{o["name"]}", color: BLUE, description: "{o["description"]}"', q)
        self.assertIn("color: GRAY", q)
        # The upgraded field serves the same sync: the claimed ticket moves to Building by its kept option id.
        self.assertEqual(self.edits({"old0": "Building"}), {"I1": "Building"})

    def test_a_board_that_already_matches_gets_no_field_write(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True)], cards=[card(1, "Building")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.field_writes(), [])

    def test_a_failure_is_one_line_and_exit_1(self):
        self.config()
        self.gh(extra=[{"args": ["api", "graphql", "-f", self.PROJECT_Q], "exit": 1,
                        "stderr": "error: your authentication token is missing the 'project' scope\nmore\n"}])
        r = self.env.run("board", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertTrue(r.stderr.startswith("board: "))
        self.assertIn("project", r.stderr)

    def test_an_unreadable_board_url_fails_in_one_line(self):
        self.config("- **Board:** https://example.com/nope\n")
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (1, 1))

    def test_a_sync_with_no_change_is_one_board_read_and_no_mutation(self):
        r = self.synced([card(1, "In review"), card(3, "Needs lead")])
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        reads = [q for q in self.queries() if "repositoryOwner" in q]
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.mutations(), [])

    def test_three_changed_rows_are_one_mutation_with_three_aliases(self):
        self.config()
        self.gh(tickets=[ticket(n, claimed=True) for n in (1, 2, 3)], cards=[card(n, "Done") for n in (1, 2, 3)])
        r = self.env.run("board", "--verbose", check=False)
        self.assertEqual((r.returncode, r.stderr.strip()), (0, "writes: 3"))
        mutations = self.mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(re.findall(r"\bm(\d+): ", mutations[0]), ["0", "1", "2"])
        self.assertEqual(self.edits(), {"I1": "Building", "I2": "Building", "I3": "Building"})

    def test_a_mutation_holds_at_most_twenty_rows(self):
        self.config()
        numbers = range(1, 46)
        self.gh(tickets=[ticket(n, claimed=True) for n in numbers], cards=[card(n, "Done") for n in numbers])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual([len(re.findall(r"\bm\d+: ", q)) for q in self.mutations()], [20, 20, 5])
        self.assertEqual(len(self.edits()), 45)

    def test_new_cards_cost_a_content_read_an_add_mutation_and_one_mutation_for_the_rest(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(2, claimed=True)], cards=[card(3, "Done")],
                new_ids=["N1", "N2"])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(len([q for q in self.queries() if q.startswith("query {")]), 1)
        self.assertEqual(len(self.mutations()), 2)
        self.assertEqual(self.adds(), [1, 2])
        self.assertEqual(self.edits(), {"N1": "Building", "N2": "Building"})

    def test_every_query_sent_has_balanced_braces_and_parentheses(self):
        self.config()
        self.gh(tickets=self.BACKLOG_TICKETS, prs=self.MERGED, new_ids=["N11", "N20"], fields=[OLD_FIELD],
                cards=[card(12, "Backlog"), card(10, "Backlog"), card(15, "Backlog"), card(1, "Done"), card(8, "In review")],
                graphql=[out({"data": {"updateProjectV2Field": {"projectV2Field": {"options": [
                    {"id": "oB", "name": "Backlog"}] + [{"id": o["id"], "name": o["name"]} for o in OLD_OPTIONS]}}}})])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        queries = self.queries()
        self.assertGreaterEqual(len(queries), 5)
        for q in queries:
            with self.subTest(q[:60]):
                self.assertEqual((q.count("{"), q.count("(")), (q.count("}"), q.count(")")))

    def test_the_project_query_reads_the_field_the_board_writes(self):
        import github
        source = (ROOT / "scripts" / "board").read_text()
        self.assertIn('FIELD = "Stage"', source)
        self.assertIn('field(name: "Stage")', github.PROJECT_QUERY)
        self.assertIn('fieldValueByName(name: "Stage")', github.PROJECT_QUERY)

    def test_a_rate_limited_owner_lookup_is_reported_as_the_rate_limit(self):
        self.config()
        reset = int(time.time()) + 3600
        self.gh(extra=[{"args": ["api", "graphql", "-f", self.PROJECT_Q], "exit": 1,
                        "stderr": f"gh: API rate limit exceeded (HTTP 403)\nx-ratelimit-remaining: 0\n"
                                  f"x-ratelimit-reset: {reset}\n"}])
        r = self.env.run("board", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("rate limit", r.stderr)
        self.assertNotIn("unknown owner type", r.stderr)

    def test_an_empty_owner_reply_is_a_lookup_failure_with_exit_2(self):
        self.config()
        self.gh(project=out({}))
        r = self.env.run("board", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.strip(), "board: owner lookup failed: gh returned no owner")

    def test_an_owner_reply_with_a_gh_error_carries_its_message(self):
        self.config()
        self.gh(project=out({"data": {"repositoryOwner": None}, "errors": [{"message": "Could not resolve to an owner"}]}))
        r = self.env.run("board", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.strip(), "board: owner lookup failed: Could not resolve to an owner")

    def test_a_bot_owner_is_an_unknown_owner_type(self):
        self.config()
        self.gh(owner="Bot")
        r = self.env.run("board", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "board: unknown owner type: Bot")

    def test_an_organization_owner_syncs(self):
        self.config()
        self.gh(owner="Organization", tickets=[ticket(1, claimed=True)], cards=[card(1, "Building")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)


class Prompts(unittest.TestCase):
    def test_setup_offers_the_board_and_writes_the_line(self):
        text = (ROOT / "skills" / "setup" / "SKILL.md").read_text()
        for phrase in ("! gh auth refresh -h <host> -s project", "`origin`", "`github.com`",
                       "- **Board:** <project URL>", "gh project create", "gh project link",
                       "@me", "retry or skip", "gh project list --owner @me", "skip with one line",
                       "lacks the `project` scope", "stays unlinked", "write the line",
                       "Board layout, grouped by `Stage`"):
            self.assertIn(phrase, text)
        self.assertIn("Board** layout, grouped by `Stage`", (ROOT / "docs" / "guide.md").read_text())

    def test_no_prompt_or_doc_gives_the_refresh_command_without_a_host(self):
        # Without -h, gh refuses to run under `!` (no TTY): "--hostname required when not running interactively".
        for rel in ("skills/setup/SKILL.md", "docs/guide.md", "README.md"):
            path = ROOT / rel
            if not path.exists():
                continue
            text = path.read_text()
            self.assertNotIn("gh auth refresh -s project", text, rel)
            if "gh auth refresh" in text:
                self.assertIn("gh auth refresh -h <host> -s project", text, rel)

    def test_run_syncs_the_board_in_step_5_and_carries_on_when_it_fails(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text()
        step5 = text.split("5. **Report and yield.**", 1)[1]
        m = re.search(r"scripts/board\" --repo \.", step5)
        self.assertTrue(m, "step 5 runs board")
        self.assertIn("never fails the pass", step5)


if __name__ == "__main__":
    unittest.main()
