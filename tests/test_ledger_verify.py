"""Build step 8a slice 3: `forgeproof verify` is the oracle-run writer (design §4a.5 V4-2;
DECISIONS-8a G4 a, D3). A default-strength final PASS on a file that carries a handed-out result id
AND the code behind it appends one oracle outcome per id; nothing else does, and the ledger never
changes verify's verdict, stdout or exit code. Fixtures only."""
from __future__ import annotations

import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from forgeproof import cli, config, ledger
from forgeproof.catalog import reference_path

from test_ledger import make_ledger
from test_scout_slice1 import _export

PATTERN = "perceptron"
SNIPPET = "def helper(xs):\n    total = 0\n    for x in xs:\n        total += x\n    return total\n"


def cid(body: str) -> str:
    return "c_" + hashlib.sha256(body.encode()).hexdigest()[:16]


@pytest.fixture
def lg(tmp_path, monkeypatch):
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    monkeypatch.delenv(config.SCOUT_DIR_KEY, raising=False)
    root = tmp_path / "export" / "v1"
    root.mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(root))
    bodies = [SNIPPET, reference_path(PATTERN).read_text()]
    _export(root, "20261102T000000Z", [{"item_id": cid(b), "body": b, "lang": "python", "kind": "write"}
                                       for b in bodies])
    return d


def exposed(body=SNIPPET, n=1) -> list:
    _, rows = ledger.record_exposure(query_sha256="a" * 64, query_lang="python",
                                     hits=[{"lens": "shape", "rank": r, "score": 0.9, "pool": "corpus",
                                            "trust": "raw", "item": f"corpus:{cid(body)}"} for r in range(1, n + 1)])
    return [r["result_id"] for r in rows]


def reused(tmp_path, rids, body=None) -> Path:
    """A project file: the id lines, then the handed-out code re-indented inside a class, plus more."""
    proj = tmp_path / "myproject"
    (proj / ".git").mkdir(parents=True, exist_ok=True)
    f = proj / "model.py"
    head = "".join(f"# forge-gate-result: {r}\n" for r in rids)
    if body is None:
        body = "class Model:\n\n" + "".join("    " + ln + "\n" if ln else "\n" for ln in SNIPPET.splitlines()) \
               + "\nx = 1\n"
    f.write_text(head + body)
    return f


def outcomes(lg) -> list:
    return [json.loads(x) for x in (lg / ledger.OUTCOME_NAME).read_text().splitlines()]


def fake_verify(monkeypatch, final, base=None, during=None):
    """cmd_verify's own flow; the oracle runs and lenses replaced by fixed statuses. ``during`` runs
    inside the first run_file call, i.e. while verify is running."""
    calls = []

    def run_file(*a, **k):
        if during and not calls:
            during()
        calls.append(1)

    monkeypatch.setattr(cli, "run_file", run_file)
    monkeypatch.setattr(cli, "verdict",
                        lambda **k: types.SimpleNamespace(status=base or final, reason="r", metrics={}))
    gate = types.SimpleNamespace(status=final, as_dict=lambda: {"status": final})
    monkeypatch.setattr(cli, "evaluate", lambda **k: gate)
    monkeypatch.setattr(cli, "render", lambda g, show_table=True: f"VERDICT {g.status}")


def verify(f, *extra):
    return cli.main(["verify", str(f), "--pattern", PATTERN, *extra])


# ------------------------------------------------------------ the real thing, once

def test_a_real_pass_on_reused_code_records_one_oracle_outcome(lg, tmp_path, capsys):
    ref = reference_path(PATTERN).read_text()
    rid, = exposed(body=ref)
    f = reused(tmp_path, [rid], body=ref)
    rc = verify(f, "--json")
    cap = capsys.readouterr()
    assert rc == 0 and json.loads(cap.out)["status"] == "PASS"            # stdout stays pure JSON
    r, = outcomes(lg)
    assert (r["writer"], r["kind"], r["result_id"], r["oracle"], r["verdict"]) == \
           ("oracle-run", "oracle", rid, f"verify:{PATTERN}", "PASS")
    assert r["reused_at"]["content_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert r["run_id"] in cap.err and "recorded" in cap.err
    assert ledger.counts(f"corpus:{cid(ref)}").oracle == 1


# ------------------------------------------------------------ the rules, on cmd_verify's flow

def test_reused_code_reindented_counts(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    rid, = exposed()
    assert verify(reused(tmp_path, [rid])) == 0
    assert [r["result_id"] for r in outcomes(lg)] == [rid]


@pytest.mark.parametrize("body", [
    "x = 1\n",                                                              # the code is gone
    SNIPPET.replace("total += x", "total -= x"),                            # the code was changed
])
def test_the_id_comment_without_the_code_records_nothing(lg, tmp_path, capsys, monkeypatch, body):
    fake_verify(monkeypatch, "PASS")
    rid, = exposed()
    assert verify(reused(tmp_path, [rid], body=body)) == 0
    assert outcomes(lg) == [] and "is not in the file" in capsys.readouterr().err


@pytest.mark.parametrize("status,rc", [("FAIL", 1), ("UNSTABLE", 1), ("UNSUPPORTED", 1)])
def test_anything_but_pass_records_nothing_and_keeps_its_exit_code(lg, tmp_path, capsys, monkeypatch, status, rc):
    fake_verify(monkeypatch, status)
    assert verify(reused(tmp_path, exposed())) == rc
    assert outcomes(lg) == [] and "not an outcome" in capsys.readouterr().err


def test_the_final_verdict_counts_not_the_base_verdict(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "FAIL", base="PASS")                         # a lens downgraded the PASS (F2)
    assert verify(reused(tmp_path, exposed())) == 1
    assert outcomes(lg) == []


@pytest.mark.parametrize("extra,why", [
    (["--tolerance", "1e-3"], "tolerance"), (["--allow-network"], "network"),
    (["--lenses", "property-primary"], "lenses off"),
    (["--timeout", "120"], "timeout 120"), (["--memory-mb", "1024"], "memory 1024"),   # 6E D3
])
def test_a_run_looser_than_the_default_records_nothing(lg, tmp_path, capsys, monkeypatch, extra, why):
    fake_verify(monkeypatch, "PASS")
    assert verify(reused(tmp_path, exposed()), *extra) == 0
    err = capsys.readouterr().err
    assert outcomes(lg) == [] and "looser than the default" in err and why in err


@pytest.mark.parametrize("extra", [[], ["--lenses", "all"], ["--tolerance", "1e-7"],
                                   ["--timeout", "30"], ["--memory-mb", "256"]])
def test_a_run_as_strict_as_the_default_records(lg, tmp_path, capsys, monkeypatch, extra):
    fake_verify(monkeypatch, "PASS")
    assert verify(reused(tmp_path, exposed()), *extra) == 0
    assert len(outcomes(lg)) == 1


def test_the_file_is_judged_as_it_was_before_the_run(lg, tmp_path, capsys, monkeypatch):
    first, second = exposed(n=2)
    f = reused(tmp_path, [first])
    fake_verify(monkeypatch, "PASS")
    assert verify(f) == 0
    r, = outcomes(lg)
    assert r["reused_at"]["content_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()


def test_a_file_changed_during_the_run_records_nothing(lg, tmp_path, capsys, monkeypatch):
    """F3, tightened by the crew review (6E A1): the writer compares the judged bytes with the file,
    so an id added during the run is never credited, and neither is the id that was there."""
    first, second = exposed(n=2)
    f = reused(tmp_path, [first])
    fake_verify(monkeypatch, "PASS",
                during=lambda: f.write_text(f"# forge-gate-result: {second}\n" + f.read_text()))
    assert verify(f) == 0
    assert outcomes(lg) == [] and "not the bytes the run judged" in capsys.readouterr().err


def test_one_run_credits_every_id_in_the_file_with_one_run_id(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    rids = exposed(n=2)
    assert verify(reused(tmp_path, rids)) == 0
    rows = outcomes(lg)
    assert [r["result_id"] for r in rows] == rids and len({r["run_id"] for r in rows}) == 1
    assert ledger.counts(f"corpus:{cid(SNIPPET)}").oracle == 1     # one file, one site (6E A9)


def test_one_lock_wait_covers_all_ids(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    waits, real = [], ledger.record_oracle

    def spy(**kw):
        waits.append(kw["wait"])
        return real(**kw)

    monkeypatch.setattr(ledger, "record_oracle", spy)
    assert verify(reused(tmp_path, exposed(n=2))) == 0
    assert len(waits) == 2 and waits[0] <= cli.ORACLE_WAIT and waits[1] < cli.ORACLE_WAIT   # F7


def test_a_file_without_an_id_records_nothing(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    assert verify(reused(tmp_path, [])) == 0
    assert outcomes(lg) == [] and "no forge-gate-result id" in capsys.readouterr().err


def test_ledger_off_verify_is_unchanged_and_silent(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    fake_verify(monkeypatch, "PASS")
    assert verify(reused(tmp_path, ["fgr-" + "a" * 12 + "-shape-1"])) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("breakage", ["no-lock", "disk-error", "unexpected"])
def test_an_unwritable_ledger_never_changes_the_verdict(lg, tmp_path, capsys, monkeypatch, breakage):
    fake_verify(monkeypatch, "PASS")
    f = reused(tmp_path, exposed())
    if breakage == "no-lock":
        (lg / ledger.LOCK_NAME).unlink()
    elif breakage == "disk-error":
        def no_space(fd):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(ledger.os, "fsync", no_space)
    else:                                                                   # F5
        def boom(**kw):
            raise ValueError("a bug somewhere below")
        monkeypatch.setattr(ledger, "record_oracle", boom)
    assert verify(f) == 0
    err = capsys.readouterr().err
    assert ("BLIND(unexpected ValueError" if breakage == "unexpected" else "BLIND(ledger-unwritable") in err
    assert (lg / ledger.OUTCOME_NAME).read_bytes() == b""


def test_refusals_are_reported_and_other_ids_still_count(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    good, = exposed()
    unknown = "fgr-" + "e" * 12 + "-shape-1"
    assert verify(reused(tmp_path, [unknown, good])) == 0
    err = capsys.readouterr().err
    assert f"{unknown}: refused" in err and f"{good}: oracle outcome recorded" in err
    assert [r["result_id"] for r in outcomes(lg)] == [good]


def test_verifying_the_code_where_scout_surfaced_it_is_refused(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    scout_dir = tmp_path / "scout"
    (scout_dir / "github-cache").mkdir(parents=True)
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(scout_dir))
    rid, = exposed()
    f = scout_dir / "github-cache" / "x.py"
    f.write_text(f"# forge-gate-result: {rid}\n" + SNIPPET)
    assert verify(f) == 0
    assert "not independent" in capsys.readouterr().err and outcomes(lg) == []


@pytest.mark.skipif(not os.path.exists(str(Path(__file__).parent).swapcase()),
                    reason="case-sensitive file system: one spelling per folder")
def test_another_spelling_of_the_scout_folder_is_still_refused(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")                                       # F6 (macOS dev host)
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(scout_dir))
    rid, = exposed()
    (scout_dir / "x.py").write_text(f"# forge-gate-result: {rid}\n" + SNIPPET)
    assert verify(tmp_path / "scout" / "x.py") == 0
    assert "not independent" in capsys.readouterr().err and outcomes(lg) == []


def test_the_note_never_reaches_stdout(lg, tmp_path, capsys, monkeypatch):
    fake_verify(monkeypatch, "PASS")
    verify(reused(tmp_path, exposed()))
    assert capsys.readouterr().out == "VERDICT PASS\n"
