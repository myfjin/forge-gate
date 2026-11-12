"""Tests for VIEW_COVERAGE — strict declaration-vs-adapter agreement.

Both RED-arm directions (undeclared-read and declared-but-unread) must be
INDEPENDENT gates. A single-direction test is advisory equivalence — the same
shape as the ADVISORY_LENSES label that lied for 32 days (6E 3b).

The check fires at test time, not import time — a mismatch is a developer
signal, not a verdict signal (6E 3d).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forge_gate import lenses as L  # noqa: E402
from forge_gate.view_coverage import check_all, derive_view  # noqa: E402


# ---- Test 1: green baseline — no mismatches on current code ------------------

def test_green_baseline_no_mismatches():
    """Every adapter's derived view must equal its declared VIEW_COVERAGE.

    Assert BOTH check_all() green AND derive_view() reports the right sets for
    two representative adapters. Mutation-verified 2026-09-05: without the
    derive_view assertions, a gutted check_all() -> [] would vacuously
    satisfy mismatches == [] and this test would be decoration.
    """
    mismatches = check_all(L)
    if mismatches:
        for name, reason in mismatches:
            print(f"  MISMATCH: {name}: {reason}")
    assert mismatches == [], f"VIEW_COVERAGE mismatches: {mismatches}"
    # Direct derive_view checks -- catch a broken check_all that returns [].
    # Guard is the pattern-agnostic asymmetry (6E); metamorphic is the
    # simplest {user_file, pattern} case.
    assert derive_view(L._run_guard) == frozenset({"user_file", "timeout"}),         f"guard derived view wrong: {derive_view(L._run_guard)}"
    assert derive_view(L._run_metamorphic) == frozenset({"user_file", "pattern"}),         f"metamorphic derived view wrong: {derive_view(L._run_metamorphic)}"


# ---- Test 2: RED-arm — undeclared read direction -----------------------------

def test_red_arm_undeclared_read():
    """Dropping a declared input that IS consumed must be caught.

    If _run_metamorphic reads `pattern` but the declaration drops it, the
    check must report an undeclared-read mismatch. This proves the check
    catches the 'adapter reads something it does not declare' direction.
    """
    original = L.VIEW_COVERAGE.copy()
    try:
        patched = dict(L.VIEW_COVERAGE)
        patched["_run_metamorphic"] = frozenset({"user_file"})  # dropped "pattern"
        L.VIEW_COVERAGE = patched
        mismatches = check_all(L)
        assert any(
            "undeclared reads" in reason and "_run_metamorphic" in name
            for name, reason in mismatches
        ), f"expected undeclared-read mismatch for _run_metamorphic, got: {mismatches}"
    finally:
        L.VIEW_COVERAGE = original


# ---- Test 3: RED-arm — declared but unread direction -------------------------

def test_red_arm_declared_but_unread():
    """Adding a fake input that is NOT consumed must be caught.

    If _run_metamorphic's declaration adds `banana` (not a parameter, not
    consumed), the check must report a declared-but-unread mismatch. This
    proves the check catches the 'adapter declares something it does not
    consume' direction.
    """
    original = L.VIEW_COVERAGE.copy()
    try:
        patched = dict(L.VIEW_COVERAGE)
        patched["_run_metamorphic"] = frozenset({"user_file", "pattern", "banana"})
        L.VIEW_COVERAGE = patched
        mismatches = check_all(L)
        assert any(
            "declared but unread" in reason and "_run_metamorphic" in name
            for name, reason in mismatches
        ), f"expected declared-but-unread mismatch for _run_metamorphic, got: {mismatches}"
    finally:
        L.VIEW_COVERAGE = original


# ---- Test 4: canonical/twin — both directions move INDEPENDENTLY -------------

def test_conceptual_inputs_both_directions():
    """For a lens with conceptual inputs (twin), both directions must fire.

    Direction 1 — undeclared read: drop `canonical_oracle` from _run_twin's
    declaration. The adapter still consumes it (via consensus_verify call),
    so the check must report an undeclared read.

    Direction 2 — declared but unread: add a fake input to _run_twin's
    declaration. The adapter does not consume it, so the check must report
    a declared-but-unread mismatch.

    These are INDEPENDENT: each direction's mismatch is reported separately,
    and neither direction's test accidentally triggers the other.
    """
    original = L.VIEW_COVERAGE.copy()

    # Direction 1: drop a conceptual input that IS consumed.
    try:
        patched = dict(L.VIEW_COVERAGE)
        patched["_run_twin"] = frozenset({"user_file", "pattern", "twin", "timeout"})
        #                          dropped: canonical_oracle ^^^
        L.VIEW_COVERAGE = patched
        mismatches = check_all(L)
        undeclared = [
            (n, r) for n, r in mismatches
            if "undeclared reads" in r and "_run_twin" in n
        ]
        assert undeclared, (
            f"dropping canonical_oracle must be caught as undeclared read, "
            f"got: {mismatches}"
        )
        # Verify the specific input is named.
        assert "canonical_oracle" in undeclared[0][1], (
            f"mismatch should name canonical_oracle, got: {undeclared[0][1]}"
        )
    finally:
        L.VIEW_COVERAGE = original

    # Direction 2: add a fake input that is NOT consumed.
    try:
        patched = dict(L.VIEW_COVERAGE)
        patched["_run_twin"] = frozenset({
            "user_file", "pattern", "canonical_oracle", "twin", "timeout", "banana",
        })
        L.VIEW_COVERAGE = patched
        mismatches = check_all(L)
        unread = [
            (n, r) for n, r in mismatches
            if "declared but unread" in r and "_run_twin" in n
        ]
        assert unread, (
            f"adding banana must be caught as declared but unread, "
            f"got: {mismatches}"
        )
        assert "banana" in unread[0][1], (
            f"mismatch should name banana, got: {unread[0][1]}"
        )
    finally:
        L.VIEW_COVERAGE = original

    # Independence: verify the two directions do NOT cross-trigger.
    # Dropping canonical_oracle should NOT produce a declared-but-unread mismatch.
    try:
        patched = dict(L.VIEW_COVERAGE)
        patched["_run_twin"] = frozenset({"user_file", "pattern", "twin", "timeout"})
        L.VIEW_COVERAGE = patched
        mismatches = check_all(L)
        cross = [
            (n, r) for n, r in mismatches
            if "declared but unread" in r and "_run_twin" in n
        ]
        assert not cross, (
            f"dropping canonical_oracle should NOT produce declared-but-unread, "
            f"got: {cross}"
        )
    finally:
        L.VIEW_COVERAGE = original