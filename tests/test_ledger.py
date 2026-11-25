"""The ledgers (design §4a.5, build step 8a; DECISIONS-8a G1-G8). Fixtures only: every test makes
its own ledger folder the way the switch-on does (folder + two empty files + the lock)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from forgeproof import config, ledger

REPO = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def make_ledger(root: Path) -> Path:
    d = root / "ledgers"
    d.mkdir()
    for name in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME, ledger.LOCK_NAME):
        (d / name).write_bytes(b"")
    return d


@pytest.fixture
def lg(tmp_path, monkeypatch):
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    for k in (config.CORPUS_KEY, config.SCOUT_DIR_KEY):
        monkeypatch.delenv(k, raising=False)
    return d


def hit(rank=1, item="c_0001", lens="shape", score=0.9):
    return {"lens": lens, "rank": rank, "score": score, "pool": "corpus", "trust": "raw", "item": item}


def lines(p: Path) -> list:
    return [json.loads(x) for x in p.read_bytes().decode().splitlines()]


def dig(f) -> str:
    return hashlib.sha256(Path(f).read_bytes()).hexdigest()


def project_file(tmp_path, name="proj", body="x = 1\n") -> Path:
    proj = tmp_path / name
    (proj / ".git").mkdir(parents=True)
    f = proj / "reuse.py"
    f.write_text(body)
    return f


# ------------------------------------------------------------ the key, the folder, the files

def test_unset_key_means_off_and_names_the_key(monkeypatch):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    assert ledger.enabled() is False
    with pytest.raises(config.NotConfigured, match=config.LEDGER_KEY):
        config.ledger_dir()
    with pytest.raises(config.NotConfigured):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])


def test_key_to_a_missing_folder_is_refused_and_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv(config.LEDGER_KEY, str(tmp_path / "nope"))
    with pytest.raises(config.NotConfigured, match="not an existing directory"):
        config.ledger_dir()
    assert not (tmp_path / "nope").exists()


@pytest.mark.parametrize("missing", [ledger.LOCK_NAME, ledger.EXPOSURE_NAME])
def test_a_writer_never_creates_the_lock_or_a_ledger(lg, missing):
    (lg / missing).unlink()
    before = sorted(os.listdir(lg))
    with pytest.raises(ledger.LedgerError, match="switch-on creates it"):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    assert sorted(os.listdir(lg)) == before


# ------------------------------------------------------------ exposure

def test_exposure_rows_carry_their_writer_and_a_result_id_per_lens_and_rank(lg):
    qid, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python",
                                       hits=[hit(1, "c_a"), hit(2, "c_b"), hit(1, "c_a", lens="behaviour")])
    got = lines(lg / ledger.EXPOSURE_NAME)
    assert got == rows and len(got) == 3
    assert {r["writer"] for r in got} == {"scout-search"}
    assert [r["result_id"] for r in got] == [f"fgr-{qid}-shape-1", f"fgr-{qid}-shape-2",
                                             f"fgr-{qid}-behaviour-1"]
    assert all(r["slot"] == "top" and r["band"] is None for r in got)
    assert (lg / ledger.OUTCOME_NAME).read_bytes() == b""          # search never writes outcomes


def test_no_hits_writes_nothing(lg):
    qid, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[])
    assert rows == [] and (lg / ledger.EXPOSURE_NAME).read_bytes() == b""


def test_writer_time_and_measurement_cannot_be_passed_by_a_caller(lg, tmp_path):
    with pytest.raises(TypeError):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()], writer="oracle-run")
    with pytest.raises(TypeError):
        ledger.record_mark(who="pat", item="c_a", now="2026-01-01T00:00:00.000000Z")
    m = ledger.record_mark(who="pat", item="c_a")

    class R:
        returncode = 0

    with pytest.raises(TypeError):                          # review F3: the rc is measured, not given
        ledger.record_event(mark_id=m["mark_id"], project=tmp_path, event_kind="tests",
                            cmd=["false"], run=lambda *a, **k: R())


def test_a_bad_row_is_refused_before_anything_is_written(lg):
    with pytest.raises(ledger.Refused):
        ledger.record_exposure(query_sha256="not-a-sha", query_lang="python", hits=[hit()])
    with pytest.raises(ledger.Refused):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(), hit(0)])
    assert (lg / ledger.EXPOSURE_NAME).read_bytes() == b""


# ------------------------------------------------------------ append discipline (G6)

def test_appends_keep_inode_mode_and_never_rewrite(lg, tmp_path):
    for name in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME):
        os.chmod(lg / name, 0o640)
    st = {n: os.stat(lg / n) for n in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME)}
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    first = (lg / ledger.EXPOSURE_NAME).read_bytes()
    _, rows2 = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    assert (lg / ledger.EXPOSURE_NAME).read_bytes().startswith(first)       # old bytes untouched
    f = project_file(tmp_path)
    ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:x",
                         verdict="PASS", verified_file=f, content_sha256=dig(f))
    ledger.record_mark(who="pat", result_id=rows2[0]["result_id"])
    for n, s in st.items():
        now = os.stat(lg / n)
        assert (now.st_ino, now.st_mode & 0o7777) == (s.st_ino, 0o640), n


def test_a_ledger_not_ending_in_newline_is_left_alone(lg):
    p = lg / ledger.EXPOSURE_NAME
    p.write_bytes(b'{"half": ')
    with pytest.raises(ledger.LedgerError, match="does not end in a newline"):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    assert p.read_bytes() == b'{"half": '


def test_a_failure_after_the_write_cuts_the_file_back(lg, monkeypatch):
    ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    p = lg / ledger.EXPOSURE_NAME
    before = p.read_bytes()
    real = os.fsync
    calls = []

    def boom(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError("disk said no")
        return real(fd)

    monkeypatch.setattr(ledger.os, "fsync", boom)
    with pytest.raises(OSError, match="disk said no"):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(), hit(2)])
    assert p.read_bytes() == before


def test_a_held_lock_blocks_every_writer_and_nothing_is_written(lg, tmp_path):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    mark = ledger.record_mark(who="pat", item="c_y")
    ran = tmp_path / "event-ran"
    snap = {n: (lg / n).read_bytes() for n in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME)}
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import fcntl, sys, time
            fd = open({str(lg / ledger.LOCK_NAME)!r}, 'rb')
            fcntl.flock(fd, fcntl.LOCK_EX)
            print('held', flush=True)
            time.sleep(30)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        f = project_file(tmp_path)
        tries = [
            lambda: ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()], wait=0.2),
            lambda: ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(),
                                         oracle="verify:x", verdict="PASS", verified_file=f, content_sha256=dig(f), wait=0.2),
            lambda: ledger.record_mark(who="pat", item="c_x", wait=0.2),
            lambda: ledger.record_event(mark_id=mark["mark_id"], project=tmp_path, event_kind="tests",
                                        cmd=[sys.executable, "-c", f"open({str(ran)!r}, 'w')"], wait=0.2),
        ]
        for t in tries:
            with pytest.raises(ledger.LockBusy):
                t()
    finally:
        holder.kill()
        holder.wait()
    assert snap == {n: (lg / n).read_bytes() for n in snap}
    assert ran.exists()          # the event's command DID run (before the lock); its result was dropped


def test_concurrent_writers_never_tear_a_line(lg):
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO / 'src')!r})
        from forgeproof import ledger
        for i in range(60):
            ledger.record_exposure(query_sha256={SHA!r}, query_lang='python', d={str(lg)!r}, wait=30,
                                   hits=[dict(lens='shape', rank=r, score=0.5, pool='corpus', trust='raw',
                                              item='c_' + 'x' * 400) for r in range(1, 6)])
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    c = ledger.read(lg)
    assert (len(c.rows[ledger.EXPOSURE_NAME]), c.refused, c.torn) == (4 * 60 * 5, [], [])


# ------------------------------------------------------------ reading

def test_reader_skips_a_torn_last_line_and_refuses_bad_rows_with_file_and_line(lg):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    good = rows[0]
    wrong_writer = dict(good, writer="oracle-run")
    with (lg / ledger.EXPOSURE_NAME).open("ab") as fh:
        fh.write(json.dumps(wrong_writer).encode() + b"\n")
        fh.write(b"not json\n")
        fh.write(json.dumps(dict(good, schema="fg-ledger/v0")).encode() + b"\n")
        fh.write(json.dumps(dict(good, extra=1)).encode() + b"\n")
        fh.write(json.dumps(good)[:40].encode())                       # in flight: no newline
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:                  # an exposure in the outcome file
        fh.write(json.dumps(good).encode() + b"\n")
    c = ledger.read(lg)
    assert c.rows[ledger.EXPOSURE_NAME] == [good] and c.rows[ledger.OUTCOME_NAME] == []
    assert c.torn == [(ledger.EXPOSURE_NAME, 6)]
    where = [(f, n) for f, n, _ in c.refused]
    assert where == [(ledger.EXPOSURE_NAME, 2), (ledger.EXPOSURE_NAME, 3), (ledger.EXPOSURE_NAME, 4),
                     (ledger.EXPOSURE_NAME, 5), (ledger.OUTCOME_NAME, 1)]
    assert "writer" in c.refused[0][2] and "belongs in" in c.refused[4][2]


def test_malformed_lines_are_refused_never_crash_the_reader(lg):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        fh.write(b'{"schema": "fg-ledger/v1", "kind": []}\n')               # unhashable kind (F1)
        fh.write(b'{"schema": "fg-ledger/v1", "kind": {"a": 1}}\n')
        fh.write(b"[" * 100000 + b"\n")                                        # RecursionError (F1)
    c = ledger.counts("c_a")
    assert c == ledger.Counts() and not c.blind
    assert [(f, n) for f, n, _ in ledger.view(lg).refused] == [(ledger.OUTCOME_NAME, 1),
                                                               (ledger.OUTCOME_NAME, 2),
                                                               (ledger.OUTCOME_NAME, 3)]


@pytest.mark.parametrize("field,value", [
    ("run_id", "fgv-0123456789ab\n"),                       # `$` would take a trailing newline (F4)
    ("time", "2026-11-15T10:00:00.00000\u0661Z"),            # Arabic-Indic digit one (F4)
    ("run_id", "fgv-0123456789a\u0661"),
])
def test_one_id_or_time_has_one_spelling(field, value):
    row = {"schema": ledger.SCHEMA, "writer": "oracle-run", "kind": "oracle", "host": "h",
           "time": "2026-11-15T10:00:00.000000Z", "result_id": "fgr-0123456789ab-shape-1",
           "run_id": "fgv-0123456789ab", "oracle": "verify:p", "verdict": "PASS",
           "reused_at": {"file_sha256": SHA, "root_sha256": SHA, "content_sha256": SHA}}
    assert ledger.check_row(row, ledger.OUTCOME_NAME) == ""
    assert ledger.check_row(dict(row, **{field: value}), ledger.OUTCOME_NAME) != ""


def test_unreadable_ledger_counts_are_blind_not_zero(monkeypatch, tmp_path):
    monkeypatch.setenv(config.LEDGER_KEY, str(tmp_path / "gone"))
    c = ledger.counts("c_a")
    assert c.blind and (c.oracle, c.person_with_id, c.person_without_id, c.pending) == (0, 0, 0, 0)


# ------------------------------------------------------------ counts: only outcomes score

def test_selection_alone_scores_nothing(lg):
    for _ in range(25):
        ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a"), hit(2, "c_a")])
    assert ledger.counts("c_a") == ledger.Counts()
    assert len(ledger.exposures("c_a")) == 50


def test_three_counts_side_by_side(lg, tmp_path):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a"), hit(2, "c_b")])
    ra, rb = rows[0]["result_id"], rows[1]["result_id"]
    f = project_file(tmp_path)
    run = ledger.new_run_id()
    ledger.record_oracle(result_id=ra, run_id=run, oracle="verify:p", verdict="PASS", verified_file=f, content_sha256=dig(f))
    ledger.record_oracle(result_id=ra, run_id=ledger.new_run_id(), oracle="verify:p", verdict="PASS", verified_file=f, content_sha256=dig(f))
    f2 = project_file(tmp_path, name="proj2")                     # a second reuse site
    ledger.record_oracle(result_id=ra, run_id=ledger.new_run_id(), oracle="verify:p", verdict="PASS", verified_file=f2, content_sha256=dig(f2))
    m1 = ledger.record_mark(who="pat", result_id=ra)            # passes -> with id
    m1b = ledger.record_mark(who="pat", result_id=ra)           # same person, same result: once
    m2 = ledger.record_mark(who="pat", item="c_a")              # passes, but pat already has an id
    m3 = ledger.record_mark(who="kim", result_id=ra)             # fails -> pending
    m4 = ledger.record_mark(who="pat", result_id=rb)            # other item
    ledger.record_mark(who="lee", result_id=ra)                 # no event -> pending
    ledger.record_mark(who="pat", result_id=ra)                 # no event, but pat already scored on ra
    ok = [sys.executable, "-c", "pass"]
    bad = [sys.executable, "-c", "raise SystemExit(1)"]
    proj = f.parent
    ledger.record_event(mark_id=m1["mark_id"], project=proj, cmd=ok, event_kind="tests")
    ledger.record_event(mark_id=m1b["mark_id"], project=proj, cmd=ok, event_kind="tests")
    ledger.record_event(mark_id=m2["mark_id"], project=proj, cmd=ok, event_kind="build")
    ledger.record_event(mark_id=m3["mark_id"], project=proj, cmd=bad, event_kind="tests")
    ledger.record_event(mark_id=m4["mark_id"], project=proj, cmd=ok, event_kind="tests")
    # two verify runs on proj + one on proj2 = 2 reuse sites; pat's three passing marks on c_a
    # (two by id, one by item) = ONE person with id (D1 as amended, crew review 6E A8)
    assert ledger.counts("c_a") == ledger.Counts(oracle=2, person_with_id=1, person_without_id=0, pending=2)
    assert ledger.counts("c_b") == ledger.Counts(person_with_id=1)
    # the same oracle row appended twice by hand counts once (dedup key result_id + run_id)
    o = next(r for r in lines(lg / ledger.OUTCOME_NAME) if r["kind"] == "oracle")
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        fh.write(json.dumps(o).encode() + b"\n")
    assert ledger.counts("c_a").oracle == 2


def test_an_event_older_than_its_mark_never_scores(lg, monkeypatch):
    monkeypatch.setattr(ledger, "now_utc", lambda: "2026-11-15T10:00:00.000000Z")
    m = ledger.record_mark(who="pat", item="c_a")
    ev = {"schema": ledger.SCHEMA, "writer": "person-mark", "kind": "person-event", "host": "h",
          "time": "2026-11-15T09:00:00.000000Z", "mark_id": m["mark_id"], "event_id": "fge-" + "0" * 12,
          "event_time": "2026-11-15T09:00:00.000000Z", "event_kind": "tests", "rc": 0,
          "cmd_sha256": SHA, "root_sha256": SHA}
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        fh.write(json.dumps(ev).encode() + b"\n")
    assert ledger.counts("c_a") == ledger.Counts(pending=1)


RAN = [sys.executable, "-c", "open('ran', 'w').write('x')"]       # its side effect shows it ran


def test_an_event_cannot_be_recorded_before_its_mark(lg, tmp_path, monkeypatch):
    """5S C5: a mark dated after now is refused BEFORE the person's command runs."""
    with monkeypatch.context() as mp:
        mp.setattr(ledger, "now_utc", lambda: "2099-01-01T00:00:00.000000Z")
        m = ledger.record_mark(who="pat", item="c_a")
    size = (lg / ledger.OUTCOME_NAME).stat().st_size
    with pytest.raises(ledger.Refused, match="older than its mark; nothing was run"):
        ledger.record_event(mark_id=m["mark_id"], project=tmp_path, event_kind="tests", cmd=RAN)
    assert not (tmp_path / "ran").exists()
    assert (lg / ledger.OUTCOME_NAME).stat().st_size == size


def test_a_clock_stepped_back_during_the_run_is_still_refused(lg, tmp_path, monkeypatch):
    """The check after the run stays: the command ran, and the event is not recorded."""
    m = ledger.record_mark(who="pat", item="c_a")
    times = iter([m["time"], "2000-01-01T00:00:00.000000Z"])        # before the run, after it
    monkeypatch.setattr(ledger, "now_utc", lambda: next(times))
    size = (lg / ledger.OUTCOME_NAME).stat().st_size
    with pytest.raises(ledger.Refused, match=r"the event \(2000-.*older than its mark"):
        ledger.record_event(mark_id=m["mark_id"], project=tmp_path, event_kind="tests", cmd=RAN)
    assert (tmp_path / "ran").exists()
    assert (lg / ledger.OUTCOME_NAME).stat().st_size == size


def test_the_ledger_folder_names_are_the_documented_ones():
    """5S-M03: KD-14 (d)'s repair procedure and the switch-on name these files; pin them."""
    assert (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME, ledger.LOCK_NAME) == \
        ("exposure.jsonl", "outcome.jsonl", "ledger.lock")


def test_a_mark_whose_item_differs_from_its_result_is_refused_at_read(lg):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    mark = {"schema": ledger.SCHEMA, "writer": "person-mark", "kind": "person-mark", "host": "h",
            "time": ledger.now_utc(), "mark_id": "fgm-" + "4" * 12, "result_id": rows[0]["result_id"],
            "item": "c_b", "who": "pat"}
    ev = {"schema": ledger.SCHEMA, "writer": "person-mark", "kind": "person-event", "host": "h",
          "time": ledger.now_utc(), "mark_id": mark["mark_id"], "event_id": "fge-" + "5" * 12,
          "event_time": ledger.now_utc(), "event_kind": "tests", "rc": 0,
          "cmd_sha256": SHA, "root_sha256": SHA}
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        for r in (mark, ev):
            fh.write(json.dumps(r).encode() + b"\n")
    v = ledger.view(lg)
    assert mark["mark_id"] not in v.marks
    assert [(f, n) for f, n, why in v.refused if "item differs" in why] == [(ledger.OUTCOME_NAME, 1)]
    assert ledger.counts("c_b") == ledger.Counts() and ledger.counts("c_a") == ledger.Counts()


def test_outcomes_naming_unknown_ids_are_orphans_and_count_nowhere(lg):
    ghost = "fgr-" + "b" * 12 + "-shape-1"
    rows = [
        {"schema": ledger.SCHEMA, "writer": "oracle-run", "kind": "oracle", "host": "h",
         "time": ledger.now_utc(), "result_id": ghost, "run_id": "fgv-" + "1" * 12, "oracle": "verify:p",
         "verdict": "PASS", "reused_at": {"file_sha256": SHA, "root_sha256": SHA, "content_sha256": SHA}},
        {"schema": ledger.SCHEMA, "writer": "person-mark", "kind": "person-event", "host": "h",
         "time": ledger.now_utc(), "mark_id": "fgm-" + "2" * 12, "event_id": "fge-" + "3" * 12,
         "event_time": ledger.now_utc(), "event_kind": "tests", "rc": 0,
          "cmd_sha256": SHA, "root_sha256": SHA},
    ]
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        for r in rows:
            fh.write(json.dumps(r).encode() + b"\n")
    v = ledger.view(lg)
    assert len(v.orphans) == 2 and not v.refused and not v.oracle and not v.marks


def test_a_hand_appended_oracle_row_with_the_wrong_writer_never_scores(lg):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    r = {"schema": ledger.SCHEMA, "writer": "scout-search", "kind": "oracle", "host": "h",
         "time": ledger.now_utc(), "result_id": rows[0]["result_id"], "run_id": "fgv-" + "1" * 12,
         "oracle": "verify:p", "verdict": "PASS", "reused_at": {"file_sha256": SHA, "root_sha256": SHA, "content_sha256": SHA}}
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        fh.write(json.dumps(r).encode() + b"\n")
    assert ledger.counts("c_a").oracle == 0 and len(ledger.view(lg).refused) == 1


# ------------------------------------------------------------ oracle-run's rules

def test_oracle_refuses_anything_but_pass_and_unknown_or_repeated_results(lg, tmp_path):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    rid, f, run = rows[0]["result_id"], project_file(tmp_path), ledger.new_run_id()
    for verdict in ("FAIL", "FLAG", "pass", ""):
        with pytest.raises(ledger.Refused, match="only a PASS"):
            ledger.record_oracle(result_id=rid, run_id=run, oracle="verify:p", verdict=verdict, verified_file=f, content_sha256=dig(f))
    with pytest.raises(ledger.Refused, match="not in this host's exposure"):
        ledger.record_oracle(result_id="fgr-" + "c" * 12 + "-shape-1", run_id=run, oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    ledger.record_oracle(result_id=rid, run_id=run, oracle="verify:p", verdict="PASS", verified_file=f, content_sha256=dig(f))
    with pytest.raises(ledger.Refused, match="already has an outcome"):
        ledger.record_oracle(result_id=rid, run_id=run, oracle="verify:p", verdict="PASS", verified_file=f, content_sha256=dig(f))
    assert len(lines(lg / ledger.OUTCOME_NAME)) == 1


@pytest.mark.parametrize("where", ["ledger", "corpus", "scout", "symlink-into-corpus"])
def test_oracle_refuses_code_where_scout_surfaced_it(lg, tmp_path, monkeypatch, where):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    corpus, scout = tmp_path / "export", tmp_path / "scoutdir"
    (corpus / "run1").mkdir(parents=True)
    (scout / "github-cache" / "o" / "r").mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(corpus))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(scout))
    target = {"ledger": lg / "x.py", "corpus": corpus / "run1" / "x.py",
              "scout": scout / "github-cache" / "o" / "r" / "x.py",
              "symlink-into-corpus": corpus / "run1" / "x.py"}[where]
    target.write_text("x = 1\n")
    f = target
    if where == "symlink-into-corpus":
        f = tmp_path / "proj" / "link.py"
        f.parent.mkdir()
        f.symlink_to(target)
    with pytest.raises(ledger.Refused, match="not independent"):
        ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    assert (lg / ledger.OUTCOME_NAME).read_bytes() == b""


def test_oracle_row_keeps_no_path_text(lg, tmp_path):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit()])
    f0 = project_file(tmp_path, name="PRIVATE_TOKEN_proj")
    (f0.parent / "pkg").mkdir()
    f = f0.parent / "pkg" / "reuse.py"                 # below the root: root != the file's folder
    f.write_text("x = 1\n")
    row = ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                               verdict="PASS", verified_file=f, content_sha256=dig(f))
    assert b"PRIVATE_TOKEN" not in (lg / ledger.OUTCOME_NAME).read_bytes()
    assert row["reused_at"]["root_sha256"] == ledger._sha(str(Path(os.path.realpath(f0.parent))))
    assert row["reused_at"]["file_sha256"] == ledger._sha(str(Path(os.path.realpath(f))))


# ------------------------------------------------------------ person-mark's rules

def test_mark_names_exactly_one_of_result_or_item(lg):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    for kw in ({}, {"result_id": rows[0]["result_id"], "item": "c_a"}):
        with pytest.raises(ledger.Refused, match="exactly one"):
            ledger.record_mark(who="pat", **kw)
    with pytest.raises(ledger.Refused, match="not in this host's exposure"):
        ledger.record_mark(who="pat", result_id="fgr-" + "d" * 12 + "-shape-1")
    m = ledger.record_mark(who="pat", result_id=rows[0]["result_id"])
    assert m["item"] == "c_a" and m["writer"] == "person-mark" and m["kind"] == "person-mark"


def test_event_for_an_unknown_mark_runs_nothing(lg, tmp_path):
    marker = tmp_path / "ran"
    with pytest.raises(ledger.Refused, match="nothing was run"):
        ledger.record_event(mark_id="fgm-" + "e" * 12, project=tmp_path, event_kind="tests",
                            cmd=[sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    assert not marker.exists() and (lg / ledger.OUTCOME_NAME).read_bytes() == b""


def test_event_is_run_and_measured_in_the_project(lg, tmp_path):
    m = ledger.record_mark(who="pat", item="c_a")
    proj = tmp_path / "proj"
    proj.mkdir()
    ev = ledger.record_event(mark_id=m["mark_id"], project=proj, event_kind="tests",
                             cmd=[sys.executable, "-c", "import os; open('cwd.txt','w').write(os.getcwd()); raise SystemExit(3)"])
    assert ev["rc"] == 3 and ev["writer"] == "person-mark"
    assert Path(os.path.realpath((proj / "cwd.txt").read_text())) == Path(os.path.realpath(proj))
    assert ev["event_time"] >= m["time"]
    assert ledger.counts("c_a") == ledger.Counts(pending=1)


def test_event_command_that_cannot_start_records_nothing(lg, tmp_path):
    m = ledger.record_mark(who="pat", item="c_a")
    size = (lg / ledger.OUTCOME_NAME).stat().st_size
    with pytest.raises(ledger.LedgerError, match="Nothing was recorded"):
        ledger.record_event(mark_id=m["mark_id"], project=tmp_path, event_kind="tests",
                            cmd=[str(tmp_path / "no-such-binary")])
    assert (lg / ledger.OUTCOME_NAME).stat().st_size == size


# ------------------------------------------------------------ ids and the comment

def test_result_ids_parse_exactly():
    q = "0123456789ab"
    assert ledger.make_result_id(q, "shape", 3) == f"fgr-{q}-shape-3"
    for bad in [("0123456789aB", "shape", 1), (q, "Shape", 1), (q, "shape", 0), (q, "sh-ape", 1)]:
        with pytest.raises(ledger.LedgerError):
            ledger.make_result_id(*bad)


def test_result_id_comment_lines_are_found_and_nothing_else():
    q = "0123456789ab"
    text = textwrap.dedent(f"""
        # forge-gate-result: fgr-{q}-shape-1
        x = 1  # forge-gate-result: fgr-{q}-shape-2
          // forge-gate-result: fgr-{q}-behaviour-4
        # forge-gate-result: fgr-{q}-shape-0
        # forge-gate-result: fgr-{q}-shape-1
        s = "# forge-gate-result: fgr-{q}-shape-9"
    """)
    assert ledger.result_ids_in(text) == [f"fgr-{q}-shape-1", f"fgr-{q}-behaviour-4"]


# ------------------------------------------------------------ the 8a crew review (6E A1, A3, A8, A9)

def test_one_file_is_one_reuse_site_whatever_ids_it_carries(lg, tmp_path):
    """A9: two handed-out ids of ONE item in one file, one run: one site, not two."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python",
                                     hits=[hit(1, "c_a"), hit(2, "c_a"), hit(3, "c_b")])
    f = project_file(tmp_path)
    run = ledger.new_run_id()
    for r in rows:
        ledger.record_oracle(result_id=r["result_id"], run_id=run, oracle="verify:p", verdict="PASS",
                             verified_file=f, content_sha256=dig(f))
    assert len([r for r in lines(lg / ledger.OUTCOME_NAME) if r["kind"] == "oracle"]) == 3
    assert ledger.counts("c_a").oracle == 1          # one file for c_a
    assert ledger.counts("c_b").oracle == 1          # the same file also reused c_b: its own site


def test_one_person_counts_once_per_item_and_an_id_wins(lg, tmp_path):
    """A8: marking one item by id AND without one is one person, counted with id."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    proj = project_file(tmp_path).parent
    ok = [sys.executable, "-c", "pass"]
    m = ledger.record_mark(who="ann", item="c_a")
    ledger.record_event(mark_id=m["mark_id"], project=proj, cmd=ok, event_kind="tests")
    assert ledger.counts("c_a") == ledger.Counts(person_without_id=1)
    m = ledger.record_mark(who="ann", result_id=rows[0]["result_id"])
    ledger.record_event(mark_id=m["mark_id"], project=proj, cmd=ok, event_kind="tests")
    assert ledger.counts("c_a") == ledger.Counts(person_with_id=1)
    m = ledger.record_mark(who="bob", item="c_a")
    ledger.record_event(mark_id=m["mark_id"], project=proj, cmd=ok, event_kind="tests")
    assert ledger.counts("c_a") == ledger.Counts(person_with_id=1, person_without_id=1)


@pytest.mark.parametrize("where", ["corpus", "elsewhere"])
def test_a_hard_linked_file_is_refused(lg, tmp_path, monkeypatch, where):
    """A3: a hard link into a surfaced root has no surfaced ancestor; any file with >1 link is refused."""
    pool = tmp_path / "pool"
    pool.mkdir()
    monkeypatch.setenv(config.CORPUS_KEY, str(pool))
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    f = project_file(tmp_path)
    other = (pool if where == "corpus" else tmp_path) / "copy.py"
    os.link(f, other)
    size = (lg / ledger.OUTCOME_NAME).stat().st_size
    with pytest.raises(ledger.Refused, match="2 hard links"):
        ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    assert (lg / ledger.OUTCOME_NAME).stat().st_size == size


@pytest.mark.parametrize("content", ["0" * 64, SHA])
def test_the_content_hash_must_be_the_files_bytes(lg, tmp_path, content):
    """A1: the writer itself checks the bytes; a made-up hash is refused."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    f = project_file(tmp_path)
    with pytest.raises(ledger.Refused, match="not the bytes the run judged"):
        ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=content)
    assert ledger.counts("c_a").oracle == 0


# ------------------------------------------------------------ F5 and N3 (the maintainer 2026-11-16)

def _argv_hash(cmd):          # written out here, not imported: the row must be THIS hash of the argv
    return hashlib.sha256(json.dumps(cmd, separators=(",", ":")).encode()).hexdigest()


def test_an_event_row_keeps_the_command_and_the_project_root_only_as_hashes(lg, tmp_path):
    """F5: what ran and where is auditable afterwards, and no command text reaches the ledger."""
    root = tmp_path / "PRIVATE_TOKEN_repo"
    sub = root / "pkg"
    sub.mkdir(parents=True)
    (root / ".git").mkdir()
    m = ledger.record_mark(who="pat", item="c_a")
    cmd = [sys.executable, "-c", "pass", "PRIVATE_TOKEN_arg"]
    ev = ledger.record_event(mark_id=m["mark_id"], project=sub, cmd=cmd, event_kind="tests")
    assert ev["cmd_sha256"] == _argv_hash(cmd)
    assert ev["cmd_sha256"] != _argv_hash(cmd[:-1])
    assert ev["root_sha256"] == ledger._sha(os.path.realpath(root))    # the git root, not pkg/
    at_root = ledger.record_event(mark_id=m["mark_id"], project=root, cmd=cmd, event_kind="tests")
    link = tmp_path / "link"                                            # a symlink to the root
    link.symlink_to(root, target_is_directory=True)
    via_link = ledger.record_event(mark_id=m["mark_id"], project=link, cmd=cmd, event_kind="tests")
    assert at_root["root_sha256"] == via_link["root_sha256"] == ev["root_sha256"]
    assert b"PRIVATE_TOKEN" not in (lg / ledger.OUTCOME_NAME).read_bytes()
    bare = tmp_path / "bare"                                            # no .git anywhere: itself
    bare.mkdir()
    ev2 = ledger.record_event(mark_id=m["mark_id"], project=bare, cmd=cmd[:3], event_kind="build")
    assert ev2["root_sha256"] == ledger._sha(os.path.realpath(bare))
    assert ledger.view(lg).events[m["mark_id"]] == [ev, at_root, via_link, ev2]


def test_an_event_and_an_oracle_row_in_one_git_project_carry_one_root(lg, tmp_path):
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    f0 = project_file(tmp_path)
    (f0.parent / "pkg").mkdir()
    f = f0.parent / "pkg" / "deep.py"
    f.write_text("y = 2\n")
    o = ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    m = ledger.record_mark(who="pat", result_id=rows[0]["result_id"])
    ev = ledger.record_event(mark_id=m["mark_id"], project=f.parent, cmd=[sys.executable, "-c", "pass"],
                             event_kind="tests")
    assert ev["root_sha256"] == o["reused_at"]["root_sha256"]


def test_a_copy_in_the_same_project_is_the_same_reuse_site(lg, tmp_path):
    """N3: `cp a.py b.py` + one more verify no longer counts twice; another project still does."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    rid = rows[0]["result_id"]
    f = project_file(tmp_path)
    copy = f.parent / "sub" / "b.py"
    copy.parent.mkdir()
    copy.write_bytes(f.read_bytes())
    for g in (f, copy):
        ledger.record_oracle(result_id=rid, run_id=ledger.new_run_id(), oracle="verify:p", verdict="PASS",
                             verified_file=g, content_sha256=dig(g))
    assert len([r for r in lines(lg / ledger.OUTCOME_NAME) if r["kind"] == "oracle"]) == 2
    assert ledger.counts("c_a").oracle == 1
    other = project_file(tmp_path, name="proj2")
    ledger.record_oracle(result_id=rid, run_id=ledger.new_run_id(), oracle="verify:p", verdict="PASS",
                         verified_file=other, content_sha256=dig(other))
    assert ledger.counts("c_a").oracle == 2


@pytest.mark.parametrize("drop,bad", [("cmd_sha256", None), ("root_sha256", None),
                                      (None, ("cmd_sha256", "A" * 64)), (None, ("root_sha256", "a" * 63))])
def test_an_event_row_without_both_hashes_is_refused_and_never_scores(lg, drop, bad):
    m = ledger.record_mark(who="pat", item="c_a")
    ev = {"schema": ledger.SCHEMA, "writer": "person-mark", "kind": "person-event", "host": "h",
          "time": ledger.now_utc(), "mark_id": m["mark_id"], "event_id": "fge-" + "6" * 12,
          "event_time": ledger.now_utc(), "event_kind": "tests", "rc": 0,
          "cmd_sha256": SHA, "root_sha256": SHA}
    if drop:
        del ev[drop]
    else:
        ev[bad[0]] = bad[1]
    with (lg / ledger.OUTCOME_NAME).open("ab") as fh:
        fh.write(json.dumps(ev).encode() + b"\n")
    v = ledger.view(lg)
    assert m["mark_id"] not in v.events and len(v.refused) == 1
    assert ("missing or unexpected" if drop else "must be sha256 hex") in v.refused[0][2]
    assert ledger.counts("c_a") == ledger.Counts(pending=1)


def test_a_nested_repository_is_its_own_root_for_events_and_oracle_rows(lg, tmp_path):
    """The innermost folder holding .git wins, for a folder (event) and a file (oracle) alike."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    f = project_file(outer, name="inner")                               # outer/inner/.git + reuse.py
    o = ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    m = ledger.record_mark(who="pat", item="c_a")
    ev = ledger.record_event(mark_id=m["mark_id"], project=f.parent, cmd=[sys.executable, "-c", "pass"],
                             event_kind="tests")
    inner = ledger._sha(os.path.realpath(f.parent))
    assert ev["root_sha256"] == o["reused_at"]["root_sha256"] == inner


def test_an_argv_that_is_not_utf8_is_recorded_and_hashed(lg, tmp_path):
    """POSIX argv may carry undecodable bytes (surrogateescape): the row must still be written."""
    m = ledger.record_mark(who="pat", item="c_a")
    cmd = [sys.executable, "-c", "pass", os.fsdecode(b"\x86")]
    ev = ledger.record_event(mark_id=m["mark_id"], project=tmp_path, cmd=cmd, event_kind="tests")
    assert ev["rc"] == 0 and ev["cmd_sha256"] == _argv_hash(cmd)
    assert ledger.view(lg).events[m["mark_id"]] == [ev]


def test_without_git_an_event_and_an_oracle_row_for_a_top_level_file_agree(lg, tmp_path):
    """Outside any git root both rules fall back to the project folder (a file's own folder)."""
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    proj = tmp_path / "nogit"
    proj.mkdir()
    f = proj / "reuse.py"
    f.write_text("x = 1\n")
    o = ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=f, content_sha256=dig(f))
    m = ledger.record_mark(who="pat", item="c_a")
    ev = ledger.record_event(mark_id=m["mark_id"], project=proj, cmd=[sys.executable, "-c", "pass"],
                             event_kind="tests")
    assert ev["root_sha256"] == o["reused_at"]["root_sha256"] == ledger._sha(os.path.realpath(proj))


def test_one_folder_spelled_in_two_cases_is_one_root(lg, tmp_path):
    """8a s7 review F1: on a case-insensitive disk `Proj` and `PROJ` are one folder, one site."""
    f = project_file(tmp_path, name="Proj")
    other = tmp_path / "PROJ" / "reuse.py"
    if not other.exists():
        pytest.skip("case-sensitive file system: PROJ is not Proj here")
    _, rows = ledger.record_exposure(query_sha256=SHA, query_lang="python", hits=[hit(1, "c_a")])
    for g in (f, other):
        ledger.record_oracle(result_id=rows[0]["result_id"], run_id=ledger.new_run_id(), oracle="verify:p",
                             verdict="PASS", verified_file=g, content_sha256=dig(g))
    assert ledger.counts("c_a").oracle == 1
    m = ledger.record_mark(who="pat", item="c_a")
    a = ledger.record_event(mark_id=m["mark_id"], project=f.parent, cmd=[sys.executable, "-c", "pass"],
                            event_kind="tests")
    b = ledger.record_event(mark_id=m["mark_id"], project=other.parent, cmd=[sys.executable, "-c", "pass"],
                            event_kind="tests")
    assert a["root_sha256"] == b["root_sha256"]


def test_the_event_root_is_taken_before_the_command_runs(lg, tmp_path):
    """8a s7 review F3: a command that creates ../.git must not choose its own audit root."""
    proj = tmp_path / "parent" / "proj"
    proj.mkdir(parents=True)
    before = ledger._sha(os.path.realpath(proj))
    m = ledger.record_mark(who="pat", item="c_a")
    ev = ledger.record_event(mark_id=m["mark_id"], project=proj, event_kind="tests",
                             cmd=[sys.executable, "-c", "import os; os.mkdir('../.git')"])
    assert (proj.parent / ".git").is_dir()                         # the command did run
    assert ev["root_sha256"] == before
