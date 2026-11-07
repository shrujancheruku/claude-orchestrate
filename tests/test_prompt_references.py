"""Prompts and the README must not name a plugin script or flag that doesn't exist (#134).

Every `scripts/<name>` reference (including the `../../scripts/<name>` and
`${CLAUDE_SKILL_DIR}/../../scripts/<name>` forms) in agents/*.md, skills/**, README.md and docs/guide.md
must be a file in scripts/. Every `--flag` written after a reference, up to the end of its
backtick span or line, must appear in that script's `--help`.

The flag check covers scripts that parse arguments with argparse (so `--help` is safe and
real). A script without argparse is skipped rather than run: running it with `--help` could
act on the repo. As a script gains a parser it is picked up automatically.
"""
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# Prompts name `scripts/<name>`; the name may end in .py but never in sentence punctuation.
REF = re.compile(r"scripts/([A-Za-z][A-Za-z0-9_-]*(?:\.py)?)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
# Scripts with subcommands whose flags live in the subcommand's own --help.
SUBCOMMANDS = {"profile.py": ("compare", "record")}


def prompt_files():
    files = sorted((ROOT / "agents").glob("*.md")) + sorted((ROOT / "skills").rglob("*.md"))
    return files + [ROOT / "README.md", ROOT / "docs/guide.md"]


def references():
    """Yield (file, line number, script name, flags written after the reference, that span)."""
    for path in prompt_files():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in REF.finditer(line):
                # The span runs to the closing backtick (inline code) or end of line (fence),
                # and stops at the next script reference so flags aren't blamed on the wrong one.
                rest = line[m.end():]
                end = len(rest)
                for stop in ("`", "scripts/"):
                    i = rest.find(stop)
                    if i != -1:
                        end = min(end, i)
                yield path.relative_to(ROOT), n, m.group(1), FLAG.findall(rest[:end]), rest[:end]


def help_text(name, span):
    script = SCRIPTS / name
    if "argparse" not in script.read_text():
        return None
    argvs = [[]] + [[s] for s in SUBCOMMANDS.get(name, ()) if re.search(rf"\b{s}\b", span)]
    out = []
    for argv in argvs:
        r = subprocess.run([sys.executable, str(script), *argv, "--help"],
                           capture_output=True, text=True, timeout=30)
        out.append(declared_flags(r.stdout + r.stderr))
    return set().union(*out)


def declared_flags(text):
    """Flags argparse itself declares: the usage block and the option lines.

    Not the description: scripts reuse their docstring there, and it can name a flag
    (`bad --cwd`) after the real option is gone.
    """
    usage = text.split("\n\n", 1)[0]
    options = [re.match(r"\s+(-[^ ].*?)(?:  |$)", l).group(1) for l in text.splitlines() if re.match(r"\s{1,4}-", l)]
    return set(FLAG.findall(usage + "\n" + "\n".join(options)))


class PromptReferences(unittest.TestCase):
    def test_references_found(self):
        # Guards the extractor itself: if the regex rots, the checks below pass vacuously.
        names = {r[2] for r in references()}
        self.assertTrue({"gate", "merge", "verify", "watch-ci", "rebuild-state", "profile.py", "ci-times", "lead-brief"} <= names, names)

    def test_every_referenced_script_exists(self):
        for path, n, name, _, _ in references():
            with self.subTest(ref=f"{path}:{n} scripts/{name}"):
                self.assertTrue((SCRIPTS / name).is_file(), f"{path}:{n} names scripts/{name}, which doesn't exist")

    def test_every_flag_a_prompt_passes_is_in_help(self):
        for path, n, name, flags, span in references():
            if not flags or not (SCRIPTS / name).is_file():
                continue
            text = help_text(name, span)
            if text is None:
                continue  # no argparse, so no --help to check against
            for flag in flags:
                with self.subTest(ref=f"{path}:{n} scripts/{name} {flag}"):
                    self.assertIn(flag, text, f"{path}:{n} passes {flag} to {name}, but its --help doesn't list it")


GUIDE = ROOT / "docs/guide.md"


def anchor(title):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title.strip().lower()))


def guide_anchors():
    return {anchor(m.group(1)) for m in re.finditer(r"^#{2,4} (.+)$", GUIDE.read_text(), re.M)}


def lead_section():
    """The guide's Lead mode section, up to the next level-2 heading."""
    text = GUIDE.read_text()
    start = text.index("## Lead mode")
    return text[start:text.index("\n## ", start + 1)]


class LeadDocs(unittest.TestCase):
    """The README and guide explain the lead: both starts, what it degrades to, ci-times and the board (#216)."""

    def test_readme_starts_the_lead_in_one_command_and_links_the_guide(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn('claude --name lead "/orchestrate:lead"', readme)
        self.assertIn("docs/guide.md#lead-mode", readme)

    def test_every_readme_link_into_the_guide_resolves(self):
        readme = (ROOT / "README.md").read_text()
        anchors = guide_anchors()
        for m in re.finditer(r"docs/guide\.md#([\w-]+)", readme):
            with self.subTest(anchor=m.group(1)):
                self.assertIn(m.group(1), anchors)

    def test_guide_covers_both_starts_and_the_fallbacks(self):
        section = lead_section()
        self.assertIn('claude --name lead "/orchestrate:lead"', section)
        self.assertIn('claude --name orchestrator "/orchestrate:run --greedy"', section)
        # Without a lead, each part falls back: stops to needs-info, discovered work and risk notes to the summary.
        self.assertIn("Without a lead", section)
        self.assertIn("needs-info", section)
        self.assertIn("discovered", section)
        self.assertIn("risk notes", section)

    def test_guide_has_ci_times_and_board_sections(self):
        anchors = guide_anchors()
        self.assertIn("measuring-ci-wait", anchors)
        self.assertIn("projects-board", anchors)


if __name__ == "__main__":
    unittest.main()
