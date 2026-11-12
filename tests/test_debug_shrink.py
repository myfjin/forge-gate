"""Test ddmin: shrink a failing input to a minimal failing subset.

BRIEF-DEBUG-FEATURE-2026-09-05.md §3.3 (delta debugging) and §8.2 (cap
the search and report the cap). Taxonomy-agnostic; no forge-gate imports.
"""
from __future__ import annotations

import pytest

from forge_gate.debug_core import ShrinkResult, ddmin


# ─────────────────────────────────────────────────────────────────────────────
# Zeller's canonical example: input fails iff {1, 8} both present.
# Minimal failing subset is exactly [1, 8].
# ─────────────────────────────────────────────────────────────────────────────


def _fail_if_contains(*required):
    req = set(required)
    def pred(xs):
        return req.issubset(set(xs))
    return pred


def test_ddmin_finds_minimal_pair():
    elements = [1, 2, 3, 4, 5, 6, 7, 8]
    r = ddmin(elements, _fail_if_contains(1, 8))
    assert isinstance(r, ShrinkResult)
    assert r.predicate_ever_failed is True
    assert sorted(r.minimal) == [1, 8]
    # Should have converged well under the default cap
    assert r.hit_step_cap is False


def test_ddmin_finds_single_failing_element():
    elements = ["a", "b", "c", "d", "BAD", "e", "f"]
    r = ddmin(elements, lambda xs: "BAD" in xs)
    assert r.minimal == ["BAD"]
    assert r.predicate_ever_failed is True


def test_ddmin_preserves_original_order():
    """The shrinker must not reorder elements — it removes them."""
    elements = list(range(20))
    r = ddmin(elements, _fail_if_contains(3, 17))
    assert r.minimal == [3, 17]  # in original order, not [17, 3]


# ─────────────────────────────────────────────────────────────────────────────
# Boundary + degenerate cases
# ─────────────────────────────────────────────────────────────────────────────


def test_ddmin_empty_input_returns_empty():
    r = ddmin([], lambda xs: True)
    assert r.minimal == []
    assert r.steps == 0
    assert r.predicate_ever_failed is False


def test_ddmin_predicate_never_fails_returns_original():
    """If the input does not reproduce the failure, ddmin returns it
    unshrunk with predicate_ever_failed=False so the caller can distinguish
    'already minimal' from 'never failed at all'."""
    elements = [1, 2, 3, 4, 5]
    r = ddmin(elements, lambda xs: False)
    assert r.minimal == elements
    assert r.predicate_ever_failed is False
    # Only the initial reproduce-check should have run
    assert r.steps == 1


def test_ddmin_already_minimal_returns_immediately():
    """A one-element failing input is already minimal — no shrinking to do.
    The initial reproduce check counts as one step; the outer loop should
    exit at the |c| >= 2 gate without further evaluations."""
    r = ddmin([42], lambda xs: 42 in xs)
    assert r.minimal == [42]
    assert r.predicate_ever_failed is True
    assert r.steps == 1


# ─────────────────────────────────────────────────────────────────────────────
# Determinism and step-cap
# ─────────────────────────────────────────────────────────────────────────────


def test_ddmin_deterministic_across_runs():
    """Same input + same predicate ⇒ same ShrinkResult. Acceptance criterion 4
    of the brief."""
    elements = list(range(30))
    pred = _fail_if_contains(5, 11, 22)
    r1 = ddmin(elements, pred)
    r2 = ddmin(elements, pred)
    assert r1.minimal == r2.minimal
    assert r1.steps == r2.steps
    assert r1.hit_step_cap == r2.hit_step_cap


def test_ddmin_hit_step_cap_reports_partial():
    """§8.2: cap the search and REPORT the cap. A tiny max_steps must return
    hit_step_cap=True and the best-so-far, not raise or silently succeed."""
    elements = list(range(100))
    pred = _fail_if_contains(3, 97)
    r = ddmin(elements, pred, max_steps=3)
    assert r.hit_step_cap is True
    # Best-so-far still reproduces the failure
    assert pred(r.minimal) is True
    assert r.steps <= 3 + 1  # at most cap + the initial reproduce-check


# ─────────────────────────────────────────────────────────────────────────────
# Predicate errors — safe default
# ─────────────────────────────────────────────────────────────────────────────


def test_ddmin_predicate_raise_treated_as_pass():
    """A predicate that raises on a candidate must NOT cause that candidate
    to be adopted as a shrink — the safe default is 'did not confirm the
    failure', which prevents ddmin from converging on inputs that broke the
    predicate itself rather than exhibiting the target defect."""
    elements = [1, 2, 3, 4, 5, 6]
    def pred(xs):
        # Raise on any subset missing element 3 — simulates predicate breaking
        # on inputs unrelated to the real defect.
        if 3 not in xs:
            raise RuntimeError("predicate broke")
        return {1, 6}.issubset(set(xs))
    r = ddmin(elements, pred)
    # Real minimal is {1, 6}; but any candidate missing 3 raises, so the
    # shrinker cannot converge past a subset that still contains 3.
    assert 3 in r.minimal
    assert 1 in r.minimal and 6 in r.minimal
    assert r.predicate_ever_failed is True
