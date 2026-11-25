"""Named writers (design §4a.5 V4-2; DECISIONS-8a G3): each ledger writer may be used only from
its own place, so scout's search path can never write the outcome ledger.

An AST scan of ``src/forgeproof``: any import, name, attribute or string literal naming a writer
counts as a use. Named limit (KD-14): a string built at run time (``getattr(ledger, "record_" +
x)``) is not seen; the row check at read time is the second layer.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "forgeproof"
# Also scanned (review F2): the repo's own tools and scripts, which import forgeproof too.
EXTRA = (REPO / "tools", REPO / "scripts")
WRITERS = ("record_exposure", "record_oracle", "record_mark", "record_event",
           # the hooks that call them are pinned the same way, so a writer's named place cannot be
           # reached from elsewhere through its hook (scout's run cannot call the oracle hook)
           "_expose", "_oracle_outcome", "_oracle_notes")
# The ledger's internals (review F2): a direct _append(_row(...)) skips every writer rule and
# writes a row that LOOKS like a valid writer's. No use outside ledger.py is allowed at all.
PRIVATE = ("_append", "_row", "_locked", "_run", "EXPOSURE_NAME", "OUTCOME_NAME", "LOCK_NAME",
           # the path canonicaliser (shared-tools S1): others use forgeproof.paths, not these copies
           "_real", "_canon", "_under")
FILE_NAMES = ("exposure.jsonl", "outcome.jsonl", "ledger.lock")
# writer -> {(file relative to src/forgeproof, top-level function)}. Grows slice by slice (s2-s4).
ALLOWED: dict = {
    "record_exposure": {("scout/report.py", "_expose")},          # s2
    "record_oracle": {("cli.py", "_oracle_notes")},               # s3 (inside _oracle_outcome)
    "_expose": {("scout/report.py", "run")},
    "_oracle_outcome": {("cli.py", "cmd_verify")},
    "_oracle_notes": {("cli.py", "_oracle_outcome")},
    "record_mark": {("cli.py", "cmd_ledger")},                    # s4
    "record_event": {("cli.py", "cmd_ledger")},                   # s4
}
OWNER = "ledger.py"


def _ledger_aliases(tree) -> set:
    """Local names bound to the ledger module in one file."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "ledger":
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "ledger":
                    names.add(a.asname or a.name.split(".")[0])
    return names


def writer_uses(src: Path, owner: str | None = OWNER) -> list:
    """Every (file, top-level function or '<module>', name) use outside ``ledger.py``: a writer named
    anywhere, a ledger internal reached through the module or imported from it, a ledger file name."""
    out = []
    for f in sorted(src.rglob("*.py")):
        rel = f.relative_to(src).as_posix()
        if rel == owner or "__pycache__" in rel:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        aliases = _ledger_aliases(tree)
        docs = {id(n.value) for n in ast.walk(tree)          # a bare string statement is prose
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for top in tree.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else "<module>"
            for node in ast.walk(top):
                names = []
                if isinstance(node, ast.Name):
                    names = [node.id]
                elif isinstance(node, ast.Attribute):
                    names = [node.attr]
                elif isinstance(node, ast.alias):
                    names = [node.name, node.asname or ""]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                    names = [node.value]
                for n in names:
                    if n in WRITERS:
                        out.append((rel, where, n))
                if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "ledger":
                    for a in node.names:
                        if a.name == "*" or a.name in PRIVATE:
                            out.append((rel, where, a.name if a.name in PRIVATE else "*"))
                if isinstance(node, ast.Attribute) and node.attr in PRIVATE and (
                        (isinstance(node.value, ast.Name) and node.value.id in aliases)
                        or (isinstance(node.value, ast.Attribute) and node.value.attr == "ledger")):
                    out.append((rel, where, node.attr))
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                        and any(fn in node.value for fn in FILE_NAMES):
                    out.append((rel, where, "<file name>"))
    return out


def violations(uses, allowed) -> list:
    return [(rel, where, n) for rel, where, n in uses
            if n not in allowed or (rel, where) not in allowed[n]]


def test_each_writer_is_used_only_from_its_named_place():
    bad = violations(writer_uses(SRC), ALLOWED)
    assert bad == [], f"ledger writer used outside its named place: {bad}"


def test_tools_and_scripts_use_no_writer_and_no_internal():
    uses = [u for root in EXTRA if root.is_dir() for u in writer_uses(root, owner=None)]
    assert uses == [], f"ledger reached from tools/ or scripts/: {uses}"


def test_violations_keep_only_the_named_place():
    allowed = {w: set() for w in WRITERS}
    allowed["record_oracle"] = {("cli.py", "cmd_verify")}
    uses = [("cli.py", "cmd_verify", "record_oracle"),          # its named place
            ("cli.py", "cmd_scout", "record_oracle"),           # same file, other function
            ("scout/report.py", "run", "record_oracle"),        # the search path
            ("cli.py", "cmd_verify", "record_mark"),            # named place of ANOTHER writer
            ("cli.py", "cmd_verify", "_append"),                # an internal, even from a named place
            ("x.py", "<module>", "*")]
    assert violations(uses, allowed) == uses[1:]


def test_the_scan_sees_every_form_of_use(tmp_path):
    """Red arm for the scan itself: each planted form must be reported."""
    (tmp_path / "scout").mkdir()
    (tmp_path / "ledger.py").write_text("def record_oracle(): pass\n")       # the owner: ignored
    (tmp_path / "scout" / "report.py").write_text(textwrap.dedent("""
        from forgeproof.ledger import record_oracle
        from forgeproof import ledger as L
        from ..ledger import record_mark as rm
        from ..ledger import *
        from forgeproof.ledger import _append
        import forgeproof.ledger
        def run():
            L.record_event()
            getattr(L, "record_exposure")()
            L._append(p, [L._row("oracle", None)])
            forgeproof.ledger._locked(d, 1)
            L._real(p), L._canon(p), L._under(p, d)
            open(d / "outcome.jsonl", "ab")
        def other():
            _append = 1                        # a local name, not the ledger's: not a use
            learn._append(x, y)                # another module's _append: not a use
    """))
    got = sorted(writer_uses(tmp_path))
    assert got == sorted([
        ("scout/report.py", "<module>", "record_oracle"),
        ("scout/report.py", "<module>", "record_mark"),
        ("scout/report.py", "<module>", "*"),
        ("scout/report.py", "<module>", "_append"),
        ("scout/report.py", "run", "record_event"),
        ("scout/report.py", "run", "record_exposure"),
        ("scout/report.py", "run", "_append"),
        ("scout/report.py", "run", "_row"),
        ("scout/report.py", "run", "_locked"),
        ("scout/report.py", "run", "_real"),
        ("scout/report.py", "run", "_canon"),
        ("scout/report.py", "run", "_under"),
        ("scout/report.py", "run", "<file name>"),
    ])


def test_allowed_places_name_real_files():
    for w, places in ALLOWED.items():
        assert w in WRITERS
        for rel, _fn in places:
            assert (SRC / rel).is_file(), rel
