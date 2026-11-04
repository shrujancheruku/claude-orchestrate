"""Deterministic (CI, free): the behavioural harness's parsing, verdicts and command line. Spawns no agent."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "behaviour"))
import agent_run  # noqa: E402

STREAM = "\n".join(json.dumps(e) for e in [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "gh issue view 1"}},
    ]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}},
    {"type": "result", "result": "status: stopped", "total_cost_usd": 0.05, "permission_denials": []},
]) + "\nnot json\n"


class Runner(unittest.TestCase):
    def test_parse_and_helpers(self):
        run = agent_run.Run(agent_run.parse_events(STREAM))
        self.assertEqual([n for n, _ in run.tool_calls()], ["Bash", "Read"])
        self.assertEqual(run.bash_commands(), ["gh issue view 1"])
        self.assertEqual(run.final_report, "status: stopped")
        self.assertAlmostEqual(run.cost, 0.05)
        self.assertTrue(run.usable())
        self.assertFalse(agent_run.Run([]).usable())

    def test_verdict(self):
        self.assertEqual([agent_run.verdict(n, 3) for n in (3, 2, 1, 0)], ["pass", "flaky", "fail", "fail"])

    def test_command_caps_and_stub(self):
        cmd = agent_run.command("worker", "brief", "/p", "/s")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "1")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "40")
        self.assertEqual(cmd.count("--plugin-dir"), 2)
        self.assertEqual(agent_run.command("worker", "brief", "/p").count("--plugin-dir"), 1)

    def test_env_is_scrubbed(self):
        from harness import ScriptEnv
        env = ScriptEnv()
        self.addCleanup(env.close)
        dirty = {k: "x" for k in agent_run.SCRUBBED_ENV}
        dirty.update(GIT_CONFIG_KEY_0="credential.helper", GH_FUTURE_SETTING="x", GITHUB_FUTURE="x", PATH="/usr/bin", HOME="/h")
        out = agent_run.child_env(env, dirty)
        allowed = {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GH_CONFIG_DIR", "GIT_SSH_COMMAND"}
        for k in out:
            if k.startswith(("GH_", "GITHUB_", "GIT_CONFIG_")):
                self.assertIn(k, allowed)
        for k in agent_run.SCRUBBED_ENV:
            if k not in allowed:
                self.assertNotIn(k, out)
        self.assertTrue(Path(out["GH_CONFIG_DIR"]).is_dir())
        self.assertEqual(list(Path(out["GH_CONFIG_DIR"]).iterdir()), [])
        self.assertEqual(out["GIT_SSH_COMMAND"], "false")
        self.assertEqual(out["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(Path(out["GIT_CONFIG_GLOBAL"]).read_text(), "")
        self.assertTrue(out["PATH"].startswith(str(env.bin)))

    def test_command_holds_no_real_checkout_path(self):
        from harness import ScriptEnv
        env = ScriptEnv()
        self.addCleanup(env.close)
        plugin, stub = agent_run.copy_plugins(Path(env.repo).parent / "plugins")
        cmd = agent_run.command("worker", "brief", plugin, stub)
        root = str(agent_run.PLUGIN_ROOT)
        self.assertEqual([c for c in cmd if root in c], [])
        self.assertTrue((plugin / "agents" / "worker.md").exists())
        self.assertTrue((plugin / ".claude-plugin" / "plugin.json").exists())
        self.assertTrue((stub / "skills" / "pr" / "SKILL.md").exists())

    def test_table(self):
        out = agent_run.format_table([("a", "pass", 3, 3, 0.1), ("b", "flaky", 2, 3, 0.2)])
        self.assertIn("flaky", out)
        self.assertIn("$0.30", out)

    def test_ci_never_enables_the_tier(self):
        wf = Path(__file__).resolve().parent.parent / ".github" / "workflows"
        for f in list(wf.glob("*.yml")) + list(wf.glob("*.yaml")):
            self.assertNotIn("ORCH_BEHAVIOUR", f.read_text())


if __name__ == "__main__":
    unittest.main()
