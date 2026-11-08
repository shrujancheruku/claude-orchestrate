"""scripts/github.py reads and writes GitHub through REST (`gh api`), not `gh pr|issue|repo` subcommands (#449).

Each converted function is run against a fake REST reply; the asserts are on the endpoint it asked for and on the
value it returns in the shape its callers already read.
"""

import json
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import github  # noqa: E402

PULLS = "repos/{owner}/{repo}/pulls"


def pull(n=1, ref="b", sha="s", base="main", draft=False, fork=False, merged_at=None, state="open", **extra):
    return dict({"number": n, "state": state, "merged_at": merged_at, "draft": draft, "title": "t", "body": "b",
                 "head": {"ref": ref, "sha": sha, "repo": {"full_name": "x/r" if fork else "o/r", "fork": fork}},
                 "base": {"ref": base, "repo": {"full_name": "o/r"}}}, **extra)


class Rest(unittest.TestCase):
    def gh(self, routes):
        """Patch `gh`: `routes` maps an endpoint path (query string included) to a JSON-able reply or a str body."""
        calls = []

        def fake(*cmd, **kw):
            calls.append(list(cmd[1:]))
            args = [a for a in cmd[1:] if a != "--include"]
            path = next((a for a in args[1:] if a.startswith(("repos/", "graphql"))), "")
            body = routes[path] if path in routes else routes.get("*")
            if body is None:
                return common.Run(1, "", f"no route {path}")
            return common.Run(0, body if isinstance(body, str) else json.dumps(body), "")

        patcher = unittest.mock.patch.object(common, "run", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = unittest.mock.patch.dict("os.environ", {"ORCHESTRATE_GH_LOG": "off"})
        env.start()
        self.addCleanup(env.stop)
        return calls

    def paths(self, calls):
        return [next(a for a in c[1:] if a.startswith("repos/")) for c in calls]

    # --- repo ---

    def test_repo_info_and_default_branch_are_one_get_each(self):
        calls = self.gh({"repos/{owner}/{repo}": {"owner": {"login": "o"}, "name": "r", "default_branch": "trunk"}})
        self.assertEqual(github.repo_info(), {"owner": {"login": "o"}, "name": "r", "defaultBranchRef": {"name": "trunk"}})
        self.assertEqual(github.default_branch(), "trunk")
        self.assertEqual([c[:2] for c in calls], [["api", "--include"]] * 2)

    def test_a_repo_without_a_default_branch_is_malformed(self):
        self.gh({"repos/{owner}/{repo}": {"name": "r"}})
        with self.assertRaises(github.GhError) as e:
            github.default_branch()
        self.assertEqual(e.exception.kind, github.MALFORMED)

    # --- pull lists ---

    def test_open_prs_reads_the_base_and_translates_rows(self):
        path = f"{PULLS}?state=open&base=main&per_page=100&page=1"
        self.gh({path: [pull(5, "b5", "sha5", draft=True), pull(6, "b6", fork=True)]})
        rows = github.open_prs("main")
        self.assertEqual([(r["number"], r["headRefName"], r["isDraft"], r["isCrossRepository"]) for r in rows],
                         [(5, "b5", True, False), (6, "b6", False, True)])

    def test_open_prs_follows_pages_until_a_short_one(self):
        first = [pull(n) for n in range(100)]
        calls = self.gh({f"{PULLS}?state=open&base=main&per_page=100&page=1": first,
                         f"{PULLS}?state=open&base=main&per_page=100&page=2": [pull(100)]})
        self.assertEqual(len(github.open_prs("main")), 101)
        self.assertEqual(len(calls), 2)

    def test_open_prs_with_heads_has_sha_and_base(self):
        self.gh({f"{PULLS}?state=open&per_page=100&page=1": [pull(7, "b7", "abc", base="dev")]})
        [row] = github.open_prs_with_heads()
        self.assertEqual((row["number"], row["headRefName"], row["headRefOid"], row["baseRefName"], row["isDraft"]),
                         (7, "b7", "abc", "dev", False))

    def test_merged_prs_keeps_only_rows_that_merged(self):
        path = f"{PULLS}?state=closed&base=main&sort=updated&direction=desc&per_page=100&page=1"
        calls = self.gh({path: [pull(1, state="closed", merged_at="2026-01-02T00:00:00Z"), pull(2, state="closed")]})
        rows = github.merged_prs("main")
        self.assertEqual([(r["number"], r["mergedAt"]) for r in rows], [(1, "2026-01-02T00:00:00Z")])
        self.assertEqual(self.paths(calls), [path])

    def test_merged_prs_without_a_base_reads_any_base_and_stops_at_the_limit(self):
        full = [pull(n, state="closed", merged_at="2026-01-02T00:00:00Z") for n in range(100)]
        calls = self.gh({"*": full})
        self.assertEqual(len(github.merged_prs(None, limit=100)), 100)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("base=", self.paths(calls)[0])

    def test_pr_files_pages_and_returns_paths(self):
        first = [{"filename": f"f{n}"} for n in range(100)]
        self.gh({f"{PULLS}/9/files?per_page=100&page=1": first, f"{PULLS}/9/files?per_page=100&page=2": [{"filename": "last"}]})
        files = github.pr_files("#9")
        self.assertEqual((len(files), files[-1]), (101, "last"))

    # --- one PR ---

    def test_pr_view_maps_rest_to_the_names_and_values_gh_used(self):
        self.gh({f"{PULLS}/3": pull(3, "br", "hd", base="dev", fork=True, merged_at="2026-02-02T00:00:00Z", state="closed",
                                    mergeable=True, mergeable_state="clean")})
        v = github.pr_view(3, "title,body,state,mergeable,mergeStateStatus,baseRefName,headRefName,headRefOid,"
                              "isCrossRepository,mergedAt")
        self.assertEqual(v, {"title": "t", "body": "b", "state": "MERGED", "mergeable": "MERGEABLE",
                             "mergeStateStatus": "CLEAN", "baseRefName": "dev", "headRefName": "br", "headRefOid": "hd",
                             "isCrossRepository": True, "mergedAt": "2026-02-02T00:00:00Z"})

    def test_state_open_closed_merged(self):
        for p, want in ((pull(state="open"), "OPEN"), (pull(state="closed"), "CLOSED"),
                        (pull(state="closed", merged_at="2026-01-01T00:00:00Z"), "MERGED")):
            with self.subTest(want=want):
                self.gh({f"{PULLS}/3": p})
                self.assertEqual(github.pr_view(3, "state"), {"state": want})

    def test_mergeable_maps_true_false_and_null(self):
        # gate branches on MERGEABLE, CONFLICTING and UNKNOWN.
        for raw, want in ((True, "MERGEABLE"), (False, "CONFLICTING"), (None, "UNKNOWN")):
            with self.subTest(raw=raw):
                self.gh({f"{PULLS}/3": pull(mergeable=raw)})
                self.assertEqual(github.pr_view(3, "mergeable"), {"mergeable": want})

    def test_every_mergeable_state_maps_to_a_merge_state_status(self):
        # merge branches on DIRTY; the rest are GitHub's documented values, each mapped, none dropped.
        table = {"clean": "CLEAN", "dirty": "DIRTY", "unstable": "UNSTABLE", "blocked": "BLOCKED", "behind": "BEHIND",
                 "draft": "DRAFT", "has_hooks": "HAS_HOOKS", "unknown": "UNKNOWN"}
        self.assertEqual(github.MERGE_STATE, table)
        for raw, want in table.items():
            with self.subTest(raw=raw):
                self.gh({f"{PULLS}/3": pull(mergeable_state=raw)})
                self.assertEqual(github.pr_view(3, "mergeStateStatus"), {"mergeStateStatus": want})

    def test_a_missing_or_new_mergeable_state_is_unknown(self):
        for extra in ({}, {"mergeable_state": None}, {"mergeable_state": "brand_new"}):
            with self.subTest(extra=extra):
                self.gh({f"{PULLS}/3": pull(**extra)})
                self.assertEqual(github.pr_view(3, "mergeStateStatus"), {"mergeStateStatus": "UNKNOWN"})

    def test_a_deleted_fork_is_cross_repository(self):
        p = pull()
        p["head"]["repo"] = None
        self.gh({f"{PULLS}/3": p})
        self.assertEqual(github.pr_view(3, "isCrossRepository"), {"isCrossRepository": True})

    def test_an_unknown_field_is_a_programming_error(self):
        self.gh({"*": pull()})
        with self.assertRaises(ValueError):
            github.pr_view(3, "files")

    def test_closing_issues_and_checks_come_from_one_explicit_graphql_query(self):
        node = {"state": "OPEN", "headRefName": "b", "headRefOid": "h", "isCrossRepository": False,
                "closingIssuesReferences": {"nodes": [{"number": 5}]},
                "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": [
                    {"__typename": "CheckRun", "name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"},
                    {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS"}]}}}}]}}
        calls = self.gh({"graphql": {"data": {"repository": {"pullRequest": node}}}})
        v = github.pr_view("#7", "state,headRefName,closingIssuesReferences,statusCheckRollup")
        self.assertEqual(v["closingIssuesReferences"], [{"number": 5}])
        self.assertEqual(github.ci_verdict(v["statusCheckRollup"]), ("pass", []))
        self.assertEqual(v["state"], "OPEN")
        self.assertEqual(len(calls), 1)
        self.assertIn("number=7", calls[0])
        self.assertTrue(github.describe(calls[0][1:])["graphql"])  # explicit `gh api graphql`: logged and header-read

    def test_a_pr_with_no_rollup_has_an_empty_check_list(self):
        node = {"state": "OPEN", "closingIssuesReferences": {"nodes": []}, "commits": {"nodes": []}}
        self.gh({"graphql": {"data": {"repository": {"pullRequest": node}}}})
        self.assertEqual(github.pr_view(7, "closingIssuesReferences,statusCheckRollup"),
                         {"closingIssuesReferences": [], "statusCheckRollup": []})

    def test_a_graphql_reply_without_the_pr_is_malformed(self):
        self.gh({"graphql": {"data": {"repository": {"pullRequest": None}}}})
        with self.assertRaises(github.GhError) as e:
            github.pr_view(7, "statusCheckRollup")
        self.assertEqual(e.exception.kind, github.MALFORMED)

    def test_pr_diff_asks_for_the_diff_media_type(self):
        calls = self.gh({f"{PULLS}/4": "diff --git a/x b/x\n"})
        self.assertEqual(github.pr_diff(4), "diff --git a/x b/x\n")
        self.assertIn("Accept: application/vnd.github.diff", calls[0])

    def test_pr_commits_lists_oid_and_committed_date(self):
        self.gh({f"{PULLS}/4/commits?per_page=100&page=1": [{"sha": "a", "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}]})
        self.assertEqual(github.pr_commits(4), [{"oid": "a", "committedDate": "2026-01-01T00:00:00Z"}])

    def test_current_pr_number_looks_up_the_open_pr_by_head(self):
        path = f"{PULLS}?head={{owner}}:{{branch}}&state=open"
        self.gh({path: [pull(12)]})
        self.assertEqual(github.current_pr_number(), "12")
        self.gh({path: []})
        self.assertEqual(github.current_pr_number(), "")

    # --- issues ---

    def test_issue_state_and_claim(self):
        self.gh({"repos/{owner}/{repo}/issues/5": {"state": "open", "assignees": [{"login": "me"}]},
                 "repos/{owner}/{repo}/issues/6": {"state": "closed", "assignees": []}})
        self.assertEqual(github.issue_state(5), "OPEN")
        self.assertEqual(github.issue_state("#6"), "CLOSED")
        self.assertEqual(github.issue_claim(5), ("OPEN", True))
        self.assertEqual(github.issue_claim(6), ("CLOSED", False))

    def test_close_issue_patches_the_state(self):
        calls = self.gh({"*": "{}"})
        github.close_issue(5)
        self.assertEqual(calls[0][2:], ["-X", "PATCH", "repos/{owner}/{repo}/issues/5", "-f", "state=closed",
                                        "-f", "state_reason=completed"])

    # --- writes ---

    def test_merge_pr_puts_the_method_and_the_head_sha(self):
        calls = self.gh({"*": "{}"})
        github.merge_pr(7, "squash", "abc")
        self.assertEqual(calls[0][2:], ["-X", "PUT", f"{PULLS}/7/merge", "-f", "merge_method=squash", "-f", "sha=abc"])
        github.merge_pr(7, "rebase")
        self.assertEqual(calls[1][2:], ["-X", "PUT", f"{PULLS}/7/merge", "-f", "merge_method=rebase"])

    def test_a_refused_merge_keeps_gh_s_message_for_merge_to_classify(self):
        def refuse(*cmd, **kw):
            return common.Run(1, '{"message":"Head branch was modified"}', "gh: Head branch was modified. (HTTP 409)")
        with unittest.mock.patch.object(common, "run", refuse), unittest.mock.patch.dict("os.environ", {"ORCHESTRATE_GH_LOG": "off"}):
            with self.assertRaises(github.GhError) as e:
                github.merge_pr(7, "squash", "abc")
        self.assertEqual(e.exception.kind, github.FAILED)
        self.assertIn("Head branch was modified", e.exception.message)

    def test_update_branch_puts_update_branch(self):
        calls = self.gh({"*": "{}"})
        github.update_branch("#7")
        self.assertEqual(calls[0][2:], ["-X", "PUT", f"{PULLS}/7/update-branch"])

    # --- every converted call is a logged, header-read `gh api` call ---

    def test_converted_calls_are_rest_not_graphql(self):
        calls = self.gh({"*": "[]"})
        for fn in (lambda: github.open_prs("main"), lambda: github.open_prs_with_heads(), lambda: github.merged_prs("main"),
                   lambda: github.pr_files(1), lambda: github.current_pr_number()):
            fn()
        for c in calls:
            self.assertEqual(c[:2], ["api", "--include"])
            self.assertFalse(github.describe(c[1:] if c[1] != "--include" else ["api", *c[2:]])["graphql"])


# The one `gh pr|issue|repo` subcommand left in scripts/, with why. #450 switches gate to github.pr_diff and drops
# its entry; nothing else is added without a REST form to point at.
SUBCOMMAND_ALLOWED = {
    ("gate", "pr", "diff"): "until #450 switches gate to github.pr_diff",
    ("watch-ci", "pr", "list"): "its check rollup has no single REST call (N+1)",
}
SUBCOMMAND = re.compile(r'(?:\bcall(?:_json)?|\bgh_json|common\.run)\(\s*(?:"gh",\s*)?(?:root,\s*)?"(pr|issue|repo)",\s*"([a-z-]+)"')


class NoSubcommands(unittest.TestCase):
    def test_no_script_runs_a_gh_pr_issue_or_repo_subcommand_but_the_allowed(self):
        found = set()
        for path in sorted((ROOT / "scripts").iterdir()):
            if path.is_dir() or path.name.endswith((".md", ".json")):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in SUBCOMMAND.finditer(text):
                found.add((path.name, m.group(1), m.group(2)))
        self.assertEqual(found, set(SUBCOMMAND_ALLOWED))


class GraphqlBraces(unittest.TestCase):
    """The fake gh never parses a query, so a lost brace passed every other test (#462): count them here."""

    def test_every_graphql_query_has_balanced_braces(self):
        # Scan by content, not name, so a new query or mutation constant cannot be skipped.
        queries = {k: v for k, v in vars(github).items()
                   if isinstance(v, str) and v.lstrip().startswith(("query", "mutation"))}
        self.assertIn("PR_QUERY", queries)
        for name, q in queries.items():
            with self.subTest(name):
                self.assertEqual(q.count("{"), q.count("}"), "braces")
                self.assertEqual(q.count("("), q.count(")"), "parentheses")


if __name__ == "__main__":
    unittest.main()
