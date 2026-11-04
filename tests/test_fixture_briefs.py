"""Deterministic (CI): every fixture brief has every field of the brief template in the run skill."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "skills" / "run" / "brief.md"
BRIEFS = ROOT / "tests" / "fixtures" / "briefs"

FIELD = re.compile(r"^([A-Z][A-Za-z\- ]*?): ", re.M)


def template_block():
    m = re.search(r"```\n(.*?)\n```", TEMPLATE.read_text(), re.S)
    assert m, "no fenced brief template in %s" % TEMPLATE
    return m.group(1)


class FixtureBriefs(unittest.TestCase):
    def test_template_has_fields(self):
        # Guards the parser: if the template's shape changes, fail here, not silently pass below.
        fields = FIELD.findall(template_block())
        self.assertIn("Ticket", fields)
        self.assertIn("Notes", fields)
        self.assertGreaterEqual(len(fields), 10)

    def test_every_fixture_has_every_field(self):
        block = template_block()
        fields = FIELD.findall(block)
        preamble = block.splitlines()[0]
        briefs = sorted(BRIEFS.glob("*.md"))
        self.assertTrue(briefs, "no fixture briefs")
        for brief in briefs:
            text = brief.read_text()
            have = FIELD.findall(text)
            with self.subTest(brief=brief.name):
                self.assertEqual([f for f in fields if f not in have], [], "missing fields")
                self.assertEqual(have[: len(fields)], fields, "fields out of order")
                self.assertIn(preamble, text.splitlines())


if __name__ == "__main__":
    unittest.main()
