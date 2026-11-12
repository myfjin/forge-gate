"""Round 2 of BRIEF §3.2 — guard lens INCONCLUSIVE becomes UNSUPPORTED(U4:coverage_gap).

The load-bearing property (§9): a lens that could not measure must not accuse
the code. Emitting ERROR bubbles to UNSTABLE at the algebra layer, which IS
an accusation. UNSUPPORTED with an explicit U4 code names the coverage gap
without blame.

RED-ARM: revert lenses.py:_run_guard's INCONCLUSIVE branch back to
``return LensResult("guard", "ERROR", verdict, detail=detail)`` and
``test_guard_inconclusive_is_unsupported_not_error`` fires.
"""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forge_gate import lenses


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_guard_inconclusive_is_unsupported_not_error(monkeypatch):
    """The core of round 2: INCONCLUSIVE must not become ERROR (→ UNSTABLE)."""
    import subprocess as sp
    monkeypatch.setattr(
        sp, "run",
        lambda *a, **kw: _fake_proc("verdict: INCONCLUSIVE 2 parameter(s) unexercised\n")
    )
    r = lenses._run_guard(Path("/tmp/whatever.py"), timeout=10.0)

    assert r.status == "UNSUPPORTED", f"expected UNSUPPORTED, got {r.status!r}"
    assert "U4:coverage_gap" in r.reason, f"reason lost the U4 code: {r.reason!r}"
    assert "INCONCLUSIVE" in r.reason, f"original verdict dropped: {r.reason!r}"
    # And it must not object — a coverage gap is not a defect finding.
    assert r.objected is False


def test_guard_fail_still_gates(monkeypatch):
    """CONTROL — verifies the FAIL path is UNTOUCHED. Without this control,
    a mutant that made ALL branches return UNSUPPORTED would pass the test above.
    """
    import subprocess as sp
    monkeypatch.setattr(
        sp, "run",
        lambda *a, **kw: _fake_proc("verdict: FAIL NaN not propagated\n")
    )
    r = lenses._run_guard(Path("/tmp/whatever.py"), timeout=10.0)
    assert r.status == "FAIL"
    assert r.objected is True


def test_guard_pass_stays_pass(monkeypatch):
    """CONTROL — the PASS path is UNTOUCHED."""
    import subprocess as sp
    monkeypatch.setattr(
        sp, "run",
        lambda *a, **kw: _fake_proc("verdict: PASS all NaN inputs handled\n")
    )
    r = lenses._run_guard(Path("/tmp/whatever.py"), timeout=10.0)
    assert r.status == "PASS"
    assert r.objected is False


def test_guard_timeout_still_becomes_error(monkeypatch):
    """CONTROL — timeout is DoS-shaped (undetermined-with-blast) and stays ERROR
    for now, deliberately. If someone converts it to UNSUPPORTED too, this test
    fires so the change is at least noticed and defended."""
    import subprocess as sp
    def raiser(*a, **kw):
        raise sp.TimeoutExpired(cmd=["x"], timeout=1.0)
    monkeypatch.setattr(sp, "run", raiser)
    r = lenses._run_guard(Path("/tmp/whatever.py"), timeout=10.0)
    assert r.status == "ERROR", f"timeout path changed silently: got {r.status!r}"
