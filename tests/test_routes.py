"""Route tests (project standard v0.3 §1-§3, §6; Forge Gate build step 1, 2026-11-11).

Every route out of the project goes through ``forge_gate.config``. These tests need no
library, no network and no particular $HOME: they run on fixtures only.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

from forge_gate import config
from forge_gate.catalog import reference_dir, reference_path, resolve_pattern, list_patterns

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def no_library(monkeypatch, tmp_path):
    """Key unset in the environment, and a config.toml that does not set it."""
    monkeypatch.delenv(config.LIBRARY_KEY, raising=False)
    monkeypatch.setenv("FORGE_GATE_CONFIG", str(tmp_path / "absent-config.toml"))


def test_library_key_unset_fails_naming_the_key(no_library):
    with pytest.raises(config.NotConfigured) as exc:
        config.library_dir()
    assert config.LIBRARY_KEY in str(exc.value)
    assert "config=" in str(exc.value)  # names where it looked


def test_library_key_from_config_toml_is_relative_to_the_config(monkeypatch, tmp_path):
    if config.tomllib is None:
        pytest.fail("no TOML parser: Python < 3.11 needs tomli (declared in pyproject)")
    lib = tmp_path / "lib"
    lib.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[paths]\n{config.LIBRARY_KEY} = "lib"\n', encoding="utf-8")
    monkeypatch.delenv(config.LIBRARY_KEY, raising=False)
    monkeypatch.setenv("FORGE_GATE_CONFIG", str(cfg))
    assert config.library_dir() == lib


def test_environment_beats_config_toml(monkeypatch, tmp_path):
    env_lib = tmp_path / "env-lib"
    env_lib.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[paths]\n{config.LIBRARY_KEY} = "nowhere"\n', encoding="utf-8")
    monkeypatch.setenv("FORGE_GATE_CONFIG", str(cfg))
    monkeypatch.setenv(config.LIBRARY_KEY, str(env_lib))
    assert config.library_dir() == env_lib


def test_verify_resolves_shipped_references_without_the_library(no_library):
    for name in list_patterns():
        meta = resolve_pattern(name)
        assert meta["aura_path"] == reference_path(name).resolve()
        assert meta["aura_path"].is_file()


def test_twin_lens_is_blind_and_names_the_key_when_library_unset(no_library):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import twin_consensus
    finally:
        sys.path.remove(str(REPO / "tools"))
    tc = twin_consensus.consensus_verify(reference_path("perceptron"), "perceptron", timeout=60)
    assert tc.status == "UNSUPPORTED", tc
    assert config.LIBRARY_KEY in tc.reason


_ORIGIN_ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \| `([0-9a-f]{64})` \|$")


def _origin_rows():
    text = (reference_dir() / "ORIGIN.md").read_text(encoding="utf-8")
    return [m.groups() for m in map(_ORIGIN_ROW.match, text.splitlines()) if m]


def test_reference_copies_match_their_origin_record():
    rows = _origin_rows()
    assert len(rows) == len(list_patterns()) == 10
    for fname, _libpath, sha in rows:
        got = hashlib.sha256((reference_dir() / fname).read_bytes()).hexdigest()
        assert got == sha, f"{fname}: sha256 {got} != ORIGIN.md {sha}"


# §6 path lint, as a test. Absolute home paths and home lookups may appear only in the
# loader (config.py). Comments count: a path in a comment is how the next edit copies it.
_HOME_PATH = re.compile(r"/(home|Users)/[A-Za-z0-9_.-]+/")
_HOME_CALL = re.compile(r"Path\.home\(\)|expanduser\(\s*[\"']~")


def _shipped_files():
    for root in (REPO / "src" / "forge_gate", REPO / "tools"):
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".json", ".sh", ".toml"} and "__pycache__" not in p.parts:
                yield p


# Ported engine files keep their source byte for byte (design §2.3); their header cites the
# source path and the frozen body may name paths of its old home. Each is md5-pinned by its own
# test and runs only inside the isolation box. The list is exact, so it cannot grow unnoticed.
PORTED_EXEMPT = {"src/forge_gate/scout/engines/executor_port.py"}


def test_ported_exemptions_are_exact_and_carry_their_header():
    assert PORTED_EXEMPT == {"src/forge_gate/scout/engines/executor_port.py"}   # pinned: widening it is a review item
    for rel in PORTED_EXEMPT:
        assert (REPO / rel).read_text().startswith("# Ported from "), rel


# Red-arm DATA, not a route: the private publisher (tools/publish_public.py, never published) plants
# a canary to prove its scanner refuses a home path. Exempt: that file's single `CANARY = "` line,
# so any other home path in it still fails. Absent file (the public tree): nothing is exempt.
CANARY_FILE, CANARY_PREFIX = "tools/publish_public.py", 'CANARY = "'


def _canary_line():
    f = REPO / CANARY_FILE
    if not f.exists():
        return None
    lines = [l for l in f.read_text().splitlines() if l.startswith(CANARY_PREFIX)]
    assert len(lines) == 1, f"{CANARY_FILE}: want exactly one canary line, found {len(lines)}"
    return lines[0]


def test_canary_exemption_is_one_line():
    _canary_line()


def test_no_home_paths_outside_the_loader():
    hits, canary = [], _canary_line()
    for p in _shipped_files():
        if p.name == "config.py" and p.parent.name == "forge_gate":
            continue
        if str(p.relative_to(REPO)) in PORTED_EXEMPT and p.read_text().startswith("# Ported from "):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if str(p.relative_to(REPO)) == CANARY_FILE and line == canary:
                continue
            if _HOME_PATH.search(line) or _HOME_CALL.search(line):
                hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()[:100]}")
    assert not hits, "home paths outside forge_gate/config.py:\n" + "\n".join(hits)


def test_lenses_outside_a_checkout_name_forge_gate_home(monkeypatch, tmp_path):
    """An install without a checkout: tool lenses go ERROR naming FORGE_GATE_HOME and
    evaluate() still returns a verdict (it must not crash)."""
    from forge_gate import lenses

    monkeypatch.setattr(lenses, "TOOLS_DIR", tmp_path / "no-tools")
    # Other tests leave the real tools/ on sys.path and its modules cached; remove both,
    # or this test passes alone and fails in the suite (seen 2026-11-11).
    real_tools = str(REPO / "tools")
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p.rstrip("/") != real_tools])
    for mod in [m for m in sys.modules if m == "metamorphic" or m.startswith("metamorphic.")]:
        monkeypatch.delitem(sys.modules, mod)
    r = lenses._run_metamorphic(reference_path("perceptron"), "perceptron")
    assert r.status == "ERROR"
    assert "FORGE_GATE_HOME" in r.reason
    g = lenses._run_guard(reference_path("perceptron"), 60)
    assert g.status == "ERROR" and "FORGE_GATE_HOME" in g.reason
