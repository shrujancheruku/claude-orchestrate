"""Run every script under scripts/ exactly as its caller does (see docs/guide.md, Development).

CLIs (extensionless) run by path with no -I, reached through the skills' `${CLAUDE_SKILL_DIR}/../../scripts/`
form, so a broken sibling import or a lost exec bit fails here. Standalone .py scripts run with `python3 -I`.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CLIS = sorted(p.name for p in SCRIPTS.iterdir() if p.suffix == "" and p.is_file())
LIBRARIES = {"common", "checks", "replay", "github", "runlog", "session", "config", "repo", "claims", "tracker", "touchmap"}
STANDALONE = {"profile.py"}


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, **kw)


class Invocation(unittest.TestCase):
    def test_every_file_has_a_declared_kind(self):
        self.assertEqual(
            sorted(p.name for p in SCRIPTS.glob("*.py")),
            sorted({n + ".py" for n in LIBRARIES} | STANDALONE),
            "new .py file: declare it a library or a standalone script here and in docs/guide.md",
        )

    def test_clis_are_executable_with_shebang(self):
        for name in CLIS:
            with self.subTest(name):
                self.assertTrue(os.access(SCRIPTS / name, os.X_OK))
                self.assertEqual((SCRIPTS / name).read_text().splitlines()[0], "#!/usr/bin/env python3")

    def test_clis_run_by_path_like_a_skill(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in CLIS:
            with self.subTest(name):
                # The path form skills use: skills/<skill>/../../scripts/<name>.
                path = ROOT / "skills" / "run" / ".." / ".." / "scripts" / name
                r = run([str(path), "--help"], cwd=tempfile.gettempdir(), env=env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_standalone_scripts_run_isolated_alone(self):
        # `-I` leaves the script's directory off sys.path, so a standalone script that imports common
        # must find it itself; it runs from a copy holding only the script and its libraries.
        for name in sorted(STANDALONE):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                shutil.copy(SCRIPTS / name, d)
                shutil.copy(SCRIPTS / "common.py", d)
                shutil.copy(SCRIPTS / "runlog.py", d)
                shutil.copy(SCRIPTS / "repo.py", d)
                shutil.copy(SCRIPTS / "session.py", d)
                shutil.copy(SCRIPTS / "touchmap.py", d)
                shutil.copy(SCRIPTS / "config.py", d)
                argv = [sys.executable, "-I", str(Path(d) / name)]
                if name == "profile.py":
                    argv.append("--help")
                r = run(argv, input="", cwd=tempfile.gettempdir())
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_docs_follow_the_convention(self):
        # Every doc line that runs a script uses `python3 -I` for a standalone .py script and no interpreter otherwise.
        call = re.compile(r"(python3\s+(?:-I\s+)?)?[\"']?([^\s`\"']*/)?scripts/([\w-]+(?:\.py)?)")
        docs = [*ROOT.glob("skills/*/*.md"), *ROOT.glob("agents/*.md"), ROOT / "README.md", ROOT / "docs/guide.md"]
        seen = set()
        for doc in docs:
            for n, line in enumerate(doc.read_text().splitlines(), 1):
                for m in call.finditer(line):
                    name = m.group(3)
                    # A bare `scripts/x` is a mention; a path ahead of it (`${CLAUDE_SKILL_DIR}/../../`) is a call.
                    if name not in set(CLIS) | STANDALONE or not (m.group(2) or m.group(1)):
                        continue
                    if line[:m.start()].rstrip(" `").endswith("copy"):
                        continue  # copying a script, not running it
                    seen.add(name)
                    with self.subTest(f"{doc.name}:{n} {name}"):
                        prefix = m.group(1) or ""
                        if name in STANDALONE:
                            self.assertIn("-I", prefix, line.strip())
                        else:
                            self.assertEqual(prefix, "", line.strip())
        self.assertTrue({"gate", "holds", "merge", "rebuild-state", "watch-ci", "profile.py"} <= seen)


if __name__ == "__main__":
    unittest.main()
