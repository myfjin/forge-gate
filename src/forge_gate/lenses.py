"""KEYSTONE: run every lens and reduce them to ONE verdict.

Every customer-facing skin (CI action, MCP server, attestation) must import this
module rather than re-implement the algebra, so a verdict means the same thing
everywhere.

THE VERDICT ALGEBRA
    FAIL   if property-primary fails, OR any enforced lens OBJECTS
    PASS   only if property-primary passes and no enforced lens objects
    UNSTABLE if a lens errored and nothing outright failed
    UNSUPPORTED lenses never count for or against

An objection is a lens actively asserting the code is wrong:
    metamorphic     RELATION-FAIL  (gating relations only -- see ADVISORY_RELATIONS)
    invariant       FAIL
    twin-consensus  FAIL or UNSTABLE (cross-language divergence)

Every verdict names which lens objected and why. No LLM anywhere in this path.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge_gate.debug_core import DebugEvidence, capture_source
from forge_gate.unsupported import _u

from forge_gate.config import NotConfigured, home as _home, where as _where  # noqa: E402
from forge_gate.config import scratch_dir as _scratch_dir  # noqa: E402

# The folder that holds the forge_gate package: src/ in a checkout, site-packages in an
# install. Child scripts put it on sys.path; it is right in both shapes.
_PKG_PARENT = Path(__file__).resolve().parents[1]
# tools/ (metamorphic, invariant, twin, guard) is NOT in the installed package: it lives
# in a source checkout, found through config.home() (FORGE_GATE_HOME or the checkout this
# file sits in). KNOWN-DEBT KD-2: package tools/ so an install alone runs every lens.
REPO_ROOT = _home() or Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"


def _tools_hint() -> str:
    """'' when tools/ is present; else the key-naming reason, appended to a lens ERROR."""
    try:
        _require_tools()
        return ""
    except NotConfigured as exc:
        return f" ({exc})"


def _require_tools() -> None:
    if not (TOOLS_DIR / "guard_lens.py").is_file():
        raise NotConfigured(
            f"lens tools not found at {TOOLS_DIR}: this lens runs from a source checkout; "
            f"set FORGE_GATE_HOME to one ({_where()})"
        )

BASE_LENS = "property-primary"
SUPPLEMENTARY_LENSES = ("metamorphic", "invariant", "twin-consensus")
# NOT "advisory": determinism is in ENFORCED_LENSES and CAN turn PASS -> FAIL. It is
# enforced with a per-pattern quarantine (DETERMINISM_ADVISORY_PATTERNS) rather than a
# global disarm. Was labelled ADVISORY_LENSES until 2026-09-05 -- a stale name that had
# already misled a reader; same shape as guard_lens.py:4 pre-campaign.
PER_PATTERN_GATED_LENSES = ("determinism",)
ENFORCED_EXTRA_LENSES = ("guard",)
ALL_LENSES = (BASE_LENS,) + SUPPLEMENTARY_LENSES + PER_PATTERN_GATED_LENSES + ENFORCED_EXTRA_LENSES
# Default is EVERY lens. The gate should hand a user its strongest verdict unless they
# deliberately ask for less. Measured 2026-07-24: all-lens costs ~0.8s more per file
# (0.14s -> 0.93s) and scores 100/100 on the corpus with zero false positives.
DEFAULT_LENSES = ALL_LENSES
# Guard and determinism lenses are ENFORCED: they can turn PASS -> FAIL.
# Relation soundness law: a lens may only gate where the canonical oracle survives it.
# Determinism has a per-pattern quarantine below for kmeans_clustering (honestly
# non-deterministic oracle). The guard lens survived the canonical-oracle bar at
# commit 876b4863cd3644ea191c72c6df7fee99: 0 of 10 canonical oracles object.
#
# A lens is enforced (can turn PASS -> FAIL) only if it survives the relation soundness
# gate: the canonical oracle must itself satisfy it.
ENFORCED_LENSES: set = set(SUPPLEMENTARY_LENSES) | {"determinism", "guard"}

# PER-PATTERN QUARANTINE for the determinism lens.
#
# The RELATION SOUNDNESS LAW says a check may only GATE where the canonical oracle
# itself survives it. That is a per-pattern fact, not a global one -- so quarantine the
# specific offender instead of disarming the whole lens (the same shape as
# ADVISORY_RELATIONS in tools/metamorphic/relations.py).
#
# MEASURED 2026-08-01, reproduced across two clean runs:
#   kmeans_clustering canonical oracle -> determinism FAIL
#     centroid [0][0][0]: 3.831617640818665 vs 5.065488147854177
#   Not floating-point noise -- a different cluster centre. k-means reaches a LOCAL
#   optimum fixed by `random.sample(range(n_samples), k)` initialisation, so varying the
#   global RNG lands it in a different (legitimately valid) basin. The oracle is honestly
#   non-deterministic; the lens is right and must not gate it.
#   4Q called this correctly in commit 390156a. An earlier note in this file claiming the
#   opposite was WRONG: it read a verdict line of "PASS" produced while the lens was
#   advisory -- an advisory lens cannot change a verdict, so that PASS said nothing about
#   what the lens found. No objection recorded is not the same as no problem found.
#
# The other 9 oracles pass the lens and it GATES for them.
DETERMINISM_ADVISORY_PATTERNS = frozenset({"kmeans_clustering"})

# VIEW_COVERAGE — which inputs each lens adapter consumes.
#
# Enforced by view_coverage.check_all() in the self-test (tests/test_view_coverage.py).
# The walker verifies BOTH directions: no undeclared reads (an input the adapter
# reads but does not declare) AND no declared-but-unread inputs (an input the
# adapter declares but does not evidence in its body). A mismatch fails the build.
#
# Conceptual inputs (canonical_oracle, twin) are consumed inside the tool, not
# the adapter. The walker detects them via _TOOL_INPUTS in view_coverage.py — a
# call-name → input mapping, not a tool walk. Tool-internal drift is scoped out
# for v1 (6E 3c).
VIEW_COVERAGE = {
    "_run_metamorphic":  frozenset({"user_file", "pattern"}),
    "_run_invariant":    frozenset({"user_file", "pattern"}),
    "_run_twin":         frozenset({"user_file", "pattern", "canonical_oracle", "twin", "timeout"}),
    "_run_determinism":  frozenset({"user_file", "pattern", "canonical_oracle", "timeout"}),
    "_run_guard":        frozenset({"user_file", "timeout"}),
}


def determinism_gates_for(pattern: str) -> bool:
    """True if the determinism lens may turn a PASS into a FAIL for this pattern."""
    return "determinism" in ENFORCED_LENSES and pattern not in DETERMINISM_ADVISORY_PATTERNS


@dataclass
class LensResult:
    name: str
    status: str
    reason: str
    objected: bool = False
    detail: List[str] = field(default_factory=list)
    # DEBUG WIRE-UP (step 5, 2026-09-07). Off-path: populated only on
    # FAIL/UNSUPPORTED by the lens adapter, None on PASS/ERROR. The verdict
    # algebra never reads this field (grep evaluate() — it looks at status,
    # reason, objected only), so acceptance criterion 1 ("verdict unchanged
    # with/without debug") holds by construction. Excluded from as_dict() by
    # default so serialization stays byte-identical; pass include_debug=True
    # when a caller explicitly wants the artifact serialized. Per 6E round-2
    # design consultation: "debug is off-path, never in the verdict."
    debug: Optional["DebugEvidence"] = None

    def as_dict(self, *, include_debug: bool = False) -> Dict[str, Any]:
        d = {"lens": self.name, "status": self.status, "reason": self.reason,
             "objected": self.objected}
        if self.detail:
            d["detail"] = self.detail
        if include_debug and self.debug is not None:
            d["debug"] = self.debug.to_dict()
        return d


@dataclass
class GateVerdict:
    status: str
    pattern: str
    user_file: str
    reason: str
    lenses: List[LensResult]
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def objectors(self) -> List[LensResult]:
        return [l for l in self.lenses if l.objected]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "pattern": self.pattern,
            "file": self.user_file,
            "reason": self.reason,
            "objected_by": [l.name for l in self.objectors],
            "metrics": self.metrics,
            "lenses": [l.as_dict() for l in self.lenses],
        }


class _ToolsPath:
    """tools/ on sys.path only while a lens runs."""
    def __enter__(self):
        # Never raises: evaluate() does not guard each lens, so a missing tools/ must
        # surface as that lens's ERROR (with _tools_hint), not crash the whole verdict.
        sys.path.insert(0, str(TOOLS_DIR))
        return self

    def __exit__(self, *exc):
        try:
            sys.path.remove(str(TOOLS_DIR))
        except ValueError:
            pass
        return False


def _run_metamorphic(user_file: Path, pattern: str) -> LensResult:
    # DEBUG WIRE-UP (step 5): capture source UNCONDITIONALLY at the top,
    # before any subprocess. Per 6E: the ordering constraint is load-bearing,
    # and capturing always (discarding on PASS) is cheaper than a conditional
    # that might capture too late. capture_source raising is not fatal to
    # verdict — degrade to empty source, verdict path continues.
    try:
        src_text, src_origin = capture_source(user_file)
    except Exception:
        src_text, src_origin = "", str(user_file)

    with _ToolsPath():
        try:
            from metamorphic.runner import run_metamorphic
            verdict, results, summary = run_metamorphic(user_file, pattern)
        except Exception as exc:
            # ERROR intentionally leaves debug=None per Q4 design (populate
            # only on FAIL/UNSUPPORTED). ERROR is "harness broke", not a
            # code fault -- no evidence artifact to surface. 6E step-5
            # design-note 2026-09-07: don't pass extras that never surface.
            return LensResult("metamorphic", "ERROR", f"{type(exc).__name__}: {exc}{_tools_hint()}")
    detail = []
    try:
        with _ToolsPath():
            from metamorphic.relations import is_gating
        for r in results or []:
            if not r.passed:
                mark = "FAIL" if is_gating(r.name) else "ADVISORY"
                detail.append(f"{r.name}: {mark} -- {r.reason}")
    except Exception:
        pass
    if verdict == "RELATION-FAIL":
        result = LensResult("metamorphic", "FAIL", summary, objected=True, detail=detail)
        # DDMIN AT LENS LAYER (step 6). Only on the no-code real-defect path,
        # only when source is long enough for shrinking to be worth the cost.
        # Per 6E Q3 design consultation: route-path property, no user flag.
        shrunk = _shrink_metamorphic_source(user_file, pattern, src_text)
        extras = {"leaf_locals": _relation_frame(results)}
        if shrunk is not None:
            extras["shrunk_input"] = shrunk
        return _attach_debug(result, src_text, src_origin, user_file, extras=extras)
    if verdict == "ERROR":
        return LensResult("metamorphic", "ERROR", summary, detail=detail)
    if verdict == "UNSUPPORTED":
        result = LensResult("metamorphic", "UNSUPPORTED",
                          _u("U1:no_coverage", summary).summary())
        return _attach_debug(result, src_text, src_origin, user_file)
    return LensResult("metamorphic", "PASS", summary, detail=detail)


def _relation_frame(results: Any) -> Dict[str, str]:
    """For a metamorphic RELATION-FAIL, surface which relation broke and its
    input→output shape so 'intermediate_values' names WHAT broke, not just
    THAT something did. Returns a dict of stable string reprs -- the debug
    core's intermediate_values field is Dict[str, str] by contract."""
    out: Dict[str, str] = {}
    for r in results or []:
        if not getattr(r, "passed", True):
            out[f"relation:{r.name}"] = str(getattr(r, "reason", ""))
            src = getattr(r, "source_input", None)
            fol = getattr(r, "follow_up_input", None)
            if src is not None:
                out[f"{r.name}.source_input"] = repr(src)[:200]
            if fol is not None:
                out[f"{r.name}.follow_up_input"] = repr(fol)[:200]
            break  # first failing relation is enough — do not dump the whole run
    return out


_DDMIN_LENGTH_FLOOR = 8      # per 6E Q3: skip ddmin on trivially-minimal input
_DDMIN_MAX_STEPS = 40        # bound the cost — each step is a full metamorphic rerun


def _shrink_metamorphic_source(
    user_file: Path, pattern: str, source_text: str,
) -> Optional[Dict[str, Any]]:
    """Shrink the user's source file to the minimum lines that still produce
    RELATION-FAIL on the same pattern. Returns a dict with the minimal source
    and the ddmin stats, or None when shrinking is skipped (too short) or
    fails (never reproduced).

    NAMED LIMIT: each ddmin step invokes a full metamorphic subprocess, so
    this is bounded by _DDMIN_MAX_STEPS. On cap hit, best-so-far is returned
    with hit_step_cap=True in the payload so a formatter can surface that.

    Predicate errors are treated as PASS by ddmin — a rerun that itself
    broke does not count as "still fails" (safe default: don't converge
    on a predicate defect).
    """
    lines = source_text.splitlines(keepends=True)
    if len(lines) < _DDMIN_LENGTH_FLOOR:
        return None

    from forge_gate.debug_core import ddmin

    def still_fails(subset_lines: List[str]) -> bool:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False,
        ) as tf:
            tf.write("".join(subset_lines))
            tmp = Path(tf.name)
        try:
            with _ToolsPath():
                from metamorphic.runner import run_metamorphic
                v, _, _ = run_metamorphic(tmp, pattern)
            return v == "RELATION-FAIL"
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass

    result = ddmin(lines, still_fails, max_steps=_DDMIN_MAX_STEPS)
    if not result.predicate_ever_failed:
        return None
    return {
        "minimal_source": "".join(result.minimal),
        "original_lines": len(lines),
        "minimal_lines": len(result.minimal),
        "steps": result.steps,
        "hit_step_cap": result.hit_step_cap,
    }


def _attach_debug(
    result: "LensResult",
    src_text: str,
    src_origin: str,
    user_file: Path,
    *,
    exc: Optional[BaseException] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> "LensResult":
    """Populate result.debug for FAIL/UNSUPPORTED verdicts; no-op on PASS.

    Route through the forge-gate caller layer (debug_caller.route) so U-code
    dispatch and the real-defect path stay in one place. Lazy-imported to
    avoid the debug_caller <-> lenses circular import.

    Any failure here is swallowed — debug is off-path, must never modify a
    verdict. A raised exception in the wire-up would silently change a PASS
    into an ERROR; the try/except guarantees the verdict returned is exactly
    the verdict computed.
    """
    # UNSTABLE joins FAIL/UNSUPPORTED because twin-consensus produces UNSTABLE
    # on a C++ compile failure, and brief §4.3 explicitly requires the
    # compiler-output debug path there. Design invariant is now:
    # "populate debug only on statuses that objected or expressed uncertainty
    # about the code" -- PASS and ERROR (harness-broke) still stay debug=None.
    if result.status not in ("FAIL", "UNSUPPORTED", "UNSTABLE"):
        return result
    try:
        from forge_gate.debug_caller import route as _debug_route
        result.debug = _debug_route(
            result,
            source_text=src_text,
            source_origin=src_origin,
            failing_input=str(user_file),
            extras=extras,
            exception=exc,
        )
    except Exception:  # noqa: BLE001 — debug is off-path
        result.debug = None
    return result


def _run_invariant(user_file: Path, pattern: str) -> LensResult:
    # DEBUG WIRE-UP (step 6, invariant lens). Same pattern as metamorphic:
    # capture source unconditionally at top, _attach_debug on FAIL/UNSUPPORTED.
    try:
        src_text, src_origin = capture_source(user_file)
    except Exception:
        src_text, src_origin = "", str(user_file)

    with _ToolsPath():
        try:
            import invariant_gate
            status, reason = invariant_gate.verify(str(user_file), pattern)
        except Exception as exc:
            return LensResult("invariant", "ERROR", f"{type(exc).__name__}: {exc}{_tools_hint()}")
    if status == "FAIL":
        return _attach_debug(
            LensResult("invariant", "FAIL", reason, objected=True),
            src_text, src_origin, user_file,
            extras={"leaf_locals": {"invariant_reason": reason}},
        )
    if status == "UNSUPPORTED":
        # Two shapes from invariant_gate — distinguished by reason substring.
        # If a new UNSUPPORTED reason ever ships from the tool, the fallback
        # here quietly maps it to U1 — the integration test on this branch
        # asserts BOTH known shapes to catch drift.
        code = "U2:contract_unratified" if "not yet ratified" in reason else "U1:no_coverage"
        return _attach_debug(
            LensResult("invariant", "UNSUPPORTED", _u(code, reason).summary()),
            src_text, src_origin, user_file,
            extras={"contract_path": None, "pattern": pattern},
        )
    return LensResult("invariant", "PASS", reason)


def _run_twin(user_file: Path, pattern: str, timeout: float) -> LensResult:
    # DEBUG WIRE-UP (step 6, twin lens). Twin adds a distinct debug path
    # per brief §4.3: compiler_output when the C++ half fails to build.
    # We surface the failing metric side (whichever twin diverged) via
    # tc.details as leaf_locals so the artifact names WHICH twin.
    try:
        src_text, src_origin = capture_source(user_file)
    except Exception:
        src_text, src_origin = "", str(user_file)

    with _ToolsPath():
        try:
            import twin_consensus
            tc = twin_consensus.consensus_verify(user_file, pattern, timeout=timeout)
        except Exception as exc:
            return LensResult("twin-consensus", "ERROR", f"{type(exc).__name__}: {exc}{_tools_hint()}")
    detail = list(tc.details or [])
    if tc.status in ("FAIL", "UNSTABLE"):
        # Brief §4.3 spec: "both twin outputs side by side, the metric each
        # produced, and the tolerance that was exceeded." Surface tc.metrics
        # (values) and tc.tolerance (bound) alongside the detail lines so a
        # maintainer sees BY HOW MUCH each twin diverged, not only WHICH.
        # 6E step-6 review Finding 4 (2026-09-07).
        iv: Dict[str, str] = {
            f"twin.detail[{i}]": d for i, d in enumerate(detail[:8])
        }
        metrics = getattr(tc, "metrics", None)
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                iv[f"twin.metric.{k}"] = repr(v)[:100]
        tol = getattr(tc, "tolerance", None)
        if tol is not None:
            iv["twin.tolerance"] = repr(tol)
        # 4Q step-6 review Finding 1: UNSTABLE is the compile-failed shape
        # per brief §4.3; put the compiler output on the dedicated
        # compiler_output field, not stuffed into intermediate_values.
        # tc.compiler_output is the natural source when available; fall back
        # to the detail lines that mention "compile"/"compiler" so a formatter
        # gets SOMETHING on the compiler_output channel.
        extras: Dict[str, Any] = {"leaf_locals": iv}
        co = getattr(tc, "compiler_output", None)
        if co:
            extras["compiler_output"] = co
        elif tc.status == "UNSTABLE":
            compile_lines = [d for d in detail if "compil" in d.lower()]
            if compile_lines:
                extras["compiler_output"] = "\n".join(compile_lines)
        return _attach_debug(
            LensResult("twin-consensus", tc.status, tc.reason, objected=True, detail=detail),
            src_text, src_origin, user_file,
            extras=extras,
        )
    if tc.status == "UNSUPPORTED":
        return _attach_debug(
            LensResult("twin-consensus", "UNSUPPORTED",
                       _u("U1:no_coverage", tc.reason).summary(),
                       detail=detail),
            src_text, src_origin, user_file,
        )
    return LensResult("twin-consensus", "PASS", tc.reason, detail=detail)


def _canonicalize(obj: Any) -> Any:
    """Convert arbitrary Python return values into a stable, comparable form.

    Custom objects become dicts of their public attributes; built-in containers
    and primitives pass through. This avoids comparing object ids or memory
    addresses (e.g. `<TreeNode object at 0x...>`) when the underlying data is
    identical.
    """
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canonicalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if hasattr(obj, "__dataclass_fields__"):
        # dataclass — use asdict
        from dataclasses import asdict
        return _canonicalize(asdict(obj))
    if hasattr(obj, "_asdict"):
        # namedtuple
        return _canonicalize(obj._asdict())
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return _canonicalize(vars(obj))
    # Fallback: repr is the best stable thing we have for truly opaque objects.
    return repr(obj)


def _run_determinism(user_file: Path, pattern: str, timeout: float) -> LensResult:
    """Detect non-deterministic implementations by varying the global RNG state.

    Retries on HARNESS error. This lens spawns subprocesses; under load a spawn can
    fail for reasons that have nothing to do with the code under test. Measured
    2026-08-01: two acceptance suites run back-to-back made this lens report ERROR
    (-> UNSTABLE) for all 10 canonical oracles, which the acceptance test then recorded
    as "the lens is unsound". Run alone, the same code returned 10/10 PASS.

    THE LAW: a harness error must never be reported as a verdict about the code. A probe
    that fails for its own reasons and reports failure accuses the pattern of a defect it
    does not have -- and that accusation flows onward as if it were measurement.
    """
    # DEBUG WIRE-UP (step 6, determinism lens). Capture source once, attach
    # to whichever final result is non-ERROR. ERROR returns pass through
    # unchanged (design: ERROR leaves debug=None).
    try:
        src_text, src_origin = capture_source(user_file)
    except Exception:
        src_text, src_origin = "", str(user_file)

    last = None
    for attempt in range(3):
        result = _run_determinism_once(user_file, pattern, timeout)
        if result.status != "ERROR":
            # Brief §4.4: surface tolerance and run-pair alongside the diff.
            # The reason string now names the run pair inline (see
            # _run_determinism_once); extras add the tolerance explicitly so
            # a formatter does not need to parse the reason to find it.
            # 6E step-6 review Finding 5 (2026-09-07).
            return _attach_debug(
                result, src_text, src_origin, user_file,
                extras={"leaf_locals": {
                    "determinism_reason": result.reason,
                    "determinism_tolerance": "1e-12",
                    "determinism_n_runs": "3",
                }},
            )
        last = result
        time.sleep(0.4 * (attempt + 1))
    return LensResult(
        "determinism", "ERROR",
        f"harness failed {3} times, no verdict on the code itself "
        f"(last: {last.reason if last else 'unknown'})",
    )


def _run_determinism_once(user_file: Path, pattern: str, timeout: float) -> LensResult:
    """One attempt. See _run_determinism for the retry rationale."""
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    import textwrap
    import types

    from .catalog import resolve_pattern
    from .runner import run_file

    N_RUNS = 3
    TOLERANCE = 1e-12

    try:
        pattern_meta = resolve_pattern(pattern)
    except Exception as exc:
        return LensResult("determinism", "ERROR", f"cannot resolve pattern: {exc}")

    fixture_script = Path(__file__).resolve().parent / "fixtures" / f"{pattern_meta['fixture']}.py"
    if not fixture_script.exists():
        return LensResult("determinism", "UNSUPPORTED", _u("U1:no_coverage", f"no fixture for {pattern}").summary())

    # ---- Step 1: capture the exact arguments the fixture passes to user_fn ----
    capture_src = textwrap.dedent(f"""
        import importlib.util
        import json
        import sys
        import types

        sys.path.insert(0, {str(_PKG_PARENT)!r})
        import forge_gate.fixture_engine as fe

        class DataCaptured(Exception):
            pass

        class FakeUserModule(types.ModuleType):
            def __init__(self):
                super().__init__("user_impl")
                self._captured = None

            def __getattr__(self, name):
                def capture(*args, **kwargs):
                    self._captured = {{"fn": name, "args": args, "kwargs": kwargs}}
                    raise DataCaptured("captured")
                return capture

        fake_user = FakeUserModule()
        sys.modules["user_impl"] = fake_user

        _real_load_module = fe.load_module
        def _patched_load_module(path, module_name):
            if module_name == "user_impl":
                return fake_user
            return _real_load_module(path, module_name)
        fe.load_module = _patched_load_module

        fixture_script_arg = sys.argv[1]
        sys.argv = [fixture_script_arg, "/dev/null"]
        spec = importlib.util.spec_from_file_location("fixture_mod", fixture_script_arg)
        fixture_mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(fixture_mod)
            if hasattr(fixture_mod, "main"):
                fixture_mod.main()
        except DataCaptured:
            pass

        if fake_user._captured is None:
            print("CAPTURE_ERROR no function captured", file=sys.stderr)
            sys.exit(1)

        print("CAPTURED " + json.dumps(fake_user._captured, default=str))
    """)

    capture_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=_scratch_dir()) as tf:
            tf.write(capture_src)
            capture_file = Path(tf.name)
        res = run_file(capture_file, timeout=timeout, extra_args=[str(fixture_script)], no_network=True, memory_mb=512)
        if res.timed_out:
            return LensResult("determinism", "ERROR", "fixture capture timed out")
        if res.exit_code != 0:
            return LensResult("determinism", "UNSUPPORTED", _u("U4:capture_failed", res.stderr[-200:]).summary())
        captured_json = None
        for line in res.stdout.splitlines():
            if line.startswith("CAPTURED "):
                captured_json = line[len("CAPTURED "):]
                break
        if captured_json is None:
            return LensResult("determinism", "ERROR", "capture produced no CAPTURED line")
        captured = json.loads(captured_json)
    except Exception as exc:
        return LensResult("determinism", "ERROR", f"capture exception: {type(exc).__name__}: {exc}")
    finally:
        if capture_file:
            try:
                capture_file.unlink()
            except Exception:
                pass

    fn_name = captured["fn"]
    args = captured["args"]
    kwargs = captured["kwargs"]

    # ---- Step 2: run the user function N times on identical data with different seeds ----
    tmpdir = Path(tempfile.mkdtemp(prefix="fg-determinism-", dir=_scratch_dir()))
    harness = tmpdir / "run_one.py"
    harness.write_text(textwrap.dedent(f"""
        import json
        import random
        import sys
        sys.path.insert(0, {str(_PKG_PARENT)!r})
        from forge_gate.fixture_engine import get_fn, load_module

        def _can(obj):
            if obj is None or isinstance(obj, (bool, int, float, str)):
                return obj
            if isinstance(obj, (list, tuple)):
                return [_can(v) for v in obj]
            if isinstance(obj, dict):
                return {{str(k): _can(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}}
            if hasattr(obj, "__dataclass_fields__"):
                from dataclasses import asdict
                return _can(asdict(obj))
            if hasattr(obj, "_asdict"):
                return _can(obj._asdict())
            if hasattr(obj, "__dict__") and not isinstance(obj, type):
                return _can(vars(obj))
            return repr(obj)

        seed_idx, user_path = sys.argv[1:3]
        user_mod = load_module(user_path, "user_impl")
        user_fn = get_fn(user_mod, {fn_name!r})

        if seed_idx == "0":
            random.seed(42)
        else:
            seed = 1000 + int(seed_idx)
            random.seed(seed)
            try:
                import numpy as np
                np.random.seed(2000 + int(seed_idx))
            except Exception:
                pass

        args = json.loads({json.dumps(args)!r})
        kwargs = json.loads({json.dumps(kwargs)!r})
        try:
            out = user_fn(*args, **kwargs)
        except Exception as exc:
            out = {{"_error": repr(exc)}}
        print(json.dumps(_can(out)))
    """), encoding="utf-8")

    outputs: list[Any] = []
    from forge_gate.config import python as _python
    runner = _python()
    try:
        for i in range(N_RUNS):
            try:
                env = os.environ.copy()
                env.pop("PYTHONPATH", None)
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                proc = subprocess.run(
                    [runner, str(harness), str(i), str(user_file)],
                    capture_output=True, text=True, timeout=timeout,
                    env=env,
                )
                if proc.returncode != 0:
                    return LensResult("determinism", "ERROR", f"run {i} exited {proc.returncode}: {proc.stderr[-200:]}")
                raw = json.loads(proc.stdout.splitlines()[-1])
                outputs.append(_canonicalize(raw))
            except Exception as exc:
                return LensResult("determinism", "ERROR", f"run {i} failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    # ---- Step 3: compare all runs to run 0 at 1e-12 ----
    def find_diff(a: Any, b: Any, prefix: str = "") -> Optional[str]:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a:
                    return f"{prefix}{k}: missing in first run"
                if k not in b:
                    return f"{prefix}{k}: missing in run {i}"
                d = find_diff(a[k], b[k], prefix=f"{prefix}{k}.")
                if d:
                    return d
            return None
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return f"{prefix}list length differs ({len(a)} vs {len(b)})"
            for idx, (x, y) in enumerate(zip(a, b)):
                d = find_diff(x, y, prefix=f"{prefix}[{idx}]")
                if d:
                    return d
            return None
        if isinstance(a, bool) or isinstance(b, bool):
            if a != b:
                return f"{prefix}: {a!r} vs {b!r}"
            return None
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if a == b:
                return None
            denom = max(abs(a), abs(b), 1.0)
            if abs(a - b) / denom > TOLERANCE:
                return f"{prefix}: {a!r} vs {b!r}"
            return None
        if a != b:
            return f"{prefix}: {a!r} vs {b!r}"
        return None

    for i in range(1, N_RUNS):
        diff = find_diff(outputs[0], outputs[i])
        if diff:
            # Brief §4.4 wants "which value diverged, at what tolerance, on
            # which run pair." Name the run pair (0 vs i) in the reason so
            # the wire-up can surface it downstream. Tolerance is a module
            # constant already; the wire-up will add it to extras.
            return LensResult(
                "determinism", "FAIL",
                f"non-deterministic output at run 0 vs run {i} (tolerance {TOLERANCE}): {diff}",
                objected=determinism_gates_for(pattern),
            )

    return LensResult("determinism", "PASS", f"{N_RUNS} runs identical at tolerance {TOLERANCE}")


def parse_lens_selection(spec: Optional[str]) -> List[str]:
    """'all' | 'default' | comma list. property-primary is always included."""
    if not spec or spec == "default":
        return list(DEFAULT_LENSES)
    if spec == "all":
        return list(ALL_LENSES)
    chosen = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [c for c in chosen if c not in ALL_LENSES]
    if unknown:
        raise ValueError(
            f"unknown lens(es): {', '.join(unknown)}. choose from: {', '.join(ALL_LENSES)}, "
            "or use 'all' / 'default'"
        )
    return [BASE_LENS] + [c for c in chosen if c != BASE_LENS]


def _run_guard(user_file: Path, timeout: float) -> LensResult:
    """Run the external guard lens subprocess and return an enforced result.

    The guard lens is now gating: a genuine NaN-defect FAIL sets objected=True
    and can turn PASS -> FAIL. A timeout or crash is returned as ERROR and
    never objected. An INCONCLUSIVE verdict is returned as UNSUPPORTED with a
    U4:coverage_gap reason code — a harness that could not measure must not
    accuse the code, and ERROR would bubble to UNSTABLE which itself accuses.
    """
    import shutil
    import subprocess

    # DEBUG WIRE-UP (step 5, fast-follow after metamorphic): capture source
    # BEFORE the guard subprocess runs. guard_lens.py uses its own
    # NamedTemporaryFile + finally: unlink() internally; we capture the
    # ADAPTER-side user_file which is a persistent path, so the ordering is
    # trivially safe here. The trap is inside guard_lens.py, not this layer.
    try:
        src_text, src_origin = capture_source(user_file)
    except Exception:
        src_text, src_origin = "", str(user_file)

    from forge_gate.config import python as _python
    runner = _python()
    if not runner:
        return LensResult("guard", "ERROR", "python3 not available")

    try:
        _require_tools()
    except NotConfigured as exc:
        return LensResult("guard", "ERROR", str(exc))
    cmd = [runner, str(TOOLS_DIR / "guard_lens.py"), "--fixture", str(user_file)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(timeout, 60.0),
            env={**__import__("os").environ, "PYTHONDONTWRITECOMPILE": "1"},
        )
    except subprocess.TimeoutExpired:
        return LensResult("guard", "ERROR", "guard lens timed out")
    except Exception as exc:
        return LensResult("guard", "ERROR", f"guard lens spawn failed: {type(exc).__name__}: {exc}")

    # Extract verdict line from stdout.
    verdict = None
    detail: List[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("verdict:"):
            verdict = line[len("verdict:"):].strip()
        elif line.startswith("detail:"):
            detail.append(line[len("detail:"):].strip())

    if proc.returncode != 0 or verdict is None:
        return LensResult(
            "guard", "ERROR",
            f"guard lens did not produce verdict (rc={proc.returncode})",
            detail=detail or [proc.stderr[-200:]] if proc.stderr else [],
        )

    # A genuine NaN-defect FAIL now gates; INCONCLUSIVE/ERROR/PASS do not.
    if verdict.startswith("FAIL"):
        return _attach_debug(
            LensResult("guard", "FAIL", verdict, objected=True, detail=detail),
            src_text, src_origin, user_file,
            extras={"leaf_locals": {"verdict": verdict, "detail": "; ".join(detail)}},
        )
    if verdict.startswith("PASS"):
        return LensResult("guard", "PASS", verdict, detail=detail)
    if verdict.startswith("INCONCLUSIVE"):
        # A harness that could not measure must not accuse the code (§3.2).
        # ERROR would bubble to UNSTABLE — itself an accusation. UNSUPPORTED
        # with an explicit U4 code names the coverage gap without blame.
        return _attach_debug(
            LensResult(
                "guard", "UNSUPPORTED",
                _u("U4:coverage_gap", verdict).summary(),
                detail=detail,
            ),
            src_text, src_origin, user_file,
            extras={"params_aborted": [], "unexercisable": []},
        )
    return _attach_debug(
        LensResult("guard", "UNSUPPORTED", verdict, detail=detail),
        src_text, src_origin, user_file,
    )


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def evaluate(
    *,
    pattern: str,
    user_file: Path,
    base_status: str,
    base_reason: str,
    base_metrics: Dict[str, Any],
    lenses: List[str],
    timeout: float = 60.0,
    short_circuit: bool = True,
) -> GateVerdict:
    """Run the selected lenses over an already-computed base verdict and reduce.

    short_circuit=True skips supplementary lenses once property-primary has failed --
    they cannot overturn it, so running them only costs time. Set False to collect
    every lens's opinion for a demo or a report; the VERDICT is identical either way.
    """
    results: List[LensResult] = [
        LensResult(
            BASE_LENS,
            base_status,
            base_reason,
            objected=(base_status not in ("PASS", "UNSUPPORTED")),
        )
    ]

    # Supplementary and advisory lenses cannot overturn a base FAIL, so by default
    # we stop there. With short_circuit=False we still gather them as evidence --
    # the verdict is unchanged, we simply show the full picture.
    if base_status == "PASS" or not short_circuit:
        if "metamorphic" in lenses:
            results.append(_run_metamorphic(user_file, pattern))
        if "invariant" in lenses:
            results.append(_run_invariant(user_file, pattern))
        if "twin-consensus" in lenses:
            results.append(_run_twin(user_file, pattern, timeout))
        if "determinism" in lenses:
            results.append(_run_determinism(user_file, pattern, timeout))
        if "guard" in lenses:
            results.append(_run_guard(user_file, timeout))

    objectors = [l for l in results if l.objected]
    errored = [l for l in results if l.status == "ERROR"]

    if objectors:
        status = "FAIL"
        if len(objectors) == 1 and objectors[0].name == BASE_LENS:
            reason = base_reason
        else:
            named = "; ".join(f"{l.name}: {l.reason}" for l in objectors)
            reason = f"property-primary passed, but {len(objectors)} lens(es) objected -- {named}"
    elif errored:
        status = "UNSTABLE"
        reason = "; ".join(f"{l.name} errored: {l.reason}" for l in errored)
    else:
        status = "PASS"
        reason = base_reason
        skipped = [l.name for l in results if l.status == "UNSUPPORTED"]
        if skipped:
            reason += f" (unsupported here: {', '.join(skipped)})"

    return GateVerdict(
        status=status,
        pattern=pattern,
        user_file=str(user_file),
        reason=reason,
        lenses=results,
        metrics=base_metrics or {},
    )


def render(v: GateVerdict, show_table: bool) -> str:
    lines = [f"{v.status}: {v.pattern}", f"  file:   {v.user_file}", f"  reason: {v.reason}"]
    if v.metrics:
        lines.append(f"  metrics: {v.metrics}")
    if show_table:
        lines.append("  lenses:")
        for l in v.lenses:
            mark = "<<" if l.objected else "  "
            lines.append(f"    {l.name:<18} {l.status:<12} {mark} {l.reason}")
            for d in l.detail:
                lines.append(f"        - {d}")
    return "\n".join(lines)
