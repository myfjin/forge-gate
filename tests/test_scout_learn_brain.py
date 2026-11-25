"""Build step 6b-1: what `learn` writes reaches the Brain's canonical through the scout reader, stays
candidate, and survives a rebuild of every real ladder (6E's decision A, 2026-11-13; review F3).

Marker ``brain``, deselected by default. It needs, and FAILS (never skips) without:

- ``FG_TEST_BRAIN_SRC``: a COPY of the Brain tree WITH the scout reader (6be79ec + the draft in
  /home/me/ai-lab/fg6b1/DRAFT-brain-scout-reader/ until 6E lands it). It must not be a git checkout,
  so a live tree can never be named here by mistake;
- ``FG_TEST_BRAIN_SRC_OLD``: a copy of the DEPLOYED Brain (6be79ec), for the red arm that shows it
  cannot see scout's files (why the reader must be deployed before learn is switched on);
- ``FG_TEST_BRAIN_DATA``: a folder holding the four real ladders and the canonical (copied into
  tmp_path; the folder itself is only read);
- ``FG_TEST_VALIDATOR_DIR``: the validator reports the build reads (only read);
- ``FG_TEST_BRAIN_PYTHON``: the interpreter for the Brain copies (default: this one).

Every run of a Brain copy prints where ``aura_brain`` was imported from and the data, registry and
validator paths it resolved; the test asserts each is that copy / tmp_path (path proof).
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

from forgeproof import config
from forgeproof.scout import cards, learn

from test_scout_learn import Env, engines_with  # noqa: E402  (same fixtures as the unit arms)

pytestmark = pytest.mark.brain

COPY = learn.REAL_LADDERS + ("logic_registry_canonical.jsonl", "regate_absence.json")

PROBE = (
    "import sys, json\n"
    "import aura_brain\n"
    "from aura_brain import paths\n"
    "print('PATHPROOF ' + json.dumps({'aura_brain': aura_brain.__file__, 'DATA': str(paths.DATA),\n"
    "      'LOGIC_REGISTRY': str(paths.LOGIC_REGISTRY), 'VALIDATOR_DIR': str(paths.VALIDATOR_DIR),\n"
    "      'CANONICAL': str(paths.LOGIC_REGISTRY_CANONICAL),\n"
    "      'EXTRA': [str(p) for p in paths.configured_list('AURA_SCOUT_REPORT_CARDS')]}), flush=True)\n"
    "from aura_brain.conscience import build_registry\n"
    "build_registry.main([])\n"
)


def _need(name: str) -> Path:
    v = os.environ.get(name, "")
    if not v:
        pytest.fail(f"{name} is not set: the brain arm needs COPIES of the Brain (see docstring)")
    return Path(v).resolve()


@pytest.fixture
def brain(tmp_path):
    b = {"src": _need("FG_TEST_BRAIN_SRC"), "old": _need("FG_TEST_BRAIN_SRC_OLD"),
         "data_from": _need("FG_TEST_BRAIN_DATA"), "val": _need("FG_TEST_VALIDATOR_DIR"),
         "python": os.environ.get("FG_TEST_BRAIN_PYTHON") or sys.executable}
    for k in ("src", "old"):
        if (b[k] / ".git").exists():
            pytest.fail(f"{b[k]} is a git checkout; give a `git archive` copy, never a live tree")
    if not (b["src"] / "src/aura_brain/paths.py").read_text().count("SCOUT_REGISTRIES"):
        pytest.fail(f"FG_TEST_BRAIN_SRC={b['src']} has no scout reader (paths.SCOUT_REGISTRIES)")
    if (b["old"] / "src/aura_brain/paths.py").read_text().count("SCOUT_REGISTRIES"):
        pytest.fail(f"FG_TEST_BRAIN_SRC_OLD={b['old']} already has the scout reader")
    for f in COPY[:5]:
        if not (b["data_from"] / f).is_file():
            pytest.fail(f"FG_TEST_BRAIN_DATA has no {f}")
    return b


def _scratch(tmp: Path, brain: dict, name: str) -> Path:
    d = tmp / name / "data"
    d.mkdir(parents=True)
    for f in COPY:
        if (brain["data_from"] / f).is_file():
            shutil.copy2(brain["data_from"] / f, d / f)
    for f in learn.SCOUT_FILES:          # the switch-on creates them empty (KD-11 k)
        (d / f).write_text("")
    return d


def _build(brain: dict, data: Path, extra: list, home: Path, src: Path | None = None):
    src = src or brain["src"]
    home.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
           "PYTHONPATH": str(src / "src"), "AURA_BRAIN_HOME": str(src),
           "AURA_BRAIN_DATA": str(data), "AURA_VALIDATOR_DIR": str(brain["val"]),
           "AURA_SCOUT_REPORT_CARDS": os.pathsep.join(str(p) for p in extra),
           "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([brain["python"], "-c", PROBE], env=env, capture_output=True, text=True,
                       timeout=600, cwd=str(home))
    proof = None
    for line in p.stdout.splitlines():
        if line.startswith("PATHPROOF "):
            proof = json.loads(line[len("PATHPROOF "):])
    print(p.stdout[-3000:], p.stderr[-3000:], sep="\n--- stderr ---\n")
    assert proof is not None, "the Brain copy printed no path proof"
    assert Path(proof["aura_brain"]).resolve().is_relative_to(src), proof
    assert Path(proof["DATA"]) == data and Path(proof["LOGIC_REGISTRY"]) == data / "logic_registry.jsonl"
    assert Path(proof["CANONICAL"]) == data / "logic_registry_canonical.jsonl"
    assert Path(proof["VALIDATOR_DIR"]) == brain["val"]
    assert [Path(x) for x in proof["EXTRA"]] == [Path(x) for x in extra]
    return p


def _rows(p: Path) -> dict:
    return {d["id"]: d for d in (json.loads(l) for l in p.read_text().splitlines() if l.strip())}


def _md5s(data: Path, names) -> dict:
    return {n: hashlib.md5((data / n).read_bytes()).hexdigest() for n in names}


def _learn(tmp_path, monkeypatch, data: Path):
    e = Env.__new__(Env)
    e.tmp = tmp_path / "learn"
    e.tmp.mkdir()
    e.scout = e.tmp / "scout"
    e.scout.mkdir()
    e.lock = e.tmp / "build.lock"
    e.lock.write_text("")
    e.export = e.tmp / "export" / "v1"
    e.export.mkdir(parents=True)
    from test_scout_learn import _export, _items
    _export(e.export, _items())
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    monkeypatch.setenv(config.CORPUS_KEY, str(e.export))
    res = learn.run(e.member(0), "4Q", engines=engines_with(chaos="FAIL", metamorphic="FAIL"))
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 2, res.outcome
    return e.scout / learn.REPORT_NAME, [f["id"] for f in res.report["functions"]]


def _all_candidate(canon: dict, ids):
    for i in ids:
        d = canon[i]
        assert d["oracle"]["tier"] == "candidate" and d["oracle"]["backed"] is False, d["oracle"]
        assert d["is_axiom"] is False and d["basis"] == "green-run-candidate"


def test_learned_rows_reach_the_canonical_through_the_scout_reader_and_stay_candidate(
        brain, tmp_path, monkeypatch):
    data = _scratch(tmp_path, brain, "arm1")
    real = _md5s(data, learn.REAL_LADDERS)
    report, ids = _learn(tmp_path, monkeypatch, data)
    assert _md5s(data, learn.REAL_LADDERS) == real            # learn wrote no real ladder
    assert set(_rows(data / "scout_registry.jsonl")) == set(ids)
    scout = _md5s(data, learn.SCOUT_FILES)

    p = _build(brain, data, [report], tmp_path / "home1")
    assert p.returncode == 0 and "Traceback" not in p.stderr
    # the reader's "card(s)" counts DISTINCT card ids: 2 functions x (chaos, metamorphic) FAIL
    assert "extra source: 1 file(s), 2 card(s); backed 0 row(s); unmatched 0" in p.stdout
    assert "scout source: 4 file(s), 2 row(s); added 2; shadowed by a real row 0" in p.stdout
    assert len(json.loads(report.read_text())["cards"]) == 4
    canon = _rows(data / "logic_registry_canonical.jsonl")
    _all_candidate(canon, ids)
    assert not set(ids) & set(_rows(data / "logic_registry.jsonl"))   # never in a real ladder
    assert _md5s(data, learn.SCOUT_FILES) == scout                     # the Brain never writes them


def test_rebuilding_every_real_ladder_keeps_the_learned_rows(brain, tmp_path, monkeypatch):
    """F3 under decision A: what each extractor does (rows from its own source only, write_text)."""
    data = _scratch(tmp_path, brain, "arm2")
    report, ids = _learn(tmp_path, monkeypatch, data)
    assert _build(brain, data, [report], tmp_path / "home2a").returncode == 0
    for n in learn.REAL_LADDERS:
        own = [d for d in _rows(data / n).values() if not str(d["id"]).startswith("scout:")]
        (data / n).write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in own))
    p = _build(brain, data, [report], tmp_path / "home2b")
    assert p.returncode == 0 and "unmatched 0" in p.stdout
    _all_candidate(_rows(data / "logic_registry_canonical.jsonl"), ids)


def test_control_a_planted_pass_card_moves_exactly_its_row(brain, tmp_path, monkeypatch):
    data = _scratch(tmp_path, brain, "arm3")
    report, ids = _learn(tmp_path, monkeypatch, data)
    planted = tmp_path / "planted_pass.json"
    cards.write_report(planted, [cards.make_card(
        ids[0], kind="chaos", verdict="PASS", technique="planted-by-test", fixture="-",
        expected="-", actual="-", repro_cmd="test control arm; learn cannot write this")])
    p = _build(brain, data, [report, planted], tmp_path / "home3")
    assert p.returncode == 0, p.stderr
    assert "card(s); backed 1 row(s); unmatched 0" in p.stdout
    canon = _rows(data / "logic_registry_canonical.jsonl")
    assert canon[ids[0]]["oracle"]["tier"] == "hard"
    assert canon[ids[1]]["oracle"]["tier"] == "candidate"


def test_red_the_deployed_brain_cannot_see_scouts_files(brain, tmp_path, monkeypatch):
    """Why the reader ships BEFORE learn is switched on: 6be79ec leaves every card unmatched."""
    data = _scratch(tmp_path, brain, "arm4")
    report, ids = _learn(tmp_path, monkeypatch, data)
    p = _build(brain, data, [report], tmp_path / "home4", src=brain["old"])
    assert p.returncode == 0, p.stderr
    assert "unmatched 2" in p.stdout and "scout source" not in p.stdout
    assert not set(ids) & set(_rows(data / "logic_registry_canonical.jsonl"))


def test_red_cards_without_kind_break_the_build(brain, tmp_path, monkeypatch):
    data = _scratch(tmp_path, brain, "arm5")
    report, _ = _learn(tmp_path, monkeypatch, data)
    d = json.loads(report.read_text())
    for c in d["cards"]:
        del c["kind"]
    stripped = tmp_path / "no_kind.json"
    stripped.write_text(json.dumps(d))
    p = _build(brain, data, [stripped], tmp_path / "home5")
    assert p.returncode != 0 and "unknown kind None" in p.stderr


def test_red_a_missing_scout_file_is_reported_by_name(brain, tmp_path, monkeypatch):
    data = _scratch(tmp_path, brain, "arm6")
    report, _ = _learn(tmp_path, monkeypatch, data)
    (data / "scout_registry_r.jsonl").unlink()
    p = _build(brain, data, [report], tmp_path / "home6")
    assert p.returncode == 0, p.stderr
    assert "scout source: 3 file(s)" in p.stdout and "MISSING" in p.stdout
    assert str(data / "scout_registry_r.jsonl") in p.stdout


def test_red_a_non_scout_id_in_a_scout_file_stops_the_build(brain, tmp_path, monkeypatch):
    data = _scratch(tmp_path, brain, "arm7")
    report, _ = _learn(tmp_path, monkeypatch, data)
    some_real = next(iter(_rows(data / "logic_registry.jsonl")))
    with (data / "scout_registry.jsonl").open("a") as fh:
        fh.write(json.dumps({"id": some_real}) + "\n")
    p = _build(brain, data, [report], tmp_path / "home7")
    assert p.returncode != 0 and "not a scout id" in p.stderr


def test_a_chaos_pass_written_by_learn_itself_makes_exactly_its_row_hard(brain, tmp_path, monkeypatch):
    """Step 6b-2, KD-11 (f): the card learn's BUILT-IN chaos engine writes (not a planted one) is
    read by the reader copy and moves its row, and only its row, to hard. The child run is stubbed
    with a clean PASS result: the real child's verdicts are tested against the validator in
    test_scout_chaos_parity.py. The gate in learn._check is the real one."""
    from forgeproof.scout.engines import chaos
    from test_scout_learn import PY_BODY

    data = _scratch(tmp_path, brain, "arm-chaos")
    sha = hashlib.sha256(PY_BODY.encode()).hexdigest()
    monkeypatch.setitem(chaos._BY_SHA, sha, "atomic_write_temp_rename")
    monkeypatch.setitem(chaos.SUBJECT, "atomic_write_temp_rename", frozenset({"area"}))
    res_stub = {"probe": "atomic_write_temp_rename", "gate": {"ok": True, "survived": [], "unclean": {}, "runs": chaos.GATE_RUNS},
                "touched": ["area"],
                "card": {"pattern": chaos.PINNED["atomic_write_temp_rename"][0], "verdict": "PASS",
                         "technique": "chaos-fault-injection", "severity": "info", "class": "chaos-survival",
                         "fixture": "crash before rename commit", "expected": "e",
                         "actual": "survival predicate held", "oracle_coverage_kind": "chaos"}}
    monkeypatch.setattr(chaos, "result_for", lambda body, probe: res_stub)
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)        # KD-12 (j) is off only in tests
    from test_scout_chaos import FP, MID, seed_calibrated
    monkeypatch.setattr(chaos, "machine_id", lambda: MID)
    monkeypatch.setattr(chaos, "fingerprint", lambda: dict(FP))
    e = Env.__new__(Env)
    e.tmp = tmp_path / "learn"
    e.tmp.mkdir()
    e.scout = e.tmp / "scout"
    e.scout.mkdir()
    seed_calibrated(e.scout)
    e.lock = e.tmp / "build.lock"
    e.lock.write_text("")
    e.export = e.tmp / "export" / "v1"
    e.export.mkdir(parents=True)
    from test_scout_learn import _export, _items
    _export(e.export, _items())
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    monkeypatch.setenv(config.CORPUS_KEY, str(e.export))
    res = learn.run(e.member(0), "4Q")                 # the built-in engines: no injection
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 1, res.outcome
    (area,) = [f["id"] for f in res.report["functions"] if f["entry"] == "area"]
    report = e.scout / learn.REPORT_NAME
    p = _build(brain, data, [report], tmp_path / "home-chaos")
    assert p.returncode == 0, p.stderr
    assert "card(s); backed 1 row(s); unmatched 0" in p.stdout
    canon = _rows(data / "logic_registry_canonical.jsonl")
    assert canon[area]["oracle"]["tier"] == "hard" and canon[area]["oracle"]["backed"] is True
    assert sorted(canon[area]["oracle"]["kinds"]) == ["chaos"]
    scale = [f["id"] for f in res.report["functions"] if f["entry"] == "scale"][0]
    assert scale not in canon                          # BLIND everywhere: no row was written
