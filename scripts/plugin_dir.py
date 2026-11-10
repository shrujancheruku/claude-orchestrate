"""The plugin snapshot store: per-commit exports of the plugin and the one stable path a run loads (#581, #728).

`scripts/launch` exports the plugin at origin/<default> once per sha under the cache and points `--plugin-dir` at
`<cache>/<repo key>/current`, a symlink to that export. Hooks re-resolve `${CLAUDE_PLUGIN_ROOT}` through the link on
every call, so `scripts/merge` can repoint it after a gated merge and a guard fix reaches the running run. The link only
ever moves to a snapshot of a default-branch commit. Stdlib only.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import common

SNAPSHOT_DIRS = (".claude-plugin", "hooks", "agents", "skills", "scripts", "viewer")  # what the plugin loads or reads (viewer: profile.py's HTML, #628)
SOURCE_FILE = ".source"  # in a snapshot: the path of the checkout it was exported from
LINK_NAME = "current"
KEEP = 3  # snapshots kept after a launch, the one in use included
MIN_AGE = 24 * 3600  # a snapshot younger than this is never pruned: a live session may still load it
SHA = re.compile(r"[0-9a-f]{40}")


class PluginDirError(RuntimeError):
    """A snapshot could not be exported or the link not moved; the message is the reason, one line."""


def cache_base() -> Path:
    """Where snapshots live: $XDG_CACHE_HOME/orchestrate/plugin, else ~/.cache/orchestrate/plugin."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "orchestrate" / "plugin"


def link_path(checkout) -> Path:
    """The stable `--plugin-dir` for the plugin checkout `checkout`: <cache>/<key>/current, one key per checkout.

    The key is a hash of the checkout's real path, so two repos never share a link and it never looks like a sha
    (the snapshot directories are the 40-hex names beside it)."""
    key = "r-" + hashlib.sha256(str(Path(checkout).resolve()).encode()).hexdigest()[:12]
    return cache_base() / key / LINK_NAME


def target_sha(link):
    """The sha of the snapshot `link` points to, else None (no link, or it points elsewhere)."""
    try:
        name = Path(link).resolve(strict=True).name
    except OSError:
        return None
    return name if SHA.fullmatch(name) else None


def export(git, top: str, sha: str) -> Path:
    """The snapshot of the plugin dirs at commit `sha`, exported once under the cache and reused after.

    `git` is the argv prefix of a repo holding `sha`; `top` is that repo's top level, written to .source (#628)."""
    base = cache_base()
    dest = base / sha
    if dest.is_dir():
        os.utime(dest)  # newest use wins the pruning
        return dest
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=base))
    try:
        proc = subprocess.Popen([*git, "archive", sha, *SNAPSHOT_DIRS], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(tmp, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        proc.stdout.close()
        failed = proc.wait() != 0
        reason = proc.stderr.read().decode(errors="replace").strip()
        proc.stderr.close()
        if failed:
            raise PluginDirError(f"git archive of {sha} failed: {reason}")
        (tmp / SOURCE_FILE).write_text(top.strip() + "\n")
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.is_dir():  # a concurrent export winning the rename is fine
                raise
    except (OSError, tarfile.TarError) as e:
        raise PluginDirError(f"could not export {sha}: {e}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def point(link, dest) -> None:
    """Repoint `link` at the snapshot `dest` atomically: a temporary symlink beside it, then rename over it."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.parent / f".{LINK_NAME}-{os.getpid()}"
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    try:
        tmp.symlink_to(dest)
        os.replace(tmp, link)
    except OSError as e:
        raise PluginDirError(f"could not repoint {link}: {e}") from e


def prune(base: Path, keep_paths) -> None:
    """Remove snapshots beyond the KEEP newest, never one in `keep_paths`, the one a link points to, or one used within MIN_AGE."""
    protected = {Path(p).resolve() for p in keep_paths}
    for link in base.glob(f"*/{LINK_NAME}"):
        try:
            protected.add(link.resolve(strict=True))
        except OSError:
            pass
    old = sorted((p for p in base.iterdir() if p.is_dir() and not p.is_symlink() and SHA.fullmatch(p.name) and p.resolve() not in protected),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    for p in old[max(KEEP - len(protected), 0):]:
        if time.time() - p.stat().st_mtime > MIN_AGE:
            shutil.rmtree(p, ignore_errors=True)
