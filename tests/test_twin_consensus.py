"""Regression tests for twin-consensus drift detection.

Guards the 2026-07-24 fix: "no shared metric" is a COVERAGE GAP, not disagreement.
The danger of that fix is over-correction -- silently passing real drift. These tests
are the negative control: genuine divergence MUST still be caught.

Exercises _metrics_consensus directly with synthetic twin results, so no signed
library twin is ever mutated.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from twin_consensus import _metrics_consensus  # noqa: E402


def twin(name, status="PASS", **metrics):
    return {"twin": name, "lang": "python", "status": status, "metrics": dict(metrics)}


# ---------------------------------------------------- drift MUST still be caught

def test_diverging_shared_metric_is_disagreement():
    ok, dis, notes = _metrics_consensus([twin("a.py", r2=0.99), twin("b.cpp", r2=0.42)])
    assert ok is False
    assert dis and "r2" in dis[0]
    assert notes == []


def test_status_mismatch_is_disagreement():
    ok, dis, notes = _metrics_consensus([twin("a.py", r2=0.99), twin("b.cpp", "FAIL", r2=0.99)])
    assert ok is False
    assert dis and "status" in dis[0]


def test_tiny_numeric_difference_still_agrees():
    ok, dis, notes = _metrics_consensus([twin("a.py", r2=0.990000), twin("b.cpp", r2=0.9900001)])
    assert ok is True
    assert dis == []


# ---------------------------------------------------- the coverage-gap fix

def test_no_shared_metric_is_a_coverage_note_not_a_disagreement():
    """The gradient_descent_momentum case: both PASS, no metric name in common."""
    ok, dis, notes = _metrics_consensus([twin("gd.py", iters=179), twin("gd.cpp", steps=181)])
    assert ok is True, "absence of a comparable metric is not evidence of drift"
    assert dis == []
    assert notes and "metric-level drift NOT checked" in notes[0]


def test_coverage_gap_is_disclosed_never_silent():
    _, _, notes = _metrics_consensus([twin("a.py", x=1.0), twin("b.cpp", y=1.0)])
    assert len(notes) == 1
    assert "status-level consensus only" in notes[0]


def test_matching_metrics_produce_no_coverage_note():
    ok, dis, notes = _metrics_consensus([twin("a.py", r2=0.9), twin("b.cpp", r2=0.9)])
    assert (ok, dis, notes) == (True, [], [])


def test_partial_overlap_compares_the_shared_key_only():
    """Extra keys on one side must not trigger a coverage note."""
    ok, dis, notes = _metrics_consensus(
        [twin("a.py", r2=0.9, bias=0.1), twin("b.cpp", r2=0.9)]
    )
    assert ok is True and notes == []


def test_partial_overlap_still_catches_drift_on_the_shared_key():
    ok, dis, notes = _metrics_consensus(
        [twin("a.py", r2=0.9, bias=0.1), twin("b.cpp", r2=0.1)]
    )
    assert ok is False and dis
