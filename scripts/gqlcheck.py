"""Find the GraphQL queries a PR adds or changes, and run each once, read-only (gate's query check, #663).

A fake `gh` in a test never parses a query, so a wrong field name reaches main (#457). The gate finds the query strings
a PR changed in its Python files (the AST of the head and base versions of each file: a string constant that starts
with `query` or `mutation` and opens a selection set; an f-string is never read, its parts are not a whole query),
runs each query once through `gh api graphql` with fixture values, and treats a GraphQL error as a failure. A
mutation is never executed: each field it selects is checked against the schema's `Mutation` fields.

Fixture values (the design call of #663): the repository's own owner and name (`gh` expands `{owner}` and `{repo}`),
the PR's own number for an `Int`, and a placeholder for any other required variable. An optional variable is left out.
Stdlib only, Python 3.9+.
"""

import ast
import re

import common
import github

# A whole operation: `query`/`mutation`, an optional name, optional variable definitions, then the selection set.
OPERATION = re.compile(r"\A\s*(query|mutation)(\s+\w+)?\s*(\((?:[^()]|\([^()]*\))*\))?\s*\{")
VARIABLE = re.compile(r"\$(\w+)\s*:\s*([^,)=$]+)")
PLACEHOLDER = "gate-fixture"  # a required variable with no better fixture; a missing node is not a schema error
MAX_QUERIES = 8  # one gate run never makes more calls than this (the rest are reported, not run)
NOT_RESOLVED = re.compile(r"Could not resolve to|NOT_FOUND", re.I)  # the data is absent, the query is valid
MUTATION_FIELDS = '{ __type(name: "Mutation") { fields { name } } }'


def norm(text) -> str:
    return " ".join(text.split())


def queries_in(source) -> list:
    """Every whole GraphQL operation string in a Python source, in order; [] when the source does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    inside_fstring = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in ast.walk(n)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside_fstring
            and OPERATION.match(n.value)]


def changed_queries(files) -> list:
    """`files`: [(path, head source, base source)]. The (path, query) pairs whose normalized text is in the head and
    not in the base, each once."""
    out, seen = [], set()
    for path, head, base in files:
        old = {norm(q) for q in queries_in(base or "")}
        for q in queries_in(head or ""):
            key = norm(q)
            if key not in old and key not in seen:
                seen.add(key)
                out.append((path, q))
    return out


def is_mutation(query) -> bool:
    return OPERATION.match(query).group(1) == "mutation"


def variables(query) -> list:
    """[(name, type)] of the operation's variable definitions."""
    defs = OPERATION.match(query).group(3) or ""
    return [(m.group(1), m.group(2).strip()) for m in VARIABLE.finditer(defs)]


def fixtures(query, pr) -> list:
    """The `gh api` arguments that give the query its variables: required ones only."""
    args = []
    for name, typ in variables(query):
        if not typ.endswith("!"):
            continue  # an optional variable (a cursor) is left out
        base = typ.rstrip("!")
        if name == "owner":
            args += ["-F", "owner={owner}"]
        elif name in ("name", "repo"):
            args += ["-F", f"{name}={{repo}}"]
        elif base == "Int":
            args += ["-F", f"{name}={pr}"]
        else:
            args += ["-f", f"{name}={PLACEHOLDER}"]
    return args


def mutation_fields(query) -> list:
    """The root field names a mutation selects (aliases resolved to the field, arguments skipped)."""
    body = query[query.index("{", OPERATION.match(query).end() - 1):]
    names, depth, paren, i = [], 0, 0, 0
    while i < len(body):
        c = body[i]
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren == 0 and c == "{":
            depth += 1
        elif paren == 0 and c == "}":
            depth -= 1
        elif paren == 0 and depth == 1 and (c.isalpha() or c == "_"):
            m = re.compile(r"\w+").match(body, i)
            i = m.end()
            rest = body[i:].lstrip()
            if rest.startswith(":"):  # `alias: field`: the field comes next
                continue
            names.append(m.group(0))
            continue
        i += 1
    return names


def run(query, pr, cwd=None):
    """(ok, why) for one query. A query is run once; a mutation is checked against the schema and never sent.
    Raises github.GhError when gh itself could not run (the caller decides)."""
    if is_mutation(query):
        data = github.call_json("api", "graphql", "-f", f"query={MUTATION_FIELDS}", cwd=cwd)
        try:
            known = {f["name"] for f in data["data"]["__type"]["fields"]}
        except (KeyError, TypeError):
            raise github.GhError(github.MALFORMED, "gh returned no Mutation fields") from None
        unknown = [f for f in mutation_fields(query) if f not in known]
        return (not unknown, "mutation field(s) not in the schema's Mutation type: " + ", ".join(unknown)
                if unknown else "")
    try:
        github.call("api", "graphql", "-f", f"query={query}", *fixtures(query, pr), cwd=cwd)
    except github.GhError as e:
        if github.could_not_run(e):
            raise
        if NOT_RESOLVED.search(e.message):
            return True, ""
        return False, e.message
    return True, ""


def label(query) -> str:
    kind = "mutation" if is_mutation(query) else "query"
    return f"graphql {kind}: {norm(query)[:70]}"


def check(files, pr, cwd=None) -> list:
    """[(label, ok, why)] for every changed query, at most MAX_QUERIES run."""
    found = changed_queries(files)
    out = [(label(q) + f" ({path})", *run(q, pr, cwd)) for path, q in found[:MAX_QUERIES]]
    if len(found) > MAX_QUERIES:
        out.append((f"graphql: {len(found) - MAX_QUERIES} more changed queries not run", True, ""))
    return out
