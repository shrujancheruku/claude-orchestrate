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

    __slots__ = ("text", "dynamic", "quoted", "glob")

    def __init__(self, text: str, dynamic: bool, quoted: bool, glob: bool = False):
        self.text, self.dynamic, self.quoted, self.glob = text, dynamic, quoted, glob


class Heredoc:
    __slots__ = ("delimiter", "quoted", "strip_tabs", "body", "closed")

    def __init__(self, delimiter: str, quoted: bool, strip_tabs: bool):
        self.delimiter, self.quoted, self.strip_tabs = delimiter, quoted, strip_tabs
        self.body, self.closed = "", False


class Command:
    """A simple command. `joiner` is the operator before it (None for the first one); `depth` is 0 at the
    top level and counts how deep inside `$(...)`, backticks or `sh -c '...'` it sits."""

    __slots__ = ("words", "redirects", "heredocs", "depth", "joiner")

    def __init__(self, words, redirects, heredocs, depth, joiner):
        self.words, self.redirects, self.heredocs, self.depth, self.joiner = words, redirects, heredocs, depth, joiner


class WordBuilder:
    def __init__(self):
        self.parts, self.dynamic, self.quoted, self.glob, self.brace = [], False, False, False, False

    def lit(self, text: str) -> None:
        self.parts.append(text)

    def exp(self) -> None:
        self.parts.append(EXP)
        self.dynamic = True

    def done(self) -> Word:
        text = "".join(self.parts)
        # A brace word only expands with a comma or a `..` inside (`{a,b}`, `{1..3}`); `{owner}/{repo}` is literal.
        return Word(text, self.dynamic, self.quoted, self.glob or (self.brace and ("," in text or ".." in text)))


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
            self.out.append(Command(self.words, self.redirects, self.heredocs, self.depth, self.joiner))
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
                        wb.exp()
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
            wb.exp()
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
    "reviewer": "`threads <pr>`, `threads <pr> --all` and `threads <pr> reopen <id> --why <why>`",
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
    if len(rest) == 4 and rest[0] == "reopen" and rest[2] == "--why":
        return "reopen"
    if rest[:1] == ["resolve"]:
        return "resolve"
    return None


# The only flags a worker's number-less threads call may carry, spelled in full: `--repo DIR` (and an
# abbreviation of it, which argparse would accept) aims the call at another repository.
THREADS_WORKER_FLAGS = ("--all", "--fixed", "--left", "--why", "--verbose")


def numberless_threads_form(cmd: Command, args: list) -> str | None:
    """The worker's current-branch form of a threads call, with no PR number: "list", "resolve" or "reopen",
    or None. The script is the first word (no env prefix such as `GH_REPO=` and no wrapper), and every
    flag is exactly one of THREADS_WORKER_FLAGS."""
    words = [w.text for w in args]
    if len(cmd.words) - len(args) != 1 or not words:
        return "list" if len(cmd.words) == 1 and not words else None
    if any(w.startswith("-") and w not in THREADS_WORKER_FLAGS for w in words):
        return None
    if words == ["--all"]:
        return "list"
    return words[0] if words[0] in ("resolve", "reopen") else None


# The only flags the orchestrator's threads call may carry, spelled in full and each once (#389).
THREADS_ORCHESTRATOR_FLAGS = ("--all", "--fixed", "--left", "--verbose")


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
    # `resolve` takes one or more thread ids, then exactly one of --fixed/--left with its value (scripts/threads).
    return (len(rest) >= 4 and rest[0] == "resolve" and rest[-2] in ("--fixed", "--left")
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
    return None


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
        # Writes that change the worker's own permissions or git internals keep their prompt.
        parts = target.relative_to(worktree).parts if target != worktree else ()
        if parts[:1] == (".git",) or (len(parts) >= 2 and parts[-2] == ".claude" and SETTINGS_FILE.fullmatch(parts[-1])):
            return None
        return "allow", "routine worker step"
    in_temp = any(str(target) == t or str(target).startswith(t + "/") for t in TEMP_ROOTS)
    in_repo = any(root == target or root in target.parents for root in repo_roots(cwd))
    if in_temp and not in_repo:
        return None  # scratch space outside every checkout
    return "deny", (f"the worker role can't edit {path}: it is outside your worktree ({worktree}). "
                    "Make every change inside your own worktree.")


def one_plain_command(parsed: "Parsed") -> Command | None:
    """The only command of the line when it is a single top-level simple command of plain words."""
    if len(parsed.cmds) != 1:
        return None
    cmd = parsed.cmds[0]
    return cmd if cmd.depth == 0 and only_plain_words(cmd) and not cmd.heredocs and cmd.words else None


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


def routine_git(cmd: Command, parsed: "Parsed", cwd: str) -> bool:
    """A worker's `git add`, `git commit` or push of its own ticket branch, from inside its worktree."""
    worktree = worker_worktree(cwd)
    if worktree is None or basename(cmd.words[0]) != "git" or len(parsed.calls) != 1:
        return False
    call = parsed.calls[0]
    if call.is_sub("add", "commit"):
        return not call.options
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
    """The scratchpad of this session when the hook input names it (`session_id`), else any session's."""
    return re.compile(r"/claude-[^/]+/[^/]+/%s/scratchpad(?:/.+)?" % (re.escape(session) if session else "[^/]+"))


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
    A short-option word that holds an `F` but is not exactly `-F` (`-F/etc/x`, `-F=x`, `-dF x`) is a form this
    parser does not read, so it fails closed."""
    j = 0
    while j < len(args):
        if re.fullmatch(r"-[^-].*", args[j]) and "F" in args[j] and args[j] != "-F":
            return False
        name, eq, value = args[j].partition("=") if args[j].startswith("--") else (args[j], "", "")
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


def approval(role: str | None, command: str, cwd: str, session: str = "") -> bool:
    """True when this command, already accepted by the rules, is one of its role's routine writes. `role`
    None is the orchestrator's own session (no `agent_type`), in a repo that carries this plugin's config."""
    parsed = Parsed(parse(command))
    if role == "reviewer":
        return review_post(parsed) == "" and posting_enabled(cwd)
    cmd = one_plain_command(parsed)
    if cmd is None:
        return False
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
    orchestrator = role is None and not agent_type  # the main session: only ever approved, never refused
    if (role is None and not orchestrator) or command is None or command == "":
        return
    if orchestrator:
        try:
            if isinstance(command, str) and approval(None, command, cwd):
                allow("routine orchestrator step")
        except Exception:
            pass  # silence: the permission layer decides
        return
    try:
        if not isinstance(command, str):
            raise TypeError("command is not a string")
        reason = verdict(role, command, cwd, tool_input.get("run_in_background") is True,
                         tool_input.get("timeout"))
    except Exception as err:  # fail closed: a crash here would let the command run unchecked
        reason = (f"the guard couldn't parse or check this command ({type(err).__name__}), so it refuses it. "
                  f"Rewrite it more simply. {NEXT_STEP[rules_role(role)]}")
    if reason:
        deny(reason)
        return
    try:
        if approval(role, command, cwd, session):
            allow(f"routine {role} step")
    except Exception:
        pass  # silence: the permission layer decides


if __name__ == "__main__":
    main()
