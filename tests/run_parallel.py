#!/usr/bin/env python3
"""Run each tests/test_*.py module in its own process, N at a time, stdlib only.

Sequential `unittest discover` takes minutes; the slowest single module is the floor
for this runner. Usage: run_parallel.py [-j N] [TESTS_DIR]   (default N: CPU count // (1 + other live runs)).

Each module runs as `python -m unittest <module>` with TESTS_DIR as cwd and on
sys.path, which is what `discover tests` does, so modules that import `harness` or
`transcripts` as bare names load. Only failing modules' output is printed, bounded
to the last TAIL lines like scripts/verify. A module that finds zero tests fails
(Python < 3.12 exits 0 for that, so the count is read from the "Ran N tests" line).
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TAIL = 40
HOOK_MODULES = ("test_guard", "test_guard_corpus")  # the modules that run hooks/guard.py in-process
RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)


def run_module(tests_dir, module):
    """Return (module, ok, seconds, output)."""
    path = str(tests_dir)  # not the caller's PYTHONPATH: it could put another checkout's scripts first
    start = time.monotonic()
    # A temp XDG_CACHE_HOME per module: tests that reach scripts/github.py must never read or write the
    # developer's real rate-limit backoff file.
    with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as state:
        # error::DeprecationWarning: an API this Python already deprecates fails the module here, so a newer
        # Python (CI's) that removes it does not surprise us (#506 called SourceFileLoader.load_module).
        env = dict(os.environ, PYTHONPATH=path, XDG_CACHE_HOME=cache, PYTHONWARNINGS="error::DeprecationWarning")
        # ORCHESTRATE_STATE_DIR (#710): the modules that run the guard in-process (it logs the `gh` calls it sees) write
        # their run log to a temp dir, never to the events log of the checkout the tests run in, under the live run's
        # session. The others build a temp repo and read its log back, so the override would hide it from them.
        env.pop("ORCHESTRATE_STATE_DIR", None)
        if module in HOOK_MODULES:
            env["ORCHESTRATE_STATE_DIR"] = state
        env.pop("CLAUDE_PLUGIN_ROOT", None)  # tests run the tree's own scripts, not an installed plugin's
        p = subprocess.run([sys.executable, "-m", "unittest", module], cwd=str(tests_dir), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    out = p.stdout
    ran = RAN.search(out)
    ok = p.returncode == 0
    # Python 3.12+ exits 5 for zero tests, older ones exit 0; either way say so in the output.
    if ran is not None and int(ran.group(1)) == 0:
        ok, out = False, out + "\nno tests found in this module\n"
    return module, ok, time.monotonic() - start, out


def count_other_runners(ps_output, me):
    """How many run_parallel.py runs other than pid `me` that `ps -axo pid=,command=` output lists."""
    n = 0
    for line in ps_output.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        words = cmd.split()
        # An interpreter running the script; a shell wrapper or an editor naming it is not a run.
        # Interpreter flags (`python3 -u run_parallel.py`) and a capitalised name (`Python`, macOS) still count.
        if not (pid.isdigit() and int(pid) != me and words and os.path.basename(words[0]).lower().startswith("python")):
            continue
        script = next((w for w in words[1:] if not w.startswith("-")), "")
        if os.path.basename(script) == "run_parallel.py":
            n += 1
    return n


def default_jobs(cpus, others):
    """CPU count shared with the other live runs: cpus // (1 + others), at least 1.

    Several verifies run at once on a dogfood machine, and at full -j each a test's own timeout fires under load.
    Dropping -j leaves every run unstarved; scaling a timeout would only hide a slow machine (#480).
    """
    return max(1, cpus // (1 + others))


def other_runners():
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return count_other_runners(out, os.getpid())


# What the test fixtures call themselves in a `gh_call` row (#710): `repos/o/r/...` paths and the made-up role
# `nonexistent`. A live run writes the real events log meanwhile (every agent's `gh` call is a row), so a growth check
# on the file's size would fail whenever one is running; only a row with these marks is the tests' own.
FIXTURE_ROW = re.compile(r'repos/o/r/|"role":"nonexistent"')


def real_events_log(tests_dir):
    """The events log of the checkout `tests_dir` belongs to, or None when it is not in a checkout that has the
    plugin's scripts (a fixture directory of this runner's own tests)."""
    scripts = Path(tests_dir).parent / "scripts"
    if not (scripts / "runlog.py").is_file():
        return None
    sys.path.insert(0, str(scripts))
    try:
        import repo
        import runlog
        # `repo.main_checkout`, not `runlog.log_path`: the override must not point this check at the temp dir.
        return repo.main_checkout(Path(tests_dir).parent) / runlog.PROFILES / runlog.EVENTS_LOG
    finally:
        sys.path.remove(str(scripts))


def log_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def leaked_rows(path, start):
    """The fixture rows appended to the events log at `path` after byte offset `start`."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if FIXTURE_ROW.search(line)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-j", "--jobs", type=int, default=None,
                    help="parallel modules (default: CPU count // (1 + other live run_parallel.py runs))")
    ap.add_argument("tests_dir", nargs="?", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args(argv)
    tests_dir = Path(args.tests_dir).resolve()
    if args.jobs is None:
        args.jobs = default_jobs(os.cpu_count() or 1, other_runners())
    modules = sorted(p.stem for p in tests_dir.glob("test_*.py"))
    if not modules:
        print("no test_*.py modules in %s" % tests_dir)
        return 1
    real_log = real_events_log(tests_dir)
    before = log_size(real_log) if real_log else 0
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda m: run_module(tests_dir, m), modules))
    failed = [r for r in results if not r[1]]
    for module, _, secs, out in failed:
        print("FAIL %s (%.1fs)" % (module, secs))
        print("\n".join(out.rstrip().splitlines()[-TAIL:]))
    print("%d/%d modules passed in %.1fs" % (len(results) - len(failed), len(results), time.monotonic() - start))
    leaked = leaked_rows(real_log, before) if real_log else []
    if leaked:
        print("FAIL: the tests wrote %d fixture row(s) into the real events log %s (#710); a test must point the run log "
              "at ORCHESTRATE_STATE_DIR. First row: %s" % (len(leaked), real_log, leaked[0][:200]))
    return 1 if failed or leaked else 0


if __name__ == "__main__":
    sys.exit(main())
