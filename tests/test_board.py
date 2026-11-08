"""scripts/board through its CLI, with a fake `gh` serving the project, the tickets and the PRs.

Also the prompt side: setup offers the board and writes the `Board:` line, and `run` syncs it in step 5.
"""

import json
import os
import re
import shutil
import sys
import tempfile
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
OLD_FIELD = {"id": "F1", "name": "Stage", "options": [{"id": o["id"], "name": o["name"]} for o in OLD_OPTIONS]}


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

    def gh(self, tickets=(), prs=(), cards=(), fields=(FIELD,), extra=(), new_ids=("NEW",), graphql=()):
        """Canned gh. The graphql replies come in call order: the tracker's issues first, then `graphql` (a field
        upgrade's two calls, then one empty reply for every card position written)."""
        issues = out({"data": {"repository": {"issues": {"nodes": list(tickets)}}}})
        self.env.set_gh([
            *extra,
            gh_repo_rest(REPO),
            {"args": ["api", "graphql"], "seq": [issues, *graphql, out({"data": {}})]},
            {"args": ["project", "view"], **out({"id": "P1"})},
            {"args": ["project", "field-list"], **out({"fields": list(fields)})},
            {"args": ["project", "field-create"], **out(FIELD)},
            gh_pr_list([p for p in prs if p.get("state") == "OPEN"], "--state", "open"),
            gh_pr_list([p for p in prs if p.get("state") == "MERGED"], "--state", "merged"),
            {"args": ["project", "item-list"], **out({"items": list(cards)})},
            {"args": ["project", "item-add"], "seq": [out({"id": i}) for i in new_ids]},
            {"args": ["project", "item-edit"]},
            {"args": ["project", "item-archive"]},
            {"args": ["project", "item-delete"]},
        ])

    def graphql_writes(self):
        """The mutation queries sent through `gh api graphql`, in order."""
        found = []
        for c in self.env.gh_calls():
            q = next((a[len("query="):] for a in c if a.startswith("query=mutation")), None)
            if c[:2] == ["api", "graphql"] and q:
                found.append(q)
        return found

    def deletes(self):
        return [c[c.index("--id") + 1] for c in self.env.gh_calls() if c[:2] == ["project", "item-delete"]]

    def edits(self):
        """{item id: stage name} for each item-edit call."""
        names = {v: k for k, v in OPT.items()}
        found = {}
        for c in self.env.gh_calls():
            if c[:2] == ["project", "item-edit"]:
                found[c[c.index("--id") + 1]] = names[c[c.index("--single-select-option-id") + 1]]
        return found

    def writes(self):
        calls = [c for c in self.env.gh_calls() if c[:2] in (["project", "item-edit"], ["project", "item-add"],
                                                             ["project", "item-archive"], ["project", "item-delete"])]
        return calls + self.graphql_writes()

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
        adds = [c for c in self.env.gh_calls() if c[:2] == ["project", "item-add"]]
        self.assertEqual([c[c.index("--url") + 1] for c in adds], ["https://github.com/acme/r/issues/1"])
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
        archived = [c for c in self.env.gh_calls() if c[:2] == ["project", "item-archive"]]
        self.assertEqual([c[c.index("--id") + 1] for c in archived], ["I9"])

    def test_the_stage_field_is_created_when_missing(self):
        self.config()
        self.gh(fields=[])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        create = next(c for c in self.env.gh_calls() if c[:2] == ["project", "field-create"])
        self.assertEqual(create[create.index("--single-select-options") + 1], ",".join(OPTIONS))

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
        adds = [c[c.index("--url") + 1] for c in self.env.gh_calls() if c[:2] == ["project", "item-add"]]
        self.assertEqual(adds, ["https://github.com/acme/r/issues/11", "https://github.com/acme/r/issues/20"])
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
                         ticket(32, ["ready-for-agent"])])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        adds = [c[c.index("--url") + 1] for c in self.env.gh_calls() if c[:2] == ["project", "item-add"]]
        # 31 is the lead's: it gets a card as a lead ticket (Building), never as Backlog.
        self.assertEqual(adds, ["https://github.com/acme/r/issues/31", "https://github.com/acme/r/issues/32"])

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
        self.assertFalse([c for c in self.env.gh_calls() if c[:2] == ["project", "item-archive"]])

    def test_a_card_whose_ticket_closed_without_a_pr_is_archived_but_an_open_ticket_keeps_its_card(self):
        self.config()
        self.gh(tickets=[ticket(31)], cards=[card(30, "Building"), card(31, "Building"), card(32, "Done")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        archived = [c[c.index("--id") + 1] for c in self.env.gh_calls() if c[:2] == ["project", "item-archive"]]
        self.assertEqual(archived, ["I30"])

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
        self.assertFalse([c for c in self.env.gh_calls() if c[:2] == ["project", "item-add"]])

    def test_dry_run_prints_each_planned_write_and_each_card_left_alone_and_writes_nothing(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True), ticket(10, ["ready-for-agent"])], fields=[OLD_FIELD],
                cards=[card(1, "Done"), card(7, "Backlog", repository="acme/other")],
                graphql=[out({"data": {"node": {"options": OLD_OPTIONS}}})])
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
        node = out({"data": {"node": {"options": OLD_OPTIONS}}})
        new = [{"id": "oB", "name": "Backlog"}] + [{"id": o["id"], "name": o["name"]} for o in OLD_OPTIONS]
        mutated = out({"data": {"updateProjectV2Field": {"projectV2Field": {"options": new}}}})
        self.gh(tickets=[ticket(1, claimed=True)], fields=[OLD_FIELD], cards=[card(1, "Done")], graphql=[node, mutated])
        r = self.env.run("board", check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        writes = self.graphql_writes()
        self.assertEqual(len(writes), 1)
        q = writes[0]
        self.assertIn("updateProjectV2Field", q)
        self.assertLess(q.index('name: "Backlog"'), q.index('id: "old0"'))
        for o in OLD_OPTIONS:
            self.assertIn(f'id: "{o["id"]}", name: "{o["name"]}", color: BLUE, description: "{o["description"]}"', q)
        self.assertIn("color: GRAY", q)
        # The upgraded field serves the same sync: the claimed ticket moves to Building by its kept option id.
        edit = next(c for c in self.env.gh_calls() if c[:2] == ["project", "item-edit"])
        self.assertEqual(edit[edit.index("--single-select-option-id") + 1], "old0")

    def test_a_board_that_already_matches_gets_no_field_write(self):
        self.config()
        self.gh(tickets=[ticket(1, claimed=True)], cards=[card(1, "Building")])
        self.assertEqual(self.env.run("board", check=False).returncode, 0)
        self.assertEqual(self.graphql_writes(), [])

    def test_a_failure_is_one_line_and_exit_1(self):
        self.config()
        self.gh(extra=[{"args": ["project", "view"], "exit": 1,
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
