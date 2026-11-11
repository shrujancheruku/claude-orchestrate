"""scripts/lead-brief through its CLI, with a fake `gh` and a fake clock (--now).

Also the prompt side: `run` posts the risk-note fallback comment and calls lead-brief rather than restating its
rule (ADR-0006), and the lead's start-up is documented.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_repo_rest  # noqa: E402

# The queries are matched by their text, so the fake gh sees the call exactly as GitHub would.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from importlib.machinery import SourceFileLoader  # noqa: E402
from importlib.util import module_from_spec, spec_from_loader  # noqa: E402

_loader = SourceFileLoader("lead_brief", str(Path(__file__).resolve().parent.parent / "scripts" / "lead-brief"))
lead_brief = module_from_spec(spec_from_loader("lead_brief", _loader))
_loader.exec_module(lead_brief)

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "run" / "SKILL.md"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
RISK = "> *Risk notes (review not posted).*"
SECTIONS = ["config", "tracker", "glossary", "adrs", "tickets", "roadmap", "merges"]


def iso(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_node(number, title, labels=(), subs=(0, 0), created=0):
    """One raw node of the open-issues snapshot (github.ISSUES_QUERY): what section_tickets and section_roadmap read."""
    return {"number": number, "title": title, "url": f"https://x/issues/{number}", "createdAt": iso(created),
            "labels": {"nodes": [{"name": n} for n in labels]},
            "subIssuesSummary": {"total": subs[0], "completed": subs[1]}}


def issues_page(nodes, more):
    return json.dumps({"data": {"repository": {"issues": {
        "pageInfo": {"hasNextPage": more, "endCursor": "c1" if more else None}, "nodes": nodes}}}})


def pr(number, days, title=None, body="Closes #7", reviews=(), comments=(), labels=(), association="COLLABORATOR"):
    return {"number": number, "title": title or f"feat: thing {number}", "url": f"https://x/pull/{number}",
            "body": body, "mergedAt": iso(days), "labels": {"nodes": [{"name": n} for n in labels]},
            "closingIssuesReferences": {"nodes": [{"number": 7}]},
            "reviews": {"nodes": [{"url": f"https://x/pull/{number}#r1", "body": b, "authorAssociation": association,
                                   "author": {"login": "dev"}} for b in reviews]},
            "comments": {"nodes": [{"url": f"https://x/pull/{number}#c1", "body": b, "authorAssociation": association,
                                    "author": {"login": "dev"}} for b in comments]}}


L_REVIEW = ("Round 1 · verdict: approve · tier: L\n\nspec ok\n\n## Discovered work\n- a bug\n\n"
            "scripts/merge:10-20 — the rebase path\n- hooks/guard.py:3 — the lexer")


class LeadBrief(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        r = self.env.repo
        (r / ".claude").mkdir(exist_ok=True)
        (r / ".claude" / "orchestration.md").write_text("CONFIG TEXT\n")
        (r / "docs" / "agents").mkdir(parents=True)
        (r / "docs" / "agents" / "issue-tracker.md").write_text("TRACKER TEXT\n")
        (r / "GLOSSARY.md").write_text("GLOSSARY TEXT\nline two\n")
        (r / "docs" / "adr").mkdir()
        (r / "docs" / "adr" / "0001-slot-count.md").write_text("# Slot count\n\nStatus: accepted\n")
        (r / "docs" / "adr" / "0002-other.md").write_text("# Other thing\n")

    def serve(self, merged=(), total=None, pages=None, merged_exit=0, issues_exit=0):
        """`pages` is the open-issues snapshot as a list of pages of raw nodes; the default is one page of five."""
        merged = list(merged)
        mbody = {"data": {"repository": {"pullRequests": {"totalCount": len(merged) if total is None else total,
                                                          "nodes": merged}}}}
        if pages is None:
            pages = [[issue_node(3, "Q", ["needs-lead"], created=2), issue_node(4, "Mine", ["lead"]),
                      issue_node(5, "Map", ["wayfinder:map"], subs=(4, 1)),
                      issue_node(6, "Parent", subs=(2, 2)), issue_node(8, "Plain")]]
        issues_resp = ({"args": ["api", "graphql", "-f", "query=" + lead_brief.github.ISSUES_QUERY],
                        "stderr": "HTTP 500", "exit": issues_exit}
                       if issues_exit else
                       {"args": ["api", "graphql", "-f", "query=" + lead_brief.github.ISSUES_QUERY],
                        "seq": [{"stdout": issues_page(p, more=i < len(pages) - 1)} for i, p in enumerate(pages)]})
        merged_resp = ({"args": ["api", "graphql", "-f", "query=" + lead_brief.MERGED_QUERY], "stderr": "HTTP 500", "exit": merged_exit}
                       if merged_exit else
                       {"args": ["api", "graphql", "-f", "query=" + lead_brief.MERGED_QUERY], "stdout": json.dumps(mbody)})
        self.env.set_gh([
            gh_repo_rest({"owner": {"login": "o"}, "name": "r"}),
            merged_resp,
            issues_resp,
        ])

    def brief(self, *args, check=True):
        r = self.env.run("lead-brief", "--now", iso(0), *args, check=check)
        return r.returncode, r.stdout, r.stderr

    def watermark(self):
        return json.loads((self.env.repo / ".claude" / "orchestrate-lead.json").read_text())

    def test_sections_print_in_the_fixed_order(self):
        self.serve([pr(10, 1)])
        _, out, _ = self.brief()
        heads = [line[3:-3] for line in out.splitlines() if line.startswith("== ")]
        self.assertEqual(heads, SECTIONS)
        for text in ("CONFIG TEXT", "TRACKER TEXT", "GLOSSARY TEXT\nline two"):
            self.assertIn(text, out)
        self.assertIn("0001 Slot count [accepted]", out)
        self.assertIn("0002 Other thing [status not stated]", out)
        self.assertIn("[needs-lead] #3 Q https://x/issues/3 (2d)", out)
        self.assertLess(out.index("[needs-lead]"), out.index("[lead] #4"))

    def test_roadmap_lists_maps_and_parents_with_counts_one_line_each(self):
        self.serve()
        _, out, _ = self.brief()
        self.assertIn("[map] #5 Map https://x/issues/5 — 3 open, 1 closed", out)
        self.assertIn("[parent] #6 Parent https://x/issues/6 — 0 open, 2 closed", out)
        self.assertNotIn("Plain", out)

    def test_the_snapshot_is_read_in_pages_once_and_shared_by_tickets_and_roadmap(self):
        first = [issue_node(n, f"M{n}", ["wayfinder:map"], subs=(1, 0)) for n in range(1, 101)]
        self.serve(pages=[first, [issue_node(101, "M101", ["wayfinder:map"], subs=(1, 0)),
                                  issue_node(102, "Late", ["needs-lead"])]])
        _, out, _ = self.brief()
        self.assertIn("[map] #1 M1", out)
        self.assertIn("[map] #101 M101", out)
        self.assertIn("[needs-lead] #102 Late", out)
        calls = [c for c in self.env.gh_calls() if "query=" + lead_brief.github.ISSUES_QUERY in c]
        self.assertEqual(len(calls), 2)  # one read of every page, not one per section
        self.assertIn("after=c1", calls[1])

    def test_lead_brief_carries_no_roadmap_query_of_its_own(self):
        self.assertFalse(hasattr(lead_brief, "ROADMAP_QUERY"))
        self.assertNotIn("ROADMAP", (ROOT / "scripts" / "lead-brief").read_text())

    def test_a_failing_snapshot_leaves_tickets_and_roadmap_unavailable_and_the_rest_prints(self):
        self.serve(issues_exit=1)
        _, out, _ = self.brief()
        self.assertIn("== tickets ==\nunavailable: gh failed: HTTP 500", out)
        self.assertIn("== roadmap ==\nunavailable: gh failed: HTTP 500", out)
        self.assertIn("== merges ==", out)
        self.assertIn("CONFIG TEXT", out)

    def test_an_l_review_without_note_lines_still_shows_tier_l_and_says_none_found(self):
        self.serve([pr(10, 1, reviews=["Round 1 · verdict: approve · tier: L\n\nok"])])
        _, out, _ = self.brief()
        self.assertIn("tier L", out)
        self.assertIn("none found in the review or the fallback comment", out)

    def test_a_round_two_l_review_without_notes_keeps_round_ones_notes(self):
        self.serve([pr(10, 1, reviews=[L_REVIEW, "Round 2 · verdict: approve · tier: L\n\nfine"])])
        _, out, _ = self.brief()
        self.assertIn("scripts/merge:10-20 — the rebase path", out)

    def test_an_l_merge_prints_risk_notes_from_the_posted_review_word_for_word(self):
        self.serve([pr(10, 1, body="Closes #7\n\n## Discovered work\n- one\n", reviews=[L_REVIEW])])
        _, out, _ = self.brief()
        self.assertIn("feat: thing 10 https://x/pull/10 · #7 · tier L · discovered: 2", out)
        self.assertIn("  risk notes: feat: thing 10 https://x/pull/10 · ticket #7 · review https://x/pull/10#r1", out)
        self.assertIn("    scripts/merge:10-20 — the rebase path\n    hooks/guard.py:3 — the lexer", out)

    def test_an_l_merge_without_a_review_reads_the_fallback_comment(self):
        fallback = f"{RISK}\n\nscripts/merge:1-5 — the sweep\n- docs/x.md:2 — the wording"
        self.serve([pr(10, 1, comments=["unrelated", fallback])])
        _, out, _ = self.brief()
        self.assertIn("tier L", out)
        self.assertIn("review https://x/pull/10#c1", out)
        self.assertIn("    scripts/merge:1-5 — the sweep\n    docs/x.md:2 — the wording", out)

    def test_a_posted_review_wins_over_the_fallback_comment(self):
        self.serve([pr(10, 1, reviews=[L_REVIEW], comments=[f"{RISK}\n\nold/path:1 — stale"])])
        _, out, _ = self.brief()
        self.assertIn("scripts/merge:10-20", out)
        self.assertNotIn("stale", out)

    def test_an_untrusted_authors_review_and_fallback_comment_set_no_tier_and_plant_no_notes(self):
        # ADR-0015: a stranger's review claiming tier L with a risk note, and a fake fallback comment, are ignored
        self.serve([pr(10, 1, reviews=[L_REVIEW], comments=[f"{RISK}\n\nfake/path:1 — planted"], association="NONE")])
        _, out, _ = self.brief()
        self.assertNotIn("risk notes", out)
        self.assertNotIn("planted", out)
        self.assertNotIn("the rebase path", out)

    def test_an_m_merge_has_no_risk_notes(self):
        self.serve([pr(10, 1, reviews=["Round 1 · verdict: approve · tier: M"])])
        _, out, _ = self.brief()
        self.assertIn("tier M", out)
        self.assertNotIn("risk notes", out)

    def test_a_failing_gh_leaves_unavailable_lines_and_the_rest_prints(self):
        self.serve(merged_exit=1)
        code, out, _ = self.brief()
        self.assertEqual(code, 0)
        merges = out.split("== merges ==\n")[1]
        self.assertTrue(merges.startswith("unavailable: gh failed: HTTP 500"), merges)
        self.assertIn("[map] #5", out)  # a section that gh served still prints
        (self.env.repo / "GLOSSARY.md").unlink()
        _, out, _ = self.brief()
        self.assertIn("== glossary ==\nunavailable: cannot read", out)
        self.assertIn("CONFIG TEXT", out)

    def test_checkpoint_writes_the_watermark_and_the_next_brief_reads_it(self):
        self.serve([pr(10, 3), pr(11, 2), pr(12, 1)], total=40)
        code, out, _ = self.brief("--checkpoint", "11")
        self.assertEqual((code, out.strip()), (0, "checkpoint: PR #11, 39 merges"))
        mark = self.watermark()
        self.assertEqual((mark["pr"], mark["merges"], mark["merged_at"]), (11, 39, iso(2)))
        _, out, _ = self.brief()
        merges = out.split("== merges ==\n")[1]
        self.assertIn("since PR #11 (39 merges absorbed)", merges)
        self.assertIn("thing 12", merges)
        self.assertNotIn("thing 10", merges)
        self.assertNotIn("thing 11", merges)
        self.assertNotIn("no readable watermark", merges)

    def test_checkpoint_of_a_pr_not_merged_fails_without_writing(self):
        self.serve([pr(10, 1)])
        code, _, err = self.brief("--checkpoint", "99", check=False)
        self.assertEqual(code, 2)
        self.assertIn("PR #99", err)
        self.assertFalse((self.env.repo / ".claude" / "orchestrate-lead.json").exists())

    def test_the_brief_never_writes_the_watermark(self):
        self.serve([pr(10, 1)])
        self.brief()
        self.assertFalse((self.env.repo / ".claude" / "orchestrate-lead.json").exists())

    def test_a_missing_watermark_falls_back_to_seven_days_capped_at_twenty(self):
        old = pr(1, 8, title="feat: too old")
        recent = [pr(100 + i, 6 - i / 10) for i in range(25)]
        self.serve([old, *recent])
        _, out, _ = self.brief()
        merges = out.split("== merges ==\n")[1]
        self.assertEqual(merges.splitlines()[0], "no readable watermark: showing merges from the last 7 days, at most 20")
        self.assertNotIn("too old", merges)
        shown = [line for line in merges.splitlines() if " · #7 · " in line]
        self.assertEqual(len(shown), 20)
        self.assertIn("thing 124", shown[-1])  # the newest 20 survive the cap
        self.assertNotIn("thing 104", merges)

    def test_an_unreadable_watermark_falls_back_the_same_way(self):
        self.serve([pr(10, 1)])
        (self.env.repo / ".claude" / "orchestrate-lead.json").write_text("{not json")
        _, out, _ = self.brief()
        self.assertIn("no readable watermark", out)
        self.assertIn("thing 10", out)

    def test_run_posts_the_fallback_comment_and_calls_lead_brief(self):
        text = SKILL.read_text()
        self.assertNotIn(RISK, text, "the marker lives in tracker.py; no prompt restates it (#661)")
        self.assertIn("--post-risk-notes", text)
        self.assertIn("scripts/lead-brief", text)
        for rule in ("7 days", "at most 20"):
            self.assertNotIn(rule, text, "run calls lead-brief and does not restate its fallback rule (ADR-0006)")
        self.assertIn("end-of-run summary", text)


class Watch(unittest.TestCase):
    """`lead-brief --watch`: the baseline, each hit kind, the poll interval and the rate limit, with a fake snapshot,
    clock and sleep (run_watch's seams) and, for the CLI, a fake gh."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def watch(self, reads, interval=300, slots=None):
        """Run the watch over `reads` (lists of raw nodes, or an exception to raise); returns (code, stdout, sleeps).
        `slots` is run_watch's seam: a callable returning (building, cap, dispatchable tickets)."""
        reads, sleeps, now = list(reads), [], [0.0]

        def read():
            r = reads.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = lead_brief.run_watch(self.env.repo, interval, read=read, clock=lambda: now[0], sleep=sleep, slots=slots)
        return code, out.getvalue(), sleeps

    BASE = [issue_node(3, "Q", ["needs-lead"]), issue_node(8, "Plain")]
    FRESH = [*BASE, issue_node(9, "Fresh", ["lead"])]

    def test_an_idle_slot_with_a_dispatchable_ticket_past_the_window_prints_the_line_and_exits(self):
        # polls at 300, 600, 900, 1200 s; the slot is seen idle from 300 s, so the 10 minute window is passed at 1200 s
        code, out, _ = self.watch([self.BASE] * 5, slots=lambda: (1, 4, [12, 13]))
        self.assertEqual(code, 0)
        self.assertEqual(out, "idle-slot: 1/4 building; dispatchable: #12 #13 (15m)\nnext: settled\n")

    def test_a_slot_idle_for_less_than_the_window_prints_nothing(self):
        code, out, _ = self.watch([self.BASE, self.BASE, self.BASE, self.FRESH], slots=lambda: (1, 4, [12]))
        self.assertEqual(out, "new: #9 Fresh [lead]\nnext: settled\n")  # 300 s and 600 s idle: not past 600 s

    def test_a_hold_or_a_full_set_of_slots_prints_nothing(self):
        held = self.watch([self.BASE] * 4 + [self.FRESH], slots=lambda: (1, 4, []))  # frontier lists nothing no hold blocks
        full = self.watch([self.BASE] * 4 + [self.FRESH], slots=lambda: (4, 4, [12]))
        for _, out, _ in (held, full):
            self.assertEqual(out, "new: #9 Fresh [lead]\nnext: settled\n")

    def test_the_idle_clock_restarts_when_the_slot_fills(self):
        # idle at 300 s and 600 s, full at 900 s, idle again from 1200 s: no stretch passes the 600 s window on its own
        seen = iter([(1, 4, [12]), (1, 4, [12]), (4, 4, []), (1, 4, [12]), (1, 4, [12]), (1, 4, [12])])
        _, out, _ = self.watch([self.BASE] * 5 + [self.FRESH], slots=lambda: next(seen))
        self.assertEqual(out, "new: #9 Fresh [lead]\nnext: settled\n")

    def test_velocity_watch_off_skips_the_idle_slot_check(self):
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text("## Velocity\n\n- **Velocity watch:** off\n")
        _, out, _ = self.watch([self.BASE] * 5 + [self.FRESH], slots=lambda: (0, 4, [12]))
        self.assertEqual(out, "new: #9 Fresh [lead]\nnext: settled\n")

    def test_the_configured_idle_window_sets_how_long_a_slot_may_idle(self):
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text("## Velocity\n\n- **Idle window:** 2m\n")
        _, out, _ = self.watch([self.BASE] * 3, slots=lambda: (1, 4, [12]))  # idle from 300 s; 600 s is past 120 s
        self.assertEqual(out, "idle-slot: 1/4 building; dispatchable: #12 (5m)\nnext: settled\n")

    def test_a_new_issue_ends_the_watch_with_its_number_title_and_labels(self):
        code, out, _ = self.watch([self.BASE, self.BASE, [*self.BASE, issue_node(9, "Fresh", ["needs-triage", "lead"])]])
        self.assertEqual(code, 0)
        self.assertEqual(out, "new: #9 Fresh [lead, needs-triage]\nnext: settled\n")

    def test_a_ticket_that_gains_needs_lead_after_start_is_a_hit_and_one_already_there_is_not(self):
        grown = [issue_node(3, "Q", ["needs-lead"]), issue_node(8, "Plain", ["needs-lead"])]
        code, out, _ = self.watch([self.BASE, self.BASE, grown])
        self.assertEqual((code, out), (0, "needs-lead: #8 Plain\nnext: settled\n"))

    def test_a_takeable_ticket_whose_blocked_by_line_names_only_closed_issues_is_stale(self):
        body = "Blocked by: #4, #5\n"
        stale = dict(issue_node(8, "Plain", ["ready-for-agent"]), body=body)
        code, out, _ = self.watch([self.BASE, [self.BASE[0], stale]])
        self.assertEqual((code, out), (0, "stale: #8 Plain blocked-by #4 #5\nnext: settled\n"))

    def test_a_blocker_still_open_or_a_claimed_ticket_is_not_stale(self):
        held = dict(issue_node(6, "Held", ["ready-for-agent"]), body="Blocked by: #3\n")
        claimed = dict(issue_node(7, "Claimed", ["ready-for-agent"]), body="Blocked by: #4\n",
                       assignees={"totalCount": 1})
        # the first poll has no hit, so the watch polls again and only the second read ends it
        quiet = [self.BASE[0], held, claimed]
        code, out, sleeps = self.watch([self.BASE, quiet, [*quiet, issue_node(10, "N")]])
        self.assertEqual(out, "new: #10 N []\nnext: settled\n")
        self.assertEqual(len(sleeps), 2)

    def test_reads_are_never_closer_than_the_interval(self):
        # a slow read (the clock moves 100s inside it) shortens the next sleep, never below the interval in total
        now, sleeps, calls = [0.0], [], [0]

        def read():
            calls[0] += 1
            now[0] += 100
            return self.BASE if calls[0] < 4 else [*self.BASE, issue_node(9, "N")]

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            lead_brief.run_watch(self.env.repo, 300, read=read, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(sleeps, [300, 200, 200])

    def test_a_rate_limit_ends_the_watch_with_retry_after(self):
        err = lead_brief.github.GhError(lead_brief.github.RATELIMITED, "rate limit until 2026-11-10T13:00:00Z")
        code, out, _ = self.watch([self.BASE, err])
        self.assertEqual((code, out), (2, "next: retry-after 2026-11-10T13:00:00Z\n"))

    def test_another_failure_is_could_not_run(self):
        code, out, _ = self.watch([self.BASE, lead_brief.Unavailable("gh failed: HTTP 500")])
        self.assertEqual((code, out), (2, "next: could-not-run\n"))

    def test_the_cli_watches_through_the_issues_snapshot_and_writes_nothing_to_the_tracker(self):
        pages = [{"stdout": issues_page(self.BASE, more=False)},
                 {"stdout": issues_page([*self.BASE, issue_node(9, "Fresh", ["lead"])], more=False)}]
        self.env.set_gh([gh_repo_rest({"owner": {"login": "o"}, "name": "r"}),
                         {"args": ["api", "graphql", "-f", "query=" + lead_brief.github.ISSUES_QUERY], "seq": pages}])
        r = self.env.run("lead-brief", "--watch", "--interval", "0")
        self.assertEqual(r.stdout, "new: #9 Fresh [lead]\nnext: settled\n")
        for call in self.env.gh_calls():
            self.assertNotIn("POST", call)
            self.assertNotIn("PATCH", call)


class Velocity(unittest.TestCase):
    """The pass digest's slot read (`--velocity`), the Velocity config defaults and the `Lands in:` reader."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.real = lead_brief.velocity
        self.addCleanup(setattr, lead_brief, "velocity", self.real)

    def digest(self, holders, config=None):
        lead_brief.velocity = lambda root, cfg=None: (1, 4, 2, [], holders)
        if config is not None:
            (self.env.repo / ".claude").mkdir(exist_ok=True)
            (self.env.repo / ".claude" / "orchestration.md").write_text(config)
        return lead_brief.section_velocity(self.env.repo)

    def test_the_digest_carries_slot_use_and_a_held_pr_at_the_threshold_but_not_one_ticket_below(self):
        self.assertEqual(self.digest({12: [5, 6, 7]}),
                         ["slots: 1/4 building, 2 parked", "expensive-hold: PR #12 holds #5 #6 #7"])
        self.assertEqual(self.digest({12: [5, 6]}), ["slots: 1/4 building, 2 parked"])

    def test_the_threshold_is_the_configs(self):
        two = "## Velocity\n\n- **Expensive hold:** 2\n"
        self.assertEqual(self.digest({12: [5, 6], 13: [8]}, two)[1:], ["expensive-hold: PR #12 holds #5 #6"])

    def test_config_defaults_when_the_section_is_missing_and_the_values_when_it_is_there(self):
        import config
        missing = config.Config.parse("## Slots\n\n- **Slots:** 2.\n")
        self.assertEqual((missing.idle_window, missing.hold_threshold, missing.velocity_watch), (10, 3, True))
        template = config.Config.parse((ROOT / "skills" / "setup" / "config-template.md").read_text())
        self.assertEqual((template.idle_window, template.hold_threshold, template.velocity_watch), (10, 3, True))
        tuned = config.Config.parse("## Velocity\n\n- **Idle window:** 20m\n- **Expensive hold:** 5\n- **Velocity watch:** off\n")
        self.assertEqual((tuned.idle_window, tuned.hold_threshold, tuned.velocity_watch), (20, 5, False))
        junk = config.Config.parse("## Velocity\n\n- **Idle window:** soon\n- **Velocity watch:** maybe\n")
        self.assertEqual((junk.idle_window, junk.velocity_watch), (10, True))

    def test_lands_in_names_the_paths_a_ticket_declares(self):
        import holds
        body = "## What\n\nx\n\nLands in: `scripts/merge`, `scripts/claims`, plus their tests, `docs/guide.md`.\n\nSource: y `no/such`"
        self.assertEqual(holds.ticket_paths(body), ["scripts/merge", "scripts/claims", "docs/guide.md"])
        self.assertEqual(holds.ticket_paths("**Lands in:** `a.py`\n`b.py`\n\n`c.py`"), ["a.py", "b.py"])
        self.assertEqual(holds.ticket_paths("nothing declared"), [])
        template = "## Lands in\n\n- Module: `scripts/merge` (the verdict)\n- Docs: `docs/guide.md`\n\n## Notes\n`x/y.py`"
        self.assertEqual(holds.ticket_paths(template), ["scripts/merge", "docs/guide.md"])

    def test_an_unreadable_state_file_ends_the_watch_could_not_run_with_a_next_line(self):
        def boom():
            raise OSError("state file unreadable")
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = lead_brief.run_watch(self.env.repo, 0, read=lambda: [], clock=lambda: 0.0, sleep=lambda s: None, slots=boom)
        self.assertEqual((code, out.getvalue()), (2, "next: could-not-run\n"))


class LeadPassReports(unittest.TestCase):
    """The prompt side (ADR-0006: the wording, not a restated script rule)."""

    def test_run_sends_the_report_and_the_asks_to_the_lead(self):
        step = next(l for l in SKILL.read_text().splitlines() if l.startswith("5. **Report and yield.**"))
        for text in ("With `Lead: live`, after printing the report, SendMessage it to the session named `lead`",
                     "`parents-closed:`", "`ready-for-human:`", "`drift:`", "the URL of every ticket you filed",
                     "A held or refused delivery is one line"):
            self.assertIn(text, step)

    def test_the_lead_starts_and_rearms_the_watch_and_grooms_every_digest(self):
        text = (ROOT / "skills" / "lead" / "SKILL.md").read_text()
        self.assertIn("lead-brief\" --repo . --watch", text)
        self.assertIn("then start it again", text)
        self.assertIn("On every pass digest, scan the takeable backlog", text)
        self.assertNotIn("Every few passes", text)
        self.assertNotIn("After every pass table", text)

    def test_the_skills_apply_the_words_and_restate_no_rule(self):
        lead = (ROOT / "skills" / "lead" / "SKILL.md").read_text()
        run = SKILL.read_text()
        self.assertIn("`idle-slot:` line goes to the orchestrator as a nudge", lead)
        self.assertIn("lead-brief\" --repo . --velocity", lead)
        self.assertIn("each `expensive-hold:` line under your asks for the developer", lead)
        self.assertIn("`frontier`: step 7, then run steps 3 and 4 in this same turn", run)
        self.assertIn("scripts/claims\" --slots", run)
        for number in ("10m", "idle window"):  # the thresholds live in the config and the script, not in the prompts
            self.assertNotIn(number, lead)

    def test_the_guide_describes_both(self):
        guide = (ROOT / "docs" / "guide.md").read_text()
        self.assertIn("lead-brief --watch", guide)
        self.assertIn("sends the lead each pass report", guide)


if __name__ == "__main__":
    unittest.main()
