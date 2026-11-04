"""Headless agent harness for the behavioural tier (opt-in: ORCH_BEHAVIOUR=1, costs money).

Runs `claude -p --agent orchestrate:<role>` in a temp repo (tests/harness.py: bare `origin`,
fake `gh` first on PATH), parses the stream-json events, and tallies each case over several
runs. The command line is the one spike #111 settled. Never reaches the real GitHub: `gh` is the
fake, and the working directory is the temp repo.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import ScriptEnv, git  # noqa: E402,F401

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
STUB_PR = PLUGIN_ROOT / "tests" / "fixtures" / "stub-pr"
BRIEFS = PLUGIN_ROOT / "tests" / "fixtures" / "briefs"

def _runs():
    """ORCH_BEHAVIOUR_RUNS (1..3) lowers the runs per case while iterating; read only when the tier is on."""
    if os.environ.get("ORCH_BEHAVIOUR") != "1":
        return 3
    try:
        return max(1, min(3, int(os.environ.get("ORCH_BEHAVIOUR_RUNS", "3"))))
    except ValueError:
        return 3


RUNS = _runs()
MAX_BUDGET_USD = 1
MAX_TURNS = 40
TIMEOUT_S = 900

ENABLED = os.environ.get("ORCH_BEHAVIOUR") == "1"


# What a worker needs from the plugin. Copied out of the checkout so the agent never sees its path.
PLUGIN_PARTS = ("agents", "skills", "hooks", "scripts", ".claude-plugin")

# A denylist of credentials and config the agent could use to reach the real GitHub or remote (see child_env).
SCRUBBED_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR",
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND", "SSH_AUTH_SOCK", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG",
)


def copy_plugins(dest, with_stub_pr=True):
    """Copy the plugin and the stub into dest (outside the temp repo); returns (plugin_dir, stub_dir | None)."""
    dest = Path(dest)
    plugin = dest / "plugin"
    plugin.mkdir(parents=True)
    for part in PLUGIN_PARTS:
        src = PLUGIN_ROOT / part
        if src.is_dir():
            shutil.copytree(src, plugin / part, ignore=shutil.ignore_patterns("__pycache__"))
    stub = None
    if with_stub_pr:
        stub = dest / "stub-pr"
        shutil.copytree(STUB_PR, stub)
    return plugin, stub


def child_env(env, base=None):
    """The agent's environment: the fake gh first on PATH, no credentials, no user git config.

    HOME stays real: a temp HOME was tried, and headless `claude -p` then reports "Not logged in".
    Nothing under ~/.claude is copied or touched.

    This is a denylist, not a sandbox. It removes every GH_*, GITHUB_* and GIT_CONFIG_* variable and the
    other git/ssh credential variables in SCRUBBED_ENV; points GH_CONFIG_DIR and GIT_CONFIG_GLOBAL at empty
    temp locations (so even /usr/bin/gh by full path finds no login and git has no credential helper); and
    makes any ssh transport fail (GIT_SSH_COMMAND=false). What the real HOME still exposes: other tools'
    credentials (cloud CLIs, npm, anything under ~/.ssh read by a program other than git-over-ssh) and
    ~/.claude, which Claude Code needs to log in.
    """
    base = dict(os.environ if base is None else base)
    for k in SCRUBBED_ENV:
        base.pop(k, None)
    for k in [k for k in base if k.startswith(("GIT_CONFIG_", "GH_", "GITHUB_"))]:
        base.pop(k)
    tmp = Path(env.repo).parent
    empty = tmp / "empty-gitconfig"
    empty.write_text("")
    gh_config = tmp / "empty-gh-config"
    gh_config.mkdir(exist_ok=True)
    base.update(
        PATH=f"{env.bin}{os.pathsep}{base.get('PATH', '')}",
        FAKE_GH_STATE=str(env.gh_state),
        GIT_CONFIG_GLOBAL=str(empty),
        GIT_CONFIG_NOSYSTEM="1",
        GH_CONFIG_DIR=str(gh_config),
        GIT_SSH_COMMAND="false",
        GIT_TERMINAL_PROMPT="0",
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        ENABLE_CLAUDEAI_MCP_SERVERS="false",
    )
    return base


def command(role, brief, plugin_dir, stub_dir=None, tools="Bash"):
    """The `claude` command line (spike #111), without the environment.

    role=None runs the main session instead of an agent (for a skill such as /orchestrate:run).
    tools is the --allowedTools list: headless `default` mode blocks any tool not listed (e.g. WebFetch).
    """
    cmd = ["claude", "-p"]
    if role:
        cmd += ["--agent", "orchestrate:" + role]
    cmd += ["--plugin-dir", str(plugin_dir)]
    if stub_dir:
        cmd += ["--plugin-dir", str(stub_dir)]
    cmd += [
        # Keep the user's own settings, MCP servers and permission allowlist out of the run.
        "--setting-sources", "project,local",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        # Headless `default` mode would block Bash before the guard sees it; the guard still denies.
        "--allowedTools", tools,
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


def run_agent(role, brief, env, with_stub_pr=True, tools="Bash"):
    """Run the agent headless in env's repo, with copies of the plugin and the fake gh; never the real checkout."""
    plugin, stub = copy_plugins(Path(env.repo).parent / "plugins", with_stub_pr)
    try:
        p = subprocess.run(
            command(role, brief, plugin, stub, tools), cwd=env.repo, env=child_env(env),
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
    """Run a case `runs` times. build() -> (env, role, brief, with_stub_pr[, tools]); check(run) raises AssertionError on a miss.

    Returns (verdict, passes, cost, failures); each failure keeps the run's report and last tool calls.
    A run with no result at all (no auth, crash) is an environment problem that retrying won't fix:
    the case gets an `error` row with the cost so far, then RuntimeError is raised.
    """
    passes, cost, failures, errored = 0, 0.0, [], False
    try:
        for i in range(runs):
            env, role, brief, with_stub, *rest = build()
            try:
                run = run_agent(role, brief, env, with_stub, *rest)
                cost += run.cost
                if not run.usable():
                    errored = True
                    raise RuntimeError("no result event (exit %s): %s" % (run.returncode, run.stderr.strip()[-500:]))
                try:
                    check(run)
                    passes += 1
                except AssertionError as e:
                    unmatched = env.gh_unmatched()
                    failures.append("run %d: %s\n  gh calls with no canned response: %s\n  report: %s\n  last tool calls: %s" % (
                        i + 1, e, unmatched, run.final_report[:1500], [(n, str(inp)[:200]) for n, inp in run.tool_calls()[-5:]]))
            finally:
                env.close()
    finally:
        TABLE.append((name, "error" if errored else verdict(passes, runs), passes, runs, cost))
    return verdict(passes, runs), passes, cost, failures


def format_table(rows=None):
    rows = TABLE if rows is None else rows
    lines = ["%-32s %-6s %-5s %s" % ("case", "result", "runs", "cost")]
    for name, v, passes, runs, cost in rows:
        lines.append("%-32s %-6s %-5s $%.2f" % (name, v, "%d/%d" % (passes, runs), cost))
    lines.append("%-32s %-6s %-5s $%.2f" % ("total", "", "", sum(r[4] for r in rows)))
    return "\n".join(lines)
