"""scripts/board through its CLI, with a fake `gh` serving the project, the tickets and the PRs.

Also the prompt side: setup offers the board and writes the `Board:` line, and `run` syncs it in step 5.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, git  # noqa: E402

import common  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/orgs/acme/projects/7"
OPTIONS = ["Building", "In review", "Needs lead", "Waiting on you", "Done"]
FIELD = {"id": "F1", "name": "Stage", "options": [{"id": "o" + str(i), "name": n} for i, n in enumerate(OPTIONS)]}
OPT = {n: "o" + str(i) for i, n in enumerate(OPTIONS)}


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket(n, labels=(), claimed=False):
    return {"number": n, "url": f"https://github.com/acme/r/issues/{n}",
            "labels": [{"name": x} for x in labels], "assignees": [{"login": "me"}] if claimed else []}


def card(n, stage=None, item="I"):
    c = {"id": f"{item}{n}", "content": {"type": "Issue", "number": n}}
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

    def gh(self, tickets=(), prs=(), cards=(), fields=(FIELD,), extra=()):
        self.env.set_gh([
            {"args": ["project", "view"], **out({"id": "P1"})},
            {"args": ["project", "field-list"], **out({"fields": list(fields)})},
            {"args": ["project", "field-create"], **out(FIELD)},
            {"args": ["issue", "list"], **out(list(tickets))},
            {"args": ["pr", "list"], **out(list(prs))},
            {"args": ["project", "item-list"], **out({"items": list(cards)})},
            {"args": ["project", "item-add"], **out({"id": "NEW"})},
            {"args": ["project", "item-edit"]},
            {"args": ["project", "item-archive"]},
            *extra,
        ])

    def edits(self):
        """{item id: stage name} for each item-edit call."""
        names = {v: k for k, v in OPT.items()}
        found = {}
        for c in self.env.gh_calls():
            if c[:2] == ["project", "item-edit"]:
                found[c[c.index("--id") + 1]] = names[c[c.index("--single-select-option-id") + 1]]
        return found

    def writes(self):
        return [c for c in self.env.gh_calls() if c[:2] in (["project", "item-edit"], ["project", "item-add"],
                                                            ["project", "item-archive"])]

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

    def test_a_failure_is_one_line_and_exit_1(self):
        self.config()
        self.gh(extra=[])
        self.env.set_gh([{"args": ["project", "view"], "stderr": "error: your authentication token is missing the 'project' scope\nmore\n", "exit": 1}])
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
                       "lacks the `project` scope", "stays unlinked", "write the line"):
            self.assertIn(phrase, text)

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
