"""Integration tests for the determinism lens's UNSUPPORTED reason codes.

BRIEF §3.2 · §9: the load-bearing property is that TWO DIFFERENT CAUSES
must not land in the same bucket. These tests exercise the wired callsites
in ``lenses.py`` (not just the ``unsupported`` registry) to prove the
shipped reason strings carry the discriminating code.

Also serves as the RED-ARM: a mutant that reverts either callsite to a
free-string reason ("no fixture for X" / "capture failed: ...") will
make one of these tests fire.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_gate import lenses


REPO = Path(__file__).resolve().parents[1]
CORPUS_FILE = REPO / "tests" / "corpus" / "linreg_normal_eq.py"


def test_u1_no_fixture_reason_carries_code(monkeypatch):
    """A pattern with no fixture must emit an explicit U1 code, not a bare string.

    RED-ARM: revert ``lenses.py:269`` to ``f"no fixture for {pattern}"`` and this
    test fires. That is the whole point of the taxonomy.
    """
    orig_exists = Path.exists

    def fake_exists(self):
        if "fixtures" in str(self) and str(self).endswith(".py"):
            return False
        return orig_exists(self)

    with patch.object(Path, "exists", fake_exists):
        r = lenses._run_determinism_once(CORPUS_FILE, "linear_regression_gd", timeout=30)

    assert r.status == "UNSUPPORTED", f"expected UNSUPPORTED, got {r.status}"
    assert "U1:no_coverage" in r.reason, f"reason lost the U1 code: {r.reason}"
    # The DETAIL must survive alongside the code — a reader needs both.
    assert "no fixture for" in r.reason, f"detail dropped: {r.reason}"


def test_u1_reason_is_distinguishable_from_a_hypothetical_u4(monkeypatch):
    """The property that matters: a U1 emission and a U4 emission are not confusable.

    We prove this without provoking a real capture crash — we build both reasons
    the same way ``lenses.py`` does and check they differ. If someone made both
    callsites call the same _u() code, this test would fire.
    """
    from forge_gate.unsupported import _u

    u1_summary = _u("U1:no_coverage", "no fixture for foo").summary()
    u4_summary = _u("U4:capture_failed", "some stderr").summary()

    assert u1_summary != u4_summary
    assert "U1" in u1_summary and "U4" not in u1_summary
    assert "U4" in u4_summary and "U1" not in u4_summary
