"""The shared path canonicaliser (shared-tools S1): ``forge_gate.paths``, moved from ``ledger.py``.

Which test runs where: a case variant of a folder exists only on a case-insensitive file system
(macOS APFS); a non-UTF-8 file name can be made only where the file system takes raw bytes (Linux).
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from forge_gate import ledger, paths


def _case_variant(p: Path) -> Path:
    return p.parent / p.name.swapcase()


def _needs_case_insensitive(p: Path) -> None:
    if not _case_variant(p).exists():
        pytest.skip("case-sensitive file system: one spelling per folder")


def test_canonical_returns_the_stored_spelling_of_a_case_variant(tmp_path):
    d = tmp_path / "MixedCase"
    d.mkdir()
    _needs_case_insensitive(d)
    variant = _case_variant(d)                                   # mIXEDcASE, typed differently
    assert paths.canonical(variant).name == "MixedCase"
    assert paths.canonical(variant) == paths.canonical(d)
    assert paths.canonical(variant / ".." / "MixedCase") == paths.canonical(d)


def test_is_under_through_a_case_variant(tmp_path):
    root = tmp_path / "Root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f.py").write_text("x = 1\n")
    (tmp_path / "Other").mkdir()
    _needs_case_insensitive(root)
    typed = _case_variant(root) / "a" / "f.py"                   # rOOT/a/f.py
    assert paths.is_under(paths.real(typed), paths.real(root))
    assert paths.is_under(paths.real(root / "a" / "f.py"), paths.real(_case_variant(root)))
    assert not paths.is_under(paths.real(tmp_path / "Other"), paths.real(_case_variant(root)))


def test_is_under_through_a_symlink(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "in.py").write_text("x = 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "out.py").write_text("x = 1\n")
    (root / "out_link").symlink_to(outside, target_is_directory=True)        # inside root, points out
    (tmp_path / "in_link").symlink_to(root / "sub", target_is_directory=True)  # outside root, points in
    # a link inside root pointing out is NOT under root once resolved
    assert not paths.is_under(paths.real(root / "out_link" / "out.py"), paths.real(root))
    # a link outside root pointing in IS under root once resolved
    assert paths.is_under(paths.real(tmp_path / "in_link" / "in.py"), paths.real(root))
    # the documented limit: the walk is lexical, so an UNRESOLVED path through the out-link counts
    assert paths.is_under(root / "out_link" / "out.py", paths.real(root))


def test_missing_path_falls_back_to_realpath(tmp_path):
    # through a link, so "the fallback resolves" is visible: tmp_path itself is already resolved
    (tmp_path / "tgt").mkdir()
    (tmp_path / "ln").symlink_to(tmp_path / "tgt", target_is_directory=True)
    missing = tmp_path / "ln" / "no" / "such" / "file.py"
    want = tmp_path / "tgt" / "no" / "such" / "file.py"
    assert paths.canonical(missing) == want
    assert paths.real(missing) == want
    assert paths.is_under(paths.real(missing), paths.real(tmp_path / "tgt"))   # missing parents are skipped
    assert not paths.is_under(paths.real(tmp_path), paths.real(tmp_path / "no"))  # missing root


def test_undecodable_name_round_trips(tmp_path):
    raw = os.path.join(os.fsencode(tmp_path), b"bad\xffname")
    try:
        os.mkdir(raw)
    except OSError as e:
        if e.errno == errno.EILSEQ:
            pytest.skip("file system refuses non-UTF-8 names (EILSEQ, APFS)")
        raise
    p = Path(os.fsdecode(raw))                                   # carries a surrogate escape
    assert os.fsencode(str(paths.canonical(p))) == os.fsencode(os.path.realpath(raw))
    assert os.fsencode(str(paths.real(p))) == os.path.realpath(raw)
    assert paths.is_under(paths.real(p), paths.real(tmp_path))


def test_the_ledger_uses_the_shared_copy():
    assert (ledger._real, ledger._canon, ledger._under) == (paths.real, paths.canonical, paths.is_under)
