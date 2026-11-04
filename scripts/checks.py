"""Check runner core: run shell commands in stages and report them in one small format.

Shared by `verify` (the worker's entry point) and the script gate, so both print
the same lines. Stdlib only, Python 3.9+; import it as a sibling (`import checks`).

A run is a list of stages, each a list of commands. Commands in a stage are
independent: all of them run, so every independent failure shows in one run.
Stages depend on each other in order: if any command in a stage fails, the
later stages are skipped, because their input (codegen, a build) is missing.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import common


@dataclass
class Result:
    command: str
    status: str  # "pass" | "fail" | "skip"
    seconds: float = 0.0
    code: int | None = None
    output: str = ""


def run_command(command: str, cwd=None) -> Result:
    start = time.monotonic()
    proc = subprocess.run(
        command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    )
    return Result(command, "pass" if proc.returncode == 0 else "fail",
                  time.monotonic() - start, proc.returncode, proc.stdout)


def run_stages(stages, cwd=None) -> list:
    results = []
    blocked = False
    for stage in stages:
        failed = False
        for command in stage:
            if blocked:
                results.append(Result(command, "skip"))
                continue
            result = run_command(command, cwd)
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
        exit_note = f"  (exit {r.code})" if r.status == "fail" else ""
        lines.append(f"{r.status:<4}  {r.seconds:>6.1f}s  {common.one_line(r.command)}{exit_note}")
        if r.status == "fail" and r.output.strip():
            lines.extend("      " + line for line in common.tail(r.output).splitlines())
    return "\n".join(lines)


def ok(results) -> bool:
    return all(r.status != "fail" for r in results)
