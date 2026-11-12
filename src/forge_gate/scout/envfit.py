"""Environment fit (design §4a.3 gate; §4a.7 / Q11: built just before behaviour, it decides
whether a run is attempted). The facts are the BOX's: which modules import inside the box is
measured by running a check there, never assumed from this host.
"""
from __future__ import annotations

import ast


def imports(source: str) -> tuple[set[str], bool]:
    """Top-level module names a source imports, and whether it has a relative import (which can
    never resolve for a lone file). Text only."""
    names, relative = set(), False
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = True
            elif node.module:
                names.add(node.module.split(".")[0])
    return names, relative


def fits(source_imports: tuple[set[str], bool], available: dict[str, bool]) -> tuple[bool, str]:
    names, relative = source_imports
    if relative:
        return False, "relative import"
    missing = sorted(n for n in names if not available.get(n, False))
    return (False, "missing: " + ",".join(missing)) if missing else (True, "fits")


def _no_call(node) -> bool:
    return not any(isinstance(n, (ast.Call, ast.Await, ast.Yield)) for n in ast.walk(node))


def runs_at_import(source: str) -> str | None:
    """Text only. None when loading the module only defines things; else what it would run.
    A script that reads files, exits or fetches at import cannot be compared as a library: its
    pairs would all fail at load (measured on the corpus 2026-11-11: 30 of 80 pairs)."""
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)):
            continue
        if isinstance(node, ast.ClassDef) and all(_no_call(d) for d in node.decorator_list) and all(_no_call(b) for b in node.bases):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _no_call(node):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue   # docstring
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test)):
            continue   # the main guard does not run on import
        return f"line {node.lineno}: {type(node).__name__}"
    return None
