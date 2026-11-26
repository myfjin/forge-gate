"""GitHub-lane S2: scout searches the GitHub pool (DESIGN github-lane-2026-11 §5 row S2).

The fixture repo (tests/fixtures/ghsearch_fixture/) is committed, ratified with throwaway test
keys and cached by ghcache, as in test_scout_learn_github. Nothing touches a network, a real key
or a real cache. GitHub code is text: a fixture file writes a canary if it is ever run."""
from __future__ import annotations

import builtins
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forgeproof.scout import ghcache, ghsearch, ratify, report
from forgeproof.scout.ids import make_id
from forgeproof.scout.outcomes import BLIND, FOUND
from test_scout_learn_github import GH, P

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

FIX = Path(__file__).parent / "fixtures" / "ghsearch_fixture"
def _fixture_files(root):
    """The fixture's source files. A plain pytest run compiles the fixture's test_*.py into
    __pycache__/ inside it; those bytes are not source and are skipped (KNOWN-DEBT KD-17 c)."""
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.relative_to(root).parts
            and not p.name.startswith(".")}


FILES = _fixture_files(FIX)
STATS = "Lib/fgfix/stats.py"
# running_median with every name changed: the shape lens must still see it.
QUERY = """def rolling_mid(xs, k):
    res = []
    win = []
    for x in xs:
        win.append(x)
        if len(win) > k:
            win.pop(0)
        s = sorted(win)
        m = len(s) // 2
        if len(s) % 2:
            res.append(s[m])
        else:
            res.append((s[m - 1] + s[m]) / 2)
    return res
"""


def _gh(tmp_path, files=None, **kw):
    d = tmp_path / "gh"
    d.mkdir()
    return GH(d, files or FILES, **kw)


def _sid(gh, path, fn):
    return make_id("github", "python/cpython", gh.sha, path, fn)


def _search(gh, query=QUERY, **kw):
    return ghsearch.search(query, **dict(gh.opts, **kw))


def test_a_near_copy_of_a_fixture_function_is_the_top_hit_with_its_scout_id(tmp_path):
    gh = _gh(tmp_path)
    r = _search(gh)
    assert r.outcome.kind == FOUND, r.outcome
    top = r.hits[0]
    assert top.item_id == _sid(gh, STATS, "running_median") and top.score >= 0.5
    assert (top.pool, top.trust, top.kind) == ("github", "raw", "function")
    assert r.control.kind == FOUND and "came back first at J=1.0" in r.control.evidence[0]
    ids = {h.item_id for h in r.hits}
    assert ids == {_sid(gh, STATS, "running_median"), _sid(gh, STATS, "Window.__init__"),
                   _sid(gh, STATS, "Window.push"), _sid(gh, "Lib/fgfix/util.py", "clamp_all")}
    assert r.looked_at == 4                                  # the test file is not a member
    assert r.repos[0]["files_read"] == 2 and r.repos[0]["excluded"] == {"not-included": 1}


@pytest.mark.parametrize("victim", [STATS, "Lib/fgfix/util.py"])
def test_a_tampered_cached_file_makes_the_whole_pool_blind_pin_mismatch(tmp_path, victim):
    gh = _gh(tmp_path)
    f = gh.cache / "python" / f"cpython@{gh.sha}" / victim
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text(f.read_text() + "\n# one more line\n")
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("pin-mismatch:"), r.outcome
    assert "bytes differ from the pinned blob" in r.outcome.reason and r.hits == []


def test_a_pin_record_that_names_another_tree_is_blind_pin_mismatch(tmp_path):
    gh = _gh(tmp_path)
    pin = gh.cache / "python" / f"cpython@{gh.sha}.pin.json"
    rec = json.loads(pin.read_text())
    rec["tree"] = "0" * 40
    os.chmod(pin.parent, 0o755)
    pin.chmod(0o644)
    pin.write_text(json.dumps(rec))
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("pin-mismatch:") and r.hits == []


def test_a_control_that_does_not_land_makes_the_pool_blind(tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    gone = {"item_id": _sid(gh, "Lib/fgfix/gone.py", "gone"), "body": QUERY.replace("rolling_mid", "gone") * 3,
            "lang": "python", "kind": "function"}
    monkeypatch.setattr(ghsearch, "_control_member", lambda members: gone)
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("control-failed: planted"), r.outcome
    assert r.control.kind == BLIND and r.hits == []


def test_the_control_must_come_back_as_itself_not_as_a_same_shape_twin(tmp_path, monkeypatch):
    # Two functions with identical shape shingles (only names differ). With the real selector the
    # control is the smaller id and wins the tie; a control that loses the tie to its twin has not
    # come back, even at J=1.0.
    gh = _gh(tmp_path, files={"LICENSE": FILES["LICENSE"], "Lib/pair.py": FILES[STATS].split("\n\nclass")[0]
                              + "\n\n\n" + QUERY})
    monkeypatch.setattr(ghsearch, "_control_member", lambda members: max(members, key=lambda m: m["item_id"]))
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("control-failed: planted"), r.outcome


def test_a_pool_with_nothing_long_enough_to_plant_is_blind_control(tmp_path):
    gh = _gh(tmp_path, files={"LICENSE": FILES["LICENSE"], "Lib/m.py": "def f():\n    return 1\n"})
    r = _search(gh)
    assert r.outcome.label() == "BLIND(control-failed: no pool member long enough to plant)"


def test_each_line_is_verified_once_and_each_cache_checked_once_per_query(tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    calls = {"ssh": 0, "check": 0, "read_file": 0}

    def count(name, real):
        def f(*a, **k):
            calls[name] += 1
            return real(*a, **k)
        return f
    monkeypatch.setattr(ratify, "_ssh_verify", count("ssh", ratify._ssh_verify))
    monkeypatch.setattr(ghcache, "check_cache", count("check", ghcache.check_cache))
    monkeypatch.setattr(ghcache, "read_file", count("read_file", ghcache.read_file))
    assert _search(gh).outcome.kind == FOUND
    assert calls == {"ssh": 2, "check": 1, "read_file": 0}     # two signatures, one cache; files via _read_pinned
    assert _search(gh).outcome.kind == FOUND
    assert calls == {"ssh": 4, "check": 2, "read_file": 0}


def test_unsigned_or_wrongly_pinned_lines_are_never_searched(tmp_path):
    gh = _gh(tmp_path, signers=("fives",))
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("unsigned: no verified line"), r.outcome
    assert r.hits == [] and r.repos == []
    (tmp_path / "gh2").mkdir()
    gh2 = GH(tmp_path / "gh2", FILES)
    r = _search(gh2, trust_pin_value=f"{P}={gh2.fives_id}")
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("unsigned: no verified line"), r.outcome
    r = _search(gh2, trust_pin_value="")
    assert r.outcome.kind == BLIND and "FORGEPROOF_RATIFY_PIN must be set" in r.outcome.reason


def test_one_refused_line_beside_a_good_one_blinds_the_pool_not_a_clean_search(tmp_path):
    gh = _gh(tmp_path)
    good = json.loads(gh.list.read_text())
    bad = dict(good, ratified_at="2026-11-15")            # fields changed after signing: bad signatures
    gh.list.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("unsigned: 1 line(s) of the list refused"), r.outcome
    assert any("line 2:" in e for e in r.outcome.evidence) and r.hits == []


def test_a_repo_whose_licence_file_is_not_the_signed_blob_is_left_out(tmp_path):
    gh = _gh(tmp_path, licence_blob="0" * 40)
    r = _search(gh)
    assert r.repos[0]["left_out"].startswith("licence:") and r.hits == [] and r.looked_at == 0
    assert r.outcome.kind == BLIND, r.outcome


def test_a_licence_off_the_allowlist_is_left_out(tmp_path):
    gh = _gh(tmp_path, spdx="GPL-3.0-only")
    r = _search(gh)
    assert r.repos[0]["left_out"].startswith("licence: 'GPL-3.0-only'") and r.hits == []


def test_a_file_with_its_own_copyleft_header_is_not_a_member(tmp_path):
    files = dict(FILES)
    files["Lib/fgfix/gpl.py"] = "# SPDX-License-Identifier: GPL-3.0-only\n" + FILES[STATS].split("\n\nclass")[0]
    gh = _gh(tmp_path, files=files)
    r = _search(gh)
    assert r.outcome.kind == FOUND and r.repos[0]["excluded"].get("licence-file") == 1
    assert not any("gpl.py" in h.item_id for h in r.hits) and r.looked_at == 4


def test_github_code_is_read_as_text_and_never_run(tmp_path, monkeypatch):
    import ast as _ast
    gh = _gh(tmp_path)
    canary = tmp_path / "RAN"
    monkeypatch.setenv("FG_GHSEARCH_CANARY", str(canary))

    def refuse(*a, **k):
        raise AssertionError("the GitHub pool search tried to execute code")
    real_compile, real_import = builtins.compile, builtins.__import__
    monkeypatch.setattr(builtins, "exec", refuse)
    monkeypatch.setattr(builtins, "eval", refuse)
    monkeypatch.setattr(builtins, "compile", lambda src, fn, mode, flags=0, *a, **k:
                        real_compile(src, fn, mode, flags, *a, **k) if flags & _ast.PyCF_ONLY_AST else refuse())
    monkeypatch.setattr(builtins, "__import__", lambda n, *a, **k:
                        refuse() if n.split(".")[0] in ("fgfix", "stats", "util", "Lib") else real_import(n, *a, **k))
    real_popen = subprocess.Popen

    def popen(args, *a, **k):
        if list(args)[:1] not in (["git"], ["ssh-keygen"]):
            raise AssertionError(f"started a process other than git / ssh-keygen: {list(args)[:3]}")
        return real_popen(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", popen)
    r = _search(gh)
    assert r.outcome.kind == FOUND and r.looked_at == 4       # it did look, through every file
    assert not canary.exists()


def test_the_report_prints_github_hits_as_raw_with_scout_ids(tmp_path, capsys):
    gh = _gh(tmp_path)
    q = tmp_path / "query.py"
    q.write_text(QUERY)
    rep = report.run(q, gh_opts=gh.opts)
    assert rep["checks"]["control-github"].kind == FOUND
    assert rep["related"]["github"]["outcome"].kind == FOUND
    out = report.render(rep)
    line = next(ln for ln in out.splitlines() if _sid(gh, STATS, "running_median") in ln)
    assert line.lstrip().startswith("J=") and "pool=github trust=raw" in line and "scout:github:" in line
    assert "  github (raw): FOUND" in out
    assert report.check_report_outcomes(out) == []
    json.loads(report.to_json(rep))


def test_the_report_with_a_tampered_cache_says_blind_pin_mismatch_and_shows_no_hit(tmp_path):
    gh = _gh(tmp_path)
    f = gh.cache / "python" / f"cpython@{gh.sha}" / STATS
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text(QUERY)
    q = tmp_path / "query.py"
    q.write_text(QUERY)
    out = report.render(report.run(q, gh_opts=gh.opts))
    assert "  github (raw): BLIND(pin-mismatch:" in out and "pool=github" not in out
    assert report.check_report_outcomes(out) == []


def test_members_are_functions_with_qualified_names_and_distinct_ids():
    text = ("class A:\n    def f(self):\n        return 1\n\n    def f(self):\n        return 2\n\n"
            "def g():\n    def h():\n        return 3\n    return h\n")
    ms = ghsearch.members_of(text, "o", "r", "a" * 40, "pkg/m.py")
    names = [m["item_id"].rsplit("::", 1)[1] for m in ms]
    assert names == ["A.f", "A.f%23L5", "g", "g.h"]
    assert ms[1]["body"] == "    def f(self):\n        return 2\n"
    assert len({m["item_id"] for m in ms}) == 4


# --- parent verify round 1 (S2 NOT VERIFIED, 2026-11-22T12:47Z): Q1, Q2, Q4-Q8 -----------------------
@pytest.mark.parametrize("bad", ["/etc/passwd", "Lib/../LICENSE", "Lib//x.py"])
def test_read_pinned_refuses_an_absolute_or_dotdot_path(tmp_path, bad):
    with pytest.raises(ghcache.PinMismatch, match="not a plain relative path"):
        ghsearch._read_pinned(tmp_path, bad, "0" * 40)


def test_a_symlink_in_the_exported_tree_blinds_the_pool_even_with_the_right_bytes(tmp_path):
    gh = _gh(tmp_path)
    f = gh.cache / "python" / f"cpython@{gh.sha}" / STATS
    twin = tmp_path / "outside.py"
    twin.write_bytes(f.read_bytes())                  # same bytes: the blob check alone would pass
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.unlink()
    f.symlink_to(twin)
    r = _search(gh)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("pin-mismatch:"), r.outcome
    assert "not a regular file" in r.outcome.reason and r.hits == []


def test_a_failing_rule2_never_reaches_the_github_search(tmp_path, monkeypatch):
    from forgeproof.scout import rule2
    monkeypatch.setattr(rule2, "evaluate", lambda *a, **k: ["planted rule2 failure"])
    called = []
    monkeypatch.setattr(ghsearch, "search", lambda *a, **k: called.append(1) or ghsearch.GhResult(_clean_spy()))
    q = tmp_path / "query.py"
    q.write_text(QUERY)
    rep = report.run(q, gh_opts={})
    assert called == []
    assert rep["related"]["github"]["outcome"].label() == "BLIND(rule2-failed: lens not switched on)"


def _clean_spy():
    from forgeproof.scout.outcomes import clean
    return clean("spy")


def test_a_trust_pin_naming_another_principal_is_unsigned(tmp_path):
    gh = _gh(tmp_path)
    r = _search(gh, trust_pin_value=f"fives={gh.fives_id}")
    assert r.outcome.kind == BLIND and "FORGEPROOF_RATIFY_PIN must be set" in r.outcome.reason, r.outcome
    assert r.hits == []


def test_a_path_the_cache_record_skipped_never_becomes_a_member(tmp_path):
    gh = _gh(tmp_path)
    pin = gh.cache / "python" / f"cpython@{gh.sha}.pin.json"
    rec = json.loads(pin.read_text())
    rec["skipped"] = ["Lib/fgfix/util.py"]
    os.chmod(pin.parent, 0o755)
    pin.chmod(0o644)
    pin.write_text(json.dumps(rec))
    r = _search(gh)
    assert r.outcome.kind == FOUND and r.looked_at == 3, r.outcome
    assert not any("util.py" in h.item_id for h in r.hits)


def test_a_file_excluded_by_its_content_is_not_a_member(tmp_path):
    files = dict(FILES)
    files["Lib/fgfix/gen.py"] = "# Code generated by a tool. DO NOT EDIT.\n" + FILES[STATS].split("\n\nclass")[0]
    gh = _gh(tmp_path, files=files)
    r = _search(gh)
    assert r.outcome.kind == FOUND and r.looked_at == 4, r.outcome
    assert r.repos[0]["excluded"] == {"not-included": 2}
    assert not any("gen.py" in h.item_id for h in r.hits)


@pytest.mark.parametrize("lang", ["r", None])
def test_a_non_python_query_is_blind_language_and_nothing_is_searched(tmp_path, monkeypatch, lang):
    gh = _gh(tmp_path)
    touched = []
    monkeypatch.setattr(ratify, "verify_list", lambda *a, **k: touched.append("verify") or [])
    monkeypatch.setattr(ghcache, "check_cache", lambda *a, **k: touched.append("check"))
    r = _search(gh, lang=lang)
    assert r.outcome.kind == BLIND and r.outcome.reason.startswith("language:"), r.outcome
    assert touched == [] and r.hits == [] and r.looked_at == 0


def test_fixture_reader_skips_pycache(tmp_path):
    """KD-17 c: a compiled file under the fixture (as a plain pytest run leaves one) must not break
    the read, and must not enter the pool's files."""
    fx = tmp_path / "fx"
    shutil.copytree(FIX, fx)
    pc = fx / "Lib" / "test" / "__pycache__"
    pc.mkdir(exist_ok=True)
    (pc / "test_stats.cpython-310.pyc").write_bytes(b"o\r\r\n\xd6\xff\x00compiled")
    (fx / "Lib" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xd6\xff")
    assert _fixture_files(fx) == FILES
    # close review N2: a filter that drops too much must fail too; these are the tracked files.
    assert sorted(FILES) == ["LICENSE", "Lib/fgfix/stats.py", "Lib/fgfix/util.py", "Lib/test/test_stats.py"]
