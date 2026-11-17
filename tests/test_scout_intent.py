"""Build step 8b slice 3: the intent lens (DECISIONS-8b H6, H7). Fake embedders; the real model is
`-m live` on the deploy host. Intent reads purpose TEXT only, never code."""
from __future__ import annotations

import json

import pytest

from forge_gate import cli
from forge_gate.scout import embed, intent_lens as I, report, shape_lens

from test_scout_slice1 import _export, export_root  # noqa: F401  (fixture)

AXES = ["backup", "errcount", "diskalert", "area", "kmeans", "json", "tax", "null", "trap", "other"]


def onehot(axis, wobble=0.0):
    v = [0.0] * (len(AXES) + 1)
    v[AXES.index(axis)] = 1.0
    v[-1] = wobble
    return v


def table(overrides=None):
    t = {}
    for g, names in I.GROUPS.items():
        for i, n in enumerate(names):
            t[I.REQUESTS[n]] = onehot(g, 0.05 * i)
    for n, ax in (("area", "area"), ("kmeans", "kmeans"), ("json_stats", "json")):
        t[I.REQUESTS[n]] = onehot(ax)
    for what, ax in zip(I.MUST_NOT, ("tax", "null", "trap")):
        t[I.MUST_NOT[what]] = onehot(ax)
    for text, ax in (overrides or {}).items():
        t[text] = onehot(ax, 0.01)
    return t


class Fake:
    digest = "d" * 64

    def __init__(self, t=None):
        self.t, self.calls = t if t is not None else table(), []

    def check(self):
        pass

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self.t.get(x, onehot("other", 0.3)) for x in texts]


def test_a_lens_that_knows_the_purposes_passes_every_case():
    assert I.rule2_run(Fake()) == []


def test_red_arm_the_keyword_baseline_fails_the_intent_set():
    fails = I.rule2_cases(shape_lens.keyword_score, I.TAU, shape_lens.keyword_score)
    assert any(f.startswith("same-purpose") for f in fails) and any(f.startswith("baseline:") for f in fails)


TAX, NULL, TRAP = (I.MUST_NOT[k] for k in I.MUST_NOT)   # dict order: tax, null, trap


@pytest.mark.parametrize("override, case", [
    ({TRAP: "backup"}, "keyword trap"),
    ({TAX: "area"}, "same look, other purpose"),
    ({NULL: "kmeans"}, "null"),
    ({I.REQUESTS["diskalert_sh"]: "other"}, "same-purpose: diskalert"),
    ({I.REQUESTS["backup_rsync"]: "json"}, "same-purpose: backup"),
])
def test_each_case_turns_red_on_its_own(override, case):
    fails = I.rule2_run(Fake(table(override)))
    assert fails and any(f.startswith(case) for f in fails), fails


def test_the_only_intent_pair_is_invisible_to_shape_and_weak_for_keywords():
    assert shape_lens.score(I.DISKALERT_PY, I.DISKALERT_SH) < shape_lens.TAU
    assert shape_lens.keyword_score(I.DISKALERT_PY, I.DISKALERT_SH) < 0.2
    assert set(I.CODE) == set(I.REQUESTS)


def test_the_trap_lures_the_keyword_baseline():
    assert max((shape_lens.keyword_score(TRAP, t), n) for n, t in I.REQUESTS.items())[1].startswith("backup")


def test_rank_compares_purpose_text_never_code():
    ms = [{"item_id": "a", "intent": I.REQUESTS["backup_py"], "body": "x"},
          {"item_id": "b", "intent": I.REQUESTS["area"], "body": "y"},
          {"item_id": "c", "body": I.REQUESTS["backup_tar"]}]                  # no intent field
    e = Fake()
    r = I.rank(I.REQUESTS["backup_tar"], ms, emb=e, pool="corpus", trust="raw")
    assert r.outcome.kind == "FOUND" and [h.item_id for h in r.hits] == ["a", "b"] and r.looked_at == 2
    assert "1 member(s) without purpose text not compared" in str(r.outcome)
    assert "x" not in e.calls[-1] and "y" not in e.calls[-1]


def test_a_pool_without_purpose_text_is_blind_no_input_never_unrelated():
    r = I.rank(I.REQUESTS["backup_tar"], [{"item_id": "a", "body": "code"}], emb=Fake(), pool="corpus", trust="raw")
    assert r.outcome.kind == "BLIND" and r.outcome.reason == "no-input: no corpus member carries purpose text"


def test_an_intent_too_short_is_blind():
    assert I.rank("backup", [], emb=Fake(), pool="corpus", trust="raw").outcome.reason.startswith("no-input")


def test_nothing_related_is_looked_clean():
    ms = [{"item_id": "a", "intent": I.REQUESTS["area"]}]
    assert I.rank(NULL, ms, emb=Fake(), pool="corpus", trust="raw").outcome.kind == "LOOKED-CLEAN"


# ---------------------------------------------------------------- in the report

def _scout(tmp_path, capsys, *extra):
    q = tmp_path / "q.py"
    q.write_text("def f(a, b):\n    return a + b\n")
    assert cli.main(["scout", str(q), "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_without_intent_the_lens_says_so_and_runs_nothing(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", [{"item_id": "c_1", "body": "x = 1\n", "lang": "python"}])
    calls = []
    monkeypatch.setattr(embed, "get", lambda: calls.append(1) or Fake())
    monkeypatch.setattr(I, "rule2_run", lambda e: pytest.fail("intent ran its Rule-2 set without an intent"))
    rep = _scout(tmp_path, capsys)
    i = rep["related"]["corpus"]["lenses"]["intent"]
    assert rep["related"]["corpus"]["intent_hits"] == [] and "not run" in rep["checks"]["rule2-intent"]["reason"]
    assert i["outcome"] == "BLIND" and "no stated intent" in i["reason"]
    assert rep["checks"]["rule2-intent"]["outcome"] == "BLIND"


def test_on_todays_corpus_intent_is_blind_no_input(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", [{"item_id": "c_1", "body": "x = 1\n", "lang": "python"}])
    monkeypatch.setattr(embed, "get", lambda: Fake())
    rep = _scout(tmp_path, capsys, "--intent", "back up my home folder every night")
    c = rep["related"]["corpus"]
    assert rep["checks"]["rule2-intent"]["outcome"] == "LOOKED-CLEAN"
    assert c["lenses"]["intent"]["reason"] == "no-input: no corpus member carries purpose text"
    assert c["groups"]["intent"]["outcome"] == "BLIND" and c["intent_hits"] == []
    assert rep["intent"] == "back up my home folder every night"


def test_a_red_intent_set_switches_the_lens_off(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", [{"item_id": "c_1", "body": "x = 1\n", "lang": "python"}])
    monkeypatch.setattr(embed, "get", lambda: Fake(table({TRAP: "backup"})))
    rep = _scout(tmp_path, capsys, "--intent", "back up my home folder every night")
    assert rep["checks"]["rule2-intent"]["outcome"] == "FOUND"
    assert "rule2-failed" in rep["related"]["corpus"]["lenses"]["intent"]["reason"]
    assert rep["related"]["corpus"]["intent_hits"] == []


def test_intent_hits_render_with_an_outcome_line():
    rep = {"file": "f.py", "language": "python", "sha256": "0", "checks": {},
           "related": {"corpus": {"export": "r", "agree": 1, "n": 3, "blind": 0, "skipped": [],
                                  "lenses": {"intent": report.found("1")}, "shape_hits": [],
                                  "intent_hits": [vars(shape_lens.Hit("c_1", 0.9, "corpus", "raw"))]},
                       "library": {"trust": "signed", "outcome": report.blind("p")},
                       "registry": {"trust": "tier", "outcome": report.blind("p")},
                       "github": {"trust": "raw", "outcome": report.blind("p")}},
           "change": report.blind("none")}
    text = report.render(rep)
    assert "I=0.90  c_1" in text and report.check_report_outcomes(text) == []


@pytest.mark.live
def test_live_intent_rule2_on_the_real_model():
    try:
        fails = I.rule2_run(embed.get())
    except embed.EmbedError as err:
        pytest.skip(str(err))
    print("intent fails:", json.dumps(fails, indent=1))
    assert I.rule2_cases(shape_lens.keyword_score, I.TAU, shape_lens.keyword_score)


def test_with_intent_and_no_corpus_the_intent_check_still_says_why(export_root, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(embed, "get", lambda: Fake())
    rep = _scout(tmp_path, capsys, "--intent", "back up my home folder every night")
    assert rep["checks"]["rule2-intent"]["outcome"] == "BLIND"
    assert rep["checks"]["rule2-intent"]["reason"].startswith("not run: corpus")


def test_a_partner_ranked_first_but_below_tau_is_red():
    t = table()
    t[I.REQUESTS["backup_py"]] = onehot("backup", 2.0)
    fails = I.rule2_run(Fake(t))
    assert fails and all(f.startswith("same-purpose") for f in fails), fails


def test_a_baseline_the_trap_does_not_lure_is_reported():
    fails = I.rule2_cases(shape_lens.keyword_score, I.TAU, lambda a, b: float(b == I.REQUESTS["area"]))
    assert any("the keyword trap did not lure the baseline" in f for f in fails), fails


def test_intent_hits_get_exposure_rows(tmp_path, monkeypatch):
    from forge_gate import config, ledger
    from test_ledger import make_ledger
    monkeypatch.setenv(config.LEDGER_KEY, str(make_ledger(tmp_path)))
    rep = {"sha256": "0" * 64, "language": "python",
           "related": {"corpus": {"intent_hits": [vars(shape_lens.Hit("c_1", 0.9, "corpus", "raw"))]}}}
    assert report._expose(rep).kind == "FOUND"
    assert [r["lens"] for r in ledger.view().exposure.values()] == ["intent"]
