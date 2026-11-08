import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import touchmap
from touchmap import Entry, TouchMapError

# The grammar table: text -> canonical form, or None for an error.
GRAMMAR = [
    ("src/a.py", "src/a.py"),
    ("src/a.py:10", "src/a.py:10"),
    ("src/a.py:10-20", "src/a.py:10-20"),
    ("src/a.py:10-20 (the join)", "src/a.py:10-20 (the join)"),
    ("src/a.py (new)", "src/a.py (new)"),
    ("`src/a.py` (new)", "src/a.py (new)"),
    ("`src/a.py`:3", "src/a.py:3"),
    ("./src/a.py:3", "src/a.py:3"),
    ("db/", "db/"),
    ("a.py:1, b.py:2", "a.py:1, b.py:2"),
    ("a.py:1; b.py:2", "a.py:1, b.py:2"),
    ("a.py:1,b.py:2", "a.py:1, b.py:2"),
    ("a.py:1 (x, y), b.py:2 (p; q)", "a.py:1 (x, y), b.py:2 (p; q)"),
    ("src/a.py (see note). src/b.py", "src/a.py (see note), src/b.py"),
    ("a.py:1.", "a.py:1"),
    ("none", "none"),
    ("None.", "none"),
    ("", "none"),
    ("`src/a.py:10`", "src/a.py:10"),
    ("`src/a.py:10-20` (x)", "src/a.py:10-20 (x)"),
    ("a.py:10-20, 30-40", None),
    ("a.py:10, 20-30", None),
    ("a.py:10, 20–30", None),
    ("src/a.py:10, 20", None),
    ("a.py:10,20, b.py:3", None),
    ("src/a.py (deleted):12", None),
    ("src/a.py:L10-L20", None),
    ("src/a.py:20-10", None),
    ("(just a note)", None),
    ("a.py (open, b.py", None),
    ("a.py and b.py", None),
    ("a.py:", None),
]


class Grammar(unittest.TestCase):
    def test_table(self):
        for text, want in GRAMMAR:
            with self.subTest(text):
                if want is None:
                    with self.assertRaises(TouchMapError):
                        touchmap.parse(text)
                else:
                    self.assertEqual(touchmap.format(touchmap.parse(text)), want)

    def test_fields(self):
        self.assertEqual(touchmap.parse("`src/a.py` (new)"), [Entry("src/a.py", None, None, "new")])
        self.assertEqual(touchmap.parse("a.py:3-9 (x)"), [Entry("a.py", 3, 9, "x")])
        self.assertEqual(touchmap.parse(None), [])

    def test_a_kept_entry_after_a_sentence_end(self):
        self.assertEqual([e.path for e in touchmap.parse("src/a.py (see note). src/b.py")], ["src/a.py", "src/b.py"])

    def test_format_is_idempotent(self):
        for text, want in GRAMMAR:
            if want:
                with self.subTest(text):
                    self.assertEqual(touchmap.format(touchmap.parse(want)), want)

    def test_overlaps_ignores_lines_and_honours_directories(self):
        a, b = touchmap.parse("x.py:1")[0], touchmap.parse("x.py:90-99 (other)")[0]
        self.assertTrue(touchmap.overlaps(a, b))
        self.assertFalse(touchmap.overlaps(a, touchmap.parse("y.py")[0]))
        self.assertTrue(touchmap.overlaps(touchmap.parse("db/")[0], touchmap.parse("db/schema.sql:1")[0]))
        self.assertTrue(touchmap.overlaps(touchmap.parse("db/schema.sql")[0], touchmap.parse("db/")[0]))


if __name__ == "__main__":
    unittest.main()
