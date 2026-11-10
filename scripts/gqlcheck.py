"""Find the GraphQL queries a PR adds or changes, and run each once, read-only (gate's query check, #663).

A fake `gh` in a test never parses a query, so a wrong field name reaches main (#457). The gate finds the query strings
a PR changed in its Python files (the AST of the head and base versions of each file: a string constant that starts
with `query` or `mutation` and opens a selection set; an f-string is never read, its parts are not a whole query),
runs each query once through `gh api graphql` with fixture values, and treats a GraphQL error as a failure. A
mutation is never executed: each field it selects is checked against the schema's `Mutation` fields.

Fixture values (the design call of #663, refined by #682): the repository's own owner and name (`gh` expands `{owner}`
and `{repo}`), the PR's own number for an `Int`, and for any other required variable a value of its declared type: a
placeholder for `String`/`ID`, `false` for `Boolean`, `0` for `Float`, an Enum's first value (schema introspection), and
for an `Int` the PR's own number, except one the query passes as a page size (`first:`/`last:`): that gets `1`, since a
connection is capped at 100 and a PR number past 100 would fail the gate. An
optional variable is left out. A required variable that cannot be filled (an input object, a list, a custom scalar)
skips that query with a note, never a failure.

Three outcomes are notes, not failures, and print as `gate: graphql ...` lines: `skipped` (a variable could not be
filled), `not run` (the token may not read the query: a missing scope or FORBIDDEN, which the worker cannot fix) and
`unchecked` (a query assembled at run time from pieces; the check never rebuilds queries, #564).
Stdlib only, Python 3.9+.
"""

import ast
import re

import common
import github

# A whole operation: `query`/`mutation`, an optional name, optional variable definitions, then the selection set; or
# the anonymous shorthand, a selection set whose first field has arguments or a sub-selection (`{ viewer { login } }`;
# a `{owner}` format string is not one).
OPERATION = re.compile(r"\A\s*(?:(query|mutation)(\s+\w+)?\s*(\((?:[^()]|\([^()]*\))*\))?\s*)?\{")
SHORTHAND = re.compile(r"\A\s*\{\s*[A-Za-z_]\w*\s*[({]")
VARIABLE = re.compile(r"\$(\w+)\s*:\s*([^,)=$]+)")
# A variable passed as a connection's page size (`first: $limit`, `last: $limit`); the scan runs on strip_strings' text,
# so a `first:` inside a string literal never counts.
PAGE_SIZE = re.compile(r"\b(?:first|last)\s*:\s*\$(\w+)")
PLACEHOLDER = "gate-fixture"  # a required variable with no better fixture; a missing node is not a schema error
MAX_QUERIES = 8  # one gate run never makes more calls than this (the rest are reported, not run)
NOT_RESOLVED = re.compile(r"Could not resolve to|NOT_FOUND", re.I)  # the data is absent, the query is valid
# The token may not read this query (a missing scope, e.g. `read:project`): the worker cannot fix it, so it is no failure.
NO_ACCESS = re.compile(r"\bscopes?\b|FORBIDDEN|Resource not accessible|insufficient", re.I)

# The start of a query built from pieces (`"mutation($id: ID!) { "`, `"mutation("`): the check names it, never runs it.
FRAGMENT = re.compile(r"\A\s*(?:query|mutation)\s*(?:\w+\s*)?[({]")
# A GraphQL string literal, block (`"""..."""`) or plain: its text may hold `)`, `}` or `{` that are not syntax.
STRING = re.compile(r'"""(?:\\"""|[^"]|"(?!""))*"""|"(?:\\.|[^"\\\n])*"')


def norm(text) -> str:
    return " ".join(text.split())


def strip_strings(text) -> str:
    """The query with each string literal emptied (`""`), so a scan for brackets never reads one inside a string."""
    return STRING.sub('""', text)


def is_operation(text) -> bool:
    """A whole operation: it opens one and its braces balance (the left half of `"query { " + FIELD + " }"` does not)."""
    m = OPERATION.match(text)
    if not m or (not m.group(1) and not SHORTHAND.match(text)):
        return False
    depth = 0
    for c in strip_strings(text):
        depth += (c == "{") - (c == "}")
        if depth < 0:
            return False
    return depth == 0


def queries_in(source) -> list:
    """Every whole GraphQL operation string in a Python source, in order; [] when the source does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    inside_fstring = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in ast.walk(n)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside_fstring
            and is_operation(n.value)]


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


def fragments_in(source) -> list:
    """[(name, text)] for each piece of a query built at run time: a string (or an f-string's literal start) that opens
    an operation but is not a whole one. `name` is the variable or function the piece sits in, else its line."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []

    def visit(node, name):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            name = target.id if isinstance(target, ast.Name) else name
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
            text = node.values[0].value  # the literal start of an f-string; the rest is run-time
        if text is not None and FRAGMENT.match(text) and not (isinstance(node, ast.Constant) and is_operation(text)):
            out.append((name or f"line {node.lineno}", text))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, name)

    visit(tree, None)
    return out


def changed_fragments(files) -> list:
    """(path, name) for each run-time-built query piece the PR adds or changes, each once."""
    out, seen = [], set()
    for path, head, base in files:
        old = {norm(t) for _, t in fragments_in(base or "")}
        for name, text in fragments_in(head or ""):
            if norm(text) not in old and (path, name) not in seen:
                seen.add((path, name))
                out.append((path, name))
    return out


def is_mutation(query) -> bool:
    return OPERATION.match(query).group(1) == "mutation"


def variables(query) -> list:
    """[(name, type)] of the operation's variable definitions."""
    defs = OPERATION.match(query).group(3) or ""
    return [(m.group(1), m.group(2).strip()) for m in VARIABLE.finditer(defs)]


def enum_first_value(type_name, cwd):
    """The first value of the Enum `type_name` by introspection, or (None, why) when it is no Enum or has no value.
    Raises github.GhError when gh itself could not run."""
    data = github.call_json("api", "graphql", "-f", f"query={{ __type(name: \"{type_name}\") {{ kind enumValues {{ name }} }} }}",
                            cwd=cwd)
    try:
        info = data["data"]["__type"]
        if info["kind"] == "ENUM" and info["enumValues"]:
            return info["enumValues"][0]["name"], ""
        return None, f"variable type {type_name} is {str(info['kind']).lower().replace('_', ' ')}, not an enum"
    except (KeyError, TypeError):
        return None, f"variable type {type_name} is not in the schema"


def fixtures(query, pr, cwd=None):
    """(args, why): the `gh api` arguments that give the query its required variables, each filled from its declared
    type; `why` is non-empty (and args empty) when a variable cannot be filled, so the query is skipped."""
    args = []
    sizes = set(PAGE_SIZE.findall(strip_strings(query)))
    for name, typ in variables(query):
        if not typ.endswith("!"):
            continue  # an optional variable (a cursor) is left out
        base = typ.rstrip("!")
        if name == "owner":
            args += ["-F", "owner={owner}"]
        elif name in ("name", "repo"):
            args += ["-F", f"{name}={{repo}}"]
        elif base == "Int":
            # A page size gets 1: GitHub caps a connection at 100, so the PR number would fail past #100. Any other Int
            # keeps the PR number, so `pullRequest(number: $n)` still names a real PR.
            args += ["-F", f"{name}={1 if name in sizes else pr}"]
        elif base in ("String", "ID"):
            args += ["-f", f"{name}={PLACEHOLDER}"]
        elif base == "Boolean":
            args += ["-F", f"{name}=false"]
        elif base == "Float":
            args += ["-F", f"{name}=0"]
        elif base.startswith("["):
            return [], f"variable ${name} is a list ({typ})"
        else:  # an Enum, an input object or a custom scalar: only the schema says which
            value, why = enum_first_value(base, cwd)
            if value is None:
                return [], f"variable ${name}: {why}"
            args += ["-f", f"{name}={value}"]
    return args, ""


def mutation_fields(query) -> list:
    """The root field names a mutation selects (aliases resolved to the field, arguments and strings skipped)."""
    query = strip_strings(query)
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
    """(outcome, why) for one query; outcome is "pass", "fail", "skipped" (a variable could not be filled) or "not run"
    (the token may not read it). A query is run once; a mutation is checked against the schema and never sent.
    Raises github.GhError when gh itself could not run (the caller decides)."""
    if is_mutation(query):
        data = github.call_json("api", "graphql", "-f", f"query={github.MUTATION_FIELDS_QUERY}", cwd=cwd)
        try:
            known = {f["name"] for f in data["data"]["__type"]["fields"]}
        except (KeyError, TypeError):
            raise github.GhError(github.MALFORMED, "gh returned no Mutation fields") from None
        unknown = [f for f in mutation_fields(query) if f not in known]
        if unknown:
            return "fail", "mutation field(s) not in the schema's Mutation type: " + ", ".join(unknown)
        return "pass", ""
    args, why = fixtures(query, pr, cwd)
    if why:
        return "skipped", why
    try:
        github.call("api", "graphql", "-f", f"query={query}", *args, cwd=cwd)
    except github.GhError as e:
        if github.could_not_run(e):
            raise
        if NOT_RESOLVED.search(e.message):
            return "pass", ""  # the data is absent, the query is valid
        if NO_ACCESS.search(e.message):
            return "not run", common.one_line(e.message)[:100]
        return "fail", e.message
    return "pass", ""


def label(query) -> str:
    kind = "mutation" if is_mutation(query) else "query"
    return f"graphql {kind}: {norm(query)[:70]}"


def query_name(query) -> str:
    return (OPERATION.match(query).group(2) or "").strip() or "anonymous"


def check(files, pr, cwd=None) -> list:
    """[(label, ok, why)] for every changed query, at most MAX_QUERIES run; a note (skipped, not run, unchecked) is
    `ok` and its label is the whole `gate: graphql ...` line."""
    found = changed_queries(files)
    out = []
    for path, q in found[:MAX_QUERIES]:
        outcome, why = run(q, pr, cwd)
        if outcome == "skipped":
            out.append((f"gate: graphql skipped {path}:{query_name(q)} ({why})", True, ""))
        elif outcome == "not run":
            out.append((f"gate: graphql not run ({why}) {path}:{query_name(q)}", True, ""))
        else:
            out.append((label(q) + f" ({path})", outcome == "pass", why if outcome == "fail" else ""))
    if len(found) > MAX_QUERIES:
        out.append((f"graphql: {len(found) - MAX_QUERIES} more changed queries not run", True, ""))
    out += [(f"gate: graphql unchecked {path}:{name}", True, "") for path, name in changed_fragments(files)]
    return out
