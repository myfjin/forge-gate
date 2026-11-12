"""The shell gates in tests/verify_*.sh, run by the test runner (standard v0.3 §6:
every self-test is in the runner or in KNOWN-DEBT.md).

Each gate runs with PYTHONPATH and FORGE_GATE_HOME set to THIS checkout, so it measures
this checkout's code even when another forge_gate is installed (on the deploy host au's
user site had an editable install of the live repo; a branch's gate run measured master
through it, 2026-11-11).

Tiers (measured 2026-11-11 on both hosts, master and build-1 branch):
- default: fast (under 5 s) and green;
- `gates`: slow (1 to 15 minutes): pytest -m gates;
- `js`: needs `npm ci` (tsc) and three JS engines: pytest -m js.
Not here: verify_input_robustness.sh, verify_js_selftest.sh, verify_no_duplicate_bodies.sh
take files as arguments; they are tools, not self-tests. verify_debug_core_purity.sh is
run by tests/test_purity_gate.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

FAST = ["verify_domain_classifier", "verify_domain_guards", "verify_portable_paths",
        "verify_python_ast_api", "verify_shape_guard"]
SLOW = ["verify_report", "verify_guard_lens", "verify_guard_lens_isolation",
        "verify_determinism_lens"]
JS = ["verify_js_oracle", "verify_js_families"]


def _run(name: str, timeout: int) -> None:
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"), FORGE_GATE_HOME=str(REPO))
    p = subprocess.run(["bash", str(REPO / "tests" / f"{name}.sh")], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((p.stdout + p.stderr).splitlines()[-25:])
    assert p.returncode == 0, f"{name}.sh rc={p.returncode}\n{tail}"


@pytest.mark.parametrize("name", FAST)
def test_fast_gate(name):
    _run(name, timeout=120)


@pytest.mark.gates
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("name", SLOW)
def test_slow_gate(name):
    _run(name, timeout=1700)


@pytest.mark.js
@pytest.mark.timeout(600)
@pytest.mark.parametrize("name", JS)
def test_js_gate(name):
    _run(name, timeout=500)


def test_every_shell_gate_is_accounted_for():
    """A new verify_*.sh must be wired here, in test_purity_gate.py, or named as a tool."""
    tools = {"verify_input_robustness", "verify_js_selftest", "verify_no_duplicate_bodies"}
    wired = set(FAST) | set(SLOW) | set(JS) | {"verify_debug_core_purity"} | tools
    found = {p.stem for p in (REPO / "tests").glob("verify_*.sh")}
    assert found == wired, f"unaccounted: {sorted(found - wired)}; gone: {sorted(wired - found)}"
