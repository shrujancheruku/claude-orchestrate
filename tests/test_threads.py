"""scripts/threads and github.review_threads through the CLI, with the fake `gh` serving the GraphQL replies."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from harness import ScriptEnv, gh_repo_view  # noqa: E402
import github  # noqa: E402
from importlib.machinery import SourceFileLoader  # noqa: E402
from importlib.util import module_from_spec, spec_from_loader  # noqa: E402

# An extensionless script: spec_from_loader with an explicit loader (load_module is gone in new Pythons).
_loader = SourceFileLoader("threads_script", str(Path(__file__).resolve().parent.parent / "scripts" / "threads"))
threads_script = module_from_spec(spec_from_loader("threads_script", _loader))
_loader.exec_module(threads_script)

SHA = "abc1234def5678abc1234def5678abc1234def56"
Q = ["api", "graphql", "-f", "query=" + github.THREADS_QUERY]
MORE = ["api", "graphql", "-f", "query=" + github.THREAD_COMMENTS_QUERY]


def aliased(field, n, bodies):
    """The batched mutation text the script sends for n threads (an alias per thread)."""
    decls = [f"$id{i}: ID!" + (f", $body{i}: String!" if bodies else "") for i in range(n)]
    calls = [f"m{i}: " + field.replace("$id", f"$id{i}").replace("$body", f"$body{i}") for i in range(n)]
    return "mutation(" + ", ".join(decls) + ") { " + " ".join(calls) + " }"


def comment(author, body):
    return {"author": {"login": author}, "body": body, "url": "u"}


def thread(tid, resolved=False, outdated=False, path="a.py", line=3, comments=None):
    return {"id": tid, "isResolved": resolved, "isOutdated": outdated, "path": path, "line": line,
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": comments or [comment("rev", "[blocking] bad\nmore")]}}


def reply(nodes, more=False, cursor=None):
    return {"stdout": json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}})}


class Threads(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def gh(self, *threads_replies, extra=()):
        self.env.set_gh([gh_repo_view(), {"args": Q, "seq": list(threads_replies)}, {"args": ["api", "--paginate"], "stdout": SHA + "\n"},
                         *[{"args": ["api", "graphql", "-f", "query=" + q], "stdout": "{}"} for q in
                          (threads_script.REPLY, threads_script.RESOLVE_THREAD, threads_script.UNRESOLVE_THREAD)], *extra])

    def mutations(self):
        return [c for c in self.env.gh_calls() if c[:3] == ["api", "graphql", "-f"] and c[3].startswith("query=mutation")]

    def test_lists_unresolved_threads_in_the_line_shape(self):
        self.gh(reply([thread("T1", outdated=True), thread("T2", line=None, path="b.py",
                                                           comments=[comment("bot", "[minor] file-level")]),
                       thread("T3", resolved=True)]))
        r = self.env.run("threads", 12)
        self.assertEqual(r.stdout.splitlines(), ["T1 rev a.py:3 outdated [blocking] bad", "T2 bot b.py:file [minor] file-level"])

    def test_all_adds_resolved_threads_with_their_closing_reply(self):
        self.gh(reply([thread("T1", resolved=True, comments=[comment("rev", "[blocking] x"), comment("w", "fixed in abc1234")]),
                       thread("T2", resolved=True, comments=[comment("rev", "[minor] y")])]))
        r = self.env.run("threads", 12, "--all")
        self.assertEqual(r.stdout.splitlines(),
                         ["T1 rev a.py:3 resolved [blocking] x", "  fixed in abc1234", "T2 rev a.py:3 resolved [minor] y"])

    def test_outdated_thread_shows_its_original_line(self):
        old = thread("T1", outdated=True, line=None)
        old["originalLine"] = 7
        self.gh(reply([old]))
        self.assertEqual(self.env.run("threads", 12).stdout.splitlines(), ["T1 rev a.py:7 outdated [blocking] bad"])

    def test_reopened_thread_shows_the_reopen_reason(self):
        self.gh(reply([thread("T1", comments=[comment("rev", "[blocking] x"), comment("w", "fixed in abc1234"),
                                              comment("rev", "reopened: still broken\nmore")])]))
        self.assertEqual(self.env.run("threads", 12).stdout.splitlines(),
                         ["T1 rev a.py:3 [blocking] x", "  reopened: still broken"])

    def test_retry_does_not_post_the_same_reply_twice(self):
        self.gh(reply([thread("T1", comments=[comment("rev", "[blocking] x"), comment("w", "left: later")])]))
        self.assertEqual(self.env.run("threads", 12, "resolve", "T1", "--left", "later").returncode, 0)
        m = self.mutations()
        self.assertEqual(len(m), 1)
        self.assertIn("resolveReviewThread", m[0][3])

    def test_nothing_printed_when_no_threads(self):
        self.gh(reply([]))
        r = self.env.run("threads", 12)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_pages_past_100_threads_and_50_comments(self):
        big = thread("T1")
        big["comments"] = {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [comment("rev", "[blocking] a")]}
        more = {"stdout": json.dumps({"data": {"node": {"comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                                      "nodes": [comment("w", "left: later")]}}}})}
        self.gh(reply([big], more=True, cursor="p1"), reply([thread("T2")]),
                extra=[{"args": MORE, "stdout": more["stdout"]}])
        r = self.env.run("threads", 12, "--all")
        self.assertEqual(r.stdout.splitlines(), ["T1 rev a.py:3 [blocking] a", "T2 rev a.py:3 [blocking] bad"])
        calls = self.env.gh_calls()
        self.assertTrue(any(c[:4] == MORE and "after=c1" in c for c in calls))
        self.assertTrue(any(c[:4] == Q and "after=p1" in c for c in calls))

    def test_resolve_fixed_replies_then_resolves_and_logs(self):
        self.gh(reply([thread("T1")]))
        r = self.env.run("threads", 12, "resolve", "T1", "--fixed", SHA[:10])
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.mutations()
        self.assertIn("addPullRequestReviewThreadReply", m[0][3])
        self.assertIn(f"body=fixed in {SHA[:10]}", m[0])
        self.assertIn("resolveReviewThread", m[1][3])
        self.assertEqual(len(m), 2)
        ev = self.env.events()[-1]
        self.assertEqual((ev["kind"], ev["outcome"], ev["ref"], ev["thread"], ev["author"]), ("threads", "resolved", 12, "T1", "rev"))

    def test_no_pr_argument_resolves_the_thread_on_the_current_branchs_pr(self):
        self.gh(reply([thread("T1")]), extra=[{"args": ["pr", "view", "--json", "number"], "stdout": "12\n"}])
        r = self.env.run("threads", "resolve", "T1", "--fixed", SHA[:10])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["pr", "view", "--json", "number", "--jq", ".number"], self.env.gh_calls())
        self.assertEqual(len(self.mutations()), 2)
        self.assertEqual(self.env.events()[-1]["ref"], 12)

    def test_no_pr_argument_lists_the_current_branchs_threads(self):
        self.gh(reply([thread("T1")]), extra=[{"args": ["pr", "view", "--json", "number"], "stdout": "12\n"}])
        r = self.env.run("threads")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("T1 rev a.py:3"))

    def test_a_word_that_is_neither_a_pr_nor_an_action_is_a_usage_error(self):
        r = self.env.run("threads", "nonsense", check=False)
        self.assertEqual(r.returncode, 2)

    def test_three_ids_make_one_reply_request_and_one_resolve_request_with_aliased_mutations(self):
        self.gh(reply([thread("T1"), thread("T2"), thread("T3")]),
                extra=[{"args": ["api", "graphql", "-f", "query=" + aliased(f, 3, body)], "stdout": "{}"}
                       for f, body in ((threads_script.REPLY_FIELD, True), (threads_script.RESOLVE_FIELD, False))])
        r = self.env.run("threads", 12, "resolve", "T1", "T2", "T3", "--left", "later", "--verbose")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.strip(), "writes: 2")
        m = self.mutations()
        self.assertEqual(len(m), 2)
        self.assertEqual(m[1][3].count("resolveReviewThread"), 3)
        for i in range(3):
            self.assertIn(f"m{i}: resolveReviewThread(input: {{threadId: $id{i}}})", m[1][3])
        self.assertEqual([a for a in m[1] if a.startswith("id")], ["id0=T1", "id1=T2", "id2=T3"])
        self.assertEqual(m[0][3].count("addPullRequestReviewThreadReply"), 3)
        self.assertEqual(sum(a == "body2=left: later" for a in m[0]), 1)
        self.assertEqual([e["thread"] for e in self.env.events() if e["kind"] == "threads"], ["T1", "T2", "T3"])
        self.assertEqual(r.stdout.splitlines()[0], "resolved T1: left: later")

    def test_one_unknown_id_among_several_changes_nothing(self):
        self.gh(reply([thread("T1")]))
        self.refused("resolve", "T1", "NOPE", "--left", "x")

    def test_a_single_id_sends_the_plain_mutations(self):
        self.gh(reply([thread("T1")]))
        self.env.run("threads", 12, "resolve", "T1", "--left", "x")
        self.assertEqual([m[3] for m in self.mutations()], ["query=" + threads_script.REPLY, "query=" + threads_script.RESOLVE_THREAD])

    def test_resolve_left_needs_no_sha_check(self):
        self.gh(reply([thread("T1")]))
        r = self.env.run("threads", 12, "resolve", "T1", "--left", "not worth it")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("body=left: not worth it", self.mutations()[0])
        self.assertFalse(any(c[:2] == ["api", "--paginate"] for c in self.env.gh_calls()))

    def test_reopen_replies_then_unresolves_and_logs(self):
        self.gh(reply([thread("T1", resolved=True)]))
        r = self.env.run("threads", 12, "reopen", "T1", "--why", "still broken")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.mutations()
        self.assertIn("body=reopened: still broken", m[0])
        self.assertIn("unresolveReviewThread", m[1][3])
        self.assertEqual(self.env.events()[-1]["outcome"], "reopened")

    def refused(self, *args):
        r = self.env.run("threads", 12, *args, check=False)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.env.events(), [])

    def test_unknown_thread_is_refused(self):
        self.gh(reply([thread("T1")]))
        self.refused("resolve", "NOPE", "--left", "x")

    def test_resolving_a_resolved_thread_is_refused(self):
        self.gh(reply([thread("T1", resolved=True)]))
        self.refused("resolve", "T1", "--left", "x")

    def test_reopening_an_open_thread_is_refused(self):
        self.gh(reply([thread("T1")]))
        self.refused("reopen", "T1", "--why", "x")

    def test_fixed_sha_not_on_the_branch_is_refused(self):
        self.gh(reply([thread("T1")]))
        self.refused("resolve", "T1", "--fixed", "deadbeef")

    def test_gh_failure_is_could_not_run(self):
        self.env.set_gh([{"args": ["repo", "view"], "stderr": "boom", "exit": 1}])
        r = self.env.run("threads", 12, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_bad_command_line_is_exit_2(self):
        self.gh(reply([]))
        for args in (["resolve", "T1"], ["resolve", "T1", "--fixed", "a", "--left", "b"], ["reopen", "T1"]):
            self.assertEqual(self.env.run("threads", 12, *args, check=False).returncode, 2, args)


ROOT = Path(__file__).resolve().parent.parent


class Prompts(unittest.TestCase):
    """The prompts name the script and leave the reply form to it (ADR-0006)."""

    def text(self, name):
        return (ROOT / name).read_text()

    def test_reviewer_reopens_in_round_2_and_never_posts_file_level_comments(self):
        s = self.text("agents/reviewer.md")
        self.assertNotIn("subject_type=file", s)
        self.assertIn("## Outside the diff", s)
        self.assertIn("threads <pr> --all", s)
        self.assertIn("threads <pr> reopen", s)
        for form in ("fixed in <sha>", "left: <why>", "reopened: <why>"):
            self.assertNotIn(form, s)

    def test_worker_resolves_threads_after_the_push_and_names_human_only(self):
        s = self.text("agents/worker.md")
        para = s[s.index('For "address review"'):]
        self.assertIn("threads resolve <id>", para)
        self.assertIn("--fixed <the pushed sha>", para)
        self.assertIn("stop-reason: human-only", para)
        self.assertNotIn("reviews/<id>/comments", s)
        self.assertNotIn("fixed in <sha>", s)

    def test_worker_weighs_body_minors_and_answers_them_in_a_pr_comment(self):
        s = self.text("agents/worker.md")
        self.assertIn("pulls/<pr>/reviews/<id>", s)  # the review body, read as well as the threads
        self.assertIn("gh pr comment <pr>", s)  # a body minor has no thread to close
        self.assertIn("outside the PR's diff", self.text("agents/reviewer.md"))

    def test_run_skill_does_not_restate_the_reply_form(self):
        s = self.text("skills/run/SKILL.md")
        self.assertIn("closes each minor's thread", s)
        self.assertIn("closes the threads with the same script", s)
        self.assertNotIn("fixed in <sha>", s)


if __name__ == "__main__":
    unittest.main()
