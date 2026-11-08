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
        self.checkout = self.tmp / "checkout"
        self.checkout.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.agents = []
        self.set_model("opus (used when the lead launches it)")
        self.fake_claude()

    def set_model(self, cell):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "orchestration.md").write_text(CONFIG % cell)

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
                   CLAUDE_CONFIG_DIR=str(self.claude_dir),
                   CLAUDE_PLUGIN_ROOT=str(root or self.checkout))
        return subprocess.run([str(LAUNCH), "--repo", str(self.repo), *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_checkout_prints_the_whole_line(self):
        r = self.launch("--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.strip(),
            f"claude --name orchestrator --permission-mode acceptEdits --settings {SETTINGS} --model opus "
            f"--plugin-dir {self.checkout} \"/orchestrate:run --greedy\"")

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
            f"--model opus --plugin-dir {self.checkout}")
        self.assertEqual(self.launch("--print", "--resume", "abc", "--greedy=2").returncode, 2)

    def test_lead_line_accepts_inbound_messages(self):
        out = self.launch("--print", "--lead").stdout.strip()
        self.assertEqual(
            out, "claude --name lead --settings '{\"crossSessionInbound\":\"accept\"}' "
                 f"--plugin-dir {self.checkout} \"/orchestrate:lead\"")

    def test_live_orchestrator_refuses_a_plain_launch(self):
        self.live({"name": "orchestrator", "pid": 4242, "sessionId": "s1"})
        r = self.launch()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn("orchestrator", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_resume_naming_the_live_session_is_allowed_but_another_is_not(self):
        self.live({"name": "orchestrator", "pid": 4242, "sessionId": "s1"})
        ok = self.launch("--resume", "s1")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("ARG:--resume\nARG:s1\n", ok.stdout)
        self.assertEqual(self.launch("--resume", "s2").returncode, 2)

    def test_stopped_row_does_not_block_a_launch(self):
        self.live({"name": "orchestrator", "pid": None, "sessionId": "s1"}, {"name": "lead", "pid": 7, "sessionId": "s2"})
        r = self.launch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:/orchestrate:run --greedy\n", r.stdout)

    def test_bg_passes_the_claude_output_through(self):
        r = self.launch("--bg")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARG:--bg\n", r.stdout)


if __name__ == "__main__":
    unittest.main()
