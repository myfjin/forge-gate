#!/usr/bin/env python3
"""Forge Gate — end-to-end demo.

Runs candidate files through the gate and prints the layered verdict. Zero LLM in
any verdict path — compile, run, compare to the signed oracle.

IMPORTANT: this demo does NOT compute its own verdict. It calls the real CLI and
prints what the product actually says (forge_gate.lenses is the single source of
truth for the algebra). If the demo and the product ever disagreed, the demo would
be lying to a customer -- so they cannot disagree by construction.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=f"{ROOT}/src:{ROOT}/tools", PYTHONDONTWRITEBYTECODE="1")

LENS_ORDER = ["property-primary", "metamorphic", "invariant", "twin-consensus"]


def gate(f: str, pattern: str) -> dict:
    """Ask the real CLI for its verdict. --no-short-circuit gathers every lens's
    opinion even after the base fails; the VERDICT is identical either way."""
    p = subprocess.run(
        [PY, "-m", "forge_gate.cli", "verify", f, "--pattern", pattern,
         "--lenses", "all", "--no-short-circuit", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), env=ENV, timeout=300,
    )
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"status": "ERROR", "reason": (p.stderr or p.stdout or "").strip()[:200],
                "lenses": [], "objected_by": []}


CASES = [
    ("examples say correct", "tests/corpus/linear_regression_gd_canonical_good.py", "linear_regression_gd",
     "A correct implementation."),
    ("correct — DIFFERENT algorithm", "tests/corpus/linreg_normal_eq.py", "linear_regression_gd",
     "Normal equations instead of gradient descent. Right answer, different method — must still PASS."),
    ("broken (sign flip)", "tests/corpus/linreg_broken_sign.py", "linear_regression_gd",
     "Gradient sign flipped — diverges."),
    ("the agent that did NOTHING", "/tmp/demo_noop.py", "linear_regression_gd",
     "Defines the function but returns without computing. The classic false-'done'."),
    ("subtly wrong — the base check MISSES it", "/tmp/demo_ratio.py", "linear_regression_gd",
     "Weights [2.4, 3.0] — plausible-looking but wrong. Pure property-primary PASSES it (R²>0.8, weights "
     "within tolerance). The metamorphic and invariant lenses CATCH it. This is exactly why one check "
     "isn't enough."),
]


def main():
    Path("/tmp/demo_noop.py").write_text(
        "def linear_regression_gd(X, y, learning_rate=0.01, iterations=200):\n    return [0.0, 0.0], 0.0\n")
    Path("/tmp/demo_ratio.py").write_text(
        "def linear_regression_gd(X, y, learning_rate=0.01, iterations=200):\n    return [2.4, 3.0], 0.0\n")

    print("=" * 78)
    print("  FORGE GATE — deterministic verification for AI-generated code")
    print("  Four execution-backed lenses. Zero LLM in the verdict path.")
    print("=" * 78)

    for title, f, pattern, desc in CASES:
        v = gate(f, pattern)
        badge = "✅ PASS" if v["status"] == "PASS" else f"❌ {v['status']}"
        print(f"\n▶ {title}   →   {badge}")
        print(f"  {desc}")

        by_name = {l["lens"]: l for l in v.get("lenses", [])}
        cells = []
        for name in LENS_ORDER:
            l = by_name.get(name)
            if l is None:
                continue
            mark = "*" if l.get("objected") else ""
            cells.append(f"{name}={l['status']}{mark}")
        print("  lenses: " + "   ".join(cells))

        objectors = v.get("objected_by", [])
        if objectors:
            print(f"  caught by: {', '.join(objectors)}   (* = objected)")

    print("\n" + "=" * 78)
    print("  PASS = compiled, ran, and matched the signed oracle across every lens.")
    print("  FAIL = at least one deterministic check caught it — and named which.")
    print("  Verdicts come from the CLI itself, not from this script.")
    print("=" * 78)


if __name__ == "__main__":
    main()
