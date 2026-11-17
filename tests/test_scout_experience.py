"""Build step 8b slices 4-5: the one-in-ten explore slot and the experience lens (design §4a.5;
DECISIONS-8b H8-H11). Experience is evidence beside a hit, never a vote; its Rule-2 set must go red
for every variant that rewards selection."""
from __future__ import annotations

import json
import sys

import pytest

from forge_gate import cli, config, ledger
from forge_gate.scout import experience as X, report, rule2, shape_lens, slots

from test_ledger import make_ledger
from test_scout_slice1 import _export, export_root  # noqa: F401  (fixture)


# ---------------------------------------------------------------- Rule 2 (written first)

def test_the_real_ordering_passes_and_every_selection_variant_is_red():
    assert X.rule2() == []
    assert X.rule2_cases(X._real) == []


@pytest.mark.parametrize("variant, case", [("rewards-exposure", "E1 selection only"),
                                           ("rewards-pending", "E2 pending"),
                                           ("blended", "E3 lexicographic")])
def test_each_variant_is_caught_by_the_case_meant_for_it(variant, case):
    fails = X.rule2_cases(X.VARIANTS[variant])
    assert any(f.startswith(case) for f in fails), fails


def test_red_arm_a_variant_the_set_cannot_see_turns_the_lens_off(monkeypatch):
    monkeypatch.setitem(X.VARIANTS, "harmless-copy", X._real)
    assert X.rule2() == ["red arm: variant 'harmless-copy' passed the set; the set cannot see it reward selection"]


def test_red_arm_an_ordering_that_ignores_score_or_trust_is_red():
    def key_first(hits, key, co, ex):
        return sorted(hits, key=lambda h: (key(co(f"corpus:{h['item_id']}"), 0), h["item_id"]))
    real = X.order
    try:
        X.order = key_first
        fails = X.rule2_cases(X._real)
    finally:
        X.order = real
    assert any("across a score" in f for f in fails) and any("across a trust level" in f for f in fails)


def test_the_synthetic_view_counts_like_the_ledger_says():
    v = X.synthetic_view()
    c = lambda i: ledger.counts(f"corpus:{i}", v=v)                          # noqa: E731
    assert (c("c_a_popular").oracle, c("c_a_popular").person_without_id) == (0, 0)
    assert len(ledger.exposures("corpus:c_a_popular", v=v)) == 50
    assert c("c_zc_pending").pending == 3 and c("c_zc_pending").person_without_id == 0
    assert c("c_p_people").person_without_id == 100 and c("c_q_oracle").oracle == 1
    assert (c("c_y_withid").person_with_id, c("c_x_noid").person_without_id) == (1, 2)


# ---------------------------------------------------------------- apply

def _block(*hits):
    return {"shape_hits": [dict(h) for h in hits]}


def test_apply_annotates_and_reorders_only_inside_ties_and_never_the_explore_slot():
    v = X.synthetic_view()
    c = _block({"item_id": "c_a_popular", "score": 0.9, "trust": "raw", "pool": "corpus"},
               {"item_id": "c_b_helped", "score": 0.9, "trust": "raw", "pool": "corpus"},
               {"item_id": "c_low", "score": 0.5, "trust": "raw", "pool": "corpus"},
               {"item_id": "c_q_oracle", "score": 0.1, "trust": "raw", "pool": "corpus", "slot": "explore"})
    n, moved = X.apply(c, v)
    ids = [h["item_id"] for h in c["shape_hits"]]
    assert ids == ["c_b_helped", "c_a_popular", "c_low", "c_q_oracle"] and (n, moved) == (2, 1)
    assert c["shape_hits"][0]["experience"] == {"oracle": 0, "with_id": 1, "without_id": 0, "pending": 0}
    assert c["shape_hits"][1]["experience"] == {"oracle": 0, "with_id": 0, "without_id": 0, "pending": 0}


def test_apply_on_an_empty_history_changes_no_order():
    c = _block(*({"item_id": f"c_{i}", "score": 0.9, "trust": "raw", "pool": "corpus"} for i in "abcd"))   # lens order
    before = [h["item_id"] for h in c["shape_hits"]]
    assert X.apply(c, ledger.View()) == (0, 0) and [h["item_id"] for h in c["shape_hits"]] == before


# ---------------------------------------------------------------- the explore slot

def _members(n):
    body = rule2.KMEANS + "\n# pool member body\n"
    return [{"item_id": "c_%016x" % i, "body": body + f"# variant {i}\n" * (i % 3), "lang": "python"}
            for i in range(1, n + 1)]


def test_a_list_longer_than_nine_gets_one_labelled_explore_hit_from_outside_the_top():
    r = shape_lens.rank(rule2.NEAREST, _members(14), pool="corpus", trust="raw")
    assert len(r.hits) == 10 and [h.slot for h in r.hits] == ["top"] * 9 + ["explore"]
    ranked = sorted(r.hits[:9], key=lambda h: (-h.score, h.item_id))
    assert r.hits[:9] == ranked
    assert r.hits[9].item_id not in {h.item_id for h in r.hits[:9]}
    again = shape_lens.rank(rule2.NEAREST, _members(14), pool="corpus", trust="raw")
    assert again.hits[9].item_id == r.hits[9].item_id                       # reproducible draw


def test_nine_or_fewer_members_have_no_explore_slot():
    r = shape_lens.rank(rule2.NEAREST, _members(9), pool="corpus", trust="raw")
    assert len(r.hits) == 9 and all(h.slot == "top" for h in r.hits)


# ---------------------------------------------------------------- through scout, with real ledgers

@pytest.fixture
def lg(tmp_path, monkeypatch):
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    return d


def _scout(tmp_path, capsys, body=rule2.NEAREST):
    q = tmp_path / "q.py"
    q.write_text(body)
    assert cli.main(["scout", str(q), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_ledger_off_experience_is_blind_and_its_rule2_still_runs(export_root, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(config.LEDGER_KEY, raising=False)
    _export(export_root, "20261102T000000Z", _members(3))
    rep = _scout(tmp_path, capsys)
    assert rep["checks"]["rule2-experience"]["outcome"] == "LOOKED-CLEAN"
    assert rep["checks"]["experience"]["outcome"] == "BLIND" and "ledger-off" in rep["checks"]["experience"]["reason"]


def test_explore_rows_reach_the_exposure_ledger_as_slot_explore_rank_10(export_root, lg, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _members(14))
    c = _scout(tmp_path, capsys)["related"]["corpus"]
    rows = sorted((r for r in ledger.view().exposure.values() if r["lens"] == "shape"), key=lambda r: r["rank"])
    assert [r["slot"] for r in rows] == ["top"] * 9 + ["explore"] and rows[-1]["rank"] == 10
    assert c["shape_hits"][9]["slot"] == "explore"


def test_a_scored_outcome_moves_its_hit_up_inside_a_tie_and_shows_beside_it(export_root, lg, tmp_path, capsys):
    same = rule2.KMEANS + "\n# pool member body\n"
    _export(export_root, "20261102T000000Z",
            [{"item_id": "c_0000000000000001", "body": same, "lang": "python"},
             {"item_id": "c_0000000000000002", "body": same, "lang": "python"}])
    c = _scout(tmp_path, capsys, same)["related"]["corpus"]
    assert [h["item_id"] for h in c["shape_hits"]] == ["c_0000000000000001", "c_0000000000000002"]
    rid = c["shape_hits"][1]["result_id"]
    mark = ledger.record_mark(who="ann", result_id=rid)
    proj = tmp_path / "proj"
    proj.mkdir()
    ledger.record_event(mark_id=mark["mark_id"], project=proj, cmd=[sys.executable, "-c", "pass"], event_kind="tests")
    rep = _scout(tmp_path, capsys, same)
    c = rep["related"]["corpus"]
    assert [h["item_id"] for h in c["shape_hits"]] == ["c_0000000000000002", "c_0000000000000001"]
    assert c["shape_hits"][0]["experience"]["with_id"] == 1
    assert rep["checks"]["experience"]["outcome"] == "FOUND"
    assert c["lenses"]["shape"]["outcome"] == "FOUND" and c["agree"] >= 1          # outcome unchanged
    capsys.readouterr()
    q = tmp_path / "q.py"
    cli.main(["scout", str(q)])
    text = capsys.readouterr().out
    assert "experience: oracle 0 · with-id 1 · without-id 0 · pending 0" in text
    assert report.check_report_outcomes(text) == []


def test_a_red_experience_set_shows_no_counts(export_root, lg, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(X.VARIANTS, "harmless-copy", X._real)
    _export(export_root, "20261102T000000Z", _members(3))
    rep = _scout(tmp_path, capsys)
    assert rep["checks"]["rule2-experience"]["outcome"] == "FOUND"
    assert "rule2-failed" in rep["checks"]["experience"]["reason"]
    assert all("experience" not in h for h in rep["related"]["corpus"]["shape_hits"])


def test_an_unreadable_ledger_is_blind_never_zero(export_root, lg, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _members(3))
    (lg / ledger.OUTCOME_NAME).chmod(0o000)
    try:
        rep = _scout(tmp_path, capsys)
    finally:
        (lg / ledger.OUTCOME_NAME).chmod(0o600)
    assert "ledger-unreadable" in rep["checks"]["experience"]["reason"]
    assert all("experience" not in h for h in rep["related"]["corpus"]["shape_hits"])


def test_a_behaviour_explore_pair_never_makes_behaviour_found(monkeypatch):
    """8b s4: behaviour's explore slot draws from ANY scored pair (also below TAU); the outcome is judged
    on the agreeing top only."""
    from forge_gate.scout import behaviour, box

    def fake(job, query, members, timeout):
        if job["mode"] == "facts":
            out = "MOD " + json.dumps({n: True for n in job["modules"]})
        else:
            out = "\n".join("PAIR " + json.dumps({"key": p["key"], "inputs": 9, "considered": 9, "agree": 3})
                            for p in job["pairs"])
        return box.BoxResult(behaviour.found("ran"), 0, out, "")
    monkeypatch.setattr(behaviour, "_box_job", fake)
    r = behaviour.rank(behaviour.Q_SQ, behaviour._pool(), pool="t", trust="t")
    assert r.outcome.kind == "LOOKED-CLEAN", r.outcome
    assert len(r.hits) == 1 and r.hits[0].slot == "explore" and r.hits[0].score < behaviour.TAU


def test_refused_outcome_rows_with_none_accepted_are_blind_never_zero(export_root, lg, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _members(3))
    (lg / ledger.OUTCOME_NAME).write_bytes(b"not json\n")
    rep = _scout(tmp_path, capsys)
    assert rep["checks"]["experience"]["outcome"] == "BLIND"
    assert "ledger-rows-refused" in rep["checks"]["experience"]["reason"]
    assert all("experience" not in h for h in rep["related"]["corpus"]["shape_hits"])


def test_behaviour_rule2_judges_the_top_hits_never_the_explore_pair(monkeypatch):
    """8b s4 review F1 (HIGH): with an explore pair drawn on every rank, behaviour's own Rule-2 set
    must still pass; before the fix it failed three cases and switched behaviour off."""
    from forge_gate.scout import behaviour as B, box
    rel = {B.SQ_LOOP, B.SQ_GEN}

    def fake(job, query, members, timeout):
        if job["mode"] == "facts":
            return box.BoxResult(B.found("ran"), 0, "MOD " + json.dumps({n: True for n in job["modules"]}), "")
        out = []
        for p in job["pairs"]:
            good = query in (B.SQ_LOOP, B.Q_SQ) and members[p["c"][len("/ref/"):]] in rel
            out.append("PAIR " + json.dumps({"key": p["key"], "inputs": 9, "considered": 9, "agree": 9 if good else 1}))
        return box.BoxResult(B.found("ran"), 0, "\n".join(out), "")
    monkeypatch.setattr(B, "_box_job", fake)
    r0 = B.rank(B.Q_NULL, B._pool(), pool="rule2", trust="fixture")
    assert [h.slot for h in r0.hits] == ["explore"]          # the condition F1 needs is present
    assert B.rule2() == []


def test_the_explore_hit_is_labelled_and_never_counted_in_found(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _members(14))
    r = shape_lens.rank(rule2.NEAREST, _members(14), pool="corpus", trust="raw")
    assert r.hits[9].slot == "explore" and r.hits[9].score >= shape_lens.TAU     # the case can tell
    assert str(r.outcome).startswith(f"FOUND: 9 at or above J={shape_lens.TAU}") or \
        f"9 at or above J={shape_lens.TAU}" in str(r.outcome), str(r.outcome)
    q = tmp_path / "q.py"
    q.write_text(rule2.NEAREST)
    assert cli.main(["scout", str(q)]) == 0
    text = capsys.readouterr().out
    assert text.count("[explore: outside the top ranks]") == 1 and report.check_report_outcomes(text) == []


def test_the_explore_draw_is_the_documented_seed_not_luck():
    """S2 survived once by chance (5 candidates, 1 in 5 to repeat). Five queries over 50 candidates
    against the seed formula written out here: an unseeded draw passes with odds about 50**-5."""
    import hashlib
    import random
    rest = [{"item_id": f"c_{i:03d}"} for i in range(50)]
    for q in ("a", "b", "def f(): pass", "ünï", "query five"):
        seed = hashlib.sha256(f"shape\0{q}".encode("utf-8", "surrogatepass")).digest()
        want = random.Random(int.from_bytes(seed[:8], "big")).randrange(50)
        assert slots.pick([dict(r) for r in rest], lens="shape", query=q)["item_id"] == f"c_{want:03d}"


@pytest.mark.parametrize("view, want", [
    (dict(refused=[(ledger.EXPOSURE_NAME, 1, "not a JSON line")]), "LOOKED-CLEAN"),   # N1: counts untouched
    (dict(orphans=[{"mark_id": "fgm-000000000001"}]), "BLIND"),                     # N2: an outcome not counted
    (dict(refused=[(ledger.OUTCOME_NAME, 1, "not a JSON line")]), "BLIND"),
    (dict(torn=[(ledger.OUTCOME_NAME, 1)]), "BLIND"),             # 8b crew review, 6E T1: in flight is not 0
    (dict(torn=[(ledger.EXPOSURE_NAME, 1)]), "LOOKED-CLEAN"),     # a torn exposure touches no count
])
def test_only_lost_outcome_rows_blind_experience(export_root, lg, tmp_path, capsys, monkeypatch, view, want):
    _export(export_root, "20261102T000000Z", _members(3))
    monkeypatch.setattr(ledger, "view", lambda: ledger.View(**view))
    rep = _scout(tmp_path, capsys)
    assert rep["checks"]["experience"]["outcome"] == want, rep["checks"]["experience"]


# ---------------------------------------------------------------- 8b crew review (2026-11-17)

def test_a_torn_outcome_line_is_lost_and_a_torn_exposure_line_is_not():
    assert ledger.uncounted_outcomes(ledger.View(torn=[(ledger.OUTCOME_NAME, 3)])) == 1
    assert ledger.uncounted_outcomes(ledger.View(torn=[(ledger.EXPOSURE_NAME, 3)])) == 0


def _render(hits, key="shape_hits"):
    rep = {"file": "f.py", "language": "python", "sha256": "0", "checks": {},
           "related": {"corpus": {"export": "r", "agree": 1, "n": 3, "blind": 0, "skipped": [],
                                  "lenses": {"shape": report.found("1")}, "shape_hits": [], key: hits},
                       "library": {"trust": "signed", "outcome": report.blind("p")},
                       "registry": {"trust": "tier", "outcome": report.blind("p")},
                       "github": {"trust": "raw", "outcome": report.blind("p")}},
           "change": report.blind("none")}
    return report.render(rep)


def _h(i, slot="top"):
    return {"item_id": f"c_{i:02d}", "score": 0.9, "pool": "corpus", "trust": "raw", "kind": "function", "slot": slot,
            "lang": "python", "agree": 3, "considered": 3, "query_fn": "f", "cand_fn": "g"}


@pytest.mark.parametrize("key", ["shape_hits", "meaning_hits", "intent_hits", "behaviour_hits"])
def test_a_list_without_an_explore_hit_says_so_on_its_last_line(key):
    short = [_h(1), _h(2), _h(3)]
    lines = _render(short, key).splitlines()
    noted = [ln for ln in lines if "[explore: none, only 3 ranked, all shown]" in ln]
    assert len(noted) == 1 and "c_03" in noted[0]
    assert all("_explore_none" not in h for h in short)          # rendering never changes the report


@pytest.mark.parametrize("key", ["shape_hits", "meaning_hits", "intent_hits", "behaviour_hits"])
def test_a_list_with_an_explore_hit_does_not_say_none(key):
    full = [_h(i) for i in range(9)] + [_h(99, "explore")]
    text = _render(full, key)
    assert "explore: none" not in text and text.count("[explore: outside the top ranks]") == 1


def test_a_torn_outcome_line_is_named_in_the_blind_evidence(export_root, lg, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", _members(3))
    monkeypatch.setattr(ledger, "view", lambda: ledger.View(torn=[(ledger.OUTCOME_NAME, 7)]))
    e = _scout(tmp_path, capsys)["checks"]["experience"]
    assert e["outcome"] == "BLIND" and "refused, torn or orphaned" in e["reason"], e
    assert any(x.startswith("outcome line 7: torn") for x in e["evidence"]), e


def test_a_real_torn_line_beside_counted_rows_is_still_shown(export_root, lg, tmp_path, capsys):
    """8b re-check (5S R17/R18, 6E R1b): with rows accepted, experience is not BLIND, so the note is the
    only place a lost row shows. A real torn file on disk, not a synthetic View."""
    same = rule2.KMEANS + "\n# pool member body\n"
    _export(export_root, "20261102T000000Z",
            [{"item_id": "c_0000000000000001", "body": same, "lang": "python"},
             {"item_id": "c_0000000000000002", "body": same, "lang": "python"}])
    rid = _scout(tmp_path, capsys, same)["related"]["corpus"]["shape_hits"][1]["result_id"]
    mark = ledger.record_mark(who="ann", result_id=rid)
    proj = tmp_path / "proj"
    proj.mkdir()
    ledger.record_event(mark_id=mark["mark_id"], project=proj, cmd=[sys.executable, "-c", "pass"], event_kind="tests")
    with open(lg / ledger.OUTCOME_NAME, "ab") as f:
        f.write(b'{"kind": "mark", "who": "bo')                      # a writer killed mid-append
    rep = _scout(tmp_path, capsys, same)
    e = rep["checks"]["experience"]
    assert e["outcome"] == "FOUND", e
    assert "0 orphan, 0 refused, 1 torn row(s) not counted" in e["evidence"], e
    assert rep["related"]["corpus"]["shape_hits"][0]["experience"]["with_id"] == 1   # the torn row never scores
