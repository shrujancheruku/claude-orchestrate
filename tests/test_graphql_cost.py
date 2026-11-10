"""GraphQL points per read query (#498): GitHub bills (1 + the nodes asked under every connection) / 100, so a
connection inside a 100-node page costs 100 requests and one inside those 10,000. Computed from the query text."""

import ast
import re
import sys
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import github  # noqa: E402
import gqlcheck  # noqa: E402

_loader = SourceFileLoader("lead_brief", str(SCRIPTS / "lead-brief"))
lead_brief = module_from_spec(spec_from_loader("lead_brief", _loader))
_loader.exec_module(lead_brief)
_board_loader = SourceFileLoader("board_script", str(SCRIPTS / "board"))
board = module_from_spec(spec_from_loader("board_script", _board_loader))
_board_loader.exec_module(board)

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
            "lastEditedAt\n        comments(last: 100) { nodes { body createdAt lastEditedAt authorAssociation author { login } } }",
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

    def query_constants(self):
        """{name: text} of every module-level string in a script module that is, or is named like, a GraphQL document:
        one that opens with `query`, `mutation` or `{` (an anonymous query), or whose name ends in `_QUERY`."""
        found = {}
        for mod in (github, lead_brief):
            for k, v in vars(mod).items():
                if isinstance(v, str) and (k.endswith("_QUERY") or v.lstrip().startswith(("query", "mutation", "{"))):
                    found[k] = v
        return found

    def test_a_query_constant_cannot_escape_the_guard_by_its_name_or_its_module(self):
        found = self.query_constants()
        self.assertIn("ISSUES_QUERY", found)
        self.assertIn("MERGED_QUERY", found)  # lead-brief's, found rather than listed
        for name, q in found.items():
            with self.subTest(name):
                self.assertTrue(name.endswith("_QUERY"), f"{name} is a query but not named *_QUERY")
                limit = lead_brief.MERGED_LIMIT if "$limit" in q else 100
                self.assertLessEqual(points(q, limit), MAX_POINTS)

    def test_every_graphql_call_site_in_scripts_sends_a_known_query_constant(self):
        known = set(self.query_constants())
        known.add("query")  # github._connection's parameter: its callers pass the constants above
        sites = re.compile(r"query=\{(?:[\w.]+\.)?(\w+)\}")
        # `query=` followed by anything but a `{name}` placeholder: a literal query, or `"query=" + Q` built by hand
        literal = re.compile(r"""query=(?!\{)[A-Za-z{]|["']query=["']\s*\+""")
        found = set()
        for path in sorted(SCRIPTS.iterdir()):
            if path.is_dir() or path.suffix in (".md", ".json"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found |= set(sites.findall(text))
            with self.subTest(path.name):
                self.assertIsNone(literal.search(text), "a graphql call site builds its query outside `query={NAME}`")
        self.assertTrue(found)
        self.assertLessEqual(found, known, "a graphql call site sends a query outside the *_QUERY set")

    def test_board_and_threads_hold_no_connection_size_outside_a_priced_constant(self):
        """The two scripts build their queries by concatenation, which `query={NAME}` scanning cannot see: a `first:`
        or `last:` in any string of theirs (a plain, an f-string or a concatenated piece) would go unpriced."""
        for name in ("board", "threads"):
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            with self.subTest(name):
                self.assertTrue(strings)
                for text in strings:
                    self.assertIsNone(re.search(r"\b(?:first|last):", text),
                                      f"{name} sizes a connection in a string the cost guard cannot price: {text[:60]}")

    def test_the_boards_content_id_lookup_is_priced_at_one_point(self):
        sent = []

        def fake(query, repo, idempotent=False):
            sent.append(query)
            return {"data": {"repository": {f"i{n}": {"id": f"C{n}"} for n in range(1, 41)}}}

        writes = board.Writes()
        for n in range(1, 41):  # more than one request's worth of adds: the lookup is still one query
            writes.add("add", n)
        with mock.patch.object(board, "graphql", fake):
            try:
                writes.flush("P", "acme/r", ".")
            except Exception:  # the fake holds no card ids for the adds; only the lookup is under test
                pass
        lookup = sent[0]
        self.assertTrue(lookup.startswith("query"))
        self.assertEqual(points(lookup), 0.01)

    def test_the_merged_prs_query_costs_at_most_five_points(self):
        self.assertLessEqual(points(lead_brief.MERGED_QUERY, lead_brief.MERGED_LIMIT), MAX_POINTS)

    def test_an_unpriceable_connection_size_fails_loudly(self):
        with self.assertRaises(ValueError):
            points("query($n: Int!) { a(first: $n) { nodes { id } } }")

    def test_the_issues_query_asks_for_edit_times_without_a_connection(self):
        self.assertNotIn("userContentEdits", github.ISSUES_QUERY)
        self.assertEqual(github.ISSUES_QUERY.count("lastEditedAt"), 2)


class GateQueryCheck(unittest.TestCase):
    """What gate's GraphQL check (gqlcheck, #682) reads from the source: strings in arguments, and `threads`' mutations."""

    def test_a_closing_paren_or_brace_inside_a_string_argument_does_not_end_the_field(self):
        m = 'mutation($id: ID!) { a(input: {body: "x) } y"}) { id } b: c(input: {t: """z } ) {"""}) { id } }'
        self.assertTrue(gqlcheck.is_operation(m))
        self.assertEqual(gqlcheck.mutation_fields(m), ["a", "c"])

    def test_a_string_with_an_escaped_quote_is_skipped_whole(self):
        m = 'mutation { a(input: {body: "say \\") } now"}) { id } }'
        self.assertEqual(gqlcheck.mutation_fields(m), ["a"])

    def test_threads_single_mutations_are_whole_queries_the_check_reads(self):
        loader = SourceFileLoader("threads_script", str(SCRIPTS / "threads"))
        threads = module_from_spec(spec_from_loader("threads_script", loader))
        loader.exec_module(threads)
        read = gqlcheck.queries_in((SCRIPTS / "threads").read_text())
        for whole in (threads.REPLY, threads.RESOLVE_THREAD, threads.UNRESOLVE_THREAD):
            self.assertIn(whole, read)
        # the literals stay the single-field form of the fields the batched aliases are built from
        self.assertEqual(threads.REPLY, "mutation($id: ID!, $body: String!) { " + threads.REPLY_FIELD + " }")
        self.assertEqual(threads.RESOLVE_THREAD, "mutation($id: ID!) { " + threads.RESOLVE_FIELD + " }")
        self.assertEqual(threads.UNRESOLVE_THREAD, "mutation($id: ID!) { " + threads.UNRESOLVE_FIELD + " }")

    def test_threads_batched_mutation_is_reported_unchecked(self):
        names = [n for n, _ in gqlcheck.fragments_in((SCRIPTS / "threads").read_text())]
        self.assertIn("batched", names)

    def test_a_page_size_int_is_filled_with_one_not_the_pr_number(self):
        """#704: GitHub caps a connection at 100, so `last: $limit` with the PR number (699) fails the gate."""
        q = ("query($owner: String!, $name: String!, $limit: Int!) "
             "{ repository(owner: $owner, name: $name) { pullRequests(last: $limit) { nodes { id } } } }")
        args, why = gqlcheck.fixtures(q, 699)
        self.assertEqual(why, "")
        self.assertEqual(args, ["-F", "owner={owner}", "-F", "name={repo}", "-F", "limit=1"])

    def test_an_int_that_is_not_a_page_size_keeps_the_pr_number(self):
        q = ("query($owner: String!, $name: String!, $n: Int!) "
             "{ repository(owner: $owner, name: $name) { pullRequest(number: $n) { title } } }")
        args, why = gqlcheck.fixtures(q, 699)
        self.assertEqual(why, "")
        self.assertEqual(args, ["-F", "owner={owner}", "-F", "name={repo}", "-F", "n=699"])

    def test_an_int_used_as_a_number_and_a_page_size_is_filled_with_one(self):
        q = "query($n: Int!) { pullRequest(number: $n) { reviews(first: $n) { nodes { id } } } }"
        args, _ = gqlcheck.fixtures(q, 699)
        self.assertEqual(args, ["-F", "n=1"])

    def test_a_page_size_inside_a_string_literal_does_not_make_an_int_a_page_size(self):
        q = 'query($n: Int!) { pullRequest(number: $n) { title(label: "last: $n") } }'
        args, _ = gqlcheck.fixtures(q, 699)
        self.assertEqual(args, ["-F", "n=699"])



if __name__ == "__main__":
    unittest.main()
