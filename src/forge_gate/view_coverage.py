"""VIEW_COVERAGE — static declaration-vs-adapter agreement for lens inputs.

STRICT: a mismatch fails the self-test. Advisory was rejected (6E 3b) because
the pre-campaign ADVISORY_LENSES label lied for 32 days and misled a reader.

SCOPED OUT (v1, 6E 3c): the walker covers adapter bodies in lenses.py only.
Tool-internal consumption drift (e.g. metamorphic/runner.py stops using
``pattern``) is INVISIBLE to this check. The declaration means "the adapter
forwards this input to its tool," not "the tool uses it." Extending the walk
into tool entry functions is a mechanical follow-up, not a new shape.

CONCEPTUAL INPUTS: some declared inputs (``canonical_oracle``, ``twin``) are
not adapter parameters — they are consumed inside the tool, not the adapter.
The walker detects them via _TOOL_INPUTS, a mapping from tool-call names to
the conceptual inputs they consume. This bridges the gap without walking tool
internals.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Dict, FrozenSet, List, Tuple, Set, Optional

# Tool calls that consume conceptual inputs (not adapter parameters).
# When the walker sees a call to one of these functions, it adds the mapped
# inputs to the derived view. This is the only place tool-specific knowledge
# enters the walker — it is a call-name → input mapping, not a tool walk.
_TOOL_INPUTS: Dict[str, FrozenSet[str]] = {
    "consensus_verify": frozenset({"canonical_oracle", "twin"}),
    "resolve_pattern": frozenset({"canonical_oracle"}),
}


def _callee_name(call: ast.Call) -> Optional[str]:
    """Extract the function name from a call expression."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _fn_params(fn_def: ast.FunctionDef) -> Set[str]:
    """Extract parameter names from an AST function definition."""
    params: Set[str] = set()
    for arg in fn_def.args.posonlyargs + fn_def.args.args:
        params.add(arg.arg)
    if fn_def.args.vararg:
        params.add(fn_def.args.vararg.arg)
    for arg in fn_def.args.kwonlyargs:
        params.add(arg.arg)
    if fn_def.args.kwarg:
        params.add(fn_def.args.kwarg.arg)
    return params


def _walk_params(node: ast.AST, params: Set[str], found: Set[str]) -> None:
    """Walk *node*'s children, collecting parameter-name reads.

    Skips nested FunctionDef / AsyncFunctionDef (they have their own params).
    Does NOT do transitive closure — parameter reads are collected from the
    adapter body only, not from utility functions the adapter calls.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id in params:
                found.add(child.id)
        _walk_params(child, params, found)


def _walk_tool_inputs(
    node: ast.AST,
    module_funcs: Dict[str, ast.FunctionDef],
    found: Set[str],
    seen: Set[str],
) -> None:
    """Walk *node*'s children, collecting conceptual inputs from tool calls.

    Skips nested FunctionDef / AsyncFunctionDef. Does transitive closure for
    same-module calls (cycle-guarded) so that a helper like _run_determinism_once
    calling resolve_pattern is detected from _run_determinism's body.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(child, ast.Call):
            name = _callee_name(child)
            if name and name in _TOOL_INPUTS:
                found |= _TOOL_INPUTS[name]
            if name and name in module_funcs and name not in seen:
                seen.add(name)
                _walk_tool_inputs(
                    module_funcs[name], module_funcs, found, seen
                )
        _walk_tool_inputs(child, module_funcs, found, seen)


def derive_view(fn) -> FrozenSet[str]:
    """AST-walk a function body, return the set of input names consumed.

    Returns parameter names actually read (excluding writes, imports), plus
    conceptual inputs detected via _TOOL_INPUTS tool-call patterns. Handles
    nested scopes (skip inner defs) and transitive calls within the same
    module.
    """
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        return frozenset()

    tree = ast.parse(textwrap.dedent(source))
    fn_def = tree.body[0] if tree.body else None
    if not isinstance(fn_def, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return frozenset()

    # Collect all module-level functions for transitive closure.
    module = inspect.getmodule(fn)
    module_funcs: Dict[str, ast.FunctionDef] = {}
    if module:
        try:
            mod_source = inspect.getsource(module)
            mod_tree = ast.parse(textwrap.dedent(mod_source))
            for node in mod_tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module_funcs[node.name] = node
        except (OSError, TypeError):
            pass

    params = _fn_params(fn_def)
    found: Set[str] = set()
    _walk_params(fn_def, params, found)
    _walk_tool_inputs(fn_def, module_funcs, found, set())
    return frozenset(found)


def check_all(module) -> List[Tuple[str, str]]:
    """For each VIEW_COVERAGE entry, compare derive_view against the declared set.

    Returns a list of ``(function_name, reason)`` mismatches. An empty list
    means every declaration agrees with its adapter body in BOTH directions:
    no undeclared reads AND no declared-but-unread inputs.
    """
    from forge_gate.lenses import VIEW_COVERAGE

    mismatches: List[Tuple[str, str]] = []
    for name, declared in VIEW_COVERAGE.items():
        fn = getattr(module, name, None)
        if fn is None:
            mismatches.append((name, f"function {name} not found in module"))
            continue
        derived = derive_view(fn)
        undeclared = derived - declared
        unread = declared - derived
        if undeclared:
            mismatches.append(
                (name, f"undeclared reads: {sorted(undeclared)}")
            )
        if unread:
            mismatches.append(
                (name, f"declared but unread: {sorted(unread)}")
            )
    return mismatches