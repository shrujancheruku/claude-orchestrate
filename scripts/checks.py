"""Check runner core: run shell commands in stages and report them in one small format.

Shared by `verify` (the worker's entry point) and the script gate, so both print
the same lines. Stdlib only, Python 3.9+; import it as a sibling (`import checks`).

A run is a list of stages, each a list of commands. Commands in a stage are
independent: all of them run, so every independent failure shows in one run.
Stages depend on each other in order: if any command in a stage fails, the
later stages are skipped, because their input (codegen, a build) is missing.
A command that outlives its timeout is killed with its whole process group and counts as a failure.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import NamedTuple, Optional

import common


@dataclass
class Result:
    command: str
    status: str  # "pass" | "fail" | "skip"
    seconds: float = 0.0
    code: int | None = None
    output: str = ""


DEFAULT_TIMEOUT = 1800  # seconds a check command may run


class ShellRun(NamedTuple):
    code: Optional[int]  # None when the command was killed for running past its timeout
    stdout: str
    stderr: str  # empty when merge_stderr folded it into stdout
    timed_out: bool = False


def run_shell(command: str, cwd=None, timeout=None, merge_stderr=False) -> ShellRun:
    """Run a shell command; past `timeout` seconds kill it and everything it started.

    The command gets its own session, so the kill reaches its children too (they would otherwise
    keep the pipes open and block the read). stdin is closed, so nothing waits for input.
    """
    proc = subprocess.Popen(
        command, cwd=cwd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, text=True, errors="replace",
        start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)  # reap it; keep what it printed before the kill
        except subprocess.TimeoutExpired as e:
            # A grandchild that left the session (setsid, a daemon) still holds the pipes: give up on them.
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            stdout, stderr = e.stdout, e.stderr
            stdout = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
        return ShellRun(None, stdout or "", stderr or "", True)
    return ShellRun(proc.returncode, stdout or "", stderr or "")


def run_command(command: str, cwd=None, timeout=None) -> Result:
    start = time.monotonic()
    r = run_shell(command, cwd, timeout, merge_stderr=True)
    seconds = time.monotonic() - start
    if r.timed_out:
        note = f"timed out after {timeout:g}s"
        return Result(command, "fail", seconds, None, (r.stdout.rstrip("\n") + "\n" if r.stdout else "") + note)
    return Result(command, "pass" if r.code == 0 else "fail", seconds, r.code, r.stdout)


def run_stages(stages, cwd=None, timeout=DEFAULT_TIMEOUT) -> list:
    results = []
    blocked = False
    for stage in stages:
        failed = False
        for command in stage:
            if blocked:
                results.append(Result(command, "skip"))
                continue
            result = run_command(command, cwd, timeout)
            failed = failed or result.status == "fail"
            results.append(result)
        blocked = blocked or failed
    return results


def format_results(results) -> str:
    """One line per command; a failure is followed by the tail of its output (indented)."""
    lines = []
    for r in results:
        if r.status == "skip":
            lines.append(f"skip  {'-':>7}  {common.one_line(r.command)}  (an earlier step failed)")
            continue
        exit_note = "" if r.status != "fail" else "  (timed out)" if r.code is None else f"  (exit {r.code})"
        lines.append(f"{r.status:<4}  {r.seconds:>6.1f}s  {common.one_line(r.command)}{exit_note}")
        if r.status == "fail" and r.output.strip():
            lines.extend("      " + line for line in common.tail(r.output).splitlines())
    return "\n".join(lines)


def ok(results) -> bool:
    return all(r.status != "fail" for r in results)
