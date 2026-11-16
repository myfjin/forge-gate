"""Build step 6b-2: PARITY of scout's chaos with the validator's ``chaos_scan.py`` (decision L3).

``pytest -m live``. Needs, and fails loudly without:
- ``FORGE_GATE_LIBRARY_DIR``: a git checkout of the pattern library that has commit 4b65044b;
- ``FORGE_GATE_CORPUS_EXPORT_DIR``: the real crew corpus export;
- ``rustc`` on PATH.

The validator is the ORIGINAL file, taken from the library's git history (blob checked). It runs
unmodified, as its own script, in a temp dir, with HOME pointed at a temp library. Scout runs its
child runner on the same library bytes. Layers:

1. probe level (no gate). The validator's ``scan(only)`` card verdict must equal the card
   verdict scout's child got. Every constructed case also asserts the verdict it was built to
   produce, so a case that stops reaching its path fails instead of passing quietly.
2. file level (with the gate). The validator's ``main`` must agree with scout's
   ``file_verdict`` on the same host:
   - ABORT <-> BLIND(the same NCs survived);
   - PASS <-> PASS;
   - INVALID-CANDIDATE <-> FAIL;
   - FLAG <-> BLIND.
3. stricter-than-validator (named conflict). With no rustc, the validator's gate passes
   vacuously. Scout's gate is unclean, so it says BLIND, never PASS.
4. learn end to end. A corpus export holding pinned library bytes:
   - learn writes a chaos PASS card exactly where the validator's ``main`` writes a PASS on this
     host, and only for the function the probe drives.
   The real export:
   - no item is probeable;
   - the validator has no probe for any item.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forge_gate import config
from forge_gate.scout import cards, corpus, learn
from forge_gate.scout.engines import chaos

sys.path.insert(0, str(Path(__file__).parent))
from test_scout_learn import Env, _export as _mk_export  # noqa: E402  (the 6b-1 fixtures)

pytestmark = [pytest.mark.live, pytest.mark.timeout(1800)]

DETERMINISTIC = [n for n in chaos.PINNED if n not in chaos.RACE]
# The validator's interpreter (review F3): the host's own python3 by default-of-the-run, set
# FG_TEST_VALIDATOR_PYTHON to it; scout's child always uses this interpreter (sys.executable).
VPY = os.environ.get("FG_TEST_VALIDATOR_PYTHON") or sys.executable
MAP = {"PASS": "PASS", "INVALID-CANDIDATE": "FAIL", "FLAG": "BLIND"}


# --- the two runners ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def library() -> Path:
    lib = config.library_dir()   # NotConfigured names the key: fails loudly
    return lib


@pytest.fixture(scope="module")
def validator_src(library) -> bytes:
    src = subprocess.run(["git", "-C", str(library), "show", f"{chaos.LIBRARY_COMMIT}:{chaos.SOURCE}"],
                         capture_output=True, check=True).stdout
    blob = subprocess.run(["git", "hash-object", "--stdin"], input=src, capture_output=True,
                          check=True).stdout.decode().strip()
    assert blob == chaos.SOURCE_BLOB, "the library's chaos_scan.py is not the source of record"
    assert shutil.which("rustc"), "rustc is not on PATH: the Rust NCs and probes cannot run"
    return src


def _stage(library: Path, root: Path, edits=None) -> Path:
    """A temp library (pinned files) under root/home/aura-pattern-library, with optional edits:
    {probe name: text appended to that probe's file}."""
    lib = root / "home" / "aura-pattern-library"
    chaos.stage_library(library, lib)
    for probe, extra in (edits or {}).items():
        p = lib / chaos.PINNED[probe][0]
        p.write_text(p.read_text() + extra)
    return lib


def validator(src: bytes, root: Path, args=(), scan_only=None, path=None) -> dict:
    """Run the ORIGINAL chaos_scan.py. ``scan_only`` calls its scan() directly (no gate)."""
    vdir = root / f"validator-{len(list(root.glob('validator-*')))}"
    vdir.mkdir()
    (vdir / "chaos_scan.py").write_bytes(src)
    tmp = vdir / "tmp"
    tmp.mkdir()
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("PYTHON")},
           "HOME": str(root / "home"), "TMPDIR": str(tmp)}
    if path is not None:
        env["PATH"] = path
    if scan_only is not None:
        cmd = [VPY, "-I", "-c",
               f"import sys; sys.path.insert(0, {str(vdir)!r}); import chaos_scan as c; "
               f"c.scan(only={{{scan_only!r}}}, verbose=False)"]
    else:
        cmd = [VPY, "-I", str(vdir / "chaos_scan.py"), *args]
    r = subprocess.run(cmd, cwd=vdir, env=env, capture_output=True, text=True, timeout=900)
    rep = vdir / "chaos_report.json"
    out = {"rc": r.returncode, "out": r.stdout, "err": r.stderr[-2000:],
           "aborted": "ABORT: chaos survival predicates are cosmetic." in r.stdout,
           "survived": sorted(l.split()[0] for l in r.stdout.splitlines() if "SURVIVED (BAD" in l),
           "cards": json.loads(rep.read_text())["cards"] if rep.exists() else None}
    return out


def scout(lib: Path, probe: str, root: Path, path=None) -> dict:
    work = root / f"scout-{probe}-{len(list(root.glob('scout-*')))}"
    work.mkdir()
    old = os.environ.get("PATH")
    if path is not None:
        os.environ["PATH"] = path
    try:
        return chaos.run_child(lib, probe, work)
    finally:
        if path is not None:
            os.environ["PATH"] = old


def scout_file(res: dict, probe: str) -> tuple:
    try:
        v, _card = chaos.file_verdict(res, probe)
        return v, ""
    except chaos.Blind as e:
        return "BLIND", str(e)


def _card(cards_, probe):
    rel = chaos.PINNED[probe][0]
    got = [c for c in cards_ if c["pattern"] == rel]
    assert len(got) == 1, (probe, cards_)
    return got[0]


RACE_NCS = set(chaos.RACE)


def assert_file_parity(v: dict, s: dict, probe: str):
    """Layer 2: the validator's main() vs scout's file_verdict, same host, same bytes.

    Scout's gate runs 3 times, the validator's once. A race NC can make them differ, but only in
    the safe direction: scout must see every NC the validator saw survive (so a validator ABORT
    is a scout BLIND), and anything extra it sees must be a race NC. That extra can turn a
    validator PASS into a scout BLIND, never the reverse. A deterministic NC must agree exactly."""
    sv, why = scout_file(s, probe)
    vs_, ss_ = set(v["survived"]), set(s["gate"]["survived"])
    assert vs_ <= ss_, ("the validator saw an NC survive that scout's 3 gate runs did not", vs_, ss_)
    assert ss_ - vs_ <= RACE_NCS, ("scout saw a deterministic NC survive that the validator did not", ss_ - vs_)
    if v["aborted"]:
        assert v["cards"] is None
        assert sv == "BLIND" and why.startswith("negative controls did not all die"), (sv, why)
    else:
        assert v["rc"] == 0, v["err"]
        want = MAP[_card(v["cards"], probe)["verdict"]]
        if ss_:
            assert sv == "BLIND" and why.startswith("negative controls did not all die"), (sv, why)
            print(f"\n  {probe}: validator {want}, scout BLIND (race NC {sorted(ss_)} survived a repeat)")
        else:
            assert sv == want, (probe, sv, why, _card(v["cards"], probe))
    return sv


# --- layer 1 + 2 on the pristine library: every deterministic probe ----------------------
def test_pristine_library_every_deterministic_probe(validator_src, library, tmp_path):
    lib = _stage(library, tmp_path)
    v_main = validator(validator_src, tmp_path)
    seen = {}
    for probe in DETERMINISTIC:
        s = scout(lib, probe, tmp_path)
        assert "error" not in s, s
        vs = validator(validator_src, tmp_path, scan_only=probe)
        assert s["card"]["verdict"] == _card(vs["cards"], probe)["verdict"], probe      # layer 1
        assert s["card"]["verdict"] == "PASS", (probe, s["card"])     # the pinned library survives
        seen[probe] = assert_file_parity(v_main, s, probe)               # layer 2 (+ F5: every probe)
        assert s["gate"]["runs"] == chaos.GATE_RUNS
        assert chaos.SUBJECT[probe] <= set(s["touched"]), (probe, s["touched"])   # subjects really driven
    print(f"\nvalidator python: {VPY}")
    print(f"\nhost gate: validator {'ABORT ' + str(v_main['survived']) if v_main['aborted'] else 'ok'}; "
          f"scout file verdicts {seen}")


# --- layer 1 + 2 on constructed cases ----------------------------------------------------
DIRECT = ('\n\ndef atomic_write(target_path, content):\n'
          '    with open(target_path, "wb") as f:\n        f.write(content)\n')
LITTER = ('\n\ndef atomic_write(target_path, content):\n'
          '    import os\n    tmp = target_path + ".tmp"\n'
          '    with open(tmp, "wb") as f:\n        f.write(content)\n'
          '    os.rename(tmp, target_path)\n')
CASES = {
    # name: (probe, appended text, the validator verdict the case is built to produce)
    "aw-direct-write": ("atomic_write_temp_rename", DIRECT, "INVALID-CANDIDATE"),
    "aw-temp-litter": ("atomic_write_temp_rename", LITTER, "INVALID-CANDIDATE"),
    "aw-api-missing": ("atomic_write_temp_rename", "\ndel atomic_write\n", "FLAG"),
    "aw-import-raises": ("atomic_write_temp_rename", "\nraise RuntimeError('import boom')\n", "FLAG"),
    "wal-replay-crashes": ("write_ahead_log",
                           "\nWAL.replay = lambda self, cb: (_ for _ in ()).throw(ValueError('torn'))\n",
                           "INVALID-CANDIDATE"),
    "etl-not-idempotent": ("idempotent_etl_step",
                           "\ndef idempotent_transform(records, checkpoint_path):\n"
                           "    return [dict(r, n=i) for i, r in enumerate(list(records) * 2)]\n",
                           "INVALID-CANDIDATE"),
    "rust-tamper-no-compile": ("tamper_evident_log", "\nfn broken( {\n", "FLAG"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_constructed_case(validator_src, library, tmp_path, case):
    probe, extra, want = CASES[case]
    lib = _stage(library, tmp_path, {probe: extra})
    vs = validator(validator_src, tmp_path, scan_only=probe)
    got = _card(vs["cards"], probe)["verdict"]
    assert got == want, f"case {case} no longer produces {want} in the validator: {got} ({vs['err'][-300:]})"
    s = scout(lib, probe, tmp_path)
    assert "error" not in s, s
    assert s["card"]["verdict"] == got                                   # layer 1
    v_main = validator(validator_src, tmp_path, args=(probe,))
    sv = assert_file_parity(v_main, s, probe)                            # layer 2
    assert sv != "PASS"
    if case == "rust-tamper-no-compile" and not v_main["aborted"]:
        # the same compile error kills the tamper NC: the validator counts it as died, scout
        # marks it unclean. Both end not-PASS for this file; scout's reason is the gate.
        assert s["gate"]["unclean"].get(probe) == "rust compile failed", s["gate"]


# --- layer 3: scout stricter than the validator (named conflict) --------------------------
def test_without_rustc_the_validator_gate_passes_vacuously_scout_says_blind(validator_src, library, tmp_path):
    rustc = shutil.which("rustc")
    path = os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep)
                           if d and not (Path(d) / "rustc").exists() and not (Path(d) / "cargo").exists())
    assert shutil.which("rustc", path=path) is None and rustc
    lib = _stage(library, tmp_path)
    probe = "atomic_write_temp_rename"
    v = validator(validator_src, tmp_path, args=(probe,), path=path)
    assert not v["aborted"] and _card(v["cards"], probe)["verdict"] == "PASS", v["out"][-800:]
    s = scout(lib, probe, tmp_path, path=path)
    sv, why = scout_file(s, probe)
    assert sv == "BLIND" and why.startswith("a negative control did not run cleanly"), (sv, why)
    rust = sorted(n for n, (rel, _) in chaos.PINNED.items() if rel.startswith("rust/"))
    assert sorted(s["gate"]["unclean"]) == rust


# --- layer 4: learn end to end ------------------------------------------------------------
def _export(root: Path, bodies: dict) -> Path:
    items = [{"item_id": f"c_{hashlib.sha256(b.encode()).hexdigest()[:16]}", "body": b,
              "lang": "python", "kind": "write",
              "file": f"corpus/python/misc/{name}.py"} for name, b in bodies.items()]
    exp = root / "pinned-export" / "v1"
    exp.mkdir(parents=True)
    _mk_export(exp, items)
    return exp


@pytest.fixture
def learn_env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(e.data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    # KD-12 (j) is off only in tests; this machine is seeded as calibrated under its REAL id and
    # fingerprint, so the latch is the only per-machine rule in play here.
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)
    from test_scout_chaos import seed_calibrated
    seed_calibrated(e.scout, mid=chaos.machine_id(), fp=chaos.fingerprint())
    chaos._CACHE.clear()
    yield e
    chaos._CACHE.clear()


def test_learn_on_pinned_bytes_matches_the_validator_on_this_host(validator_src, library, tmp_path,
                                                                 learn_env, monkeypatch):
    probes = ["atomic_write_temp_rename", "idempotent_etl_step", "checkpoint_resume",
              "point_in_time_restore", "idempotent_once_guard"]
    bodies = {p: (library / chaos.PINNED[p][0]).read_text() for p in probes}
    exp = _export(tmp_path, bodies)
    monkeypatch.setenv(config.CORPUS_KEY, str(exp))
    _stage(library, tmp_path)
    v_main = validator(validator_src, tmp_path)
    latched = f"BLIND(machine {chaos.machine_id()[:8]}"
    hpath = learn_env.scout / chaos.HISTORY_NAME
    scout_survived = lambda: set(json.loads(hpath.read_text())["machines"][chaos.machine_id()]["survived"])
    compared, compared_pass = [], []       # review F5: what was really compared, not just looped over
    for p in probes:
        f = tmp_path / f"{p}.py"
        f.write_text(bodies[p])
        res = learn.run(f, "4Q")
        assert res.code == learn.EXIT_OK, res.outcome
        verdicts = {fn["entry"]: fn["engines"]["chaos"] for fn in res.report["functions"]}
        if p in chaos.RACE:
            assert all(x.startswith("BLIND(race/timing probe") for x in verdicts.values()), verdicts
            continue
        if v_main["aborted"]:
            assert all(x.startswith(("BLIND(negative controls did not all die", latched))
                       for x in verdicts.values()), verdicts
            compared.append(p)
            continue
        if scout_survived():
            # a race NC survived in scout's runs but not in the validator's one run: scout is BLIND,
            # the stricter side (the same rule as layer 2); only a race NC may cause this
            assert scout_survived() <= RACE_NCS, scout_survived()
            assert all(x.startswith(("BLIND(negative controls did not all die", latched))
                       for x in verdicts.values()), verdicts
            print(f"\n{p}: scout latched by a race NC the validator's run did not see: {scout_survived()}")
            continue
        want = MAP[_card(v_main["cards"], p)["verdict"]]
        assert want == "PASS"
        driven = {e for e, x in verdicts.items() if not x.endswith(f"is not a subject the {p} probe drove)")}
        assert all(verdicts[e] == want for e in driven), verdicts
        assert driven == chaos.SUBJECT[p], (p, verdicts)
        if p == "idempotent_etl_step":
            assert verdicts["stable_checksum"].startswith("BLIND(")   # the fault site (F1)
        if p == "point_in_time_restore":
            assert verdicts["vals"].startswith("BLIND(")              # the measuring tool (F1)
        compared.append(p)
        compared_pass.append(p)
    if not compared:
        pytest.skip("scout latched on a race NC before any probe was compared: parity NOT measured "
                    "on this run (review F5)")
    hist = json.loads(hpath.read_text())["machines"][chaos.machine_id()]
    print(f"\nmachine history after learn: {hist}")
    assert hist["configs"][chaos.config_key(chaos.fingerprint())]["gate_runs"] >= chaos.CALIBRATION_N + chaos.GATE_RUNS
    if v_main["aborted"]:
        assert set(v_main["survived"]) <= set(hist["survived"]), hist   # the host is now on record
    rep = learn_env.scout / learn.REPORT_NAME
    got = cards.load_report(rep) if rep.exists() else []
    assert all(c["kind"] == "chaos" for c in got)
    if not v_main["aborted"] and not hist["survived"]:
        assert sorted(c["id"].rsplit("::", 1)[1] for c in got) == [
            "atomic_write", "idempotent_transform", "process_batch_with_checkpoint", "restore_to"]
        assert all(c["verdict"] == "PASS" for c in got)
    elif v_main["aborted"]:
        assert got == []
    else:                                   # latched mid-test by a race NC: fewer cards, all PASS
        assert all(c["verdict"] == "PASS" for c in got)
    if not v_main["aborted"]:               # exactly the compared probes' subjects carry cards (F5)
        assert sorted(c["id"].rsplit("::", 1)[1] for c in got) == sorted(
            e for p in compared_pass for e in chaos.SUBJECT[p])


def test_real_corpus_no_item_is_probeable_and_the_validator_has_no_probe_for_any(validator_src):
    intake = corpus.read_export()          # FORGE_GATE_CORPUS_EXPORT_DIR, fails loudly if unset
    assert intake.outcome.kind == "FOUND", intake.outcome.label()
    assert len(intake.items) > 1000
    rels = {rel for rel, _sha in chaos.PINNED.values()}
    probe_paths = [i["file"] for i in intake.items if any(i["file"].endswith(r) for r in rels)]
    probeable = [i["item_id"] for i in intake.items if chaos.probe_for(i["body"].encode("utf-8"))]
    assert probe_paths == [] and probeable == []
    print(f"\nreal export {intake.run_id}: {len(intake.items)} items, 0 probeable, 0 at a probe path")
