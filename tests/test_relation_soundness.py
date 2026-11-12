"""RELATION SOUNDNESS LAW -- the gate that would have caught the 2026-07-23 false FAILs.

A metamorphic relation may only GATE (turn a property-primary PASS into a FAIL) if the
CANONICAL ORACLE ITSELF satisfies it across seed variation. A relation the oracle
violates is a false-FAIL generator: it rejects correct code depending on nothing but
the input data, which is the one failure mode that destroys trust in a verification gate.

This test perturbs every seed the relations use and runs all relations against their own
canonical oracle. Any GATING relation the oracle violates fails this test. To add a new
enforced relation, it must survive this sweep -- otherwise list it in
tools/metamorphic/relations.py::ADVISORY_RELATIONS with the measured evidence.

Run directly:   python3 tests/test_relation_soundness.py
Run via pytest: pytest tests/test_relation_soundness.py
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import metamorphic.relations as R  # noqa: E402

PATTERN_CATEGORY = {
    "linear_regression_gd": "regression",
    "polynomial_regression": "regression",
    "ridge_regression_normal_eq": "regression",
    "logistic_regression": "classification",
    "knn_classifier": "classification",
    "decision_tree_id3": "classification",
    "naive_bayes_text": "classification",
    "perceptron": "classification",
    "kmeans_clustering": "clustering",
    "gradient_descent_momentum": "optimization",
}

SEED_OFFSETS = [0, 1, 2, 3, 4, 5, 6, 7]

_ORIGINAL_SEED = random.seed
_offset = 0


def _seeded(a=None, *args, **kwargs):
    """Shift every integer seed so relation data varies run to run."""
    if isinstance(a, int):
        return _ORIGINAL_SEED(a + _offset)
    return _ORIGINAL_SEED(a, *args, **kwargs)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sweep():
    """Return {relation_name: [offsets where the ORACLE violated it]}."""
    global _offset
    violations: dict[str, list[int]] = {}
    seen: set[str] = set()
    random.seed = _seeded
    try:
        for pattern, category in PATTERN_CATEGORY.items():
            oracle = REPO_ROOT / "tests" / "corpus" / f"{pattern}_canonical_good.py"
            if not oracle.exists():
                continue
            for offset in SEED_OFFSETS:
                _offset = offset
                try:
                    mod = _load(oracle, f"{pattern}_soundness_{offset}")
                    results = R.evaluate_relations(category, mod, pattern)
                except Exception:
                    continue
                for r in results:
                    seen.add(r.name)
                    if not r.passed:
                        violations.setdefault(r.name, []).append(offset)
    finally:
        random.seed = _ORIGINAL_SEED
        _offset = 0
    return violations, seen


def test_gating_relations_are_satisfied_by_their_own_oracle():
    violations, seen = sweep()
    assert seen, "no relations were exercised -- sweep is broken"

    offenders = {
        name: offs for name, offs in violations.items() if R.is_gating(name)
    }
    assert not offenders, (
        "GATING relations violated by the canonical oracle itself -- these will "
        "produce FALSE FAILS on correct user code:\n"
        + "\n".join(f"  {n}: oracle failed at seed offsets {o}" for n, o in sorted(offenders.items()))
        + "\n\nEither fix the relation, or move it to ADVISORY_RELATIONS with this evidence."
    )


def test_advisory_list_has_no_stale_entries():
    """An advisory relation that the oracle now always satisfies should be promoted."""
    violations, seen = sweep()
    stale = sorted(n for n in R.ADVISORY_RELATIONS if n in seen and n not in violations)
    assert not stale, (
        "these relations are marked ADVISORY but the oracle satisfied them at every "
        f"offset -- re-measure and consider promoting them to gating: {stale}"
    )


if __name__ == "__main__":
    violations, seen = sweep()
    gating_bad = {n: o for n, o in violations.items() if R.is_gating(n)}
    advisory_bad = {n: o for n, o in violations.items() if not R.is_gating(n)}
    print("=" * 78)
    print("RELATION SOUNDNESS SWEEP -- oracle vs its own relations")
    print("=" * 78)
    print(f"relations exercised : {len(seen)}")
    print(f"seed offsets        : {SEED_OFFSETS}")
    print(f"advisory (expected) : {len(advisory_bad)}")
    for n, o in sorted(advisory_bad.items()):
        print(f"    ADVIS  {n:<38} oracle failed at {o}")
    if gating_bad:
        print(f"\nFAIL -- {len(gating_bad)} GATING relation(s) violated by the oracle:")
        for n, o in sorted(gating_bad.items()):
            print(f"    FAIL   {n:<38} oracle failed at {o}")
        sys.exit(1)
    print("\nPASS -- every gating relation is satisfied by its own oracle at all offsets.")
