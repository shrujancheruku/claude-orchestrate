"""Smoke tests: trivial CLIs built on scripts/common.py, driven through the harness."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
from harness import SCRIPTS, ScriptEnv, gh_pr_view, git # noqa: E402

# One gh call through scripts/github.py (which logs a `gh_call` row), then one event of its own.
GH_CALL_CLI = '''
import sys
sys.path.insert(0, %r)
import github, runlog

github.pr_view(7, "state")
runlog.append_event(".", "merge", "merged", ref="#7", session="s1")
''' % str(SCRIPTS)

TRIVIAL_CLI = '''
import subprocess, sys
import common, runlog

slots = common.config_section(".", "Slots")
out = subprocess.run(["gh", "pr", "view", sys.argv[1], "--json", "state"], capture_output=True, text=True).stdout
print("pr", sys.argv[1], common.one_line(out))
print(common.tail("\\n".join("line %d" % i for i in range(100))).splitlines()[0])
print("slots:", common.one_line(slots or "none"))
runlog.append_event(".", "merge", "merged", ref="#" + sys.argv[1], session="s1")
'''

SECTION_CLI = '''
import sys
import common, runlog

print(common.config_section(".", sys.argv[1]))
runlog.append_event(".", "gate", "pass")
'''


class HarnessSmoke(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def cli(self, name, source):
        # A temp scripts/ dir holding a copy of common.py beside the fixture CLI, so the
        # CLI's plain `import common` only works if the script's own directory is on sys.path.
        d = self.env.repo.parent / "scripts"
        d.mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "common.py", d / "common.py")
        shutil.copy(SCRIPTS / "runlog.py", d / "runlog.py")
        shutil.copy(SCRIPTS / "repo.py", d / "repo.py")
        path = d / name
        path.write_text(source)
        return path

    def test_cli_through_harness(self):
        e = self.env
        e.commit(".claude/orchestration.md", "## Slots\n\n- **Slots:** 2.\n\n## Other\n\nx\n", "chore: config")
        e.set_gh([{"args": ["pr", "view", "7"], "stdout": '{"state": "OPEN"}'}])

        out = e.run(self.cli("cli.py", TRIVIAL_CLI), 7).stdout.splitlines()

        self.assertEqual(out[0], 'pr 7 {"state": "OPEN"}')
        self.assertEqual(out[1], "... 60 earlier lines omitted")
        self.assertEqual(out[2], "slots: - **Slots:** 2.")
        self.assertEqual(e.gh_calls(), [["pr", "view", "7", "--json", "state"]])
        (event,) = e.events()
        self.assertEqual(
            {k: event[k] for k in ("session", "kind", "ref", "outcome")},
            {"session": "s1", "kind": "merge", "ref": "#7", "outcome": "merged"},
        )
        self.assertIn("ts", event)
        self.assertEqual(git(e.repo, "ls-remote", "--heads", "origin").split()[1], "refs/heads/main")

    def test_section_boundaries(self):
        e = self.env
        e.commit(".claude/orchestration.md", "## A\n\none\n### sub\ntwo\n\n## B\n\nthree\n", "chore: config")
        cli = self.cli("section.py", SECTION_CLI)
        self.assertEqual(e.run(cli, "A").stdout, "one\n### sub\ntwo\n")
        self.assertEqual(e.run(cli, "b").stdout, "three\n")
        self.assertEqual(e.run(cli, "C").stdout, "None\n")

    def test_session_default_from_the_state_file_not_the_environment(self):
        e = self.env
        cli = self.cli("section.py", SECTION_CLI)
        saved = os.environ.get("CLAUDE_SESSION_ID")
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_SESSION_ID", saved) if saved else os.environ.pop("CLAUDE_SESSION_ID", None))
        os.environ["CLAUDE_SESSION_ID"] = "abc"
        e.run(cli, "A")
        self.assertEqual([x["session"] for x in e.events()], ["test-session"])

    def test_no_state_file_and_no_session_raises_nosession_and_writes_nothing(self):
        # The library raises (ADR-0014); this bare CLI does not catch it, so Python reports it and exits 1.
        e = self.env
        (e.repo / ".claude" / "orchestrate-state.json").unlink()
        cli = self.cli("section.py", SECTION_CLI)
        res = e.run(cli, "A", check=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("runlog.NoSession: runlog: no session, gate event not written", res.stderr)
        self.assertEqual(e.events(), [])

    def test_gh_calls_are_not_logged_by_default_so_event_counts_hold(self):
        self.env.set_gh([gh_pr_view(7, {"state": "OPEN"})])
        self.env.run(self.cli("ghcall.py", GH_CALL_CLI))
        self.assertEqual([e["kind"] for e in self.env.events(profiling=True)], ["merge"])

    def test_gh_log_opt_in_logs_gh_calls_and_the_reader_hides_them_unless_asked(self):
        env = ScriptEnv(gh_log=True)
        self.addCleanup(env.close)
        env.set_gh([gh_pr_view(7, {"state": "OPEN"})])
        d = env.repo.parent / "scripts"
        d.mkdir(exist_ok=True)
        for name in ("common.py", "runlog.py", "repo.py"):
            shutil.copy(SCRIPTS / name, d / name)
        cli = d / "ghcall.py"
        cli.write_text(GH_CALL_CLI)
        env.run(cli)
        self.assertEqual([e["kind"] for e in env.events()], ["merge"])
        kinds = [e["kind"] for e in env.events(profiling=True)]
        self.assertIn("gh_call", kinds)
        self.assertEqual(len(kinds), 2)


class ScriptEnvCopyRace(unittest.TestCase):
    def test_copies_keep_git_background_maintenance_off(self):
        # Auto maintenance holds objects/maintenance.lock while it runs, and a copy racing it fails (#415).
        env = ScriptEnv()
        self.addCleanup(env.close)
        for d in (env.repo, env.origin):
            self.assertEqual(git(d, "config", "maintenance.auto"), "false")
            self.assertEqual(git(d, "config", "gc.auto"), "0")

    def test_a_commit_in_a_copy_never_starts_git_maintenance(self):
        # The race (#415) needs a `git maintenance run --auto` child, which a commit spawns unless
        # maintenance.auto is false. Trace the commit's child processes: with the config gone from the
        # template it spawns one and this fails; it is the behaviour the fix guards, not config text.
        env = ScriptEnv()
        self.addCleanup(env.close)
        trace = Path(env._tmp.name) / "trace2.json"
        r = subprocess.run(["git", "commit", "--allow-empty", "-m", "chore: probe"], cwd=env.repo,
                           env=dict(os.environ, GIT_TRACE2_EVENT=str(trace)), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\"maintenance\",\"run\"", trace.read_text())

    def test_scripts_and_env_come_from_the_tests_own_tree(self):
        # A copy of tests/ with its own scripts/ (a scratch worktree at an older commit) must run those scripts,
        # whatever PYTHONPATH or CLAUDE_PLUGIN_ROOT point at (#480).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tests").mkdir()
            (root / "scripts").mkdir()
            shutil.copy(Path(harness.__file__), root / "tests" / "harness.py")
            for name in ("runlog.py", "common.py"):
                shutil.copy(SCRIPTS / name, root / "scripts" / name)
            (root / "scripts" / "probe").write_text(
                "import os\nprint(os.environ.get('PYTHONPATH'), os.environ.get('CLAUDE_PLUGIN_ROOT'))\n")
            code = ("import harness; e = harness.ScriptEnv(); print(harness.SCRIPTS);"
                    " print(e.run('probe').stdout.strip()); e.close()")
            env = dict(os.environ, PYTHONPATH=str(SCRIPTS), CLAUDE_PLUGIN_ROOT=str(SCRIPTS.parent))
            r = subprocess.run([sys.executable, "-c", code], cwd=str(root / "tests"), env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines(), [str((root / "scripts").resolve()), "None None"])

    def test_no_transient_lock_files_in_a_copy(self):
        env = ScriptEnv()
        self.addCleanup(env.close)
        locks = [p.name for root in (env.repo / ".git", env.origin) for p in root.rglob("*.lock")]
        self.assertEqual(locks, [])


if __name__ == "__main__":
    unittest.main()
