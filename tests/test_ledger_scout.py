"""Build step 8a slice 2: scout's search path writes the exposure ledger (writer scout-search) and
hands out a result's code with its result id (`scout --show`, V41-2). Fixtures only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from forge_gate import cli, config, ledger
from forge_gate.scout import report, rule2

from test_ledger import make_ledger
from test_scout_slice1 import CANARY_NAME, _bodies, _export, _scout

TOKEN = "QUERY_PRIVATE_TOKEN_8a"
QUERY = rule2.NEAREST + f"\n# {TOKEN}\n"


def cid(body: str) -> str:
    return "c_" + hashlib.sha256(body.encode()).hexdigest()[:16]


def real_ids(items: list) -> list:
    """The slice-1 fixture bodies with REAL content ids (what the exporter writes)."""
    return [dict(i, item_id=cid(i["body"])) for i in items]


@pytest.fixture
def corpus_run(tmp_path, monkeypatch):
    root = tmp_path / "export" / "v1"
    root.mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(root))
    _export(root, "20261102T000000Z", real_ids(_bodies(tmp_path / CANARY_NAME)))
    return root


def one_item_export(tmp_path, monkeypatch, body, lang="python", item_id=None):
    root = tmp_path / "export1" / "v1"
    root.mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(root))
    iid = item_id or cid(body)
    _export(root, "20261102T000000Z", [{"item_id": iid, "body": body, "lang": lang, "kind": "write"}])
    _, rows = ledger.record_exposure(query_sha256="a" * 64, query_lang=lang,
                                     hits=[{"lens": "shape", "rank": 1, "score": 1.0, "pool": "corpus",
                                            "trust": "raw", "item": f"corpus:{iid}"}])
    return rows[0]["result_id"]


@pytest.fixture
def lg(tmp_path, monkeypatch):
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    return d


def snap(d: Path) -> dict:
    return {n: (d / n).read_bytes() for n in (ledger.EXPOSURE_NAME, ledger.OUTCOME_NAME)}


def test_enabled_follows_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    assert ledger.enabled() is False
    monkeypatch.setenv(config.LEDGER_KEY, str(tmp_path))
    assert ledger.enabled() is True


def test_ledger_off_scout_says_so_and_still_succeeds(corpus_run, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    rc, rep = _scout(tmp_path, QUERY, capsys=capsys)
    e = rep["checks"]["exposure"]
    assert rc == 0 and e["outcome"] == "BLIND" and e["reason"].startswith("ledger-off")
    assert config.LEDGER_KEY in e["reason"]
    assert all("result_id" not in h for h in rep["related"]["corpus"]["shape_hits"])


def test_every_shown_result_gets_one_exposure_row_and_its_id(corpus_run, lg, tmp_path, capsys):
    rc, rep = _scout(tmp_path, QUERY, capsys=capsys)
    assert rc == 0 and rep["checks"]["exposure"]["outcome"] == "FOUND"
    hits = rep["related"]["corpus"]["shape_hits"]
    rows = [json.loads(x) for x in (lg / ledger.EXPOSURE_NAME).read_text().splitlines()]
    shown = [("shape", r, h) for r, h in enumerate(hits, 1)] + \
            [("behaviour", r, h) for r, h in enumerate(rep["related"]["corpus"]["behaviour_hits"], 1)]
    assert len(rows) == len(shown) >= 1
    qids = {r["query_id"] for r in rows}
    assert len(qids) == 1
    for row, (lens, rank, h) in zip(rows, shown):
        assert (row["writer"], row["lens"], row["rank"], row["item"], row["score"]) == \
               ("scout-search", lens, rank, f"corpus:{h['item_id']}", h["score"])
        assert h["result_id"] == row["result_id"]
    assert rows[0]["query_sha256"] == hashlib.sha256((tmp_path / "query.py").read_bytes()).hexdigest()
    assert (lg / ledger.OUTCOME_NAME).read_bytes() == b""          # the search path never writes outcomes


def test_each_lens_list_is_exposed_in_its_own_ranks(lg):
    """Both lenses' lists (the dev host has no box, so behaviour hits are built here directly)."""
    def h(item, score):
        return {"item_id": item, "score": score, "pool": "corpus", "trust": "raw", "kind": "write"}
    rep_ = {"sha256": "b" * 64, "language": "python",
            "related": {"corpus": {"shape_hits": [h("c_1", 0.9), h("c_2", 0.5)],
                                   "behaviour_hits": [h("c_2", 1.0), h("c_3", 0.75), h("c_1", 0.5)]}}}
    o = report._expose(rep_)
    rows = [json.loads(x) for x in (lg / ledger.EXPOSURE_NAME).read_text().splitlines()]
    assert o.kind == "FOUND"
    assert [(r["lens"], r["rank"], r["item"], r["score"]) for r in rows] == [
        ("shape", 1, "corpus:c_1", 0.9), ("shape", 2, "corpus:c_2", 0.5),
        ("behaviour", 1, "corpus:c_2", 1.0), ("behaviour", 2, "corpus:c_3", 0.75), ("behaviour", 3, "corpus:c_1", 0.5)]
    c = rep_["related"]["corpus"]
    assert [x["result_id"] for x in c["shape_hits"] + c["behaviour_hits"]] == [r["result_id"] for r in rows]


def test_the_human_report_prints_each_result_id(corpus_run, lg, tmp_path, capsys):
    rc, out = _scout(tmp_path, QUERY, json_out=False, capsys=capsys)
    rids = [r["result_id"] for r in map(json.loads, (lg / ledger.EXPOSURE_NAME).read_text().splitlines())]
    assert rc == 0 and rids and all(f"id={rid}" in out for rid in rids)
    assert report.check_report_outcomes(out) == []


def test_the_ledger_keeps_no_query_text(corpus_run, lg, tmp_path, capsys):
    _scout(tmp_path, QUERY, capsys=capsys)
    assert TOKEN.encode() not in b"".join(snap(lg).values())


def test_nothing_shown_records_nothing(tmp_path, lg, capsys, monkeypatch):
    monkeypatch.setenv(config.CORPUS_KEY, str(tmp_path / "no-export"))       # corpus BLIND: no hits
    (tmp_path / "no-export").mkdir()
    rc, rep = _scout(tmp_path, QUERY, capsys=capsys)
    assert rc == 0 and rep["checks"]["exposure"]["outcome"] == "LOOKED-CLEAN"
    assert snap(lg) == {ledger.EXPOSURE_NAME: b"", ledger.OUTCOME_NAME: b""}


@pytest.mark.parametrize("breakage", ["no-lock", "no-folder", "torn-tail", "disk-error"])
def test_an_unwritable_ledger_is_blind_and_never_fails_scout(corpus_run, lg, tmp_path, capsys, monkeypatch, breakage):
    if breakage == "no-lock":
        (lg / ledger.LOCK_NAME).unlink()
    elif breakage == "no-folder":
        monkeypatch.setenv(config.LEDGER_KEY, str(tmp_path / "missing"))
    elif breakage == "torn-tail":
        (lg / ledger.EXPOSURE_NAME).write_bytes(b'{"half"')
    else:                                                     # a raw OSError from the write (review F4)
        def no_space(fd):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(ledger.os, "fsync", no_space)
    rc, rep = _scout(tmp_path, QUERY, capsys=capsys)
    e = rep["checks"]["exposure"]
    assert rc == 0 and e["outcome"] == "BLIND" and e["reason"].startswith("ledger-unwritable")
    assert rep["related"]["corpus"]["lenses"]["shape"]["outcome"] == "FOUND"      # the report is intact
    if breakage == "disk-error":
        assert (lg / ledger.EXPOSURE_NAME).read_bytes() == b""                     # cut back


def test_scout_waits_briefly_for_another_writer(corpus_run, lg, tmp_path, capsys, monkeypatch):
    seen = {}
    real = ledger.record_exposure

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(ledger, "record_exposure", spy)
    _scout(tmp_path, QUERY, capsys=capsys)
    assert seen["wait"] == report.EXPOSE_WAIT <= 2.0


# ------------------------------------------------------------ --show

def _first_rid(lg):
    return json.loads((lg / ledger.EXPOSURE_NAME).read_text().splitlines()[0])


def test_show_hands_out_the_code_with_its_result_id_and_writes_nothing(corpus_run, lg, tmp_path, capsys):
    _scout(tmp_path, QUERY, capsys=capsys)
    row = _first_rid(lg)
    before = snap(lg)
    rc = cli.main(["scout", "--show", row["result_id"]])
    out = capsys.readouterr().out
    body = next(b["body"] for b in real_ids(_bodies(tmp_path / CANARY_NAME)) if f"corpus:{b['item_id']}" == row["item"])
    assert rc == 0
    assert out.splitlines()[0] == f"# forge-gate-result: {row['result_id']}"
    assert out.split("\n", 1)[1].rstrip("\n") == body.rstrip("\n")
    assert ledger.result_ids_in(out) == [row["result_id"]]
    assert snap(lg) == before                                     # being handed out never scores
    assert not (tmp_path / CANARY_NAME).exists()                   # read, never run


def test_show_uses_slashes_for_c_family_code(tmp_path, lg, capsys, monkeypatch):
    rid = one_item_export(tmp_path, monkeypatch, "int main(){}", lang="cpp")
    assert cli.main(["scout", "--show", rid]) == 0
    assert capsys.readouterr().out == f"// forge-gate-result: {rid}\nint main(){{}}\n"


def test_show_drops_an_older_id_so_the_snippet_names_one_result(tmp_path, lg, capsys, monkeypatch):
    old = "fgr-" + "0" * 12 + "-shape-3"
    body = f"# forge-gate-result: {old}\nx = 1\n  # forge-gate-result: {old}\ny = 2  # keep me\n"
    rid = one_item_export(tmp_path, monkeypatch, body)
    assert cli.main(["scout", "--show", rid]) == 0
    out = capsys.readouterr().out
    assert ledger.result_ids_in(out) == [rid]
    assert out == f"# forge-gate-result: {rid}\nx = 1\ny = 2  # keep me\n"


def test_show_keeps_a_shebang_on_the_first_line(tmp_path, lg, capsys, monkeypatch):
    rid = one_item_export(tmp_path, monkeypatch, "#!/usr/bin/env python3\nprint(1)\n")
    assert cli.main(["scout", "--show", rid]) == 0
    assert capsys.readouterr().out == f"#!/usr/bin/env python3\n# forge-gate-result: {rid}\nprint(1)\n"


def test_show_refuses_an_item_whose_body_does_not_match_its_id(tmp_path, lg, capsys, monkeypatch):
    rid = one_item_export(tmp_path, monkeypatch, "x = 1\n", item_id="c_" + "0" * 16)
    assert cli.main(["scout", "--show", rid]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "does not match its body" in cap.err


@pytest.mark.parametrize("case,why", [
    ("not-an-id", "is not a result id"), ("unknown", "not in this host's exposure ledger"),
    ("ledger-off", "the ledgers are off"), ("gone-from-export", "not in the newest OK export"),
    ("other-pool", "is not handed out yet"), ("with-file", "not both")])
def test_show_refuses_with_rc_2_and_prints_nothing_on_stdout(corpus_run, lg, tmp_path, capsys, monkeypatch, case, why):
    _scout(tmp_path, QUERY, capsys=capsys)
    rid = _first_rid(lg)["result_id"]
    args = ["scout", "--show", rid]
    if case == "not-an-id":
        args = ["scout", "--show", rid + "x"]
    elif case == "unknown":
        args = ["scout", "--show", "fgr-" + "f" * 12 + "-shape-1"]
    elif case == "ledger-off":
        monkeypatch.delenv(config.LEDGER_KEY)
    elif case == "gone-from-export":
        _export(corpus_run, "20261103T000000Z", [{"item_id": cid("x = 1\n" * 20), "body": "x = 1\n" * 20,
                                                  "lang": "python", "kind": "write"}])
    elif case == "other-pool":
        ledger.record_exposure(query_sha256="a" * 64, query_lang="python",
                               hits=[{"lens": "shape", "rank": 1, "score": 1.0, "pool": "github", "trust": "raw",
                                      "item": "github:scout:github:x@y:z::f"}])
        rows = (lg / ledger.EXPOSURE_NAME).read_text().splitlines()
        args = ["scout", "--show", json.loads(rows[-1])["result_id"]]
    else:
        args = ["scout", str(tmp_path / "query.py"), "--show", rid]
    rc = cli.main(args)
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == "" and why in cap.err
