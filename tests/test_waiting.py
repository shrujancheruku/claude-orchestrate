"""scripts/waiting through its CLI, with the fake `gh` (#706): the footer's sections, config, format and the shared waits file."""

import json
import re
import unittest
from pathlib import Path

from harness import PR_GRAPHQL, ScriptEnv, gh_pulls, gh_repo_rest, pr_graphql_reply, rest_pull
import github  # tests/harness.py puts scripts/ on sys.path
import runlog
import waits

GREEN = [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RED = [{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]
HEAD = "a" * 40
COMMAND = f"gh pr merge 7 --squash --delete-branch --match-head-commit {HEAD}"


def node(number, title, labels=()):
    return {"number": number, "title": title, "url": f"https://x/issues/{number}", "labels": {"nodes": [{"name": n} for n in labels]}}


def issues_reply(nodes):
    return {"args": ["api", "graphql", "-f", "query=" + github.ISSUES_QUERY],
            "stdout": json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})}


def pull(n, **v):
    return {"args": ["api", f"repos/{{owner}}/{{repo}}/pulls/{n}"], "stdout": json.dumps(rest_pull(number=n, **v))}


def checks(view):
    return {"args": PR_GRAPHQL, "stdout": pr_graphql_reply(view)}


class Waiting(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.issues = [node(12, "Needs a human", ["ready-for-human"]), node(13, "Look at me", ["needs-triage"]),
                       node(14, "Another human", ["ready-for-human"]), node(15, "Plain")]
        self.extra = []

    def config(self, text):
        (self.env.repo / ".claude").mkdir(exist_ok=True)
        (self.env.repo / ".claude" / "orchestration.md").write_text(text)

    def gh(self):
        self.env.set_gh([gh_repo_rest({"owner": {"login": "o"}, "name": "r"}), *self.labelled_replies(), *self.extra])

    def labelled_replies(self):
        """One reply per label for the label-filtered query: number and title of the open issues carrying it (#776)."""
        out = []
        for label in ("ready-for-human", "needs-triage"):
            nodes = [{"number": n["number"], "title": n["title"]} for n in self.issues
                     if label in [l["name"] for l in n["labels"]["nodes"]]]
            out.append({"args": ["api", "graphql", "-f", "query=" + github.LABELLED_ISSUES_QUERY, "-f", "owner=o", "-f", "name=r", "-f", f"label={label}"],
                        "stdout": json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})})
        return out

    def footer(self, *args):
        self.gh()
        r = self.env.run("waiting", *args, extra_env={"COLUMNS": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_the_default_sections_print_in_order_with_their_counts_and_items(self):
        self.assertEqual(self.footer("--plain"), [
            "\U0001f7e5 Ready for you (2)", "  #12 Needs a human", "  #14 Another human",
            "\U0001f7e8 Needs triage (1)", "  #13 Look at me"])

    def test_the_default_colours_are_red_yellow_and_blue_ansi(self):
        self.env.run("waiting", "--add", "ask me")
        out = self.footer()
        self.assertTrue(out[0].startswith("\033[1;31m") and out[0].endswith("\033[0m"), out[0])
        self.assertTrue(out[3].startswith("\033[1;33m"), out[3])
        self.assertTrue(out[5].startswith("\033[1;34m"), out[5])

    def test_the_order_and_colours_come_from_the_config(self):
        self.config("## Developer waits\n\n- sections: needs-triage, ready-for-human\n- colours: needs-triage green\n")
        out = self.footer()
        self.assertEqual([l for l in out if l.startswith("\033[1;")],
                         ["\033[1;32mNeeds triage (1)\033[0m", "\033[1;31mReady for you (2)\033[0m"])

    def test_a_bad_config_section_is_one_line_on_stderr_and_the_defaults(self):
        self.config("## Developer waits\n\n- colours: ready-for-human purple\n")
        self.gh()
        r = self.env.run("waiting", "--plain")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn("purple", r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "\U0001f7e5 Ready for you (2)")

    def test_disabled_prints_nothing(self):
        self.config("## Developer waits\n\n- enabled: no\n")
        self.assertEqual(self.footer(), [])

    def test_an_empty_section_is_left_out(self):
        self.issues = [node(13, "Look at me", ["needs-triage"])]
        self.assertEqual(self.footer("--plain"), ["\U0001f7e8 Needs triage (1)", "  #13 Look at me"])

    def test_every_section_empty_is_one_line(self):
        self.issues = [node(15, "Plain")]
        self.assertEqual(self.footer(), ["Waiting on you: nothing"])
        self.assertEqual(self.footer("--plain"), ["Waiting on you: nothing"])

    def test_a_long_title_is_cut_to_the_width(self):
        self.issues = [node(12, "x" * 200, ["ready-for-human"])]
        line = self.footer("--plain", "--width", "40")[1]
        self.assertEqual(len(line), 40)
        self.assertTrue(line.endswith("…"))

    def test_plain_has_no_ansi_codes(self):
        self.assertNotIn("\033", "\n".join(self.footer("--plain")))

    def test_add_then_done_round_trip(self):
        r = self.env.run("waiting", "--add", "pick a model", "--where", "in the lead's session")
        wait_id = r.stdout.strip()
        self.assertRegex(wait_id, r"^[0-9a-f]{6}$")
        out = self.footer("--plain")
        self.assertIn("\U0001f7e6 Lead asks (1)", out)
        self.assertIn("  pick a model — in the lead's session", out)
        self.assertEqual(self.env.run("waiting", "--done", wait_id).stdout.strip(), f"done {wait_id}")
        self.assertNotIn("Lead asks", "\n".join(self.footer("--plain")))
        gone = self.env.run("waiting", "--done", wait_id, check=False)
        self.assertEqual((gone.returncode, gone.stdout.strip()), (1, f"no wait {wait_id}"))

    def test_the_waits_file_is_owner_only(self):
        self.env.run("waiting", "--add", "x")
        mode = (self.env.repo / ".claude" / "orchestrate-waits.jsonl").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_an_orchestrator_ask_names_who_asks(self):
        self.env.run("waiting", "--add", "approve hooks/**", "--from", "orchestrator", "--where", "claude attach orchestrator")
        self.assertIn("  [orchestrator] approve hooks/** — claude attach orchestrator", self.footer("--plain"))

    def test_an_optional_source_turns_on_when_listed(self):
        self.config("## Developer waits\n\n- sections: release-prs\n")
        self.extra = [gh_pulls([{"number": 20, "headRefName": "release-please--x"}, {"number": 21, "headRefName": "1-x"}], "open"),
                      pull(20, title="chore(main): release 1.2.0"), pull(21, title="feat: other")]
        self.assertEqual(self.footer("--plain"), ["\U0001fa75 Release PRs (1)", "  PR #20 chore(main): release 1.2.0"])

    def gated_pr(self, head=HEAD):
        """An open PR 30 touching hooks/**, green, for the optional `merge-gates` source."""
        self.config("## Gates\n\n- **Merge gates:** PRs touching `hooks/**` are merged by the developer.\n\n"
                    "## Developer waits\n\n- sections: merge-gates\n")
        self.extra = [gh_pulls([{"number": 30, "headRefName": "30-x"}], "open"),
                      {"args": ["api", "~repos/{owner}/{repo}/pulls/30/files"], "stdout": json.dumps([{"filename": "hooks/guard.py"}])},
                      pull(30, title="fix: gated", headRefOid=head), checks({"headRefOid": head, "statusCheckRollup": GREEN, "state": "OPEN"})]

    def review(self, outcome, head):
        runlog.append_event(self.env.repo, runlog.REVIEW, outcome, ref=30, session="s1", round=1, head=head)

    def test_merge_gates_lists_a_green_gated_pr_whose_current_head_is_approved(self):
        self.gated_pr()
        self.review("approve", HEAD)
        self.assertEqual(self.footer("--plain"), [
            "\U0001f7ea Merge gates (1)",
            f"  PR #30 fix: gated: approved, green. Merge: ! gh pr merge 30 --squash --delete-branch --match-head-commit {HEAD}"])

    def test_merge_gates_does_not_list_a_green_gated_pr_without_an_approval_of_its_head(self):
        cases = {"no review": None, "approval of an older head": ("approve", "b" * 40),
                 "sent back": ("request-changes", HEAD), "approval then a send-back": ("request-changes", HEAD)}
        for name, event in cases.items():
            with self.subTest(name):
                self.setUp()
                self.gated_pr()
                if name == "approval then a send-back":
                    self.review("approve", HEAD)
                if event:
                    self.review(*event)
                self.assertEqual(self.footer("--plain"), ["Waiting on you: nothing"])

    def test_an_entry_for_a_closed_pr_is_dropped_and_cleared(self):
        waits.add(self.env.repo, "PR #7 (#3) x", source="merge", kind="merge-gate", pr=7, ticket=3, head=HEAD, command=COMMAND)
        self.extra = [pull(7, state="CLOSED")]
        self.assertEqual([l for l in self.footer("--plain") if "PR #7" in l], [])
        self.assertEqual(waits.live(self.env.repo), [])

    def test_an_entry_for_a_closed_ticket_is_dropped(self):
        waits.add(self.env.repo, "answer me", ticket=99)
        self.extra = [{"args": ["api", "repos/{owner}/{repo}/issues/99"], "stdout": json.dumps({"state": "closed"})}]
        self.assertNotIn("Lead asks", "\n".join(self.footer("--plain")))

    def test_an_entry_for_an_open_ticket_stays(self):
        waits.add(self.env.repo, "answer me", ticket=98)
        self.extra = [{"args": ["api", "repos/{owner}/{repo}/issues/98"], "stdout": json.dumps({"state": "open"})}]
        self.assertIn("  answer me", self.footer("--plain"))

    def test_one_run_reads_only_label_filtered_issues_never_the_backlog(self):
        waits.add(self.env.repo, "answer me", ticket=98)
        self.extra = [{"args": ["api", "repos/{owner}/{repo}/issues/98"], "stdout": json.dumps({"state": "open"})}]
        self.footer("--plain")
        queries = [c[3] for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        self.assertEqual(sorted(queries), sorted(["query=" + github.LABELLED_ISSUES_QUERY] * 2))
        self.assertNotIn("query=" + github.ISSUES_QUERY, queries)
        self.assertIn("labels: [$label]", github.LABELLED_ISSUES_QUERY)
        self.assertNotIn("body", github.LABELLED_ISSUES_QUERY)
        sent = [c for c in self.env.gh_calls() if c[:2] == ["api", "graphql"]]
        self.assertEqual(sorted(c[-1] for c in sent), ["label=needs-triage", "label=ready-for-human"])

    def test_escape_bytes_and_triple_backticks_are_stripped_in_both_forms(self):
        waits.add(self.env.repo, "run \x1b[31mred\x1b[0m ```rm -rf``` `ok` \x9b2J", source="prompt", kind="prompt", ts=waits.datetime.now(waits.timezone.utc).isoformat())
        self.env.run("waiting", "--add", "ask \x1b]0;x\x07 ```fence``` ````")
        for args in (("--plain",), ()):
            with self.subTest(args):
                out = "\n".join(self.footer(*args))
                body = re.sub(r"\x1b\[(?:1;)?\d+m", "", out, flags=0)  # the footer's own colour codes, which wrap each line
                for bad in ("\x1b", "\x9b", "```", "\x07"):
                    self.assertNotIn(bad, body)
                self.assertIn("[31mred", out)  # the injected escape lost its ESC byte and is plain text now
                self.assertIn("`ok`", out)
                self.assertIn("'''fence'''", out)

    def test_a_ready_gated_pr_whose_head_moved_shows_both_heads(self):
        note = f"(approved head {'b' * 12}, current head {'a' * 12}: a main-only update, no change of the PR's own)"
        waits.add(self.env.repo, "PR #7 (#3) x", source="merge", kind="merge-gate", pr=7, ticket=3, head=HEAD, command=COMMAND, heads=note)
        self.extra = [pull(7, mergeStateStatus="CLEAN"), checks({"headRefOid": HEAD, "statusCheckRollup": GREEN, "state": "OPEN"})]
        line = self.footer("--plain", "--width", "400")[-1]
        self.assertIn(f": approved, green. {note} Merge: ! {COMMAND}", line)

    def test_a_ready_gated_pr_prints_its_merge_command_whole(self):
        waits.add(self.env.repo, "PR #7 (#3) " + "t" * 150, source="merge", kind="merge-gate", pr=7, ticket=3, head=HEAD, command=COMMAND)
        self.extra = [pull(7, mergeStateStatus="CLEAN"), checks({"headRefOid": HEAD, "statusCheckRollup": GREEN, "state": "OPEN"})]
        line = self.footer("--plain", "--width", "120")[-1]
        self.assertTrue(line.endswith(f": approved, green. Merge: ! {COMMAND}"), line)
        self.assertLessEqual(len(line), 2 + 120 + len(COMMAND))

    def test_a_moved_head_red_ci_or_behind_turns_the_command_into_not_ready(self):
        waits.add(self.env.repo, "PR #7 (#3) x", source="merge", kind="merge-gate", pr=7, ticket=3, head=HEAD, command=COMMAND)
        cases = (({"headRefOid": "b" * 40, "statusCheckRollup": GREEN}, "CLEAN", "not ready: the head moved"),
                 ({"headRefOid": HEAD, "statusCheckRollup": RED}, "CLEAN", "not ready: CI failed"),
                 ({"headRefOid": HEAD, "statusCheckRollup": GREEN}, "BEHIND", "not ready: behind"))
        for view, merge_state, want in cases:
            with self.subTest(want):
                self.extra = [pull(7, mergeStateStatus=merge_state), checks({**view, "state": "OPEN"})]
                line = self.footer("--plain")[-1]
                self.assertIn(want, line)
                self.assertNotIn("gh pr merge", line)

    def test_a_merged_pr_drops_the_entry(self):
        waits.add(self.env.repo, "PR #7 (#3) x", source="merge", kind="merge-gate", pr=7, ticket=3, head=HEAD, command=COMMAND)
        self.extra = [pull(7, state="MERGED")]
        self.assertNotIn("Lead asks", "\n".join(self.footer("--plain")))
        self.assertEqual(waits.live(self.env.repo), [])

    def test_a_stale_snapshot_lists_the_relaunch_and_a_later_launch_clears_it(self):
        runlog.append_event(self.env.repo, runlog.SNAPSHOT, "stale", ref=731, sha="c" * 40)
        out = self.footer("--plain")
        self.assertTrue(any(l.strip().startswith("relaunch: snapshot behind main (PR #731)") and "scripts/launch --bg" in l
                            for l in out), out)
        runlog.append_event(self.env.repo, runlog.SNAPSHOT, "launched")
        self.assertFalse(any("relaunch:" in l for l in self.footer("--plain")))


if __name__ == "__main__":
    unittest.main()
