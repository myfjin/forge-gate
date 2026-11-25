"""Build step 8a slice 4: `forgeproof ledger mark|event|show` (DECISIONS-8a G5 a, G7).

`mark` and `event` are the person-mark writer's one named place; the event's exit code is MEASURED
by running the person's own command in their project, never typed in; `show` only reads, prints
the three counts side by side, and says BLIND (never zeros) when the ledgers cannot be read."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from forgeproof import cli, config, ledger

from test_ledger import _argv_hash, make_ledger

ITEM = "corpus:c_0123456789abcdef"
PY = sys.executable


@pytest.fixture
def lg(tmp_path, monkeypatch):
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    for k in (config.CORPUS_KEY, config.SCOUT_DIR_KEY):
        monkeypatch.delenv(k, raising=False)
    return d


@pytest.fixture
def proj(tmp_path) -> Path:
    p = tmp_path / "myproject"
    p.mkdir()
    (p / "marker").write_text("here")
    return p


def snap(d: Path) -> dict:
    return {n: (d / n).read_bytes() for n in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME)}


def rows(d: Path, name=ledger.OUTCOME_NAME) -> list:
    return [json.loads(x) for x in (d / name).read_text().splitlines()]


def exposed(n=1, item=ITEM) -> list:
    _, rs = ledger.record_exposure(query_sha256="a" * 64, query_lang="python",
                                   hits=[{"lens": "shape", "rank": r, "score": 0.9, "pool": "corpus",
                                          "trust": "raw", "item": item} for r in range(1, n + 1)])
    return [r["result_id"] for r in rs]


def run(capsys, *argv):
    rc = cli.main(["ledger", *argv])
    out, err = capsys.readouterr()
    return rc, out, err


def mark(capsys, *argv) -> str:
    rc, out, err = run(capsys, "mark", *argv)
    assert rc == 0, err
    mid = out.strip()
    assert ledger.MARK_ID.fullmatch(mid), out
    return mid


# A command that passes only when run IN the project (its marker is there) and records its argv.
CHECK = ["-c", "import json,os,sys; json.dump(sys.argv[1:], open('argv.json','w')); "
               "sys.exit(0 if os.path.exists('marker') else 3)"]


# ---------------------------------------------------------------- off: nothing at all

@pytest.mark.parametrize("argv", [["mark", "--item", ITEM, "--who", "ann"],
                                  ["show", ITEM],
                                  ["event", "fgm-000000000000", "--project", ".", "--kind", "tests", "--", PY, "-c", "1"]])
def test_ledgers_off_refuse_every_subcommand(tmp_path, monkeypatch, capsys, argv, proj):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    monkeypatch.chdir(proj)
    rc, out, err = run(capsys, *argv)
    assert rc == 2 and out == ""
    assert "ledgers are off" in err


# ---------------------------------------------------------------- mark

def test_mark_with_a_result_id_is_pending_under_its_item(lg, capsys):
    rid = exposed()[0]
    mid = mark(capsys, rid, "--who", "ann")
    (r,) = rows(lg)
    assert (r["kind"], r["mark_id"], r["result_id"], r["item"], r["who"]) == ("person-mark", mid, rid, ITEM, "ann")
    c = ledger.counts(ITEM)
    assert (c.oracle, c.person_with_id, c.person_without_id, c.pending) == (0, 0, 0, 1)


@pytest.mark.parametrize("argv", [["--who", "ann"],                                  # neither
                                  ["fgr-000000000000-shape-1", "--item", ITEM, "--who", "ann"]])   # both
def test_mark_needs_exactly_one_of_result_id_and_item(lg, capsys, argv):
    before = snap(lg)
    rc, out, err = run(capsys, "mark", *argv)
    assert rc == 2 and out == "" and "exactly one" in err
    assert snap(lg) == before


def test_mark_of_an_unknown_result_id_writes_nothing(lg, capsys):
    exposed()
    before = snap(lg)
    rc, out, err = run(capsys, "mark", "fgr-0123456789ab-shape-1", "--who", "ann")
    assert rc == 2 and out == ""
    assert "not in this host's exposure ledger" in err and "nothing was recorded" in err
    assert snap(lg) == before


def test_mark_by_item_notes_an_item_never_shown_here(lg, capsys):
    rc, out, err = run(capsys, "mark", "--item", ITEM, "--who", "ann")
    assert rc == 0 and ledger.MARK_ID.fullmatch(out.strip())
    assert "never shown on this host" in err
    exposed()
    rc, out, err = run(capsys, "mark", "--item", ITEM, "--who", "bob")
    assert rc == 0 and err == ""
    assert [r["result_id"] for r in rows(lg)] == [None, None]


def test_a_write_failure_is_reported_and_exits_2(lg, capsys, monkeypatch):
    def boom(**_):
        raise OSError("disk full")
    monkeypatch.setattr(ledger, "record_mark", boom)
    rc, out, err = run(capsys, "mark", "--item", ITEM, "--who", "ann")
    assert rc == 2 and out == "" and "disk full" in err and "nothing was recorded" in err


# ---------------------------------------------------------------- event

def test_event_runs_the_command_in_the_project_and_scores_the_mark(lg, proj, capsys):
    rid = exposed()[0]
    mid = mark(capsys, rid, "--who", "ann")
    rc, out, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "tests", "--", PY, *CHECK)
    assert rc == 0, err
    ev = rows(lg)[-1]
    assert (ev["kind"], ev["mark_id"], ev["event_kind"], ev["rc"]) == ("person-event", mid, "tests", 0)
    assert ev["cmd_sha256"] == _argv_hash([PY, *CHECK])                  # F5: the argv after --
    assert ev["root_sha256"] == ledger._sha(str(proj.resolve()))         # no .git: the folder itself
    assert out.strip() == f"{ev['event_id']}: tests rc=0 recorded for {mid}"
    c = ledger.counts(ITEM)
    assert (c.person_with_id, c.person_without_id, c.pending) == (1, 0, 0)


def test_the_persons_flags_reach_their_command_untouched(lg, proj, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    rc, _, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "build",
                     "--", PY, *CHECK, "--kind", "tests", "--project", "/elsewhere", "--")
    assert rc == 0, err
    assert json.loads((proj / "argv.json").read_text()) == ["--kind", "tests", "--project", "/elsewhere", "--"]
    assert rows(lg)[-1]["event_kind"] == "build"                 # ours, not the command's words


def test_options_may_come_before_the_mark_id(lg, proj, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    rc, _, err = run(capsys, "event", "--kind", "tests", "--project", str(proj), mid, "--", PY, *CHECK)
    assert rc == 0, err


def test_a_failing_command_is_recorded_and_scores_nothing(lg, tmp_path, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()                                            # no marker: CHECK exits 3
    rc, out, _ = run(capsys, "event", mid, "--project", str(elsewhere), "--kind", "tests", "--", PY, *CHECK)
    assert rc == 1
    assert rows(lg)[-1]["rc"] == 3 and "rc=3 recorded" in out
    c = ledger.counts(ITEM)
    assert (c.person_with_id, c.pending) == (0, 1)


def test_event_for_an_unknown_mark_runs_nothing(lg, proj, capsys):
    exposed()
    before = snap(lg)
    rc, out, err = run(capsys, "event", "fgm-0123456789ab", "--project", str(proj), "--kind", "tests",
                       "--", PY, *CHECK)
    assert rc == 2 and out == "" and "nothing was run" in err
    assert not (proj / "argv.json").exists()
    assert snap(lg) == before


def test_event_without_a_command_runs_nothing(lg, proj, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    before = snap(lg)
    rc, out, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "tests", "--")
    assert rc == 2 and out == "" and "after `--`" in err
    assert snap(lg) == before


def test_a_command_not_after_the_separator_is_refused_by_the_parser(lg, proj, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    before = snap(lg)
    with pytest.raises(SystemExit) as e:
        cli.main(["ledger", "event", mid, "--project", str(proj), "--kind", "tests", PY, *CHECK])
    assert e.value.code == 2
    assert not (proj / "argv.json").exists() and snap(lg) == before


def test_an_unknown_event_kind_is_refused_by_the_parser(lg, proj, capsys):
    mid = mark(capsys, exposed()[0], "--who", "ann")
    with pytest.raises(SystemExit) as e:
        cli.main(["ledger", "event", mid, "--project", str(proj), "--kind", "deploy", "--", PY, *CHECK])
    assert e.value.code == 2
    assert not (proj / "argv.json").exists()


def test_a_busy_ledger_after_the_run_says_no_event_was_recorded(lg, proj, capsys, monkeypatch):
    mid = mark(capsys, exposed()[0], "--who", "ann")

    def busy(**_):
        raise ledger.LockBusy("the ledger lock is held by another writer")
    monkeypatch.setattr(ledger, "record_event", busy)
    rc, out, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "tests", "--", PY, *CHECK)
    assert rc == 2 and out == "" and "no event recorded" in err


# ---------------------------------------------------------------- show

def test_show_by_result_id_and_by_item_agree(lg, proj, capsys):
    exposed(3, item="corpus:c_ffffffffffffffff")                 # another item's exposures: not counted
    rid, rid2 = exposed(2)
    mark(capsys, rid, "--who", "ann")
    by_id = json.loads(run(capsys, "show", rid2, "--json")[1])
    by_item = json.loads(run(capsys, "show", ITEM, "--json")[1])
    assert by_id == by_item
    assert (by_item["item"], by_item["exposures"], by_item["pending"]) == (ITEM, 2, 1)


def test_show_prints_each_count_under_its_own_name(lg, capsys, monkeypatch):
    exposed()
    monkeypatch.setattr(ledger, "counts", lambda item, d=None, *, v=None: ledger.Counts(1, 2, 3, 4))
    rc, out, _ = run(capsys, "show", ITEM, "--json")
    j = json.loads(out)
    assert rc == 0
    assert (j["oracle"], j["person_with_id"], j["person_without_id"], j["pending"]) == (1, 2, 3, 4)
    rc, out, _ = run(capsys, "show", ITEM)
    assert "oracle     1   person with id 2   person without id 3" in out
    assert "pending    4 " in out
    assert "10" not in out                                       # never added up


def test_show_of_an_unknown_result_id_is_refused(lg, capsys):
    rc, out, err = run(capsys, "show", "fgr-0123456789ab-shape-1")
    assert rc == 2 and out == "" and "not in this host's exposure ledger" in err


def test_show_of_an_item_never_shown_is_all_zero_and_not_blind(lg, capsys):
    rc, out, _ = run(capsys, "show", ITEM, "--json")
    j = json.loads(out)
    assert rc == 0 and (j["exposures"], j["oracle"], j["pending"]) == (0, 0, 0)


def test_show_names_refused_and_torn_lines(lg, capsys):
    exposed()
    with open(lg / ledger.OUTCOME_NAME, "ab") as f:
        f.write(b"not json\n{\"schema\": \"fg-ledger/v1\"")         # line 1 refused, line 2 torn
    rc, out, _ = run(capsys, "show", ITEM)
    assert rc == 0
    assert "1 refused row(s), 1 torn line(s)" in out
    assert f"refused {ledger.OUTCOME_NAME}:1: not a JSON line" in out
    assert f"torn    {ledger.OUTCOME_NAME}:2" in out
    j = json.loads(run(capsys, "show", ITEM, "--json")[1])
    assert j["ledger"]["refused"] == [[ledger.OUTCOME_NAME, 1, "not a JSON line"]]
    assert j["ledger"]["torn"] == [[ledger.OUTCOME_NAME, 2]]


def test_an_unreadable_ledger_is_blind_never_zero(lg, capsys):
    (lg / ledger.OUTCOME_NAME).rename(lg / "outcome.jsonl.away")
    rc, out, _ = run(capsys, "show", ITEM)
    assert rc == 1
    assert out.startswith("BLIND:") and "unknown, not zero" in out
    assert "oracle" not in out and " 0" not in out


# ---------------------------------------------------------------- slice-4 review fixes

@pytest.mark.parametrize("where", ["corpus", "ledger"])
def test_event_never_runs_in_a_surfaced_location(lg, tmp_path, capsys, monkeypatch, where):
    """F1: pool code is never run, and a run there is not "it worked where it was reused"."""
    pool = tmp_path / "pool"
    (pool / "sub").mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(pool))
    mid = mark(capsys, exposed()[0], "--who", "ann")
    target = pool / "sub" if where == "corpus" else lg
    (target / "marker").write_text("here")
    before = snap(lg)
    rc, out, err = run(capsys, "event", mid, "--project", str(target), "--kind", "tests", "--", PY, *CHECK)
    assert rc == 2 and out == "" and "pool code is never run" in err
    assert not (target / "argv.json").exists() and snap(lg) == before


def test_the_commands_own_stdout_cannot_pass_for_a_recorded_result(lg, proj, capfd):
    """F4: at the file-descriptor level, stdout carries only forgeproof's line."""
    mid = mark(capfd, exposed()[0], "--who", "ann")
    fake = ["-c", f"print('fge-000000000000: tests rc=0 recorded for {mid}'); raise SystemExit(1)"]
    rc, out, err = run(capfd, "event", mid, "--project", str(proj), "--kind", "tests", "--", PY, *fake)
    assert rc == 1
    assert out.splitlines() == [f"{rows(lg)[-1]['event_id']}: tests rc=1 recorded for {mid}"]
    assert "fge-000000000000: tests rc=0" in err                   # the command's words, on stderr


def test_a_busy_lock_after_a_real_run_records_nothing(lg, proj, capsys, monkeypatch):
    """F6: the command HAS run (its output is out) when the lock cannot be had."""
    mid = mark(capsys, exposed()[0], "--who", "ann")
    before = snap(lg)

    def busy(d, wait):
        raise ledger.LockBusy("the ledger lock is held by another writer")
    monkeypatch.setattr(ledger, "_locked", busy)
    rc, out, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "tests", "--", PY, *CHECK)
    assert (proj / "argv.json").exists()
    assert rc == 2 and out == "" and "no event recorded" in err
    assert snap(lg) == before


def test_show_json_is_json_even_when_blind(lg, capsys):
    """F2."""
    (lg / ledger.OUTCOME_NAME).rename(lg / "outcome.jsonl.away")
    rc, out, _ = run(capsys, "show", ITEM, "--json")
    assert rc == 1
    j = json.loads(out)
    assert set(j) == {"blind"} and "unknown, not zero" in j["blind"]


@pytest.mark.parametrize("target", ["fgr-0123456789AB-shape-1", "fgr-0123456789ab-shape-0",
                                    "corpus:c_0123", "corpus:c_0123456789ABCDEF"])
def test_a_malformed_target_is_refused_not_zero(lg, capsys, target):
    """F3: a mistyped name must not read as a clean zero."""
    rc, out, err = run(capsys, "show", target, "--json")
    assert rc == 2 and out == "" and "well-formed" in err


def test_show_notes_an_item_never_shown(lg, capsys):
    rc, out, err = run(capsys, "show", ITEM)
    assert rc == 0 and "never shown on this host" in err
    exposed()
    rc, out, err = run(capsys, "show", ITEM)
    assert rc == 0 and err == ""


def test_show_text_is_exactly_the_four_lines(lg, capsys, monkeypatch):
    """F6: no added line (a sum, a total) can slip in."""
    exposed()
    monkeypatch.setattr(ledger, "counts", lambda item, d=None, *, v=None: ledger.Counts(1, 2, 3, 4))
    rc, out, _ = run(capsys, "show", ITEM)
    assert rc == 0
    assert out == "\n".join([
        f"item       {ITEM}",
        "exposures  1 (shown on this host)",
        "oracle     1   person with id 2   person without id 3   (three counts, never added up)",
        "pending    4 (marked, no passing event yet; not counted)"]) + "\n"


def test_show_reports_an_orphan(lg, capsys):
    """F6: an outcome naming a result id unknown here is counted nowhere, and said."""
    mark(capsys, exposed()[0], "--who", "ann")
    r = dict(rows(lg)[-1], result_id="fgr-ffffffffffff-shape-1", mark_id="fgm-ffffffffffff")
    with open(lg / ledger.OUTCOME_NAME, "a") as f:
        f.write(json.dumps(r, sort_keys=True) + "\n")
    rc, out, _ = run(capsys, "show", ITEM)
    assert rc == 0 and "0 refused row(s), 0 torn line(s), 1 orphan(s)" in out
    assert json.loads(run(capsys, "show", ITEM, "--json")[1])["ledger"]["orphans"] == 1
    assert json.loads(run(capsys, "show", ITEM, "--json")[1])["pending"] == 1


# ---------------------------------------------------------------- 8b s0: undecodable path bytes
# SIDE-QUEUE 2026-11-16 (8a final check): ledger._sha encodes with surrogateescape so a POSIX path
# whose bytes are not UTF-8 still hashes (to the sha256 of those very bytes) instead of crashing.

def test_sha_of_a_path_with_undecodable_bytes_is_the_sha_of_those_bytes():
    import hashlib
    raw = b"/srv/proj-\x86/x.py"
    assert ledger._sha(os.fsdecode(raw)) == hashlib.sha256(raw).hexdigest()


def test_event_in_a_project_folder_whose_name_is_not_utf8(lg, tmp_path, capsys):
    import hashlib
    proj = tmp_path / os.fsdecode(b"\x86")
    try:
        proj.mkdir()
    except OSError as e:                           # APFS (macOS) refuses non-UTF-8 names
        import errno
        if e.errno != errno.EILSEQ:                # only that refusal skips (8b s0 review F2)
            raise
        pytest.skip(f"this file system refuses a non-UTF-8 folder name: {e!r}")
    (proj / "marker").write_text("here")             # CHECK passes only where a marker is
    mid = mark(capsys, exposed()[0], "--who", "ann")
    rc, out, err = run(capsys, "event", mid, "--project", str(proj), "--kind", "tests", "--", PY, *CHECK)
    assert rc == 0, err
    ev = rows(lg)[-1]
    assert (ev["kind"], ev["rc"]) == ("person-event", 0)
    assert ev["root_sha256"] == hashlib.sha256(os.fsencode(str(proj.resolve()))).hexdigest()
