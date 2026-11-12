"""Negative-control test for the debug wire-up (step 5, forge-gate 2026-09-07).

BRIEF-DEBUG-FEATURE-2026-09-05.md acceptance criteria §6:
  1. Verdict unchanged with/without debug -- proved by test_verdict_algebra_unchanged
  2. Every FAIL / UNSUPPORTED emits a DebugEvidence artifact -- proved per-lens
  3. Negative control: a deliberately broken pattern produces a debug artifact
     a human can act on -- proved by test_metamorphic_relation_fail_wireup
     with 7 CONTENT asserts (per 6E's Q5 design consultation)
  4. Determinism preserved -- proved by test_wireup_deterministic

Per 6E's design consultation (2026-09-07):
  - metamorphic wired first (Q1)
  - source capture inline, unconditional (Q2)
  - LensResult.debug field, excluded from as_dict() by default (Q4)
  - negative control asserts on evidence CONTENT, not existence (Q5)

Wire-up rides through _run_metamorphic / _run_guard with the metamorphic
runner (or guard subprocess) monkeypatched. The monkeypatch replaces the
runner ONLY; capture_source, route(), and _attach_debug all execute for
real -- so a wire-up defect (missing capture, wrong route, dropped
evidence) surfaces in the asserts."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forge_gate import lenses as L  # noqa: E402
from forge_gate.debug_core import DebugEvidence  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def broken_pattern_file(tmp_path):
    """A pattern file whose SOURCE_TEXT is what capture_source must recover.
    Content is unique so an assert on it kills a mutation that captured the
    wrong file or an empty string."""
    p = tmp_path / "broken_scale_invariance.py"
    p.write_text(
        "# unique-marker-BB77C\n"
        "def predict(model, X, k=1.0):\n"
        "    # BROKEN: does not scale predictions by k\n"
        "    return model.predict(X)\n"
    )
    return p


# ─────────────────────────────────────────────────────────────────────────────
# CRITERION 1 — verdict unchanged with/without debug
# ─────────────────────────────────────────────────────────────────────────────


def test_verdict_algebra_reads_only_status_reason_objected(monkeypatch, broken_pattern_file):
    """A LensResult with `debug` populated vs. `debug=None` must produce the
    SAME GateVerdict. If evaluate() ever reads the debug field, this fails."""
    _stub_meta_fail(monkeypatch, with_debug=True)
    v_with = L.evaluate(
        pattern="scale_invariance", user_file=broken_pattern_file,
        base_status="PASS", base_reason="ok", base_metrics={},
        lenses=list(L.ALL_LENSES),
    )
    _stub_meta_fail(monkeypatch, with_debug=False)
    v_without = L.evaluate(
        pattern="scale_invariance", user_file=broken_pattern_file,
        base_status="PASS", base_reason="ok", base_metrics={},
        lenses=list(L.ALL_LENSES),
    )
    # Verdict fields must be byte-identical
    assert v_with.status == v_without.status
    assert v_with.reason == v_without.reason
    assert [l.name for l in v_with.objectors] == [l.name for l in v_without.objectors]


def test_as_dict_excludes_debug_by_default(broken_pattern_file):
    """Serialization must stay byte-identical with/without debug. If a
    caller wants the artifact serialized, include_debug=True is explicit."""
    src, origin = "src", "<inline>"
    ev = DebugEvidence(source_text=src, source_origin=origin, failing_input=None)
    r_no_debug = L.LensResult("metamorphic", "FAIL", "boom", objected=True)
    r_with_debug = L.LensResult("metamorphic", "FAIL", "boom", objected=True, debug=ev)
    assert r_no_debug.as_dict() == r_with_debug.as_dict()
    # Only include_debug=True adds the field.
    with_kw = r_with_debug.as_dict(include_debug=True)
    assert "debug" in with_kw
    assert with_kw["debug"]["source_text"] == "src"


# ─────────────────────────────────────────────────────────────────────────────
# CRITERION 3 — negative control: metamorphic RELATION-FAIL wire-up
# The 7 CONTENT asserts per 6E's Q5.
# ─────────────────────────────────────────────────────────────────────────────


def _stub_meta_fail(monkeypatch, *, with_debug=True):
    """Monkeypatch the metamorphic runner to return RELATION-FAIL. Capture,
    route, and _attach_debug execute for REAL -- so a wire-up defect shows
    up in the content asserts."""
    if not with_debug:
        # Simulate "wire-up removed" by stubbing _attach_debug to a no-op.
        monkeypatch.setattr(L, "_attach_debug",
                            lambda result, *a, **kw: result)

    fake_result = SimpleNamespace(
        name="scale_invariance",
        passed=False,
        reason="predictions did not scale by k (expected 10.0, got 1.0)",
        source_input={"X": [[1.0]], "k": 10.0},
        follow_up_input={"X": [[10.0]], "k": 10.0},
    )

    def fake_run(user_file, pattern):
        return ("RELATION-FAIL", [fake_result],
                "gating relation scale_invariance failed")

    # The metamorphic runner is imported *inside* _run_metamorphic via
    # _ToolsPath. Monkeypatch by injecting a module.
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = fake_run
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    rel = _t.ModuleType("metamorphic.relations")
    rel.is_gating = lambda name: True
    monkeypatch.setitem(sys.modules, "metamorphic.relations", rel)
    # Also register the parent package so `from metamorphic.runner import ...` works.
    pkg = _t.ModuleType("metamorphic")
    monkeypatch.setitem(sys.modules, "metamorphic", pkg)


def test_metamorphic_relation_fail_wireup_populates_seven_evidence_fields(
    monkeypatch, broken_pattern_file
):
    """The 7-assert content check per 6E Q5: fields that can only be present
    if capture_source ran BEFORE the runner and route() populated the
    artifact from the real result. A wire-up defect (missing capture, empty
    source, wrong route) surfaces in the asserts."""
    _stub_meta_fail(monkeypatch, with_debug=True)
    result = L._run_metamorphic(broken_pattern_file, "scale_invariance")

    # 1. wire-up attached the artifact
    assert result.debug is not None, "wire-up did not populate result.debug"
    ev = result.debug

    # 2. no-code real-defect path (metamorphic FAIL carries no U-code)
    assert ev.caller_label is None, (
        f"expected no-code path, got caller_label={ev.caller_label!r}"
    )
    assert ev.caller_metadata.get("path") == "real_defect"

    # 3. source_text captured — equals file content (kills a mutation that
    #    passed an empty string or captured the wrong file)
    assert ev.source_text, "source_text is empty — capture did not run"
    assert "unique-marker-BB77C" in ev.source_text
    assert ev.source_text == broken_pattern_file.read_text()

    # 4. source_origin is the resolved real path — not "<inline>" fallback
    assert ev.source_origin != "<inline>"
    assert ev.source_origin.endswith("broken_scale_invariance.py")

    # 5. failing_input populated (the input the runner ran against)
    assert ev.failing_input is not None
    assert str(broken_pattern_file) in str(ev.failing_input)

    # 6. intermediate_values names WHICH relation broke and its inputs --
    #    the "human can act on" content
    iv = ev.intermediate_values or {}
    assert any("scale_invariance" in k for k in iv.keys()), (
        f"intermediate_values does not name the failing relation: {iv}"
    )

    # 7. verdict on the LensResult still exactly what the runner returned
    #    (debug is off-path — verdict must be untouched)
    assert result.status == "FAIL"
    assert result.objected is True


def test_wireup_deterministic(monkeypatch, broken_pattern_file):
    """Criterion 4: two runs on the same broken fixture produce evidence
    with the same source_text and same intermediate_values keys."""
    _stub_meta_fail(monkeypatch, with_debug=True)
    r1 = L._run_metamorphic(broken_pattern_file, "scale_invariance")
    _stub_meta_fail(monkeypatch, with_debug=True)
    r2 = L._run_metamorphic(broken_pattern_file, "scale_invariance")
    assert r1.debug is not None and r2.debug is not None
    assert r1.debug.source_text == r2.debug.source_text
    assert set((r1.debug.intermediate_values or {}).keys()) == \
           set((r2.debug.intermediate_values or {}).keys())


# ─────────────────────────────────────────────────────────────────────────────
# UNSUPPORTED path — U-code routes correctly
# ─────────────────────────────────────────────────────────────────────────────


def test_metamorphic_unsupported_routes_through_u1(monkeypatch, broken_pattern_file):
    """UNSUPPORTED must attach a U1:no_coverage caller_label (via route()),
    proving the U-code path of the wire-up (not the no-code path)."""
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = lambda uf, p: ("UNSUPPORTED", [], "no coverage for this pattern")
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    rel = _t.ModuleType("metamorphic.relations")
    rel.is_gating = lambda name: True
    monkeypatch.setitem(sys.modules, "metamorphic.relations", rel)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    result = L._run_metamorphic(broken_pattern_file, "scale_invariance")
    assert result.status == "UNSUPPORTED"
    assert result.debug is not None
    assert result.debug.caller_label == "U1:no_coverage"


def test_metamorphic_pass_does_not_populate_debug(monkeypatch, broken_pattern_file):
    """PASS verdict must leave debug=None. Off-path discipline: an artifact
    on PASS would burn memory and confuse a formatter."""
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = lambda uf, p: ("OK", [], "all relations held")
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    rel = _t.ModuleType("metamorphic.relations")
    rel.is_gating = lambda name: True
    monkeypatch.setitem(sys.modules, "metamorphic.relations", rel)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    result = L._run_metamorphic(broken_pattern_file, "scale_invariance")
    assert result.status == "PASS"
    assert result.debug is None


# ─────────────────────────────────────────────────────────────────────────────
# guard lens — fast-follow wire-up
# ─────────────────────────────────────────────────────────────────────────────


def test_guard_fail_wireup_populates_evidence(monkeypatch, broken_pattern_file):
    """Fast-follow: guard lens FAIL also attaches DebugEvidence with the
    same content-assert shape (adapted for guard's semantics)."""
    class _FakeProc:
        returncode = 0
        stdout = "verdict: FAIL: NaN defect in x > 0 branch\ndetail: input=NaN"
        stderr = ""
    def fake_run(*a, **kw):
        return _FakeProc()
    monkeypatch.setattr("subprocess.run", fake_run)

    result = L._run_guard(broken_pattern_file, timeout=5.0)
    assert result.status == "FAIL"
    assert result.debug is not None
    ev = result.debug
    assert ev.source_text
    assert "unique-marker-BB77C" in ev.source_text
    assert ev.source_origin.endswith("broken_scale_invariance.py")
    assert ev.caller_label is None  # guard FAIL has no U-code -> real-defect
    assert ev.caller_metadata.get("path") == "real_defect"


def test_guard_unsupported_routes_through_u4_coverage_gap(monkeypatch, broken_pattern_file):
    class _FakeProc:
        returncode = 0
        stdout = "verdict: INCONCLUSIVE: no scalar params to probe"
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeProc())

    result = L._run_guard(broken_pattern_file, timeout=5.0)
    assert result.status == "UNSUPPORTED"
    assert result.debug is not None
    assert result.debug.caller_label == "U4:coverage_gap"


def test_guard_pass_does_not_populate_debug(monkeypatch, broken_pattern_file):
    class _FakeProc:
        returncode = 0
        stdout = "verdict: PASS: no NaN defect"
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeProc())

    result = L._run_guard(broken_pattern_file, timeout=5.0)
    assert result.status == "PASS"
    assert result.debug is None


# ─────────────────────────────────────────────────────────────────────────────
# 6E step-5 review findings — regression tests (2026-09-07)
# ─────────────────────────────────────────────────────────────────────────────


def test_attach_debug_does_not_swallow_base_exception(monkeypatch, broken_pattern_file):
    """6E step-5 Finding 1: `_attach_debug`'s except is scoped to Exception
    so KeyboardInterrupt / SystemExit propagate. Widening to BaseException
    would silently eat user Ctrl-C during the wire-up. Assert the current
    scope by making debug_caller.route raise KeyboardInterrupt and confirming
    it propagates instead of being swallowed to debug=None."""
    class _Boom(KeyboardInterrupt):
        pass

    def raise_boom(*a, **kw):
        raise _Boom()

    # debug_caller is lazy-imported inside _attach_debug; register a stub.
    import types as _t
    dc = _t.ModuleType("forge_gate.debug_caller")
    dc.route = raise_boom
    monkeypatch.setitem(sys.modules, "forge_gate.debug_caller", dc)

    result = L.LensResult("guard", "FAIL", "boom", objected=True)
    with pytest.raises(KeyboardInterrupt):
        L._attach_debug(result, "src", "<inline>", broken_pattern_file)


def test_attach_debug_early_returns_on_error_status(broken_pattern_file):
    """6E step-5 Finding 2: the `if result.status not in ("FAIL","UNSUPPORTED")`
    guard is load-bearing for the ERROR case — populate-only-on-FAIL/UNSUPPORTED
    is the design invariant (Q4). Assert ERROR results pass through with
    debug=None; a removed guard would let ERROR grow a real-defect artifact."""
    err = L.LensResult("metamorphic", "ERROR", "harness broke")
    ret = L._attach_debug(err, "src", "<inline>", broken_pattern_file)
    assert ret is err
    assert ret.debug is None
    # And a PASS: same behavior, no evidence.
    ok = L.LensResult("metamorphic", "PASS", "ok")
    ret2 = L._attach_debug(ok, "src", "<inline>", broken_pattern_file)
    assert ret2.debug is None


def test_metamorphic_runner_exception_returns_error_with_no_debug(monkeypatch, broken_pattern_file):
    """The runner raising becomes an ERROR verdict with debug=None (design:
    ERROR is 'harness broke', not a code fault, so no evidence artifact).
    Regression: earlier step-5 code passed `exc=exc_caught` to _attach_debug
    on this path, which was dead work — 6E design-note 2026-09-07."""
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    def raiser(*a, **kw):
        raise RuntimeError("runner exploded")
    mod.run_metamorphic = raiser
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    result = L._run_metamorphic(broken_pattern_file, "any_pattern")
    assert result.status == "ERROR"
    assert result.debug is None
    assert "RuntimeError" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 -- invariant / twin / determinism wire-up (mechanical port)
# ─────────────────────────────────────────────────────────────────────────────


def _stub_invariant(monkeypatch, *, status, reason):
    import types as _t
    mod = _t.ModuleType("invariant_gate")
    mod.verify = lambda uf, p: (status, reason)
    monkeypatch.setitem(sys.modules, "invariant_gate", mod)


def test_invariant_fail_populates_debug(monkeypatch, broken_pattern_file):
    """Regression for 4Q step-6 Finding 2 (2026-09-07): the invariant FAIL
    wire-up passes `{"invariant_reason": reason}` as leaf_locals. Removing
    those extras used to survive because the test did not assert on
    intermediate_values. Assert both the shape AND the content."""
    _stub_invariant(monkeypatch, status="FAIL", reason="value 3.4 outside bounds [0,1]")
    r = L._run_invariant(broken_pattern_file, "some_pattern")
    assert r.status == "FAIL"
    assert r.debug is not None
    assert r.debug.caller_label is None  # real-defect path
    assert r.debug.source_text and "unique-marker-BB77C" in r.debug.source_text
    iv = r.debug.intermediate_values or {}
    assert iv.get("invariant_reason") == "value 3.4 outside bounds [0,1]"


def test_invariant_unsupported_ratified_routes_u2(monkeypatch, broken_pattern_file):
    _stub_invariant(monkeypatch, status="UNSUPPORTED", reason="contract not yet ratified")
    r = L._run_invariant(broken_pattern_file, "p")
    assert r.status == "UNSUPPORTED"
    assert r.debug is not None
    assert r.debug.caller_label == "U2:contract_unratified"


def test_invariant_unsupported_no_coverage_routes_u1(monkeypatch, broken_pattern_file):
    _stub_invariant(monkeypatch, status="UNSUPPORTED", reason="no invariant for pattern x")
    r = L._run_invariant(broken_pattern_file, "p")
    assert r.debug is not None
    assert r.debug.caller_label == "U1:no_coverage"


def test_invariant_pass_no_debug(monkeypatch, broken_pattern_file):
    _stub_invariant(monkeypatch, status="PASS", reason="ok")
    r = L._run_invariant(broken_pattern_file, "p")
    assert r.status == "PASS"
    assert r.debug is None


def _stub_twin(monkeypatch, *, status, reason, details=()):
    import types as _t
    mod = _t.ModuleType("twin_consensus")
    class _TC:
        pass
    tc = _TC()
    tc.status = status
    tc.reason = reason
    tc.details = list(details)
    mod.consensus_verify = lambda uf, p, timeout: tc
    monkeypatch.setitem(sys.modules, "twin_consensus", mod)


def test_twin_fail_populates_debug(monkeypatch, broken_pattern_file):
    """Regression for 6E step-6 Finding 4 (2026-09-07): §4.3 requires metric
    each twin produced AND the tolerance that was exceeded. Assert both."""
    _stub_twin_with_metrics(monkeypatch, status="FAIL",
                            reason="metric divergence 0.4 > 1e-3",
                            details=["py/foo -> FAIL", "cpp/foo -> FAIL"],
                            metrics={"intra": 0.9, "inter": 0.5},
                            tolerance=1e-3)
    r = L._run_twin(broken_pattern_file, "p", timeout=5.0)
    assert r.status == "FAIL"
    assert r.debug is not None
    assert r.debug.caller_label is None
    iv = r.debug.intermediate_values or {}
    assert any("twin.detail" in k for k in iv.keys())
    # F4: metrics + tolerance must surface — a mutation removing either
    # would leave one of these keys absent.
    assert iv.get("twin.metric.intra") is not None
    assert iv.get("twin.metric.inter") is not None
    assert iv.get("twin.tolerance") is not None


def _stub_twin_with_metrics(monkeypatch, *, status, reason, details, metrics, tolerance):
    import types as _t
    class _TC:
        pass
    tc = _TC()
    tc.status = status
    tc.reason = reason
    tc.details = list(details)
    tc.metrics = dict(metrics)
    tc.tolerance = tolerance
    mod = _t.ModuleType("twin_consensus")
    mod.consensus_verify = lambda uf, p, timeout: tc
    monkeypatch.setitem(sys.modules, "twin_consensus", mod)


def test_twin_unstable_populates_debug(monkeypatch, broken_pattern_file):
    """Regression for 4Q step-6 Finding 1 (2026-09-07): brief §4.3 wants
    compiler_output on the dedicated DebugEvidence.compiler_output field
    for C++ compile failures (UNSTABLE), not stuffed into
    intermediate_values. Assert the field is populated when the twin
    detail names a compile failure, AND assert objected=True since twin
    UNSTABLE gates the verdict."""
    _stub_twin(monkeypatch, status="UNSTABLE", reason="cpp compile failed",
               details=["compiler: undefined reference to `predict`",
                        "py/foo -> compile-failed"])
    r = L._run_twin(broken_pattern_file, "p", timeout=5.0)
    assert r.status == "UNSTABLE"
    assert r.objected is True  # step-6 4Q side-note: UNSTABLE gates
    assert r.debug is not None
    assert r.debug.compiler_output is not None
    assert "compiler" in r.debug.compiler_output.lower()


def test_twin_unstable_uses_compiler_output_when_stub_provides_it(monkeypatch, broken_pattern_file):
    """When tc.compiler_output is explicit, use it directly (do not derive
    from detail lines)."""
    import types as _t
    class _TC: pass
    tc = _TC()
    tc.status = "UNSTABLE"; tc.reason = "cpp build failed"
    tc.details = []; tc.compiler_output = "error: 'foo' was not declared"
    mod = _t.ModuleType("twin_consensus")
    mod.consensus_verify = lambda uf, p, timeout: tc
    monkeypatch.setitem(sys.modules, "twin_consensus", mod)
    r = L._run_twin(broken_pattern_file, "p", timeout=5.0)
    assert r.debug is not None
    assert r.debug.compiler_output == "error: 'foo' was not declared"


def test_twin_unsupported_routes_u1(monkeypatch, broken_pattern_file):
    _stub_twin(monkeypatch, status="UNSUPPORTED", reason="no twin for this pattern")
    r = L._run_twin(broken_pattern_file, "p", timeout=5.0)
    assert r.debug is not None
    assert r.debug.caller_label == "U1:no_coverage"


def test_twin_pass_no_debug(monkeypatch, broken_pattern_file):
    _stub_twin(monkeypatch, status="PASS", reason="metrics match", details=["py=cpp"])
    r = L._run_twin(broken_pattern_file, "p", timeout=5.0)
    assert r.debug is None


def test_determinism_fail_populates_debug(monkeypatch, broken_pattern_file):
    """Determinism wraps _run_determinism_once with retry; monkeypatch the
    inner function so we can test the wire-up without spawning subprocesses.
    Regression for 6E step-6 Finding 2 + 5 (2026-09-07): assert intermediate_values
    surfaces determinism_reason AND the tolerance/n_runs (§4.4 spec)."""
    reason = "non-deterministic output at run 0 vs run 2 (tolerance 1e-12): centroid[0]: 3.83 vs 5.06"
    monkeypatch.setattr(
        L, "_run_determinism_once",
        lambda uf, p, t: L.LensResult("determinism", "FAIL", reason, objected=True),
    )
    r = L._run_determinism(broken_pattern_file, "p", timeout=5.0)
    assert r.status == "FAIL"
    assert r.debug is not None
    assert r.debug.caller_label is None
    assert "unique-marker-BB77C" in r.debug.source_text
    iv = r.debug.intermediate_values or {}
    # F2: the leaf_locals extras must actually surface — a mutation that
    # drops them would leave intermediate_values empty.
    assert iv.get("determinism_reason") == reason
    # F5: §4.4 requires tolerance AND run-pair. Tolerance surfaces via
    # extras key; run pair is named inside the reason string.
    assert iv.get("determinism_tolerance") == "1e-12"
    assert iv.get("determinism_n_runs") == "3"
    assert "run 0 vs run 2" in iv["determinism_reason"]


def test_determinism_unsupported_routes_u1(monkeypatch, broken_pattern_file):
    import forge_gate.unsupported as U
    monkeypatch.setattr(
        L, "_run_determinism_once",
        lambda uf, p, t: L.LensResult("determinism", "UNSUPPORTED",
                                       U._u("U1:no_coverage", "no fixture").summary()),
    )
    r = L._run_determinism(broken_pattern_file, "p", timeout=5.0)
    assert r.debug is not None
    assert r.debug.caller_label == "U1:no_coverage"


def test_determinism_error_no_debug(monkeypatch, broken_pattern_file):
    """All 3 retries return ERROR -> final ERROR, debug=None."""
    monkeypatch.setattr(
        L, "_run_determinism_once",
        lambda uf, p, t: L.LensResult("determinism", "ERROR", "harness broke"),
    )
    r = L._run_determinism(broken_pattern_file, "p", timeout=5.0)
    assert r.status == "ERROR"
    assert r.debug is None


# ─────────────────────────────────────────────────────────────────────────────
# DDMIN AT LENS LAYER (step 6, metamorphic source-line shrinking)
# ─────────────────────────────────────────────────────────────────────────────


def test_shrink_metamorphic_source_below_length_floor_skips(monkeypatch, broken_pattern_file):
    """Length floor: source with < 8 lines returns None (skip ddmin).
    Regression for 6E step-6 Finding 1 (2026-09-07): the previous version
    of this test did not stub the runner, so ddmin's predicate raised on
    the missing import, `predicate_ever_failed` stayed False, and the test
    passed even with the length floor removed. Fix: stub the runner to
    return RELATION-FAIL, so a mutation that removed the floor would let
    ddmin run and (with the reproducing predicate) actually shrink the
    input -- our assertion of `ret is None` then kills the mutant."""
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    # If the floor is removed, ddmin runs with a reproducing predicate
    # (RELATION-FAIL on any subset), converges below the floor, and
    # returns a non-None result. Only the floor keeps ret=None here.
    mod.run_metamorphic = lambda uf, p: ("RELATION-FAIL", [], "")
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))
    ret = L._shrink_metamorphic_source(broken_pattern_file, "p", "def f():\n    pass\n")
    assert ret is None


def test_shrink_metamorphic_source_shrinks_when_predicate_reproduces(
    monkeypatch, tmp_path
):
    """Long source + predicate that reproduces on any subset containing a
    marker line -> ddmin converges on {marker line}."""
    long_src = "\n".join(f"# noise line {i}" for i in range(30))
    long_src = long_src + "\nBREAK_MARKER_XYZ\n" + "\n".join(f"# tail {i}" for i in range(20))
    fake_file = tmp_path / "long.py"
    fake_file.write_text(long_src)

    import types as _t
    def fake_run(uf, p):
        text = Path(uf).read_text()
        v = "RELATION-FAIL" if "BREAK_MARKER_XYZ" in text else "OK"
        return (v, [], "")
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = fake_run
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    ret = L._shrink_metamorphic_source(fake_file, "p", long_src)
    assert ret is not None
    assert "BREAK_MARKER_XYZ" in ret["minimal_source"]
    assert ret["minimal_lines"] < ret["original_lines"]
    assert ret["steps"] > 0


def test_shrink_cleans_up_tmp_files(monkeypatch, tmp_path):
    """Regression for 6E step-6 Finding 3 (2026-09-07): each ddmin
    predicate call writes a NamedTemporaryFile(delete=False) and unlinks
    in `finally`. If the unlink is dropped, tmp files leak. Assert the
    system tempdir has no leaked files matching our pattern after a
    shrink run."""
    import glob
    import os
    import tempfile

    long_src = "\n".join(f"# noise {i}" for i in range(30)) + "\nBREAK_MARKER_LEAK\n"
    fake = tmp_path / "long.py"
    fake.write_text(long_src)

    tmp_root = tempfile.gettempdir()
    before = set(glob.glob(os.path.join(tmp_root, "tmp*.py")))

    import types as _t
    def fake_run(uf, p):
        # Confirm the tmp file exists during the predicate call
        assert os.path.exists(uf)
        text = Path(uf).read_text()
        return ("RELATION-FAIL" if "BREAK_MARKER_LEAK" in text else "OK", [], "")
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = fake_run
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    ret = L._shrink_metamorphic_source(fake, "p", long_src)
    assert ret is not None  # sanity: shrink ran

    after = set(glob.glob(os.path.join(tmp_root, "tmp*.py")))
    leaked = after - before
    assert leaked == set(), f"shrink leaked tmp files: {leaked}"


def test_shrink_never_reproduces_returns_none(monkeypatch, tmp_path):
    long_src = "\n".join(f"line {i}" for i in range(30))
    fake = tmp_path / "long.py"
    fake.write_text(long_src)
    import types as _t
    mod = _t.ModuleType("metamorphic.runner")
    mod.run_metamorphic = lambda uf, p: ("OK", [], "")
    monkeypatch.setitem(sys.modules, "metamorphic.runner", mod)
    monkeypatch.setitem(sys.modules, "metamorphic", _t.ModuleType("metamorphic"))

    ret = L._shrink_metamorphic_source(fake, "p", long_src)
    assert ret is None  # predicate never confirmed FAIL


# ─────────────────────────────────────────────────────────────────────────────
# CRITERION 1 STRONG PROOF — frozen-fixture verdict byte-identity
# ─────────────────────────────────────────────────────────────────────────────


def test_gate_verdict_byte_identical_with_and_without_debug_wireup(monkeypatch, broken_pattern_file):
    """Stronger than the status/reason/objected check: the FULL
    GateVerdict.as_dict() serialization must be byte-identical whether the
    debug wire-up ran or not. Frozen-fixture proof for criterion 1."""
    import json

    def build_verdict():
        return L.evaluate(
            pattern="scale_invariance", user_file=broken_pattern_file,
            base_status="PASS", base_reason="ok", base_metrics={"m": 1},
            lenses=list(L.ALL_LENSES),
        )

    _stub_meta_fail(monkeypatch, with_debug=True)
    v1 = json.dumps(build_verdict().as_dict(), sort_keys=True)

    _stub_meta_fail(monkeypatch, with_debug=False)
    v2 = json.dumps(build_verdict().as_dict(), sort_keys=True)

    assert v1 == v2, (
        f"criterion-1 violation: GateVerdict.as_dict() differs "
        f"with/without debug wire-up.\nWITH:    {v1}\nWITHOUT: {v2}"
    )


def test_guard_error_paths_have_no_debug(monkeypatch, broken_pattern_file):
    """Guard timeout / spawn-fail / rc!=0 all return ERROR with debug=None."""
    import subprocess
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr("subprocess.run", timeout)
    r1 = L._run_guard(broken_pattern_file, timeout=1.0)
    assert r1.status == "ERROR"
    assert r1.debug is None

    def spawnfail(*a, **kw):
        raise OSError("no exec")
    monkeypatch.setattr("subprocess.run", spawnfail)
    r2 = L._run_guard(broken_pattern_file, timeout=1.0)
    assert r2.status == "ERROR"
    assert r2.debug is None

    class _BadRc:
        returncode = 2
        stdout = ""
        stderr = "boom"
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _BadRc())
    r3 = L._run_guard(broken_pattern_file, timeout=1.0)
    assert r3.status == "ERROR"
    assert r3.debug is None
