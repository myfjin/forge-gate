"""Route tests (project standard v0.3 §1-§3, §6; ForgeProof build step 1, 2026-11-11).

Every route out of the project goes through ``forgeproof.config``. These tests need no
library, no network and no particular $HOME: they run on fixtures only.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

from forgeproof import config
from forgeproof.catalog import reference_dir, reference_path, resolve_pattern, list_patterns

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def no_library(monkeypatch, tmp_path):
    """Key unset in the environment, and a config.toml that does not set it."""
    monkeypatch.delenv(config.LIBRARY_KEY, raising=False)
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(tmp_path / "absent-config.toml"))


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
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(cfg))
    assert config.library_dir() == lib


def test_environment_beats_config_toml(monkeypatch, tmp_path):
    env_lib = tmp_path / "env-lib"
    env_lib.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[paths]\n{config.LIBRARY_KEY} = "nowhere"\n', encoding="utf-8")
    monkeypatch.setenv("FORGEPROOF_CONFIG", str(cfg))
    monkeypatch.setenv(config.LIBRARY_KEY, str(env_lib))
    assert config.library_dir() == env_lib


def test_verify_resolves_shipped_references_without_the_library(no_library):
    for name in list_patterns():
        meta = resolve_pattern(name)
        assert meta["aura_path"] == reference_path(name).resolve()
        assert meta["aura_path"].is_file()


def test_twin_lens_is_blind_and_names_the_key_when_library_unset(no_library):
    from forgeproof.lens_tools import twin_consensus
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
    for root in (REPO / "src" / "forgeproof", REPO / "tools"):
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".json", ".sh", ".toml"} and "__pycache__" not in p.parts:
                yield p


# Ported engine files keep their source byte for byte (design §2.3); their header cites the
# source path and the frozen body may name paths of its old home. Each is md5-pinned by its own
# test and runs only inside the isolation box. The list is exact, so it cannot grow unnoticed.
# step 6b-2 adds the chaos port (review item, named in the 6b-2 ledger row): its LIB default is
# never used; chaos_runner.py points LIB at a staged copy of the pinned library.
PORTED_EXEMPT = {"src/forgeproof/scout/engines/executor_port.py",
                 "src/forgeproof/scout/engines/chaos_port.py"}


def test_ported_exemptions_are_exact_and_carry_their_header():
    assert PORTED_EXEMPT == {"src/forgeproof/scout/engines/executor_port.py",
                             "src/forgeproof/scout/engines/chaos_port.py"}   # pinned: widening it is a review item
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
        if p.name == "config.py" and p.parent.name == "forgeproof":
            continue
        if str(p.relative_to(REPO)) in PORTED_EXEMPT and p.read_text().startswith("# Ported from "):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if str(p.relative_to(REPO)) == CANARY_FILE and line == canary:
                continue
            if _HOME_PATH.search(line) or _HOME_CALL.search(line):
                hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()[:100]}")
    assert not hits, "home paths outside forgeproof/config.py:\n" + "\n".join(hits)


# ── KD-2 (done 2026-11-25): the lens tools ship in the package ─────────────────────────────────
# Exact statuses for one good file, all lenses, with no checkout, no FORGEPROOF_HOME, no library.
# "Not ERROR" would not be enough: a wheel without invariant_contracts/ makes invariant UNSUPPORTED.
KD2_PATTERN = "linear_regression_gd"
KD2_GOOD = REPO / "examples" / "linear_regression_gd_good.py"
KD2_WANT = {"property-primary": "PASS", "metamorphic": "PASS", "invariant": "PASS",
            "twin-consensus": "UNSUPPORTED", "determinism": "PASS", "guard": "PASS"}
KD2_TOOLS = ("metamorphic/runner.py", "metamorphic/relations.py", "invariant_gate.py",
             "twin_consensus.py", "guard_lens.py", "invariant_contracts/linear_regression_gd.json",
             "invariant_contracts/polynomial_regression.json",
             "invariant_contracts/ridge_regression_normal_eq.json")


def test_every_lens_runs_without_a_checkout(monkeypatch, tmp_path, no_library):
    """In-process: config finds no checkout and FORGEPROOF_HOME is unset (the conftest sets it,
    so it is removed here), cwd is elsewhere and no tools/ is on sys.path. Every lens runs, and
    each tool module loads from forgeproof/lens_tools/ of the package under test."""
    import forgeproof
    from forgeproof import lens_tools, lenses
    from forgeproof.cli import main

    monkeypatch.delenv("FORGEPROOF_HOME", raising=False)
    monkeypatch.setattr(config, "CODE", tmp_path / "not-a-checkout")
    assert config.home() is None
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if not p.rstrip("/").endswith("/tools")])
    for top in ("metamorphic", "invariant_gate", "twin_consensus", "guard_lens"):
        assert top not in sys.modules or not str(getattr(sys.modules[top], "__file__", "")).endswith(".py"), top

    pkg_tools = Path(forgeproof.__file__).resolve().parent / "lens_tools"
    assert lens_tools.HERE == pkg_tools and lenses.GUARD_LENS == pkg_tools / "guard_lens.py"
    for rel in KD2_TOOLS:
        assert (pkg_tools / rel).is_file(), rel

    import io, json, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["verify", str(KD2_GOOD), "--pattern", KD2_PATTERN, "--json"])
    d = json.loads(buf.getvalue())
    got = {l["lens"]: l["status"] for l in d["lenses"]}
    assert got == KD2_WANT, got
    assert (rc, d["status"]) == (0, "PASS"), (rc, d["status"], d.get("reason"))
    twin = next(l for l in d["lenses"] if l["lens"] == "twin-consensus")
    assert config.LIBRARY_KEY in json.dumps(twin)          # blind for the library, not for tools
    assert "FORGEPROOF_HOME" not in buf.getvalue()
    from forgeproof.lens_tools import invariant_gate, twin_consensus
    from forgeproof.lens_tools.metamorphic import runner
    for m in (invariant_gate, twin_consensus, runner):
        assert Path(m.__file__).resolve().is_relative_to(pkg_tools), m.__file__


def test_a_built_wheel_runs_every_lens_with_no_checkout(tmp_path):
    """The install route itself: build the wheel from a copy of this tree with the declared
    backend (no network, no pip), unpack it as a site-packages, and run `forgeproof verify` from
    it in a child with -I, an empty HOME, cwd outside, and no FORGEPROOF_* or FORGE_GATE_* variable."""
    import json, os, shutil, subprocess, zipfile

    src = tmp_path / "src-copy"
    for rel in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        if (REPO / rel).is_file():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, src / rel)
    shutil.copytree(REPO / "src", src / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc"))
    wheels = tmp_path / "dist"
    wheels.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FORGEPROOF_", "FORGE_GATE_")) and k != "PYTHONPATH"}
    # The BUILD sees the interpreter's normal site (pip would bring its own setuptools; this test
    # has no network). It must be one that reads [project]: setuptools 59 (Ubuntu 22.04's, seen on
    # the deploy host under -I, 2026-11-25) builds a wheel with no packages at all and exits 0.
    build = subprocess.run(
        [sys.executable, "-c",
         "import sys, setuptools; from setuptools import build_meta as b; "
         "v = int(setuptools.__version__.split('.')[0]); "
         "assert v >= 61, f'setuptools {setuptools.__version__} at {setuptools.__file__} cannot read [project]; need >=61'; "
         "print(b.build_wheel(sys.argv[1]))", str(wheels)],
        cwd=str(src), env=dict(env, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=300)
    assert build.returncode == 0, build.stderr[-2000:]
    # the RUN is isolated: -I, an empty HOME, cwd outside, no FORGEPROOF_* / FORGE_GATE_*
    env.update(HOME=str(tmp_path / "home"), PYTHONDONTWRITEBYTECODE="1")
    (tmp_path / "home").mkdir()
    whl = wheels / build.stdout.strip().splitlines()[-1]
    names = set(zipfile.ZipFile(whl).namelist())
    missing = [r for r in KD2_TOOLS if f"forgeproof/lens_tools/{r}" not in names]
    assert not missing, f"not in the wheel: {missing}"
    assert not any(n.startswith("tools/") for n in names), "the dev tools/ must not ship"

    site = tmp_path / "site"
    zipfile.ZipFile(whl).extractall(site)
    out = tmp_path / "cwd"
    out.mkdir()
    boot = ("import sys; sys.path.insert(0, sys.argv[1]); import forgeproof; "
            "print('IMPORTED', forgeproof.__file__, file=sys.stderr); "
            "from forgeproof.cli import main; sys.exit(main(sys.argv[2:]))")
    run = subprocess.run(
        [sys.executable, "-I", "-c", boot, str(site), "verify", str(KD2_GOOD), "--pattern", KD2_PATTERN, "--json"],
        cwd=str(out), env=env, capture_output=True, text=True, timeout=300)
    imported = [l for l in run.stderr.splitlines() if l.startswith("IMPORTED ")]
    assert imported and imported[0].split(" ", 1)[1].startswith(str(site)), run.stderr[-1000:]
    d = json.loads(run.stdout)
    got = {l["lens"]: l["status"] for l in d["lenses"]}
    assert got == KD2_WANT, (got, run.stderr[-1000:])
    assert (run.returncode, d["status"]) == (0, "PASS"), (run.returncode, d["status"], d.get("reason"))
    assert "FORGEPROOF_HOME" not in run.stdout + run.stderr
