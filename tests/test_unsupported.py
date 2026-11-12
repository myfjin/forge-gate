"""Tests for the UNSUPPORTED reason-code registry (BRIEF §3.2).

⛔ THE TEST IS NOT "do the reason codes exist".
THE TEST IS: **can two different causes land in the same bucket?**
"""
from __future__ import annotations
import pytest
from forge_gate.unsupported import _u, declared_codes, Unsupported, _REGISTRY


# ------------------------------------------------------------------ registry discipline

def test_undeclared_code_raises_keyerror():
    """The whole point: an undeclared code cannot ship."""
    with pytest.raises(KeyError):
        _u("U9:this_does_not_exist", "detail")


def test_every_declared_code_has_all_fields():
    """A code with no declared remedy is a code that should not exist."""
    for code in declared_codes():
        u = _u(code, "test")
        assert u.subclass in ("U1", "U2", "U3", "U4"), f"{code}: bad subclass {u.subclass}"
        assert u.meaning.strip(), f"{code}: empty meaning"
        assert u.remedy.strip(), f"{code}: empty remedy"
        assert len(u.dispositions) >= 1, f"{code}: no allowed dispositions"


def test_subclass_matches_code_prefix():
    """U1:x codes must have subclass U1. A mismatch means the registry is inconsistent."""
    for code in declared_codes():
        prefix = code.split(":", 1)[0]
        u = _u(code, "")
        assert u.subclass == prefix, f"{code}: prefix {prefix!r} != subclass {u.subclass!r}"


# ------------------------------------------------------------------ the load-bearing property
#   Two different CAUSES must not land in the same bucket. This is the property
#   §3.2 exists to enforce, and the test §9 explicitly names.

def test_different_causes_do_not_collide_in_summary():
    """A U1 emission and a U4 emission with the same detail must be distinguishable."""
    detail = "same detail string"
    u1 = _u("U1:no_coverage", detail)
    u4 = _u("U4:capture_failed", detail)
    assert u1.summary() != u4.summary(), "distinct causes produced identical summary"
    assert u1.code != u4.code
    assert u1.subclass != u4.subclass


def test_input_malformed_and_capture_failed_are_distinct():
    """U3 (cause CERTAIN) and U4 (cause UNDETERMINED) must not conflate.

    Reporting "your code failed" when the honest answer is "our analyser could
    not complete" is the single most damaging thing this tool can do.
    """
    u3 = _u("U3:input_malformed", "malformed")
    u4 = _u("U4:capture_failed", "died")
    # dispositions must differ — U3 puts blame on input, U4 admits ambiguity
    assert u3.dispositions != u4.dispositions
    assert "TOOL_WORK" not in u3.dispositions
    assert "TOOL_WORK" in u4.dispositions


# ------------------------------------------------------------------ integrity

def test_all_codes_unique():
    assert len(declared_codes()) == len(set(declared_codes()))


def test_detail_is_truncated():
    """A wall of stderr should not become the reason string."""
    huge = "x" * 5000
    u = _u("U4:capture_failed", huge)
    assert len(u.detail) <= 200


def test_summary_contains_code_and_meaning():
    """A reader must be able to see the code AND its meaning without an atlas."""
    u = _u("U4:capture_failed", "exit 1")
    assert "U4:capture_failed" in u.summary()
    assert "undetermined" in u.summary().lower()


# ------------------------------------------------------------------ MUTATION GATES
#   These are the gates that catch a future edit that quietly re-collapses the taxonomy.
#   If someone changes _u() to always return the same object, these fire.

def test_mutation_gate_different_codes_produce_different_objects():
    """A mutant that made _u() return the same object for every code would survive
    the unit tests above if we only checked one code at a time. This forces the diff."""
    codes = declared_codes()
    assert len(codes) >= 4, "registry has fewer than the 4 documented subclasses"
    seen_summaries = {_u(c, "same-detail").summary() for c in codes}
    assert len(seen_summaries) == len(codes), "at least two codes collapsed to the same summary"


def test_mutation_gate_dispositions_are_not_all_identical():
    """A mutant that hardcoded a single frozenset for every code would produce
    a taxonomy that discriminates on paper and not in effect."""
    disposition_sets = {frozenset(_u(c, "").dispositions) for c in declared_codes()}
    assert len(disposition_sets) >= 2, "every code emits identical dispositions — taxonomy is decoration"
