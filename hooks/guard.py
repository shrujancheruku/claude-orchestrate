"""PreToolUse guard for the orchestrate plugin's agents.

Claude Code runs this before every Bash call while the plugin is enabled. It
acts only when the call comes from one of this plugin's agents (the hook input
names it in `agent_type`) and refuses the commands that agent's role must
leave to someone else. The one write it lets a read-only role make is a
reviewer's comment review on a PR, and only when the repo's config turns
posting on.

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
REVIEWER_GIT_WRITES = (GIT_WRITES - {"branch"}) | {"update-ref", "symbolic-ref"}
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
    or backtick expansion (each shows as EXP in `text`). `quoted`: part of it was quoted or escaped."""

    __slots__ = ("text", "dynamic", "quoted")

    def __init__(self, text: str, dynamic: bool, quoted: bool):
        self.text, self.dynamic, self.quoted = text, dynamic, quoted


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
        self.parts, self.dynamic, self.quoted = [], False, False

    def lit(self, text: str) -> None:
        self.parts.append(text)

    def exp(self) -> None:
        self.parts.append(EXP)
        self.dynamic = True

    def done(self) -> Word:
        return Word("".join(self.parts), self.dynamic, self.quoted)


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
                self.word().lit(c)
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


def api_writes(args: list) -> bool:
    """True when `gh api`'s flags make it a write: a method other than GET (`-X V`, `-XV`, `--method V`,
    `--method=V`), any field flag (gh POSTs on `-f` and `-F`, attached or separate), or `--input`."""
    j = 0
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
                    return True
            elif name in API_FIELD_FLAGS:
                return True
            elif name in API_LONG_VALUE and not eq:
                j += 1
        elif t.startswith("-") and len(t) > 1:
            for pos, ch in enumerate(t[1:], 1):  # a cluster such as `-iXPOST` or `-ftitle=x`
                if ch in "fF":
                    return True
                if ch in API_SHORT_VALUE:
                    value, dynamic = t[pos + 1:], w.dynamic
                    if not value and j < len(args):
                        value, dynamic = args[j].text, args[j].dynamic
                        j += 1
                    if ch == "X" and (dynamic or value.casefold() != "get"):
                        return True
                    break
    return False


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


# The plugin's merge script, run as a command: at the start of the command or after `;`, `&`, `|`
# or `(`, optionally behind python3 and in quotes (the script path is usually a quoted
# "${CLAUDE_SKILL_DIR}/../../scripts/merge"). Checked on the raw command, because the quoted path
# is exactly what a plain-word check would miss; anchoring at command position keeps
# `grep scripts/merge` allowed.
# Wrappers that still run it: VAR=1 assignments, env/nohup/time/exec/command/timeout <n>, an
# interpreter (bash, sh, zsh, python3 [-I]) with its flags, and `sh -c '...'`. A bare `./merge`
# counts after `cd <...>scripts &&`. A renamed or copied script is out of scope (a guardrail).
_WRAP = (r"(?:(?:\w+=\S*|(?:\S*/)?(?:env|nohup|time|exec|command|timeout|bash|sh|zsh|python3?)"
         r"(?:\s+-[-\w=]+)*(?:\s+\d+[smhd]?)?)\s+)*")
_END = r"[\"']?(?=[\s\"';&|)]|$)"
MERGE_SCRIPT = re.compile(
    r"(?:^|[;&|(]|\s-c\s+[\"'])\s*(?:"
    + _WRAP + r"[\"']?[^\s\"']*\bscripts/merge" + _END
    + r"|cd\s+[^\s;&|]*scripts/?[\"']?\s*&&\s*" + _WRAP + r"[\"']?\./merge" + _END
    + r")"
)

# The writes a reviewer may make: a comment review posted through the API (its JSON on a
# heredoc) or through `gh pr review --comment`. The aimed command must be exactly one of
# these shapes (see review_post); the payload is checked separately. A heredoc delimiter must be
# quoted, or the shell would expand `$(...)` inside the body, and no word may hold an expansion.
# An optional leading `cd <path> &&` is allowed, a plain cd and nothing chained after it.
REVIEWS_PATH = re.compile(r"pulls/\d+/reviews")
REVIEW_ENDPOINT = re.compile(r"repos/[^/\s]+/[^/\s]+/pulls/\d+/reviews")
SHAPES = (
    "Post exactly one of these (an optional leading `cd <path> &&` is fine, nothing else chained, nothing after the heredoc):\n"
    "gh api --method POST repos/{owner}/{repo}/pulls/<n>/reviews --input - <<'EOF'\n"
    '{"event": "COMMENT", "body": "<body>", "comments": []}\nEOF\n'
    "gh pr review <n> --comment --body-file - <<'EOF'\n<body>\nEOF\n"
    "gh pr review <n> --comment --body '<body, no single quotes>'"
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


def rule_git_writes(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub(*GIT_WRITES) or (call.is_sub("worktree") and call.args[:1] and call.args[0].text in WORKTREE_WRITES):
            return "changes git state"
    return None


def rule_reviewer_git(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub(*REVIEWER_GIT_WRITES):
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
        if call.is_sub("api") and api_writes(call.args):
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


def rule_force_push(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub("push") and any(
                a.text.startswith("--force") or re.fullmatch(r"-[^-]*f[^-]*", a.text) or re.match(r"\+.", a.text)
                for a in call.args if not a.dynamic):
            return "force-pushes"
    return None


def rule_push_default(p: Parsed, cwd: str) -> str | None:
    for call in p.tool("git"):
        if call.is_sub("push") and any(
                re.fullmatch(r"(?:[^:]+:)?(?:main|master)", a.text) for a in call.args if not a.dynamic):
            return "pushes to the default branch"
    return None


READ_ONLY = [rule_expansion, rule_bare_expansion, rule_git_writes, rule_gh_writes, rule_gh_api_writes,
             rule_fs_writes, rule_redirects]

RULES = {
    "scout": READ_ONLY,
    "reviewer": [rule_expansion, rule_bare_expansion, rule_reviewer_git, *READ_ONLY[3:]],
    "worker": [rule_expansion, rule_gh_merge, rule_force_push, rule_push_default],
}

NEXT_STEP = {
    "scout": "Report what you found and let your caller act on it.",
    "reviewer": "Read code with `gh pr diff`, `git show <ref>:<path>` or a scratch checkout "
                "(`git worktree add --detach <absolute dir outside the repo> FETCH_HEAD`) and put the change in your findings.",
    "worker": "Push your own branch normally and report the PR; the orchestrator merges.",
}


def role_of(agent_type: str) -> str | None:
    plugin, _, role = agent_type.partition(":")
    if plugin == PLUGIN and role in RULES:
        return role
    return None


def only_plain_words(cmd: Command) -> bool:
    return not cmd.redirects and not any(w.dynamic for w in cmd.words)


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
        return None
    if words[1:3] != ["pr", "review"] or len(words) < 5 or not words[3].isdigit():
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
    return call.is_sub("api") and api_writes(call.args) and any(REVIEWS_PATH.search(a.text) for a in call.args)


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
        if ([w.text for w in cd.words[:1]] != ["cd"] or len(cd.words) != 2 or not only_plain_words(cd)
                or cd.heredocs or cmds[1].joiner != "&&"):
            return "it isn't in the accepted shape"
    post = cmds[at]
    shape = review_shape(post)
    if shape is None or len(post.heredocs) != (0 if shape == "inline" else 1):
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


def verdict(role: str, command: str, cwd: str) -> str | None:
    """The reason to deny this command for this role, or None to let it run."""
    try:
        parsed = Parsed(parse(command))
    except ParseError as err:
        return (f"the guard couldn't parse this command ({err}), so it can't check it. "
                f"Rewrite it with balanced quotes and brackets. {NEXT_STEP[role]}")
    if role == "reviewer":
        problem = review_post(parsed)
        if problem == "":
            if posting_enabled(cwd):
                return None
            return "the config doesn't say `Post reviews: yes`, so return your verdict without posting it."
        if problem:
            return f"this review post is refused because {problem}. {SHAPES}"
    if MERGE_SCRIPT.search(command):
        return (f"the {role} role can't run a command that merges a PR (merging belongs to the orchestrator). "
                f"{NEXT_STEP[role]}")
    for rule in RULES[role]:
        what = rule(parsed, cwd)
        if what:
            return f"the {role} role can't run a command that {what}. {NEXT_STEP[role]}"
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    role = role_of(data.get("agent_type") or "")
    command = (data.get("tool_input") or {}).get("command") or ""
    if role is None or not command:
        return
    try:
        reason = verdict(role, command, data.get("cwd") or "")
    except Exception as err:  # fail closed: a crash here would let the command run unchecked
        reason = (f"the guard couldn't parse or check this command ({type(err).__name__}), so it refuses it. "
                  f"Rewrite it more simply. {NEXT_STEP[role]}")
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
