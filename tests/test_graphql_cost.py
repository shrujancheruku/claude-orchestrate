"""GraphQL points per read query (#498): GitHub bills (1 + the nodes asked under every connection) / 100, so a
connection inside a 100-node page costs 100 requests and one inside those 10,000. Computed from the query text."""

import re
import sys
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import github  # noqa: E402

_loader = SourceFileLoader("lead_brief", str(SCRIPTS / "lead-brief"))
lead_brief = module_from_spec(spec_from_loader("lead_brief", _loader))
_loader.exec_module(lead_brief)

MAX_POINTS = 5
OPEN = re.compile(r"(\w+)\(([^)]*)\)\s*\{|\{|\}")
SIZE = re.compile(r"\b(?:first|last):\s*(\d+|\$limit)")


def points(query: str, limit: int = 100) -> float:
    """(1 + the requests every connection needs) / 100: a connection needs one request per node of its parent."""
    stack, requests = [1], 1
    for m in OPEN.finditer(query):
        if m.group(0) == "}":
            stack.pop()
            continue
        args = m.group(2) or ""
        size = SIZE.search(args)
        # Any other size variable would be priced as no connection at all: fail loudly instead.
        if re.search(r"\b(?:first|last):\s*(?!\d|\$limit\b)\S", args):
            raise ValueError(f"cannot price a connection size in ({args})")
        if size:  # a connection: one request per parent node, and its own nodes multiply what is inside it
            requests += stack[-1]
            n = limit if size.group(1) == "$limit" else int(size.group(1))
            stack.append(stack[-1] * n)
        else:
            stack.append(stack[-1])
    return requests / 100


class QueryCost(unittest.TestCase):
    def test_the_estimator_prices_the_old_issues_query_at_about_105(self):
        old = github.ISSUES_QUERY.replace(
            "lastEditedAt\n        comments(last: 100) { nodes { body createdAt lastEditedAt } }",
            "userContentEdits(first: 20) { nodes { editedAt } }\n"
            "        comments(last: 100) { nodes { body createdAt userContentEdits(first: 20) { nodes { editedAt } } } }")
        self.assertNotEqual(old, github.ISSUES_QUERY)
        self.assertAlmostEqual(points(old), 105, delta=2)

    def test_every_github_query_costs_at_most_five_points(self):
        queries = {k: v for k, v in vars(github).items()
                   if k.endswith("_QUERY") and isinstance(v, str)}
        self.assertIn("ISSUES_QUERY", queries)
        for name, q in queries.items():
            with self.subTest(name):
                self.assertLessEqual(points(q), MAX_POINTS)

    def test_a_query_constant_cannot_escape_the_guard_by_its_name(self):
        # Discovery is by content: any module-level string that opens with `query` or `mutation`, whatever its name.
        by_content = {k for k, v in vars(github).items() if isinstance(v, str) and v.lstrip().startswith(("query", "mutation"))}
        for name in by_content:
            with self.subTest(name):
                self.assertTrue(name.endswith("_QUERY"), f"{name} is a query but not named *_QUERY")
                self.assertLessEqual(points(vars(github)[name]), MAX_POINTS)

    def test_every_graphql_call_site_in_scripts_sends_a_known_query_constant(self):
        known = {k for k, v in vars(github).items() if k.endswith("_QUERY") and isinstance(v, str)} | {"MERGED_QUERY"}
        known.add("query")  # github._connection's parameter: its callers pass the constants above
        sites = re.compile(r"query=\{(?:[\w.]+\.)?(\w+)\}")
        found = set()
        for path in sorted(SCRIPTS.iterdir()):
            if path.is_dir() or path.suffix in (".md", ".json"):
                continue
            found |= set(sites.findall(path.read_text(encoding="utf-8", errors="replace")))
        self.assertTrue(found)
        self.assertLessEqual(found, known, "a graphql call site sends a query outside the *_QUERY set")
        literal = re.compile(r"query=(?!\{)[A-Za-z{]")
        for path in sorted(SCRIPTS.iterdir()):
            if not path.is_dir() and path.suffix not in (".md", ".json"):
                with self.subTest(path.name):
                    self.assertIsNone(literal.search(path.read_text(encoding="utf-8", errors="replace")))

    def test_the_merged_prs_query_costs_at_most_five_points(self):
        self.assertLessEqual(points(lead_brief.MERGED_QUERY, lead_brief.MERGED_LIMIT), MAX_POINTS)

    def test_an_unpriceable_connection_size_fails_loudly(self):
        with self.assertRaises(ValueError):
            points("query($n: Int!) { a(first: $n) { nodes { id } } }")

    def test_the_issues_query_asks_for_edit_times_without_a_connection(self):
        self.assertNotIn("userContentEdits", github.ISSUES_QUERY)
        self.assertEqual(github.ISSUES_QUERY.count("lastEditedAt"), 2)


if __name__ == "__main__":
    unittest.main()
