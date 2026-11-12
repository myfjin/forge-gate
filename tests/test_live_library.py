"""Live tier: compare the shipped reference copies with the pattern library.

Deselected by default (pyproject: ``-m "not live"``). Run with ``pytest -m live``.
Needs FORGE_GATE_LIBRARY_DIR; when it is unset the test FAILS naming the key (project
standard §6: a test needing live data fails loudly, it never skips quietly).
"""
from __future__ import annotations

import hashlib
import re

import pytest

from forge_gate import config
from forge_gate.catalog import reference_dir

pytestmark = pytest.mark.live

_ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \| `([0-9a-f]{64})` \|$")


def test_reference_copies_equal_the_library():
    lib = config.library_dir()  # unset -> NotConfigured naming the key
    text = (reference_dir() / "ORIGIN.md").read_text(encoding="utf-8")
    rows = [m.groups() for m in map(_ROW.match, text.splitlines()) if m]
    assert rows
    drift = []
    for fname, libpath, sha in rows:
        got = hashlib.sha256((lib / libpath).read_bytes()).hexdigest()
        if got != sha:
            drift.append(f"{libpath}: library {got[:12]} != copied {sha[:12]} ({fname})")
    assert not drift, "library moved since the copy; re-copy and update ORIGIN.md:\n" + "\n".join(drift)


def test_library_checkout_is_at_the_pinned_commit():
    """Drift is graded, not printed (standard §6 tooth c)."""
    import subprocess
    from pathlib import Path

    lib = config.library_dir()
    lock = (Path(__file__).resolve().parents[1] / "deps.lock").read_text(encoding="utf-8")
    pin = re.search(r'key = "FORGE_GATE_LIBRARY_DIR".*?pin = "commit ([0-9a-f]{40})"', lock, re.S).group(1)
    # Read-only, so any user may read another user's checkout: safe.directory on this one
    # call. A checkout that still cannot be read fails as UNREADABLE, never as a pin
    # mismatch (6E review KD3-2): the two need different fixes.
    import getpass

    r = subprocess.run(["git", "-c", f"safe.directory={lib}", "-C", str(lib), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.fail(f"library at {lib} UNREADABLE as {getpass.getuser()} "
                    f"(git exit {r.returncode}): {r.stderr.strip()[:300]}")
    head = r.stdout.strip()
    assert head == pin, f"library at {lib} is at {head[:12]}, deps.lock pins {pin[:12]} (KNOWN-DEBT KD-3)"
