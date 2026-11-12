"""Tests for step-3 helpers in debug_core: capture_traceback, dump_leaf_locals,
_safe_repr. All three are taxonomy-agnostic; nothing forge-gate-specific here.
BRIEF-DEBUG-FEATURE-2026-09-05.md §9 step 3.
"""
from __future__ import annotations

from forge_gate.debug_core import (
    capture_traceback,
    dump_leaf_locals,
    _safe_repr,
    _TB_CAP_BYTES,
    _REPR_CAP,
)


# ─────────────────────────────────────────────────────────────────────────────
# _safe_repr — never raises, never overruns cap
# ─────────────────────────────────────────────────────────────────────────────


def test_safe_repr_plain_value():
    assert _safe_repr(42) == "42"
    assert _safe_repr("hello") == "'hello'"


def test_safe_repr_defends_against_raising_repr():
    class Boom:
        def __repr__(self) -> str:
            raise RuntimeError("nope")
    r = _safe_repr(Boom())
    assert "unrepr-able" in r
    assert "Boom" in r
    assert "RuntimeError" in r


def test_safe_repr_caps_long_value():
    huge = "x" * 5000
    r = _safe_repr(huge, cap=50)
    assert len(r) <= 50
    assert "chars total" in r
    # cap must be respected even for tiny caps that force the suffix to eat
    # everything (`keep` clamps to 0 rather than going negative)
    r_tiny = _safe_repr(huge, cap=10)
    assert len(r_tiny) <= 10 or r_tiny == f"... [{len(repr(huge))} chars total]"


# ─────────────────────────────────────────────────────────────────────────────
# dump_leaf_locals
# ─────────────────────────────────────────────────────────────────────────────


def test_dump_leaf_locals_none_traceback_returns_none():
    assert dump_leaf_locals(None) is None


def test_dump_leaf_locals_returns_innermost_frame_locals():
    def _inner():
        secret = "leaf-value"
        raise ValueError("x")
    def _outer():
        outer_var = "not-here"
        _inner()
    try:
        _outer()
    except ValueError as e:
        got = dump_leaf_locals(e.__traceback__)
    assert got is not None
    assert got["secret"] == "'leaf-value'"
    # outer_var lives in the caller frame, not the leaf -> must NOT appear
    assert "outer_var" not in got


def test_dump_leaf_locals_source_only_filters_out_wrong_file():
    def _inner():
        marker = 1
        raise ValueError("x")
    try:
        _inner()
    except ValueError as e:
        # a path we know cannot match this test file -> filter drops the frame
        got = dump_leaf_locals(e.__traceback__, source_only="/definitely/not/this.py")
    assert got is None


def test_dump_leaf_locals_source_only_matches_this_file_lets_through():
    def _inner():
        marker = 42
        raise ValueError("x")
    try:
        _inner()
    except ValueError as e:
        got = dump_leaf_locals(e.__traceback__, source_only=__file__)
    assert got is not None and got["marker"] == "42"


# ─────────────────────────────────────────────────────────────────────────────
# capture_traceback — bounded, contains the exception, truncates cleanly
# ─────────────────────────────────────────────────────────────────────────────


def test_capture_traceback_contains_exception_name_and_message():
    try:
        raise ValueError("the-message-42")
    except ValueError as e:
        text = capture_traceback(e)
    assert "ValueError" in text
    assert "the-message-42" in text


def test_capture_traceback_truncates_when_over_cap(monkeypatch):
    """Force truncation by pinning the cap tiny for this test, so the
    truncation-marker assertion FIRES rather than being skipped. A
    conditional `if 'truncated' in text: assert ...` silently passes when
    truncation didn't happen -- that shape is what M3 in the mutation suite
    exploited to survive."""
    from forge_gate import debug_core
    monkeypatch.setattr(debug_core, "_TB_CAP_BYTES", 200)
    try:
        raise RuntimeError("x" * 500)  # message alone blows past 200
    except RuntimeError as e:
        text = capture_traceback(e)
    assert len(text) <= 200
    # These MUST both hold — no conditional skip
    assert "truncated" in text, f"truncation marker missing: {text!r}"
    assert "bytes elided" in text
