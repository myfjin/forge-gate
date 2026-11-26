"""Shared engine code used by gate fixtures.

This module is imported by fixtures. It provides helpers to load a Python file
as a module and to compare/emit results.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple


def load_module(path: str, module_name: str = "user_impl") -> Any:
    """Load a Python source file as a module."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    spec = importlib.util.spec_from_file_location(module_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_fn(mod: Any, name: str) -> Callable:
    """Get a function from a module, raising a clear error if missing."""
    fn = getattr(mod, name, None)
    if fn is None:
        raise AttributeError(
            f"Module {mod.__file__} must define function '{name}'"
        )
    if not callable(fn):
        raise TypeError(f"'{name}' in {mod.__file__} is not callable")
    return fn


def approx_eq(a: Any, b: Any, rel_tol: float = 1e-5, abs_tol: float = 1e-8) -> bool:
    """Recursive approximate equality for floats, lists, and dicts."""
    if isinstance(a, float) and isinstance(b, (int, float)):
        return math.isclose(a, float(b), rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(approx_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(approx_eq(a[k], b[k]) for k in a)
    return a == b


def emit(result: Dict[str, Any]) -> None:
    """Print the structured gate result line."""
    print("FORGE_GATE_RESULT " + json.dumps(result, sort_keys=True, default=str))


def run_both(
    user_path: str,
    aura_path: str,
    fn_name: str,
    build_fixture: Callable,
    evaluate: Callable[[Any, Any], Dict[str, Any]],
) -> None:
    """Generic helper: build one fixture, run user and AURA functions, emit result."""
    try:
        user_mod = load_module(user_path, "user_impl")
        aura_mod = load_module(aura_path, "aura_impl")
        user_fn = get_fn(user_mod, fn_name)
        aura_fn = get_fn(aura_mod, fn_name)

        fixture = build_fixture()
        user_out = user_fn(**fixture) if isinstance(fixture, dict) else user_fn(*fixture)
        aura_out = aura_fn(**fixture) if isinstance(fixture, dict) else aura_fn(*fixture)

        result = evaluate(user_out, aura_out)
        emit(result)
    except Exception as exc:  # noqa: BLE001
        emit({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)
