"""Step 7 slice 3: the private pinned cache and its pin check (design §12.5 D7-2c; DECISIONS G3).
A local fixture repo stands in for GitHub; nothing touches a network."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from forgeproof.scout import ghcache, ratify

GENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"], "HOME": os.devnull}


def g(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, env=GENV, check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def origin(tmp_path):
    """A repo with two commits; the pin is the second. A symlink is in the tree on purpose."""
    src = tmp_path / "origin"
    src.mkdir()
    g("init", "-q", "-b", "main", cwd=src)
    (src / "Lib" / "urllib").mkdir(parents=True)
    (src / "Lib" / "urllib" / "request.py").write_text("def urlopen(url):\n    return url\n")
    (src / "LICENSE").write_text("PSF licence text\n")
    g("add", "-A", cwd=src)
    g("commit", "-q", "-m", "one", cwd=src)
    (src / "Lib" / "urllib" / "request.py").write_text("def urlopen(url, data=None):\n    return url\n")
    os.symlink("LICENSE", src / "COPYING")
    g("add", "-A", cwd=src)
    g("commit", "-q", "-m", "two", cwd=src)
    return src, g("rev-parse", "HEAD", cwd=src), g("rev-parse", "HEAD~1", cwd=src)


@pytest.fixture
def kept(tmp_path, origin):
    """What the fetch box hands back: a bare store after a depth-1 fetch of the pin."""
    src, sha, _ = origin
    k = tmp_path / "kept"
    g("init", "-q", "--bare", str(k))
    g("-c", "protocol.file.allow=always", "-c", "fetch.unpackLimit=1", "--git-dir", str(k), "fetch", "-q", "--depth", "1",
      f"file://{src}", "main")
    return k


def verdict(sha, **over):
    fields = {"org": "python", "repo": "cpython", "commit": sha, "licence_spdx": "Python-2.0",
              "licence_blob": "b" * 40, "host_set": "github-git", "trust_signals": {},
              "candidate_ref": "x", "ratified_at": "2026-11-14"}
    fields.update(over)
    return ratify.Verdict(1, True, "", fields, ["k1", "k2"], [ratify.REQUIRED_PRINCIPAL, "fives"])


@pytest.fixture
def built(tmp_path, origin, kept):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    rec = ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    return root, sha, rec


def _leftovers(root: Path):
    return sorted(p.name for p in root.rglob("*"))


def test_build_exports_the_pin_read_only_and_records_it(built):
    root, sha, rec = built
    assert rec["commit"] == sha and rec["files"] == 2 and rec["skipped"] == ["COPYING"]
    tree = root / "python" / f"cpython@{sha}"
    f = tree / "Lib" / "urllib" / "request.py"
    assert f.read_text().startswith("def urlopen(url, data=None)")
    assert not os.access(f, os.W_OK) and not (tree / "COPYING").exists()
    assert not os.access(root / ".objects" / "python" / "cpython.git" / "objects", os.W_OK)   # review F8
    assert ghcache.check_cache("python", "cpython", sha, verdict(sha), full=True, root=root)["tree"] == rec["tree"]
    assert not list(root.glob(".build-*"))


def test_read_file_returns_the_pinned_bytes(built):
    root, sha, _ = built
    data = ghcache.read_file("python", "cpython", sha, "Lib/urllib/request.py", verdict(sha), root=root)
    assert data.startswith(b"def urlopen(url, data=None)")


def test_an_edited_file_is_caught_at_read_and_by_the_full_check(built):
    root, sha, _ = built
    f = root / "python" / f"cpython@{sha}" / "Lib" / "urllib" / "request.py"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text("def urlopen(url):\n    import os; os.system('x')\n")
    with pytest.raises(ghcache.PinMismatch, match="bytes differ"):
        ghcache.read_file("python", "cpython", sha, "Lib/urllib/request.py", verdict(sha), root=root)
    with pytest.raises(ghcache.PinMismatch, match="bytes differ"):
        ghcache.check_cache("python", "cpython", sha, verdict(sha), full=True, root=root)


def test_an_added_file_is_caught_by_the_full_check(built):
    root, sha, _ = built
    d = root / "python" / f"cpython@{sha}"
    os.chmod(d, 0o755)
    (d / "planted.py").write_text("x = 1\n")
    with pytest.raises(ghcache.PinMismatch, match="extra"):
        ghcache.check_cache("python", "cpython", sha, verdict(sha), full=True, root=root)


def test_a_changed_signed_line_invalidates_the_cache(built):
    root, sha, _ = built
    with pytest.raises(ghcache.PinMismatch, match="signed line changed"):
        ghcache.check_cache("python", "cpython", sha, verdict(sha, licence_spdx="MIT"), root=root)


def test_an_unverified_or_other_line_is_refused(built):
    root, sha, _ = built
    bad = verdict(sha)
    bad.ok, bad.reason = False, "no signatures"
    with pytest.raises(ghcache.PinMismatch, match="not verified"):
        ghcache.read_file("python", "cpython", sha, "LICENSE", bad, root=root)
    with pytest.raises(ghcache.PinMismatch, match="another pin"):
        ghcache.check_cache("python", "cpython", sha, verdict("c" * 40), root=root)


def test_a_tampered_pin_file_is_caught(built):
    root, sha, _ = built
    pin = root / "python" / f"cpython@{sha}.pin.json"
    os.chmod(pin, 0o644)
    pin.write_text(pin.read_text().replace('"tree": "', '"tree": "0'))
    with pytest.raises(ghcache.PinMismatch, match="tree"):
        ghcache.check_cache("python", "cpython", sha, verdict(sha), root=root)


@pytest.mark.parametrize("path", ["../LICENSE", "/etc/passwd", "Lib/urllib", "Lib//urllib/request.py",
                                  "nope.py", ""])
def test_read_file_refuses_anything_but_a_pinned_file(built, path):
    root, sha, _ = built
    with pytest.raises(ghcache.PinMismatch):
        ghcache.read_file("python", "cpython", sha, path, verdict(sha), root=root)


def test_build_refuses_without_a_verified_line_and_leaves_nothing(tmp_path, origin, kept):
    _, sha, _ = origin
    root = tmp_path / "c"
    root.mkdir()
    bad = verdict(sha)
    bad.ok = False
    with pytest.raises(ghcache.PinMismatch):
        ghcache.build(kept, "python", "cpython", sha, bad, root=root)
    with pytest.raises(ghcache.PinMismatch):
        ghcache.build(kept, "python", "cpython", sha, verdict("c" * 40), root=root)
    assert _leftovers(root) == []


def test_a_store_without_the_pinned_commit_is_refused(tmp_path, origin, kept):
    _, sha, older = origin
    root = tmp_path / "c"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch, match="cat-file|not a commit"):
        ghcache.build(kept, "python", "cpython", older, verdict(older), root=root)
    assert [p for p in _leftovers(root) if not p.startswith(".") and p not in ("python",)] == []
    assert not (root / "python" / f"cpython@{older}").exists()


def test_alternates_in_the_fetched_store_are_never_followed(tmp_path, origin, kept):
    """Red arm: a fetched store whose own packs are gone but whose objects/info/alternates points
    at a full repo must NOT build (we copy objects, never the alternates file)."""
    src, sha, _ = origin
    for p in (kept / "objects" / "pack").iterdir():
        p.unlink()
    (kept / "objects" / "info").mkdir(exist_ok=True)
    (kept / "objects" / "info" / "alternates").write_text(str(src / ".git" / "objects") + "\n")
    root = tmp_path / "c"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)


def test_a_corrupted_pack_is_refused(tmp_path, origin, kept):
    _, sha, _ = origin
    (pack,) = (kept / "objects" / "pack").glob("*.pack")
    b = bytearray(pack.read_bytes())
    b[len(b) // 2] ^= 0xFF
    os.chmod(pack, 0o644)
    pack.write_bytes(bytes(b))
    root = tmp_path / "c"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    assert not (root / "python" / f"cpython@{sha}").exists()


def test_a_second_build_is_refused_until_archived(built, kept):
    root, sha, _ = built
    with pytest.raises(ghcache.PinMismatch, match="already present"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)


def _writable_store(root: Path) -> Path:
    s = root.parent / "writable.git"
    g("init", "-q", "--bare", str(s))
    return s


def test_our_git_never_uses_a_transport(built, origin):
    """protocol.allow=never: the hardened helper cannot fetch even from a local path."""
    src, _, _ = origin
    store = _writable_store(built[0])     # the cache's own store is read-only (F8): that would mask the guard
    r = ghcache.git(store, "fetch", f"file://{src}", "main")
    assert r.returncode != 0 and b"not allowed" in r.stderr


def test_a_bad_shallow_file_is_refused(tmp_path, origin, kept):
    _, sha, _ = origin
    (kept / "shallow").write_text("not-a-sha\n")
    root = tmp_path / "c"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch, match="shallow"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)


def test_a_config_written_in_the_fetched_store_is_never_obeyed(tmp_path, origin, kept):
    """Slice-1 review F1: the box writes the fetched store, so its config/hooks/info are hostile.
    build() copies objects only; a planted fsmonitor/hook/alternates never runs or counts."""
    _, sha, _ = origin
    marker = tmp_path / "PWNED"
    (kept / "config").write_text(f"[core]\n\tbare = true\n\tfsmonitor = touch {marker}\n"
                                 f"\tpager = touch {marker}\n[include]\n\tpath = {tmp_path}/evil.cfg\n")
    (tmp_path / "evil.cfg").write_text(f"[core]\n\tsshCommand = touch {marker}\n")
    (kept / "hooks").mkdir(exist_ok=True)
    (kept / "hooks" / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(kept / "hooks" / "post-checkout", 0o755)
    root = tmp_path / "c"
    root.mkdir()
    rec = ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    ghcache.read_file("python", "cpython", sha, "LICENSE", verdict(sha), root=root)
    assert rec["files"] == 2 and not marker.exists()
    cfg = (root / ".objects" / "python" / "cpython.git" / "config").read_text()
    assert "fsmonitor" not in cfg and "include" not in cfg


def test_our_git_refuses_https_too(built):
    """Review F4: protocol.allow=never covers https, the transport a real fetch would use. The URL
    is loopback port 9, so even a broken guard reaches no outside host."""
    r = ghcache.git(_writable_store(built[0]), "fetch", "https://127.0.0.1:9/x.git")
    assert r.returncode != 0 and b"not allowed" in r.stderr


def test_the_repos_own_gitattributes_cannot_change_the_exported_bytes(tmp_path):
    """Review F2: export-ignore, export-subst, ident and eol in the pinned repo's .gitattributes
    are ignored; the cache holds the blob bytes exactly (git archive would not)."""
    src = tmp_path / "o"
    src.mkdir()
    g("init", "-q", "-b", "main", cwd=src)
    (src / ".gitattributes").write_text("*.py export-subst ident text eol=crlf\nLib/hidden.py export-ignore\n")
    (src / "Lib").mkdir()
    body = "x = '$Format:%H$'\n# $Id$\n"
    (src / "Lib" / "hidden.py").write_text(body)
    (src / "LICENSE").write_text("PSF\n")
    g("add", "-A", cwd=src)
    g("commit", "-q", "-m", "attrs", cwd=src)
    sha = g("rev-parse", "HEAD", cwd=src)
    k = tmp_path / "k"
    g("init", "-q", "--bare", str(k))
    g("-c", "protocol.file.allow=always", "--git-dir", str(k), "fetch", "-q", "--depth", "1", f"file://{src}", "main")
    root = tmp_path / "c"
    root.mkdir()
    ghcache.build(k, "python", "cpython", sha, verdict(sha), root=root)
    assert ghcache.read_file("python", "cpython", sha, "Lib/hidden.py", verdict(sha), root=root) == body.encode()


def test_a_symlink_in_the_fetched_objects_is_never_copied(tmp_path):
    """Review F8: _copy_regular opens with O_NOFOLLOW and copies regular files only."""
    secret = tmp_path / "secret"
    secret.write_text("key\n")
    link = tmp_path / "link"
    os.symlink(secret, link)
    ghcache._copy_regular(link, tmp_path / "out1")
    ghcache._copy_regular(secret, tmp_path / "out2")
    assert not (tmp_path / "out1").exists() and (tmp_path / "out2").read_text() == "key\n"


def test_a_fifo_or_directory_in_the_fetched_objects_is_skipped_without_hanging(tmp_path):
    """Mutant C5: only the fstat check refuses these (a FIFO would block a plain open)."""
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    (tmp_path / "adir").mkdir()
    ghcache._copy_regular(fifo, tmp_path / "out1")
    ghcache._copy_regular(tmp_path / "adir", tmp_path / "out2")
    assert not (tmp_path / "out1").exists() and not (tmp_path / "out2").exists()


def test_a_forged_loose_commit_under_the_pinned_name_is_refused(tmp_path, origin, kept):
    """Mutant C9: fsck is what proves the object named by the pinned sha hashes to that sha.
    Here the pinned commit's loose object holds the OLDER commit; everything after it would
    agree with itself (tree, export, per-file check), so only fsck can refuse it."""
    import zlib
    src, sha, older = origin
    body = subprocess.run(["git", "cat-file", "commit", older], cwd=src, env=GENV, check=True,
                          capture_output=True).stdout
    loose = tmp_path / "loose"           # a store of LOOSE objects (origin's own; packs would be read first)
    g("init", "-q", "--bare", str(loose))
    for d in (src / ".git" / "objects").iterdir():
        if len(d.name) == 2:
            (loose / "objects" / d.name).mkdir()
            for f in d.iterdir():
                (loose / "objects" / d.name / f.name).write_bytes(f.read_bytes())
    kept = loose
    obj = kept / "objects" / sha[:2] / sha[2:]
    assert obj.is_file()
    obj.write_bytes(zlib.compress(b"commit %d\x00" % len(body) + body))
    root = tmp_path / "c"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    assert not (root / "python" / f"cpython@{sha}").exists()


# ---- Step-7 crew review (6E REVIEW-6E-7.md F1-F6): every failure is a PinMismatch, and nothing is left.

def _forged(tmp_path, entries):
    """A bare store holding one commit whose tree is exactly ``entries`` [(name, bytes)], built with
    mktree so names the filesystem would refuse are possible."""
    k = tmp_path / "forged.git"
    g("init", "-q", "--bare", str(k))

    def run(*a, inp):
        return subprocess.run(["git", "--git-dir", str(k), *a], input=inp, env=GENV, check=True,
                              capture_output=True).stdout.decode().strip()
    lines = [f"100644 blob {run('hash-object', '-w', '--stdin', inp=data)}\t{name}" for name, data in entries]
    tree = run("mktree", inp=("\n".join(lines) + "\n").encode())
    return k, run("commit-tree", tree, "-m", "forged", inp=b"")


def _nothing_left(root):
    assert not list(root.glob(".build-*"))
    for d in (root / ".objects" / "python", root / "python"):
        assert not d.exists() or not any(d.iterdir()), sorted(d.iterdir())


def test_a_build_while_another_holds_the_lock_is_refused(tmp_path, origin, kept):
    import fcntl
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    fd = os.open(root / ".build.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(ghcache.PinMismatch, match="another build holds"):
            ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    finally:
        os.close(fd)
    _nothing_left(root)
    assert ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)["commit"] == sha  # lock freed


def test_a_hung_git_is_a_pin_mismatch_not_a_timeout(tmp_path, origin, kept, monkeypatch):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    real = subprocess.run

    def run(cmd, *a, **k):
        if "fsck" in cmd:
            raise subprocess.TimeoutExpired(cmd, 600)
        return real(cmd, *a, **k)
    monkeypatch.setattr(ghcache.subprocess, "run", run)
    with pytest.raises(ghcache.PinMismatch, match="no answer within"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


def test_a_name_the_filesystem_refuses_is_a_pin_mismatch(tmp_path):
    k, sha = _forged(tmp_path, [("a" * 300 + ".py", b"x = 1\n")])
    root = tmp_path / "cache"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch, match="build failed: OSError"):
        ghcache.build(k, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


def test_a_store_over_the_size_cap_is_refused(tmp_path, origin, kept, monkeypatch):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    # 7b review F4: the cap sits between the largest file and the total, so only a TOTAL budget trips it
    sizes = [f.stat().st_size for f in (kept / "objects").rglob("*")
             if f.is_file() and "info" not in f.relative_to(kept / "objects").parts]
    cap = max(sizes)
    assert sum(sizes) > cap and len(sizes) > 1
    monkeypatch.setattr(ghcache, "MAX_STORE_BYTES", cap)
    with pytest.raises(ghcache.PinMismatch, match=f"over {cap} bytes"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


@pytest.mark.parametrize("cap, body, why", [(40, "a" * 40 + "\n", "over 40 bytes"),
                                            (None, b"\xff\xfe", "not a list of commit ids")],
                         ids=["over-cap", "not-ascii"])
def test_the_shallow_file_is_bounded_and_ascii(tmp_path, origin, kept, monkeypatch, cap, body, why):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    if cap is not None:
        monkeypatch.setattr(ghcache, "MAX_SHALLOW_BYTES", cap)
    sh = kept / "shallow"
    sh.write_bytes(body if isinstance(body, bytes) else body.encode())
    with pytest.raises(ghcache.PinMismatch, match=why):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


def test_a_symlinked_org_folder_is_refused_and_nothing_lands_outside(tmp_path, origin, kept):
    _, sha, _ = origin
    root, elsewhere = tmp_path / "cache", tmp_path / "ELSEWHERE"
    root.mkdir()
    elsewhere.mkdir()
    os.symlink(elsewhere, root / "python")
    with pytest.raises(ghcache.PinMismatch, match="not a real folder"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("name", [".git", ".GIT"])
def test_a_dot_git_entry_in_the_tree_is_refused(tmp_path, name):
    k, sha = _forged(tmp_path, [(name, b"gitdir: /elsewhere\n"), ("a.py", b"x = 1\n")])
    root = tmp_path / "cache"
    root.mkdir()
    with pytest.raises(ghcache.PinMismatch, match="a .git entry"):
        ghcache.build(k, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


def test_a_forged_store_with_ordinary_names_still_builds(tmp_path):
    """Control for the two forged-store tests above: the helper itself builds when nothing is wrong."""
    k, sha = _forged(tmp_path, [("a.py", b"x = 1\n")])
    root = tmp_path / "cache"
    root.mkdir()
    assert ghcache.build(k, "python", "cpython", sha, verdict(sha), root=root)["files"] == 1


# ---- 7b adversarial review (FIX-FIRST F1-F3, notes F6-F7)

def test_a_dangling_symlink_at_the_pin_path_is_never_written_through(tmp_path, origin, kept):
    _, sha, _ = origin
    root, outside = tmp_path / "cache", tmp_path / "OUTSIDE"
    (root / "python").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside / "planted.txt", root / "python" / f"cpython@{sha}.pin.json")
    with pytest.raises(ghcache.PinMismatch, match="already present"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("record", ["[1, 2]", '"text"', None], ids=["list", "string", "skipped-int"])
def test_a_corrupt_pin_record_is_a_pin_mismatch_at_check_and_read(built, record):
    root, sha, rec = built
    pin = root / "python" / f"cpython@{sha}.pin.json"
    if record is None:
        import json
        record = json.dumps(dict(rec, skipped=5))
    os.chmod(pin, 0o644)
    pin.write_text(record)
    with pytest.raises(ghcache.PinMismatch):
        ghcache.check_cache("python", "cpython", sha, verdict(sha), full=True, root=root)
    with pytest.raises(ghcache.PinMismatch):
        ghcache.read_file("python", "cpython", sha, "Lib/urllib/request.py", verdict(sha), root=root)


def test_a_build_creates_a_missing_cache_root(tmp_path, origin, kept):
    _, sha, _ = origin
    root = tmp_path / "fresh" / "cache"
    assert ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)["commit"] == sha


def test_a_hung_git_init_is_a_pin_mismatch(tmp_path, origin, kept, monkeypatch):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    real = subprocess.run

    def run(cmd, *a, **k):
        if "init" in cmd:
            raise subprocess.TimeoutExpired(cmd, 60)
        return real(cmd, *a, **k)
    monkeypatch.setattr(ghcache.subprocess, "run", run)
    with pytest.raises(ghcache.PinMismatch, match="build failed: TimeoutExpired"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)
    _nothing_left(root)


def test_a_fifo_at_the_lock_path_is_refused_without_hanging(tmp_path, origin, kept):
    _, sha, _ = origin
    root = tmp_path / "cache"
    root.mkdir()
    os.mkfifo(root / ".build.lock")
    with pytest.raises(ghcache.PinMismatch, match="not a regular file"):
        ghcache.build(kept, "python", "cpython", sha, verdict(sha), root=root)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_cached_file_is_a_pin_mismatch_not_a_permission_error(built):
    """7b review F2: the wrap itself (not the record checks) turns an OSError into BLIND."""
    root, sha, _ = built
    f = root / "python" / f"cpython@{sha}" / "Lib" / "urllib" / "request.py"
    os.chmod(f, 0o000)
    try:
        with pytest.raises(ghcache.PinMismatch, match="PermissionError"):
            ghcache.check_cache("python", "cpython", sha, verdict(sha), full=True, root=root)
        with pytest.raises(ghcache.PinMismatch, match="PermissionError"):
            ghcache.read_file("python", "cpython", sha, "Lib/urllib/request.py", verdict(sha), root=root)
    finally:
        os.chmod(f, 0o444)
