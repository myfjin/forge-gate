"""Test that tests/verify_debug_core_purity.sh catches every documented
bypass shape. Regression for 6E review round-2 Finding 2 (2026-09-07):
the previous grep rule `^(from|import)...` missed indented and dynamic
imports; five bypass spellings passed the gate with exit 0."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "tests" / "verify_debug_core_purity.sh"
CANONICAL = REPO_ROOT / "src" / "forge_gate" / "debug_core.py"


def _run_gate_against(target: Path) -> int:
    """Run the gate against a specific TARGET file by pointing SRC to a
    fake repo root whose src/forge_gate/debug_core.py IS that file. The
    gate resolves TARGET as $ROOT/src/forge_gate/debug_core.py from its
    own dirname, so we invoke via a temp tree."""
    proc = subprocess.run(
        ["bash", str(GATE)],
        capture_output=True,
        text=True,
    )
    return proc.returncode


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Build a temp tree matching the layout the gate expects, symlink the
    gate script in, and return the debug_core.py path for the test to
    write into."""
    fake_root = tmp_path / "fake-forge-gate"
    (fake_root / "src" / "forge_gate").mkdir(parents=True)
    (fake_root / "tests").mkdir()
    gate_copy = fake_root / "tests" / "verify_debug_core_purity.sh"
    shutil.copy(GATE, gate_copy)
    gate_copy.chmod(0o755)
    dc = fake_root / "src" / "forge_gate" / "debug_core.py"
    dc.write_text(CANONICAL.read_text())
    return dc, gate_copy


def _exit(gate: Path) -> int:
    return subprocess.run(["bash", str(gate)], capture_output=True, text=True).returncode


def test_gate_green_on_canonical(fake_repo):
    """Sanity: the canonical debug_core.py passes the gate."""
    _, gate = fake_repo
    assert _exit(gate) == 0


BYPASSES = {
    "indented_from_dotted":
        "if True:\n    from forge_gate.lenses import LensResult\n",
    "indented_from_nondotted":
        "if True:\n    from forge_gate import lenses\n",
    "function_local_import":
        "def f():\n    import forge_gate.unsupported\n",
    "importlib_dynamic":
        "import importlib\nm = importlib.import_module('forge_gate.lenses')\n",
    "dunder_import_dynamic":
        "m = __import__('forge_gate.unsupported')\n",
    "wildcard_from_forge_gate":
        "from forge_gate import *\n",
}


@pytest.mark.parametrize("name,snippet", list(BYPASSES.items()))
def test_gate_catches_bypass(fake_repo, name, snippet):
    """Every documented bypass must make the gate RED (exit 1)."""
    dc, gate = fake_repo
    dc.write_text(dc.read_text() + "\n" + snippet)
    assert _exit(gate) == 1, f"bypass {name!r} not caught by gate"
