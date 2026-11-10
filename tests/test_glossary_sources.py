"""GLOSSARY.md `Source:` paths exist, and a `## Sources` section is backed by docs/sources.md (ADR-0017)."""
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TICKET = re.compile(r"#\d+")
BACKTICKED = re.compile(r"`([^`]+)`")
YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def glossary_problems(root):
    """A `Source:` path that does not exist, unless the entry names a ticket (`#<n>`) that will create it."""
    problems = []
    for line in (root / "GLOSSARY.md").read_text().splitlines():
        if "Source:" not in line and "Sources:" not in line:
            continue
        source = re.split(r"Sources?:", line, maxsplit=1)[1]
        if TICKET.search(source):
            continue
        for token in BACKTICKED.findall(source):
            path = token.split()[0]
            if "/" in path and not any(c in path for c in "<>*{}") and not (root / path).exists():
                problems.append(f"GLOSSARY.md: Source `{path}` does not exist; fix the path or name the ticket that creates it")
    return problems


def sources_section(text):
    match = re.search(r"(?ms)^## Sources\s*$(.*?)(?=^## |\Z)", text)
    return match.group(1) if match else ""


def credit_problems(root):
    """Each author and year a `## Sources` section names must appear in docs/sources.md."""
    ledger = (root / "docs" / "sources.md").read_text()
    problems = []
    # ADRs and the guide-level docs; docs/research notes are free-form and cite per finding.
    docs = sorted((root / "docs").glob("*.md")) + sorted((root / "docs" / "adr").glob("*.md"))
    for path in docs:
        if path.name == "sources.md":
            continue
        for part in re.split(r"[;\n]", sources_section(path.read_text())):
            year = YEAR.search(part)
            surname = re.search(r"[^\W\d_][\w'’-]*", part.lstrip(" -*"))
            if not year or not surname:
                continue
            if not any(surname.group(0) in line and year.group(0) in line for line in ledger.splitlines()):
                rel = path.relative_to(root)
                problems.append(f"{rel}: Sources names {surname.group(0)} {year.group(0)}, which docs/sources.md lacks")
    return problems


class GlossarySources(unittest.TestCase):
    def test_repo_glossary_sources_exist(self):
        self.assertEqual(glossary_problems(ROOT), [])

    def test_repo_sources_sections_are_in_the_ledger(self):
        self.assertEqual(credit_problems(ROOT), [])

    def fixture(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return root

    def test_missing_source_path_fails(self):
        root = self.fixture({"GLOSSARY.md": "**X**:\nA thing. Source: `scripts/nope`.\n"})
        self.assertIn("scripts/nope", glossary_problems(root)[0])

    def test_missing_source_naming_a_ticket_passes(self):
        root = self.fixture({"GLOSSARY.md": "A thing. Source: `scripts/nope` (for #545).\n"})
        self.assertEqual(glossary_problems(root), [])

    def test_existing_source_passes(self):
        root = self.fixture({"GLOSSARY.md": "A thing. Source: `scripts/real`, `skills/run/SKILL.md` (A pass).\n", "scripts/real": "", "skills/run/SKILL.md": ""})
        self.assertEqual(glossary_problems(root), [])

    def test_source_missing_from_the_ledger_fails(self):
        root = self.fixture({
            "docs/sources.md": "- Ferdous, A. (2026). Title.\n",
            "docs/adr/0099-x.md": "# X\n\n## Sources\n\n- Ferdous et al. 2026; Parris 2026.\n",
        })
        problems = credit_problems(root)
        self.assertEqual(len(problems), 1)
        self.assertIn("Parris 2026", problems[0])

    def test_source_in_the_ledger_passes(self):
        root = self.fixture({
            "docs/sources.md": "- Ferdous, A. (2026). Title.\n",
            "docs/adr/0099-x.md": "# X\n\n## Sources\n\n- Ferdous et al. 2026. See `docs/sources.md`.\n- Map #588.\n",
        })
        self.assertEqual(credit_problems(root), [])


if __name__ == "__main__":
    unittest.main()
