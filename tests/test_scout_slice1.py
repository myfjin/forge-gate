"""Build step 5, scout slice 1 (design v1.4.5 §5, §12.4, §4a.2, §4a.4; exit rule: executes no
pool code). Fixtures only: every export here is built in tmp_path by ``_export``.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import random
import subprocess
from pathlib import Path

import pytest

from forge_gate import cli, config
from forge_gate.scout import corpus, report, rule2, shape, shape_lens

REPO = Path(__file__).resolve().parents[1]

POOL_BODY = rule2.KMEANS + "\n# pool member body\n"
CANARY_NAME = "EXECUTED-CANARY"


def _bodies(canary: Path):
    # A body that, if ever executed, writes the canary file. Scout must only read it.
    evil = f"import pathlib\npathlib.Path({str(canary)!r}).write_text('ran')\nfor i in range(3):\n    print(i, i * i)\n"
    return [
        {"item_id": "c_0000000000000001", "body": POOL_BODY, "lang": "python", "kind": "write"},
        {"item_id": "c_0000000000000002", "body": rule2.JSON_STATS * 3, "lang": "python", "kind": "write"},
        {"item_id": "c_0000000000000003", "body": evil * 4, "lang": "python", "kind": "heredoc"},
        {"item_id": "c_0000000000000004", "body": rule2.BACKUP_TAR, "lang": "shell", "kind": "heredoc"},
    ]


def _export(root: Path, run_id: str, items, *, status="OK", schema=corpus.SCHEMA, corrupt=None, drop_rows=False):
    d = root / run_id
    d.mkdir(parents=True)
    files = {"items.jsonl": "".join(json.dumps(i) + "\n" for i in items),
             "clusters.jsonl": "", "scout_ready_mismatch.jsonl": ""}
    meta = {}
    for name, text in files.items():
        b = text.encode()
        (d / name).write_bytes(b)
        meta[name] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b), "rows": b.count(b"\n")}
        if drop_rows:
            del meta[name]["rows"]
    (d / "manifest.json").write_text(json.dumps(
        {"schema": schema, "run_id": run_id, "run_status": status, "files": meta}))
    if corrupt:
        p = d / corrupt
        b = bytearray(p.read_bytes())
        b[len(b) // 2] ^= 1
        p.write_bytes(bytes(b))
    return d


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    root = tmp_path / "export" / "v1"
    root.mkdir(parents=True)
    monkeypatch.setenv(config.CORPUS_KEY, str(root))
    return root


# --- port-and-freeze ---------------------------------------------------------------------
def test_shape_port_body_matches_its_origin_md5():
    lines = (REPO / "src/forge_gate/scout/shape.py").read_bytes().split(b"\n")
    marker = lines.index(b"# --- ported body below ---")
    body = b"\n".join(lines[marker + 1:])
    assert hashlib.md5(body).hexdigest() == "0350722f31814c31085d55cce603cfd6"


# --- intake (§12.4, P1) ------------------------------------------------------------------
def test_intake_reads_the_newest_ok_run_and_skips_newer_non_ok(export_root, tmp_path):
    items = _bodies(tmp_path / CANARY_NAME)
    _export(export_root, "20261101T000000Z", items[:1])
    _export(export_root, "20261102T000000Z", items)
    _export(export_root, "20261103T000000Z", items, status="FLAGGED")
    _export(export_root, "20261104T000000Z", items, schema="code-corpus-export/v2")
    it = corpus.read_export()
    assert it.outcome.kind == "FOUND" and it.run_id == "20261102T000000Z" and len(it.items) == 4
    assert [r for r, _ in it.skipped] == ["20261104T000000Z", "20261103T000000Z"]


@pytest.mark.parametrize("name", ["items.jsonl", "clusters.jsonl"])
def test_a_bad_sha_in_the_newest_ok_run_is_blind_never_a_fallback(export_root, tmp_path, name):
    items = _bodies(tmp_path / CANARY_NAME)
    _export(export_root, "20261101T000000Z", items)                 # older, intact
    if name == "clusters.jsonl":
        items = items + [{"item_id": "c_x", "body": "x = 1\n"}]
    d = _export(export_root, "20261102T000000Z", items)
    if name == "clusters.jsonl":  # an empty file cannot flip a byte: plant a mismatch in the manifest
        m = json.loads((d / "manifest.json").read_text())
        m["files"][name]["sha256"] = "0" * 64
        (d / "manifest.json").write_text(json.dumps(m))
    else:
        b = bytearray((d / name).read_bytes()); b[10] ^= 1; (d / name).write_bytes(bytes(b))
    it = corpus.read_export()
    assert it.outcome.kind == "BLIND" and it.outcome.reason == f"sha-recheck: sha256 mismatch {name}"
    assert it.items == []                         # nothing read from the damaged run
    assert it.run_id == "20261102T000000Z"        # and no fall back to the older run


def test_unset_key_is_blind_naming_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv(config.CORPUS_KEY, raising=False)
    monkeypatch.setenv("FORGE_GATE_CONFIG", str(tmp_path / "absent.toml"))
    it = corpus.read_export()
    assert it.outcome.kind == "BLIND" and config.CORPUS_KEY in it.outcome.reason


def test_no_ok_run_is_blind_listing_why(export_root, tmp_path):
    _export(export_root, "20261103T000000Z", _bodies(tmp_path / CANARY_NAME), status="FLAGGED")
    it = corpus.read_export()
    assert it.outcome.label() == "BLIND(no-OK-export)" and "FLAGGED" in it.outcome.evidence[0]


def test_unreadable_items_over_ten_percent_flag_the_run(export_root, tmp_path):
    good = _bodies(tmp_path / CANARY_NAME)[:2]
    bad = [{"item_id": f"c_bad{i}", "body": None} for i in range(2)]
    _export(export_root, "20261102T000000Z", good + bad)
    it = corpus.read_export()
    assert it.unreadable == 2 and it.flagged


# --- Rule 2 (§4a.4) ----------------------------------------------------------------------
def test_shape_lens_passes_its_known_case_set():
    assert rule2.evaluate(shape_lens.score, shape_lens.TAU, shape_lens.keyword_score) == []


def _shuffled(a, b):
    r = random.Random(hashlib.md5((a + b).encode()).hexdigest())
    return r.random()


BROKEN = {
    "always-0": lambda a, b: 0.0,
    "always-1": lambda a, b: 1.0,
    "random": _shuffled,
    "keyword-baseline-as-lens": shape_lens.keyword_score,
    "raw-token-jaccard (no normalization)": lambda a, b: shape.jaccard(set(a.split()), set(b.split())),
    "length-ratio": lambda a, b: min(len(a), len(b)) / max(len(a), len(b)),
}


@pytest.mark.parametrize("name", list(BROKEN))
def test_rule2_red_arm_a_broken_lens_turns_the_set_red(name):
    assert rule2.evaluate(BROKEN[name], shape_lens.TAU, shape_lens.keyword_score), name


# --- the scout run ---------------------------------------------------------------------------
def _scout(tmp_path, body, suffix=".py", json_out=True, capsys=None):
    q = tmp_path / f"query{suffix}"
    q.write_text(body)
    rc = cli.main(["scout", str(q)] + (["--json"] if json_out else []))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if json_out else out)


def test_scout_finds_a_near_copy_and_the_control_comes_back(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    rc, rep = _scout(tmp_path, rule2.NEAREST, capsys=capsys)
    assert rc == 0
    c = rep["related"]["corpus"]
    assert c["lenses"]["shape"]["outcome"] == "FOUND"
    assert c["shape_hits"][0]["item_id"] == "c_0000000000000001"
    assert all(h["pool"] == "corpus" and h["trust"] == "raw" for h in c["shape_hits"])
    assert {h["item_id"] for h in c["shape_hits"]} <= {"c_0000000000000001", "c_0000000000000002", "c_0000000000000003"}  # python only
    assert rep["checks"]["control"]["outcome"] == "FOUND"
    n_blind = sum(1 for o in c["lenses"].values() if o["outcome"] == "BLIND")
    assert (c["n"], c["agree"], c["blind"]) == (3, 1, n_blind)   # n fixed before the query (rule 1)
    assert c["lenses"]["intent"]["outcome"] == "BLIND"            # not built; behaviour runs only on Linux


def test_scout_with_no_related_member_is_looked_clean_not_empty(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    rc, rep = _scout(tmp_path, rule2.NULL, capsys=capsys)
    assert rep["related"]["corpus"]["lenses"]["shape"]["outcome"] == "LOOKED-CLEAN"


@pytest.mark.parametrize("body,suffix,reason", [
    ("echo hi\n" * 10, ".sh", "language"),
    ("x\n", ".py", "no-input"),
])
def test_scout_cannot_look_is_blind_with_a_reason(export_root, tmp_path, capsys, body, suffix, reason):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    rc, rep = _scout(tmp_path, body, suffix, capsys=capsys)
    o = rep["related"]["corpus"]["lenses"]["shape"]
    assert rc == 0 and o["outcome"] == "BLIND" and o["reason"].startswith(reason)


def test_a_failing_rule2_switches_the_lens_off(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    monkeypatch.setattr(shape_lens, "score", lambda a, b: 1.0)
    rc, rep = _scout(tmp_path, rule2.NEAREST, capsys=capsys)
    assert rep["checks"]["rule2-shape"]["outcome"] == "FOUND"
    assert rep["related"]["corpus"]["lenses"]["shape"]["reason"].startswith("rule2-failed")


def test_a_failing_control_makes_the_lens_blind(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    real = shape_lens.rank
    monkeypatch.setattr(shape_lens, "rank", lambda q, m, **kw: shape_lens.LensResult(shape_lens.clean("x"))
                        if q == m[0]["body"] or len(q) > 300 else real(q, m, **kw))
    rc, rep = _scout(tmp_path, rule2.NEAREST, capsys=capsys)
    assert rep["checks"]["control"]["outcome"] == "BLIND"
    assert rep["related"]["corpus"]["lenses"]["shape"]["reason"] == "control-failed"


# --- exit rule: no pool or query code runs OUTSIDE the box -----------------------------------
# Slice 1 (step 5) ran no code at all. From step 6a the behaviour lens runs code, but only inside
# the isolation box (§4a.6). So: nothing is executed in scout's own process (parsing to an AST is
# allowed: it runs nothing), and the only subprocesses are the box (bwrap, possibly via the box
# user's sudo + prlimit) and the `sudo -n -u <box user> -- true` reachability check.
def _box_only_popen(real):
    def popen(args, *a, **k):
        argv = list(args) if not isinstance(args, (str, bytes)) else [args]
        ok = argv[:1] == ["bwrap"] or (argv[:1] == ["sudo"] and (
            "bwrap" in argv or argv[-1:] == ["true"] or argv[4:7] == ["chmod", "-R", "g+rwX"]))
        if not ok:
            raise AssertionError(f"scout started a process outside the box: {argv[:4]}")
        return real(args, *a, **k)
    return popen


def test_scout_executes_no_pool_or_query_code(export_root, tmp_path, capsys, monkeypatch):
    import ast as _ast
    canary = tmp_path / CANARY_NAME
    _export(export_root, "20261102T000000Z", _bodies(canary))

    def refuse(*a, **k):
        raise AssertionError("scout tried to execute code")
    for name in ("exec", "eval", "compile", "__import__"):
        real = getattr(builtins, name)
        if name == "__import__":   # imports of modules scout itself needs are fine; pool names are not
            monkeypatch.setattr(builtins, name, lambda n, *a, _r=real, **k: _r(n, *a, **k)
                                if n not in ("query", "items") else refuse())
        elif name == "compile":    # ast.parse: text to a tree, nothing runs
            monkeypatch.setattr(builtins, name, lambda src, fn, mode, flags=0, *a, _r=real, **k:
                                _r(src, fn, mode, flags, *a, **k) if flags & _ast.PyCF_ONLY_AST else refuse())
        else:
            monkeypatch.setattr(builtins, name, refuse)
    monkeypatch.setattr(subprocess, "Popen", _box_only_popen(subprocess.Popen))
    q_canary = tmp_path / "QUERY-CANARY"
    body = f"import pathlib\npathlib.Path({str(q_canary)!r}).write_text('ran')\n" * 5
    rc, out = _scout(tmp_path, body, json_out=False, capsys=capsys)
    assert rc == 0 and "FORGE-GATE SCOUT REPORT" in out
    assert not canary.exists() and not q_canary.exists()


# --- §10.2 Rule 6 checker --------------------------------------------------------------------
def test_every_line_of_a_real_report_carries_an_outcome(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    rc, out = _scout(tmp_path, rule2.NEAREST, json_out=False, capsys=capsys)
    assert report.check_report_outcomes(out) == []


def test_rule6_checker_fails_a_report_with_an_empty_result():
    text = "CHECKS\n  control   FOUND  x\nRELATED\n  intent    no related code\nCHANGE   BLIND(x)\n"
    assert report.check_report_outcomes(text) == ["  intent    no related code"]
    text2 = "CHECKS\n  control   \nRELATED\nCHANGE   BLIND(x)\n"
    assert report.check_report_outcomes(text2) == ["  control   "]


# --- 6E review S1-1..S1-3 ----------------------------------------------------------------------
def test_a_manifest_without_rows_is_blind_not_a_weaker_check(export_root, tmp_path):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME), drop_rows=True)
    it = corpus.read_export()
    assert it.outcome.kind == "BLIND" and it.outcome.reason.startswith("sha-recheck: schema: no rows for")


def test_a_skipped_newer_run_is_shown_and_labels_do_not_nest(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _bodies(tmp_path / CANARY_NAME))
    bad = export_root / "20261103T000000Z"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")
    rc, out = _scout(tmp_path, rule2.NEAREST, json_out=False, capsys=capsys)
    assert "skipped    BLIND(newer export 20261103T000000Z not used: manifest unreadable" in out
    assert report.check_report_outcomes(out) == []
    d = _export(export_root, "20261104T000000Z", _bodies(tmp_path / CANARY_NAME), corrupt="items.jsonl")
    rc, rep = _scout(tmp_path, rule2.NEAREST, capsys=capsys)
    r = rep["related"]["corpus"]["lenses"]["shape"]["reason"]
    assert r.startswith("corpus: sha-recheck") and "BLIND(" not in r
