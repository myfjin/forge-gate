"""Forge-gate caller layer for the debug core. Owns U-code routing.

BRIEF-DEBUG-FEATURE-2026-09-05.md §4 and §9 step 2.

The debug core (``debug_core.py``) is taxonomy-agnostic by design — it never
sees a U-code. Routing on U-codes lives HERE, in the forge-gate layer that
knows about ``LensResult`` and the ``unsupported`` registry. The core imports
this module NEVER; this module imports the core.

Two paths for a FAIL, per §4:

  FAIL without a U-code -> real defect. Full traceback + intermediate-value
      dump + delta-debug -- populated in step 3+ of the brief. The stub here
      raises NotImplementedError so a caller that hits it before step 3
      lands fails loudly rather than returning an empty payload.

  FAIL with a U-code -> code-specific evidence path. The diagnosis is
      already scoped by the code; debug only SURFACES what the code carried,
      it does not re-diagnose. §4's table maps each of the five declared
      codes to what it surfaces.

The U-code inside a ``LensResult`` today lives in the ``reason`` string,
formatted by ``Unsupported.summary()`` as ``"[U<n>:<name>] meaning: detail"``.
We recover the code (and the trailing detail) with a regex whose vocabulary
is ``declared_codes()`` — an undeclared or missing prefix means "no U-code",
which sends the FAIL down the real-defect path.
"""
from __future__ import annotations

import re
from typing import Any

from forge_gate.debug_core import DebugEvidence, capture_traceback, dump_leaf_locals
from forge_gate.lenses import LensResult
from forge_gate.unsupported import declared_codes


# ─────────────────────────────────────────────────────────────────────────────
# Code extraction
# ─────────────────────────────────────────────────────────────────────────────

_CODE_RE = re.compile(r"^\[(U\d+:[a-z_]+)\]\s*(.*)$", re.DOTALL)


def extract_code(reason: str | None) -> tuple[str | None, str]:
    """Pull ``(code, remainder)`` out of a LensResult.reason string.

    Returns ``(None, reason_or_"")`` unchanged when the reason has no
    ``[U<n>:name]`` prefix, or when the prefix is malformed or not in
    ``declared_codes()``. An unknown code is treated as "no code" rather than
    raising — the router then sends the FAIL to the real-defect path, which
    is the safe default.

    A non-str reason (``None``, bytes, anything else) is treated as "no code":
    a ``LensResult`` with ``reason=None`` is not forbidden by the dataclass,
    and crashing here would take down the router instead of routing the FAIL
    to the real-defect path. Fixed 2026-09-05 per 6E review Finding 1.
    """
    if not isinstance(reason, str):
        return None, "" if reason is None else str(reason)
    m = _CODE_RE.match(reason)
    if not m:
        return None, reason
    code = m.group(1)
    if code not in declared_codes():
        return None, reason
    return code, m.group(2)


# ─────────────────────────────────────────────────────────────────────────────
# Per-code handlers — each surfaces what §4 says to surface for its code.
# Handlers take (remainder, lens, extras) and return a partial evidence dict
# that the router merges into DebugEvidence. Handlers NEVER re-run analysis;
# they only reshape what the U-code already carried.
# ─────────────────────────────────────────────────────────────────────────────


def _surface_u1_no_coverage(remainder: str, lens: LensResult, extras: dict) -> dict:
    """§4: which inputs/patterns are missing from the lens's coverage set.

    VIEW_COVERAGE is the authoritative map; extras may carry it pre-computed
    (the caller has cheaper access than importing lenses here would be). If
    missing, we surface the reason text alone -- the code already carries
    enough for a human to act.
    """
    view = extras.get("view_coverage")
    # A wrong-shaped view_coverage (str/list/int — anything but dict) must
    # degrade, not crash. Fixed 2026-09-05 per 6E review Finding 2.
    if not isinstance(view, dict):
        view = {}
    # The value AT the _run_<lens> key must also be defended: a caller who
    # supplies {'_run_metamorphic': 42} (int/None/non-iterable) would crash
    # sorted() with TypeError. Container-guard alone is not enough. Fixed
    # 2026-09-07 per 6E review round-2 Finding 1.
    raw = view.get(f"_run_{lens.name.replace('-', '_')}", [])
    try:
        declared = sorted(raw)
    except TypeError:
        declared = []
    return {
        "missing_from": lens.name,
        "declared_inputs": declared,
        "reason_detail": remainder,
    }


def _surface_u2_contract_unratified(remainder: str, lens: LensResult, extras: dict) -> dict:
    """§4: which contract file, which pattern, ratified: false state.

    The contract path is not in the LensResult; extras may supply it. If
    absent we surface the code's detail alone.
    """
    return {
        "contract_path": extras.get("contract_path"),
        "pattern": extras.get("pattern"),
        "ratified": False,
        "reason_detail": remainder,
    }


def _surface_u3_input_malformed(remainder: str, lens: LensResult, extras: dict) -> dict:
    """§4: which input is absent/malformed. Cause CERTAIN — the disposition
    is FIX or QUARANTINE (per unsupported.py); debug only says WHAT."""
    return {
        "malformed_input": extras.get("input_name"),
        "reason_detail": remainder,
    }


def _surface_u4_capture_failed(remainder: str, lens: LensResult, extras: dict) -> dict:
    """§4: subprocess exit + stderr tail. The reason already carries the
    stderr tail (lenses.py:364 slices the last 200 chars); we pass it
    through untouched."""
    return {
        "subprocess_stderr_tail": remainder,
        "exit_hint": extras.get("subprocess_exit"),
    }


def _surface_u4_coverage_gap(remainder: str, lens: LensResult, extras: dict) -> dict:
    """§4: params_aborted / unexercisable list. The guard-lens carries this
    inside the verdict string it hands back; extras may split it out."""
    return {
        "params_aborted": extras.get("params_aborted") or [],
        "unexercisable": extras.get("unexercisable") or [],
        "reason_detail": remainder,
    }


_HANDLERS = {
    "U1:no_coverage":         _surface_u1_no_coverage,
    "U2:contract_unratified": _surface_u2_contract_unratified,
    "U3:input_malformed":     _surface_u3_input_malformed,
    "U4:capture_failed":      _surface_u4_capture_failed,
    "U4:coverage_gap":        _surface_u4_coverage_gap,
}


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────


def route(
    lens: LensResult,
    *,
    source_text: str,
    source_origin: str,
    failing_input: Any,
    extras: dict[str, Any] | None = None,
    exception: BaseException | None = None,
) -> DebugEvidence:
    """Build a DebugEvidence for one lens's FAIL / UNSUPPORTED verdict.

    Callers pre-capture ``source_text`` via ``debug_core.capture_source``
    BEFORE any ``finally: unlink()`` in the isolation code — see the
    top-of-file trap in debug_core's module docstring.

    ``extras`` is an opaque dict for per-code side channels the LensResult
    does not carry (contract path, params_aborted list, VIEW_COVERAGE map).
    A missing key is fine; every handler treats absence as "not available"
    and surfaces the code's own detail instead of failing.
    """
    extras = extras or {}
    code, remainder = extract_code(lens.reason)

    if code is None:
        return _route_no_code(lens, source_text, source_origin, failing_input, extras, exception)

    handler = _HANDLERS[code]  # KeyError impossible: extract_code filters to declared
    payload = handler(remainder, lens, extras)

    return DebugEvidence(
        source_text=source_text,
        source_origin=source_origin,
        failing_input=failing_input,
        caller_label=code,
        caller_metadata={
            "lens_name":   lens.name,
            "lens_status": lens.status,
            "surfaced":    payload,
        },
    )


def _route_no_code(
    lens: LensResult,
    source_text: str,
    source_origin: str,
    failing_input: Any,
    extras: dict[str, Any],
    exception: BaseException | None,
) -> DebugEvidence:
    """Real-defect path: this is what fires when a lens says FAIL and the
    reason string carries no U-code. Diagnosis is genuinely open -- the
    code under test is the suspect. We surface a traceback (if an exception
    is available) and the leaf-frame locals at the moment the exception
    surfaced.

    ``exception=None`` is legal: some lenses (metamorphic RELATION-FAIL,
    invariant bounds violation, twin metric disagreement) produce a FAIL
    verdict WITHOUT raising anything -- their contract IS the assertion.
    In that case the returned evidence has ``traceback_text=None`` and
    ``intermediate_values`` is only populated if the caller placed a
    pre-computed frame dump in ``extras['leaf_locals']``.

    Leaf-frame locals only, not a full sys.settrace tracer. A tracer is
    subtle to get right and its failure mode is a crash at the moment the
    user needs debug info most -- see the commit body of step 3 for why
    this scope is intentional.
    """
    tb_text: str | None = None
    leaf: dict[str, str] | None = None

    if exception is not None:
        tb_text = capture_traceback(exception)
        # source_only is the path the caller wrote to disk; leaf locals from
        # inside stdlib code would be noise, not evidence.
        filter_path = source_origin if source_origin and source_origin != "<inline>" else None
        leaf = dump_leaf_locals(exception.__traceback__, source_only=filter_path)

    if leaf is None:
        leaf = extras.get("leaf_locals")

    # SHRUNK_INPUT (step 6, ddmin-at-lens): the caller may have run ddmin
    # against the failing input and passed the result under extras. Surface
    # it on the DebugEvidence so a formatter can print "minimal reproducer:
    # N lines" alongside the traceback.
    shrunk = extras.get("shrunk_input")

    # COMPILER_OUTPUT (4Q step-6 review Finding 1, 2026-09-07): the twin
    # lens on C++ compile failure needs its compiler output on the dedicated
    # DebugEvidence.compiler_output field (brief §4.3), not stuffed into
    # intermediate_values. Any caller may pass extras['compiler_output'].
    compiler_out = extras.get("compiler_output")

    return DebugEvidence(
        source_text=source_text,
        source_origin=source_origin,
        failing_input=failing_input,
        traceback_text=tb_text,
        intermediate_values=leaf,
        compiler_output=compiler_out,
        shrunk_input=shrunk,
        caller_label=None,  # explicit: no U-code -> real defect
        caller_metadata={
            "lens_name":   lens.name,
            "lens_status": lens.status,
            "path":        "real_defect",
        },
    )
