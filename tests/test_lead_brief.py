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
from harness import ScriptEnv  # noqa: E402

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


def pr(number, days, title=None, body="Closes #7", reviews=(), comments=(), labels=()):
    return {"number": number, "title": title or f"feat: thing {number}", "url": f"https://x/pull/{number}",
            "body": body, "mergedAt": iso(days), "labels": {"nodes": [{"name": n} for n in labels]},
            "closingIssuesReferences": {"nodes": [{"number": 7}]},
            "reviews": {"nodes": [{"url": f"https://x/pull/{number}#r1", "body": b} for b in reviews]},
            "comments": {"nodes": [{"url": f"https://x/pull/{number}#c1", "body": b} for b in comments]}}


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
            {"args": ["repo", "view"], "stdout": json.dumps({"owner": {"login": "o"}, "name": "r"})},
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
        self.assertIn(RISK, text)
        self.assertIn("scripts/lead-brief", text)
        for rule in ("7 days", "at most 20"):
            self.assertNotIn(rule, text, "run calls lead-brief and does not restate its fallback rule (ADR-0006)")
        self.assertIn("end-of-run summary", text)


if __name__ == "__main__":
    unittest.main()
