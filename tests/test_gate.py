"""CLI tests for scripts/gate, driven through the harness with a fake gh."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402

# A fake `gh` whose n-th `pr view` reply is views[n] (the last one repeats); other calls use the canned
# replies in the state file, as harness.FAKE_GH does. Records calls the same way, for gh_calls().
SEQUENCED_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    views = __VIEWS__
    n = sum(1 for l in open(state + ".calls") if json.loads(l)[:2] == ["pr", "view"]) - 1
    sys.stdout.write(views[min(n, len(views) - 1)])
    sys.exit(0)
for r in json.load(open(state))["responses"]:
    if args[: len(r["args"])] == r["args"]:
        sys.stdout.write(r.get("stdout", ""))
        sys.exit(r.get("exit", 0))
sys.exit(1)
'''


class GateTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def prime(self, title="feat(x): add y", mergeable="MERGEABLE", behind=0):
        view = {"title": title, "mergeable": mergeable, "baseRefName": "main", "headRefName": "br"}
        self.env.set_gh([
            {"args": ["pr", "view"], "stdout": json.dumps(view)},
            {"args": ["api"], "stdout": f"{behind}\n"},
        ])

    def config(self, line):
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"## Review\n\n- **Repo-specific checks:** {line}\n- **Post reviews:** no\n")

    def gate(self, *extra):
        return self.env.run("gate", "7", "--retry-delay", "0", *extra, check=False)

    def event(self):
        (e,) = self.env.events()
        self.assertEqual((e["kind"], e["ref"]), ("gate", 7))
        return e

    def test_passing_gate(self):
        self.prime()
        self.config("codegen is current (run: `echo ok`), see `README.md`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(l.startswith("pass") for l in lines), lines)
        self.assertIn("behind_by 0", lines[1])
        e = self.event()
        self.assertEqual((e["outcome"], e["failed"], e["behind_by"]), ("pass", [], 0))
        # the compare call used the PR's base and head
        self.assertIn("repos/{owner}/{repo}/compare/main...br", self.env.gh_calls()[1])

    def test_title_convention_failure(self):
        self.prime(title="Add the thing")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.splitlines()[0].startswith("fail"))
        self.assertIn("Add the thing", r.stdout)
        e = self.event()
        self.assertEqual(e["outcome"], "fail")
        self.assertEqual(e["failed"], ["title follows Conventional Commits"])

    def test_conflicting_pr(self):
        self.prime(mergeable="CONFLICTING")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("conflicts with main", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")

    def test_behind_base_is_not_mergeable_and_is_reported(self):
        self.prime(behind=3)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("behind main by 3", r.stdout)
        self.assertEqual(self.event()["behind_by"], 3)

    def test_failing_repo_command(self):
        self.prime()
        self.config("codegen is up to date (run: `echo stale codegen; exit 4`)")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("(exit 4)", r.stdout)
        self.assertIn("stale codegen", r.stdout)
        e = self.event()
        self.assertEqual(e["failed"], ["echo stale codegen; exit 4"])

    def test_plain_backticks_are_never_run(self):
        self.prime()
        # This repo's own line (.claude/orchestration.md:66): `README.md` is prose, not a command.
        self.config("behaviour changes come with tests; Python 3.9 compatible; user-facing changes update `README.md`; "
                    "release-please files untouched; plugin features work out of the box, with no manual edits to the "
                    "user's config scripts (degrade instead).")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 2)  # title + mergeable only
        self.assertNotIn("README.md", r.stdout)

    def test_missing_cwd_exits_2_before_any_check(self):
        self.prime()
        r = self.gate("--cwd", str(self.env.repo / "gone"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--cwd", r.stderr)
        self.assertIn("does not exist", r.stderr)
        self.assertEqual(self.env.gh_calls(), [])
        self.assertEqual(self.env.events(), [])

    def test_cwd_is_where_repo_commands_and_config_live(self):
        self.prime()
        other = self.env.repo / "wt"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "orchestration.md").write_text("## Review\n\n- **Repo-specific checks:** where (run: `pwd`)\n")
        r = self.gate("--cwd", str(other))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("pwd", r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 3)

    def test_several_markers_in_one_item_all_run(self):
        self.prime()
        self.config("a (run: `echo one > one.txt`) and b (run: `echo two > two.txt`)")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((self.env.repo / "one.txt").exists() and (self.env.repo / "two.txt").exists())

    def test_dry_run_is_not_a_marker(self):
        self.prime()
        self.config("dry-run: `touch nope.txt`")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.env.repo / "nope.txt").exists())

    def test_malformed_markers_fail(self):
        for item in ("run: ``echo x``", "run:", "run: `` nothing", "must run: the tests"):
            with self.subTest(item=item):
                self.prime()
                self.config(item)
                r = self.gate()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("malformed run: marker", r.stdout)

    def test_only_the_repo_specific_checks_label_counts(self):
        self.prime()
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("## Review\n\n- Tiers: see Repo-specific checks (run: `touch nope.txt`)\n- **Repo-specific checks:** none\n")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.env.repo / "nope.txt").exists())

    def test_scoped_and_breaking_titles_pass(self):
        for title in ("feat(gate)!: drop x", "fix!: y", "chore(deps-dev): bump"):
            with self.subTest(title=title):
                self.prime(title=title)
                self.assertEqual(self.gate().returncode, 0)

    def test_bad_titles_fail(self):
        for title in ("feat: ", "feat(): x", "Feat: x", "feat x"):
            with self.subTest(title=title):
                self.prime(title=title)
                self.assertEqual(self.gate().returncode, 1)

    def test_unknown_mergeability_retries_then_exits_3(self):
        self.prime(mergeable="UNKNOWN")
        r = self.gate()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("run the gate again", r.stdout)
        views = [c for c in self.env.gh_calls() if c[:2] == ["pr", "view"]]
        self.assertGreater(len(views), 1)
        self.assertEqual(self.env.events()[-1]["outcome"], "unknown")

    def test_unknown_with_another_failure_is_a_failure(self):
        self.prime(title="bad title", mergeable="UNKNOWN")
        self.assertEqual(self.gate().returncode, 1)

    def test_unknown_behind_base_is_a_failure_not_a_rerun(self):
        # UNKNOWN does not hide a real problem: a PR behind its base is sent back to the worker.
        self.prime(mergeable="UNKNOWN", behind=3)
        r = self.gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("behind main by 3", r.stdout)
        e = self.event()
        self.assertEqual((e["outcome"], e["behind_by"]), ("fail", 3))

    def test_unknown_with_unreadable_behind_is_a_failure_not_a_rerun(self):
        self.env.set_gh([
            {"args": ["pr", "view"], "stdout": json.dumps(
                {"title": "feat(x): add y", "mergeable": "UNKNOWN", "baseRefName": "main", "headRefName": "br"})},
            {"args": ["api"], "stderr": "HTTP 502", "exit": 1},
        ])
        r = self.gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("could not read behind_by", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")

    def test_unknown_settles_to_mergeable_on_retry(self):
        # The canned-reply fake answers by args only, so this test swaps in a fake whose `pr view`
        # replies follow a sequence: UNKNOWN on the first look, MERGEABLE after that.
        self.env.set_gh([{"args": ["api"], "stdout": "0\n"}])
        views = [json.dumps({"title": "feat(x): add y", "mergeable": m, "baseRefName": "main", "headRefName": "br"})
                 for m in ("UNKNOWN", "MERGEABLE")]
        fake = SEQUENCED_GH.replace("__VIEWS__", json.dumps(views))
        (self.env.bin / "gh").write_text(fake)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len([c for c in self.env.gh_calls() if c[:2] == ["pr", "view"]]), 2)
        self.assertEqual(self.event()["outcome"], "pass")

    def test_gh_failure_exits_2(self):
        self.env.set_gh([{"args": ["pr", "view"], "stderr": "boom", "exit": 1}])
        r = self.gate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_no_config_runs_builtin_checks_only(self):
        self.prime()
        r = self.gate()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 2)

    def test_missing_gh_is_could_not_run(self):
        r = self.env.run("gate", "7", "--retry-delay", "0", check=False, no_gh=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("gh", r.stderr)
        self.assertEqual(self.env.events(), [])

    def test_malformed_gh_json_is_could_not_run(self):
        self.env.set_gh([{"args": ["pr", "view"], "stdout": "<html>not json"}])
        r = self.gate()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("malformed", r.stderr)

    def test_json_of_the_wrong_shape_is_could_not_run(self):
        self.env.set_gh([{"args": ["pr", "view"], "stdout": "[1, 2]"}])
        self.assertEqual(self.gate().returncode, 2)

    def test_non_positive_timeout_is_could_not_run(self):
        self.prime()
        self.assertEqual(self.gate("--timeout", "0").returncode, 2)

    def test_hung_repo_check_is_killed_and_fails(self):
        self.prime()
        self.config("slow (run: `sleep 30`)")
        r = self.gate("--timeout", "0.5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timed out after 0.5s", r.stdout)
        self.assertEqual(self.event()["outcome"], "fail")


if __name__ == "__main__":
    unittest.main()
