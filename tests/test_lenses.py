"""Tests for the KEYSTONE verdict algebra (forge_gate.lenses).

The algebra is what every customer-facing skin will sit on, so it is tested in
isolation from the (slow) real lenses: the lens runners are monkeypatched and only
the reduction is exercised.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forge_gate import lenses as L  # noqa: E402


def _base(status="PASS"):
    return dict(
        pattern="linear_regression_gd",
        user_file=Path("/tmp/whatever.py"),
        base_status=status,
        base_reason="Linear regression quality properties " + ("hold" if status == "PASS" else "fail"),
        base_metrics={"r2": 0.99},
    )


def _stub(monkeypatch, *, meta=None, inv=None, twin=None, det=None, guard=None):
    if meta is not None:
        monkeypatch.setattr(L, "_run_metamorphic", lambda f, p: meta)
    if inv is not None:
        monkeypatch.setattr(L, "_run_invariant", lambda f, p: inv)
    if twin is not None:
        monkeypatch.setattr(L, "_run_twin", lambda f, p, t: twin)
    if det is not None:
        monkeypatch.setattr(L, "_run_determinism", lambda f, p, t: det)
    if guard is not None:
        monkeypatch.setattr(L, "_run_guard", lambda f, t: guard)


# ---------------------------------------------------------------- selection

def test_default_is_every_lens():
    """The gate hands users its STRONGEST verdict unless they ask for less.
    Flipped 2026-07-24 after all-lens measured 100/100 with zero false positives."""
    assert set(L.parse_lens_selection(None)) == set(L.ALL_LENSES)
    assert set(L.parse_lens_selection("default")) == set(L.ALL_LENSES)


def test_a_subset_can_still_be_requested():
    assert L.parse_lens_selection("metamorphic") == ["property-primary", "metamorphic"]


def test_all_selection_includes_every_lens():
    assert set(L.parse_lens_selection("all")) == set(L.ALL_LENSES)


def test_base_lens_is_always_included():
    assert L.parse_lens_selection("invariant")[0] == L.BASE_LENS


def test_unknown_lens_is_rejected():
    with pytest.raises(ValueError):
        L.parse_lens_selection("bogus")


# ---------------------------------------------------------------- algebra

def test_base_fail_is_decisive_and_skips_other_lenses(monkeypatch):
    """A property-primary FAIL cannot be overturned, so nothing else should run."""
    called = []
    monkeypatch.setattr(L, "_run_metamorphic", lambda f, p: called.append("m"))
    v = L.evaluate(**_base("FAIL"), lenses=list(L.ALL_LENSES))
    assert v.status == "FAIL"
    assert called == []
    assert [l.name for l in v.objectors] == ["property-primary"]


def test_all_pass_gives_pass(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "PASS", "ok"),
          inv=L.LensResult("invariant", "PASS", "ok"),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"),
          guard=L.LensResult("guard", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "PASS"
    assert v.objectors == []


def test_single_lens_objection_downgrades_pass_to_fail(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "FAIL", "relation broke", objected=True),
          inv=L.LensResult("invariant", "PASS", "ok"),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "FAIL"
    assert [l.name for l in v.objectors] == ["metamorphic"]
    assert "metamorphic" in v.reason and "relation broke" in v.reason


def test_verdict_names_every_objector(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "FAIL", "relations failed", objected=True),
          inv=L.LensResult("invariant", "FAIL", "ratio outside band", objected=True),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "FAIL"
    assert {l.name for l in v.objectors} == {"metamorphic", "invariant"}
    assert "ratio outside band" in v.reason


def test_unsupported_lens_does_not_count_against(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "UNSUPPORTED", "no relations"),
          inv=L.LensResult("invariant", "UNSUPPORTED", "no contract"),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"),
          guard=L.LensResult("guard", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "PASS"
    assert "unsupported here" in v.reason


def test_twin_unstable_objects(monkeypatch):
    """Cross-language divergence is an objection, not a shrug."""
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "PASS", "ok"),
          inv=L.LensResult("invariant", "PASS", "ok"),
          twin=L.LensResult("twin-consensus", "UNSTABLE", "twins disagree", objected=True),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "FAIL"
    assert [l.name for l in v.objectors] == ["twin-consensus"]


def test_lens_error_yields_unstable_not_pass(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "ERROR", "boom"),
          inv=L.LensResult("invariant", "PASS", "ok"),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "UNSTABLE"


def test_objection_beats_error(monkeypatch):
    """A definite FAIL outranks an inconclusive lens."""
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "ERROR", "boom"),
          inv=L.LensResult("invariant", "FAIL", "violated", objected=True),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=list(L.ALL_LENSES))
    assert v.status == "FAIL"


def test_selection_is_honoured(monkeypatch):
    """A lens that was not selected must not run, even if it would object."""
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "PASS", "ok"),
          inv=L.LensResult("invariant", "FAIL", "would have objected", objected=True),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    v = L.evaluate(**_base(), lenses=["property-primary", "metamorphic"])
    assert v.status == "PASS"
    assert "invariant" not in {l.name for l in v.lenses}


def test_json_shape_is_stable(monkeypatch):
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "FAIL", "nope", objected=True),
          inv=L.LensResult("invariant", "PASS", "ok"),
          twin=L.LensResult("twin-consensus", "PASS", "ok"),
          det=L.LensResult("determinism", "PASS", "ok"))
    d = L.evaluate(**_base(), lenses=list(L.ALL_LENSES)).as_dict()
    assert set(d) == {"status", "pattern", "file", "reason", "objected_by", "metrics", "lenses"}
    assert d["objected_by"] == ["metamorphic"]
    json.dumps(d)  # must be serialisable


# ---------------------------------------------------------------- integration

def test_cli_json_end_to_end(tmp_path):
    """One real end-to-end run: correct code, all lenses, JSON out."""
    good = REPO_ROOT / "tests" / "corpus" / "linreg_normal_eq.py"
    env = {"PYTHONPATH": f"{REPO_ROOT / 'src'}:{REPO_ROOT / 'tools'}", "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, "-m", "forge_gate.cli", "verify", str(good),
         "--pattern", "linear_regression_gd", "--lenses", "all", "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=600,
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "PASS"
    assert payload["objected_by"] == []
    assert {l["lens"] for l in payload["lenses"]} == set(L.ALL_LENSES)


def test_short_circuit_can_be_disabled_without_changing_the_verdict(monkeypatch):
    """Evidence gathering must never alter the verdict -- only what is reported."""
    _stub(monkeypatch,
          meta=L.LensResult("metamorphic", "FAIL", "nope", objected=True),
          inv=L.LensResult("invariant", "FAIL", "nope", objected=True),
          twin=L.LensResult("twin-consensus", "FAIL", "nope", objected=True),
          det=L.LensResult("determinism", "FAIL", "nope", objected=True))

    fast = L.evaluate(**_base("FAIL"), lenses=list(L.ALL_LENSES))
    full = L.evaluate(**_base("FAIL"), lenses=list(L.ALL_LENSES), short_circuit=False)

    assert fast.status == full.status == "FAIL"
    assert len(fast.lenses) == 1, "short circuit should stop after the base lens"
    assert len(full.lenses) == 6, "no-short-circuit should gather every lens"
