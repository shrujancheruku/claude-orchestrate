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

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "run" / "SKILL.md"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
RISK = "> *Risk notes (review not posted).*"
SECTIONS = ["config", "tracker", "glossary", "adrs", "tickets", "roadmap", "merges"]


def iso(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def serve(self, merged=(), total=None, roadmap=None, issues=None, merged_exit=0):
        merged = list(merged)
        mbody = {"data": {"repository": {"pullRequests": {"totalCount": len(merged) if total is None else total,
                                                          "nodes": merged}}}}
        rbody = {"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": roadmap if roadmap is not None else [
            {"number": 5, "title": "Map", "url": "https://x/issues/5", "labels": {"nodes": [{"name": "wayfinder:map"}]},
             "subIssuesSummary": {"total": 4, "completed": 1}},
            {"number": 6, "title": "Parent", "url": "https://x/issues/6", "labels": {"nodes": []},
             "subIssuesSummary": {"total": 2, "completed": 2}},
            {"number": 8, "title": "Plain", "url": "https://x/issues/8", "labels": {"nodes": []},
             "subIssuesSummary": {"total": 0, "completed": 0}}]}}}}
        issues = issues or {}
        merged_resp = ({"args": ["api", "graphql", "-F", "which=merged"], "stderr": "HTTP 500", "exit": merged_exit}
                       if merged_exit else
                       {"args": ["api", "graphql", "-F", "which=merged"], "stdout": json.dumps(mbody)})
        self.env.set_gh([
            {"args": ["repo", "view"], "stdout": json.dumps({"owner": {"login": "o"}, "name": "r"})},
            merged_resp,
            {"args": ["api", "graphql", "-F", "which=roadmap"], "stdout": json.dumps(rbody)},
            {"args": ["issue", "list", "--state", "open", "--label", "needs-lead"],
             "stdout": json.dumps(issues.get("needs-lead", [{"number": 3, "title": "Q", "url": "https://x/issues/3",
                                                             "createdAt": iso(2)}]))},
            {"args": ["issue", "list", "--state", "open", "--label", "lead"],
             "stdout": json.dumps(issues.get("lead", [{"number": 4, "title": "Mine", "url": "https://x/issues/4",
                                                       "createdAt": iso(0)}]))},
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

    def test_the_roadmap_reads_every_page_of_100(self):
        self.serve()
        page = lambda nodes, more: json.dumps({"data": {"repository": {"issues": {  # noqa: E731
            "pageInfo": {"hasNextPage": more, "endCursor": "c1" if more else None}, "nodes": nodes}}}})
        item = lambda n: {"number": n, "title": f"M{n}", "url": f"https://x/issues/{n}",  # noqa: E731
                          "labels": {"nodes": [{"name": "wayfinder:map"}]}, "subIssuesSummary": {"total": 1, "completed": 0}}
        responses = json.loads(self.env.gh_state.read_text())["responses"]
        for r in responses:
            if r["args"][-1] == "which=roadmap":
                r.pop("stdout")
                r["seq"] = [{"stdout": page([item(n) for n in range(1, 101)], True)},
                            {"stdout": page([item(101)], False)}]
        self.env.set_gh(responses)
        _, out, _ = self.brief()
        self.assertIn("[map] #1 M1", out)
        self.assertIn("[map] #101 M101", out)
        calls = [c for c in self.env.gh_calls() if "which=roadmap" in c]
        self.assertEqual(len(calls), 2)
        self.assertIn("after=c1", calls[1])
        self.assertTrue(all("limit=100" in c for c in calls))

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
