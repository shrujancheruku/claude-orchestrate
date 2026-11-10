"""scripts/frontier through its CLI, with a fake `gh` serving the open issues (two pages in one test)."""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import Run, ScriptEnv, gh_repo_view  # noqa: E402


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
        return Run(self.env.run("frontier", *args, check=check))

    def test_default_prints_one_line_per_takeable_ticket_ascending(self):
        self.serve(page([node(9), node(3), node(5, claimed=True), node(6, labels=("ready-for-human",))]))
        r = self.frontier()
        self.assertEqual(r.stdout, "#3 title 3 model sonnet\n#9 title 9 model sonnet\n")
        self.assertEqual(r.stderr, "")

    def test_no_session_keeps_the_answer_and_exit_code_and_says_so_on_stderr(self):
        (self.env.repo / ".claude" / "orchestrate-state.json").unlink()
        self.serve(page([node(3)]))
        r = self.frontier()
        self.assertEqual((r.returncode, r.stdout), (0, "#3 title 3 model sonnet\n"))
        self.assertEqual(r.stderr, "runlog: no session, frontier event not written\n")

    def test_two_pages_are_read_to_exhaustion_once_each(self):
        self.serve(page([node(2), node(4)], cursor="CUR1"), page([node(1), node(3)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1 model sonnet\n#2 title 2 model sonnet\n#3 title 3 model sonnet\n#4 title 4 model sonnet\n")
        calls = [c for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("after=CUR1", calls[0])
        self.assertIn("after=CUR1", calls[1])

    def test_an_issue_repeated_across_pages_is_returned_once(self):
        # an issue created mid-read can shift a page: the last issue of page one reappears first on page two
        self.serve(page([node(1), node(2)], cursor="CUR1"), page([node(2), node(3)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1 model sonnet\n#2 title 2 model sonnet\n#3 title 3 model sonnet\n")

    def test_the_open_issues_query_pages_by_creation_oldest_first(self):
        # the query the script sent (the fake gh recorded it), not the module constant
        self.serve(page([node(1)]))
        self.frontier()
        [call] = [c for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        sent = next(a for a in call if a.startswith("query="))
        self.assertIn("CREATED_AT, direction: ASC", sent)

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
            "#1 title 1 model sonnet",
            "not-takeable:", "#2 claimed", "#3 blocked-by #8", "#4 parent", "#5 blocked-by #1 (body)", "#7 lead",
            "ready-for-human:", "#6 title 6",
            "parents-closed:", "#4 title 4",
            "drift:", "#5 body says blocked by #1, tracker does not",
        ])

    def test_prose_blocked_by_does_not_hold_a_ticket(self):
        self.serve(page([node(1, body="Blocked by the outage, see #12"), node(12, labels=())]))
        out = self.frontier("--all").stdout.splitlines()
        self.assertEqual(out[0], "#1 title 1 model sonnet")
        self.assertEqual(out[1], "not-takeable:")
        self.assertNotIn("#1 blocked-by #12 (body)", out)
        self.assertEqual(out[out.index("drift:") + 1], "none")

    def test_a_colonless_blocked_by_does_not_hold_a_ticket(self):
        self.serve(page([node(1, body="Blocked by #12"), node(12, labels=())]))
        out = self.frontier("--all").stdout.splitlines()
        self.assertEqual(out[0], "#1 title 1 model sonnet")
        self.assertNotIn("#1 blocked-by #12 (body)", out)
        self.assertEqual(out[out.index("drift:") + 1], "none")

    def test_a_self_reference_is_not_drift(self):
        self.serve(page([node(5, body="Blocked by: #5")]))
        out = self.frontier("--all").stdout.splitlines()
        self.assertEqual(out[0], "#5 title 5 model sonnet")
        self.assertEqual(out[out.index("drift:") + 1], "none")

    def test_all_says_none_for_each_empty_section(self):
        self.serve(page([node(1)]))
        self.assertEqual(self.frontier("--all").stdout.splitlines(),
                         ["#1 title 1 model sonnet", "not-takeable:", "none", "ready-for-human:", "none",
                          "parents-closed:", "none", "drift:", "none"])

    def test_parent_limits_every_section_to_its_sub_issues(self):
        self.serve(page([node(1, total=2), node(2, parent=1), node(3, parent=1, claimed=True), node(4)]))
        self.assertEqual(self.frontier("--parent", "1").stdout, "#2 title 2 model sonnet\n")
        out = self.frontier("--parent", "1", "--all").stdout.splitlines()
        self.assertEqual(out[:3], ["#2 title 2 model sonnet","not-takeable:", "#3 claimed"])
        self.assertNotIn("#4 title 4", out)

    def test_ticket_prints_its_line_or_its_reason(self):
        self.serve(page([node(1), node(2, claimed=True), node(3, labels=())]))
        self.assertEqual(self.frontier("--ticket", "1").stdout, "#1 title 1 model sonnet\n")
        self.assertEqual(self.frontier("--ticket", "2").stdout, "#2 claimed\n")
        self.assertEqual(self.frontier("--ticket", "3").stdout, "#3 not ready-for-agent\n")
        self.assertEqual(self.frontier("--ticket", "99").stdout, "#99 not open\n")

    def test_an_unreadable_triage_labels_doc_exits_2(self):
        doc = self.env.repo / "docs" / "agents" / "triage-labels.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.mkdir()  # reading a directory raises OSError other than "not found"
        self.serve(page([node(1)]))
        r = self.frontier(check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))

    def test_an_empty_frontier_exits_0_with_no_output(self):
        self.serve(page([node(1, claimed=True)]))
        r = self.frontier()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_the_label_string_comes_from_the_triage_labels_doc(self):
        doc = self.env.repo / "docs" / "agents" / "triage-labels.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("| `ready-for-agent` | `afk` | x |\n")
        self.serve(page([node(1, labels=("afk",)), node(2)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1 model sonnet\n")

    def models(self, row="sonnet; haiku for S tickets (at most 3 files)", risk="`scripts/gate`"):
        cfg = self.env.repo / ".claude" / "orchestration.md"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(f"## Models\n\n| Role | Model | Effort |\n|---|---|---|\n| Worker | {row} | medium |\n\n"
                       f"## Review\n\n- **Risk paths** (any PR touching them is reviewed as L): {risk}\n"
                       "- **Tiers:** S is at most 50 changed lines in at most 3 files, none on a risk path; "
                       "L touches a risk path or changes more than 400 lines; M is everything else.\n")

    def test_the_model_follows_the_label_the_title_and_the_config(self):
        self.models()
        small = node(2, labels=("ready-for-agent", "size:S"))
        design = dict(node(3, labels=("ready-for-agent", "size:S")), title="design the thing")
        self.serve(page([node(1), small, design, node(4, body="please Investigate first"),
                         node(5, labels=("ready-for-agent", "size:M"))]))
        self.assertEqual(self.frontier().stdout.splitlines(),
                         ["#1 title 1 model sonnet", "#2 title 2 model haiku", "#3 design the thing model sonnet",
                          "#4 title 4 model sonnet", "#5 title 5 model sonnet"])

    def test_touch_map_earns_an_unlabelled_ticket_the_small_model_unless_it_is_big_or_risky(self):
        self.models()
        for tmap, want in (("a.py, b.py", "haiku"), ("a.py, b.py, c.py, d.py", "sonnet"), ("a.py, scripts/gate", "sonnet"),
                           ("none", "haiku")):
            with self.subTest(touch_map=tmap):
                self.serve(page([node(1)]))
                self.assertEqual(self.frontier("--ticket", "1", "--touch-map", tmap).stdout, f"#1 title 1 model {want}\n")

    def test_touch_map_needs_ticket_and_a_valid_map(self):
        self.serve(page([node(1)]))
        self.assertEqual(self.frontier("--touch-map", "a.py", check=False).returncode, 2)
        self.assertEqual(self.frontier("--ticket", "1", "--touch-map", "a.py, 20", check=False).returncode, 2)

    def test_a_config_without_a_small_model_uses_the_default_for_every_ticket(self):
        self.models(row="opus")
        self.serve(page([node(1, labels=("ready-for-agent", "size:S"))]))
        self.assertEqual(self.frontier().stdout, "#1 title 1 model opus\n")

    def test_it_logs_one_frontier_event_with_the_count(self):
        self.serve(page([node(1), node(2)]))
        self.frontier()
        events = [e for e in self.env.events() if e["kind"] == "frontier"]
        self.assertEqual([(e["outcome"], e["takeable"]) for e in events], [("ran", 2)])

    def snapshot_event(self, outcome, sha=None):
        from runlog import append_event, SNAPSHOT
        append_event(self.env.repo, SNAPSHOT, outcome, ref=7, **({"sha": sha} if sha else {}))

    def test_a_standing_snapshot_stale_dispatches_nothing_and_asks_for_a_relaunch(self):
        self.snapshot_event("stale", "a" * 40)
        self.serve(page([node(1), node(2)]))
        r = self.frontier()
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("#1 ", r.stdout)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("relaunch: snapshot behind main at aaaaaaaaaaaa (PR #7)"), r.stdout)
        self.assertEqual(len(lines), 1)
        # the count the event logs is still the tracker's, so the profile stays honest
        self.assertEqual([e["takeable"] for e in self.env.events() if e["kind"] == "frontier"], [2])

    def test_a_fresh_launch_clears_snapshot_stale(self):
        self.snapshot_event("stale")
        self.snapshot_event("launched")
        self.serve(page([node(1)]))
        self.assertEqual(self.frontier().stdout, "#1 title 1 model sonnet\n")

    def test_a_merge_after_the_relaunch_stales_it_again(self):
        self.snapshot_event("stale")
        self.snapshot_event("launched")
        self.snapshot_event("stale")
        self.serve(page([node(1)]))
        self.assertTrue(self.frontier().stdout.startswith("relaunch:"))

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
        r = Run(self.env.run("frontier", check=False, no_gh=True))
        self.assertEqual((r.returncode, r.stdout, r.verdict), (2, "", "next: could-not-run"))

    def test_success_ends_next_done_even_when_nothing_is_takeable(self):
        self.serve(page([node(1, claimed=True)]))
        r = self.frontier("--all")
        self.assertEqual((r.returncode, r.verdict), (0, "next: done"))

    def test_a_program_error_ends_could_not_run(self):
        self.serve(page([node(1)]))
        r = self.frontier("--touch-map", "a.py", check=False)  # --touch-map needs --ticket
        self.assertEqual((r.returncode, r.verdict), (2, "next: could-not-run"))
        self.serve({"stdout": json.dumps({"data": {}})})
        self.assertEqual(self.frontier(check=False).verdict, "next: could-not-run")

    def test_a_rate_limit_ends_retry_after_with_the_reset_time(self):
        self.env.set_gh([gh_repo_view(), {"args": ["api", "graphql"], "exit": 1,
                                          "stderr": "gh: You have exceeded a secondary rate limit. (HTTP 403)\nretry-after: 300\n"}])
        with tempfile.TemporaryDirectory() as cache, unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            r = self.frontier(check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertRegex(r.verdict, r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


if __name__ == "__main__":
    unittest.main()
