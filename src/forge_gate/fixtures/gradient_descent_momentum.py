#!/usr/bin/env python3
"""Gate fixture for gradient_descent_momentum.

PASS is decided by convergence properties (both solvers reach near-zero loss,
momentum converges in fewer iterations than vanilla), not by matching the
AURA oracle's exact iteration counts.
"""
import random
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[2]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from forge_gate.catalog import reference_path
from forge_gate.fixture_engine import approx_eq, emit, get_fn, load_module


def main():
    if len(sys.argv) < 2:
        print("usage: gradient_descent_momentum.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    aura_path = str(reference_path("gradient_descent_momentum"))

    user_mod = load_module(user_path, "user_impl")
    aura_mod = load_module(aura_path, "aura_impl")

    user_vanilla = get_fn(user_mod, "vanilla_gd")
    user_momentum = get_fn(user_mod, "momentum_gd")
    aura_vanilla = get_fn(aura_mod, "vanilla_gd")
    aura_momentum = get_fn(aura_mod, "momentum_gd")

    random.seed(42)
    x0, y0 = 10.0, 10.0

    user_vanilla_iters, user_vanilla_fx = user_vanilla(x0, y0)
    user_momentum_iters, user_momentum_fx = user_momentum(x0, y0)
    aura_vanilla_iters, aura_vanilla_fx = aura_vanilla(x0, y0)
    aura_momentum_iters, aura_momentum_fx = aura_momentum(x0, y0)

    canonical_match = (
        approx_eq(user_vanilla_iters, aura_vanilla_iters, rel_tol=1e-5)
        and approx_eq(user_momentum_iters, aura_momentum_iters, rel_tol=1e-5)
    )

    metrics = {
        "vanilla_iters": user_vanilla_iters,
        "momentum_iters": user_momentum_iters,
        "vanilla_fx": round(user_vanilla_fx, 9),
        "momentum_fx": round(user_momentum_fx, 9),
        "speedup": round(user_vanilla_iters / max(user_momentum_iters, 1), 3),
        "canonical_match": canonical_match,
    }

    ok = (
        user_vanilla_fx < 1e-6
        and user_momentum_fx < 1e-6
        and user_momentum_iters < user_vanilla_iters
    )

    emit({
        "status": "PASS" if ok else "FAIL",
        "pattern": "gradient_descent_momentum",
        "reason": "Momentum GD convergence properties hold" if ok else "Momentum GD convergence properties fail",
        "metrics": metrics,
    })


if __name__ == "__main__":
    main()
