"""One path canonicaliser for forge-gate and its tools, moved verbatim from ``ledger.py``.

``real`` resolves links; ``canonical`` returns the spelling the file system stores; ``is_under``
compares by identity (device + inode). ``ledger.py`` keeps its old private names as aliases;
every other module uses these public names, never the ledger's copies (``tests/test_ledger_routes.py``).
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


def real(p) -> Path:
    return Path(os.path.realpath(p))


def canonical(p) -> Path:
    """The path as the file system spells it, for the path HASHES (8a s7 review F1): on macOS's
    case-insensitive disk ``realpath`` keeps the case that was typed, so ``Proj`` and ``PROJ``
    would be two reuse sites for one folder. ``F_GETPATH`` (macOS) returns the stored spelling;
    elsewhere, or if the path cannot be opened, ``realpath`` (case-sensitive file systems)."""
    rp = os.path.realpath(p)
    if hasattr(fcntl, "F_GETPATH"):
        try:
            fd = os.open(rp, os.O_RDONLY)
        except OSError:
            return Path(rp)
        try:
            return Path(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode(
                "utf-8", "surrogateescape"))
        except OSError:
            return Path(rp)
        finally:
            os.close(fd)
    return Path(rp)


def is_under(p: Path, root: Path) -> bool:
    """By identity (device + inode), not by spelling: on a case-insensitive file system two
    spellings of one folder are one folder (8a s3 review F6).

    Callers pass RESOLVED paths (``real(...)``): the walk is over ``p``'s lexical parents, so an
    unresolved ``root/link/x`` whose link points outside ``root`` still counts as under it."""
    try:
        r = os.stat(root)
    except OSError:
        return False
    for q in (p, *p.parents):
        try:
            st = os.stat(q)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == (r.st_dev, r.st_ino):
            return True
    return False
