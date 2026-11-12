"""Signature extractor (design v1.4.5 §3a), Python only. Reads source as TEXT (ast), never runs it.

For each public top-level function with at least one positional argument it returns the
registry row shape: ``{"entry", "consumes": [{name, type, noun}], "produces": {type, names, noun}}``
plus ``required`` / ``total`` positional counts (the contract gate uses them). ``noun`` is always
None: §3a.3 option (a), null-by-default, no inference.
"""
from __future__ import annotations

import ast


def _returns(fn: ast.FunctionDef) -> list[str]:
    names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            parts = v.elts if isinstance(v, ast.Tuple) else [v]
            for p in parts:
                if isinstance(p, ast.Name) and p.id not in names:
                    names.append(p.id)
    return names


def extract(source: str) -> list[dict]:
    """Raises SyntaxError for text that is not Python."""
    tree = ast.parse(source)
    out = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
            continue
        a = fn.args
        pos = list(a.posonlyargs) + list(a.args)
        if not pos:
            continue
        n_default = len(a.defaults)
        out.append({
            "entry": fn.name,
            "consumes": [{"name": p.arg, "type": ast.unparse(p.annotation) if p.annotation else None,
                          "noun": None} for p in pos],
            "produces": {"type": ast.unparse(fn.returns) if fn.returns else None,
                         "names": _returns(fn), "noun": None},
            "required": len(pos) - n_default, "total": len(pos),
        })
    return out


def async_only(source: str) -> bool:
    """True when the source's only public top-level functions are async (not supported yet)."""
    tree = ast.parse(source)
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
    return bool(fns) and all(isinstance(n, ast.AsyncFunctionDef) for n in fns)
