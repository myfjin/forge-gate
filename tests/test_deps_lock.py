"""deps.lock grades the config loader's outside routes (standard v0.3 §2, §6).

Prints host, config file and CODE; refuses to grade when CODE is not the project home.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from forge_gate import config

REPO = Path(__file__).resolve().parents[1]
CLASSES = {"dependency", "host-binary", "runtime-root"}
# Keys config.py resolves outside the project. In-project keys (FORGE_GATE_HOME,
# FORGE_GATE_SCRATCH under home, FORGE_GATE_CONFIG) are classed by construction.
OUTSIDE_KEYS = {"FORGE_GATE_LIBRARY_DIR", "FORGE_GATE_CORPUS_EXPORT_DIR", "FORGE_GATE_PYTHON", "FORGE_GATE_NODE",
                "FORGE_GATE_BUN", "FORGE_GATE_QJS"}


def _rows():
    if config.tomllib is None:
        pytest.fail("no TOML parser: Python < 3.11 needs tomli (declared in pyproject)")
    with (REPO / "deps.lock").open("rb") as fh:
        return config.tomllib.load(fh)["route"]


def test_grades_only_from_the_project_home():
    print(config.where())
    assert config.home() == REPO, f"refusing to grade: CODE/home is not this checkout ({config.where()})"


def test_every_outside_key_has_a_classed_row_and_no_row_is_stale():
    rows = _rows()
    keys = {r["key"] for r in rows}
    src = (REPO / "src" / "forge_gate" / "config.py").read_text(encoding="utf-8")
    in_loader = set(re.findall(r'"(FORGE_GATE_[A-Z_]+)"', src)) | {
        "FORGE_GATE_" + n.upper() for n in ("node", "bun", "qjs")}
    assert OUTSIDE_KEYS <= in_loader, OUTSIDE_KEYS - in_loader
    missing = OUTSIDE_KEYS - keys
    assert not missing, f"outside keys with no deps.lock row: {sorted(missing)}"
    stale = {k for k in keys if k.startswith("FORGE_GATE_")} - in_loader
    assert not stale, f"deps.lock rows for keys config.py does not have: {sorted(stale)}"
    for r in rows:
        assert r.get("class") in CLASSES, r
        assert r.get("pin") and r.get("pinned"), r


def test_library_pin_matches_the_reference_origin():
    pin = next(r["pin"] for r in _rows() if r["key"] == "FORGE_GATE_LIBRARY_DIR")
    origin = (REPO / "src" / "forge_gate" / "reference" / "ORIGIN.md").read_text(encoding="utf-8")
    commit = pin.split()[-1]
    assert re.fullmatch(r"[0-9a-f]{40}", commit), pin
    assert commit in origin, "deps.lock library pin and reference/ORIGIN.md disagree"
