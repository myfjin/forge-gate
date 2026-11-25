"""The bug planter for the GitHub fact tool (GitHub-lane loop S3, DESIGN github-lane-2026-11 §5 R5).

It plants small, single-node bugs into ONE top-level function of a Python source file, so the
fact tool can ask whether the project's own tests notice them. It never runs anything.

How a mutant is made (R5):
- ``ast`` finds the candidate nodes inside the function's body;
- ``tokenize`` finds the operator token inside the node's span. ``ast`` gives operators no
  position, and ``col_offset`` counts UTF-8 BYTES, while tokenize counts characters;
- the change is spliced into the ORIGINAL SOURCE BYTES, so everything outside the token stays
  byte-identical (docstrings and their doctests included; ``ast.unparse`` would rewrite them).

Operators: comparison flip (``<`` <-> ``>=``, ``==`` <-> ``!=``, ``is`` <-> ``is not``,
``in`` <-> ``not in``), ``+`` <-> ``-``, an integer constant + 1, ``return <expr>`` ->
``return None``, ``and`` <-> ``or`` (every operator token of one BoolOp).

Self-check per mutant: the mutant compiles, and its AST differs from the original's in EXACTLY
ONE node, of the stated operator class. Otherwise it is dropped and counted as ``not_planted``.
A candidate anywhere inside an ``if`` (or a conditional expression) whose test reads
``sys.version_info``, ``sys.hexversion``, ``hexversion`` or ``version_info`` is not planted and is
counted as ``version_gated`` (unreachable on the host's Python, so it could only "survive" falsely).

At most :data:`MAX_MUTANTS` per function, spread evenly over the valid ones in source order. Ids
are ``<function>:<op>:<line>:<col>`` (line 1-based, col the 0-based character column of the
token), stable for the same bytes.
"""
from __future__ import annotations

import ast
import difflib
import io
import tokenize
from dataclasses import dataclass, field

MAX_MUTANTS = 12
OPS = ("cmp", "arith", "const", "return", "bool")
VERSION_NAMES = frozenset({"version_info", "hexversion"})

_CMP_TEXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
             ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}
_CMP_FLIP = {ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.Eq: ast.NotEq,
             ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
_CMP_WORDS = frozenset({"<", "<=", ">", ">=", "==", "!=", "is", "not", "in"})


class PlantError(Exception):
    """The source cannot be planted at all (not UTF-8, does not parse, no such function)."""


@dataclass
class Mutant:
    id: str
    op: str
    line: int
    col: int
    source: bytes          # the whole mutated file
    diff: str              # unified diff of the changed line(s)


@dataclass
class Plan:
    function: str
    mutants: list = field(default_factory=list)
    candidates: int = 0           # valid mutants before the cap
    capped: int = 0               # valid mutants left out by the cap
    not_planted: int = 0          # failed the self-check (or no token found)
    version_gated: int = 0
    dropped: list = field(default_factory=list)   # [(id, why)] for not_planted


@dataclass
class _Cand:
    op: str
    start: int             # absolute byte offsets of the span to replace
    end: int
    new: bytes
    line: int
    col: int               # character column, for the id
    check: object          # callable(orig_node, mut_node) -> bool on the single diff

    def splice(self, source: bytes) -> bytes:
        return source[:self.start] + self.new + source[self.end:]


def functions(source: bytes) -> list[str]:
    """Top-level ``def`` names in source order (methods and async functions are not planted)."""
    tree = _parse(source)
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def plant(source: bytes, function: str, max_mutants: int = MAX_MUTANTS) -> Plan:
    tree = _parse(source)
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    if len(defs) != 1:
        raise PlantError(f"{function}: {len(defs)} top-level definitions (need exactly one)")
    fn = defs[0]
    lines = source.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))
    toks = _tokens(source, lines, starts)
    plan = Plan(function)
    gated = _gated_nodes(fn)
    valid = []
    for node in _body_nodes(fn):
        for cand in _candidates(node, toks, lines, starts):
            if id(node) in gated:
                plan.version_gated += 1
                continue
            if cand is None:              # the token was not where the node says: not planted
                plan.not_planted += 1
                plan.dropped.append((f"{function}:?:{getattr(node, 'lineno', 0)}", "no-token"))
                continue
            mid = f"{function}:{cand.op}:{cand.line}:{cand.col}"
            new = cand.splice(source)
            why = _self_check(tree, new, cand)
            if why:
                plan.not_planted += 1
                plan.dropped.append((mid, why))
                continue
            valid.append(Mutant(mid, cand.op, cand.line, cand.col, new, _diff(source, new)))
    valid.sort(key=lambda m: (m.line, m.col, m.op))
    plan.candidates = len(valid)
    if len(valid) > max_mutants:
        keep = sorted({i * len(valid) // max_mutants for i in range(max_mutants)})
        plan.capped = len(valid) - len(keep)
        valid = [valid[i] for i in keep]
    plan.mutants = valid
    return plan


# --- parsing and positions -------------------------------------------------------------------

def _parse(source: bytes) -> ast.Module:
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PlantError(f"not UTF-8: {e}") from None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as e:
        raise PlantError(f"does not parse: {e}") from None


def _tokens(source, lines, starts) -> list[tuple[int, int, str, int]]:
    """(abs_byte_start, abs_byte_end, string, type) for every token on one line."""
    out = []
    for t in tokenize.tokenize(io.BytesIO(source).readline):
        if t.type in (tokenize.ENCODING, tokenize.ENDMARKER) or t.start[0] != t.end[0]:
            continue
        row = t.start[0]
        if row > len(lines):
            continue
        text = lines[row - 1].decode("utf-8")
        b0 = starts[row - 1] + len(text[:t.start[1]].encode("utf-8"))
        b1 = starts[row - 1] + len(text[:t.end[1]].encode("utf-8"))
        out.append((b0, b1, t.string, t.type))
    return out


def _abs(lines, starts, lineno, col_bytes) -> int:
    return starts[lineno - 1] + col_bytes


def _char_col(lines, abs_byte, starts) -> tuple[int, int]:
    for i in range(len(lines)):
        if starts[i] <= abs_byte < starts[i + 1]:
            return i + 1, len(lines[i][:abs_byte - starts[i]].decode("utf-8", "replace"))
    return len(lines), 0


def _span(node, lines, starts) -> tuple[int, int]:
    return (_abs(lines, starts, node.lineno, node.col_offset),
            _abs(lines, starts, node.end_lineno, node.end_col_offset))


def _between(toks, lo, hi, words) -> list:
    return [t for t in toks if lo <= t[0] and t[1] <= hi and t[2] in words]


def _body_nodes(fn: ast.FunctionDef):
    for stmt in fn.body:
        yield from ast.walk(stmt)


def _reads_version(test: ast.AST) -> bool:
    for n in ast.walk(test):
        if isinstance(n, ast.Attribute) and n.attr in VERSION_NAMES:
            return True
        if isinstance(n, ast.Name) and n.id in VERSION_NAMES:
            return True
    return False


def _gated_nodes(fn: ast.FunctionDef) -> set:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.If, ast.IfExp)) and _reads_version(n.test):
            out.update(id(x) for x in ast.walk(n))
    return out


# --- candidates ----------------------------------------------------------------------------------

def _candidates(node, toks, lines, starts):
    """Yield _Cand (or None when the node's operator token cannot be found) for one node."""
    if isinstance(node, ast.Compare):
        prev = node.left
        for op, right in zip(node.ops, node.comparators):
            lo, hi = _span(prev, lines, starts)[1], _span(right, lines, starts)[0]
            found = _between(toks, lo, hi, _CMP_WORDS)
            text = " ".join(t[2] for t in found)
            if not found or text != _CMP_TEXT[type(op)]:
                yield None
            else:
                want = _CMP_FLIP[type(op)]
                yield _cand("cmp", found[0][0], found[-1][1], _CMP_TEXT[want], lines, starts,
                            lambda a, b, o=type(op), w=want: type(a) is o and type(b) is w)
            prev = right
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        lo, hi = _span(node.left, lines, starts)[1], _span(node.right, lines, starts)[0]
        found = _between(toks, lo, hi, {"+", "-"})
        sym = "+" if isinstance(node.op, ast.Add) else "-"
        if len(found) != 1 or found[0][2] != sym:
            yield None
        else:
            want = ast.Sub if sym == "+" else ast.Add
            yield _cand("arith", found[0][0], found[0][1], "-" if sym == "+" else "+", lines, starts,
                        lambda a, b, o=type(node.op), w=want: type(a) is o and type(b) is w)
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        lo, hi = _span(node, lines, starts)
        found = [t for t in toks if t[0] == lo and t[1] == hi and t[3] == tokenize.NUMBER]
        if len(found) != 1:
            yield None
        else:
            v = node.value
            yield _cand("const", lo, hi, str(v + 1), lines, starts,
                        lambda a, b, v=v: (isinstance(a, ast.Constant) and isinstance(b, ast.Constant)
                                           and type(a.value) is int and type(b.value) is int
                                           and a.value == v and b.value == v + 1))
    elif isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None):
        lo, hi = _span(node.value, lines, starts)
        yield _cand("return", lo, hi, "None", lines, starts,
                    lambda a, b, orig=node.value: (a is orig and isinstance(b, ast.Constant)
                                                   and b.value is None))
    elif isinstance(node, ast.BoolOp):
        found = []
        for left, right in zip(node.values, node.values[1:]):
            lo, hi = _span(left, lines, starts)[1], _span(right, lines, starts)[0]
            found += _between(toks, lo, hi, {"and", "or"})
        sym = "and" if isinstance(node.op, ast.And) else "or"
        if len(found) != len(node.values) - 1 or any(t[2] != sym for t in found):
            yield None
        else:
            yield _BoolCand(found, "or" if sym == "and" else "and", lines, starts, type(node.op))


def _cand(op, b0, b1, new_text, lines, starts, check) -> _Cand:
    line, col = _char_col(lines, b0, starts)
    return _Cand(op, b0, b1, new_text.encode("utf-8"), line, col, check)


class _BoolCand(_Cand):
    """Several tokens of one BoolOp, all flipped: spliced as one span from first to last token."""

    def __init__(self, found, new_word, lines, starts, orig_cls):
        line, col = _char_col(lines, found[0][0], starts)
        want = ast.Or if orig_cls is ast.And else ast.And
        super().__init__("bool", found[0][0], found[-1][1], b"", line, col,
                         lambda a, b: type(a) is orig_cls and type(b) is want)
        self._found = found
        self._word = new_word.encode("utf-8")

    def splice(self, source: bytes) -> bytes:
        out, pos = [], self.start
        for b0, b1, _s, _t in self._found:
            out.append(source[pos:b0])
            out.append(self._word)
            pos = b1
        return source[:self.start] + b"".join(out) + source[self.end:]


# --- self-check ------------------------------------------------------------------------------------

def _self_check(orig_tree, new: bytes, cand: _Cand) -> str | None:
    try:
        mut_tree = ast.parse(new)
        compile(mut_tree, "<mutant>", "exec")
    except (SyntaxError, ValueError) as e:
        return f"does-not-compile: {e.__class__.__name__}"
    diffs = []
    _diffs(orig_tree, mut_tree, diffs)
    if len(diffs) != 1:
        return f"ast-differs-in-{len(diffs)}-places"
    a, b = diffs[0]
    if not cand.check(a, b):
        return f"wrong-node-class: {type(a).__name__}->{type(b).__name__}"
    return None


def _diffs(a, b, out: list) -> None:
    """The top-most differing node pairs of two ASTs (positions ignored)."""
    if type(a) is not type(b):
        out.append((a, b))
        return
    if isinstance(a, ast.AST):
        for f in a._fields:
            va, vb = getattr(a, f, None), getattr(b, f, None)
            if isinstance(va, list) and isinstance(vb, list):
                if len(va) != len(vb):
                    out.append((a, b))
                    continue
                for x, y in zip(va, vb):
                    _diffs(x, y, out)
            elif isinstance(va, ast.AST) or isinstance(vb, ast.AST):
                _diffs(va, vb, out)
            elif type(va) is not type(vb) or va != vb:
                out.append((a, b))
                return
    elif a != b:
        out.append((a, b))


def _diff(old: bytes, new: bytes) -> str:
    return "".join(difflib.unified_diff(old.decode("utf-8").splitlines(keepends=True),
                                        new.decode("utf-8").splitlines(keepends=True), n=0))
