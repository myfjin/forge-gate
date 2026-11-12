"""Build step 6a: signature (§3a), contract and environment-fit gates, the executor port, and the
behaviour lens with its Rule-2 set (§4a.4). Box-running tests need Linux + bwrap."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from forge_gate.scout import behaviour, box, contract, envfit, report, signature

LINUX = box.available() is None
PORT_BODY_MD5 = "20593fe438f7e0f72cef105ae295c530"   # the port after its two recorded comment edits (source md5: tests/test_port_provenance.py)


def test_signature_reads_text_and_never_runs_it():
    src = "import os\nos._exit(7)\ndef f(a: list, b=2) -> float:\n    r = a\n    return r\n" \
          "def _private(x): return x\ndef noargs(): return 1\nclass K:\n    def m(self, z): return z\n"
    sigs = signature.extract(src)
    assert [s["entry"] for s in sigs] == ["f"]
    f = sigs[0]
    assert (f["required"], f["total"]) == (1, 2)
    assert f["consumes"][0] == {"name": "a", "type": "list", "noun": None}
    assert f["produces"] == {"type": "float", "names": ["r"], "noun": None}
    with pytest.raises(SyntaxError):
        signature.extract("def (:")


def test_contract_gate_filters_arity_and_annotated_kinds_only():
    q = signature.extract("def q(xs: list) -> float: return 0.0")[0]
    assert contract.fits(q, signature.extract("def c(v): return 0")[0])[0]              # unknown passes
    assert contract.fits(q, signature.extract("def c(v, w=1): return 0")[0])[0]
    assert not contract.fits(q, signature.extract("def c(v, w): return 0")[0])[0]        # arity
    assert not contract.fits(q, signature.extract("def c(v: str): return 0")[0])[0]      # kind
    assert not contract.fits(q, signature.extract("def c(v: list) -> str: return ''")[0])[0]
    assert contract.fits(q, signature.extract("def c(v: List[float]) -> float: return 0.0")[0])[0]


def test_envfit_reads_imports_and_judges_against_box_facts():
    imp = envfit.imports("import numpy as np\nfrom os import path\nimport json.decoder\n")
    assert imp == ({"numpy", "os", "json"}, False)
    assert envfit.imports("from . import x\n")[1] is True
    assert envfit.fits(imp, {"numpy": False, "os": True, "json": True}) == (False, "missing: numpy")
    assert envfit.fits(imp, {"numpy": True, "os": True, "json": True}) == (True, "fits")
    assert envfit.fits(({"os"}, True), {"os": True})[0] is False


def test_executor_port_is_the_source_body_byte_for_byte():
    text = behaviour.PORT.read_text()
    head, body = text.split("# --- ported body below ---\n", 1)
    assert "Ported from /home/me/ai-lab/c1-measurement/validator-COPY-from-dot5/executor.py" in head
    assert "03ce6d10c70afad81f427f6a67d32c4f24b3ec08387bd7596ef1e3e354ec4361" in head
    assert hashlib.md5(body.encode()).hexdigest() == PORT_BODY_MD5
    assert "from oracle import" not in body


def test_executor_port_is_used_only_inside_the_box():
    root = Path(behaviour.__file__).parents[1]
    users = [p for p in root.rglob("*.py") if "executor_port" in p.read_text() and p.name != "executor_port.py"]
    assert [p.name for p in users] == ["behaviour.py"], users   # copied into the box, never imported


def test_rule2_evaluator_fails_a_broken_lens():
    """Red arm for the evaluator itself, no box needed: a lens that says 'everything agrees'."""
    def everything(query, members, **kw):
        hits = [behaviour.BHit(1.0, m["item_id"], "rule2", "fixture", "rule2", "f", "g", 9, 9) for m in members]
        return behaviour.BehaviourResult(behaviour.found("all"), hits, {h.item_id: 1.0 for h in hits})
    fails = behaviour.rule2(everything)
    assert any("same-look/different-meaning" in f for f in fails)
    assert any(f.startswith("null") for f in fails)
    assert any(f.startswith("unrelated returned") for f in fails)


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_rule2_set_passes_in_the_box():
    assert behaviour.rule2() == []


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_a_lens_with_no_threshold_fails_rule2(monkeypatch):
    monkeypatch.setattr(behaviour, "TAU", 0.0)
    fails = behaviour.rule2()
    assert any("null" in f or "fork" in f or "unrelated" in f for f in fails), fails


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_hits_name_the_pair_and_env_fit_filters_missing_modules():
    members = behaviour._pool() + [
        {"item_id": "x:needs-numpy", "body": "import numpy_not_here_fg\ndef s(xs):\n    return sum(x*x for x in xs)\n",
         "lang": "python", "kind": "t"}]
    r = behaviour.rank(behaviour.Q_SQ, members, pool="corpus", trust="raw")
    assert r.outcome.kind == "FOUND", r.outcome
    h = {x.item_id: x for x in r.hits}["rule2:sq_gen"]
    assert (h.query_fn, h.cand_fn) == ("squares_total", "energy") and h.considered >= behaviour.MIN_CONSIDERED
    assert r.gates["env-fit"] == 1 and "x:needs-numpy" not in r.scores


def test_report_renders_behaviour_lines_with_three_outcomes():
    rep = {"file": "f.py", "language": "python", "sha256": "0", "checks": {"rule2-behaviour": behaviour.clean("ok")},
           "related": {"corpus": {"export": "r", "agree": 1, "n": 3, "blind": 1, "skipped": [],
                                  "lenses": {"shape": behaviour.clean("x"), "behaviour": behaviour.found("1 hit"),
                                             "intent": behaviour.blind("not-built")},
                                  "shape_hits": [], "behaviour_hits": [vars(behaviour.BHit(1.0, "c:1", "corpus", "raw", "k", "f", "g", 9, 9))]},
                       "library": {"trust": "signed", "outcome": behaviour.blind("p")},
                       "registry": {"trust": "tier", "outcome": behaviour.blind("p")},
                       "github": {"trust": "raw", "outcome": behaviour.blind("p")}},
           "change": behaviour.blind("none")}
    text = report.render(rep)
    assert "B=1.00  c:1" in text and "(f vs g)" in text
    assert report.check_report_outcomes(text) == []


@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_ref_tree_is_read_only_inside_the_box(tmp_path, monkeypatch):
    """6E 6a pre-note: what a candidate is compared against must not be writable by it. Run
    WITHOUT the box user: through it, file modes alone would block the write, and this test
    would no longer see the mount."""
    monkeypatch.delenv("FORGE_GATE_BOX_USER", raising=False)
    monkeypatch.delenv("FORGE_GATE_BOX_ROOT", raising=False)
    src, ref = tmp_path / "src", tmp_path / "ref"
    src.mkdir(); ref.mkdir(); (ref / "c.py").write_text("x = 1\n")
    (src / "w.py").write_text(
        "try:\n    open('/ref/c.py', 'w').write('tampered'); print('REF-WRITABLE')\n"
        "except OSError as e:\n    print('REF-RO', type(e).__name__)\n")
    r = box.run(src, ["python3", "/src/w.py"], ref=ref)
    assert r.outcome.kind == "FOUND" and "REF-RO" in r.stdout and "REF-WRITABLE" not in r.stdout, (r.outcome, r.stdout, r.stderr)
    assert (ref / "c.py").read_text() == "x = 1\n"



@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_identical_code_that_often_raises_still_agrees_fully():
    """Both sides raising says nothing about meaning, so it is left out of the score; counting it
    as disagreement would make identical code look unrelated."""
    raiser = ("def picky(xs: list) -> float:\n    if len(xs) % 2:\n        raise ValueError('odd')\n"
              "    return sum(xs)\n")
    r = behaviour.rank(raiser, [{"item_id": "t:copy", "body": raiser.replace("picky", "picky2"),
                                 "lang": "python", "kind": "t"}], pool="t", trust="t")
    assert r.scores.get("t:copy") == 1.0, (r.scores, r.gates, r.outcome)



def test_runs_at_import_gate_reads_text():
    lib = '"""doc"""\nimport os\nX = 3\nT: int = 4\ndef f(a):\n    return a\nclass K:\n    pass\n' \
          'if __name__ == "__main__":\n    f(1)\n'
    assert envfit.runs_at_import(lib) is None
    assert envfit.runs_at_import("import sys\nsys.exit(1)\ndef f(a): return a\n").startswith("line 2: Expr")
    assert envfit.runs_at_import("DATA = open('x').read()\ndef f(a): return a\n").startswith("line 1: Assign")
    assert envfit.runs_at_import("for i in range(3):\n    pass\n").startswith("line 1: For")



def test_more_than_ten_percent_pair_errors_flags_the_run(monkeypatch):
    """P1: a run where over 10% of pairs could not look is flagged, and the error kinds are named."""
    import json as _json
    def fake(job, query, members, timeout):
        if job["mode"] == "facts":
            out = "MOD " + _json.dumps({n: True for n in job["modules"]})
        else:
            out = "\n".join("PAIR " + _json.dumps({"key": p["key"], "error": "load: SystemExit"} if p["key"] % 2 == 0
                                                   else {"key": p["key"], "inputs": 9, "considered": 9, "agree": 9})
                             for p in job["pairs"])
        return box.BoxResult(behaviour.found("ran"), 0, out, "")
    monkeypatch.setattr(behaviour, "_box_job", fake)
    r = behaviour.rank(behaviour.Q_SQ, behaviour._pool(), pool="t", trust="t")
    text = " ".join(map(str, r.outcome.evidence)) + r.outcome.reason
    assert "FLAGGED" in text and r.gates["error-kinds"].startswith("load: SystemExit"), (r.outcome, r.gates)



@pytest.mark.skipif(not LINUX, reason="needs Linux + bwrap")
def test_pairs_that_never_answer_are_counted_and_blind_not_clean():
    """6E B6-1 (their arm c): both sides raise on every input."""
    q = "def boom(xs: list) -> float:\n    raise ValueError('no')\n"
    r = behaviour.rank(q, [{"item_id": "t:b", "body": q.replace("boom", "boom2"), "lang": "python", "kind": "t"}],
                       pool="t", trust="t")
    assert r.gates["no-answer"] == 1 and r.outcome.kind == "BLIND", (r.outcome, r.gates)


def test_keyword_baseline_case_can_fail():
    """6E B6-2: the keyword baseline must rank a trap above a true match, or the case is void."""
    from forge_gate.scout import shape_lens as s
    kw = {k: s.keyword_score(behaviour.Q_SQ, v) for k, v in behaviour.POOL.items()}
    assert kw["kw_trap"] > min(kw[k] for k in behaviour.RELATED), kw


def test_async_only_members_are_counted_not_hidden():
    assert signature.async_only("async def f(x):\n    return x\n")
    assert not signature.async_only("def f(x):\n    return x\n")
    assert not signature.async_only("x = 1\n")
