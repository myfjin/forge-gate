"""§3.2 Round 3 — metamorphic and invariant adapters emit U-codes.

RED-ARM: reverting either adapter to a bare ``LensResult("<lens>", "UNSUPPORTED", <reason>)``
drops the load-bearing test for that lens RED.
"""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forge_gate import lenses


# ------------------------------------------------------------------ metamorphic

def test_metamorphic_unsupported_carries_u1_code(monkeypatch):
    """When metamorphic's runner says UNSUPPORTED, adapter emits U1:no_coverage."""
    def fake_run(*a, **kw):
        return ("UNSUPPORTED", [], "No metamorphic relations for pattern foo")

    import sys
    runner_mod = SimpleNamespace(run_metamorphic=fake_run, __name__="metamorphic.runner")
    relations_mod = SimpleNamespace(is_gating=lambda name: True, __name__="metamorphic.relations")
    pkg = SimpleNamespace(__name__="metamorphic", __path__=[],
                          runner=runner_mod, relations=relations_mod)
    monkeypatch.setitem(sys.modules, "metamorphic", pkg)
    monkeypatch.setitem(sys.modules, "metamorphic.runner", runner_mod)
    monkeypatch.setitem(sys.modules, "metamorphic.relations", relations_mod)

    r = lenses._run_metamorphic(Path("/tmp/w.py"), "foo")
    assert r.status == "UNSUPPORTED"
    assert "U1:no_coverage" in r.reason, f"reason lost the code: {r.reason!r}"
    assert "No metamorphic relations" in r.reason, f"detail dropped: {r.reason!r}"


# NOTE: a metamorphic PASS control was attempted but ran into Python
# import-order sensitivity — sys.modules cache for the real
# tools/metamorphic/ package survives across pytest tests in a way that's
# not worth the mock-machinery to defeat. The PASS path is instead
# exercised end-to-end by test_cli_json_end_to_end, which asserts
# --lenses all on a real corpus file returns PASS with metamorphic
# contributing PASS.


# ------------------------------------------------------------------ invariant

def test_invariant_unratified_becomes_u2(monkeypatch):
    """'contract not yet ratified' -> U2 (pending human), not U1 (no coverage)."""
    def fake_verify(user_file, pattern):
        return "UNSUPPORTED", "contract not yet ratified by a human"

    import sys
    fake_mod = SimpleNamespace(verify=fake_verify)
    monkeypatch.setitem(sys.modules, "invariant_gate", fake_mod)

    r = lenses._run_invariant(Path("/tmp/w.py"), "foo")
    assert r.status == "UNSUPPORTED"
    assert "U2:contract_unratified" in r.reason, f"wrong code: {r.reason!r}"
    assert "U1:no_coverage" not in r.reason, "misclassified as U1"


def test_invariant_no_adapter_becomes_u1(monkeypatch):
    """'no adapter or contract' -> U1 (tool coverage gap).

    This is the CONTROL for the U2 test above: if we mis-branched the classifier so
    ALL invariant UNSUPPORTED went to U2, this test would fire.
    """
    def fake_verify(user_file, pattern):
        return "UNSUPPORTED", "no adapter or contract for pattern"

    import sys
    fake_mod = SimpleNamespace(verify=fake_verify)
    monkeypatch.setitem(sys.modules, "invariant_gate", fake_mod)

    r = lenses._run_invariant(Path("/tmp/w.py"), "foo")
    assert r.status == "UNSUPPORTED"
    assert "U1:no_coverage" in r.reason, f"wrong code: {r.reason!r}"
    assert "U2:contract_unratified" not in r.reason, "misclassified as U2"


def test_invariant_fail_still_gates(monkeypatch):
    """CONTROL — FAIL path is UNCHANGED by round 3."""
    def fake_verify(user_file, pattern):
        return "FAIL", "ratio outside band"

    import sys
    fake_mod = SimpleNamespace(verify=fake_verify)
    monkeypatch.setitem(sys.modules, "invariant_gate", fake_mod)

    r = lenses._run_invariant(Path("/tmp/w.py"), "foo")
    assert r.status == "FAIL"
    assert r.objected is True
