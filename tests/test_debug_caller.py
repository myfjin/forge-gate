"""Test the forge-gate debug caller-layer routing (BRIEF §4, §9 step 2).

What we test here:
  - extract_code() recovers the code from LensResult.reason format, returns
    (None, reason) on missing / malformed / undeclared prefix
  - route() dispatches EACH declared U-code to its own handler and the
    caller_label on the produced DebugEvidence matches the code
  - route() with a no-U-code FAIL raises NotImplementedError (step-3 stub)
  - the surfaced payload for one representative code (U4:capture_failed)
    passes the stderr tail through untouched
"""
from __future__ import annotations

import pytest

from forge_gate.debug_caller import extract_code, route, _HANDLERS
from forge_gate.debug_core import DebugEvidence
from forge_gate.lenses import LensResult
from forge_gate.unsupported import _u, declared_codes


def _lens(name: str, status: str, reason: str) -> LensResult:
    return LensResult(name=name, status=status, reason=reason, objected=(status == "FAIL"))


# ─────────────────────────────────────────────────────────────────────────────
# extract_code
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_code_recovers_each_declared():
    for code in declared_codes():
        summary = _u(code, "some detail here").summary()  # "[code] meaning: some detail here"
        got, rest = extract_code(summary)
        assert got == code, f"failed on {code}: got {got}"
        assert "some detail here" in rest


def test_extract_code_no_prefix_returns_none():
    got, rest = extract_code("plain FAIL reason, no code")
    assert got is None
    assert rest == "plain FAIL reason, no code"


def test_extract_code_undeclared_treated_as_no_code():
    got, rest = extract_code("[U9:invented] not a real code")
    assert got is None
    assert rest == "[U9:invented] not a real code"


def test_extract_code_malformed_bracket_returns_none():
    got, rest = extract_code("[not-a-code] whatever")
    assert got is None


def test_extract_code_none_reason_routes_safe(recwarn):
    """Regression for 6E review Finding 1 (2026-09-05): extract_code(None)
    used to raise TypeError, taking down route() before it could dispatch
    to the real-defect path. LensResult.reason=None is legal (dataclass
    doesn't forbid it) and must degrade to (None, "")."""
    got, rest = extract_code(None)
    assert got is None
    assert rest == ""


def test_route_with_none_reason_reaches_real_defect_path():
    """The router must not crash when a lens returns reason=None -- it
    should route to the no-U-code real-defect path. Because we pass no
    exception, that path returns evidence with traceback_text=None."""
    lens = _lens("determinism", "FAIL", None)  # type: ignore[arg-type]
    ev = route(
        lens,
        source_text="s",
        source_origin="<inline>",
        failing_input=None,
    )
    assert ev.caller_label is None
    assert ev.caller_metadata["path"] == "real_defect"


def test_u1_handler_degrades_on_wrong_shaped_view_coverage():
    """Regression for 6E review Finding 2 (2026-09-05): a wrong-shaped
    extras['view_coverage'] (str/list/int — anything truthy but not a
    dict) used to crash `_surface_u1_no_coverage` with AttributeError.
    Must degrade to declared_inputs=[] instead."""
    reason = _u("U1:no_coverage", "no coverage").summary()
    lens = _lens("metamorphic", "UNSUPPORTED", reason)
    for bad_shape in ("oops", ["a", "b"], 42):
        ev = route(
            lens,
            source_text="s",
            source_origin="<inline>",
            failing_input=None,
            extras={"view_coverage": bad_shape},
        )
        surfaced = ev.caller_metadata["surfaced"]
        assert surfaced["declared_inputs"] == [], f"failed on {bad_shape!r}"


def test_u1_handler_degrades_on_non_iterable_value_at_run_key():
    """Regression for 6E review round-2 Finding 1 (2026-09-07): the round-1
    fix only guarded the container type. A dict whose value at the
    `_run_<lens>` key is non-iterable (int/None/etc.) still crashed
    sorted() with TypeError, taking down route() before it could return.
    Must degrade to declared_inputs=[]."""
    reason = _u("U1:no_coverage", "no coverage").summary()
    lens = _lens("metamorphic", "UNSUPPORTED", reason)
    for bad_value in (42, None, 3.14, object()):
        ev = route(
            lens,
            source_text="s",
            source_origin="<inline>",
            failing_input=None,
            extras={"view_coverage": {"_run_metamorphic": bad_value}},
        )
        surfaced = ev.caller_metadata["surfaced"]
        assert surfaced["declared_inputs"] == [], f"failed on value {bad_value!r}"


# ─────────────────────────────────────────────────────────────────────────────
# route — every declared code routes and labels correctly
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", list(declared_codes()))
def test_route_dispatches_each_declared_code(code):
    reason = _u(code, "detail for this arm").summary()
    lens = _lens("determinism", "UNSUPPORTED", reason)
    ev = route(
        lens,
        source_text="def f(): return 1",
        source_origin="<inline>",
        failing_input={"seed": 7},
    )
    assert isinstance(ev, DebugEvidence)
    assert ev.caller_label == code
    assert ev.caller_metadata["lens_name"] == "determinism"
    assert ev.caller_metadata["lens_status"] == "UNSUPPORTED"
    assert ev.source_text == "def f(): return 1"


def test_handlers_cover_every_declared_code():
    # Drift alarm: if unsupported.py grows a new code and _HANDLERS forgets,
    # the parametrized test above would still pass (KeyError only fires at
    # route()); this makes the discipline explicit.
    assert set(_HANDLERS.keys()) == set(declared_codes()), (
        "debug_caller._HANDLERS out of sync with unsupported._REGISTRY"
    )


# ─────────────────────────────────────────────────────────────────────────────
# U4:capture_failed — surfacing carries stderr tail through
# ─────────────────────────────────────────────────────────────────────────────


def test_u4_coverage_gap_defaults_are_lists_not_none():
    """Regression for 6E review Finding 4 (2026-09-05): the handler's
    `or []` default was untested — dropping it made the absent-key value
    `None` and a consumer iterating the list would crash. This test
    catches that mutation by asserting the surfaced shape."""
    reason = _u("U4:coverage_gap", "some verdict text").summary()
    lens = _lens("guard", "UNSUPPORTED", reason)
    ev = route(
        lens,
        source_text="src",
        source_origin="<inline>",
        failing_input=None,
    )
    surfaced = ev.caller_metadata["surfaced"]
    # Missing keys must degrade to empty LIST, not None. A None here would
    # crash any consumer that iterates the surface (`for x in params_aborted`).
    assert surfaced["params_aborted"] == []
    assert surfaced["unexercisable"] == []
    assert isinstance(surfaced["params_aborted"], list)
    assert isinstance(surfaced["unexercisable"], list)


def test_u4_capture_failed_passes_stderr_tail_through():
    stderr_tail = "Traceback: NameError: undefined name 'x'"
    reason = _u("U4:capture_failed", stderr_tail).summary()
    lens = _lens("determinism", "UNSUPPORTED", reason)
    ev = route(
        lens,
        source_text="src",
        source_origin="/tmp/x.py",
        failing_input={},
        extras={"subprocess_exit": 1},
    )
    surfaced = ev.caller_metadata["surfaced"]
    assert stderr_tail in surfaced["subprocess_stderr_tail"]
    assert surfaced["exit_hint"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# no-U-code FAIL -> step-3 stub
# ─────────────────────────────────────────────────────────────────────────────


def test_no_code_fail_without_exception_returns_evidence():
    """A lens can FAIL without raising — metamorphic RELATION-FAIL, invariant
    bounds violation, twin metric disagreement. Then traceback_text is None
    and intermediate_values falls back to extras['leaf_locals'] if provided."""
    lens = _lens("invariant", "FAIL", "value 3.4 outside bounds [0, 1]")
    ev = route(
        lens,
        source_text="s",
        source_origin="<inline>",
        failing_input=None,
        extras={"leaf_locals": {"observed": "3.4", "lo": "0", "hi": "1"}},
    )
    assert ev.caller_label is None
    assert ev.caller_metadata["path"] == "real_defect"
    assert ev.traceback_text is None
    assert ev.intermediate_values == {"observed": "3.4", "lo": "0", "hi": "1"}


def test_no_code_fail_with_exception_captures_traceback_and_leaf_locals():
    """When the caller passes an exception, traceback is formatted and leaf
    locals are dumped."""
    def _boom():
        x = 42
        d = {"k": "v"}
        raise ValueError("boom")
    try:
        _boom()
    except ValueError as e:
        exc = e
    lens = _lens("determinism", "FAIL", "plain FAIL, no code")
    ev = route(
        lens,
        source_text="dummy",
        source_origin="<inline>",  # forces filter=None -> leaf captured regardless of file
        failing_input=None,
        exception=exc,
    )
    assert "ValueError: boom" in ev.traceback_text
    assert ev.intermediate_values["x"] == "42"
    assert ev.intermediate_values["d"] == "{'k': 'v'}"
    assert ev.caller_label is None
