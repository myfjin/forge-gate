"""Build steps 6b-1/6b-2: `forge-gate learn` plumbing (design v1.4.5 §3, §4.5, §4.4.1; DECISIONS-6b L1-L4).

Fixtures only. Every registry, lock, scout folder and corpus export here lives in tmp_path;
conftest removes learn's three keys from the environment and the host config.

Red arms, each with the claim it holds up:
- (C1, restated for 6b-2) only the BUILT-IN chaos engine can PASS: every other built-in engine is
  BLIND(not wired), chaos is BLIND on any file the validator has no probe for, and an injected
  engine that returns PASS (any kind, chaos included) or any verdict but FAIL stops the run
  with nothing written (tests/test_scout_chaos.py holds the chaos engine's own arms);
- a held build lock makes learn wait, then fail loudly (and a lock released in time is taken);
- learn holds the build lock while it appends (checked from another process mid-append);
- a second learn fails fast on scout_learn.lock;
- a file that is not a corpus member writes nothing;
- duplicate ids are skipped and reported.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from forge_gate import cli, config
from forge_gate.scout import cards, corpus, learn
from forge_gate.scout.ids import parse_id

RUN_ID = "20261111T130229Z"
PY_BODY = (
    "import math\n\n\n"
    "def area(r: float) -> float:\n    out = math.pi * r * r\n    return out\n\n\n"
    "def scale(xs: list, k=2):\n    return [x * k for x in xs]\n\n\n"
    "def _private(a):\n    return a\n"
)
PY_ONE = "def only(a, b):\n    return a + b\n"
RUST_BODY = "fn main() { println!(\"hi\"); }\n"
SH_BODY = "for f in *.log; do\n  gzip \"$f\"\ndone\n"
LIB_ROW = {"id": "ab_interleaving", "sphere": "PY", "oracle": {"tier": "hard"}}
ROW_KEYS = {"id", "sphere", "category", "signature", "post_conditions", "invariant_guards",
            "core_idea", "belief_sentence", "type_nouns_raw", "verified_run", "strength",
            "is_axiom", "extraction", "oracle", "prior_is_axiom", "basis"}


def _items():
    return [
        {"item_id": "c_00000000000000a1", "body": PY_BODY, "lang": "python", "kind": "write",
         "file": "corpus/python/misc/c_00000000000000a1.py"},
        {"item_id": "c_00000000000000a2", "body": PY_ONE, "lang": "python", "kind": "write",
         "file": "corpus/python/misc/c_00000000000000a2.py"},
        {"item_id": "c_00000000000000a3", "body": RUST_BODY, "lang": "rust", "kind": "write",
         "file": "corpus/rust/misc/c_00000000000000a3.rs"},
        {"item_id": "c_00000000000000a4", "body": SH_BODY, "lang": "shell", "kind": "heredoc",
         "file": "corpus/shell/misc/c_00000000000000a4.sh"},
        {"item_id": "c_00000000000000a5", "body": "{\"a\": 1}\n", "lang": "fortran", "kind": "write",
         "file": "corpus/x/c_00000000000000a5.f"},
    ]


def _export(root: Path, items, run_id=RUN_ID, corrupt=False):
    d = root / run_id
    d.mkdir(parents=True)
    files = {"items.jsonl": "".join(json.dumps(i) + "\n" for i in items),
             "clusters.jsonl": "", "scout_ready_mismatch.jsonl": ""}
    meta = {}
    for name, text in files.items():
        b = text.encode()
        (d / name).write_bytes(b)
        meta[name] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b), "rows": b.count(b"\n")}
    (d / "manifest.json").write_text(json.dumps(
        {"schema": corpus.SCHEMA, "run_id": run_id, "run_status": "OK", "files": meta}))
    if corrupt:
        p = d / "items.jsonl"
        b = bytearray(p.read_bytes())
        b[len(b) // 2] ^= 1
        p.write_bytes(bytes(b))
    return d


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.data = tmp / "data"
        self.data.mkdir()
        self.main = self.data / "logic_registry.jsonl"        # a REAL ladder: learn only reads it
        self.main.write_text(json.dumps(LIB_ROW) + "\n")
        for s in learn.REAL_LADDERS[1:] + learn.SCOUT_FILES:
            (self.data / s).write_text("")
        self.scout_main = self.data / "scout_registry.jsonl"   # Python rows go here (decision A)
        self.scout = tmp / "scout"
        self.scout.mkdir()
        self.lock = tmp / "build.lock"
        self.lock.write_text("")
        self.export = tmp / "export" / "v1"
        self.export.mkdir(parents=True)
        _export(self.export, _items())

    def member(self, i=0, name=None) -> Path:
        it = _items()[i]
        p = self.tmp / (name or Path(it["file"]).name)
        p.write_text(it["body"])
        return p

    def snapshot(self):
        files = sorted(p for p in self.tmp.rglob("*") if p.is_file() and "export" not in p.parts
                       and p.name != learn.LEARN_LOCK_NAME and p.suffix != ".py")
        return {str(p.relative_to(self.tmp)): hashlib.md5(p.read_bytes()).hexdigest() for p in files}


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(e.data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    monkeypatch.setenv(config.CORPUS_KEY, str(e.export))
    return e


def engines_with(**verdicts):
    """The built-in battery with some kinds replaced by a fixed verdict (test injection)."""
    out = []
    for kind, fn in learn.ENGINES:
        if kind in verdicts:
            v = verdicts[kind]
            out.append((kind, lambda item, sig, sid, k=kind, v=v: learn.EngineResult(
                k, verdict=v, technique="fixture", fixture="f", expected="e", actual="a")))
        else:
            out.append((kind, fn))
    return out


def _rows(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# --- only the built-in chaos engine can PASS (L3, 6b-2) -----------------------------------
def test_every_kind_has_one_builtin_engine_only_chaos_is_wired():
    assert [k for k, _ in learn.ENGINES] == sorted(cards.KINDS)
    for kind, fn in learn.ENGINES:
        r = fn({"item_id": "x", "body": PY_BODY}, {"entry": "area"}, "sid")
        if kind == "chaos":
            assert fn is learn.chaos_engine
            assert r == learn.EngineResult("chaos", blind_reason="no validator chaos probe for these bytes")
        else:
            assert r == learn.EngineResult(kind, blind_reason="not wired"), kind
            assert r.label() == "BLIND(not wired)"


def test_the_card_vocabulary_admits_pass_for_chaos_only():
    assert learn.CARD_VERDICTS == {"FAIL"} and learn.PASS_KINDS == {"chaos"}
    assert learn.CARD_VERDICTS | {"PASS"} <= learn.READER_VERDICTS & cards.VERDICTS


def test_real_battery_on_a_member_writes_nothing_and_says_why(env, capsys):
    before = env.snapshot()
    rc = cli.main(["learn", str(env.member(0)), "--author=4Q"])
    out = capsys.readouterr().out
    assert rc == learn.EXIT_OK
    engine_lines = [l for l in out.splitlines() if l.startswith("      ")]
    assert len(engine_lines) == 2 * len(learn.ENGINES)   # area + scale, every kind
    chaos_lines = [l for l in engine_lines if l.split()[0] == "chaos"]
    assert len(chaos_lines) == 2
    assert all(l.endswith("BLIND(no validator chaos probe for these bytes)") for l in chaos_lines)
    assert all(l.endswith("BLIND(not wired)") for l in engine_lines if l not in chaos_lines)
    assert "no engine verdict: no row" in out and "0 rows" in out
    assert env.snapshot() == before
    assert not (env.scout / learn.REPORT_NAME).exists()


@pytest.mark.parametrize("verdict", ["PASS", "RATIFIED", "FLAG", "UNSTABLE", "UNSUPPORTED", "PROPOSAL"])
@pytest.mark.parametrize("kind", ["metamorphic", "chaos"])
def test_an_injected_engine_returning_anything_but_fail_is_refused_and_nothing_is_written(env, verdict, kind):
    """An injected chaos engine is not the built-in one: its PASS is refused like any other."""
    other = "xlang"
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(**{other: "FAIL", kind: verdict}))
    assert res.code == learn.EXIT_REFUSED, res.outcome
    assert repr(verdict) in res.outcome and "Nothing was written" in res.outcome
    assert env.snapshot() == before


def test_a_builtin_engine_moved_to_another_kind_cannot_pass(env):
    """PASS needs kind chaos AND the chaos engine object: the chaos engine filed under another kind,
    or a wrapper around it, is refused."""
    def as_metamorphic(item, sig, sid):
        return learn.EngineResult("metamorphic", verdict="PASS")
    eng = [(k, as_metamorphic if k == "metamorphic" else fn) for k, fn in learn.ENGINES]
    assert learn.run(env.member(0), "4Q", engines=eng).code == learn.EXIT_REFUSED
    with pytest.raises(learn.Refused):
        learn._check("metamorphic", learn.EngineResult("metamorphic", verdict="PASS"), learn.chaos_engine)
    with pytest.raises(learn.Refused):
        learn._check("chaos", learn.EngineResult("chaos", verdict="PASS"), lambda *a: None)
    with pytest.raises(learn.Refused):
        learn._check("chaos", learn.EngineResult("chaos", verdict="PASS"))
    learn._check("chaos", learn.EngineResult("chaos", verdict="PASS"), learn.chaos_engine)


def test_a_pass_on_a_later_function_still_stops_everything(env):
    """The gate runs over every function before the first byte is written."""
    calls = []

    def chaos(item, sig, sid):
        calls.append(sig["entry"])
        return learn.EngineResult("chaos", verdict="FAIL" if sig["entry"] == "area" else "PASS")
    eng = [(k, chaos if k == "chaos" else fn) for k, fn in learn.ENGINES]
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=eng)
    assert calls == ["area", "scale"] and res.code == learn.EXIT_REFUSED
    assert env.snapshot() == before


def test_an_engine_answering_for_another_kind_or_with_both_is_refused(env):
    bad_kind = [(k, (lambda i, s, d: learn.EngineResult("chaos", verdict="FAIL")) if k == "xlang" else fn)
                for k, fn in learn.ENGINES]
    assert learn.run(env.member(0), "4Q", engines=bad_kind).code == learn.EXIT_REFUSED
    both = [(k, (lambda i, s, d, k=k: learn.EngineResult(k, verdict="FAIL", blind_reason="x"))
             if k == "chaos" else fn) for k, fn in learn.ENGINES]
    assert learn.run(env.member(0), "4Q", engines=both).code == learn.EXIT_REFUSED


# --- rows, cards, survival gate --------------------------------------------------------
def test_a_fail_verdict_appends_rows_in_the_45_shape_and_cards_the_reader_can_parse(env):
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL", xlang="FAIL"))
    assert res.code == learn.EXIT_OK, res.outcome
    assert _rows(env.main) == [LIB_ROW]          # the real ladder is never written (decision A)
    new = _rows(env.scout_main)
    assert len(new) == 2
    assert [r["signature"]["entry"] for r in new] == ["area", "scale"]
    for r in new:
        assert set(r) == ROW_KEYS
        sid = parse_id(r["id"])
        assert (sid.pool, sid.source, sid.commit, sid.path, sid.function) == (
            "corpus", "c_00000000000000a1", RUN_ID, "corpus/python/misc/c_00000000000000a1.py",
            r["signature"]["entry"])
        assert r["oracle"] == {"backed": False, "kinds": [], "verdicts": {}, "tier": "candidate",
                               "checked_against": "scout-6b-2", "stamp": r["oracle"]["stamp"]}
        assert (r["is_axiom"], r["prior_is_axiom"], r["basis"]) == (False, False, "green-run-candidate")
        assert r["strength"] == "none" and r["verified_run"] is False and r["sphere"] == "PY"
        x = r["extraction"]
        assert (x["method"], x["author"], x["pool"], x["corpus_export"]) == ("scout", "4Q", "corpus", RUN_ID)
        assert x["sha256"] == hashlib.sha256(PY_BODY.encode()).hexdigest()
    assert new[0]["signature"]["consumes"] == [{"name": "r", "type": "float", "noun": None}]
    assert new[0]["type_nouns_raw"] == ["float"]
    got = cards.load_report(env.scout / learn.REPORT_NAME)
    assert len(got) == 4 and res.report["cards_written"] == 4
    assert {(c["id"], c["kind"], c["verdict"]) for c in got} == {
        (r["id"], k, "FAIL") for r in new for k in ("chaos", "xlang")}
    assert all(c["kind"] == c["oracle_coverage_kind"] for c in got)


def test_survival_gate_only_functions_with_a_verdict_get_rows(env):
    def chaos(item, sig, sid):
        if sig["entry"] == "area":
            return learn.EngineResult("chaos", verdict="FAIL")
        return learn.EngineResult("chaos", blind_reason="no inputs")
    eng = [(k, chaos if k == "chaos" else fn) for k, fn in learn.ENGINES]
    res = learn.run(env.member(0), "4Q", engines=eng)
    assert res.code == learn.EXIT_OK
    assert [r["signature"]["entry"] for r in _rows(env.scout_main)] == ["area"]
    assert [c["id"] for c in cards.load_report(env.scout / learn.REPORT_NAME)] == [_rows(env.scout_main)[0]["id"]]


def test_duplicate_ids_are_skipped_and_reported_never_duplicated(env):
    eng = engines_with(chaos="FAIL")
    assert learn.run(env.member(0), "4Q", engines=eng).code == learn.EXIT_OK
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=eng)
    assert res.code == learn.EXIT_OK
    assert res.report["rows_appended"] == 0 and len(res.report["duplicates"]) == 2
    assert "2 duplicate id(s) skipped" in res.outcome
    assert env.snapshot() == before


def test_an_id_already_in_a_sibling_file_is_a_duplicate_too(env):
    eng = engines_with(chaos="FAIL")
    sid = learn.make_id("corpus", "c_00000000000000a2", RUN_ID,
                        "corpus/python/misc/c_00000000000000a2.py", "only")
    (env.data / "rust_registry.jsonl").write_text(json.dumps({"id": sid}) + "\n")
    before = env.snapshot()
    res = learn.run(env.member(1), "4Q", engines=eng)
    assert res.code == learn.EXIT_OK and res.report["duplicates"] == [sid]
    assert env.snapshot() == before


def test_a_registry_without_a_final_newline_is_refused(env):
    env.scout_main.write_text(json.dumps({"id": "scout:corpus:other"}))   # no final newline
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and "newline" in res.outcome
    assert env.snapshot() == before


# --- pool (L2: corpus members only, for now) -------------------------------------------
def test_a_file_outside_the_corpus_is_blind_and_writes_nothing(env, capsys):
    p = env.tmp / "local.py"
    p.write_text(PY_ONE.replace("a + b", "a - b"))
    before = env.snapshot()
    rc = cli.main(["learn", str(p), "--author=Claude"])
    cap = capsys.readouterr()
    assert rc == learn.EXIT_BLIND and "BLIND(not in a pool" in cap.out
    assert env.snapshot() == before and not (env.scout / learn.REPORT_NAME).exists()
    # one byte away from a member is not a member: the match is on the exact bytes
    q = env.tmp / "near.py"
    q.write_text(PY_ONE + "\n")
    assert learn.run(q, "Claude", engines=engines_with(chaos="FAIL")).code == learn.EXIT_BLIND
    assert env.snapshot() == before


def test_an_export_that_fails_its_sha_recheck_is_blind(env, tmp_path, monkeypatch):
    bad = tmp_path / "badexport"
    bad.mkdir()
    _export(bad, _items(), corrupt=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(bad))
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_BLIND and "sha-recheck" in res.outcome
    assert env.snapshot() == before


@pytest.mark.parametrize("i,code,text", [
    (2, learn.EXIT_BLIND, "no signature extractor for 'rust'"),
    (3, learn.EXIT_BLIND, "no signature extractor for 'shell'"),
    (4, learn.EXIT_BLIND, "language 'fortran' has no ladder file"),
])
def test_members_in_other_languages_route_then_go_blind(env, i, code, text):
    res = learn.run(env.member(i), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == code and text in res.outcome, res.outcome


def test_routing_table_and_a_missing_sibling_fails_naming_the_file(env):
    assert learn.route("python", env.data) == env.data / "scout_registry.jsonl"
    assert learn.route("cpp", env.data) == env.data / "scout_registry.jsonl"
    assert learn.route("r", env.data) == env.data / "scout_registry_r.jsonl"
    assert learn.route("rust", env.data) == env.data / "scout_registry_rust.jsonl"
    assert learn.route("systemd", env.data) == env.data / "scout_registry_sysadmin.jsonl"
    assert not {learn.route(lang, env.data).name for lang in learn.ROUTE} & set(learn.REAL_LADDERS)
    (env.data / "scout_registry_rust.jsonl").rename(env.tmp / "moved.jsonl")
    res = learn.run(env.member(2), "4Q")
    assert res.code == learn.EXIT_CONFIG and "scout_registry_rust.jsonl" in res.outcome

# --- config: no defaults ----------------------------------------------------------------
@pytest.mark.parametrize("key", [config.BRAIN_DATA_KEY, config.SCOUT_DIR_KEY, config.BUILD_LOCK_KEY])
def test_each_key_unset_fails_naming_it(env, monkeypatch, key):
    monkeypatch.delenv(key)
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and key in res.outcome
    assert env.snapshot() == before


def test_a_missing_build_lock_is_refused_and_never_created(env, monkeypatch):
    ghost = env.tmp / "no-such.lock"
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(ghost))
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and "does not exist" in res.outcome
    assert not ghost.exists()


def test_a_bad_author_is_refused(env):
    assert learn.run(env.member(0), "two words").code == learn.EXIT_CONFIG
    assert learn.run(env.member(0), "").code == learn.EXIT_CONFIG


# --- locks (L1 build lock; learn-vs-learn singleton) -------------------------------------
HOLDER = ("import fcntl, os, sys, time\n"
          "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o660)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "print('held', flush=True)\n"
          "time.sleep(float(sys.argv[2]))\n")


def _hold(path: Path, seconds: float) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_a_held_build_lock_makes_learn_wait_then_fail_loudly(env):
    holder = _hold(env.lock, 30)
    try:
        before = env.snapshot()
        t = time.monotonic()
        res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"), lock_wait=1.0)
        took = time.monotonic() - t
    finally:
        holder.kill()
        holder.wait()
    assert res.code == learn.EXIT_LOCK, res.outcome
    assert str(env.lock) in res.outcome and "waited 1 s" in res.outcome
    assert took >= 1.0
    assert env.snapshot() == before


def test_a_build_lock_released_while_learn_waits_is_taken(env):
    holder = _hold(env.lock, 0.8)
    try:
        t = time.monotonic()
        res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"), lock_wait=10)
        took = time.monotonic() - t
    finally:
        holder.wait()
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 2
    assert took >= 0.5   # it waited for the holder, it did not ignore the lock


def test_learn_holds_the_build_lock_while_it_appends(env, monkeypatch):
    probe = ("import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_RDONLY)\n"
             "try:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\n"
             "except BlockingIOError:\n    print('held')\n")
    seen = []
    real = learn._append

    def spy(target, rows):
        seen.append(subprocess.run([sys.executable, "-c", probe, str(env.lock)],
                                   capture_output=True, text=True).stdout.strip())
        real(target, rows)
    monkeypatch.setattr(learn, "_append", spy)
    assert learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL")).code == learn.EXIT_OK
    assert seen == ["held"]


def test_a_second_learn_fails_fast_on_the_singleton(env):
    holder = _hold(env.scout / learn.LEARN_LOCK_NAME, 30)
    try:
        before = env.snapshot()
        t = time.monotonic()
        res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"), lock_wait=10)
        took = time.monotonic() - t
    finally:
        holder.kill()
        holder.wait()
    assert res.code == learn.EXIT_LOCK and "another `forge-gate learn`" in res.outcome
    assert took < 1.0, took   # LOCK_NB: no wait
    assert env.snapshot() == before


RUNNER = ("import sys, json\n"
          "from forge_gate.scout import learn\n"
          "eng = [(k, (lambda i, s, d, k=k: learn.EngineResult(k, verdict='FAIL')) if k == 'chaos' else f)\n"
          "       for k, f in learn.ENGINES]\n"
          "r = learn.run(sys.argv[1], '4Q', engines=eng, lock_wait=float(sys.argv[2]))\n"
          "print(json.dumps({'code': r.code, 'outcome': r.outcome}))\n")


def test_two_learn_processes_at_once_the_second_fails_fast_the_first_completes(env):
    """Real concurrency: learn A holds the singleton while it waits for the build lock (held by a
    third process); learn B starts then and must fail at once; A completes after the release."""
    holder = _hold(env.lock, 2.5)
    envv = {**os.environ, "PYTHONPATH": str(Path(learn.__file__).resolve().parents[2])}
    member = env.member(0)
    a = subprocess.Popen([sys.executable, "-c", RUNNER, str(member), "15"], env=envv,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.0)            # A is inside its build-lock wait, singleton held
        assert a.poll() is None
        t = time.monotonic()
        b = subprocess.run([sys.executable, "-c", RUNNER, str(member), "15"], env=envv,
                           capture_output=True, text=True, timeout=30)
        b_took = time.monotonic() - t
        a_out, a_err = a.communicate(timeout=30)
    finally:
        holder.wait()
        if a.poll() is None:
            a.kill()
    rb, ra = json.loads(b.stdout), json.loads(a_out)
    assert rb["code"] == learn.EXIT_LOCK and "another `forge-gate learn`" in rb["outcome"], rb
    assert b_took < 1.0 + 1.0, b_took   # interpreter start-up + LOCK_NB, never the 15 s wait
    assert ra["code"] == learn.EXIT_OK and "2 row(s) appended" in ra["outcome"], (ra, a_err)
    assert len(_rows(env.scout_main)) == 2 and _rows(env.main) == [LIB_ROW]


# --- cards: the field the deployed reader joins on --------------------------------------
def apply_scout_cards_6be79ec(oracle, extra_files):
    """Verbatim body of apply_scout_cards from the Brain at 6be79ec
    (src/aura_brain/conscience/honest_pass.py:106-144, read 2026-11-13). If the Brain changes
    it, re-copy; the real reader is exercised by test_scout_learn_brain.py on the deploy host."""
    SCOUT_KINDS = {"xlang", "positive", "chaos", "metamorphic", "invariant",
                   "roundtrip", "reseed", "reseed_ref"}
    VERDICTS = {"PASS", "FAIL", "FLAG", "RATIFIED"}
    card_ids, added = set(), set()
    for path in extra_files:
        d = json.load(open(path))
        cs = d['cards'] if isinstance(d, dict) else d
        for c in cs:
            cid = c.get('id')
            if not cid:
                raise ValueError(f"{path}: a scout card has no 'id' (D7-5 requires a namespaced id)")
            if c.get('source') != 'scout':
                raise ValueError(f"{path}: card {cid!r} has source={c.get('source')!r}, want 'scout'")
            kind = c.get('kind') or c.get('oracle_kind')
            if kind not in SCOUT_KINDS:
                raise ValueError(f"{path}: card {cid!r} names unknown kind {kind!r}")
            if 'verdict' not in c:
                raise ValueError(f"{path}: card {cid!r} has no 'verdict' (J3c: never defaulted)")
            verdict = c['verdict']
            if verdict not in VERDICTS:
                raise ValueError(f"{path}: card {cid!r} has unknown verdict {verdict!r}")
            card_ids.add(cid)
            if kind == 'invariant':
                if c.get('ratified') is True and verdict == 'RATIFIED':
                    if 'invariant' not in oracle[cid]:
                        added.add(cid)
                    oracle[cid]['invariant'] = {'verdict': 'RATIFIED',
                                                'source': os.path.basename(path)}
                continue
            if verdict == 'PASS' and kind not in oracle[cid]:
                oracle[cid][kind] = {'verdict': 'PASS', 'source': os.path.basename(path)}
                added.add(cid)
    return card_ids, added


def test_learned_cards_pass_the_deployed_readers_checks_and_back_nothing(env):
    import collections
    learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL", metamorphic="FAIL"))
    rep = env.scout / learn.REPORT_NAME
    oracle = collections.defaultdict(dict)
    ids, added = apply_scout_cards_6be79ec(oracle, [rep])
    assert ids == {r["id"] for r in _rows(env.scout_main)} and added == set()
    assert not any(oracle.values())


def test_a_3a_shaped_card_without_kind_is_what_the_reader_rejects(env):
    """Why cards.py gained `kind` in 6b-1: the reader cannot see oracle_coverage_kind."""
    import collections
    learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    rep = env.scout / learn.REPORT_NAME
    d = json.loads(rep.read_text())
    for c in d["cards"]:
        del c["kind"]
    rep.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="unknown kind None"):
        apply_scout_cards_6be79ec(collections.defaultdict(dict), [rep])
    with pytest.raises(cards.BadCard):
        cards.load_report(rep)


def test_a_card_whose_kind_disagrees_with_its_coverage_kind_is_refused():
    sid = learn.make_id("corpus", "c_1", RUN_ID, "a.py", "f")
    c = cards.make_card(sid, kind="chaos", verdict="FAIL", technique="t", fixture="f",
                        expected="e", actual="a", repro_cmd="r")
    c["kind"] = "metamorphic"
    with pytest.raises(cards.BadCard, match="kind"):
        cards.check_scout_card(c)


# --- review round (adversarial-reviewer 2026-11-13: F2, F4, F5, F6, F8) -------------------
LOCK_PROBE = ("import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_RDONLY)\n"
              "try:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\n"
              "except BlockingIOError:\n    print('held')\n")


def _lock_state(lock: Path) -> str:
    return subprocess.run([sys.executable, "-c", LOCK_PROBE, str(lock)],
                          capture_output=True, text=True).stdout.strip()


def test_the_build_lock_is_held_at_the_dup_check_the_card_check_and_the_report_swap(env, monkeypatch):
    """F5 (mutants C2, D): not only during the append."""
    seen = {}
    for name, obj, attr in (("dup-check", learn, "_ladder_ids"), ("card-stage", learn.cards, "stage_report"),
                            ("append", learn, "_append"), ("report-swap", learn, "_commit_report")):
        real = getattr(obj, attr)

        def spy(*a, _real=real, _name=name, **k):
            seen[_name] = _lock_state(env.lock)
            return _real(*a, **k)
        monkeypatch.setattr(obj, attr, spy)
    assert learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL")).code == learn.EXIT_OK
    assert seen == {"dup-check": "held", "card-stage": "held", "append": "held", "report-swap": "held"}
    assert _lock_state(env.lock) == "free"


def test_a_name_defined_twice_keeps_the_last_def_like_python_does(env, monkeypatch, tmp_path):
    """F5 (mutant A) + F6: one row, for the def Python binds."""
    body = "def f(a):\n    return a\n\n\ndef f(b, c):\n    return b + c\n"
    items = _items() + [{"item_id": "c_00000000000000b1", "body": body, "lang": "python",
                         "kind": "write", "file": "corpus/python/misc/c_00000000000000b1.py"}]
    root = tmp_path / "export2"
    root.mkdir()
    _export(root, items)
    monkeypatch.setenv(config.CORPUS_KEY, str(root))
    p = tmp_path / "twice.py"
    p.write_text(body)
    res = learn.run(p, "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 1, res.outcome
    rows = _rows(env.scout_main)
    assert [c["name"] for c in rows[0]["signature"]["consumes"]] == ["b", "c"]
    assert res.report["duplicates"] == [rows[0]["id"]]
    assert [f["row"] for f in res.report["functions"]] == [
        "shadowed by a later def of the same name: skipped", "appended to scout_registry.jsonl"]


def test_cards_already_in_the_report_for_a_rowless_id_stop_the_run(env):
    """F5 (mutant B): an orphan card from an earlier failure is reported, not overwritten."""
    sid = learn.make_id("corpus", "c_00000000000000a1", RUN_ID,
                        "corpus/python/misc/c_00000000000000a1.py", "area")
    cards.write_report(env.scout / learn.REPORT_NAME, [cards.make_card(
        sid, kind="chaos", verdict="FAIL", technique="t", fixture="f", expected="e", actual="a",
        repro_cmd="r")])
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and "already has cards" in res.outcome and sid in res.outcome
    assert env.snapshot() == before


@pytest.mark.parametrize("where", ["append", "report-swap"])
def test_a_failure_after_the_registry_changes_leaves_both_files_as_they_were(env, monkeypatch, where):
    """F2 + F8: rows never land without their cards, and a half-written line is cut back."""
    if where == "append":
        def boom(target, rows):
            with target.open("ab") as fh:
                fh.write(b'{"id": "half-a-ro')     # a torn write, then the disk is full
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(learn, "_append", boom)
    else:
        def boom(tmp, path):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(learn, "_commit_report", boom)
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and "Nothing was kept" in res.outcome, res.outcome
    assert env.snapshot() == before
    assert [p.name for p in env.scout.iterdir()] == [learn.LEARN_LOCK_NAME]   # no temp litter
    # and the next run is not fooled into "duplicates skipped": it learns them for real
    monkeypatch.undo()
    for k, v in ((config.BRAIN_DATA_KEY, env.data), (config.SCOUT_DIR_KEY, env.scout),
                 (config.BUILD_LOCK_KEY, env.lock), (config.CORPUS_KEY, env.export)):
        monkeypatch.setenv(k, str(v))
    again = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert again.code == learn.EXIT_OK and again.report["rows_appended"] == 2
    assert len(cards.load_report(env.scout / learn.REPORT_NAME)) == 2


def test_a_card_the_report_refuses_is_caught_before_the_registry_changes(env):
    """F2 probe P1: two engines claiming the same kind used to append rows, then fail."""
    eng = list(engines_with(chaos="FAIL"))
    eng.append(("chaos", eng[0][1]))
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=eng)
    assert res.code == learn.EXIT_CONFIG and "same id and kind" in res.outcome
    assert env.snapshot() == before


def test_the_report_is_readable_by_the_registry_builds_user(env):
    """F4: mkstemp's 0600 would stop the hourly build (another user) with PermissionError."""
    learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert (env.scout / learn.REPORT_NAME).stat().st_mode & 0o777 == cards.REPORT_MODE == 0o644


# --- decision A (6E, 2026-11-13; review F3): scout's own ladder files ----------------------
def test_learn_never_writes_a_real_ladder_file(env):
    """The F3 fix: learned rows live only where no Brain extractor writes."""
    real = {n: (env.data / n).read_bytes() for n in learn.REAL_LADDERS}
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 2
    assert {n: (env.data / n).read_bytes() for n in learn.REAL_LADDERS} == real
    assert res.report["target"] == str(env.scout_main)


def test_a_rebuild_of_every_real_ladder_cannot_remove_a_learned_row(env):
    """What a Brain extractor does after learn: rows = its own source only, then write_text."""
    learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    learned = _rows(env.scout_main)
    for n in learn.REAL_LADDERS:
        (env.data / n).write_text(json.dumps({"id": f"rebuilt-{n}"}) + "\n")
    assert len(learned) == 2 and _rows(env.scout_main) == learned


def test_an_id_already_in_another_scout_file_is_a_duplicate_too(env):
    sid = learn.make_id("corpus", "c_00000000000000a2", RUN_ID,
                        "corpus/python/misc/c_00000000000000a2.py", "only")
    (env.data / "scout_registry_sysadmin.jsonl").write_text(json.dumps({"id": sid}) + "\n")
    before = env.snapshot()
    res = learn.run(env.member(1), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_OK and res.report["duplicates"] == [sid]
    assert env.snapshot() == before


@pytest.mark.parametrize("bad", ["missing", "checkout-root"])
def test_brain_data_must_be_the_brains_data_folder(env, monkeypatch, bad):
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(env.tmp / "nope" if bad == "missing" else env.tmp))
    before = env.snapshot()
    res = learn.run(env.member(0), "4Q", engines=engines_with(chaos="FAIL"))
    assert res.code == learn.EXIT_CONFIG and config.BRAIN_DATA_KEY in res.outcome, res.outcome
    assert env.snapshot() == before
