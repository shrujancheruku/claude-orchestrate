#!/usr/bin/env python3
"""Run each tests/test_*.py module in its own process, N at a time, stdlib only.

Sequential `unittest discover` takes minutes; the slowest single module is the floor
for this runner. Usage: run_parallel.py [-j N] [TESTS_DIR]   (default N: CPU count).

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
RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)


def run_module(tests_dir, module):
    """Return (module, ok, seconds, output)."""
    path = os.pathsep.join(filter(None, [str(tests_dir), os.environ.get("PYTHONPATH", "")]))
    start = time.monotonic()
    # A temp XDG_CACHE_HOME per module: tests that reach scripts/github.py must never read or write the
    # developer's real rate-limit backoff file.
    with tempfile.TemporaryDirectory() as cache:
        env = dict(os.environ, PYTHONPATH=path, XDG_CACHE_HOME=cache, ORCHESTRATE_GH_LOG="off")
        p = subprocess.run([sys.executable, "-m", "unittest", module], cwd=str(tests_dir), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    out = p.stdout
    ran = RAN.search(out)
    ok = p.returncode == 0
    # Python 3.12+ exits 5 for zero tests, older ones exit 0; either way say so in the output.
    if ran is not None and int(ran.group(1)) == 0:
        ok, out = False, out + "\nno tests found in this module\n"
    return module, ok, time.monotonic() - start, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("tests_dir", nargs="?", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args(argv)
    tests_dir = Path(args.tests_dir).resolve()
    modules = sorted(p.stem for p in tests_dir.glob("test_*.py"))
    if not modules:
        print("no test_*.py modules in %s" % tests_dir)
        return 1
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda m: run_module(tests_dir, m), modules))
    failed = [r for r in results if not r[1]]
    for module, _, secs, out in failed:
        print("FAIL %s (%.1fs)" % (module, secs))
        print("\n".join(out.rstrip().splitlines()[-TAIL:]))
    print("%d/%d modules passed in %.1fs" % (len(results) - len(failed), len(results), time.monotonic() - start))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
