"""§3.2 Round 4 — twin-consensus adapter emits U1:no_coverage.

All three UNSUPPORTED shapes at tools/twin_consensus.py are tool coverage gaps
(verified 2026-09-05): CatalogError (line 272), fixture missing under
src/forge_gate/fixtures/ (line 276), no twins configured (line 336). Single
code at the adapter; classifier at the tool layer if a shape ever needs U-code
discrimination.

RED-ARM: reverting the adapter's UNSUPPORTED branch to the pre-round-4 form
drops the load-bearing test RED.
"""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace

from forge_gate import lenses


def _tc(status, reason, details=None):
    """Build a TwinConsensus-shaped object (SimpleNamespace with the fields the adapter reads)."""
    return SimpleNamespace(status=status, reason=reason, details=details or [])


def test_twin_unsupported_carries_u1_code(monkeypatch):
    """Load-bearing: any UNSUPPORTED from the tool becomes U1:no_coverage."""
    import sys
    fake = SimpleNamespace(consensus_verify=lambda uf, p, timeout=60.0:
                           _tc("UNSUPPORTED", "fixture missing: /some/path"))
    monkeypatch.setitem(sys.modules, "twin_consensus", fake)

    r = lenses._run_twin(Path("/tmp/w.py"), "foo", timeout=10.0)
    assert r.status == "UNSUPPORTED", f"got {r.status}: {r.reason!r}"
    assert "U1:no_coverage" in r.reason, f"code missing: {r.reason!r}"
    assert "fixture missing" in r.reason, f"detail dropped: {r.reason!r}"


def test_twin_unsupported_preserves_reason_regardless_of_shape(monkeypatch):
    """Second load-bearing: ANY reason string from the tool preserves as detail.

    Also serves as drift-catcher — if the tool ever ships a shape that isn't
    genuinely U1 (e.g. adds a "contract not ratified"-style reason), this test
    still passes as long as the string is preserved, but the reason string
    would visibly not fit U1's meaning and prompt a re-classification.
    """
    import sys
    fake = SimpleNamespace(consensus_verify=lambda uf, p, timeout=60.0:
                           _tc("UNSUPPORTED", "no language twins found for this pattern"))
    monkeypatch.setitem(sys.modules, "twin_consensus", fake)

    r = lenses._run_twin(Path("/tmp/w.py"), "foo", timeout=10.0)
    assert r.status == "UNSUPPORTED"
    assert "U1:no_coverage" in r.reason
    assert "no language twins" in r.reason


def test_twin_fail_still_gates(monkeypatch):
    """CONTROL — the FAIL path is UNCHANGED by round 4."""
    import sys
    fake = SimpleNamespace(consensus_verify=lambda uf, p, timeout=60.0:
                           _tc("FAIL", "user implementation fails property check"))
    monkeypatch.setitem(sys.modules, "twin_consensus", fake)

    r = lenses._run_twin(Path("/tmp/w.py"), "foo", timeout=10.0)
    assert r.status == "FAIL"
    assert r.objected is True


def test_twin_unstable_still_objects(monkeypatch):
    """CONTROL — the UNSTABLE (twins disagree) path is UNCHANGED by round 4."""
    import sys
    fake = SimpleNamespace(consensus_verify=lambda uf, p, timeout=60.0:
                           _tc("UNSTABLE", "reference twins disagree: py vs cpp"))
    monkeypatch.setitem(sys.modules, "twin_consensus", fake)

    r = lenses._run_twin(Path("/tmp/w.py"), "foo", timeout=10.0)
    assert r.status == "UNSTABLE"
    assert r.objected is True
