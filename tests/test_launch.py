"""scripts/launch through its CLI: the orchestrator's launch and resume line, with a fake `claude` on PATH (#403)."""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / "scripts" / "launch"
SETTINGS = "'{\"disableAutoMode\":\"disable\"}'"
BG_SETTINGS = "'{\"disableAutoMode\":\"disable\",\"crossSessionInbound\":\"accept\"}'"

CONFIG = """# Orchestration config

## Models

| Role | Model | Effort |
|---|---|---|
| Lead | %s | |
| Orchestrator | %s | |
| Worker | sonnet | medium |
"""


class Launch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.claude_dir = self.tmp / "dot-claude"
        (self.claude_dir / "plugins" / "market").mkdir(parents=True)
        self.cache = self.tmp / "cache"
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        self.checkout = self.tmp / "checkout"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.checkout))
        self.sha = self.commit_plugin("v1")
        self.snap = self.cache / "orchestrate" / "plugin" / self.sha
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.agents = []
        self.set_model("opus (used when the lead launches it)")
        self.fake_claude()

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()

    def commit_plugin(self, text):
        """Commit the plugin dirs (and a file outside them) with `text` in each, push to origin/main; return the sha."""
        for d in (".claude-plugin", "hooks", "agents", "skills", "scripts"):
            (self.checkout / d).mkdir(exist_ok=True)
            (self.checkout / d / "f.txt").write_text(text)
        (self.checkout / "README.md").write_text(text)
        self.git(self.checkout, "add", "-A")
        self.git(self.checkout, "commit", "-q", "-m", text)
        self.git(self.checkout, "push", "-q", "origin", "HEAD:main")
        self.git(self.checkout, "fetch", "-q", "origin")
        self.git(self.checkout, "remote", "set-head", "origin", "main")
        return self.git(self.checkout, "rev-parse", "origin/main")

    def set_model(self, cell, lead=""):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "orchestration.md").write_text(CONFIG % (lead, cell))

    def fake_claude(self):
        """A `claude` that answers `agents --json` with self.agents and otherwise echoes its arguments, one per line."""
        script = self.bin / "claude"
        script.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = agents ]; then cat {self.tmp}/agents.json; exit 0; fi\n'
            'for a in "$@"; do echo "ARG:$a"; done\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.tmp / "agents.json").write_text(json.dumps(self.agents))

    def live(self, *rows):
        self.agents = list(rows)
        self.fake_claude()

    def launch(self, *args, root=None):
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                   CLAUDE_CONFIG_DIR=str(self.claude_dir), XDG_CACHE_HOME=str(self.cache),
                   CLAUDE_PLUGIN_ROOT=str(root or self.checkout))
        return subprocess.run([str(LAUNCH), "--repo", str(self.repo), *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_checkout_prints_the_whole_line(self):
        r = self.launch("--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.strip(),
            f"claude --name orchestrator --permission-mode acceptEdits --settings {SETTINGS} --model opus "
            f"--plugin-dir {self.snap} \"/orchestrate:run --greedy\"")

    def test_plugin_dir_is_never_inside_the_repo_in_any_mode(self):
        modes = [(), ("--bg",), ("--lead",), ("--resume", "abc"), ("--permission-mode", "bypassPermissions")]
        for mode in modes:
            r = self.launch("--print", *mode)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.count("--plugin-dir"), 1, mode)
            path = Path(r.stdout.split("--plugin-dir ")[1].split()[0]).resolve()
            for forbidden in (self.checkout.resolve(), self.repo.resolve(), self.claude_dir.resolve()):
                self.assertNotIn(forbidden, [path, *path.parents], mode)

    def test_snapshot_holds_the_plugin_dirs_at_origin_default_and_is_reused(self):
        r = self.launch("--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"plugin snapshot {self.sha[:12]}", r.stderr)
        for d in (".claude-plugin", "hooks", "agents", "skills", "scripts"):
            self.assertEqual((self.snap / d / "f.txt").read_text(), "v1")
        self.assertFalse((self.snap / "README.md").exists())
        marker = self.snap / "marker"
        marker.write_text("kept")
        self.assertEqual(self.launch("--print").returncode, 0)
        self.assertTrue(marker.exists())  # reused, not rebuilt

    def test_unpushed_local_commits_are_not_in_the_snapshot(self):
        (self.checkout / "hooks" / "f.txt").write_text("local")
        self.git(self.checkout, "commit", "-qam", "local")
        self.launch("--print")
        self.assertEqual((self.snap / "hooks" / "f.txt").read_text(), "v1")

    def test_only_the_three_newest_snapshots_are_kept(self):
        shas = [self.sha]
        for n in range(2, 6):
            shas.append(self.commit_plugin(f"v{n}"))
            self.assertEqual(self.launch().returncode, 0)
            base = self.cache / "orchestrate" / "plugin"
            os.utime(base / shas[-1], (n * 1000, n * 1000))  # long ago, in a deterministic age order
        base = self.cache / "orchestrate" / "plugin"
        self.assertEqual(sorted(p.name for p in base.iterdir()), sorted(shas[-3:]))

    def test_a_snapshot_used_within_a_day_and_a_print_are_never_pruned(self):
        base = self.cache / "orchestrate" / "plugin"
        shas = [self.sha]
        self.assertEqual(self.launch().returncode, 0)
        for n in range(2, 5):  # all recent: a live session may be loading any of them
            shas.append(self.commit_plugin(f"v{n}"))
            self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(sorted(p.name for p in base.iterdir()), sorted(shas))
        for sha in shas:
            os.utime(base / sha, (1000, 1000))
        shas.append(self.commit_plugin("v5"))
        self.assertEqual(self.launch("--print").returncode, 0)  # --print prunes nothing
        self.assertEqual(len(list(base.iterdir())), 5)
        self.assertEqual(self.launch().returncode, 0)  # a real launch prunes the old ones
        self.assertEqual(len(list(base.iterdir())), 3)

    def test_a_plugin_root_that_is_not_a_checkout_is_refused(self):
        r = self.launch("--print", root=self.tmp / "bin")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git checkout", r.stderr)

    def test_marketplace_root_has_no_plugin_dir(self):
        r = self.launch("--print", root=self.claude_dir / "plugins" / "market")
        self.assertNotIn("--plugin-dir", r.stdout)
        self.assertIn("--model opus", r.stdout)

    def test_no_model_row_or_old_wording_has_no_model_flag(self):
        for cell in ("", "the session's model"):
            with self.subTest(cell=cell):
                self.set_model(cell)
                self.assertNotIn("--model", self.launch("--print").stdout)
        (self.repo / ".claude" / "orchestration.md").unlink()
        self.assertNotIn("--model", self.launch("--print").stdout)

    def test_no_model_drops_the_flag_the_config_names(self):
        self.assertNotIn("--model", self.launch("--print", "--no-model").stdout)

    def test_greedy_n_and_ticket_pass_through(self):
        self.assertTrue(self.launch("--print", "--greedy=4").stdout.strip().endswith('"/orchestrate:run --greedy=4"'))
        self.assertTrue(self.launch("--print", "403").stdout.strip().endswith('"/orchestrate:run 403"'))
        self.assertEqual(self.launch("--print", "--greedy=4", "403").returncode, 2)
        self.assertEqual(self.launch("--print", "abc").returncode, 2)

    def test_bg_adds_the_flag_and_cross_session_inbound(self):
        out = self.launch("--print", "--bg").stdout
        self.assertTrue(out.startswith("claude --bg --name orchestrator "), out)
        self.assertIn(BG_SETTINGS, out)

    def test_bypass_permissions_accepts_inbound_messages(self):
        out = self.launch("--print", "--permission-mode", "bypassPermissions").stdout
        self.assertIn("--permission-mode bypassPermissions", out)
        self.assertIn(BG_SETTINGS, out)

    def test_resume_keeps_the_flags_and_drops_the_prompt(self):
        out = self.launch("--print", "--resume", "abc-123").stdout.strip()
        self.assertEqual(
            out,
            f"claude --resume abc-123 --name orchestrator --permission-mode acceptEdits --settings {SETTINGS} "
            f"--model opus --plugin-dir {self.snap}")
        self.assertEqual(self.launch("--print", "--resume", "abc", "--greedy=2").returncode, 2)

    def test_lead_line_accepts_inbound_messages(self):
        out = self.launch("--print", "--lead").stdout.strip()
        self.assertEqual(
            out, "claude --name lead --settings '{\"crossSessionInbound\":\"accept\"}' "
                 f"--plugin-dir {self.snap} \"/orchestrate:lead\"")

    def test_live_orchestrator_refuses_a_plain_launch(self):
        self.live({"name": "orchestrator", "pid": 4242, "sessionId": "s1"})
        r = self.launch()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn("orchestrator", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_resume_refuses_a_live_session_id_and_any_live_orchestrator(self):
        self.live({"name": "orchestrator", "pid": 4242, "sessionId": "s1"})
        for sid in ("s1", "s2"):
            with self.subTest(sid):
                r = self.launch("--resume", sid)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.live({"name": "other", "pid": 9, "sessionId": "s3"})
        self.assertEqual(self.launch("--resume", "s3").returncode, 2)

    def test_resume_of_a_stopped_session_runs(self):
        self.live({"name": "orchestrator", "pid": None, "sessionId": "s1"})
        r = self.launch("--resume", "s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:--resume\nARG:s1\n", r.stdout)

    def test_agents_listing_that_cannot_be_trusted_fails_closed(self):
        for body in ("not json", '{"agents": []}', "3"):
            with self.subTest(body):
                (self.tmp / "agents.json").write_text(body)
                r = self.launch()
                self.assertEqual(r.returncode, 2)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertEqual(r.stdout, "")
        script = self.bin / "claude"
        script.write_text('#!/bin/sh\nif [ "$1" = agents ]; then exit 1; fi\necho ran\n')
        r = self.launch()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("ran", r.stdout)

    def test_lead_checks_for_a_live_lead_only(self):
        self.live({"name": "orchestrator", "pid": 1, "sessionId": "s1"})
        self.assertEqual(self.launch("--lead").returncode, 0)
        self.live({"name": "lead", "pid": 2, "sessionId": "s2"})
        self.assertEqual(self.launch("--lead").returncode, 2)

    def test_lead_names_its_model_from_the_lead_row_only(self):
        self.assertNotIn("--model", self.launch("--print", "--lead").stdout)  # no Lead row value: nothing passed
        self.set_model("sonnet", lead="opus (used when scripts/launch --lead starts it)")
        out = self.launch("--print", "--lead").stdout.strip()
        self.assertEqual(
            out, "claude --name lead --settings '{\"crossSessionInbound\":\"accept\"}' --model opus "
                 f"--plugin-dir {self.snap} \"/orchestrate:lead\"")
        self.set_model("sonnet", lead="--dangerously-skip-permissions")
        self.assertEqual(self.launch("--print", "--lead").returncode, 2)

    def test_lead_passes_greedy_a_ticket_and_profile_on(self):
        for args, tail in ((["--greedy=4", "--profile"], '"/orchestrate:lead --greedy=4 --profile"'),
                           (["--greedy"], '"/orchestrate:lead --greedy"'),
                           (["403"], '"/orchestrate:lead 403"'),
                           (["403", "--profile"], '"/orchestrate:lead 403 --profile"'),
                           (["--profile"], '"/orchestrate:lead --profile"')):
            with self.subTest(args):
                r = self.launch("--print", "--lead", *args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(r.stdout.strip().endswith(tail), r.stdout)
        self.assertEqual(self.launch("--print", "--lead", "--greedy=4", "403").returncode, 2)

    def test_profile_is_appended_to_the_run_prompt(self):
        self.assertTrue(self.launch("--print", "--greedy=4", "--profile").stdout.strip()
                        .endswith('"/orchestrate:run --greedy=4 --profile"'))
        self.assertTrue(self.launch("--print", "403", "--profile").stdout.strip().endswith('"/orchestrate:run 403 --profile"'))
        self.assertEqual(self.launch("--print", "--resume", "s1", "--profile").returncode, 2)

    def test_lead_refuses_flags_that_would_be_dropped(self):
        for extra in (["--bg"], ["--resume", "s1"], ["--resume-latest"], ["--no-model"]):
            with self.subTest(extra):
                self.assertEqual(self.launch("--print", "--lead", *extra).returncode, 2)

    def test_lead_passes_the_permission_mode(self):
        out = self.launch("--print", "--lead", "--permission-mode", "plan").stdout
        self.assertIn("--permission-mode plan", out)

    def test_unknown_permission_mode_is_refused(self):
        self.assertNotEqual(self.launch("--print", "--permission-mode", "yolo").returncode, 0)

    def test_values_that_could_be_flags_or_non_ascii_digits_are_refused(self):
        for args in (["--resume=-x"], ["²"], ["--greedy=²"], ["--resume", "a b"]):
            with self.subTest(args):
                self.assertEqual(self.launch("--print", *args).returncode, 2)
        self.set_model("--dangerously-skip-permissions")
        self.assertEqual(self.launch("--print").returncode, 2)
        self.assertEqual(self.launch("--print", "--no-model").returncode, 0)

    def test_stopped_row_with_no_pid_key_does_not_block_a_launch(self):
        self.live({"name": "orchestrator", "sessionId": "s1", "state": "idle"})
        self.assertEqual(self.launch().returncode, 0)

    def test_stopped_row_does_not_block_a_launch(self):
        self.live({"name": "orchestrator", "pid": None, "sessionId": "s1"}, {"name": "lead", "pid": 7, "sessionId": "s2"})
        r = self.launch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:/orchestrate:run --greedy\n", r.stdout)

    def test_bg_passes_the_claude_output_through(self):
        r = self.launch("--bg")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:--bg\n", r.stdout)


    def stopped(self, sid, cwd, started=None, **extra):
        row = {"name": "orchestrator", "pid": None, "sessionId": sid, "cwd": str(cwd)}
        if started is not None:
            row["startedAt"] = started
        return dict(row, **extra)

    def test_resume_latest_picks_the_newest_stopped_row_of_this_repo(self):
        other = self.tmp / "other"
        self.live(self.stopped("old-here", self.repo, "2026-11-01T10:00:00Z"),
                  self.stopped("newer-elsewhere", other, "2026-11-08T10:00:00Z"),
                  self.stopped("new-here", self.repo, "2026-11-05T10:00:00Z"))
        r = self.launch("--print", "--resume-latest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude --resume new-here --name orchestrator", r.stdout)
        self.assertEqual(len(r.stderr.strip().splitlines()), 2, r.stderr)  # the choice, then the snapshot line
        self.assertIn("new-here", r.stderr)
        self.assertIn("newest startedAt", r.stderr)

    def test_resume_latest_falls_back_to_the_row_listed_last(self):
        self.live(self.stopped("a", self.repo, "2026-11-05T10:00:00Z"), self.stopped("b", self.repo, "2026-11-05T10:00:00Z"))
        self.assertIn("--resume b ", self.launch("--print", "--resume-latest").stdout)
        self.live(self.stopped("a", self.repo), self.stopped("b", self.repo))
        self.assertIn("--resume b ", self.launch("--print", "--resume-latest").stdout)
        # a mix of dated and undated rows: the undated one makes the order unknowable, so the last listed wins
        self.live(self.stopped("a", self.repo, 1762000000000), self.stopped("b", self.repo))
        self.assertIn("--resume b ", self.launch("--print", "--resume-latest").stdout)
        self.live(self.stopped("a", self.repo), self.stopped("b", self.repo, 1762000000000),
                  self.stopped("c", self.repo, 1762000001000))
        self.assertIn("--resume c ", self.launch("--print", "--resume-latest").stdout)
        self.live(self.stopped("a", self.repo, 1762000001000), self.stopped("b", self.repo, 1762000000000),
                  self.stopped("c", self.repo))
        self.assertIn("--resume c ", self.launch("--print", "--resume-latest").stdout)

    def test_resume_latest_refuses_a_live_orchestrator_and_no_candidate(self):
        self.live(self.stopped("a", self.repo, "2026-11-05T10:00:00Z"),
                  {"name": "orchestrator", "pid": 5, "sessionId": "live", "cwd": str(self.repo)})
        r = self.launch("--print", "--resume-latest")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.live(self.stopped("x", self.tmp / "other", "2026-11-05T10:00:00Z"))
        self.assertEqual(self.launch("--print", "--resume-latest").returncode, 2)
        self.assertEqual(self.launch("--print", "--resume-latest", "--resume", "s1").returncode, 2)
        self.assertEqual(self.launch("--print", "--resume-latest", "--greedy=2").returncode, 2)

    def test_resume_latest_runs_the_chosen_session(self):
        self.live(self.stopped("a", self.repo, "2026-11-05T10:00:00Z"))
        r = self.launch("--resume-latest", "--bg")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:--resume\nARG:a\n", r.stdout)


if __name__ == "__main__":
    unittest.main()
