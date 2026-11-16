"""Build step 6b-2: scout's chaos engine, default tier (fixtures only, no library, no rustc).

The child runner's real verdicts are compared with the validator's in
tests/test_scout_chaos_parity.py (``-m live``). Here the rules around the child are checked with
constructed child results:
- only bytes pinned to a validator probe reach the child, and race/timing probes never do;
- PASS needs: a clean gate (no NC survived, none unclean), a card for this probe, verdict PASS,
  and a function the probe drove;
- INVALID-CANDIDATE -> FAIL; FLAG, an error, a timeout or a card for another probe -> BLIND;
- the runner's own gate wrapper marks a raised / uncompiled NC as unclean (scout is stricter
  than the validator there) and reports a survived NC.
Each arm writes through learn.run with the BUILT-IN engines, so learn's verdict gate is the real one.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from forge_gate import config
from forge_gate.scout import cards, learn
from forge_gate.scout.engines import chaos, chaos_runner

sys.path.insert(0, str(Path(__file__).parent))
from test_scout_learn import PY_BODY, Env, _rows  # noqa: E402  (the 6b-1 fixtures)

PROBE = "atomic_write_temp_rename"
REL = chaos.PINNED[PROBE][0]
SHA = hashlib.sha256(PY_BODY.encode()).hexdigest()


MID = "0123456789abcdef0123456789abcdef"          # a fixed machine id for the unit tier
FP = {"python": "3.x", "rustc": "rustc 1.0", "port_md5": "0" * 32, "cpu": "test x8", "os": "Test 1"}
_REAL_MACHINE_ID = chaos.machine_id


def seed_calibrated(scout: Path, mid=MID, fp=FP, clean=chaos.CALIBRATION_N, survived=None):
    """A v2 history in which (mid, fp) has ``clean`` clean gate runs (and optional survivals)."""
    h = {"version": chaos.HISTORY_VERSION, "machines": {mid: {
        "hostname": "seeded", "survived": dict(survived or {}),
        "configs": {chaos.config_key(fp): {"fingerprint": fp, "gate_runs": clean, "clean_runs": clean}}}}}
    (scout / chaos.HISTORY_NAME).write_text(json.dumps(h))


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setattr(chaos, "machine_id", lambda: MID)
    monkeypatch.setattr(chaos, "fingerprint", lambda: dict(FP))
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(e.data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    monkeypatch.setenv(config.CORPUS_KEY, str(e.export))
    chaos._CACHE.clear()
    yield e
    chaos._CACHE.clear()


def _gate(ok=True, survived=(), unclean=None, runs=chaos.GATE_RUNS):
    return {"ok": ok, "survived": list(survived), "unclean": dict(unclean or {}), "runs": runs}


def _card(verdict="PASS", pattern=REL, kind="chaos"):
    c = {"pattern": pattern, "technique": "chaos-fault-injection", "fixture": "crash before rename commit",
         "expected": "dest==OLD (no partial) & no temp litter", "verdict": verdict,
         "oracle_coverage_kind": kind, "verdict_target": "PATTERN", "confidence": "high"}
    if verdict == "PASS":
        c.update(severity="info", **{"class": "chaos-survival"}, actual="survival predicate held")
    elif verdict == "INVALID-CANDIDATE":
        c.update(severity="high", **{"class": "safety-theater"}, actual="died to its own fault: x",
                 note="MUST NOT auto-flip a frozen exam verdict; contradiction => injection miscalibrated")
    else:
        c.update(**{"class": "chaos-harness-error"}, actual="AttributeError: no atomic_write")
    return c


def _res(card=None, gate=None, touched=("area",), probe=PROBE):
    return {"probe": probe, "gate": gate or _gate(), "card": card or _card(), "touched": list(touched)}


@pytest.fixture
def pinned(env, monkeypatch):
    """Pin the fixture body to the atomic-write probe, let learn take library copies (KD-12 j is
    off only here), mark this machine calibrated, and stub the child; returns the call log."""
    calls = []
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)
    seed_calibrated(env.scout)
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.setitem(chaos.SUBJECT, PROBE, frozenset({"area"}))   # the fixture's function
    state = {"res": _res()}

    def fake(body, probe):
        calls.append(probe)
        if isinstance(state["res"], Exception):
            raise state["res"]
        return state["res"]
    monkeypatch.setattr(chaos, "result_for", fake)
    return calls, state


def _report(env):
    p = env.scout / learn.REPORT_NAME
    return cards.load_report(p) if p.exists() else []


# --- the PASS path, and what it writes ----------------------------------------------------
def test_pass_backs_only_the_function_the_probe_drove(env, pinned):
    calls, _ = pinned
    res = learn.run(env.member(0), "4Q")
    assert res.code == learn.EXIT_OK, res.outcome
    assert calls == [PROBE, PROBE]                       # asked once per function (cache: below)
    rows = _rows(env.scout_main)
    assert [r["signature"]["entry"] for r in rows] == ["area"]
    r = rows[0]
    assert r["strength"] == "weak" and r["verified_run"] is True
    assert r["oracle"]["tier"] == "candidate"            # learn never promotes; honest_pass does
    assert "survived the validator's chaos probe" in r["belief_sentence"]["text"]
    (c,) = _report(env)
    assert (c["id"], c["kind"], c["verdict"], c["class"], c["severity"]) == (
        r["id"], "chaos", "PASS", "chaos-survival", "info")
    assert c["technique"] == "chaos-fault-injection" and c["actual"] == "survival predicate held"
    scale = [f for f in res.report["functions"] if f["entry"] == "scale"][0]
    assert scale["engines"]["chaos"] == f"BLIND(scale is not a subject the {PROBE} probe drove)"


def test_a_function_the_probe_read_but_is_not_its_subject_is_blind(env, pinned):
    """Review F1: touched alone is not enough (ETL's stable_checksum is read to inject the
    fault, PITR's vals to measure). Only SUBJECT ∩ touched is backed."""
    _, state = pinned
    state["res"] = _res(touched=("area", "scale"))
    res = learn.run(env.member(0), "4Q")
    assert [r["signature"]["entry"] for r in _rows(env.scout_main)] == ["area"]
    scale = [f for f in res.report["functions"] if f["entry"] == "scale"][0]
    assert scale["engines"]["chaos"] == f"BLIND(scale is not a subject the {PROBE} probe drove)"


def test_a_subject_the_run_did_not_record_is_blind(env, pinned):
    _, state = pinned
    state["res"] = _res(touched=())
    assert learn.run(env.member(0), "4Q").outcome.startswith("0 rows")


def test_subjects_are_never_the_measuring_tool_or_the_fault_site():
    assert chaos.SUBJECT["point_in_time_restore"] == {"restore_to"}
    assert chaos.SUBJECT["idempotent_etl_step"] == {"idempotent_transform"}
    assert all(not v for k, v in chaos.SUBJECT.items() if k in chaos.RACE or chaos.PINNED[k][0].startswith("rust/"))


def test_invalid_candidate_becomes_fail_with_the_validators_fields(env, pinned):
    _, state = pinned
    state["res"] = _res(_card("INVALID-CANDIDATE"))
    assert learn.run(env.member(0), "4Q").code == learn.EXIT_OK
    (c,) = _report(env)
    assert (c["verdict"], c["class"], c["severity"]) == ("FAIL", "safety-theater", "high")
    assert c["why"].startswith("MUST NOT auto-flip")
    (r,) = _rows(env.scout_main)
    assert r["strength"] == "none" and r["verified_run"] is False


@pytest.mark.parametrize("case,reason", [
    ("survived", "negative controls did not all die, so the validator ABORTs here: seqlock_snapshot"),
    ("unclean", "a negative control did not run cleanly: wal_append_replay: rust compile failed"),
    ("not-ok", "the negative-control gate did not pass"),
    ("one-gate-run", "the negative-control gate ran 1 times, not 3"),
    ("flag", "chaos harness error: AttributeError: no atomic_write"),
    ("error", "chaos harness error: ImportError: boom"),
    ("other-probe", "chaos result does not belong to this probe"),
    ("other-pattern", "chaos result does not belong to this probe"),
    ("other-kind", "chaos result does not belong to this probe"),
    ("unknown-verdict", "unknown validator verdict 'MAYBE'"),
    ("blind", "chaos run timed out after 900 s"),
])
def test_everything_but_a_clean_pass_or_invalid_is_blind_and_writes_nothing(env, pinned, case, reason):
    _, state = pinned
    state["res"] = {
        "survived": _res(gate=_gate(ok=False, survived=["seqlock_snapshot"])),
        "unclean": _res(gate=_gate(unclean={"wal_append_replay": "rust compile failed"})),
        "not-ok": _res(gate=_gate(ok=False)),
        "one-gate-run": _res(gate=_gate(runs=1)),
        "flag": _res(_card("FLAG")),
        "error": {"error": "ImportError: boom"},
        "other-probe": _res(probe="write_ahead_log"),
        "other-pattern": _res(_card(pattern=chaos.PINNED["write_ahead_log"][0])),
        "other-kind": _res(_card(kind="metamorphic")),
        "unknown-verdict": _res(_card("MAYBE")),
        "blind": chaos.Blind("chaos run timed out after 900 s"),
    }[case]
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q")
    assert res.code == learn.EXIT_OK and "0 rows" in res.outcome, res.outcome
    for f in res.report["functions"]:
        assert f["engines"]["chaos"] == f"BLIND({reason})", f
    assert env.snapshot() == before


def test_a_pass_with_an_unclean_gate_is_still_blind(env, pinned):
    """The gate is checked before the card: a PASS card from a run whose gate was not clean is
    the validator's ABORT, which writes no card at all."""
    _, state = pinned
    state["res"] = _res(gate=_gate(ok=True, unclean={"seqlock_snapshot": "raised RuntimeError: x"}))
    assert learn.run(env.member(0), "4Q").outcome.startswith("0 rows")


def test_race_probes_never_reach_the_child(env, pinned, monkeypatch):
    calls, _ = pinned
    for probe in sorted(chaos.RACE):
        monkeypatch.setitem(chaos._BY_SHA, SHA, probe)
        res = learn.run(env.member(0), "4Q")
        assert res.outcome.startswith("0 rows")
        assert all(f["engines"]["chaos"] == f"BLIND(race/timing probe {probe}: parity with the "
                                            "validator not shown)" for f in res.report["functions"])
    assert calls == []


def test_unpinned_bytes_never_reach_the_child(env, monkeypatch):
    called = []
    monkeypatch.setattr(chaos, "result_for", lambda *a: called.append(a))
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows") and called == []


def test_one_byte_off_a_pinned_file_has_no_probe():
    body = b"x" * 100
    sha = hashlib.sha256(body).hexdigest()
    assert chaos.probe_for(body) is None
    chaos._BY_SHA[sha] = PROBE
    try:
        assert chaos.probe_for(body) == PROBE
        assert chaos.probe_for(body + b"\n") is None and chaos.probe_for(body[:-1]) is None
    finally:
        del chaos._BY_SHA[sha]


# --- the real result_for, short of the child ---------------------------------------------
def test_library_not_configured_is_blind(env, monkeypatch):
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)   # these test the copy path's machinery
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.delenv(config.LIBRARY_KEY, raising=False)
    res = learn.run(env.member(0), "4Q")
    assert all(f["engines"]["chaos"].startswith("BLIND(not configured: ") for f in res.report["functions"])


def test_a_library_not_at_the_pinned_bytes_is_blind_and_leaves_no_scratch(env, monkeypatch, tmp_path):
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)   # these test the copy path's machinery
    lib = tmp_path / "lib"
    for name, (rel, _sha) in chaos.PINNED.items():
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_text("not the pinned bytes\n")
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.setenv(config.LIBRARY_KEY, str(lib))
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("FORGE_GATE_SCRATCH", str(scratch))
    ran = []
    monkeypatch.setattr(chaos, "run_child", lambda *a, **k: ran.append(a))
    res = learn.run(env.member(0), "4Q")
    first = chaos.PINNED[next(iter(chaos.PINNED))][0]
    assert all(f["engines"]["chaos"] == f"BLIND(library file {first} is not at the pinned bytes "
                                        "(library 4b65044b))" for f in res.report["functions"])
    assert ran == [] and list(scratch.iterdir()) == []


def test_one_child_run_per_file_not_per_function(env, monkeypatch, tmp_path):
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)   # these test the copy path's machinery
    seed_calibrated(env.scout)
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.setitem(chaos.SUBJECT, PROBE, frozenset({"area"}))
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)
    runs = []
    monkeypatch.setattr(chaos, "run_child", lambda lib, probe, work: runs.append(probe) or _res())
    res = learn.run(env.member(0), "4Q")
    assert runs == [PROBE] and res.report["rows_appended"] == 1


def test_a_child_that_overruns_is_killed_and_blind(tmp_path, monkeypatch):
    slow = tmp_path / "slow_runner.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(chaos, "RUNNER", slow)
    with pytest.raises(chaos.Blind, match="timed out after 1 s"):
        chaos.run_child(tmp_path, PROBE, tmp_path, timeout=1)


def test_a_timed_out_child_leaves_no_grandchild(tmp_path, monkeypatch):
    """Review F4: the child's whole process group is killed, not only the child."""
    pidf = tmp_path / "grandchild.pid"
    slow = tmp_path / "spawning_runner.py"
    slow.write_text("import subprocess, sys, time\n"
                    f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"open({str(pidf)!r}, 'w').write(str(g.pid))\n"
                    "time.sleep(60)\n")
    monkeypatch.setattr(chaos, "RUNNER", slow)
    with pytest.raises(chaos.Blind, match="timed out"):
        chaos.run_child(tmp_path, PROBE, tmp_path, timeout=2)
    import os, time
    pid = int(pidf.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        try:                                   # reap it if it is a zombie of ours (it is not)
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.1)
    else:
        pytest.fail(f"grandchild {pid} outlived the timeout")


def test_a_child_that_writes_nothing_is_blind(tmp_path, monkeypatch):
    quiet = tmp_path / "quiet_runner.py"
    quiet.write_text("print('{\"gate\": {\"ok\": true}}')\n")      # stdout is never read
    monkeypatch.setattr(chaos, "RUNNER", quiet)
    with pytest.raises(chaos.Blind, match="wrote no result"):
        chaos.run_child(tmp_path, PROBE, tmp_path)


# --- the runner's gate wrapper, on the real ported negative_controls() --------------------
def _port_with(tmp_path, ncs):
    port = chaos_runner._port(tmp_path, tmp_path)
    port.PROBES = [(n, f"x/{n}.py", None, nc, "fault") for n, nc in ncs]
    return port


def test_gate_all_died_is_ok(tmp_path, capsys):
    port = _port_with(tmp_path, [("a", lambda: True), ("b", lambda: True)])
    assert chaos_runner._gate(port) == {"ok": True, "survived": [], "unclean": {}}


def test_gate_a_survived_nc_is_reported(tmp_path, capsys):
    port = _port_with(tmp_path, [("a", lambda: True), ("b", lambda: False)])
    g = chaos_runner._gate(port)
    assert g["ok"] is False and g["survived"] == ["b"] and g["unclean"] == {}


def test_gate_a_raised_nc_passes_the_validator_but_is_unclean_for_scout(tmp_path, capsys):
    def boom():
        raise RuntimeError("no rustc")
    port = _port_with(tmp_path, [("a", lambda: True), ("b", boom)])
    g = chaos_runner._gate(port)
    assert g["ok"] is True                      # the validator's own verdict: a raise = died
    assert g["survived"] == [] and g["unclean"] == {"b": "raised RuntimeError: no rustc"}


def test_gate_a_rust_compile_failure_passes_the_validator_but_is_unclean_for_scout(tmp_path, capsys):
    port = chaos_runner._port(tmp_path, tmp_path)
    port._rust_chaos = lambda relpath, main, fixture=False: {"compile_err": "error[E0425]"}
    rust = [p for p in port.PROBES if p[1].startswith("rust/")][0]
    port.PROBES = [rust]
    g = chaos_runner._gate(port)
    assert g["ok"] is True and g["unclean"] == {rust[0]: "rust compile failed"}


def test_gates_a_race_nc_that_survives_once_in_three_runs_fails_the_gate(tmp_path, capsys):
    """The 6b-2 the deploy host parity failure, reduced: an NC that dies 2 runs out of 3 must fail the gate."""
    seq = iter([True, False, True])
    port = _port_with(tmp_path, [("a", lambda: True), ("race", lambda: next(seq))])
    g = chaos_runner._gates(port)
    assert g == {"ok": False, "survived": ["race"], "unclean": {}, "runs": 3}
    port = _port_with(tmp_path, [("a", lambda: True)])
    assert chaos_runner._gates(port)["ok"] is True


# --- per-machine history (parent review; KD-12 m and c, the maintainer 2026-11-14) ----------------------
def _chaos_verdict(res, entry="area"):
    return [f for f in res.report["functions"] if f["entry"] == entry][0]["engines"]["chaos"]


def test_library_copies_are_blind_by_default(env, monkeypatch):
    """KD-12 (j): a corpus item that IS a pinned library file adds no evidence."""
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.setattr(chaos, "result_for", lambda *a: pytest.fail("a library copy reached the child"))
    assert chaos.LEARN_LIBRARY_COPIES is False
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows")
    assert _chaos_verdict(res).startswith(f"BLIND(library copy ({PROBE})")


def test_a_clean_run_on_a_machine_whose_nc_survived_before_is_blind(env, pinned):
    _, state = pinned
    chaos.record_gate(env.scout / chaos.HISTORY_NAME, _gate(ok=False, survived=["seqlock_snapshot"]))
    state["res"] = _res()                                     # this run: clean gate, PASS card
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows"), res.outcome
    assert _chaos_verdict(res).startswith(f"BLIND(machine {MID[:8]} ({chaos.host()}): negative controls "
                                          "have survived here before (seqlock_snapshot x1)")
    assert env.snapshot() == before


def test_the_latch_survives_a_rename(env, pinned, monkeypatch):
    """The hostname is a label, never the key: a rename does not reset a latched machine."""
    monkeypatch.setattr(chaos, "host", lambda: "old-name")
    chaos.record_gate(env.scout / chaos.HISTORY_NAME, _gate(ok=False, survived=["seqlock_snapshot"]))
    monkeypatch.setattr(chaos, "host", lambda: "new-name")
    assert _chaos_verdict(learn.run(env.member(0), "4Q")).startswith(f"BLIND(machine {MID[:8]} (new-name)")


def test_a_latch_survives_a_configuration_change(env, pinned, monkeypatch):
    chaos.record_gate(env.scout / chaos.HISTORY_NAME, _gate(ok=False, survived=["seqlock_snapshot"]))
    monkeypatch.setattr(chaos, "fingerprint", lambda: dict(FP, rustc="rustc 2.0"))
    assert "have survived here before" in _chaos_verdict(learn.run(env.member(0), "4Q"))


def test_another_machines_latch_does_not_blind_this_machine(env, pinned):
    h = json.loads((env.scout / chaos.HISTORY_NAME).read_text())
    h["machines"]["f" * 32] = {"hostname": "other", "survived": {"seqlock_snapshot": 9}, "configs": {}}
    (env.scout / chaos.HISTORY_NAME).write_text(json.dumps(h))
    assert learn.run(env.member(0), "4Q").report["rows_appended"] == 1


@pytest.mark.parametrize("clean", [0, 1, chaos.CALIBRATION_N - 1])
def test_an_uncalibrated_configuration_is_blind(env, pinned, clean):
    seed_calibrated(env.scout, clean=clean)
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows")
    assert _chaos_verdict(res).startswith(f"BLIND(machine {MID[:8]} ({chaos.host()}): "
                                          f"{clean}/{chaos.CALIBRATION_N} clean gate runs")


def test_a_configuration_change_starts_a_fresh_calibration(env, pinned, monkeypatch):
    assert learn.run(env.member(0), "4Q").report["rows_appended"] == 1     # calibrated under FP
    chaos._CACHE.clear()
    monkeypatch.setattr(chaos, "fingerprint", lambda: dict(FP, rustc="rustc 2.0"))
    res = learn.run(env.member(0), "4Q")               # the SAME pinned bytes, new configuration
    got = [f["engines"]["chaos"] for f in res.report["functions"] if f["entry"] == "area"]
    # non-empty: the earlier version filtered every verdict out of an unpinned member and passed on
    # all([]) (mutant MS, a constant config key, survived it, 2026-11-14)
    assert got and all(x.startswith(f"BLIND(machine {MID[:8]}") and f"0/{chaos.CALIBRATION_N} clean" in x
                       for x in got), got


def test_another_machines_calibration_does_not_count_here(env, pinned):
    seed_calibrated(env.scout, mid="f" * 32)
    assert "0/" in _chaos_verdict(learn.run(env.member(0), "4Q"))


def test_an_unclean_or_failed_gate_is_not_a_clean_run(env):
    hist = env.scout / chaos.HISTORY_NAME
    chaos.record_gate(hist, _gate(unclean={"wal_append_replay": "rust compile failed"}))
    chaos.record_gate(hist, _gate(ok=False))
    chaos.record_gate(hist, _gate())
    c = json.loads(hist.read_text())["machines"][MID]["configs"][chaos.config_key(FP)]
    assert c == {"fingerprint": FP, "gate_runs": 9, "clean_runs": 3}


def test_the_real_machine_id_is_stable_and_well_formed():
    a, b = _REAL_MACHINE_ID(), _REAL_MACHINE_ID()
    assert a == b and len(a) >= 16 and a == a.lower()


def test_an_unknown_machine_is_blind(monkeypatch):
    monkeypatch.setattr(chaos.sys, "platform", "sunos5")
    with pytest.raises(chaos.Blind, match="machine identity unknown"):
        _REAL_MACHINE_ID()


def test_the_real_fingerprint_names_what_the_race_depends_on():
    fp = chaos.fingerprint.__wrapped__() if hasattr(chaos.fingerprint, "__wrapped__") else chaos.fingerprint()
    assert set(fp) == {"python", "rustc", "port_md5", "cpu", "os"}
    assert fp["port_md5"] == __import__("hashlib").md5(chaos.PORT.read_bytes()).hexdigest()


@pytest.mark.parametrize("text", ["not json", "[]", '{"hosts": {}}', '{"version": 1, "machines": {}}',
                                  '{"version": 2, "machines": []}',
                                  '{"version": 2, "machines": {"m": {"survived": {}, "configs": {"c": {"gate_runs": "3", "clean_runs": 0}}}}}'])
def test_an_unreadable_or_old_history_is_blind(env, pinned, text):
    (env.scout / chaos.HISTORY_NAME).write_text(text)
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows")
    assert "is unreadable: chaos stays BLIND" in _chaos_verdict(res)


@pytest.mark.parametrize("target", ["dangling", "clean-file", "calibrated-file"])
def test_a_symlinked_history_is_blind_and_nothing_writes_through_it(env, pinned, tmp_path, target):
    """Crew review 2026-11-14, 5S F1: a DANGLING symlink took the "missing -> empty" branch
    (Path.exists follows links), so a latched machine read as clean, and record_gate then replaced
    the link with a fresh file. Any symlink at the history path is BLIND, and record_gate refuses to
    write through it."""
    hist = env.scout / chaos.HISTORY_NAME
    hist.unlink()                                             # the pinned fixture's seed
    dest = tmp_path / "elsewhere"
    dest.mkdir()
    if target != "dangling":
        seed_calibrated(dest, clean=chaos.CALIBRATION_N if target == "calibrated-file" else 0)
    hist.symlink_to(dest / chaos.HISTORY_NAME)
    res = learn.run(env.member(0), "4Q")
    assert res.outcome.startswith("0 rows"), res.outcome
    assert "is a symlink: chaos stays BLIND" in _chaos_verdict(res)
    with pytest.raises(chaos.Blind, match="is a symlink"):
        chaos.record_gate(hist, _gate())
    assert hist.is_symlink() and ((dest / chaos.HISTORY_NAME).exists() == (target != "dangling"))


def test_the_real_result_for_records_every_gate_run(env, monkeypatch, tmp_path):
    monkeypatch.setattr(chaos, "LEARN_LIBRARY_COPIES", True)
    seed_calibrated(env.scout)
    monkeypatch.setitem(chaos._BY_SHA, SHA, PROBE)
    monkeypatch.setitem(chaos.SUBJECT, PROBE, frozenset({"area"}))
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)
    gates = iter([_gate(), _gate(ok=False, survived=["seqlock_snapshot"]), _gate()])
    spawned = []
    monkeypatch.setattr(chaos, "run_child", lambda lib, probe, work: spawned.append(probe) or _res(gate=next(gates)))
    assert learn.run(env.member(0), "4Q").report["rows_appended"] == 1          # clean, recorded
    rec = lambda: json.loads((env.scout / chaos.HISTORY_NAME).read_text())["machines"][MID]
    cfg = lambda: rec()["configs"][chaos.config_key(FP)]
    assert (cfg()["gate_runs"], cfg()["clean_runs"]) == (chaos.CALIBRATION_N + 3, chaos.CALIBRATION_N + 3)
    chaos._CACHE.clear()
    learn.run(env.member(1), "4Q")                     # another member: not pinned, no child (review F6)
    assert spawned == [PROBE] and cfg()["gate_runs"] == chaos.CALIBRATION_N + 3
    chaos._BY_SHA[hashlib.sha256(env.member(1).read_bytes()).hexdigest()] = PROBE
    try:
        learn.run(env.member(1), "4Q")                 # child: NC survived -> recorded, latched
    finally:
        del chaos._BY_SHA[hashlib.sha256(env.member(1).read_bytes()).hexdigest()]
    assert rec()["survived"] == {"seqlock_snapshot": 1} and rec()["first_survival"]
    assert (cfg()["gate_runs"], cfg()["clean_runs"]) == (chaos.CALIBRATION_N + 6, chaos.CALIBRATION_N + 3)
    chaos._CACHE.clear()
    res = learn.run(env.member(0), "4Q")               # member 0 again: a clean child is not enough now
    assert all(f["engines"]["chaos"].startswith(f"BLIND(machine {MID[:8]}") or "already in" in f.get("row", "")
               for f in res.report["functions"] if f["entry"] == "area")


def test_calibrate_records_gate_only_runs_and_stops_at_the_first_survival(env, monkeypatch, tmp_path):
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)
    asked = []
    gates = iter([_gate(), _gate(), _gate(ok=False, survived=["seqlock_snapshot"]), _gate()])

    def child(lib, probe, work):
        asked.append(probe)
        return {"probe": probe, "gate": next(gates)}
    monkeypatch.setattr(chaos, "run_child", child)
    out = chaos.calibrate(30)
    assert asked == [chaos.GATE_ONLY] * 3                 # stopped at the survival
    assert out["gate_runs_this_call"] == 9 and out["survived"] == {"seqlock_snapshot": 1}
    assert out["config"]["clean_runs"] == 6


def test_calibrate_stops_at_the_requested_runs_and_refuses_a_broken_child(env, monkeypatch, tmp_path):
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)
    monkeypatch.setattr(chaos, "run_child", lambda lib, probe, work: {"probe": probe, "gate": _gate()})
    assert chaos.calibrate(7)["config"]["clean_runs"] == 9            # 3 child runs of 3
    monkeypatch.setattr(chaos, "run_child", lambda lib, probe, work: {"error": "boom"})
    with pytest.raises(chaos.Blind, match="calibration child failed: boom"):
        chaos.calibrate(3)


def test_cli_chaos_calibrate_holds_learns_lock_and_reports(env, monkeypatch, capsys):
    from forge_gate import cli
    done = {"asked": 6, "survived": {}, "config": {"clean_runs": chaos.CALIBRATION_N}}
    monkeypatch.setattr(chaos, "calibrate", lambda runs: {**done, "asked": runs})
    assert cli.main(["chaos-calibrate", "--runs", "6"]) == learn.EXIT_OK
    assert json.loads(capsys.readouterr().out) == done
    fd = learn._singleton(env.scout)                    # a learn is running
    try:
        assert cli.main(["chaos-calibrate"]) == learn.EXIT_LOCK
    finally:
        __import__("os").close(fd)
    monkeypatch.setattr(chaos, "calibrate", lambda runs: (_ for _ in ()).throw(chaos.Blind("calibration child failed: x")))
    assert cli.main(["chaos-calibrate"]) == learn.EXIT_BLIND
    monkeypatch.delenv(config.SCOUT_DIR_KEY)
    assert cli.main(["chaos-calibrate"]) == learn.EXIT_CONFIG


@pytest.mark.parametrize("res", [
    {"survived": {"seqlock_snapshot": 1}, "config": {"clean_runs": chaos.CALIBRATION_N}},   # latched
    {"survived": {}, "config": {"clean_runs": chaos.CALIBRATION_N - 1}},                    # short
    {"survived": {}, "config": {"clean_runs": 0, "gate_runs": chaos.CALIBRATION_N}},        # all unclean
    {"survived": {}, "config": {}}])
def test_cli_chaos_calibrate_never_exits_ok_when_not_calibrated(env, monkeypatch, capsys, res):
    """Review F1: a latched or under-calibrated machine is not a successful calibration."""
    from forge_gate import cli
    monkeypatch.setattr(chaos, "calibrate", lambda runs: res)
    assert cli.main(["chaos-calibrate"]) == learn.EXIT_BLIND
    assert "NOT calibrated" in capsys.readouterr().err


def test_cli_chaos_calibrate_bad_runs_unopenable_lock_and_io_errors_are_config(env, monkeypatch):
    """Review F4: --runs < 1 records nothing and says so; a LearnError keeps its own code; an
    OSError is a config error, not a traceback."""
    from forge_gate import cli
    asked = []
    monkeypatch.setattr(chaos, "calibrate", lambda runs: asked.append(runs))
    for bad in ("0", "-5"):
        assert cli.main(["chaos-calibrate", "--runs", bad]) == learn.EXIT_CONFIG
    assert asked == []
    real = learn._singleton
    monkeypatch.setattr(learn, "_singleton", lambda d: (_ for _ in ()).throw(learn.LearnError("cannot open lock")))
    assert cli.main(["chaos-calibrate"]) == learn.EXIT_CONFIG
    monkeypatch.setattr(learn, "_singleton", real)
    monkeypatch.setattr(chaos, "calibrate", lambda runs: (_ for _ in ()).throw(OSError(28, "No space left")))
    assert cli.main(["chaos-calibrate"]) == learn.EXIT_CONFIG


def test_runner_gate_only_mode_runs_the_gate_and_no_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(chaos_runner, "_gates", lambda port, **kw: _gate())
    out = tmp_path / "r.json"
    assert chaos_runner.main(["x", str(tmp_path), chaos_runner.GATE_ONLY, str(out)]) == 0
    assert json.loads(out.read_text()) == {"probe": chaos_runner.GATE_ONLY, "gate": _gate()}
    assert chaos_runner.GATE_ONLY == chaos.GATE_ONLY


def test_runner_writes_each_gate_run_before_the_next_and_keeps_it_on_error(tmp_path, monkeypatch):
    """Review F2: run 1 sees a survival, run 2 dies. The survival is already on disk before run 2
    starts, and the error result still carries it."""
    out = tmp_path / "r.json"
    calls = []

    def gate(port):
        calls.append(1)
        if len(calls) == 1:
            return {"ok": False, "survived": ["seqlock_snapshot"], "unclean": {}}
        on_disk = json.loads(out.read_text())
        assert on_disk == {"probe": chaos_runner.GATE_ONLY, "partial_gate": {
            "ok": False, "survived": ["seqlock_snapshot"], "unclean": {}, "runs": 1}}
        raise RuntimeError("the child died here")
    monkeypatch.setattr(chaos_runner, "_gate", gate)
    assert chaos_runner.main(["x", str(tmp_path), chaos_runner.GATE_ONLY, str(out)]) == 0
    res = json.loads(out.read_text())
    assert len(calls) == 2 and res["error"].startswith("RuntimeError: the child died here")
    assert res["partial_gate"] == {"ok": False, "survived": ["seqlock_snapshot"], "unclean": {}, "runs": 1}


_FAKE_RUNNER = """import json, sys, time
out = sys.argv[3]
json.dump({"probe": sys.argv[2], "partial_gate": {"ok": False, "survived": ["seqlock_snapshot"],
          "unclean": {}, "runs": 1}}, open(out, "w"))
MODE
"""


@pytest.mark.parametrize("mode,timeout,why", [("time.sleep(60)", 2, "timed out"),
                                              ("pass", 30, "wrote no result")])
def test_run_child_hands_up_the_partial_gate_of_a_child_that_hung_or_died(tmp_path, monkeypatch, mode, timeout, why):
    """Review F2, with a real child process: the survival it wrote reaches the caller."""
    runner = tmp_path / "fake_runner.py"
    runner.write_text(_FAKE_RUNNER.replace("MODE", mode))
    monkeypatch.setattr(chaos, "RUNNER", runner)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(chaos.Blind, match=why) as e:
        chaos.run_child(tmp_path, chaos.GATE_ONLY, work, timeout=timeout)
    assert e.value.partial_gate == {"ok": False, "survived": ["seqlock_snapshot"], "unclean": {}, "runs": 1}


def _partial_gate(survived=("seqlock_snapshot",)):
    return {"ok": True, "survived": list(survived), "unclean": {}, "runs": 1}


@pytest.mark.parametrize("how", ["raises", "error_result"])
def test_result_for_and_calibrate_record_a_partial_gate_as_not_clean_and_latch(env, monkeypatch, tmp_path, how):
    """Review F2: a child that died after seeing a survival still latches the machine, and its
    finished runs never count as clean (ok=True in the partial is ignored on purpose)."""
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)

    def child(lib, probe, work):
        if how == "raises":
            raise chaos.Blind("chaos run timed out after 900 s", partial_gate=_partial_gate())
        return {"error": "RuntimeError: died", "partial_gate": _partial_gate()}
    monkeypatch.setattr(chaos, "run_child", child)
    path = env.scout / chaos.HISTORY_NAME
    for call in (lambda: chaos.result_for(b"x", PROBE), lambda: chaos.calibrate(3)):
        try:
            call()
        except chaos.Blind:
            pass
        chaos._CACHE.clear()
    m = json.loads(path.read_text())["machines"][MID]
    assert m["survived"] == {"seqlock_snapshot": 2}
    assert m["configs"][chaos.config_key(FP)]["gate_runs"] == 2
    assert m["configs"][chaos.config_key(FP)]["clean_runs"] == 0
    with pytest.raises(chaos.Blind, match="have survived here before"):
        chaos.check_host()


def test_a_partial_gate_never_counts_clean_even_without_a_survival(env, monkeypatch, tmp_path):
    """Review F2 / mutant M2: two clean runs of a child that then died are recorded, not clean."""
    monkeypatch.setenv(config.LIBRARY_KEY, str(tmp_path))
    monkeypatch.setattr(chaos, "stage_library", lambda lib, dest: None)
    monkeypatch.setattr(chaos, "run_child", lambda lib, probe, work: {
        "error": "RuntimeError: died", "partial_gate": {"ok": True, "survived": [], "unclean": {}, "runs": 2}})
    with pytest.raises(chaos.Blind, match="calibration child failed"):
        chaos.calibrate(3)
    m = json.loads((env.scout / chaos.HISTORY_NAME).read_text())["machines"][MID]
    c = m["configs"][chaos.config_key(FP)]
    assert (c["gate_runs"], c["clean_runs"], m["survived"]) == (2, 0, {})


def test_a_survival_is_never_clean_even_when_the_gate_says_ok(env):
    """Review F7: the ``not survived`` clause stands on its own."""
    path = env.scout / chaos.HISTORY_NAME
    chaos.record_gate(path, {"ok": True, "survived": ["seqlock_snapshot"], "unclean": {}, "runs": 3})
    c = json.loads(path.read_text())["machines"][MID]["configs"][chaos.config_key(FP)]
    assert (c["gate_runs"], c["clean_runs"]) == (3, 0)


def test_the_real_fingerprint_asks_rustc_from_the_childs_scratch_dir(monkeypatch):
    """Review F3: a rust-toolchain.toml in the caller's cwd must not name the fingerprint's rustc."""
    seen = []
    real = chaos.subprocess.run

    def run(cmd, **kw):
        if cmd[:1] == ["rustc"]:
            seen.append(kw.get("cwd"))
        return real(cmd, **kw)
    monkeypatch.setattr(chaos.subprocess, "run", run)
    chaos.fingerprint()
    assert seen == [config.scratch_dir()]


def test_runner_reports_an_unknown_probe_as_an_error(tmp_path):
    out = tmp_path / "r.json"
    assert chaos_runner.main(["x", str(tmp_path), "no_such_probe", str(out)]) == 0
    assert json.loads(out.read_text())["error"].startswith("ValueError: no such probe")
