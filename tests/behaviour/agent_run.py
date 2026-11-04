"""Headless agent harness for the behavioural tier (opt-in: ORCH_BEHAVIOUR=1, costs money).

Runs `claude -p --agent orchestrate:<role>` in a temp repo (tests/harness.py: bare `origin`,
fake `gh` first on PATH), parses the stream-json events, and tallies each case over several
runs. The command line is the one spike #111 settled. Never reaches the real GitHub: `gh` is the
fake, and the working directory is the temp repo.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import ScriptEnv, git  # noqa: E402,F401

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
STUB_PR = PLUGIN_ROOT / "tests" / "fixtures" / "stub-pr"
BRIEFS = PLUGIN_ROOT / "tests" / "fixtures" / "briefs"

RUNS = 3
MAX_BUDGET_USD = 1
MAX_TURNS = 40
TIMEOUT_S = 900

ENABLED = os.environ.get("ORCH_BEHAVIOUR") == "1"


def command(role, brief, with_stub_pr=True):
    """The `claude` command line (spike #111), without the environment."""
    cmd = ["claude", "-p", "--agent", "orchestrate:" + role, "--plugin-dir", str(PLUGIN_ROOT)]
    if with_stub_pr:
        cmd += ["--plugin-dir", str(STUB_PR)]
    cmd += [
        # Keep the user's own settings, MCP servers and permission allowlist out of the run.
        "--setting-sources", "project,local",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        # Headless `default` mode would block Bash before the guard sees it; the guard still denies.
        "--allowedTools", "Bash",
        "--output-format", "stream-json", "--verbose",
        "--max-turns", str(MAX_TURNS), "--max-budget-usd", str(MAX_BUDGET_USD),
        brief,
    ]
    return cmd


def parse_events(text):
    events = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


class Run:
    """One headless run's event stream, with assertion helpers."""

    def __init__(self, events, env=None, returncode=0, stderr=""):
        self.events = events
        self.env = env
        self.returncode = returncode
        self.stderr = stderr

    def tool_calls(self, name=None):
        """[(name, input)] in order."""
        calls = []
        for e in self.events:
            if e.get("type") != "assistant":
                continue
            for block in e.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and name in (None, block.get("name")):
                    calls.append((block["name"], block.get("input", {})))
        return calls

    def bash_commands(self):
        return [i.get("command", "") for _, i in self.tool_calls("Bash")]

    def gh_calls(self):
        """Argument lists the fake gh recorded."""
        return self.env.gh_calls() if self.env else []

    @property
    def result(self):
        for e in reversed(self.events):
            if e.get("type") == "result":
                return e
        return {}

    @property
    def final_report(self):
        return self.result.get("result") or ""

    @property
    def cost(self):
        return float(self.result.get("total_cost_usd") or 0)

    @property
    def permission_denials(self):
        return self.result.get("permission_denials") or []

    def usable(self):
        """False when the run never produced a result (no auth, claude missing, crash)."""
        return bool(self.result)


def run_agent(role, brief, env, with_stub_pr=True):
    """Run the agent headless in env's repo; the env's fake gh goes first on PATH."""
    child_env = dict(
        os.environ,
        PATH=f"{env.bin}{os.pathsep}{os.environ['PATH']}",
        FAKE_GH_STATE=str(env.gh_state),
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        ENABLE_CLAUDEAI_MCP_SERVERS="false",
    )
    try:
        p = subprocess.run(
            command(role, brief, with_stub_pr), cwd=env.repo, env=child_env,
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except FileNotFoundError:
        return Run([], env, 127, "claude not found on PATH")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout
        return Run(parse_events(out), env, 124, "timeout")
    return Run(parse_events(p.stdout), env, p.returncode, p.stderr)


def verdict(passes, runs):
    """3/3 pass, 2/3 flaky, less fail (scaled to `runs`)."""
    if passes == runs:
        return "pass"
    return "flaky" if passes == runs - 1 and passes >= 2 else "fail"


TABLE = []  # (case, verdict, passes, runs, cost)


def run_case(name, build, check, runs=RUNS):
    """Run a case `runs` times. build() -> (env, role, brief, with_stub_pr); check(run) raises AssertionError on a miss.

    Returns (verdict, passes, cost, failures). Raises RuntimeError at once when a run
    produced no result at all: an environment problem, which retrying won't fix.
    """
    passes, cost, failures = 0, 0.0, []
    for i in range(runs):
        env, role, brief, with_stub = build()
        try:
            run = run_agent(role, brief, env, with_stub)
            cost += run.cost
            if not run.usable():
                raise RuntimeError("no result event (exit %s): %s" % (run.returncode, run.stderr.strip()[-500:]))
            try:
                check(run)
                passes += 1
            except AssertionError as e:
                failures.append("run %d: %s" % (i + 1, e))
        finally:
            env.close()
    v = verdict(passes, runs)
    TABLE.append((name, v, passes, runs, cost))
    return v, passes, cost, failures


def format_table(rows=None):
    rows = TABLE if rows is None else rows
    lines = ["%-32s %-6s %-5s %s" % ("case", "result", "runs", "cost")]
    for name, v, passes, runs, cost in rows:
        lines.append("%-32s %-6s %-5s $%.2f" % (name, v, "%d/%d" % (passes, runs), cost))
    lines.append("%-32s %-6s %-5s $%.2f" % ("total", "", "", sum(r[4] for r in rows)))
    return "\n".join(lines)
