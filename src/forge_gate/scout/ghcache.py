"""The private pinned GitHub cache and its pin check (design v1.4.5 §12.5 D7-2c; build step 7
slice 3; DECISIONS-7-github G3).

Layout under ``$FORGE_GATE_SCOUT_DIR/github-cache/``:
- ``.objects/<org>/<repo>.git``: a bare object store WE create. The fetch box hands back only
  its ``objects/`` files and ``shallow``; nothing it wrote is run or read as git config, hooks
  or refs.
- ``<org>/<repo>@<commit>/``: the tree exported from that store at the pin, read-only. Regular
  files only; symlinks and anything else are left out and listed.
- ``<org>/<repo>@<commit>.pin.json``: {commit, tree, line_sha256, files, skipped, built_at}.

The pin check (any difference is BLIND(pin-mismatch), until the cache is rebuilt):
- WHOLE CACHE (:func:`check_cache`, before any lens reads):
  - the pin file names this commit;
  - the commit is in the store, with the recorded tree;
  - the pin file's ``line_sha256`` equals the CURRENTLY verified signed line's hash.
  - ``full=True`` also requires the exported files to equal the tree, path for path and blob
    for blob. It runs at build time and on demand.
- PER FILE (:func:`read_file`, at every read): the file's git blob sha, recomputed from the
  bytes on disk, equals the tree's entry at the pin.

Every git call is hardened:
- no system/global config, no hooks;
- ``protocol.allow=never`` except ``file`` for a local carry, so git can never reach a network
  from here;
- no terminal prompt, no lazy fetch.

Failure contract (step-7 crew review, 6E F1-F3): every failure of :func:`build` and of the checks is
a PinMismatch, never a bare OSError, a timeout or a parse error, so a caller that catches PinMismatch reports
BLIND instead of dying. One build at a time per cache root (a ``flock`` on ``.build.lock``).

Derived and rebuildable: never in git, never in the public repo (standard §4).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .. import config
from . import ratify

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_LSTREE = re.compile(r"^(\d{6}) (blob|tree|commit) ([0-9a-f]{40})\t(.+)$")
# 6E F4: the carried-copy path has no box in front, so ghcache bounds what it copies itself. Same
# figure as box.KEEP_MAX_BYTES. A depth-1 fetch writes one line to `shallow`; 64 KiB holds ~1600.
MAX_STORE_BYTES = 1 << 30
MAX_SHALLOW_BYTES = 64 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


class PinMismatch(Exception):
    """The cache does not match its pin or the verified line: BLIND(pin-mismatch)."""


def cache_root() -> Path:
    return config.scout_dir() / "github-cache"


def _paths(root: Path, org: str, repo: str, commit: str) -> tuple[Path, Path, Path]:
    for part in (org, repo):
        if not ratify._NAME.fullmatch(part) or ".." in part:
            raise PinMismatch(f"not a plain GitHub name: {part!r}")
    if not _HEX40.fullmatch(commit):
        raise PinMismatch(f"commit must be 40 hex: {commit!r}")
    return (root / ".objects" / org / f"{repo}.git", root / org / f"{repo}@{commit}",
            root / org / f"{repo}@{commit}.pin.json")


def git(store: Path, *args: str, input: bytes | None = None, allow_file: bool = False,
        timeout: float = 600) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as home:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
               "GIT_ATTR_NOSYSTEM": "1", "LC_ALL": "C"}
        cmd = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.allow=never",
               "-c", f"protocol.file.allow={'always' if allow_file else 'never'}",
               "-c", "safe.directory=" + str(store), "--git-dir", str(store), *args]
        try:
            return subprocess.run(cmd, input=input, capture_output=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:       # 6E F2
            raise PinMismatch(f"git {args[0] if args else ''}: no answer within {timeout:g} s") from None
        except OSError as e:
            raise PinMismatch(f"git {args[0] if args else ''}: could not run: {e}") from None


def _ok(r: subprocess.CompletedProcess, what: str) -> bytes:
    if r.returncode != 0:
        raise PinMismatch(f"{what}: git exit {r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
    return r.stdout


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def line_sha256(verdict: ratify.Verdict) -> str:
    return hashlib.sha256(ratify.canonical(verdict.fields)).hexdigest()


def tree_files(store: Path, commit: str) -> dict[str, tuple[str, str]]:
    """{path: (mode, blob sha)} for every blob in the commit's tree (recursive)."""
    out = _ok(git(store, "ls-tree", "-r", "--full-tree", "-z", commit), "ls-tree")
    files = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        m = _LSTREE.fullmatch(rec.decode("utf-8", "surrogateescape"))
        if not m:
            raise PinMismatch(f"unparsable ls-tree record: {rec[:120]!r}")
        mode, kind, sha, path = m.groups()
        if kind == "blob":
            files[path] = (mode, sha)
    return files


def _copy_regular(src: Path, dst: Path, budget: int | None = None) -> int:
    """Copy one REGULAR file; a symlink or anything else (even one swapped in after a check) is
    skipped. O_NOFOLLOW + fstat on the open descriptor (review F8). Returns the bytes copied;
    more than ``budget`` is a PinMismatch (6E F4)."""
    try:
        # O_NONBLOCK: a FIFO planted in the fetched store must not hang the open (mutant C5)
        fd = os.open(src, os.O_RDONLY | _NOFOLLOW)
    except OSError:
        return 0
    n, budget = 0, MAX_STORE_BYTES if budget is None else budget
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return 0
        with os.fdopen(fd, "rb", closefd=False) as fh, open(dst, "xb") as out:
            while chunk := fh.read(1 << 20):
                n += len(chunk)
                if n > budget:
                    raise PinMismatch(f"the fetched store is over {MAX_STORE_BYTES} bytes")
                out.write(chunk)
    finally:
        os.close(fd)
    return n


def _import_objects(kept: Path, store: Path) -> None:
    """Copy ONLY object files and the shallow list from an untrusted store into ours."""
    used = 0
    src_objects = kept / "objects"
    if not src_objects.is_dir() or src_objects.is_symlink():
        raise PinMismatch(f"no objects/ in {kept}")
    for dirpath, dirnames, filenames in os.walk(src_objects, followlinks=False):
        rel = Path(dirpath).relative_to(src_objects)
        if rel.parts and rel.parts[0] == "info":
            dirnames[:] = []      # info/alternates would point git at objects elsewhere: never copied
            continue
        for d in list(dirnames):
            if (Path(dirpath) / d).is_symlink():
                dirnames.remove(d)
            else:
                (store / "objects" / rel / d).mkdir(exist_ok=True)
        for f in filenames:
            used += _copy_regular(Path(dirpath) / f, store / "objects" / rel / f, MAX_STORE_BYTES - used)
    shallow = kept / "shallow"
    if shallow.is_file() and not shallow.is_symlink():
        fd = os.open(shallow, os.O_RDONLY | _NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(MAX_SHALLOW_BYTES + 1)       # 6E F4: bounded BEFORE it is checked
        if len(raw) > MAX_SHALLOW_BYTES:
            raise PinMismatch(f"shallow file is over {MAX_SHALLOW_BYTES} bytes")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            raise PinMismatch("shallow file is not a list of commit ids") from None
        if not all(_HEX40.fullmatch(x) for x in text.split()):
            raise PinMismatch("shallow file is not a list of commit ids")
        (store / "shallow").write_text(text)


def _extract(store: Path, commit: str, dest: Path) -> list[str]:
    """Write the pin's REGULAR blobs (modes 100644/100755) into dest, byte for byte from the object
    store (cat-file --batch). Never git archive: the repo's own .gitattributes (export-ignore,
    export-subst, eol, ident) would change the bytes (review F2). Symlinks (120000) and anything
    else are listed, not written."""
    files = tree_files(store, commit)
    want, skipped = [], []
    for path, (mode, sha) in sorted(files.items()):
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts or not path or p.as_posix() != path:
            raise PinMismatch(f"unsafe path in tree: {path!r}")
        if any(part.casefold() == ".git" for part in p.parts):    # 6E F6: fsck only warns (hasDotgit)
            raise PinMismatch(f"a .git entry in the tree: {path!r}")
        if mode in ("100644", "100755"):
            want.append((path, sha))
        else:
            skipped.append(path)
    out = _ok(git(store, "cat-file", "--batch", input="".join(f"{sha}\n" for _, sha in want).encode()), "cat-file")
    pos = 0
    for path, sha in want:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].decode().split()
        if len(head) != 3 or head[0] != sha or head[1] != "blob":
            raise PinMismatch(f"cat-file answered {head} for {path}")
        size = int(head[2])
        data = out[nl + 1: nl + 1 + size]
        pos = nl + 1 + size + 1
        target = dest.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as fh:
            fh.write(data)
    return skipped


def _read_only(top: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(top):
        for f in filenames:
            os.chmod(Path(dirpath) / f, 0o444)
        os.chmod(dirpath, 0o555)


def _real_dirs(root: Path, *dirs: Path) -> None:
    """Every folder from ``root`` down to each of ``dirs`` is a real folder, not a symlink (6E F5:
    a symlinked ``root/<org>`` would carry the build and its pin file outside the cache root)."""
    for d in dirs:
        cur = root
        for part in d.relative_to(root).parts:
            cur = cur / part
            if cur.is_symlink() or not cur.is_dir():
                raise PinMismatch(f"{cur} is not a real folder under the cache root")


def build(kept: Path, org: str, repo: str, commit: str, verdict: ratify.Verdict, *,
          root: Path | None = None) -> dict:
    """Build the cache for org/repo@commit from an untrusted fetched store ``kept`` (the box's
    output, or a carried copy). The verified line must be for exactly this pin. Returns the pin
    record. Raises PinMismatch and leaves nothing behind on any failure."""
    if not verdict.ok or (verdict.fields.get("org"), verdict.fields.get("repo"), verdict.fields.get("commit")) \
            != (org, repo, commit):
        raise PinMismatch(f"no verified signed line for {org}/{repo}@{commit}: {verdict.reason or 'other pin'}")
    root = Path(root) if root is not None else cache_root()
    store, tree_dir, pin = _paths(root, org, repo, commit)
    try:
        root.mkdir(parents=True, exist_ok=True)        # 7b review F3: a fresh host has no cache root yet
        fd = os.open(root / ".build.lock", os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600)
    except OSError as e:
        raise PinMismatch(f"cache lock: {e}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):      # 7b review F7: a planted FIFO is not "another build"
            raise PinMismatch(f"{root / '.build.lock'} is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)     # 6E F1: the checks below are check-then-use
        except OSError:
            raise PinMismatch(f"another build holds {root / '.build.lock'}") from None
        return _build(kept, org, repo, commit, verdict, root, store, tree_dir, pin)
    except (OSError, subprocess.TimeoutExpired) as e:          # 6E F1-F3: the contract is PinMismatch
        raise PinMismatch(f"build failed: {type(e).__name__}: {e}") from None
    finally:
        os.close(fd)


def _build(kept, org, repo, commit, verdict, root, store, tree_dir, pin) -> dict:
    # lexists: a DANGLING symlink at the pin path is "present" too (7b review F1)
    if os.path.lexists(tree_dir) or os.path.lexists(pin):
        raise PinMismatch(f"cache already present for {org}/{repo}@{commit}: archive it before a rebuild")
    if os.path.lexists(store):
        raise PinMismatch(f"object store already present: {store}: archive it before a rebuild")
    store.parent.mkdir(parents=True, exist_ok=True)
    tree_dir.parent.mkdir(parents=True, exist_ok=True)
    _real_dirs(root, store.parent, tree_dir.parent)
    tmp = Path(tempfile.mkdtemp(prefix=".build-", dir=root))
    try:
        new_store, new_tree = tmp / "store.git", tmp / "tree"
        r = subprocess.run(["git", "init", "-q", "--bare", str(new_store)], capture_output=True,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1",
                                "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(tmp)}, timeout=60)
        _ok(r, "init")
        shutil.rmtree(new_store / "hooks", ignore_errors=True)   # git init's samples: never needed
        _import_objects(Path(kept), new_store)
        if _ok(git(new_store, "cat-file", "-t", commit), "cat-file").strip() != b"commit":
            raise PinMismatch(f"{commit} is not a commit in the fetched store")
        _ok(git(new_store, "fsck", "--full", "--no-dangling", "--no-progress"), "fsck")   # re-hashes every object
        tree = _ok(git(new_store, "rev-parse", f"{commit}^{{tree}}"), "rev-parse").decode().strip()
        new_tree.mkdir()
        skipped = _extract(new_store, commit, new_tree)
        record = {"org": org, "repo": repo, "commit": commit, "tree": tree,
                  "line_sha256": line_sha256(verdict), "ratified_by": sorted(verdict.key_ids),
                  "skipped": skipped, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        record["files"] = _full_check(new_store, commit, new_tree, skipped)
        os.replace(new_store, store)
        os.replace(new_tree, tree_dir)      # a folder must be writable to move it: read-only after
        _read_only(tree_dir)
        _read_only(store)                    # review F8: nothing rewrites the objects after the build
        # O_EXCL|O_NOFOLLOW: never written through a link planted after the check (7b review F1)
        with os.fdopen(os.open(pin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o444), "w") as fh:
            fh.write(json.dumps(record, indent=1, sort_keys=True) + "\n")
        os.chmod(pin, 0o444)
        return record
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # our own build folder, created above


def _full_check(store: Path, commit: str, tree_dir: Path, skipped: list[str]) -> int:
    want = tree_files(store, commit)
    have = {}
    for dirpath, _dirs, files in os.walk(tree_dir):
        for f in files:
            p = Path(dirpath) / f
            have[p.relative_to(tree_dir).as_posix()] = p
    missing = sorted(set(want) - set(have) - set(skipped))
    extra = sorted(set(have) - set(want))
    if missing or extra:
        raise PinMismatch(f"exported tree differs from {commit}: missing {missing[:5]}, extra {extra[:5]}")
    for rel, p in have.items():
        if p.is_symlink() or blob_sha(p.read_bytes()) != want[rel][1]:
            raise PinMismatch(f"{rel}: bytes differ from the pinned blob")
    return len(have)


_CHECK_ERRORS = (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.TimeoutExpired)


def check_cache(org: str, repo: str, commit: str, verdict: ratify.Verdict, *, full: bool = False,
                root: Path | None = None) -> dict:
    """The whole-cache pin check. Returns the pin record, or raises PinMismatch (and nothing else:
    a corrupt pin file or an unreadable cache is BLIND, not a traceback; 7b review F2)."""
    try:
        return _check_cache(org, repo, commit, verdict, full=full, root=root)
    except _CHECK_ERRORS as e:
        raise PinMismatch(f"cache check failed: {type(e).__name__}: {e}") from None


def _check_cache(org, repo, commit, verdict, *, full, root) -> dict:
    root = Path(root) if root is not None else cache_root()
    store, tree_dir, pin = _paths(root, org, repo, commit)
    if not verdict.ok:
        raise PinMismatch(f"line not verified: {verdict.reason}")
    if (verdict.fields.get("org"), verdict.fields.get("repo"), verdict.fields.get("commit")) != (org, repo, commit):
        raise PinMismatch("the verified line is for another pin")
    if pin.is_symlink():
        raise PinMismatch("the pin file is a symlink")
    try:
        record = json.loads(pin.read_text())
    except (OSError, ValueError) as e:
        raise PinMismatch(f"pin file unreadable: {e}") from None
    if not isinstance(record, dict) or not isinstance(record.get("skipped", []), list):
        raise PinMismatch("pin file is not a pin record")
    if (record.get("org"), record.get("repo"), record.get("commit")) != (org, repo, commit):
        raise PinMismatch(f"pin file names {record.get('org')}/{record.get('repo')}@{record.get('commit')}")
    if record.get("line_sha256") != line_sha256(verdict):
        raise PinMismatch("the signed line changed since the cache was built")
    if not store.is_dir() or not tree_dir.is_dir() or tree_dir.is_symlink() or store.is_symlink():
        raise PinMismatch("store or tree missing")
    _real_dirs(root, store.parent, tree_dir.parent)
    if _ok(git(store, "cat-file", "-t", commit), "cat-file").strip() != b"commit":
        raise PinMismatch(f"{commit} is not a commit in the store")
    tree = _ok(git(store, "rev-parse", f"{commit}^{{tree}}"), "rev-parse").decode().strip()
    if tree != record.get("tree"):
        raise PinMismatch(f"tree {tree} differs from the pin file's {record.get('tree')}")
    if full:
        _full_check(store, commit, tree_dir, record.get("skipped", []))
    return record


def read_file(org: str, repo: str, commit: str, path: str, verdict: ratify.Verdict, *,
              root: Path | None = None) -> bytes:
    """One file's bytes from the cache, after the whole-cache check and the per-file blob check.
    Raises PinMismatch and nothing else (7b review F2)."""
    try:
        return _read_file(org, repo, commit, path, verdict, root=root)
    except _CHECK_ERRORS as e:
        raise PinMismatch(f"read failed: {type(e).__name__}: {e}") from None


def _read_file(org, repo, commit, path, verdict, *, root) -> bytes:
    root = Path(root) if root is not None else cache_root()
    check_cache(org, repo, commit, verdict, root=root)
    store, tree_dir, _ = _paths(root, org, repo, commit)
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not path or path != p.as_posix():
        raise PinMismatch(f"not a plain relative path: {path!r}")
    want = _ok(git(store, "ls-tree", "--full-tree", "-z", commit, "--", path), "ls-tree").rstrip(b"\0")
    m = _LSTREE.fullmatch(want.decode("utf-8", "surrogateescape")) if want else None
    if not m or m.group(2) != "blob" or m.group(4) != path:
        raise PinMismatch(f"{path} is not a file at {commit}")
    f = tree_dir.joinpath(*p.parts)
    if f.is_symlink() or not f.is_file():
        raise PinMismatch(f"{path} missing from the exported tree")
    data = f.read_bytes()
    if blob_sha(data) != m.group(3):
        raise PinMismatch(f"{path}: bytes differ from the pinned blob")
    return data
