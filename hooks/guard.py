"""PreToolUse guard for the orchestrate plugin's agents.

Claude Code runs this before every Bash call, and before a file edit, while the
plugin is enabled. It acts only when the call comes from one of this plugin's
agents (the hook input names it in `agent_type`) and refuses the commands that
agent's role must leave to someone else. The one write it lets a read-only role
make is a reviewer's comment review on a PR, and only when the repo's config
turns posting on. For a call its rules accept that is one of the role's routine
writes it also answers `allow`, so auto mode's classifier isn't asked (#358);
the user's own deny and ask rules still win.

It reads a command the way the shell tokenises it. One lexer splits the
command into simple commands and words, with quotes, escapes, line
continuations, `$(...)` and backticks resolved, and every `gh` and `git` check
decides on those tokens, not on the command text. It is a guardrail against
mistakes, not a sandbox: aliases, `eval`, scripts written to disk and then run,
and computed strings can still get past it.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

PLUGIN = "orchestrate"

# git subcommands that change the repository, the index or the working tree.
GIT_WRITE_VERBS = (
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit", "merge", "mv", "pull",
    "push", "rebase", "reset", "restore", "revert", "rm", "stash", "switch", "tag",
)
GIT_WRITES = frozenset(GIT_WRITE_VERBS)
WORKTREE_WRITES = frozenset(("add", "remove", "move", "prune"))
# The reviewer reads a PR head with git, so `branch` (listing) and `worktree add --detach`
# (a scratch checkout) are judged by reviewer_git below instead of being refused outright.
# update-ref and symbolic-ref move refs without any of the other verbs.
REVIEWER_GIT_WRITES = (GIT_WRITES - {"branch"}) | {"update-ref", "symbolic-ref", "gc", "init", "clone", "update-index"}
CONFIG_READS = frozenset(("--get", "--get-all", "--get-regexp", "--list", "-l"))
# The scout writes no ref or config either. It keeps `branch` (even listing) and every worktree change.
SCOUT_GIT_WRITES = REVIEWER_GIT_WRITES | {"branch"}
# git's global options that take their value as the next word (`--opt=value` is one word).
GIT_VALUE_OPTIONS = frozenset(
    ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env",
     "--attr-source")
)
# Global options that point git at another repo, config or hooks.
UNSAFE_GIT_OPTIONS = frozenset(("-c", "-C", "--git-dir", "--work-tree", "--exec-path", "--namespace", "--config-env"))
# `-C <dir>` is fine before a fetch: it only picks which repo's FETCH_HEAD is written.
UNSAFE_FETCH_OPTIONS = UNSAFE_GIT_OPTIONS - {"-C"}

GH_GROUPS = frozenset(("pr", "issue", "release", "repo", "label", "workflow", "run", "secret", "variable", "project"))
GH_VERBS = frozenset((
    "create", "edit", "merge", "close", "reopen", "comment", "review", "ready", "delete", "lock", "unlock",
    "transfer", "pin", "unpin", "develop", "rerun", "cancel", "enable", "disable", "set", "archive", "rename",
    "fork", "sync", "item-add", "item-edit", "item-delete",
))
# `gh pr"merge"` is one word to the shell (`prmerge`, which gh rejects); it is refused as a write anyway.
GH_RUN_TOGETHER = frozenset(g + v for g in GH_GROUPS for v in GH_VERBS)
# gh's global flags that take their value as the next word.
GH_VALUE_FLAGS = frozenset(("-R", "--repo", "--hostname"))
# `gh api` flags that take a value: short ones (the rest of the cluster, or the next word), long ones.
API_SHORT_VALUE = frozenset("XHfFpqt")
API_LONG_VALUE = frozenset(("--header", "--jq", "--template", "--cache", "--preview", "--hostname"))
API_FIELD_FLAGS = frozenset(("--field", "--raw-field", "--input"))

FS_VERBS = frozenset(("rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "tee", "truncate", "dd", "make"))
INSTALLERS = frozenset(("npm", "pnpm", "yarn", "bun", "pip", "pip3", "brew", "cargo", "go"))
INSTALL_VERBS = frozenset(("install", "add", "remove", "uninstall", "get", "i"))
SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh"))
# Words that run the command after them, and the flow keywords that put a command after them.
WRAPPERS = frozenset((
    "env", "command", "exec", "time", "nohup", "nice", "sudo", "xargs", "timeout", "builtin", "setsid",
    "stdbuf", "ionice", "caffeinate", "watch", "!", "{", "if", "then", "else", "elif", "while", "until", "do",
))


# --- The shell lexer: the one place that decides what is quoted, escaped or expanded. ---

EXP = "\x00"  # stands in a word's text for an expansion whose value the guard can't know


class ParseError(Exception):
    """The command isn't shell the lexer can tokenise (an unbalanced quote or bracket, say)."""


class Word:
    """One shell word with its quoting resolved. `dynamic`: it holds a `$x`, `${x}`, `$(...)`, `$((...))`
    or backtick expansion (each shows as EXP in `text`). `quoted`: part of it was quoted or escaped.
    `glob`: it holds an unquoted `*`, `?`, `[`, `{`, `~` or `$`, which the shell expands to other words (#441)."""

    __slots__ = ("text", "dynamic", "quoted", "glob", "names")

    def __init__(self, text: str, dynamic: bool, quoted: bool, glob: bool = False, names: tuple = ()):
        self.text, self.dynamic, self.quoted, self.glob, self.names = text, dynamic, quoted, glob, names


class Heredoc:
    __slots__ = ("delimiter", "quoted", "strip_tabs", "body", "closed")

    def __init__(self, delimiter: str, quoted: bool, strip_tabs: bool):
        self.delimiter, self.quoted, self.strip_tabs = delimiter, quoted, strip_tabs
        self.body, self.closed = "", False


class Command:
    """A simple command. `joiner` is the operator before it (None for the first one); `depth` is 0 at the
    top level and counts how deep inside `$(...)`, backticks or `sh -c '...'` it sits. `ends` is the operator
    after it (None at the end of the line), so a trailing `&` is seen too."""

    __slots__ = ("words", "redirects", "heredocs", "depth", "joiner", "ends")

    def __init__(self, words, redirects, heredocs, depth, joiner, ends=None):
        self.words, self.redirects, self.heredocs, self.depth, self.joiner = words, redirects, heredocs, depth, joiner
        self.ends = ends


class WordBuilder:
    def __init__(self):
        self.parts, self.dynamic, self.quoted, self.glob, self.brace = [], False, False, False, False
        self.names: list = []  # one per EXP in `parts`: the variable a plain `$x` or `${x}` names, else None

    def lit(self, text: str) -> None:
        self.parts.append(text)

    def exp(self, name: str | None = None) -> None:
        self.parts.append(EXP)
        self.names.append(name)
        self.dynamic = True

    def done(self) -> Word:
        text = "".join(self.parts)
        # A brace word only expands with a comma or a `..` inside (`{a,b}`, `{1..3}`); `{owner}/{repo}` is literal.
        return Word(text, self.dynamic, self.quoted, self.glob or (self.brace and ("," in text or ".." in text)),
                    tuple(self.names))


REDIRECT_OPS = ("<<<", "<<-", "<<", "<&", "<>", "<", ">>", ">&", ">|", ">")
WRITE_REDIRECTS = frozenset((">", ">>", ">|", "&>", "&>>", ">&", "<>"))
SPECIAL_PARAM = re.compile(r"[A-Za-z_]\w*|[0-9?$!#@*-]")


class Lexer:
    def __init__(self, s: str, out: list, depth: int = 0, nested: bool = False):
        self.s, self.out, self.depth, self.nested = s, out, depth, nested
        self.i = 0
        self.paren = 0
        self.wb = None
        self.redir = None  # a redirect operator waiting for its target word
        self.pending = []  # heredocs waiting for their body after the next newline
        self.joiner = None
        self.words, self.redirects, self.heredocs = [], [], []

    def word(self) -> WordBuilder:
        if self.wb is None:
            self.wb = WordBuilder()
        return self.wb

    def end_word(self) -> None:
        if self.wb is None:
            return
        w = self.wb.done()
        self.wb = None
        if self.redir is None:
            self.words.append(w)
            return
        op, self.redir = self.redir, None
        if op in ("<<", "<<-"):
            hd = Heredoc(w.text, w.quoted, op == "<<-")
            self.heredocs.append(hd)
            self.pending.append(hd)
        else:
            self.redirects.append((op, w))

    def end_command(self, op) -> None:
        self.end_word()
        if self.redir is not None:
            raise ParseError("a redirection with no target")
        if self.words or self.redirects or self.heredocs:
            self.out.append(Command(self.words, self.redirects, self.heredocs, self.depth, self.joiner, op))
        self.words, self.redirects, self.heredocs = [], [], []
        self.joiner = op

    def start_redirect(self, op: str) -> None:
        wb = self.wb
        if wb is not None and not wb.dynamic and not wb.quoted and "".join(wb.parts).isdigit():
            self.wb = None  # the `2` of `2>&1` is a file descriptor, not an argument
        else:
            self.end_word()
        if self.redir is not None:
            raise ParseError("two redirections in a row")
        self.redir = op

    def run(self) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            i, c = self.i, s[self.i]
            if c in " \t":
                self.end_word()
                self.i += 1
            elif c == "\n":
                self.end_command("\n")
                self.i += 1
                self.read_heredocs()
            elif c == "\\":
                if i + 1 >= n:
                    self.word().lit("\\")
                    self.i += 1
                elif s[i + 1] == "\n":
                    self.i += 2  # a line continuation: the shell drops it and the word goes on
                else:
                    wb = self.word()
                    wb.lit(s[i + 1])
                    wb.quoted = True
                    self.i += 2
            elif c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise ParseError("unterminated single quote")
                wb = self.word()
                wb.lit(s[i + 1:j])
                wb.quoted = True
                self.i = j + 1
            elif c == '"':
                wb = self.word()
                wb.quoted = True
                self.i = self.dq(i + 1, wb, '"')
            elif c == "$":
                self.i = self.dollar(i, self.word(), False)
            elif c == "`":
                self.i = self.backtick(i, self.word())
            elif c == "#" and self.wb is None:
                j = s.find("\n", i)
                self.i = n if j < 0 else j
            elif c in "<>" and s[i + 1:i + 2] == "(":  # process substitution
                sub = Lexer(s, self.out, self.depth + 1, True)
                sub.i = i + 2
                sub.run()
                self.word().exp()
                self.i = sub.i
            elif c in "<>" or (c == "&" and s[i + 1:i + 2] == ">"):
                if c == "&":
                    op = "&>>" if s.startswith("&>>", i) else "&>"
                else:
                    op = next(o for o in REDIRECT_OPS if s.startswith(o, i))
                self.start_redirect(op)
                self.i = i + len(op)
            elif c in ";&|":
                two = s[i:i + 2]
                op = two if two in ("&&", "||", "|&", ";;", ";&") else c
                self.end_command(op)
                self.i = i + len(op)
            elif c == "(":
                self.end_command("(")
                self.paren += 1
                self.i += 1
            elif c == ")":
                self.end_command(")")
                self.i += 1
                if self.paren > 0:
                    self.paren -= 1
                elif self.nested:
                    return
            else:
                wb = self.word()
                wb.lit(c)
                wb.glob = wb.glob or c in "*?[~"
                wb.brace = wb.brace or c == "{"
                self.i += 1
        if self.nested:
            raise ParseError("unterminated $( )")
        self.end_command(None)

    def read_heredocs(self) -> None:
        s, n = self.s, len(self.s)
        for hd in self.pending:
            lines = []
            while self.i < n:
                j = s.find("\n", self.i)
                line, self.i = (s[self.i:j], j + 1) if j >= 0 else (s[self.i:], n)
                if (line.lstrip("\t") if hd.strip_tabs else line) == hd.delimiter:
                    hd.closed = True
                    break
                lines.append(line)
            hd.body = "\n".join(lines)
            if not hd.quoted:
                # An unquoted delimiter lets the shell expand the body, so read what it would run.
                Lexer(hd.body, self.out, self.depth + 1).dq(0, WordBuilder(), None)
        self.pending = []

    def dq(self, i: int, wb: WordBuilder, term) -> int:
        """Read double-quote-style text from i up to the quote char `term`, or up to the end if None."""
        s, n = self.s, len(self.s)
        while True:
            if i >= n:
                if term is None:
                    return i
                raise ParseError("unterminated double quote")
            c = s[i]
            if term is not None and c == term:
                return i + 1
            if c == "\\" and s[i + 1:i + 2] and s[i + 1] in '$`"\\\n':
                if s[i + 1] != "\n":
                    wb.lit(s[i + 1])
                i += 2
            elif c == "$":
                i = self.dollar(i, wb, True)
            elif c == "`":
                i = self.backtick(i, wb)
            else:
                wb.lit(c)
                i += 1

    def dollar(self, i: int, wb: WordBuilder, in_dq: bool) -> int:
        s, n = self.s, len(self.s)
        nxt = s[i + 1:i + 2]
        if nxt == "(":
            if s[i + 2:i + 3] == "(":  # $(( arithmetic ))
                depth = 0
                for j in range(i + 1, n):
                    depth += (s[j] == "(") - (s[j] == ")")
                    if depth == 0:
                        wb.exp()
                        return j + 1
                raise ParseError("unterminated $(( ))")
            sub = Lexer(s, self.out, self.depth + 1, True)
            sub.i = i + 2
            sub.run()
            wb.exp()
            return sub.i
        if nxt == "{":
            j, depth = i + 2, 1
            while j < n:
                c = s[j]
                if c == "\\":
                    j += 2
                elif c == "$" and s[j + 1:j + 2] == "(":
                    j = self.dollar(j, WordBuilder(), True)
                elif c == "`":
                    j = self.backtick(j, WordBuilder())
                else:
                    depth += (c == "{") - (c == "}")
                    j += 1
                    if depth == 0:
                        inner = s[i + 2:j - 1]
                        wb.exp(inner if re.fullmatch(r"[A-Za-z_]\w*", inner) else None)
                        return j
            raise ParseError("unterminated ${ }")
        if nxt == "'" and not in_dq:  # $'...': escapes the lexer doesn't decode make the word unknowable
            j = i + 2
            while j < n and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                raise ParseError("unterminated $'...'")
            wb.quoted = True
            if "\\" in s[i + 2:j]:
                wb.exp()
            else:
                wb.lit(s[i + 2:j])
            return j + 1
        m = SPECIAL_PARAM.match(s, i + 1)
        if m:
            # `$?` is named too: the exit status is a digit string, so a read-only echo may print it (#562).
            wb.exp(m.group() if re.fullmatch(r"[A-Za-z_]\w*|\?", m.group()) else None)
            return m.end()
        wb.lit("$")
        wb.glob = wb.glob or not in_dq
        return i + 1

    def backtick(self, i: int, wb: WordBuilder) -> int:
        s, n = self.s, len(self.s)
        j, chars = i + 1, []
        while j < n and s[j] != "`":
            if s[j] == "\\" and s[j + 1:j + 2] in ("`", "\\", "$"):
                j += 1
            chars.append(s[j])
            j += 1
        if j >= n:
            raise ParseError("unterminated backtick")
        Lexer("".join(chars), self.out, self.depth + 1).run()
        wb.exp()
        return j + 1


def basename(w: Word) -> str:
    return w.text.rsplit("/", 1)[-1]


def parse(command: str) -> list:
    """The simple commands in a command line, with those inside `$(...)`, backticks and `sh -c '...'`.
    Raises ParseError when it can't be tokenised."""
    out: list = []
    Lexer(command, out).run()
    seen = 0
    while seen < len(out):  # `sh -c '<script>'` runs another command line: read that one too
        cmd = out[seen]
        seen += 1
        for k, w in enumerate(cmd.words):
            if not w.dynamic and basename(w) in SHELLS:
                for flag, script in zip(cmd.words[k + 1:], cmd.words[k + 2:]):
                    if re.fullmatch(r"-\w*c\w*", flag.text) and not flag.dynamic and not script.dynamic:
                        Lexer(script.text, out, cmd.depth + 1).run()
                        break
    return out


# --- Reading gh and git calls off the tokens. ---

class Call:
    """A `gh` or `git` call. For git, `options` are the global options and `sub` the subcommand. For gh,
    `sub` is the first word after its global flags (`pr`, `api`) and `verb` the next (`merge`). `args` are
    the words after those. A dynamic `sub` or `verb` is one the guard can't read."""

    def __init__(self, tool, sub=None, verb=None, options=(), args=()):
        self.tool, self.sub, self.verb, self.options, self.args = tool, sub, verb, list(options), list(args)

    def is_sub(self, *names: str) -> bool:
        return self.sub is not None and not self.sub.dynamic and self.sub.text in names

    def is_verb(self, *names: str) -> bool:
        return self.verb is not None and not self.verb.dynamic and self.verb.text in names

    def option_names(self) -> set:
        return {name for name, _ in self.options}


def parse_git(words: list, k: int) -> Call:
    """The git call whose command word is words[k]: its global options (those taking a separate value
    included), then the real subcommand."""
    options, j = [], k + 1
    while j < len(words):
        w = words[j]
        if w.dynamic:
            return Call("git", sub=w, options=options)
        if w.text.startswith("-") and len(w.text) > 1:
            name = w.text.split("=", 1)[0] if w.text.startswith("--") else w.text
            if "=" not in w.text and name in GIT_VALUE_OPTIONS:
                options.append((name, words[j + 1] if j + 1 < len(words) else None))
                j += 2
            else:
                options.append((name, None))
                j += 1
        else:
            return Call("git", sub=w, options=options, args=words[j + 1:])
    return Call("git", options=options)


def parse_gh(words: list, k: int) -> Call:
    """The gh call whose command word is words[k]: skip global flags wherever they sit
    (`gh pr -R o/r merge 12`), then take the subcommand pair."""
    found, j = [], k + 1
    while j < len(words) and len(found) < 2:
        w = words[j]
        j += 1
        if not w.dynamic and w.text.startswith("-") and len(w.text) > 1:
            if w.text in GH_VALUE_FLAGS:
                j += 1
            continue
        found.append(w)
        if len(found) == 1 and not w.dynamic and w.text == "api":
            break
    return Call("gh", sub=found[0] if found else None, verb=found[1] if len(found) > 1 else None, args=words[j:])


class ApiScan:
    """What `gh api`'s flags say: `other` is a write by method or `--input`; `fields` holds each `-f`/`-F`
    value as a Word (gh POSTs on any of them); `endpoint` is the first operand."""

    def __init__(self):
        self.other, self.fields, self.endpoint = False, [], None


def scan_api(args: list) -> ApiScan:
    """Walk `gh api`'s arguments: a method other than GET (`-X V`, `-XV`, `--method V`, `--method=V`),
    `--input`, and the field flags (attached or separate)."""
    scan, j = ApiScan(), 0
    while j < len(args):
        w = args[j]
        t = w.text
        j += 1
        if t.startswith("--"):
            name, eq, value = t.partition("=")
            if name == "--method":
                dynamic = w.dynamic
                if not eq:
                    if j >= len(args):
                        continue
                    value, dynamic = args[j].text, args[j].dynamic
                    j += 1
                if dynamic or value.casefold() != "get":
                    scan.other = True
            elif name == "--input":
                scan.other = True
            elif name in API_FIELD_FLAGS:
                if not eq and j < len(args):
                    scan.fields.append(args[j])
                    j += 1
                elif eq:
                    scan.fields.append(Word(value, w.dynamic, w.quoted))
                else:
                    scan.other = True
            elif name in API_LONG_VALUE and not eq:
                j += 1
        elif t.startswith("-") and len(t) > 1:
            for pos, ch in enumerate(t[1:], 1):  # a cluster such as `-iXPOST` or `-ftitle=x`
                if ch in API_SHORT_VALUE:
                    value, dynamic, quoted = t[pos + 1:], w.dynamic, w.quoted
                    if not value and j < len(args):
                        value, dynamic, quoted = args[j].text, args[j].dynamic, args[j].quoted
                        j += 1
                    if ch in "fF":
                        scan.fields.append(Word(value, dynamic, quoted))
                    elif ch == "X" and (dynamic or value.casefold() != "get"):
                        scan.other = True
                    break
        elif scan.endpoint is None:
            scan.endpoint = w
    return scan


def api_writes(args: list) -> bool:
    """True when `gh api`'s flags make it a write: a method other than GET, any field flag (gh POSTs on
    `-f` and `-F`), or `--input`."""
    scan = scan_api(args)
    return scan.other or bool(scan.fields)


def graphql_refusal(args: list) -> str | None:
    """For `gh api graphql`: None when it is a plain read (one `query` operation whose text is literal),
    else why not. The field flags carry the query and its variables, so they aren't writes here; the
    query's text decides. Not a graphql call: "" (the caller applies the ordinary rule)."""
    scan = scan_api(args)
    if scan.endpoint is None or scan.endpoint.dynamic or scan.endpoint.text != "graphql":
        return ""
    if scan.other:
        return "writes through the GitHub API (graphql with a method or --input)"
    queries = [f for f in scan.fields if f.text.startswith("query=")]
    if len(queries) != 1:
        return "writes through the GitHub API (graphql without exactly one query field)"
    q = queries[0]
    text = q.text[len("query="):]
    if q.dynamic:
        return "writes through the GitHub API (graphql query the guard can't read: it holds an expansion)"
    if any(f.text.partition("=")[2].startswith("@") for f in scan.fields):  # -F name=@file sends a local file
        return "writes through the GitHub API (graphql field read from a file or stdin)"
    if text.startswith("@"):
        return "writes through the GitHub API (graphql query read from a file or stdin)"
    bare = "\n".join(line.split("#", 1)[0] for line in text.splitlines()).strip()
    # Search the unstripped text: a `#` inside a string literal isn't a comment, and stripping it would hide
    # a later `mutation` (picked by operationName). A comment naming the word is refused too, on purpose.
    if re.search(r"\b(mutation|subscription)\b", text):
        return "writes through the GitHub API (graphql mutation or subscription)"
    if not (bare.startswith("{") or re.match(r"query\b", bare)):
        return "writes through the GitHub API (graphql query the guard can't read as a query)"
    return None


def is_subsequence(part: str, whole: str) -> bool:
    chars = iter(whole)
    return all(ch in chars for ch in part)


def could_be_tool(w: Word) -> bool:
    """Whether a command word holding an expansion might turn out to be `gh` or `git`: what is literal
    in its last path part has to fit inside one of those names."""
    tail = w.text.rsplit("/", 1)[-1].replace(EXP, "")
    return any(is_subsequence(tail, name) for name in ("gh", "git"))


def command_index(words: list) -> int:
    """Index of the word that names the program: after leading `VAR=value` words and wrappers."""
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"[A-Za-z_]\w*=", w.text):
            i += 1
        elif not w.dynamic and basename(w) in WRAPPERS:
            i += 1
            while i < len(words) and not words[i].dynamic and re.fullmatch(r"-.*|\d+[smhd]?|[A-Za-z_]\w*=.*", words[i].text):
                i += 1
        else:
            break
    return i


class Parsed:
    """The command line as the rules see it: its simple commands and the gh and git calls in them. A call
    is any `gh` or `git` word, wherever it sits (`xargs git push`, `env git push`)."""

    def __init__(self, cmds: list):
        self.cmds, self.calls = cmds, []
        for cmd in cmds:
            for k, w in enumerate(cmd.words):
                if not w.dynamic and basename(w) in ("gh", "git"):
                    self.calls.append(parse_gh(cmd.words, k) if basename(w) == "gh" else parse_git(cmd.words, k))

    def tool(self, name: str) -> list:
        return [c for c in self.calls if c.tool == name]


# The writes a reviewer may make: a comment review posted through the API (its JSON on a
# heredoc) or through `gh pr review --comment`. The aimed command must be exactly one of
# these shapes (see review_post); the payload is checked separately. A heredoc delimiter must be
# quoted, or the shell would expand `$(...)` inside the body, and no word may hold an expansion.
# An optional leading `cd <path> &&` is allowed, a plain cd and nothing chained after it.
REVIEWS_PATH = re.compile(r"pulls/\d+/reviews")
REVIEW_ENDPOINT = re.compile(r"repos/[^/\s]+/[^/\s]+/pulls/\d+/reviews")
# A finding whose line is outside the diff is posted on its own as a file-level comment (agents/reviewer.md).
FILE_COMMENT_PATH = re.compile(r"pulls/\d+/comments")
FILE_COMMENT_ENDPOINT = re.compile(r"repos/[^/\s]+/[^/\s]+/pulls/\d+/comments")
FILE_COMMENT_FIELDS = {"path", "subject_type", "commit_id", "body"}
SHAPES = (
    "Post exactly one of these (an optional leading `cd <path> &&` is fine, nothing else chained, nothing after the heredoc):\n"
    "gh api --method POST repos/{owner}/{repo}/pulls/<n>/reviews --input - <<'EOF'\n"
    '{"event": "COMMENT", "body": "<body>", "comments": []}\nEOF\n'
    "gh pr review <n> --comment --body-file - <<'EOF'\n<body>\nEOF\n"
    "gh pr review <n> --comment --body '<body, no single quotes>'\n"
    "A finding outside the diff, posted after the review as a file-level comment:\n"
    "gh api --method POST repos/{owner}/{repo}/pulls/<n>/comments -f path=<path> -f subject_type=file "
    "-f commit_id=<head sha> -f body=<finding>"
)
# The config line that turns posting on, e.g. `- **Post reviews:** yes`. Leaving `<` out of
# the separator keeps the template's `<yes | no>` placeholder from reading as yes.
POST_FLAG = re.compile(r"^[-*\s]*Post reviews[:*\s]*(yes|no)\b", re.IGNORECASE | re.MULTILINE)
CONFIG = Path(".claude") / "orchestration.md"

BRANCH_LIST = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l", "--show-current"}
BRANCH_REF_ARG = {"--contains", "--merged", "--no-merged", "--points-at"}
WORKTREE_FLAGS = {"--detach", "-q", "--quiet", "--no-checkout"}


def repo_roots(cwd: str) -> list[Path]:
    """The checkout holding cwd and, inside a linked worktree (where `.git` is a file), the main
    repo's checkout too."""
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return [directory]
        if dot_git.is_file():
            # Fail closed: "/" is a parent of every absolute path, so an unreadable `.git` file
            # makes every reviewer worktree add look like it is inside the repo.
            closed = [Path("/")]
            try:
                gitdir = (directory / dot_git.read_text().split("gitdir:", 1)[1].strip().splitlines()[0]).resolve()
                if not gitdir.is_dir():
                    return closed
                if (gitdir / "commondir").is_file():
                    common = (gitdir / (gitdir / "commondir").read_text().strip()).resolve()
                    return [directory, common.parent]
                if gitdir.parent.name == "worktrees":
                    return closed  # a linked worktree whose main repo can't be found
                return [directory]  # a submodule: its own checkout
            except (OSError, IndexError):
                return closed
    return [start]


def branch_lists_only(args: list[str]) -> bool:
    """True for `git branch` arguments that only list: listing flags, a ref after
    --contains and the like, and name patterns only after --list."""
    patterns_ok = "--list" in args or "-l" in args
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in BRANCH_REF_ARG:
            skip = True
        elif arg not in BRANCH_LIST and (arg.startswith("-") or not patterns_ok):
            return False
    return True


def reviewer_git_problem(call: Call, cwd: str) -> str | None:
    """What a reviewer's `git branch`, `git fetch` or `git worktree` call does wrong, or None.

    Listing branches, fetching into FETCH_HEAD and adding a detached scratch worktree outside
    the repo are the reads a reviewer needs; everything else those verbs do changes state."""
    if call.is_sub("branch"):
        if any(a.dynamic for a in call.args) or not branch_lists_only([a.text for a in call.args]):
            return "creates, deletes or renames a branch (only listing is allowed)"
    elif call.is_sub("fetch"):
        if call.option_names() & UNSAFE_FETCH_OPTIONS:
            return "points fetch at another repo or config through a global git option"
        # `fetch origin x:y` writes a local ref. A URL remote has colons of its own.
        for arg in call.args:
            if arg.dynamic:
                return "fetches with a shell expansion that can't be checked"
            if ":" in arg.text and not re.match(r"^(?:\w+://|[\w.-]+@[\w.-]+:)", arg.text):
                return "fetches into a named local ref (fetch only into FETCH_HEAD)"
    elif call.is_sub("worktree"):
        args = call.args
        if args[:1] and args[0].text == "add" and not args[0].dynamic:
            flags = [a.text for a in args[1:] if a.text.startswith("-")]
            names = [a for a in args[1:] if not a.text.startswith("-")]
            if "--detach" not in flags or any(f not in WORKTREE_FLAGS for f in flags):
                return "adds a worktree that isn't a plain `--detach` one"
            if call.option_names() & UNSAFE_GIT_OPTIONS or not 1 <= len(names) <= 2 or any(a.dynamic for a in args):
                return ("adds a worktree in a form that can't be checked "
                        "(use `git worktree add --detach <dir> <ref>` with no -c, -C or --git-dir before it)")
            target = names[0]
            if target.quoted or not (target.text.startswith("/") or target.text.startswith("~/")):
                return "adds a worktree at a path that isn't a plain absolute path"
            path = Path(target.text).expanduser().resolve()
            if any(path == root or root in path.parents for root in repo_roots(cwd)):
                return "adds a worktree inside the repo (use a scratch dir outside it)"
        elif not (args[:1] and args[0].text == "list" and not args[0].dynamic):
            return "changes worktrees"
    return None


# --- The rules. Each takes the parsed command line and the cwd, and returns what the command does wrong. ---

def bare_expansion(w: Word) -> bool:
    """A command word with nothing literal in its last path part (`$PYTHON`, `$(which python3)`)."""
    return not w.text.rsplit("/", 1)[-1].replace(EXP, "")


def rule_bare_expansion(p: Parsed, cwd: str) -> str | None:
    """Read-only roles only: a command named wholly by an expansion could be gh or git. The worker may
    run them (a repo's verify command is often `$PYTHON x.py`) and is judged on its own rules."""
    for cmd in p.cmds:
        k = command_index(cmd.words)
        if k < len(cmd.words) and cmd.words[k].dynamic and bare_expansion(cmd.words[k]):
            return "runs a command named by a shell expansion (the guard can't tell it from gh or git)"
    return None


def rule_expansion(p: Parsed, cwd: str) -> str | None:
    """A command word or gh/git subcommand the guard can't read because it holds an expansion."""
    for cmd in p.cmds:
        k = command_index(cmd.words)
        if k < len(cmd.words) and cmd.words[k].dynamic and could_be_tool(cmd.words[k]) and not bare_expansion(cmd.words[k]):
            return "runs a command named by a shell expansion (the guard can't tell it from gh or git)"
    for call in p.calls:
        if call.tool == "git" and call.sub is not None and call.sub.dynamic:
            return "puts a shell expansion where a git subcommand goes (the guard can't check it)"
        if call.tool == "gh" and (call.sub is not None and call.sub.dynamic or (
                call.verb is not None and call.verb.dynamic and call.is_sub(*GH_GROUPS))):
            return "puts a shell expansion where a gh subcommand goes (the guard can't check it)"
    return None


QUIET_FLAGS = frozenset(("-v", "--verbose", "-q", "--quiet"))


def first_arg_reads(reads: frozenset):
    """A read-form test: after any leading -v/-q flags, no subcommand or one of `reads`. A
    subcommand hiding behind a flag (`git remote -v add x y`) is still judged, and any other
    flag or a dynamic argument is refused because it can't be judged."""
    def test(args: list) -> bool:
        if any(a.dynamic for a in args):
            return False
        words = list(args)
        while words and words[0].text in QUIET_FLAGS:
            words.pop(0)
        return not words or words[0].text in reads
    return test


def config_reads(args: list) -> bool:
    """`git config` that only reads: one of CONFIG_READS and no other flag (-f, --edit, --add,
    --unset, --replace-all ...). A name, and for --get* a value pattern, may follow; --list takes none."""
    if any(a.dynamic for a in args):
        return False
    flags = [a.text for a in args if a.text.startswith("-")]
    words = [a for a in args if not a.text.startswith("-")]
    if not flags or any(f not in CONFIG_READS for f in flags):
        return False
    return len(words) <= (0 if flags[0] in ("--list", "-l") else 2)


# Verbs whose read forms stay allowed, each as a test on the arguments; every other form is a write.
SUBCOMMAND_READS = {
    "submodule": first_arg_reads(frozenset(("status", "summary"))),
    "reflog": first_arg_reads(frozenset(("show", "exists"))),
    "remote": first_arg_reads(frozenset(("show", "get-url"))),
    "config": config_reads,
}


def subcommand_writes(call: Call) -> bool:
    """True for a `git submodule`, `reflog`, `remote` or `config` call that isn't one of its read
    forms. `config` with no read flag is a write (or an error), so it is refused."""
    for name, is_read in SUBCOMMAND_READS.items():
        if call.is_sub(name):
            return not is_read(call.args)
    return False


def rule_git_writes(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub(*SCOUT_GIT_WRITES) or subcommand_writes(call) or (
                call.is_sub("worktree") and call.args[:1] and call.args[0].text in WORKTREE_WRITES):
            return "changes git state"
        if call.is_sub("fetch"):  # the scout, like the reviewer, may fetch only into FETCH_HEAD
            problem = reviewer_git_problem(call, cwd)
            if problem:
                return problem
    return None


def rule_reviewer_git(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub(*REVIEWER_GIT_WRITES) or subcommand_writes(call):
            return "changes git state"
        problem = reviewer_git_problem(call, cwd)
        if problem:
            return problem
    return None


def rule_gh_writes(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("gh"):
        if (call.is_sub(*GH_GROUPS) and call.is_verb(*GH_VERBS)) or call.is_sub(*GH_RUN_TOGETHER):
            return "writes to the git host"
    return None


def rule_gh_api_writes(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("gh"):
        if call.is_sub("api"):
            gql = graphql_refusal(call.args)
            if gql is None:
                continue
            if gql:
                return gql
            if api_writes(call.args):
                return "writes through the GitHub API"
    return None


def rule_fs_writes(p: Parsed, cwd: str) -> str | None:
    for cmd in p.cmds:
        words, program = cmd.words, command_index(cmd.words)
        for k, w in enumerate(words):
            # Quoted words are search text, except the one naming the program.
            if w.dynamic or (w.quoted and k != program):
                continue
            nxt = words[k + 1] if k + 1 < len(words) else None
            if w.text in FS_VERBS:
                return "writes files or runs a build"
            if w.text in INSTALLERS and nxt is not None and not nxt.dynamic and nxt.text in INSTALL_VERBS:
                return "writes files or runs a build"
            if nxt is not None and not nxt.dynamic and (
                    (w.text == "sed" and re.match(r"-\w*i|--in-place", nxt.text))
                    or (w.text == "perl" and re.match(r"-\w*i", nxt.text))):
                return "writes files or runs a build"
    return None


def rule_redirects(p: Parsed, cwd: str) -> str | None:
    for cmd in p.cmds:
        for op, target in cmd.redirects:
            if op not in WRITE_REDIRECTS:
                continue
            if not target.dynamic and (target.text == "/dev/null" or (op == ">&" and re.fullmatch(r"\d+|-", target.text))):
                continue
            return "redirects output into a file"
    return None


def rule_gh_merge(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("gh"):
        if (call.is_sub("pr") and call.is_verb("merge")) or call.is_sub("prmerge"):
            return "merges a PR (merging belongs to the orchestrator)"
    return None


# The one body line that lets a worker open its PR ready: drafts are unavailable (ADR 0011).
DRAFT_UNAVAILABLE = re.compile(r"(?m)^Draft unavailable: *\S")


def pr_create_bodies(p: Parsed, call: "Call", cwd: str) -> list:
    """The body text(s) a `gh pr create` carries: `--body`/`-b` values, a `--body-file` read from disk
    (relative to cwd), or `-` for the command's heredoc. Unreadable or dynamic sources give nothing."""
    texts, args, j = [], call.args, 0
    while j < len(args):
        a = args[j]
        j += 1
        name, eq, value = a.text.partition("=") if a.text.startswith("--") else (a.text, "", "")
        if name not in ("--body", "-b", "--body-file", "-F"):
            continue
        if not eq:
            if j >= len(args):
                break
            value, dynamic = args[j].text, args[j].dynamic
            j += 1
        else:
            dynamic = a.dynamic
        if name in ("--body", "-b"):
            texts.append(value)
        elif value == "-":
            texts.extend(hd.body for cmd in p.cmds for hd in cmd.heredocs)
        elif not dynamic:
            try:
                path = Path(value)
                texts.append((path if path.is_absolute() else Path(cwd or ".") / path).read_text(errors="replace"))
            except OSError:
                pass
    return texts


def rule_pr_draft(p: Parsed, cwd: str) -> str | None:
    """A worker opens its PR as a draft at the end of Orient (ADR 0011); a model that skips the step
    opens it ready at Ship (#355), so the guard refuses a `gh pr create` that is not a draft. The one
    exception is the fallback: a body line `Draft unavailable: <reason>` (the draft itself failed)."""
    for call in p.tool("gh"):
        if not (call.is_sub("pr") and call.is_verb("create")):
            continue
        if any(a.text in ("--draft", "-d", "--draft=true") for a in call.args if not a.dynamic):
            continue
        if any(DRAFT_UNAVAILABLE.search(t) for t in pr_create_bodies(p, call, cwd)):
            continue
        return ("opens a PR that is not a draft: add `--draft` to `gh pr create`; Ship marks it ready "
                "with `gh pr ready`. Only if `gh pr create --draft` itself failed twice, open it ready "
                "with a body line `Draft unavailable: <reason>` (a `--body-file <path>` must already exist when this "
                "runs: write it in an earlier call, or pass `--body-file -` with a heredoc, or a literal `--body`)")
    return None


def rule_force_push(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub("push") and any(
                a.text.startswith("--force") or re.fullmatch(r"-[^-]*f[^-]*", a.text) or re.match(r"\+.", a.text)
                for a in call.args if not a.dynamic):
            return "force-pushes"
    return None


def default_branches(cwd: str) -> set:
    """main and master, plus the branch origin/HEAD points at when that is cheap to read: a file
    read in the checkout (or the main repo, from a linked worktree), never a git subprocess."""
    names = {"main", "master"}
    prefix = "ref: refs/remotes/origin/"
    for git_dir in common_git_dirs(cwd):
        try:
            head = (git_dir / "refs" / "remotes" / "origin" / "HEAD").read_text().strip()
        except (OSError, UnicodeDecodeError):
            continue  # unreadable here: try the next, and main and master still stand
        if head.startswith(prefix) and len(head) > len(prefix):
            names.add(head[len(prefix):])
            break
    return names


def common_git_dirs(cwd: str) -> list:
    """Git directories that can hold refs/remotes for cwd: the common dir a linked worktree's
    `.git` file points to (via its `commondir`), then each checkout's own `.git` directory."""
    found = []
    try:
        start = Path(cwd or ".").resolve()
        for directory in (start, *start.parents):
            dot_git = directory / ".git"
            if dot_git.is_file():
                try:
                    gitdir = (directory / dot_git.read_text().split("gitdir:", 1)[1].strip().splitlines()[0]).resolve()
                    common = gitdir / (gitdir / "commondir").read_text().strip()
                    found.append(common.resolve())
                except (OSError, IndexError, UnicodeDecodeError):
                    pass
                break
            if dot_git.is_dir():
                found.append(dot_git)
                break
    except OSError:
        pass
    for root in repo_roots(cwd):
        if (root / ".git").is_dir():
            found.append(root / ".git")
    return found


def rule_push_default(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if not call.is_sub("push"):
            continue
        # --all and --mirror push every branch, the default branch among them.
        if any(a.text in ("--all", "--mirror") for a in call.args if not a.dynamic):
            return "pushes to the default branch (--all and --mirror push every branch)"
        names = "|".join(re.escape(n) for n in sorted(default_branches(cwd)))
        # A bare name or the destination of `src:dst`, spelled short or as refs/heads/<name>.
        pattern = rf"(?:[^:]*:)?(?:refs/heads/)?(?:{names})"
        if any(re.fullmatch(pattern, a.text) for a in call.args if not a.dynamic):
            return "pushes to the default branch"
    return None


READ_ONLY = [rule_expansion, rule_bare_expansion, rule_git_writes, rule_gh_writes, rule_gh_api_writes,
             rule_fs_writes, rule_redirects]

RULES = {
    "scout": READ_ONLY,
    "reviewer": [rule_expansion, rule_bare_expansion, rule_reviewer_git, *READ_ONLY[3:]],
    "worker": [rule_expansion, rule_gh_merge, rule_pr_draft, rule_force_push, rule_push_default],
}

NEXT_STEP = {
    "scout": "Report what you found and let your caller act on it.",
    "reviewer": "Read code with `gh pr diff`, `git show <ref>:<path>` or a scratch checkout "
                "(`git worktree add --detach <absolute dir outside the repo> FETCH_HEAD`) and put the change in your findings.",
    "worker": "Push your own branch normally and report the PR; the orchestrator merges.",
}


def role_of(agent_type: str) -> str | None:
    """The role of an `orchestrate:<role>` agent, or None for anyone else's agent.

    A role with no rule set (retired, from another version, a typo) is still returned: it is
    ours, so verdict() holds it to the read-only rules rather than letting it run unguarded."""
    plugin, _, role = agent_type.partition(":")
    if plugin.strip().lower() == PLUGIN:  # the role part stays exact
        return role
    return None


def rules_role(role: str) -> str:
    """The role whose rules and next-step advice apply: itself, or the read-only scout's when the
    guard doesn't know the role."""
    return role if role in RULES else "scout"


def role_label(role: str) -> str:
    """How a refusal names the role."""
    if role in RULES:
        return f"the {role} role"
    return f"the {role or '(unnamed)'} role, which this version of the guard doesn't know and so treats as read-only,"


def is_number(word: str) -> bool:
    """ASCII digits only: `str.isdigit` also accepts `\u00b2`."""
    return word.isdigit() and word.isascii()


def only_plain_words(cmd: Command) -> bool:
    """No redirect, no expansion, and no unquoted glob, brace or tilde word: the shell would turn that into
    other words before the script sees them (#441)."""
    return not cmd.redirects and not any(w.dynamic or w.glob for w in cmd.words)


def cd_target_plain(cd: Command) -> bool:
    """The leading `cd <path>` of a review post: plain words, except that `~` may start the path (`cd ~/x`), as
    before #441. It only changes directory, like any other path the rule allows; a glob or brace still fails."""
    if cd.redirects or any(w.dynamic for w in cd.words):
        return False
    target = cd.words[1]
    return not target.glob or (target.text.startswith("~") and not re.search(r"[*?\[{$]", target.text))


def review_shape(cmd: Command) -> str | None:
    """Which accepted shape a `gh` command is: "api" (the review JSON on a heredoc), "body-file" (the
    body on a heredoc) or "inline" (the body on the command line); None for anything else."""
    words = [w.text for w in cmd.words]
    if words[:1] != ["gh"] or not only_plain_words(cmd):
        return None
    if words[1:2] == ["api"]:
        args = words[2:]
        if (len(args) == 5 and args[0] in ("-X", "--method") and args[1] == "POST"
                and REVIEW_ENDPOINT.fullmatch(args[2]) and args[3:] == ["--input", "-"]):
            return "api"
        if len(args) == 11 and args[0] in ("-X", "--method") and args[1] == "POST" and FILE_COMMENT_ENDPOINT.fullmatch(args[2]):
            fields = {}
            for flag, pair in zip(args[3::2], args[4::2]):
                key, eq, value = pair.partition("=")
                if flag != "-f" or not eq:
                    return None
                fields[key] = value
            if set(fields) == FILE_COMMENT_FIELDS and fields["subject_type"] == "file":
                return "file-comment"
        return None
    if words[1:3] != ["pr", "review"] or len(words) < 5 or not is_number(words[3]):
        return None
    args, comment, body, body_file = words[4:], 0, 0, 0
    j = 0
    while j < len(args):
        a = args[j]
        j += 1
        if a == "--comment":
            comment += 1
        elif a in ("-b", "--body") and j < len(args):
            body += 1
            j += 1
        elif a.startswith("--body="):
            body += 1
        elif a in ("--body-file", "-F") and args[j:j + 1] == ["-"]:
            body_file += 1
            j += 1
        elif a == "--body-file=-":
            body_file += 1
        else:
            return None
    if comment == 1 and body == 1 and body_file == 0:
        return "inline"
    if comment == 1 and body == 0 and body_file == 1:
        return "body-file"
    return None


def review_aimed(call: Call) -> bool:
    """A gh call aimed at posting a review, whether or not it has the right shape. Reading reviews
    back (a GET) isn't aimed at a post."""
    if call.is_sub("pr") and call.is_verb("review"):
        return True
    return call.is_sub("api") and api_writes(call.args) and any(
        REVIEWS_PATH.search(a.text) or FILE_COMMENT_PATH.search(a.text) for a in call.args)


def review_post(p: Parsed) -> str | None:
    """Check a reviewer's review post. None if the command isn't aimed at one; otherwise
    "" if it's well formed, or what's wrong with it."""
    if not any(review_aimed(c) for c in p.tool("gh")):
        return None
    cmds = p.cmds
    if any(c.depth for c in cmds):
        return "it isn't in the accepted shape"
    at = next((i for i, c in enumerate(cmds) if any(
        not w.dynamic and basename(w) == "gh" and review_aimed(parse_gh(c.words, k)) for k, w in enumerate(c.words))), None)
    if at is None or at > 1:
        return "it isn't in the accepted shape"
    if at == 1:  # only a plain `cd <path> &&` may come first
        cd = cmds[0]
        if ([w.text for w in cd.words[:1]] != ["cd"] or len(cd.words) != 2 or not cd_target_plain(cd)
                or cd.heredocs or cmds[1].joiner != "&&"):
            return "it isn't in the accepted shape"
    post = cmds[at]
    shape = review_shape(post)
    if shape is None or len(post.heredocs) != (0 if shape in ("inline", "file-comment") else 1):
        return "it isn't in the accepted shape"
    heredoc = post.heredocs[0] if post.heredocs else None
    if heredoc and not heredoc.quoted:
        return "it isn't in the accepted shape"
    if heredoc and not heredoc.closed:
        return "its heredoc never closes"
    # Anything after the review would run unchecked.
    if len(cmds) > at + 1:
        return "it runs more after the review"
    if shape != "api":
        return ""
    try:
        payload = json.loads(heredoc.body)
    except json.JSONDecodeError:
        return "its payload isn't JSON"
    if not isinstance(payload, dict) or payload.get("event") != "COMMENT":
        return 'its event isn\'t "COMMENT" (approving or requesting changes stays with the orchestrator)'
    return ""


def posting_enabled(cwd: str) -> bool:
    """True when the nearest config up from cwd says `Post reviews: yes`."""
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        config = directory / CONFIG
        if config.is_file():
            flag = POST_FLAG.search(config.read_text(encoding="utf-8", errors="replace"))
            return bool(flag) and flag.group(1).lower() == "yes"
    return False


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"orchestrate guard: {reason}",
            }
        },
        sys.stdout,
    )


# The plugin's verify script, by any path ending in `scripts/verify`. Decided on lexed words (ADR 0002), so
# quoting is resolved: the command word of a simple command, behind VAR=1 assignments, flags and the usual
# wrappers or interpreters. `grep scripts/verify` or a commit message that names it is not a run of it.
VERIFY_SCRIPT = re.compile(r"(?:^|/)scripts/verify$")
VERIFY_WRAPPERS = {"env", "nohup", "time", "exec", "command", "timeout", "bash", "sh", "zsh", "python", "python3"}


# The Bash timeout a worker's verify call must carry, in ms (the tool's ceiling). Without one the harness
# moves a call that runs past 180s to the background on its own, which the `run_in_background` check can't
# see (#301). agents/worker.md and the refusal message state this same number.
VERIFY_TIMEOUT_MS = 600000


def script_call(parsed: "Parsed", script: "re.Pattern", dynamic_ok: bool = False) -> tuple | None:
    """(the command, the words after the script's path) of the first simple command that runs a script
    matching `script`, or None. With `dynamic_ok`, a command word holding an expansion whose text still
    ends in the script's path (`$ROOT/scripts/threads`) counts as the script, so the caller can refuse it."""
    for cmd in parsed.cmds:
        for k, w in enumerate(cmd.words):
            if w.dynamic and not (dynamic_ok and script.search(w.text)):
                break
            if re.fullmatch(r"\w+=.*|-.*|\d+[smhd]?", w.text) or basename(w) in VERIFY_WRAPPERS:
                continue
            if script.search(w.text):
                return cmd, cmd.words[k + 1:]
            break
    return None


# The plugin's merge script, run as a command. Judged on the lexed command like verify and threads, so
# words that merely name it (a heredoc body, a quoted argument, a commit message, a path given to grep)
# are not a run of it (#327). The wrappers script_call skips (env, timeout, bash, python3 ...) still
# count, and so does the form it can't see: a bare `./merge` after `cd <...>scripts &&`. A renamed or copied script is out of scope (a guardrail).
MERGE_SCRIPT = re.compile(r"(?:^|/)scripts/merge$")
BARE_MERGE = re.compile(r"^\./merge$")


def runs_merge(parsed: "Parsed") -> bool:
    if script_call(parsed, MERGE_SCRIPT, dynamic_ok=True) is not None:
        return True
    # parse() lexes `sh -c '<cmd>'` into commands appended after all the outer ones, so order is lost. Once
    # a `cd <..>scripts` appears anywhere in the line, a bare `./merge` anywhere counts: it errs towards
    # denying (`cd scripts; cd /tmp; ./merge`) rather than miss `cd scripts && sh -c ./merge && cd -` (#327).
    cd_scripts = any(len(c.words) == 2 and basename(c.words[0]) == "cd" and re.search(r"scripts/?$", c.words[1].text)
                     for c in parsed.cmds)
    return cd_scripts and any(script_call(Parsed([c]), BARE_MERGE) is not None for c in parsed.cmds)


def runs_verify(parsed: "Parsed") -> bool:
    return script_call(parsed, VERIFY_SCRIPT) is not None


# The plugin's threads script (ADR-0006), judged like verify, by role. A worker closes its own threads; a
# reviewer reads them and reopens one, but never closes a finding of its own; a scout only reads.
THREADS_SCRIPT = re.compile(r"(?:^|/)scripts/threads$")
THREADS_FORMS = {
    "reviewer": "`threads <pr>`, `threads <pr> --all` and `threads <pr> reopen <id>... --why <why>`",
    "scout": "`threads <pr>` and `threads <pr> --all`",
}


def threads_form(args: list) -> str | None:
    """Which form a threads call is, from its lexed words after the script: "list", "reopen", "resolve"
    or None for anything else."""
    words = [w.text for w in args]
    if not words or not is_number(words[0]):
        return None
    rest = words[1:]
    if rest in ([], ["--all"]):
        return "list"
    # `reopen <id>... --why <why>`: one or more thread ids (scripts/threads takes several), then the reason.
    if (len(rest) >= 4 and rest[0] == "reopen" and rest[-2] == "--why"
            and not any(w.startswith("-") for w in rest[1:-2] + rest[-1:])):
        return "reopen"
    if rest[:1] == ["resolve"]:
        return "resolve"
    return None


# The only flags a worker's number-less threads call may carry, spelled in full: `--repo DIR` (and an
# abbreviation of it, which argparse would accept) aims the call at another repository. `--why` is not
# here: a worker closes its own threads, and reopening a reviewer's is left to the permission layer.
THREADS_WORKER_FLAGS = ("--all", "--fixed", "--left", "--followup", "--no-followup", "--verbose")


def numberless_threads_form(cmd: Command, args: list) -> str | None:
    """The worker's current-branch form of a threads call, with no PR number: "list" or "resolve", or None
    (`reopen` is a reviewer's move, so a worker's is neither approved nor refused here). The script is the first word (no env prefix such as `GH_REPO=` and no wrapper), and every
    flag is exactly one of THREADS_WORKER_FLAGS."""
    words = [w.text for w in args]
    if len(cmd.words) - len(args) != 1 or not words:
        return "list" if len(cmd.words) == 1 and not words else None
    if any(w.startswith("-") and w not in THREADS_WORKER_FLAGS for w in words):
        return None
    if words == ["--all"]:
        return "list"
    return "resolve" if words[0] == "resolve" else None


# The only flags the orchestrator's threads call may carry, spelled in full and each once (#389).
THREADS_ORCHESTRATOR_FLAGS = ("--all", "--fixed", "--left", "--followup", "--no-followup", "--verbose")


def orchestrator_threads_form(cmd: Command, args: list) -> bool:
    """True when the orchestrator's threads call is one of its routine forms, each naming its PR: `<pr>`,
    `<pr> --all`, or `<pr> resolve <id>... --fixed|--left <value>`, with an optional trailing `--verbose`. Like
    the worker's (#379) the script is the first word (no env prefix, no wrapper) and every flag is one of
    THREADS_ORCHESTRATOR_FLAGS in full. Anything else (`--repo`, `reopen`, an abbreviation, a number-less
    call) is left to the permission layer, so a mistaken `resolve` can't land on another PR or repository."""
    words = [w.text for w in args]
    if len(cmd.words) - len(args) != 1 or not words or not is_number(words[0]):
        return False
    if words and words[-1] == "--verbose":
        words = words[:-1]
    rest = words[1:]
    if rest in ([], ["--all"]):
        return True
    # `--left <why>` is followed by `--followup <issue>` or `--no-followup` (#442); strip it and require the `--left`.
    left_only = False
    if rest[-1:] == ["--no-followup"]:
        rest, left_only = rest[:-1], True
    elif len(rest) >= 2 and rest[-2] == "--followup" and rest[-1].isascii() and rest[-1].isdigit():
        rest, left_only = rest[:-2], True
    # `resolve` takes one or more thread ids, then exactly one of --fixed/--left with its value (scripts/threads).
    return (len(rest) >= 4 and rest[0] == "resolve" and rest[-2] in (("--left",) if left_only else ("--fixed", "--left"))
            and not any(w.startswith("-") for w in rest[1:-2] + rest[-1:]))


def threads_refusal(role: str, parsed: "Parsed") -> str | None:
    """Why a scout's or reviewer's call of scripts/threads is refused, or None. Not a threads call, or a
    worker's (every form), is None. Like the review post: an optional leading `cd <path> &&`, nothing else
    chained, no word with an expansion."""
    found = script_call(parsed, THREADS_SCRIPT, dynamic_ok=True)
    if found is None or role not in THREADS_FORMS:
        return None
    cmd, args = found
    cmds = parsed.cmds
    lead = cmds[0]
    if cmd is not lead:
        ok = (cmd is cmds[1] and [w.text for w in lead.words[:1]] == ["cd"] and len(lead.words) == 2
              and only_plain_words(lead) and not lead.heredocs and cmd.joiner == "&&")
    else:
        ok = True
    ok = ok and len(cmds) == (2 if cmd is not lead else 1) and not any(c.depth for c in cmds)
    ok = ok and only_plain_words(cmd) and not cmd.heredocs
    form = threads_form(args) if ok else None
    allowed = form == "list" or (form == "reopen" and role == "reviewer")
    if allowed:
        return None
    what = ("a reviewer never closes its own findings, so `resolve` is the worker's" if form == "resolve" and role == "reviewer"
            else "a scout only reads threads" if form in ("resolve", "reopen")
            else "it isn't in an accepted shape (an optional leading `cd <path> &&`, nothing else chained, plain words)")
    return f"this threads call is refused because {what}. The {role} may run {THREADS_FORMS[role]}."


def verdict(role: str, command: str, cwd: str, background: bool = False, timeout: object = None) -> str | None:
    """The reason to deny this command for this role, or None to let it run.

    `background` is the call's `run_in_background`: a worker that leaves a command in the background is
    woken by each completion and hands back again (#229, #255, #259), and has no command that needs it,
    so every backgrounded worker call is refused. This only sees the flag the worker set; when the harness
    itself moves a long foreground call to the background, the worker waits for it (see agents/worker.md).
    `timeout` is the call's Bash `timeout`: a worker's verify call must carry one of VERIFY_TIMEOUT_MS or more
    so the harness keeps it in the foreground (#301). Anything but a real number fails closed."""
    who, role = role_label(role), rules_role(role)
    try:
        parsed = Parsed(parse(command))
    except ParseError as err:
        return (f"the guard couldn't parse this command ({err}), so it can't check it. "
                f"Rewrite it with balanced quotes and brackets. {NEXT_STEP[role]}")
    if background and role == "worker":
        if runs_verify(parsed):
            return (f"{who} can't run verify in the background (it would hand back before verify finishes). "
                    "Run it in the foreground and let it finish. If the harness moves it to the background "
                    "after a few minutes, wait for it to exit (a Monitor) before you go on.")
        return (f"{who} can't run a command in the background (each time it reports, the worker is woken "
                "and hands back again). Run it in the foreground and wait for it to finish.")
    if role == "worker" and runs_verify(parsed) and not (
            isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout >= VERIFY_TIMEOUT_MS):
        return (f"{who} must run verify with a Bash timeout (the harness moves a call without one to the "
                f"background after 180s, and verify would hand back before it finishes). Run it again in the "
                f"foreground and pass a Bash timeout of {VERIFY_TIMEOUT_MS}.")
    if role == "reviewer":
        problem = review_post(parsed)
        if problem == "":
            if posting_enabled(cwd):
                return None
            return "the config doesn't say `Post reviews: yes`, so return your verdict without posting it."
        if problem:
            return f"this review post is refused because {problem}. {SHAPES}"
    refused = threads_refusal(role, parsed)
    if refused:
        return refused
    if runs_merge(parsed):
        return (f"{who} can't run a command that merges a PR (merging belongs to the orchestrator). "
                f"{NEXT_STEP[role]}")
    for rule in RULES[role]:
        what = rule(parsed, cwd)
        if what:
            return f"{who} can't run a command that {what}. {NEXT_STEP[role]}"
    if role == "worker":
        writes = chained_external_writes(parsed)
        if writes:
            return (f"{who} can't chain an external write with other commands: the permission layer refuses a chain "
                    f"as a whole. Run each external write as its own Bash call: {', '.join(writes)}.")
    return None


# --- A worker's chain that holds an external write (#537). ---
#
# agents/worker.md says each external write is its own Bash call, because the guard approves one simple
# command and a chain falls to the permission layer, which refuses it with no hint a background worker can read.
# A pipe is not a chain (`git push 2>&1 | tail -2` is one write); `&&`, `||`, `;` and a newline are. Local-only
# chains (`git add && git commit`, a verify chain) stay silent.

CHAIN_BREAKS = ("&&", "||", ";", "\n")


def external_write(cmd: Command) -> str | None:
    """What external write this simple command is, named for the denial, or None: `git push`, `gh pr create`,
    `gh pr edit`, `gh pr ready`, `gh pr comment`, or `scripts/threads resolve` / `reopen`."""
    words = cmd.words
    k = command_index(words)
    if k >= len(words) or words[k].dynamic:
        return None
    head = basename(words[k])
    if head == "git":
        return "git push" if parse_git(words, k).is_sub("push") else None
    if head == "gh":
        call = parse_gh(words, k)
        if call.is_sub("pr") and call.is_verb("create", "edit", "ready", "comment"):
            return f"gh pr {call.verb.text}"
        return None
    found = script_call(Parsed([cmd]), THREADS_SCRIPT, dynamic_ok=True)
    if found is not None:
        for w in found[1]:
            if not w.dynamic and w.text in ("resolve", "reopen"):
                return f"scripts/threads {w.text}"
    return None


def chained_external_writes(parsed: "Parsed") -> list:
    """The external writes of a line that chains commands, in order; none for a single command, a pipe, or a
    chain with no external write."""
    if not any(c.joiner in CHAIN_BREAKS for c in parsed.cmds):
        return []
    return [w for w in (external_write(c) for c in parsed.cmds) if w]


# --- Routine steps the guard approves outright (#358). ---
#
# Auto mode's classifier refused a worker's plain steps: editing in its own worktree, pushing its ticket
# branch, editing its own PR, resolving its own threads. A PreToolUse `allow` skips that layer (the user's own
# deny and ask rules still bind it). The guard says `allow` only for a call its rules already accepted AND that
# is one of the role's routine writes, in a shape it reads whole: one simple command of plain words, no chain.
# Everything else stays silent, so the permission layer applies as before.

EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
REMOTE_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+?(?:\.git)?")
CREDENTIAL_OPTION = "credential.helper=!gh auth git-credential"
BOARD_SCRIPT = re.compile(r"(?:^|/)scripts/board$")
PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REPO_FLAG = re.compile(r"-R.*|--repo(?:=.*)?")
SETTINGS_FILE = re.compile(r"settings[^/]*\.json")


def worker_worktree(cwd: str) -> Path | None:
    """The worker's own worktree: the `.claude/worktrees/agent-*` directory that holds cwd, or None."""
    if not cwd:
        return None
    try:
        start = Path(cwd).resolve()
    except OSError:
        return None
    for directory in (start, *start.parents):
        if (directory.name.startswith("agent-") and directory.parent.name == "worktrees"
                and directory.parent.parent.name == ".claude"):
            return directory
    return None


def worktree_branch(worktree: Path) -> str | None:
    """The branch checked out in a linked worktree, read from its HEAD file (no git subprocess)."""
    try:
        gitdir = (worktree / (worktree / ".git").read_text().split("gitdir:", 1)[1].strip().splitlines()[0]).resolve()
        head = (gitdir / "HEAD").read_text().strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) and len(head) > len(prefix) else None


def edit_decision(tool_name: str, tool_input: object, cwd: str) -> tuple | None:
    """("allow" | "deny", reason) for a worker's Edit, Write, MultiEdit or NotebookEdit, or None for silence.
    A worker's file edits belong inside its own worktree (#320); a path in a temp directory is scratch."""
    worktree = worker_worktree(cwd)
    key = EDIT_TOOLS.get(tool_name)
    path = tool_input.get(key) if key and isinstance(tool_input, dict) else None
    if worktree is None or not isinstance(path, str) or not path:
        return None  # not in an agent worktree, or no path to judge: the permission layer decides
    target = Path(os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path)))
    if target == worktree or worktree in target.parents:
        # Writes that change the worker's own permissions or git internals are refused: a background worker
        # has nobody to answer the prompt they would get (#567).
        parts = target.relative_to(worktree).parts if target != worktree else ()
        if parts[:1] == (".git",) or (len(parts) >= 2 and parts[-2] == ".claude" and SETTINGS_FILE.fullmatch(parts[-1])):
            return "deny", (f"the worker role can't edit {path}: it is git internals or a settings file. "
                            "Change only your ticket's files.")
        # Only `default` mode reaches this allow (an Edit there would prompt). `acceptEdits` and `bypassPermissions`
        # pass the edit anyway, and under `auto` the classifier skips a subagent's edit before a hook allow
        # matters (#378, #407). Kept: it costs nothing and covers a worker run in `default`.
        return "allow", "routine worker step"
    # Outside the worktree is a deny even in a temp dir: `acceptEdits` covers only the session's own dirs, so
    # a Write to /tmp/... prompted (#567).
    return "deny", (f"the worker role can't edit {path}: it is outside your worktree ({worktree}). "
                    "Write scratch files inside your worktree (stage by explicit path so they never ship), "
                    "or pass a PR body with `--body-file -`.")


def one_plain_command(parsed: "Parsed") -> Command | None:
    """The only command of the line when it is a single top-level simple command of plain words."""
    if len(parsed.cmds) != 1:
        return None
    cmd = parsed.cmds[0]
    return cmd if cmd.depth == 0 and only_plain_words(cmd) and not cmd.heredocs and cmd.words else None


def one_body_heredoc_command(parsed: "Parsed") -> Command | None:
    """A worker's `gh pr create|edit ... --body-file - <<'EOF' ... EOF` (#567): the only command of the line, plain
    words, and one quoted heredoc, which is data the shell never runs (an unquoted delimiter would expand
    `$(...)` in the body). Returned without the heredoc, so the usual gh checks judge the rest."""
    if len(parsed.cmds) != 1:
        return None
    cmd = parsed.cmds[0]
    if (cmd.depth or not cmd.words or len(cmd.heredocs) != 1 or not cmd.heredocs[0].quoted or not cmd.heredocs[0].closed
            or cmd.redirects or not only_plain_words(cmd) or cmd.words[0].text != "gh"):
        return None
    args = [w.text for w in cmd.words]
    if args[:2] != ["gh", "pr"] or not any(a == "--body-file=-" or (a == "-" and args[i - 1] in ("--body-file", "-F"))
                                             for i, a in enumerate(args)):
        return None
    return Command(cmd.words, cmd.redirects, [], cmd.depth, cmd.joiner, cmd.ends)


def github_slug(url: str) -> str | None:
    m = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", url)
    return m.group(1).lower() if m else None


def is_origin_url(url: str, cwd: str) -> bool:
    """The HTTPS push form (for the credential helper) is approved only for the repo `origin` names."""
    slug = github_slug(url)
    if slug is None or not REMOTE_URL.fullmatch(url):
        return False
    for git_dir in common_git_dirs(cwd):
        try:
            text = (git_dir / "config").read_text()
        except (OSError, UnicodeDecodeError):
            continue
        m = re.search(r'\[remote "origin"\][^\[]*?^\s*url\s*=\s*(\S+)', text, re.MULTILINE)
        if m:
            return github_slug(m.group(1)) == slug
    return False


def own_script(cmd: Command, args: list, cwd: str, name: str) -> bool:
    """The script word of a threads, merge or board call is this plugin's own scripts/<name>, not a
    same-named file elsewhere (a worker can write one in its worktree)."""
    word = cmd.words[len(cmd.words) - len(args) - 1].text
    return Path(os.path.realpath(os.path.join(cwd or ".", word))) == PLUGIN_SCRIPTS / name


# The flags the orchestrator's merge call may carry, spelled in full (argparse accepts abbreviations, and
# `--cwd` aims the script at another repository), each taking one value. `--also-approved` and `--ticket`
# repeat; the rest are given once (#441).
MERGE_VALUE_FLAGS = ("--method", "--approved-head", "--ticket", "--also-approved", "--worktree", "--timeout", "--interval")
MERGE_REPEATABLE = ("--ticket", "--also-approved")


def orchestrator_merge_form(cmd: Command, args: list) -> bool:
    """True when the orchestrator's merge call is `<pr> [flags]`: the script first (no env prefix, no wrapper),
    a PR number, then only MERGE_VALUE_FLAGS, each with one value, in full (`--flag value` or `--flag=value`)."""
    words = [w.text for w in args]
    if len(cmd.words) - len(args) != 1 or not words or not is_number(words[0]):
        return False
    seen, j = set(), 1
    while j < len(words):
        name, eq, _ = words[j].partition("=")
        if name not in MERGE_VALUE_FLAGS or (name in seen and name not in MERGE_REPEATABLE):
            return False
        if not eq:
            j += 1
            if j >= len(words) or words[j].startswith("-"):
                return False
        seen.add(name)
        j += 1
    return True


BOARD_FLAGS = ("--dry-run", "--verbose")


def orchestrator_board_form(cmd: Command, args: list) -> bool:
    """True when the orchestrator's board call carries only BOARD_FLAGS, each once, in full, optionally after
    exactly `--repo .` (the run skill's form): any other `--repo` aims the sync at another repository, and a
    positional word is refused (#441)."""
    words = [w.text for w in args]
    if words[:2] == ["--repo", "."]:  # the run skill's own sync (`scripts/board --repo .`): the current repo only
        words = words[2:]
    return len(cmd.words) - len(args) == 1 and len(set(words)) == len(words) and all(w in BOARD_FLAGS for w in words)


# The brief's branch name, `<n>-<slug>` (the config's `Branch:` convention), as a worker's first git step makes it.
BRANCH_NAME = re.compile(r"\d+-[a-z0-9][a-z0-9-]*")


def branch_step_ok(args: list, cwd: str) -> bool:
    """A worker's `git switch -c <n>-<slug> origin/<default>` or `git switch -C <n>-<slug> origin/<that branch>`
    (agents/worker.md, Orient), with `-q` allowed. Another name form, another start point, or a branch
    that is a default branch is not the step (#567). It only moves the worker's own worktree."""
    if any(a.dynamic or a.glob for a in args):
        return False
    rest = [a.text for a in args if a.text not in ("-q", "--quiet")]
    if len(rest) != 3:
        return False
    flag, name, start = rest
    if flag not in ("-c", "-C") or not BRANCH_NAME.fullmatch(name) or name in default_branches(cwd):
        return False
    if flag == "-c":
        return start in {f"origin/{d}" for d in default_branches(cwd)}
    return start == f"origin/{name}"


def routine_git(cmd: Command, parsed: "Parsed", cwd: str) -> bool:
    """A worker's `git add`, `git commit` or push of its own ticket branch, from inside its worktree."""
    worktree = worker_worktree(cwd)
    if worktree is None or basename(cmd.words[0]) != "git" or len(parsed.calls) != 1:
        return False
    call = parsed.calls[0]
    if call.is_sub("add", "commit"):
        return not call.options
    if call.is_sub("switch"):
        return not call.options and branch_step_ok(call.args, cwd)
    if not call.is_sub("push"):
        return False
    if [(n, v.text if v else None) for n, v in call.options] not in ([], [("-c", CREDENTIAL_OPTION)]):
        return False
    args = [a.text for a in call.args if a.text not in ("-u", "--set-upstream")]
    branch = worktree_branch(worktree)
    if branch is None or branch in default_branches(cwd) or len(args) != 2:
        return False
    remote, ref = args
    return (remote == "origin" or is_origin_url(remote, cwd)) and ref in (branch, f"{branch}:{branch}")


# The `gh pr edit` flags a worker's own-PR edit may carry, each taking one value.
PR_EDIT_VALUE_FLAGS = ("--body-file", "-F", "--body", "-b", "--title", "-t")


def numberless_edit(args: list) -> bool:
    """True when every word of a `gh pr edit` is one of PR_EDIT_VALUE_FLAGS and its value. Any other word,
    a number, a branch or a URL, could name a PR that is not the checked-out branch's, so it fails closed."""
    j = 0
    while j < len(args):
        name, eq, _ = args[j].partition("=")
        if name not in PR_EDIT_VALUE_FLAGS or (not eq and j + 1 >= len(args)):
            return False
        j += 1 if eq else 2
    return bool(args)


# The session scratchpad: `<tmp>/claude-<uid>/<project>/<session>/scratchpad/...` under a temp root.


def scratchpad_pattern(session: str) -> "re.Pattern":
    """This session's scratchpad, named by the hook input's `session_id`. With no session id nothing can be
    matched, so no scratchpad path is approved (fail closed)."""
    return re.compile(r"/claude-[^/]+/[^/]+/%s/scratchpad(?:/.+)?" % re.escape(session)) if session else re.compile(r"(?!)")


def body_file_ok(value: str, cwd: str, session: str = "") -> bool:
    """A worker's `--body-file` is `-` (stdin) or a path that, resolved against cwd with symlinks followed, sits
    inside its own worktree or the session scratchpad: any other readable file (a key, `../../.env`) would
    become a PR body (#434). The word is literal (an unquoted `~` is not a plain word), so it is not expanded."""
    if value == "-":
        return True
    worktree = worker_worktree(cwd)
    if worktree is None or not value:
        return False
    target = Path(os.path.realpath(os.path.join(cwd, value)))
    if worktree in target.parents:
        return True
    pad = scratchpad_pattern(session)
    return any(str(target).startswith(t + "/") and pad.fullmatch(str(target)[len(t):]) for t in TEMP_ROOTS)


def body_files_ok(args: list, cwd: str, session: str = "") -> bool:
    """Every `--body-file`, `--body-file=<path>` and `-F <path>` word of a `gh pr` call passes body_file_ok.
    The attached shorthand (`-F/etc/x`, `-F=x`) is read as pflag reads it; a
    cluster holding an `F` after other letters (`-dF x`) is a form this parser does not read, so it fails closed."""
    j = 0
    while j < len(args):
        word = args[j]
        if re.match(r"-[A-Za-z0-9]+F", word) and not word.startswith("-F"):
            return False  # a cluster such as `-dF x`: not read, so it fails closed
        if word.startswith("-F") and len(word) > 2:
            # pflag's attached shorthand: `-F<path>` and `-F=<path>` both name the path after the F
            value = word[2:]
            if not body_file_ok(value[1:] if value.startswith("=") else value, cwd, session):
                return False
            j += 1
            continue
        name, eq, value = word.partition("=") if word.startswith("--") else (word, "", "")
        j += 1
        if name in ("--body-file", "-F"):
            if not eq:
                if j >= len(args):
                    return False
                value, j = args[j], j + 1
            if not body_file_ok(value, cwd, session):
                return False
    return True


def routine_pr(call: Call, cwd: str = "", session: str = "") -> bool:
    """A worker's draft PR, or its own PR made ready or edited. Ready and edit are approved only in the
    current-branch form, with no PR number (#379): `gh` resolves the PR from the checked-out branch, and the
    guard can't tell which PR a number names, so a call naming one is left to the permission layer."""
    if not call.is_sub("pr") or any(a.dynamic for a in call.args):
        return False
    args = [a.text for a in call.args]
    if not body_files_ok(args, cwd, session):
        return False
    if call.is_verb("create"):
        return "--draft" in args
    if call.is_verb("ready"):
        return args == []
    return call.is_verb("edit") and numberless_edit(args)


def config_path(cwd: str) -> Path | None:
    """The nearest `.claude/orchestration.md` up from cwd."""
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG).is_file():
            return directory / CONFIG
    return None


# --- The orchestrator's chained plugin-script commands (#407). ---
#
# A background orchestrator has nobody to answer a prompt, and its reconcile pass is a chain of the plugin's
# own scripts (`S=...; $S/snapshot --repo . 2>&1 | tail -n 5; $S/claims ...; echo ---claims`). Allow rules
# can't see through a shell variable, so the guard reads the chain whole and approves it only when EVERY
# simple command is on the list below. One other command, an `||`, a background `&`, a subshell, a heredoc, an
# expansion it can't resolve, or any redirect but `2>&1` leaves the whole line to the permission layer.

CHAIN_JOINERS = (None, ";", "&&", "|", "\n")
CHAIN_PLAIN = frozenset(("tail", "head", "echo", "printf", "sort", "grep", "wc"))
# An assignment that changes how the shell finds or starts commands is never a plain variable.
UNSAFE_VARIABLE = re.compile(r"PATH|IFS|ENV|CDPATH|SHELL|HOME|(?:BASH|LD|DYLD|PS)\w*")
ASSIGNMENT = re.compile(r"([A-Za-z_]\w*)=(.*)", re.DOTALL)
SKILL_DIR = PLUGIN_SCRIPTS.parent / "skills" / "run"  # what `${CLAUDE_SKILL_DIR}` is in the run skill


def resolve_word(w: Word, env: dict, text: str | None = None) -> str | None:
    """The word's text with each `$x` / `${x}` replaced by its known value, or None when one is not known
    (an unset or computed variable, a command substitution, a glob). Values in `env` are texts already."""
    text = w.text if text is None else text
    if w.glob:
        return None
    names = iter(w.names)
    out = []
    for ch in text:
        if ch != EXP:
            out.append(ch)
            continue
        value = env.get(next(names, None))
        if value is None:
            return None
        out.append(value)
    return "".join(out)


def chain_plain_args(args: list) -> bool:
    return not any(a.dynamic or a.glob for a in args)


# The only options a chained `sort` or `printf` may carry. `sort -o/--output/--compress-program` write a file
# or run a program (getopt accepts any prefix of a long option), and `printf -v` assigns a shell variable the
# guard's `env` can't see, so a flag outside these is refused rather than recognised (#554).
CHAIN_SORT_FLAG = re.compile(r"-[nrufbdMV]+|-k\d+(?:,\d+)?")


CHAIN_LINES = re.compile(r"(?:-n\s*\d+|-\d+)?")


def chain_flags_ok(name: str, args: list) -> bool:
    if name in ("tail", "head"):
        # A read-only output filter: `-n <digits>` or `-<digits>` and nothing else, so no `-f` (which never
        # returns), no `-c`, and no file argument.
        return CHAIN_LINES.fullmatch(" ".join(a.text for a in args)) is not None
    if name == "sort":
        return all(not a.text.startswith("-") or a.text == "-" or CHAIN_SORT_FLAG.fullmatch(a.text) for a in args)
    if name == "printf":
        return not (args and args[0].text.startswith("-"))
    return True


# The plugin scripts a chain may call besides merge, threads and board (which keep their own forms): the
# read-only pass scripts. launch, sandbox-git, rescue, verify and guard-corpus are not here, so they fall to
# the permission layer as before. Their options are held to SAFE_SCRIPT_FLAGS, spelled in full (argparse
# accepts abbreviations); `--repo` names the current directory only, and `--out` is snapshot's own file.
CHAIN_SCRIPTS = frozenset(("snapshot", "rebuild-state", "claims", "frontier", "holds", "gate", "ci-times",
                           "watch-ci", "worker-report", "lead-route", "lead-brief", "prompts"))
SAFE_SCRIPT_FLAGS = frozenset(("--parent", "--ticket", "--all", "--pr", "--base", "--branch", "--reviewer", "--since",
                               "--now", "--session", "--lead", "--cap", "--touch-map", "--literal", "--timeout", "--retry-delay",
                               "--interval", "--no-run-after", "--stuck-after", "--verbose", "--watermark"))
SNAPSHOT_FILE = ".claude/orchestrate-snapshot.json"


def gate_cwd_ok(value: str | None, cwd: str) -> bool:
    """gate's `--cwd` (#562): a literal directory that resolves under `<repo>/.claude/worktrees/`, the worktree
    of a PR's branch. Any other value aims the gate's checks at a place the run didn't make."""
    if not value or value.startswith("-"):
        return False
    real = Path(os.path.realpath(os.path.join(cwd or ".", value)))
    return any((root.resolve() / ".claude" / "worktrees") in real.parents for root in repo_roots(cwd))


def chain_script_args_ok(name: str, args: list, cwd: str = "") -> bool:
    texts = [a.text for a in args]
    i = 0
    while i < len(texts):
        arg = texts[i]
        i += 1
        if arg.startswith("-") and not arg.startswith("--"):
            return False
        if not arg.startswith("--"):
            continue
        flag, eq, value = arg.partition("=")
        if flag in ("--repo", "--out", "--snapshot", "--cwd") and not eq:
            value, i = (texts[i] if i < len(texts) else None), i + 1
        if flag == "--cwd" and name == "gate" and gate_cwd_ok(value, cwd):
            continue
        if flag == "--repo" and value == ".":
            continue
        if flag == "--out" and name == "snapshot" and value == SNAPSHOT_FILE:
            continue
        if flag == "--snapshot" and name != "snapshot" and value == SNAPSHOT_FILE:
            continue  # the pass's own snapshot file, read by claims, frontier and lead-route
        if flag not in SAFE_SCRIPT_FLAGS:
            return False
    return True


def chain_cat_ok(args: list, cwd: str) -> bool:
    """`cat` of files under the repo's `.claude/` directory only."""
    config = config_path(cwd)
    files = [a.text for a in args if not a.text.startswith("-")]
    if config is None or not files:
        return False
    base = config.parent.resolve()
    return all(base in Path(os.path.realpath(os.path.join(cwd or ".", f))).parents for f in files)


def chain_script_ok(cmd: Command, env: dict, cwd: str) -> bool:
    """The command word is a file directly in this plugin's own scripts/ directory (by resolved path), called
    with plain words. merge, threads and board keep their exact argument forms."""
    resolved = resolve_word(cmd.words[0], env)
    if resolved is None:
        return False
    path = Path(os.path.realpath(os.path.join(cwd or ".", resolved)))
    args = cmd.words[1:]
    if path.parent != PLUGIN_SCRIPTS or path.suffix == ".py" or not path.is_file() or not chain_plain_args(args):
        return False
    form = {"merge": orchestrator_merge_form, "threads": orchestrator_threads_form,
            "board": orchestrator_board_form}.get(path.name)
    if form:
        return form(cmd, args)
    return path.name in CHAIN_SCRIPTS and chain_script_args_ok(path.name, args, cwd)


def chain_assignment_ok(cmd: Command, env: dict, cwd: str) -> bool:
    """A pure assignment command (every word `name=value`) whose values name a place inside the plugin; it
    records them in `env`."""
    for w in cmd.words:
        m = ASSIGNMENT.fullmatch(w.text)
        value = resolve_word(w, env, m.group(2)) if m else None
        if not m or value is None or UNSAFE_VARIABLE.fullmatch(m.group(1)):
            return False
        # A variable may only name a place inside the plugin (what `$S/<script>` needs): another
        # value could change how a script, or the `gh`/`git` it runs, behaves (#554).
        target = Path(os.path.realpath(os.path.join(cwd or ".", value)))
        if target != PLUGIN_SCRIPTS.parent and PLUGIN_SCRIPTS.parent not in target.parents:
            return False
        env[m.group(1)] = value
    return True


def report_heredoc(cmds: list, k: int, env: dict, cwd: str) -> bool:
    """`cat <<'EOF' | "$S/worker-report" ...` (#567): a bare `cat` of one quoted heredoc, piped into the plugin's
    worker-report. The body is data, never run (a quoted delimiter stops the shell expanding it), so it is as
    harmless as the `printf ... |` the same script already takes."""
    cmd = cmds[k]
    if (len(cmd.words) != 1 or cmd.words[0].dynamic or cmd.words[0].text != "cat" or cmd.redirects or cmd.depth
            or len(cmd.heredocs) != 1 or not cmd.heredocs[0].quoted or not cmd.heredocs[0].closed or cmd.ends != "|"
            or k + 1 >= len(cmds)):
        return False
    nxt = cmds[k + 1]
    resolved = resolve_word(nxt.words[0], env) if nxt.words else None
    return resolved is not None and Path(os.path.realpath(os.path.join(cwd or ".", resolved))) == PLUGIN_SCRIPTS / "worker-report"


def chain_approved(parsed: "Parsed", cwd: str) -> bool:
    """True when every simple command of the line is a plugin script, one of CHAIN_PLAIN, a `cat` under
    `.claude/`, or a variable assignment, joined by `;`, `&&`, `|` or a newline."""
    env = {"CLAUDE_SKILL_DIR": str(SKILL_DIR), "PWD": os.path.realpath(cwd or ".")}
    for k, cmd in enumerate(parsed.cmds):
        if cmd.depth or not cmd.words or cmd.joiner not in CHAIN_JOINERS or cmd.ends not in CHAIN_JOINERS:
            return False
        if cmd.heredocs:
            if not report_heredoc(parsed.cmds, k, env, cwd):
                return False
            continue
        if not allowed_redirects(cmd):
            return False
        first = cmd.words[0]
        if ASSIGNMENT.fullmatch(first.text):
            if not chain_assignment_ok(cmd, env, cwd):
                return False
            continue
        args = cmd.words[1:]
        if not first.dynamic and first.text in CHAIN_PLAIN:
            # The same read bound as the compound form (#566): `grep x ~/.ssh/id` is no routine step.
            if (not chain_plain_args(args) or not chain_flags_ok(first.text, args)
                    or not read_paths_ok(first.text, [a.text for a in args], cwd or ".", cwd)):
                return False
        elif not first.dynamic and first.text == "cat":
            if not chain_plain_args(args) or not chain_cat_ok(args, cwd):
                return False
        elif not chain_script_ok(cmd, env, cwd):
            return False
    return True


# --- Read-only compound commands for every role, and a worker's test runs and edits (#562). ---
#
# An agent that reads with `grep -n x f | sed -n 1,60p; ls | head` hands the permission layer a chain it
# prompts on, though no step writes. compound_approved reads the line whole and approves it only when EVERY
# simple command is a form the guard already approves for the role, a read on the lists below, or (a worker,
# inside its own worktree) a test run or an edit of its own files. It runs after the role's deny rules, which
# are unchanged. Anything the lexer can't fully read, or one step outside the lists, leaves the whole line to
# the permission layer (ADR-0002).

COMPOUND_JOINERS = (None, ";", "&&", "||", "|", "\n")
# Commands whose arguments need no check beyond "plain words" (the flags that write are screened below).
READ_PLAIN = frozenset(("cat", "wc", "cut", "tr", "ls", "diff", "echo", "true", "test"))
LOOP_VAR = re.compile(r"[A-Za-z_]\w*")
LOOP_ITEM = re.compile(r"[A-Za-z0-9_./:@=+,%][A-Za-z0-9_./:@=+,%-]*")
# A flag that writes or runs a program, by the prefix git, ripgrep or find would accept (getopt takes
# abbreviations): refused whatever its spelling.
GIT_READ_VERBS = frozenset(("status", "log", "diff", "show", "ls-remote", "ls-files", "rev-parse", "rev-list", "grep"))
GIT_UNSAFE_LONG = ("--output", "--ext-diff", "--upload-pack", "--exec", "--open-files-in-pager")
GIT_BRANCH_READS = frozenset(("-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "--show-current"))
GIT_FETCH_FLAGS = frozenset(("-q", "--quiet", "-v", "--verbose", "--no-tags", "--tags"))
FIND_UNSAFE = re.compile(r"-(?:exec|execdir|ok|okdir|delete|fprint\w*|fls)")
RG_UNSAFE = ("--pre", "--hostname-bin")
SED_FLAG_CLUSTER = re.compile(r"-([nErsu]*)(e?)")
SED_SUFFIX = re.compile(r"[A-Za-z0-9._~-]*")
UNITTEST_FLAGS = frozenset(("-v", "-q", "-b", "-f", "-c", "-k", "-s", "-t", "-p", "--verbose", "--quiet", "--buffer",
                            "--failfast", "--catch", "--locals", "--start-directory", "--top-level-directory",
                            "--pattern"))


def sed_script_ok(script: str) -> bool:
    """True when a sed script holds only commands that print, delete, quit or substitute with the plain
    flags: no `w`, `e`, `r`, `y`, label or text command, and no flag after `s///` but `gpiImM` and digits.
    A `[...]` in a regex must close, and may not hold the delimiter, so that GNU and BSD sed split the
    script at the same places and no hidden `w` can ride behind a delimiter one of them reads differently."""
    n = len(script)

    def skip(i: int, chars: str = " \t") -> int:
        while i < n and script[i] in chars:
            i += 1
        return i

    def regex_end(i: int, delim: str) -> int:
        """Index after the closing delimiter of the regex starting at i, or -1."""
        while i < n:
            c = script[i]
            if c == "\\":
                i += 2
            elif c == "\n":
                return -1
            elif c == delim:
                return i + 1
            elif c == "[":
                i += 1
                if i < n and script[i] == "^":
                    i += 1
                if i < n and script[i] == "]":
                    i += 1
                while i < n and script[i] != "]":
                    if script[i] in (delim, "\n"):
                        return -1
                    if script[i] == "[" and i + 1 < n and script[i + 1] in ".:=":
                        end = script.find(script[i + 1] + "]", i + 2)
                        if end < 0:
                            return -1
                        i = end + 2
                    else:
                        i += 1
                if i >= n:
                    return -1
                i += 1
            else:
                i += 1
        return -1

    def address(i: int) -> int | None:
        """Index after the address at i, None when there is none, -1 when it is malformed."""
        if i < n and script[i] == "$":
            return i + 1
        m = re.compile(r"\d+(?:~\d+)?").match(script, i)
        if m:
            return m.end()
        if i < n and script[i] in "/\\":
            if script[i] == "\\":
                if i + 1 >= n or script[i + 1].isalnum() or script[i + 1] in " \n\\":
                    return -1
                i += 1
            end = regex_end(i + 1, script[i])
            if end < 0:
                return -1
            while end < n and script[end] in "IM":
                end += 1
            return end
        return None

    i = 0
    while True:
        i = skip(i, " \t\n;")
        if i >= n:
            return True
        end = address(i)
        if end == -1:
            return False
        if end is not None:
            i = skip(end)
            if i < n and script[i] == ",":
                i = skip(i + 1)
                m = re.compile(r"[+~]\d+").match(script, i)
                end = m.end() if m else address(i)
                if end is None or end == -1:
                    return False
                i = end
            i = skip(i)
        if i < n and script[i] == "!":
            i = skip(i + 1)
        if i >= n:
            return False
        c = script[i]
        if c in "{}":
            i += 1
            continue
        if c in "pdnN=gGhHxDP":
            i += 1
        elif c in "qQ":
            i += 1
            while i < n and script[i].isdigit():
                i += 1
        elif c == "s":
            if i + 1 >= n or script[i + 1].isalnum() or script[i + 1] in " \n\\":
                return False
            delim = script[i + 1]
            i = regex_end(i + 2, delim)
            if i < 0:
                return False
            while i < n and script[i] != delim:  # the replacement: a newline is allowed only escaped
                if script[i] == "\\":
                    i += 1
                elif script[i] == "\n":
                    return False
                i += 1
            if i >= n:
                return False
            i += 1
            while i < n and (script[i] in "gpiImM" or script[i].isdigit()):
                i += 1
        else:
            return False
        i = skip(i)
        if i < n and script[i] not in ";\n}":
            return False


def sed_args(args: list) -> tuple | None:
    """(in place?, scripts, files) for a sed call whose options are all ones the guard knows, else None. An
    in-place suffix is `-i` alone, `-i.bak`, `--in-place[=.bak]`, or the BSD form `-i ''`."""
    inplace, scripts, files, have_e, done, i = False, [], [], False, False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if done or a == "-" or not a.startswith("-"):
            if have_e or scripts:
                files.append(a)
            else:
                scripts.append(a)
        elif a == "--":
            done = True
        elif a in ("--quiet", "--silent", "--regexp-extended", "--separate", "--unbuffered"):
            continue
        elif a in ("-e", "--expression"):
            if i >= len(args):
                return None
            scripts.append(args[i])
            have_e = True
            i += 1
        elif a.startswith("--expression="):
            scripts.append(a.partition("=")[2])
            have_e = True
        elif a.startswith("-i") and SED_SUFFIX.fullmatch(a[2:]):
            inplace = True
            if a == "-i" and i < len(args) and args[i] == "":
                i += 1
        elif a == "--in-place" or (a.startswith("--in-place=") and SED_SUFFIX.fullmatch(a.partition("=")[2])):
            inplace = True
        else:
            m = SED_FLAG_CLUSTER.fullmatch(a)
            if not m or not (m.group(1) or m.group(2)):
                return None
            if m.group(2):
                if i >= len(args):
                    return None
                scripts.append(args[i])
                have_e = True
                i += 1
    if have_e and not scripts or not scripts:
        return None
    return inplace, scripts, files


def inside_worktree(path: str, here: str, worktree: Path | None) -> bool:
    """A literal relative path that resolves to a file inside the worker's worktree, not in `.git`."""
    if worktree is None or not path or path.startswith(("-", "/", "~")):
        return False
    resolved = Path(os.path.realpath(os.path.join(here, path)))
    if worktree not in resolved.parents:
        return False
    parts = resolved.relative_to(worktree).parts
    # The same files edit_decision keeps behind a prompt for the Edit tool: git internals and the settings files.
    return ".git" not in parts and not (len(parts) >= 2 and parts[-2] == ".claude" and SETTINGS_FILE.fullmatch(parts[-1]))


def remote_names(cwd: str) -> set:
    """The remote names configured in the repo's `.git/config` (and the main repo's, from a linked worktree)."""
    names = set()
    for root in repo_roots(cwd):
        try:
            text = (root / ".git" / "config").read_text()
        except (OSError, UnicodeDecodeError):
            continue
        names.update(re.findall(r'^\s*\[remote "([^"\n]+)"\]', text, re.M))
    return names


def read_bound(cwd: str) -> list:
    """Where an approved read may look (#566): the repo root(s), the caller's worktree and the system temp
    dirs. A root of `/` (repo_roots failing closed) is no bound."""
    roots = [r.resolve() for r in repo_roots(cwd)]
    worktree = worker_worktree(cwd)
    if worktree is not None:
        roots.append(worktree)
    temps = [Path("/tmp"), Path("/private/tmp")]
    if os.environ.get("TMPDIR"):
        temps.append(Path(os.path.realpath(os.environ["TMPDIR"])))
    return [r for r in roots + temps if r != Path("/")]


def in_read_bound(arg: str, here: str, cwd: str) -> bool:
    """A literal path argument that resolves (symlinks followed) inside the read bound or a reviewer's scratch
    checkout. A `~` path is outside: the shell would expand it to the home directory."""
    if arg.startswith("~"):
        return False
    real = Path(os.path.realpath(os.path.join(here, arg)))
    if any(real == r or r in real.parents for r in read_bound(cwd)):
        return True
    return any(scratch_worktree(p, cwd) for p in (real, *real.parents) if p != Path("/"))


FILE_TESTS = frozenset(("-e", "-f", "-d", "-s", "-r", "-w", "-x", "-L", "-h", "-p", "-S", "-b", "-c", "-g", "-u", "-k", "-O", "-G", "-N"))
FILE_COMPARES = frozenset(("-nt", "-ot", "-ef"))
PGREP_FLAGS = frozenset(("-f", "-x", "-l", "-a", "-c", "-i", "-n", "-o", "-q", "-d"))


def read_paths_ok(name: str, args: list, here: str, cwd: str) -> bool:
    """Every path a read command names is inside the bound. A read with no path argument (stdin in a pipe)
    passes. A short flag with `/` or `~` glued to it counts as outside; a `--flag=value` value is checked as
    a path. sed's files, awk's operands after its program and grep's operands after its pattern are told
    apart from the script; any other word counts as a path, so a pattern like `/etc/x` just falls to the prompt."""
    if name in ("echo", "true", "printf", "pgrep"):
        return True
    if name in ("test", "["):
        # Only the operand of a file test names a path (`-e <path>`, `a -nt b`): the other words are strings.
        operands = [args[i + 1] for i, a in enumerate(args[:-1]) if a in FILE_TESTS]
        operands += [args[i + d] for i, a in enumerate(args) if a in FILE_COMPARES for d in (-1, 1) if 0 <= i + d < len(args)]
        return all(o != "" and in_read_bound(o, here, cwd) for o in operands)
    check = []
    if name == "sed":
        parsed = sed_args(args)
        check = parsed[2] if parsed else []
    elif name == "awk":
        k = awk_program(args)
        check = args[k + 1:] if k is not None else []
    else:
        # getopt takes abbreviations, so any long flag that is a prefix of one that names the pattern (or, for
        # rg, takes none: `--files`, `--type-list`) counts as it. With no pattern word, every word is a path.
        def long_prefix(a: str, full: tuple) -> bool:
            flag = a.partition("=")[0]
            return a.startswith("--") and len(flag) >= 3 and any(f.startswith(flag) for f in full)

        skip = name in ("grep", "egrep", "rg") and not any(
            long_prefix(a, ("--regexp", "--file", "--files", "--type-list"))
            or (a.startswith("-") and not a.startswith("--") and ("e" in a or "f" in a)) for a in args)
        for a in args:
            if a == "-":
                continue
            if a.startswith("--"):
                value = a.partition("=")[2]
                if value:
                    check.append(value)
            elif a.startswith("-"):
                if "/" in a or "~" in a:
                    return False
            elif skip:
                skip = False
            else:
                check.append(a)
    return all(a != "" and in_read_bound(a, here, cwd) for a in check)


def awk_ok(args: list) -> bool:
    return awk_program(args) is not None


def awk_program(args: list) -> int | None:
    """Index of the program text in an awk call whose program can't write, run a command, read a command's
    output or the environment, and with no `-f` file; None otherwise."""
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a in ("-F", "-v"):
            i += 1
        elif a.startswith(("-F", "-v")) and len(a) > 2:
            continue
        elif a == "--":
            break
        elif a.startswith("-") and a != "-":
            return None
        else:
            i -= 1
            break
    if i < len(args) and not re.search(r"system|ENVIRON|ARGV|ARGC|>|\||getline|@", args[i]):
        return i
    return None


def git_paths_ok(args: list, here: str, cwd: str) -> bool:
    """Every path an approved git read names is inside the read bound (#567). `git diff --no-index a b` (and a
    `git diff a b` with a path outside the repo, which git reads the same way) reads any file the way `cat`
    does, so a word that is an absolute or `..` path, names an existing file, or follows `--` is held to the bound.
    A revision (`origin/main`, `HEAD~3..HEAD`, `rev:path`, whose path is read from the repo's own objects) is not a file
    on disk and passes."""
    no_index = any(a.startswith("--") and len(a.partition("=")[0]) >= 4 and "--no-index".startswith(a.partition("=")[0])
                   for a in args)
    after_dashes = False
    for a in args:
        if a == "--" and not after_dashes:
            after_dashes = True
            continue
        if not after_dashes and a.startswith("-"):
            # `-O<file>` reads an orderfile and `-O<cmd>` of `git grep` runs a pager: neither is a plain read.
            if a.startswith("-O") or ("=" in a and not a.startswith("--")):
                return False
            value = a.partition("=")[2] if a.startswith("--") else ""
            if value and ("/" in value or value.startswith("~")) and not in_read_bound(value, here, cwd):
                return False
            continue
        if after_dashes or no_index:
            candidate = True
        else:
            parts = a.split("/")
            candidate = (a.startswith(("/", "~")) or ".." in parts or os.path.lexists(os.path.join(here, a)))
        if candidate and not in_read_bound(a, here, cwd):
            return False
    return True


def git_read_ok(cmd: Command, cwd: str = "", here: str = ".") -> bool:
    call = parse_git(cmd.words, 0)
    if call.options or call.sub is None or call.sub.dynamic:
        return False
    sub, args = call.sub.text, [a.text for a in call.args]
    # A long flag that is a prefix of a writing one (`--outp`) counts as it: git accepts abbreviations.
    if any(a.startswith("--") and len(a.partition("=")[0]) >= 3 and any(u.startswith(a.partition("=")[0]) for u in GIT_UNSAFE_LONG)
           for a in args):
        return False
    if sub == "ls-remote":
        # Only `origin`, never a URL, a path or another configured remote: `git remote add x https://evil` followed by
        # `git ls-remote x` would send data out (#566, #567). Nothing approves `git remote add` or `set-url`.
        positional = [a for a in args if not a.startswith("-")]
        return bool(positional) and positional[0] == "origin" and "origin" in remote_names(cwd)
    if sub == "remote":
        # `git remote`, `-v`, and `get-url <name>` read the config; `show` contacts the remote, so it is not here.
        return args in ([], ["-v"], ["--verbose"]) or (len(args) == 2 and args[0] == "get-url" and not args[1].startswith("-"))
    if sub in GIT_READ_VERBS:
        return git_paths_ok(args, here, cwd)
    if sub == "branch":
        return all(a in GIT_BRANCH_READS for a in args)
    if sub == "fetch":
        # No refspec that writes a local ref (`src:dst`, `+src`) and no flag but the quiet or tag ones.
        positional = [a for a in args if not a.startswith("-")]
        if any(a.startswith("-") and a not in GIT_FETCH_FLAGS for a in args) or not positional:
            return False
        remote, refs = positional[0], positional[1:]
        return (remote == "origin" or REMOTE_URL.fullmatch(remote) is not None) and all(
            re.fullmatch(r"[\w./@-]+", r) and not r.startswith("+") for r in refs)
    return False


def gh_read_ok(cmd: Command) -> bool:
    call = parse_gh(cmd.words, 0)
    args = [a.text for a in call.args]
    if call.is_sub("issue", "pr") and call.is_verb("view", "list", "diff", "checks"):
        return not any(a in ("-w", "--web", "--watch") for a in args)
    if call.is_sub("api"):
        scan = scan_api(call.args)
        return (not scan.other and not scan.fields and scan.endpoint is not None and scan.endpoint.text != "graphql")
    return False


# The long options each read command may carry, spelled in full. getopt (and clap's `--fil`) take any prefix of a
# long option, so an abbreviation of a checked option (`grep --fil`, `rg --pre`) would slip past a check written for
# the full name. A long option outside the command's list is therefore refused, abbreviated or not (#567).
_COMMON_LONG = {"--help", "--version"}
LONG_OPTIONS = {
    "grep": {"--recursive", "--dereference-recursive", "--line-number", "--ignore-case", "--no-ignore-case", "--include",
             "--exclude", "--exclude-dir", "--files-with-matches", "--files-without-match", "--count", "--invert-match",
             "--word-regexp", "--line-regexp", "--fixed-strings", "--extended-regexp", "--perl-regexp", "--basic-regexp",
             "--only-matching", "--quiet", "--silent", "--no-messages", "--with-filename", "--no-filename", "--null",
             "--text", "--after-context", "--before-context", "--context", "--max-count", "--color", "--colour",
             "--regexp", "--file", "--byte-offset", "--initial-tab", "--binary-files", "--label", "--null-data",
             "--no-ignore", "--line-buffered", "--binary", "--directories", "--devices", "--exclude-from"},
    "ls": {"--all", "--almost-all", "--human-readable", "--recursive", "--reverse", "--directory", "--classify",
           "--color", "--sort", "--time", "--size", "--inode", "--full-time", "--group-directories-first", "--dereference",
           "--literal", "--hide", "--ignore", "--indicator-style", "--format", "--numeric-uid-gid", "--width"},
    "cat": {"--number", "--show-all", "--squeeze-blank", "--number-nonblank", "--show-ends", "--show-tabs",
            "--show-nonprinting"},
    "head": {"--lines", "--bytes", "--quiet", "--silent", "--verbose"},
    "wc": {"--lines", "--words", "--bytes", "--chars", "--max-line-length"},
    "cut": {"--bytes", "--characters", "--fields", "--delimiter", "--complement", "--only-delimited",
            "--output-delimiter"},
    "tr": {"--delete", "--squeeze-repeats", "--complement", "--truncate-set1"},
    "uniq": {"--count", "--repeated", "--unique", "--ignore-case", "--skip-fields", "--skip-chars", "--check-chars",
             "--all-repeated", "--zero-terminated"},
    "diff": {"--brief", "--unified", "--context", "--ignore-case", "--ignore-space-change", "--ignore-all-space",
             "--ignore-blank-lines", "--recursive", "--new-file", "--text", "--side-by-side", "--color",
             "--no-dereference", "--report-identical-files", "--minimal", "--show-c-function", "--ignore-tab-expansion",
             "--ignore-trailing-space", "--strip-trailing-cr", "--exclude", "--from-file", "--to-file", "--label",
             "--normal", "--ed", "--width", "--expand-tabs", "--initial-tab", "--suppress-common-lines"},
    "sed": {"--quiet", "--silent", "--regexp-extended", "--separate", "--null-data", "--posix", "--line-length",
            "--unbuffered", "--sandbox", "--debug", "--binary", "--follow-symlinks"},
    "rg": {"--files", "--type-list", "--type", "--type-not", "--glob", "--iglob", "--hidden", "--no-ignore",
           "--max-depth", "--line-number", "--no-line-number", "--ignore-case", "--smart-case", "--case-sensitive",
           "--fixed-strings", "--word-regexp", "--line-regexp", "--count", "--count-matches", "--files-with-matches",
           "--files-without-match", "--only-matching", "--context", "--after-context", "--before-context", "--json",
           "--heading", "--no-heading", "--sort", "--sortr", "--regexp", "--file", "--invert-match", "--max-count",
           "--no-messages", "--with-filename", "--no-filename", "--color", "--colour", "--multiline", "--pcre2",
           "--null", "--text", "--column", "--vimgrep", "--stats", "--max-filesize", "--no-config", "--unrestricted",
           "--quiet", "--replace", "--trim", "--follow", "--glob-case-insensitive", "--no-ignore-vcs", "--pretty"},
}
LONG_OPTIONS["egrep"] = LONG_OPTIONS["grep"]
GLOB_SAFE = re.compile(r"[A-Za-z0-9_./*?\[\]+@%:=,!^-]+")


def long_options_ok(name: str, args: list) -> bool:
    """No long option the command's list (LONG_OPTIONS) doesn't spell in full; everything after `--` is operands."""
    allowed = LONG_OPTIONS.get(name)
    if allowed is None:
        return True
    for a in args:
        if a == "--":
            break
        if a.startswith("--") and a.partition("=")[0] not in allowed | _COMMON_LONG:
            return False
    return True


def expand_words(words: list, here: str) -> list | None:
    """The texts of the words with each unquoted glob expanded the way the shell would, or None when one can't be
    trusted (#567). A glob word is relative, has no `..`, and uses only `*`, `?` and `[...]`; a flag's glob
    (`--include=*`) is kept as written, for the path check to judge its value. An expansion that names a file
    starting with `-` would turn into an option, so it is refused. The expanded names go to the read bound, so a
    symlink in the repo that leads out is caught by their resolved paths."""
    out = []
    for w in words:
        if not w.glob:
            out.append(w.text)
            continue
        text = w.text
        if (not GLOB_SAFE.fullmatch(text) or ".." in text or text.startswith(("/", "~")) or "{" in text or "$" in text):
            return None
        if text.startswith("-"):
            out.append(text)
            continue
        matches = sorted(glob.glob(os.path.join(glob.escape(here), text)))
        if len(matches) > 2000 or any(os.path.basename(m).startswith("-") for m in matches):
            return None
        out.extend(matches or [text])
    return out


def read_command_ok(cmd: Command, here: str = ".", cwd: str = "") -> bool:
    """One simple command of plain words that only reads (the allowlist of #562), every path of it inside
    the read bound (#566). A relative glob is expanded and its matches are held to the bound (#567)."""
    first = cmd.words[0]
    name, words = first.text, cmd.words[1:]
    if first.dynamic or any(w.dynamic for w in words) or (first.glob and name != "["):
        return False
    if name in ("git", "gh"):
        if any(w.glob for w in words):
            return False
        return git_read_ok(cmd, cwd, here) if name == "git" else gh_read_ok(cmd)
    expanded = expand_words(words, here)
    if expanded is None:
        return False
    args = expanded
    if not long_options_ok(name, args) or not read_paths_ok(name, args, here, cwd):
        return False
    if name in READ_PLAIN:
        return True
    if name == "pgrep":
        return all(a in PGREP_FLAGS or not a.startswith("-") for a in args)
    if name == "[":
        return bool(args) and args[-1] == "]"
    if name == "printf":
        return chain_flags_ok("printf", words)
    if name == "sort":
        return chain_flags_ok("sort", words)
    if name == "head":
        return True
    if name == "tail":
        # getopt takes abbreviations (`--fol`), so a long option is allowed only by its full name.
        return not any(re.fullmatch(r"-[A-Za-z0-9]*[fF][A-Za-z0-9]*", a)
                       or (a.startswith("--") and a.partition("=")[0] not in ("--lines", "--bytes", "--quiet", "--silent"))
                       for a in args)
    if name == "uniq":
        return len([a for a in args if not a.startswith("-")]) <= 1
    if name in ("grep", "egrep", "rg"):
        return not any(a.startswith(RG_UNSAFE) for a in args)
    if name == "find":
        return not any(FIND_UNSAFE.fullmatch(a) for a in args)
    if name == "awk":
        return awk_ok(args)
    if name == "sed":
        parsed = sed_args(args)
        return parsed is not None and not parsed[0] and all(sed_script_ok(s) for s in parsed[1])
    return False


def worker_work_ok(cmd: Command, here: str, worktree: Path | None) -> bool:
    """A worker's own test runs and edits (#562): this plugin's verify, `python3 -m unittest|py_compile`,
    `python3 tests/run_parallel.py`, and `sed -i` on literal relative paths inside its worktree."""
    if worktree is None or cmd.words[0].dynamic or any(w.dynamic or w.glob for w in cmd.words):
        return False
    name, args = cmd.words[0].text, [w.text for w in cmd.words[1:]]
    if Path(os.path.realpath(os.path.join(here, name))) == PLUGIN_SCRIPTS / "verify":
        return True
    if name in ("python3", "/usr/bin/python3") and args:
        if args[0] == "-m" and len(args) > 1 and args[1] == "unittest":
            return all(not a.startswith("-") or a.partition("=")[0] in UNITTEST_FLAGS for a in args[2:])
        if args[0] == "-m" and len(args) > 2 and args[1] == "py_compile":
            return all(inside_worktree(a, here, worktree) for a in args[2:])
        return Path(os.path.realpath(os.path.join(here, args[0]))) == worktree / "tests" / "run_parallel.py"
    if name == "sed":
        parsed = sed_args(args)
        return (parsed is not None and parsed[0] and bool(parsed[2]) and all(sed_script_ok(s) for s in parsed[1])
                and all(inside_worktree(f, here, worktree) for f in parsed[2]))
    return False


def scratch_worktree(real: Path, cwd: str) -> bool:
    """A reviewer's scratch checkout (#562): a directory outside the repo whose `.git` file points into the
    repo's `.git/worktrees/`. Read off the `.git` file, so no git subprocess runs on a hook call."""
    roots = [r.resolve() for r in repo_roots(cwd)]
    if any(real == r or r in real.parents for r in roots):
        return False
    try:
        line = (real / ".git").read_text().split("gitdir:", 1)[1].strip().splitlines()[0]
        gitdir = Path(os.path.realpath(real / line))
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return any(gitdir.parent == Path(os.path.realpath(r / ".git" / "worktrees")) for r in roots)


def reviewer_verify_ok(cmd: Command, here: str, cwd: str) -> bool:
    """A reviewer's `<plugin>/scripts/verify --config --cwd <scratch checkout>`, and nothing else."""
    if cmd.words[0].dynamic or any(w.dynamic or w.glob for w in cmd.words):
        return False
    if Path(os.path.realpath(os.path.join(here, cmd.words[0].text))) != PLUGIN_SCRIPTS / "verify":
        return False
    args, target, config = [w.text for w in cmd.words[1:]], None, False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--config":
            config = True
        elif a == "--cwd" and i < len(args) and target is None:
            target, i = args[i], i + 1
        elif a.startswith("--cwd=") and target is None:
            target = a.partition("=")[2]
        else:
            return False
    return config and bool(target) and scratch_worktree(Path(os.path.realpath(os.path.join(here, target))), cwd)


def allowed_redirects(cmd: Command) -> bool:
    """Only `2>&1` (or `>&2`) and output to /dev/null."""
    for op, target in cmd.redirects:
        if target.dynamic:
            return False
        if not ((op in (">", "&>") and target.text == "/dev/null") or (op == ">&" and target.text in ("1", "2"))):
            return False
    return True


def substitute(cmd: Command, values: dict) -> Command | None:
    """The command with each `$name` the values name replaced by its text, or None when a word holds an
    expansion that is not one of them."""
    words = []
    for w in cmd.words:
        if not w.dynamic:
            words.append(w)
            continue
        if not w.names or any(name not in values for name in w.names):
            return None
        names = iter(w.names)
        text = "".join(values[next(names)] if ch == EXP else ch for ch in w.text)
        # Whether the word still globs depends on the text it became: an unquoted `$R` is flagged as a glob before its
        # value is known, and `origin/main` is not one.
        words.append(Word(text, False, w.quoted, bool(re.search(r"[*?\[{~$]", text))))
    return Command(words, cmd.redirects, cmd.heredocs, cmd.depth, cmd.joiner, cmd.ends)


# A variable a program the command runs may read from its environment (`GH_HOST`, `GIT_DIR`, `PYTHONPATH`, ...). A
# plain assignment is not exported unless the name is already in the environment, so the names below, and any name
# the guard's own environment holds, are never taken as a scratch variable (#567).
RESERVED_VARIABLE = re.compile(r"(?:GH|GIT|PYTHON|LC|SSH|NODE|PERL|RUBY|EDITOR|VISUAL|PAGER|XDG|TERM|TMP|LANG|TZ|"
                               r"PATH|IFS|ENV|CDPATH|SHELL|HOME|BASH|LD|DYLD|PS|CLAUDE|ANTHROPIC)\w*")


def literal_assignment(w: Word) -> bool:
    """`NAME=word`, the word plain characters (a ref, a sha, a short path), and NAME a fresh variable no program reads."""
    m = ASSIGNMENT.fullmatch(w.text)
    return bool(m) and not w.dynamic and not w.glob and not RESERVED_VARIABLE.fullmatch(m.group(1)) and (
        m.group(1) not in os.environ) and (m.group(2) == "" or LOOP_ITEM.fullmatch(m.group(2)) is not None)


def sleep_ok(cmd: Command) -> bool:
    """`sleep N` with a plain number, the pause of a wait loop."""
    return (len(cmd.words) == 2 and cmd.words[0].text == "sleep" and not cmd.redirects
            and re.fullmatch(r"\d+(?:\.\d+)?", cmd.words[1].text) is not None)


REVIEW_SCRATCH = re.compile(r"pr-\d+[A-Za-z]?(?:-[\w.-]+)?")


def reviewer_worktree_ok(cmd: Command, cwd: str) -> bool:
    """A reviewer's `git worktree add --detach <dir> <ref>` (agents/reviewer.md): the one scratch root the guard approves
    is a directory named `pr-<n>` (a letter or `-<suffix>` after it) under a temp dir (`/tmp/c<ticket>/scratch/pr-5`, the
    session scratchpad), outside the repo (#567). The rules already refuse the other worktree forms."""
    if any(w.dynamic or w.glob for w in cmd.words):
        return False
    call = parse_git(cmd.words, 0)
    if call.options or not call.is_sub("worktree") or not call.args or call.args[0].text != "add":
        return False
    if reviewer_git_problem(call, cwd):
        return False
    names = [a.text for a in call.args[1:] if not a.text.startswith("-")]
    if not names or not names[0].startswith("/"):
        return False
    target = Path(os.path.realpath(names[0]))
    in_temp = any(str(target).startswith(t + "/") for t in TEMP_ROOTS)
    return (in_temp and REVIEW_SCRATCH.fullmatch(target.name) is not None
            and (len(names) == 1 or re.fullmatch(r"[\w./@^~-]+", names[1]) is not None))


def compound_approved(role: str | None, parsed: "Parsed", cwd: str, session: str = "") -> bool:
    """True when every simple command of the line is approved for the role (see the section comment). The
    loop form is `for NAME in WORD...; do ...; done`, with literal words and one level."""
    orchestrator = role is None
    if orchestrator and not config_path(cwd):
        return False
    worktree = worker_worktree(cwd)
    if role == "worker" and worktree is None:
        return False
    here, env = os.path.realpath(cwd or "."), {"CLAUDE_SKILL_DIR": str(SKILL_DIR)}
    loop, pending = None, None  # the open loop's (name, values); a `for` waiting for its `do`
    waiting = None  # inside `until|while <test>; do <body>; done`: "test" until its `do`, then "body"
    values = {}  # literal `NAME=word` assignments seen so far, for the reads that use them
    env["PWD"] = here
    for k, cmd in enumerate(parsed.cmds):
        if cmd.depth or cmd.heredocs or not cmd.words or cmd.joiner not in COMPOUND_JOINERS or cmd.ends not in COMPOUND_JOINERS:
            return False
        head = cmd.words[0]
        if head.dynamic:
            if not orchestrator or not allowed_redirects(cmd):
                return False
        elif head.text in ("until", "while") and loop is None and pending is None and waiting is None:
            # A wait loop (#567): `until [!] <read-only test>; do sleep N; done`. The test and the body must each be
            # an approved read (the body may also `sleep N`), so a loop that writes is not approved.
            rest = cmd.words[1:]
            if rest and not rest[0].dynamic and rest[0].text == "!":
                rest = rest[1:]
            if not rest or cmd.ends not in (";", "\n", "&&", "||") or cmd.joiner not in (None, ";", "&&", "\n"):
                return False
            waiting = "test"
            cmd = Command(rest, cmd.redirects, cmd.heredocs, cmd.depth, cmd.joiner, cmd.ends)
        elif waiting == "test" and head.text == "do":
            if cmd.ends not in (";", "\n"):
                return False
            waiting = "body"
            if len(cmd.words) == 1 and not cmd.redirects:
                continue
            cmd = Command(cmd.words[1:], cmd.redirects, cmd.heredocs, cmd.depth, cmd.joiner, cmd.ends)
        elif waiting == "body" and head.text == "done" and len(cmd.words) == 1 and not cmd.redirects:
            waiting = None
            continue
        elif head.text == "for" and loop is None and pending is None and waiting is None:
            words = cmd.words
            if (cmd.redirects or len(words) < 4 or words[2].text != "in" or not LOOP_VAR.fullmatch(words[1].text)
                    or cmd.ends not in (";", "\n")
                    or any(w.dynamic or w.glob or not LOOP_ITEM.fullmatch(w.text) for w in words[3:])
                    or any(w.dynamic or w.glob for w in words[:3])):
                return False
            pending = (words[1].text, [w.text for w in words[3:]])
            continue
        elif pending is not None:
            if head.text != "do":
                return False
            loop, pending = pending, None
            if len(cmd.words) == 1 and not cmd.redirects:
                continue
            cmd = Command(cmd.words[1:], cmd.redirects, cmd.heredocs, cmd.depth, cmd.joiner, cmd.ends)
        elif head.text == "done" and loop is not None and len(cmd.words) == 1 and not cmd.redirects:
            loop = None
            continue
        if not allowed_redirects(cmd):
            return False
        cmd = Command(cmd.words, [], [], cmd.depth, cmd.joiner, cmd.ends)
        if k == 0 and cmd.words[0].text == "cd" and not cmd.words[0].dynamic and loop is None:
            # A leading `cd <dir>`: the repo root or the caller's own worktree, and nothing else. A worker's
            # routine git steps are safe only in its worktree, so it may not cd to the repo root. The cd must
            # take effect for what follows: `cd X | cmd` runs it in a subshell, `cd X || cmd` skips cmd on success.
            target = cmd.words[1] if len(cmd.words) == 2 else None
            if target is None or target.dynamic or target.glob or cmd.ends not in (";", "&&", "\n"):
                return False
            real = Path(os.path.realpath(os.path.join(here, target.text)))
            roots = [worktree] if role == "worker" else [r.resolve() for r in repo_roots(cwd)] + ([worktree] if worktree else [])
            if real not in roots and not (role == "reviewer" and scratch_worktree(real, cwd)):
                return False
            here = str(real)
            continue
        if ASSIGNMENT.fullmatch(cmd.words[0].text) and loop is None and waiting is None:
            if orchestrator and not any(w.text.partition("=")[0] in values for w in cmd.words) and chain_assignment_ok(cmd, env, cwd):
                continue
            # A plain `NAME=word` assignment of literal words (`R=origin/main`), for the reads after it to use.
            # Not in a pipeline (the shell would run it in a subshell, and the value would not exist after).
            if (cmd.joiner == "|" or cmd.ends not in (";", "&&", "\n") or cmd.redirects
                    or not all(literal_assignment(w) and w.text.partition("=")[0] not in env for w in cmd.words)):
                return False
            for w in cmd.words:
                name, _, value = w.text.partition("=")
                values[name] = value
            continue
        if orchestrator and waiting is None:
            if chain_script_ok(cmd, env, here):  # relative paths resolve from the dir after a leading cd
                continue
        for value in (loop[1] if loop else [None]):
            sub = substitute(cmd, {"?": "0", **values, **({loop[0]: value} if loop else {})})
            if sub is None or not sub.words or sub.words[0].dynamic:
                return False
            one = Parsed([sub])
            plain = one_plain_command(one)
            if waiting:
                if not (read_command_ok(sub, here, cwd) or (waiting == "body" and sleep_ok(sub))):
                    return False
                continue
            if not (read_command_ok(sub, here, cwd) or (plain is not None and routine_single(role, one, plain, cwd, session))
                    or (role == "worker" and worker_work_ok(sub, here, worktree))
                    or (role == "reviewer" and (reviewer_verify_ok(sub, here, cwd) or reviewer_worktree_ok(sub, cwd)))):
                return False
    return loop is None and pending is None and waiting is None


def approval(role: str | None, command: str, cwd: str, session: str = "") -> bool:
    """True when this command, already accepted by the rules, is one of its role's routine writes. `role`
    None is the orchestrator's own session (no `agent_type`), in a repo that carries this plugin's config."""
    parsed = Parsed(parse(command))
    if role == "reviewer":
        if review_post(parsed) == "" and posting_enabled(cwd):
            return True
    elif role is None and config_path(cwd) and chain_approved(parsed, cwd):
        return True
    else:
        cmd = one_plain_command(parsed) or (one_body_heredoc_command(parsed) if role == "worker" else None)
        if cmd is not None and routine_single(role, parsed, cmd, cwd, session):
            return True
    return compound_approved(role, parsed, cwd, session)


def routine_single(role: str | None, parsed: "Parsed", cmd: Command, cwd: str, session: str = "") -> bool:
    """True when `cmd`, the only plain command of `parsed`, is one of the role's routine writes."""
    # `-R`/`--repo` aims a gh call at a repo the agent was not given: leave it to the permission layer.
    plain_gh = (len(parsed.calls) == 1 and cmd.words[0].text == "gh"
                and not any(REPO_FLAG.fullmatch(w.text) for w in cmd.words))
    if role == "worker":
        if worker_worktree(cwd) is None:
            return False
        found = script_call(parsed, THREADS_SCRIPT)
        if found is not None:
            # Only the current-branch form (no PR number) is approved: the script resolves the PR from the branch.
            return numberless_threads_form(found[0], found[1]) is not None and own_script(found[0], found[1], cwd, "threads")
        return routine_git(cmd, parsed, cwd) or (plain_gh and routine_pr(parsed.calls[0], cwd, session))
    if role is None and config_path(cwd):
        for pattern, name in ((MERGE_SCRIPT, "merge"), (THREADS_SCRIPT, "threads"), (BOARD_SCRIPT, "board")):
            found = script_call(parsed, pattern)
            if found is not None:
                form = {"threads": orchestrator_threads_form, "merge": orchestrator_merge_form,
                        "board": orchestrator_board_form}[name]
                if not form(found[0], found[1]):
                    return False
                return own_script(found[0], found[1], cwd, name)
        return plain_gh and parsed.calls[0].is_sub("issue") and parsed.calls[0].is_verb("edit", "comment", "create", "close")
    return False


WAIT_REFUSAL = ("the orchestrator doesn't wait with `sleep`, `read -t` or an `until` loop around `gh`: a hand-made "
                "wait runs past the moment CI finishes and delays the merge. Waits go through watch-ci as a Monitor.")


def is_orchestrator_session(transcript_path: object) -> bool:
    """True when the main session ran `/orchestrate:run` and never `/orchestrate:lead`, among the first 50
    lines of its transcript (so a `/model` or `/rename` before the run does not hide it). The hook input
    carries no session name, so this is the signal that tells the orchestrator from the lead or a developer's
    own session in the same repo (#537). Unreadable or missing transcript: False, so the refusal stays off
    rather than hitting a session it was not written for."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return False
    ran = set()
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                try:
                    message = json.loads(line).get("message")
                except (json.JSONDecodeError, AttributeError):
                    continue
                content = message.get("content") if isinstance(message, dict) and message.get("role") == "user" else None
                if isinstance(content, str) and "<command-name>" in content:
                    ran.update(c.strip() for c in re.findall(r"<command-name>([^<]*)</command-name>", content))
    except (OSError, UnicodeDecodeError):
        return False
    return "/orchestrate:run" in ran and "/orchestrate:lead" not in ran


def orchestrator_waits(parsed: "Parsed") -> bool:
    """True when a command of the orchestrator's line waits by hand (#542): `sleep` (alone, behind `timeout`
    or another wrapper, or inside `sh -c`), `read -t`, or an `until` loop whose test runs `gh`. Judged on
    lexed command words, so `echo sleep` or a commit message that says it is not a wait."""
    for cmd in parsed.cmds:
        words = cmd.words
        k = command_index(words)
        if k < len(words) and not words[k].dynamic:
            head = basename(words[k])
            if head == "sleep" or (head == "read" and any(re.fullmatch(r"-\w*t\w*", w.text) for w in words[k + 1:])):
                return True
        if words and not words[0].dynamic and words[0].text == "until" and any(
                not w.dynamic and basename(w) == "gh" for w in words):
            return True
    return False


# --- A background role never prompts (#567). ---
#
# A worker, reviewer or scout runs with nobody to answer a permission prompt, and a Sonnet worker given a prompt
# retried with ever stranger scripts. So a call the guard can't approve is denied with the reason and the approved
# way to do it, and a call the agent really needs becomes a stop it reports. Fail closed (ADR 0002): a crash while
# deciding is a deny too. The orchestrator's own session keeps the fall-through to the prompt (the developer is there).

# Every refusal of an unapproved call starts with this, so a reader (and the tests) can tell it from a rule's refusal.
UNAPPROVED_MARK = "not approved for"
INTERPRETERS = frozenset(("python", "python3", "perl", "ruby", "node", "php", "osascript", "lua", "tclsh"))
EDIT_REASON = ("edit files with the Edit or Write tool, never a heredoc script, `python3 -c`, `perl -i` or similar "
               "(the guard approves those two inside your worktree)")
MIXED_REASON = "run the write on its own, as a separate Bash call: a write chained with other commands is not approved"
GENERIC_REASON = "find an approved form, or stop and report what you need"
MONITOR_REASON = "run the tests in the foreground, or wait with an approved loop (`until <read-only test>; do sleep N; done`)"
SCRIPT_EDIT_FLAGS = re.compile(r"-[A-Za-z]*[ceEi][A-Za-z]*|--eval|--command")


def is_script_edit(parsed: "Parsed") -> bool:
    """A heredoc, an interpreter's inline program, `perl -i`/`sed -i`, or a redirect that writes a file: how a
    worker edits files when it should use Edit/Write."""
    for cmd in parsed.cmds:
        if cmd.heredocs or any(op in WRITE_REDIRECTS and t.text not in ("/dev/null", "1", "2") for op, t in cmd.redirects):
            return True
        k = command_index(cmd.words)
        if k >= len(cmd.words) or cmd.words[k].dynamic:
            continue
        head, rest = basename(cmd.words[k]), [w.text for w in cmd.words[k + 1:]]
        if head in INTERPRETERS and (not rest or rest[0] == "-" or any(SCRIPT_EDIT_FLAGS.fullmatch(a) for a in rest if a.startswith("-"))):
            return True
        if head in ("sed", "perl") and any(re.fullmatch(r"-[A-Za-z]*i.*|--in-place.*", a) for a in rest):
            return True
    return False


def unapproved_reason(role: str, parsed: "Parsed", cwd: str, session: str, tool: str = "Bash") -> str:
    """Why a background role's call got no approval, and what to do instead."""
    who = {"worker": "a worker", "reviewer": "a reviewer", "scout": "a scout"}.get(rules_role(role), "this role")
    head = f"{UNAPPROVED_MARK} {who}: "
    if tool == "Monitor":
        return head + MONITOR_REASON
    if is_script_edit(parsed):
        return head + EDIT_REASON
    if len(parsed.cmds) > 1:
        reads = [read_command_ok(c, os.path.realpath(cwd or "."), cwd) if c.words and not c.heredocs and not c.depth else False
                 for c in parsed.cmds]
        for cmd, is_read in zip(parsed.cmds, reads):
            if is_read or not cmd.words or cmd.depth or cmd.heredocs:
                continue
            alone = Command(cmd.words, cmd.redirects, cmd.heredocs, cmd.depth, None, None)
            try:
                if compound_approved(role, Parsed([alone]), cwd, session):
                    return head + MIXED_REASON
            except Exception:
                pass
    return head + GENERIC_REASON


def allow(reason: str) -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                      "permissionDecisionReason": f"orchestrate guard: {reason}"}}, sys.stdout)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return  # valid JSON that isn't an object names no agent, like bad JSON
    agent_type = data.get("agent_type")
    tool_input = data.get("tool_input")
    role = role_of(agent_type if isinstance(agent_type, str) else "")
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
    session = data.get("session_id") if isinstance(data.get("session_id"), str) else ""
    if data.get("tool_name") in EDIT_TOOLS:
        decision = edit_decision(data["tool_name"], tool_input, cwd) if role == "worker" else None
        if decision:
            (deny if decision[0] == "deny" else allow)(decision[1])
        return
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    orchestrator = role is None and not agent_type  # the main session: approved, and the orchestrator's refused for a hand-made wait
    if (role is None and not orchestrator) or command is None or command == "":
        return
    if orchestrator:
        try:
            if (isinstance(command, str) and config_path(cwd) and orchestrator_waits(Parsed(parse(command)))
                    and is_orchestrator_session(data.get("transcript_path"))):  # the file is read only for a wait
                deny(WAIT_REFUSAL)
                return
        except Exception:
            pass  # silence: the permission layer decides
        try:
            if isinstance(command, str) and approval(None, command, cwd):
                allow("routine orchestrator step")
        except Exception:
            pass  # silence: the permission layer decides
        return
    tool = "Monitor" if data.get("tool_name") == "Monitor" else "Bash"
    try:
        if not isinstance(command, str):
            raise TypeError("command is not a string")
        reason = verdict(role, command, cwd, tool == "Bash" and tool_input.get("run_in_background") is True,
                         tool_input.get("timeout") if tool == "Bash" else None)
    except Exception as err:  # fail closed: a crash here would let the command run unchecked
        reason = (f"the guard couldn't parse or check this command ({type(err).__name__}), so it refuses it. "
                  f"Rewrite it more simply. {NEXT_STEP[rules_role(role)]}")
    if reason:
        deny(reason)
        return
    # No rule refused it. A background role never gets the permission prompt (#567): a call that is not a routine
    # step is denied with the approved way to do it, and a crash while deciding is a deny too (ADR 0002).
    try:
        if approval(role, command, cwd, session):
            allow(f"routine {role} step")
            return
    except Exception:
        pass
    try:
        deny(unapproved_reason(role, Parsed(parse(command)), cwd, session, tool))
    except Exception:
        deny(f"{UNAPPROVED_MARK} this role: {GENERIC_REASON}")


if __name__ == "__main__":
    main()
