"""Build step 8b slice 2: the language-meaning lens (DECISIONS-8b H2-H5).

Fake embedders only (the real model is `-m live`, deploy host): a JOB embedder that knows which
fixture does which job (it must pass the Rule-2 set), a WORDS embedder that is a bag of words (the
keyword baseline in disguise: it must fail), and single-case breakers (each must turn exactly its
case red). Rule 2 is a gate, so the gate is what these tests attack."""
from __future__ import annotations

import hashlib
import json
import math
import re

import pytest

from forge_gate import cli, config, ledger
from forge_gate.scout import embed, meaning_lens as M, report, rule2, shape_lens

from test_scout_slice1 import _export, export_root  # noqa: F401  (fixture)

DIG = "d" * 64
JOBS = {"backup": ("backup_tar", "backup_py", "backup_rsync"), "errcount": ("errcount_py", "errcount_sh"),
        "kmeans": ("kmeans",), "area": ("area",), "json": ("json_stats",)}
QUERIES = {"kmeans": rule2.NEAREST, "tax": rule2.TAX, "null": rule2.NULL, "trap": rule2.KEYWORD_TRAP}
AXES = ["backup", "errcount", "kmeans", "area", "json", "tax", "null", "trap", "other"]


def onehot(axis: str, wobble: float = 0.0) -> list:
    v = [0.0] * (len(AXES) + 1)
    v[AXES.index(axis)] = 1.0
    v[-1] = wobble                       # same job, slightly different look: cos still > 0.99
    return v


def job_table(overrides=None) -> dict:
    """text (for both scorers) -> vector; fixtures by the job they do."""
    t = {}
    for job, names in JOBS.items():
        for i, n in enumerate(names):
            t[M.POOL[n]] = onehot(job, 0.05 * i)
    for job, q in QUERIES.items():
        t[q] = onehot(job)
    for name, axis in (overrides or {}).items():
        t[M.POOL.get(name) or QUERIES[name]] = onehot(axis, 0.01)
    return {sc: {M.lens_text(k, sc): v for k, v in t.items()} for sc in M.SCORERS}


class Fake:
    """An embedder: ``table[scorer-text] -> vector``, anything else hashed (unrelated)."""
    def __init__(self, table=None, digest=DIG):
        self.table, self.digest, self.calls = table or {}, digest, []

    def check(self):
        pass

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = next((tab[t] for tab in self.table.values() if t in tab), None)
            if v is None:
                h = hashlib.sha256(t.encode()).digest()
                v = [0.0] * len(AXES) + [1.0] + [b / 255 for b in h[:4]]
                v = v[:len(AXES) + 1]
                v[AXES.index("other")] = 1.0
            out.append(v)
        return out


class Words:
    """A bag-of-words embedder: the keyword baseline wearing an embedder's clothes."""
    digest = DIG

    def check(self):
        pass

    def embed(self, texts):
        vocab = sorted({w for t in texts for w in re.findall(r"[a-z]+", t.lower())})
        return [[float(w in set(re.findall(r"[a-z]+", t.lower()))) for w in vocab] or [1.0] for t in texts]


# ---------------------------------------------------------------- Rule 2 (written first)

def test_a_lens_that_knows_the_jobs_passes_every_case():
    sc, tried = M.choose(Fake(job_table()))
    assert sc == "raw" and tried == {"raw": []}


def test_red_arm_a_bag_of_words_embedder_is_not_switched_on():
    sc, tried = M.choose(Words())
    assert sc == "" and set(tried) == set(M.SCORERS)
    assert all(tried[s] for s in M.SCORERS)
    assert any("keyword trap" in f or "same-meaning" in f for f in tried["raw"])


def test_red_arm_the_keyword_baseline_itself_cannot_pass():
    fails = M.rule2_cases(shape_lens.keyword_score, 0.1, shape_lens.keyword_score)
    assert any(f.startswith("baseline:") for f in fails) and any("same-meaning" in f for f in fails)


def test_red_arm_shape_cannot_pass_the_meaning_set():
    fails = M.rule2_cases(shape_lens.score, shape_lens.TAU, shape_lens.keyword_score)
    assert any("same-meaning/different-look" in f for f in fails)
    assert any(f.startswith("TAX") for f in fails)          # shape matches same look by design


@pytest.mark.parametrize("override, case", [
    ({"trap": "backup"}, "keyword trap"),
    ({"tax": "area"}, "TAX (same look as area)"),
    ({"null": "kmeans"}, "null (Stack)"),
    ({"kmeans": "null"}, "renamed copy"),
    ({"backup_py": "json"}, "same-meaning/different-look: backup_py"),
    ({"errcount_sh": "other"}, "same-meaning/different-look: errcount_sh"),
])
def test_each_case_turns_red_on_its_own(override, case):
    t = job_table(override)
    fails = M.rule2_run(Fake(t), "raw")
    assert fails and any(f.startswith(case) for f in fails), fails


def test_a_same_job_partner_below_tau_is_red_even_when_ranked_first():
    t = job_table()
    for tab in t.values():
        for n in ("errcount_py", "errcount_sh"):
            k = M.lens_text(M.POOL[n], "raw")
            if k in tab:
                v = onehot("errcount")
                v[AXES.index("other")] = 2.0 if n.endswith("sh") else -2.0
                tab[k] = v
    fails = M.rule2_run(Fake(t), "raw")
    assert any(f.startswith("same-meaning/different-look: errcount") for f in fails), fails


def test_the_trap_still_lures_the_keyword_baseline():
    top = max((shape_lens.keyword_score(rule2.KEYWORD_TRAP, b), n) for n, b in M.POOL.items())
    assert top[1].startswith("backup")


def test_the_second_family_is_invisible_to_shape():
    assert shape_lens.score(M.ERRCOUNT_PY, M.ERRCOUNT_SH) < shape_lens.TAU


# ---------------------------------------------------------------- words_text

def test_words_text_keeps_comments_and_split_identifiers_drops_keywords_and_shebang():
    t = M.words_text("#!/bin/bash\n# Rotate old Logs\ndef rotateLogFiles(max_age):\n    return max_age\n")
    assert t.split() == ["rotate", "old", "logs", "rotate", "log", "files", "max", "age", "max", "age"]


def test_text_sent_is_capped():
    assert len(M.lens_text("x " * 10000, "raw")) == M.TEXT_MAX


# ---------------------------------------------------------------- rank and control

def _members():
    return [{"item_id": "c_%016x" % i, "body": b, "lang": l, "kind": "write"} for i, (b, l) in enumerate(
        [(rule2.BACKUP_PY, "python"), (rule2.BACKUP_TAR, "shell"), (rule2.AREA, "python"),
         (rule2.KMEANS, "python")], 1)]


def _cache(emb, members, scorer="raw"):
    texts = [M.lens_text(m["body"], scorer) for m in members]
    return {embed.key(t, emb.digest): v for t, v in zip(texts, emb.embed(texts))}


def test_rank_finds_the_same_job_across_languages():
    e = Fake(job_table())
    ms = _members()
    r = M.rank(rule2.BACKUP_RSYNC, ms, emb=e, cache=_cache(e, ms), scorer="raw", pool="corpus", trust="raw")
    assert r.outcome.kind == "FOUND" and r.looked_at == 4
    assert {h.item_id for h in r.hits[:2]} == {"c_0000000000000001", "c_0000000000000002"}
    assert {h.lang for h in r.hits[:2]} == {"python", "shell"}
    assert [h.score for h in r.hits] == sorted((h.score for h in r.hits), reverse=True)


def test_rank_with_nothing_related_is_looked_clean():
    e = Fake(job_table())
    ms = _members()
    r = M.rank(rule2.NULL, ms, emb=e, cache=_cache(e, ms), scorer="raw", pool="corpus", trust="raw")
    assert r.outcome.kind == "LOOKED-CLEAN" and r.hits


def test_a_member_missing_from_the_cache_makes_the_lens_blind_never_partial():
    e = Fake(job_table())
    ms = _members()
    c = _cache(e, ms)
    c.pop(embed.key(M.lens_text(ms[0]["body"], "raw"), e.digest))
    r = M.rank(rule2.BACKUP_RSYNC, ms, emb=e, cache=c, scorer="raw", pool="corpus", trust="raw")
    assert r.outcome.kind == "BLIND" and "index-incomplete: 3 of 4" in r.outcome.reason and r.hits == []
    assert e.calls[-1] != [M.lens_text(rule2.BACKUP_RSYNC, "raw")]     # the query was not even embedded


def test_a_cache_from_another_digest_does_not_count():
    e = Fake(job_table())
    ms = _members()
    r = M.rank(rule2.BACKUP_RSYNC, ms, emb=e, cache=_cache(Fake(job_table(), "e" * 64), ms),
               scorer="raw", pool="corpus", trust="raw")
    assert r.outcome.kind == "BLIND" and "0 of 4" in r.outcome.reason


def test_a_query_with_too_few_words_is_blind():
    e = Fake(job_table())
    r = M.rank("x = 1", _members(), emb=e, cache={}, scorer="words", pool="corpus", trust="raw")
    assert r.outcome.kind == "BLIND" and r.outcome.reason.startswith("no-input")


def test_control_comes_back_and_catches_a_cache_from_another_model_state():
    e = Fake(job_table())
    ms = _members()
    c = _cache(e, ms)
    ok, why = M.control(ms, emb=e, cache=c, scorer="raw")
    assert ok and "cos=1.0000" in why
    planted = min(ms, key=lambda m: (len(m["body"]), m["item_id"]))
    k = embed.key(M.lens_text(planted["body"], "raw"), e.digest)
    c[k] = onehot("null")
    ok, why = M.control(ms, emb=e, cache=c, scorer="raw")
    assert not ok and "live vs cached" in why


# ---------------------------------------------------------------- in the report

QUERY_BODY = rule2.BACKUP_RSYNC.replace("#!/bin/bash\n", "")      # scouted as a .py file (slice 1)


@pytest.fixture
def wired(export_root, tmp_path, monkeypatch):
    ms = _members()
    _export(export_root, "20261102T000000Z", ms)
    t = job_table()
    for sc in M.SCORERS:
        t[sc][M.lens_text(QUERY_BODY, sc)] = onehot("backup", 0.07)
    e = Fake(t)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o700)                 # umask-proof: the deploy host's umask is 002
    monkeypatch.setenv(config.EMBED_CACHE_KEY, str(cache_dir))
    monkeypatch.setattr(embed, "get", lambda: e)
    return e, ms, cache_dir


def _scout(tmp_path, capsys, body=QUERY_BODY):
    q = tmp_path / "q.py"
    q.write_text(body)
    assert cli.main(["scout", str(q), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_before_the_fill_the_lens_is_blind_index_incomplete(wired, tmp_path, capsys):
    rep = _scout(tmp_path, capsys)
    c = rep["related"]["corpus"]
    assert rep["checks"]["rule2-meaning"]["outcome"] == "LOOKED-CLEAN"
    assert c["lenses"]["meaning"]["outcome"] == "BLIND"
    assert "index-incomplete" in c["lenses"]["meaning"]["reason"] and "--embed-fill" in c["lenses"]["meaning"]["reason"]


def test_after_the_fill_meaning_finds_what_shape_misses_and_the_group_counts_once(wired, tmp_path, capsys):
    e, ms, d = wired
    assert cli.main(["scout", "--embed-fill"]) == 0
    assert "4 embedded, 0 already cached" in capsys.readouterr().out
    rep = _scout(tmp_path, capsys)
    c = rep["related"]["corpus"]
    assert rep["checks"]["control-meaning"]["outcome"] == "FOUND"
    assert c["lenses"]["meaning"]["outcome"] == "FOUND" and c["meaning_scorer"] == "raw"
    assert c["lenses"]["shape"]["outcome"] != "FOUND"                        # shape misses the job
    assert {h["item_id"] for h in c["meaning_hits"][:2]} == {"c_0000000000000001", "c_0000000000000002"}
    assert c["groups"]["shape+meaning"]["outcome"] == "FOUND"
    assert c["n"] == 3 and c["agree"] == sum(1 for o in c["groups"].values() if o["outcome"] == "FOUND")
    assert cli.main(["scout", "--embed-fill"]) == 0
    assert "0 embedded, 4 already cached" in capsys.readouterr().out


def test_the_rendered_report_shows_meaning_lines_with_outcomes(wired, tmp_path, capsys):
    cli.main(["scout", "--embed-fill"])
    q = tmp_path / "q.py"
    q.write_text(QUERY_BODY)
    capsys.readouterr()
    assert cli.main(["scout", str(q)]) == 0
    text = capsys.readouterr().out
    assert "M=1.00" in text and "reason: same meaning (raw embedding)" in text
    assert report.check_report_outcomes(text) == []


def test_meaning_hits_get_exposure_rows_and_result_ids(wired, tmp_path, capsys, monkeypatch):
    from test_ledger import make_ledger
    d = make_ledger(tmp_path)
    monkeypatch.setenv(config.LEDGER_KEY, str(d))
    cli.main(["scout", "--embed-fill"])
    capsys.readouterr()
    c = _scout(tmp_path, capsys)["related"]["corpus"]
    rows = [r for r in ledger.view().exposure.values() if r["lens"] == "meaning"]
    assert len(rows) == len(c["meaning_hits"]) > 0
    assert all(h["result_id"].split("-")[2] == "meaning" for h in c["meaning_hits"])


def test_a_red_rule2_switches_the_lens_off_and_fill_refuses(export_root, tmp_path, capsys, monkeypatch):
    _export(export_root, "20261102T000000Z", _members())
    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setenv(config.EMBED_CACHE_KEY, str(d))
    monkeypatch.setattr(embed, "get", lambda: Words())
    assert cli.main(["scout", "--embed-fill"]) == 2
    assert "the meaning lens is off" in capsys.readouterr().err
    assert list(d.iterdir()) == []
    rep = _scout(tmp_path, capsys)
    c = rep["related"]["corpus"]
    assert rep["checks"]["rule2-meaning"]["outcome"] == "FOUND"          # FOUND = failed cases found
    assert c["lenses"]["meaning"]["outcome"] == "BLIND" and "rule2-failed" in c["lenses"]["meaning"]["reason"]
    assert c["meaning_hits"] == []


def test_without_an_embedder_meaning_is_blind_and_scout_still_reports(export_root, tmp_path, capsys):
    _export(export_root, "20261102T000000Z", _members())
    rep = _scout(tmp_path, capsys)
    m = rep["related"]["corpus"]["lenses"]["meaning"]
    assert m["outcome"] == "BLIND" and "not-configured: FORGE_GATE_EMBED_URL" in m["reason"]
    assert rep["checks"]["rule2-meaning"]["outcome"] == "BLIND"


def test_embed_fill_takes_no_file(capsys):
    assert cli.main(["scout", "x.py", "--embed-fill"]) == 2


@pytest.mark.parametrize("s, m, want", [("FOUND", "LOOKED-CLEAN", "FOUND"), ("LOOKED-CLEAN", "FOUND", "FOUND"),
                                         ("BLIND", "FOUND", "FOUND"), ("BLIND", "LOOKED-CLEAN", "LOOKED-CLEAN"),
                                         ("LOOKED-CLEAN", "BLIND", "LOOKED-CLEAN"), ("BLIND", "BLIND", "BLIND")])
def test_group_outcome(s, m, want):
    mk = {"FOUND": report.found, "LOOKED-CLEAN": report.clean, "BLIND": report.blind}
    assert report.group_outcome(mk[s]("x"), mk[m]("y")).kind == want


# ---------------------------------------------------------------- the real model (deploy host)

@pytest.mark.live
def test_live_rule2_on_the_real_model():
    """Evidence run, not a pass/fail on the model: the lens is switched on iff its scorer has no failed
    case, and the keyword baseline swapped in for the lens is red on the same live fixtures."""
    try:
        e = embed.get()
        sc, tried = M.choose(e)
    except embed.EmbedError as err:
        pytest.skip(str(err))
    print("scorer:", sc or "NONE", json.dumps(tried, indent=1))
    assert M.rule2_cases(shape_lens.keyword_score, M.TAU["raw"], shape_lens.keyword_score)


class Flat:
    digest = "f" * 64

    def check(self):
        pass

    def embed(self, texts):
        return [[1.0, float(len(t) % 7) + 1.0] for t in texts]


def test_a_member_too_short_to_embed_is_left_out_of_rank():
    ms = [{"item_id": "c_1", "body": "x = 1\n", "lang": "python"},
          {"item_id": "c_2", "body": rule2.KMEANS, "lang": "python"}]
    assert [m["item_id"] for m in M.comparable(ms, "words")] == ["c_2"]
    e, t = Flat(), M.lens_text(rule2.KMEANS, "words")
    r = M.rank(rule2.KMEANS, ms, emb=e, cache={embed.key(t, e.digest): e.embed([t])[0]}, scorer="words",
               pool="corpus", trust="raw")
    assert r.outcome.kind == "FOUND" and r.looked_at == 1
    assert "1 member(s) under 3 words not compared" in str(r.outcome)


def test_a_too_short_member_neither_stops_the_fill_nor_blinds_the_lens(wired, export_root, tmp_path, capsys):
    e, ms, d = wired
    _export(export_root, "20261103T000000Z", ms + [{"item_id": "c_ffffffffffffffff", "body": "x=1\n", "lang": "python"}])
    assert cli.main(["scout", "--embed-fill"]) == 0
    assert "4 embedded, 0 already cached, 1 under 3 words skipped" in capsys.readouterr().out
    m = _scout(tmp_path, capsys)["related"]["corpus"]["lenses"]["meaning"]
    assert m["outcome"] == "FOUND" and "1 member(s) under 3 words not compared" in json.dumps(m)


def test_a_partner_ranked_first_but_below_tau_is_red():
    """M1: the partners are first, but one pair scores under tau; only that case may fail."""
    t = job_table()
    for sc in M.SCORERS:
        t[sc][M.lens_text(M.POOL["backup_py"], sc)] = onehot("backup", 2.0)
    fails = M.rule2_run(Fake(t), "raw")
    assert fails and all(f.startswith("same-meaning/different-look") for f in fails), fails


def test_a_baseline_the_trap_does_not_lure_is_reported():
    fails = M.rule2_cases(shape_lens.keyword_score, M.TAU["raw"], lambda a, b: float(b == M.POOL["area"]))
    assert any("the keyword trap did not lure the baseline" in f for f in fails), fails


def test_shape_and_meaning_both_found_count_once(wired, export_root, tmp_path, capsys):
    e, ms, d = wired
    _export(export_root, "20261103T000000Z",
            ms + [{"item_id": "c_eeeeeeeeeeeeeeee", "body": QUERY_BODY, "lang": "python", "kind": "write"}])
    assert cli.main(["scout", "--embed-fill"]) == 0
    capsys.readouterr()
    c = _scout(tmp_path, capsys)["related"]["corpus"]
    assert c["lenses"]["shape"]["outcome"] == "FOUND" and c["lenses"]["meaning"]["outcome"] == "FOUND"
    assert c["agree"] == sum(1 for g in c["groups"].values() if g["outcome"] == "FOUND")
    assert c["agree"] == sum(1 for o in c["lenses"].values() if o["outcome"] == "FOUND") - 1


def test_a_failed_control_blinds_the_lens(wired, tmp_path, capsys, monkeypatch):
    assert cli.main(["scout", "--embed-fill"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(M, "control", lambda *a, **k: (False, "planted c_x: live vs cached cos=0.5 < 0.999"))
    c = _scout(tmp_path, capsys)["related"]["corpus"]
    assert c["lenses"]["meaning"]["outcome"] == "BLIND"
    assert c["lenses"]["meaning"]["reason"].startswith("control-failed") and c["meaning_hits"] == []


def test_the_same_look_case_says_when_it_no_longer_tests_anything(monkeypatch):
    """8b crew review (6E): TAX tests 'same look, different meaning' only while shape matches it to area."""
    assert not any(f.startswith("case invalid") for f in M.rule2_run(Fake(job_table()), "raw"))
    monkeypatch.setattr(shape_lens, "score", lambda a, b: 0.0)
    assert any(f.startswith("case invalid") for f in M.rule2_run(Fake(job_table()), "raw"))
