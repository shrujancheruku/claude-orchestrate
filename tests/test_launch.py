"""scripts/launch through its CLI: the orchestrator's launch and resume line, with a fake `claude` on PATH (#403)."""

import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / "scripts" / "launch"


def settings_arg(snap, auto=True, inbound=False):
    """The --settings argument as launch prints it: single-quoted compact JSON, the snapshot read rule last (#617)."""
    body = {}
    if auto:
        body["disableAutoMode"] = "disable"
    if inbound:
        body["crossSessionInbound"] = "accept"
    if auto:  # the orchestrator's lines carry the role in the settings env too (#628); the lead's never do
        body["env"] = {"ORCHESTRATE_ROLE": "orchestrator"}
    body["permissions"] = {"allow": [f"Read(/{snap}/**)"]}
    return "'" + json.dumps(body, separators=(",", ":")) + "'"

CONFIG = """# Orchestration config

## Models

| Role | Model | Effort |
|---|---|---|
| Lead | %s | |
| Orchestrator | %s | |
| Worker | sonnet | medium |
"""


class SnapshotCoverage(unittest.TestCase):
    """The snapshot carries every plugin-root directory a script or prompt reads (#628)."""

    # Development tools never run inside a session: guard-corpus reads hooks/ and writes into tests/.
    DEV_ONLY = {"guard-corpus"}
    FROM_ROOT = re.compile(r'\.parent\.parent\s*/\s*"([A-Za-z._-]+)"')
    PLUGIN_REF = re.compile(r'CLAUDE_PLUGIN_ROOT\}?/([A-Za-z._-]+)|CLAUDE_SKILL_DIR\}/\.\./\.\./([A-Za-z._-]+)')

    def test_every_plugin_root_directory_read_is_in_snapshot_dirs(self):
        loader = importlib.machinery.SourceFileLoader("launch_mod", str(LAUNCH))
        spec = importlib.util.spec_from_loader("launch_mod", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        found = {}
        for sub in ("scripts", "skills", "agents", "hooks"):
            for path in sorted((ROOT / sub).rglob("*")):
                if not path.is_file() or path.name in self.DEV_ONLY or "__pycache__" in path.parts:
                    continue
                try:
                    text = path.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                for m in self.FROM_ROOT.finditer(text):
                    found.setdefault(m.group(1), path)
                for m in self.PLUGIN_REF.finditer(text):
                    found.setdefault(m.group(1) or m.group(2), path)
        self.assertIn("viewer", found)  # the scan sees profile.py's read: it would miss new gaps otherwise
        missing = {d: str(p.relative_to(ROOT)) for d, p in found.items() if d not in mod.SNAPSHOT_DIRS}
        self.assertEqual(missing, {}, "read from the plugin root but absent from launch's SNAPSHOT_DIRS")


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
        for d in (".claude-plugin", "hooks", "agents", "skills", "scripts", "viewer"):
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
            f"ORCHESTRATE_ROLE=orchestrator claude --name orchestrator --permission-mode acceptEdits --settings {settings_arg(self.snap)} --model opus "
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
        for d in (".claude-plugin", "hooks", "agents", "skills", "scripts", "viewer"):
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
        self.assertTrue(out.startswith("ORCHESTRATE_ROLE=orchestrator claude --bg --name orchestrator "), out)
        self.assertIn(settings_arg(self.snap, inbound=True), out)

    def test_bypass_permissions_accepts_inbound_messages(self):
        out = self.launch("--print", "--permission-mode", "bypassPermissions").stdout
        self.assertIn("--permission-mode bypassPermissions", out)
        self.assertIn(settings_arg(self.snap, inbound=True), out)

    def settings_of(self, *args):
        """The parsed --settings JSON of a printed line."""
        out = self.launch("--print", *args)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(shlex.split(out.stdout)[shlex.split(out.stdout).index("--settings") + 1])

    def test_every_line_allows_reads_of_exactly_the_snapshot(self):
        want = [f"Read(/{self.snap}/**)"]  # `//` + the path without its leading slash
        self.assertTrue(want[0].startswith("Read(//"))
        for mode in [(), ("--bg",), ("--lead",), ("--resume", "abc"), ("--permission-mode", "bypassPermissions")]:
            s = self.settings_of(*mode)
            self.assertEqual(s["permissions"], {"allow": want}, mode)
            if "--lead" not in mode:
                self.assertEqual(s["disableAutoMode"], "disable", mode)
            if mode in [("--bg",), ("--lead",), ("--permission-mode", "bypassPermissions")]:
                self.assertEqual(s["crossSessionInbound"], "accept", mode)

    def test_settings_carry_no_edit_write_or_bash_rule(self):
        for mode in [(), ("--bg",), ("--lead",), ("--resume", "abc")]:
            perms = self.settings_of(*mode)["permissions"]
            self.assertEqual(list(perms), ["allow"], mode)
            for rule in perms["allow"]:
                self.assertTrue(rule.startswith("Read("), rule)
        self.assertNotIn("--add-dir", self.launch("--print").stdout)

    def test_marketplace_install_has_no_read_rule(self):
        root = self.claude_dir / "plugins" / "market"
        self.assertNotIn("permissions", self.launch("--print", root=root).stdout)

    def test_resume_keeps_the_flags_and_drops_the_prompt(self):
        out = self.launch("--print", "--resume", "abc-123").stdout.strip()
        self.assertEqual(
            out,
            f"ORCHESTRATE_ROLE=orchestrator claude --resume abc-123 --name orchestrator --permission-mode acceptEdits --settings {settings_arg(self.snap)} "
            f"--model opus --plugin-dir {self.snap}")
        self.assertEqual(self.launch("--print", "--resume", "abc", "--greedy=2").returncode, 2)

    def test_lead_line_accepts_inbound_messages(self):
        out = self.launch("--print", "--lead").stdout.strip()
        self.assertEqual(
            out, f"claude --name lead --settings {settings_arg(self.snap, auto=False, inbound=True)} "
                 f"--plugin-dir {self.snap} \"/orchestrate:lead\"")

    def test_the_role_variable_is_set_on_the_orchestrator_lines_and_never_on_the_lead(self):
        # The guard tells the orchestrator from the lead by ORCHESTRATE_ROLE, so the environment `claude` is started
        # with must carry it on every orchestrator line and never on the lead's (#579). The fake `claude` prints it.
        script = self.bin / "claude"
        script.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = agents ]; then cat {self.tmp}/agents.json; exit 0; fi\n'
            'echo "ROLE:${ORCHESTRATE_ROLE-unset}"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.tmp / "agents.json").write_text(json.dumps([self.bg_row()]))
        env_free = self.launch("--lead").stdout  # not an orchestrator line
        self.assertEqual(env_free.strip(), "ROLE:unset")
        with mock.patch.dict(os.environ, {"ORCHESTRATE_ROLE": "orchestrator"}):  # inherited from the caller's shell
            self.assertEqual(self.launch("--lead").stdout.strip(), "ROLE:unset")
        for args in (["--greedy=2"], ["403"], ["--bg"], ["--resume", "b1"]):
            with self.subTest(args):
                if args[0] == "--bg":  # a live orchestrator row would refuse it
                    (self.tmp / "agents.json").write_text("[]")
                if args[0] == "--resume":
                    (self.tmp / "agents.json").write_text(json.dumps([self.bg_row()]))
                r = self.launch(*args)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, "ROLE:orchestrator"), r.stderr)
        (self.tmp / "agents.json").write_text(json.dumps([self.stopped("s9", self.repo, "2026-11-05T10:00:00Z")]))
        r = self.launch("--resume-latest")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ROLE:orchestrator"), r.stderr)

    def test_the_role_rides_in_the_settings_env_of_every_orchestrator_line_and_never_the_lead(self):
        # `claude --bg` hands the session to a pre-spawned daemon that never sees the line's own variable (#628).
        for mode in [(), ("--bg",), ("--resume", "abc"), ("--permission-mode", "bypassPermissions")]:
            self.assertEqual(self.settings_of(*mode)["env"], {"ORCHESTRATE_ROLE": "orchestrator"}, mode)
        self.assertNotIn("env", self.settings_of("--lead"))

    def test_launch_from_a_snapshot_reuses_it_instead_of_refusing(self):
        self.assertEqual(self.launch("--print").returncode, 0)  # exports the snapshot
        (self.snap / "marker").write_text("kept")
        for mode in [("--bg", "--greedy=4"), ("--lead",), ()]:
            r = self.launch("--print", *mode, root=self.snap)
            self.assertEqual(r.returncode, 0, (mode, r.stderr))
            self.assertIn(f"--plugin-dir {self.snap} ", r.stdout + " ", mode)
            self.assertIn(f"plugin snapshot {self.sha[:12]}", r.stderr)
        r = self.launch("--bg", "--greedy=4", root=self.snap)  # a real launch from a snapshot
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ARG:--plugin-dir\nARG:{self.snap}\n", r.stdout)
        self.assertTrue((self.snap / "marker").exists())
        self.assertTrue((self.snap / "viewer" / "f.txt").exists())

    def test_launch_from_an_old_snapshot_exports_the_newer_origin(self):
        self.assertEqual(self.launch("--print").returncode, 0)  # snapshot of v1, with its .source
        newer = self.commit_plugin("v2")
        r = self.launch("--bg", "--greedy=4", root=self.snap)
        self.assertEqual(r.returncode, 0, r.stderr)
        new_snap = self.cache / "orchestrate" / "plugin" / newer
        self.assertIn(f"ARG:--plugin-dir\nARG:{new_snap}\n", r.stdout)
        self.assertEqual((new_snap / "hooks" / "f.txt").read_text(), "v2")

    def test_launch_from_a_snapshot_without_a_usable_source_reuses_it(self):
        self.assertEqual(self.launch("--print").returncode, 0)
        self.commit_plugin("v2")
        (self.snap / ".source").unlink()  # a snapshot exported before the file existed
        r = self.launch("--bg", "--greedy=4", root=self.snap)
        self.assertIn(f"ARG:--plugin-dir\nARG:{self.snap}\n", r.stdout)
        (self.snap / ".source").write_text(str(self.tmp / "gone") + "\n")  # its checkout is gone
        r = self.launch("--bg", "--greedy=4", root=self.snap)
        self.assertIn(f"ARG:--plugin-dir\nARG:{self.snap}\n", r.stdout)

    def test_a_directory_in_the_cache_that_is_not_a_sha_is_still_refused(self):
        odd = self.cache / "orchestrate" / "plugin" / "not-a-sha"
        odd.mkdir(parents=True)
        self.assertEqual(self.launch("--print", root=odd).returncode, 2)

    def loaded_snapshot(self, sid, sha):
        """A saved transcript that names the snapshot the session loaded: agent rows carry no plugin dir (#628)."""
        d = self.claude_dir / "projects" / "-repo"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"type": "attachment", "text": f"loaded plugin from {self.cache}/orchestrate/plugin/{sha}/skills/run"}
        (d / f"{sid}.jsonl").write_text(json.dumps(rec) + "\n")

    def test_a_bg_resume_says_when_its_snapshot_is_older_than_origin(self):
        self.assertEqual(self.launch("--print").returncode, 0)  # snapshot of v1
        self.loaded_snapshot("b1", self.sha)
        newer = self.commit_plugin("v2")
        self.live(self.bg_row())
        r = self.launch("--resume", "b1", "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("resume keeps snapshot"), 0)  # --print launches nothing
        self.live(self.bg_row())
        r = self.launch("--resume", "b1")  # from the checkout: the snapshot comes from the transcript, not the runtime root
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"launch: resume keeps snapshot {self.sha[:12]}; origin/main is {newer[:12]}: a fresh launch picks it up", r.stderr)

    def test_a_bg_resume_is_quiet_when_the_plugin_tree_is_unchanged(self):
        self.assertEqual(self.launch("--print").returncode, 0)
        (self.checkout / "README.md").write_text("docs only")
        self.git(self.checkout, "commit", "-qam", "docs")
        self.git(self.checkout, "push", "-q", "origin", "HEAD:main")
        self.git(self.checkout, "fetch", "-q", "origin")
        self.loaded_snapshot("b1", self.sha)
        self.live(self.bg_row())
        r = self.launch("--resume", "b1", root=self.snap)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("resume keeps snapshot", r.stderr)

    def test_a_bg_resume_with_no_snapshot_in_its_transcript_says_so_in_one_line(self):
        self.assertEqual(self.launch("--print").returncode, 0)
        self.live(self.bg_row())  # no transcript at all
        r = self.launch("--resume", "b1")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stderr.splitlines() if "cannot tell which snapshot" in l]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn("cannot tell which snapshot b1 keeps", lines[0])
        self.live(self.bg_row())  # a marketplace install has no snapshot to be stale
        r = self.launch("--resume", "b1", root=self.claude_dir / "plugins" / "market")
        self.assertNotIn("cannot tell which snapshot", r.stderr)

    def test_the_printed_orchestrator_line_carries_the_variable_and_the_lead_line_does_not(self):
        self.assertTrue(self.launch("--print").stdout.startswith("ORCHESTRATE_ROLE=orchestrator claude "))
        self.assertNotIn("ORCHESTRATE_ROLE", self.launch("--print", "--lead").stdout)

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

    def resume_events(self):
        path = self.repo / ".claude" / "orchestrate-profiles" / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_resume_appends_one_resume_event_for_the_session(self):
        self.live({"name": "orchestrator", "pid": None, "sessionId": "s1"})
        self.assertEqual(self.launch("--resume", "s1").returncode, 0)
        (event,) = self.resume_events()
        self.assertEqual((event["kind"], event["outcome"], event["session"]), ("resume", "resumed", "s1"))

    def test_no_resume_event_for_a_fresh_launch_a_print_or_a_refused_resume(self):
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.launch("--print", "--resume", "s1").returncode, 0)
        self.live({"name": "orchestrator", "pid": 4242, "sessionId": "s1"})
        self.assertEqual(self.launch("--resume", "s1").returncode, 2)
        self.assertEqual(self.resume_events(), [])

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
            out, f"claude --name lead --settings {settings_arg(self.snap, auto=False, inbound=True)} --model opus "
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

    # A background row keeps its saved options: flags would fork an idle copy (#555).
    def bg_row(self, sid="b1", **extra):
        return {"name": "orchestrator", "pid": None, "sessionId": sid, "kind": "background", "cwd": str(self.repo), **extra}

    def transcript(self, sid, *records):
        d = self.claude_dir / "projects" / "-repo"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    def user(self, text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def test_background_resume_passes_only_the_resume_and_bg(self):
        self.live(self.bg_row())
        self.transcript("b1", self.user("hello"), self.user("/orchestrate:run 533 --profile"))
        out = self.launch("--print", "--resume", "b1").stdout.strip()
        self.assertEqual(out, 'ORCHESTRATE_ROLE=orchestrator claude --resume b1 --bg "/orchestrate:run 533 --profile"')
        r = self.launch("--resume-latest")  # --bg is added though not given, and the fake claude echoes the argv
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "ARG:--resume\nARG:b1\nARG:--bg\nARG:/orchestrate:run 533 --profile\n")
        self.assertNotIn("plugin snapshot", r.stderr)

    def test_background_resume_reads_a_slash_command_record(self):
        self.live(self.bg_row())
        self.transcript("b1", self.user("<command-name>/orchestrate:run</command-name>\n<command-args>--greedy=4</command-args>"))
        self.assertTrue(self.launch("--print", "--resume", "b1").stdout.strip().endswith('"/orchestrate:run --greedy=4"'))

    def test_background_resume_without_a_transcript_builds_the_prompt_from_tokens(self):
        self.live(self.bg_row())
        out = self.launch("--print", "--resume", "b1", "--greedy=4", "--profile").stdout.strip()
        self.assertEqual(out, 'ORCHESTRATE_ROLE=orchestrator claude --resume b1 --bg "/orchestrate:run --greedy=4 --profile"')
        self.assertEqual(self.launch("--print", "--resume", "b1", "403").stdout.strip(),
                         'ORCHESTRATE_ROLE=orchestrator claude --resume b1 --bg "/orchestrate:run 403"')

    def test_background_resume_with_no_prompt_prints_the_attach_line(self):
        self.live(self.bg_row())
        r = self.launch("--print", "--resume", "b1")
        self.assertEqual(r.stdout.strip(), "ORCHESTRATE_ROLE=orchestrator claude --resume b1 --bg")
        self.assertIn("claude attach b1", r.stderr)
        self.assertIn("/orchestrate:run", r.stderr)
        self.assertNotIn("--profile", r.stderr)
        r = self.launch("--print", "--resume", "b1", "--profile")
        self.assertIn("/orchestrate:run --greedy --profile", r.stderr)

    def test_background_resume_refuses_a_permission_mode_that_differs_or_is_unreadable(self):
        self.live(self.bg_row())
        self.transcript("b1", {"type": "user", "permissionMode": "acceptEdits", "message": {"content": "x"}})
        for sid, mode in (("b1", "bypassPermissions"), ("nope", "acceptEdits")):
            with self.subTest(sid):
                self.live(self.bg_row(sid))
                r = self.launch("--print", "--resume", sid, "--permission-mode", mode)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertIn("saved options", r.stderr)
        self.live(self.bg_row())
        self.assertEqual(self.launch("--print", "--resume", "b1", "--permission-mode", "acceptEdits").returncode, 0)

    def test_interactive_resume_keeps_the_flags_and_takes_no_prompt(self):
        self.live(self.bg_row("i1", kind="interactive"))
        out = self.launch("--print", "--resume", "i1").stdout.strip()
        self.assertIn("--name orchestrator --permission-mode acceptEdits", out)
        self.assertIn(f"--plugin-dir {self.snap}", out)
        self.assertEqual(self.launch("--print", "--resume", "i1", "--greedy=2").returncode, 2)


if __name__ == "__main__":
    unittest.main()
