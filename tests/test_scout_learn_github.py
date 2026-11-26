"""Step 7 slice 4: the GitHub pool in learn (design §12.5; DECISIONS-7-github G4, G5, G6, G7).

A local fixture repo stands in for cpython, signed lines use throwaway test keys, and the cache
is built by ghcache from a depth-1 fetch of the fixture. Nothing touches a network or a real
key, and nothing touches a live Brain: every Brain file is a tmp copy (the env fixture)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forgeproof import cli
from forgeproof.scout import ghcache, ghpool, learn, ratify
from forgeproof.scout.engines import chaos
from test_ratify import _key, _sign            # throwaway ssh keys + signer
from test_scout_learn import Env, _rows, engines_with

P = ratify.REQUIRED_PRINCIPAL      # the published copy renames the principal; the tests follow it

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

GENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"], "HOME": os.devnull}
REQUEST = "def urlopen(url, data=None):\n    return url\n\n\ndef build_opener(handlers):\n    return list(handlers)\n"
LICENSE = "PSF LICENSE AGREEMENT FOR PYTHON (fixture)\n"


def g(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, env=GENV, check=True, capture_output=True).stdout.decode().strip()


def _blob(text: str) -> str:
    return ghcache.blob_sha(text.encode())


class GH:
    """A ratified, cached fixture repo plus a learn env (tmp Brain data, scout dir, lock)."""

    def __init__(self, tmp: Path, files: dict, *, spdx="Python-2.0", licence_blob=None, signers=(P, "fives")):
        src = tmp / "origin"
        src.mkdir()
        g("init", "-q", "-b", "main", cwd=src)
        for rel, text in files.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(text)
        g("add", "-A", cwd=src)
        g("commit", "-q", "-m", "pin", cwd=src)
        self.sha = g("rev-parse", "HEAD", cwd=src)
        kept = tmp / "kept"
        g("init", "-q", "--bare", str(kept))
        g("-c", "protocol.file.allow=always", "-c", "fetch.unpackLimit=1", "--git-dir", str(kept), "fetch", "-q",
          "--depth", "1", f"file://{src}", "main")
        keys = {p: _key(tmp, p) for p in (P, "fives")}
        self.signers = tmp / "allowed_signers"
        self.signers.write_text("".join(f'{p} namespaces="forge-gate-ratify" ssh-ed25519 {keys[p][1]}\n' for p in keys))
        fields = {"org": "python", "repo": "cpython", "commit": self.sha, "licence_spdx": spdx,
                  "licence_blob": licence_blob or _blob(files.get("LICENSE", "")), "host_set": "github-git",
                  "trust_signals": {"first_commit_age_years": {"value": 35, "source": "5S G report (relayed)"},
                                    "contributors": {"value": 4100, "source": "5S G report (relayed)"},
                                    "closed_issues": {"value": None, "source": "[NEED]"}},
                  "candidate_ref": "G-repo-selection-REPORT.md", "ratified_at": "2026-11-14"}
        data = ratify.canonical(fields)
        line = dict(fields, signatures=[{"signer": w, "sig": _sign(keys[w][0], data)} for w in signers])
        self.list = tmp / "trusted_repos.jsonl"
        self.list.write_text(json.dumps(line) + "\n")
        self.cache = tmp / "cache"
        self.cache.mkdir()
        v = ratify.ratified(self.list, self.signers, "python", "cpython", self.sha)
        if v.ok:
            ghcache.build(kept, "python", "cpython", self.sha, v, root=self.cache)
        self.key_ids = sorted(ratify.key_id(keys[p][1]) for p in keys)
        self.spec = f"python/cpython@{self.sha}"
        self.pin = f"{P}={ratify.key_id(keys[P][1])}"
        self.fives_id = ratify.key_id(keys["fives"][1])
        self.opts = {"list_file": self.list, "signers_file": self.signers, "cache_root": self.cache,
                     "trust_pin_value": self.pin}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from forgeproof import config
    e = Env(tmp_path)
    monkeypatch.setenv(config.BRAIN_DATA_KEY, str(e.data))
    monkeypatch.setenv(config.SCOUT_DIR_KEY, str(e.scout))
    monkeypatch.setenv(config.BUILD_LOCK_KEY, str(e.lock))
    return e


def _gh(tmp_path, **kw):
    files = kw.pop("files", None) or {"LICENSE": LICENSE, "Lib/urllib/request.py": REQUEST}
    d = tmp_path / "gh"
    d.mkdir()
    return GH(d, files, **kw)


def _brain_bytes(env):
    return {p.name: p.read_bytes() for p in sorted(env.data.iterdir())}


def test_the_real_battery_on_a_ratified_file_writes_nothing_and_chaos_says_why(env, tmp_path):
    """G6: chaos stays BLIND for GitHub files (no validator probe for these bytes), so learn on
    cpython writes 0 rows today; the chaos history is never touched (no child runs)."""
    gh = _gh(tmp_path)
    before = _brain_bytes(env)
    res = learn.run("Lib/urllib/request.py", None, github=gh.spec, gh_opts=gh.opts)
    assert res.code == learn.EXIT_OK, res.outcome
    assert res.outcome.startswith("0 rows"), res.outcome
    fns = {f["entry"]: f for f in res.report["functions"]}
    assert set(fns) == {"urlopen", "build_opener"}
    for f in fns.values():
        assert f["engines"]["chaos"] == "BLIND(no validator chaos probe for these bytes)", f
        assert f["id"].startswith(f"scout:github:python%2Fcpython@{gh.sha}:Lib%2Furllib%2Frequest.py::")
    assert _brain_bytes(env) == before
    assert not (env.scout / chaos.HISTORY_NAME).exists()
    assert res.report["pool"]["ratified_by"] == gh.key_ids and res.report["pool"]["licence"] == "Python-2.0"


def test_a_fail_row_from_github_carries_the_125_metadata_and_no_author(env, tmp_path):
    gh = _gh(tmp_path)
    res = learn.run("Lib/urllib/request.py", None, github=gh.spec, gh_opts=gh.opts,
                    engines=engines_with(metamorphic="FAIL"))
    assert res.code == learn.EXIT_OK and res.report["rows_appended"] == 2, res.outcome
    row = _rows(env.scout_main)[0]
    ex = row["extraction"]
    assert ex["method"] == "scout-github" and ex["pool"] == "github"
    assert ex["src"] == f"github.com/python/cpython@{gh.sha}/Lib/urllib/request.py"
    assert ex["license"] == f"Python-2.0@{gh.sha}" and ex["ratified_by"] == gh.key_ids
    assert ex["repo_trust_score"] == {"met": 2, "known": 2, "of": 5,
                                      "unknown": ["last_commit_age_days", "closed_issues", "dependents"]}
    assert "author" not in ex and "GitHub python/cpython@" in row["core_idea"]
    assert "open-source code" in row["belief_sentence"]["text"] and row["strength"] == "none"
    rep = json.loads((env.scout / learn.REPORT_NAME).read_text())
    cards = rep if isinstance(rep, list) else rep.get("cards", rep)
    assert f"forgeproof learn --github python/cpython@{gh.sha} Lib/urllib/request.py" in json.dumps(cards)


def test_a_library_copy_from_github_is_blind_and_runs_no_child(env, tmp_path, monkeypatch):
    """KD-12 (j), the same pool-independent rule: a GitHub file that IS a probed library file
    is a library copy. No child run (result_for is never called)."""
    gh = _gh(tmp_path)
    monkeypatch.setattr(chaos, "probe_for", lambda body: "etl_checksum")
    monkeypatch.setattr(chaos, "result_for", lambda *a: (_ for _ in ()).throw(AssertionError("child ran")))
    res = learn.run("Lib/urllib/request.py", None, github=gh.spec, gh_opts=gh.opts)
    assert res.code == learn.EXIT_OK and res.outcome.startswith("0 rows")
    assert all(f["engines"]["chaos"].startswith("BLIND(library copy") for f in res.report["functions"])


def _blind(env, tmp_path, path="Lib/urllib/request.py", spec=None, opts=None, **gh_kw):
    gh = _gh(tmp_path, **gh_kw)
    before = _brain_bytes(env)
    o = dict(gh.opts, **(opts(gh) if opts else {}))
    res = learn.run(path, None, github=gh.spec if spec is None else spec, gh_opts=o, engines=engines_with(metamorphic="FAIL"))
    assert res.code == learn.EXIT_BLIND, res.outcome
    assert _brain_bytes(env) == before and not (env.scout / learn.REPORT_NAME).exists()
    return res.outcome



def test_a_file_marked_psf_2_0_is_admitted_and_an_unknown_id_is_not(env, tmp_path):
    """KD-13 (d) (decided 2026-11-15): PSF-2.0 is on the closed list; an id not on it stays BLIND."""
    gh = _gh(tmp_path, files={"LICENSE": LICENSE, "Lib/p.py": "# SPDX-License-Identifier: PSF-2.0\n" + REQUEST})
    assert ghpool.admit(gh.spec, "Lib/p.py", **gh.opts).path == "Lib/p.py"
    (tmp_path / "gh").rename(tmp_path / "gh-1")
    gh = _gh(tmp_path, files={"LICENSE": LICENSE, "Lib/q.py": "# SPDX-License-Identifier: PSF-3.0\n" + REQUEST})
    with pytest.raises(ghpool.NotAdmitted, match="licence-file: SPDX 'PSF-3.0' is not on the allowlist"):
        ghpool.admit(gh.spec, "Lib/q.py", **gh.opts)


def test_crew_seat_alone_is_unsigned(env, tmp_path):
    assert _blind(env, tmp_path, signers=("fives",)).startswith("BLIND(unsigned:")


def test_a_pin_without_a_line_is_unsigned(env, tmp_path):
    assert _blind(env, tmp_path, spec="python/cpython@" + "d" * 40).startswith("BLIND(unsigned:")


@pytest.mark.parametrize("spec", ["python/cpython", "python/cpython@abc123", "../x/y@" + "a" * 40, "",
                                  "python/cpython@" + "a" * 40 + "\n"])
def test_a_malformed_spec_is_unsigned(env, tmp_path, spec):
    assert _blind(env, tmp_path, spec=spec).startswith("BLIND(unsigned:")


def test_a_licence_off_the_allowlist_is_blind(env, tmp_path):
    assert _blind(env, tmp_path, spdx="GPL-3.0-only").startswith("BLIND(licence:")


def test_a_signed_licence_blob_that_is_not_the_licence_file_is_blind(env, tmp_path):
    assert _blind(env, tmp_path, licence_blob="e" * 40).startswith("BLIND(licence: the top-level licence file(s)")


# Step-7 crew review, 5S S6: the folder names match in any case (Docs/, TEST/ are not in the set as written).
@pytest.mark.parametrize("path", ["Lib/Docs/x.py", "Lib/TEST/x.py", "Lib/Examples/x.py"])
def test_a_source_exclusion_folder_matches_in_any_case(path):
    assert ghpool.included("Lib/x.py", b"x = 1\n") == ""          # control
    assert ghpool.included(path, b"x = 1\n").startswith("not-included: under ")


def test_a_test_file_is_not_included(env, tmp_path):
    files = {"LICENSE": LICENSE, "Lib/test/test_urllib.py": REQUEST}
    assert _blind(env, tmp_path, path="Lib/test/test_urllib.py", files=files).startswith("BLIND(not-included:")


def test_a_generated_file_is_not_included(env, tmp_path):
    files = {"LICENSE": LICENSE, "Lib/gen.py": "# Generated by Tools/build. DO NOT EDIT.\n" + REQUEST}
    assert "generated-code marker" in _blind(env, tmp_path, path="Lib/gen.py", files=files)


def test_a_file_with_its_own_copyleft_header_is_blind(env, tmp_path):
    files = {"LICENSE": LICENSE, "Lib/x.py": "# SPDX-License-Identifier: GPL-2.0-or-later\n" + REQUEST}
    assert _blind(env, tmp_path, path="Lib/x.py", files=files).startswith("BLIND(licence-file:")
    files = {"LICENSE": LICENSE, "Lib/y.py": "# This file is under the GNU General Public License v3\n" + REQUEST}
    (tmp_path / "gh").rename(tmp_path / "gh-1")
    assert _blind(env, tmp_path, path="Lib/y.py", files=files).startswith("BLIND(licence-file:")


def test_a_tampered_cached_file_is_pin_mismatch(env, tmp_path):
    gh = _gh(tmp_path)
    f = gh.cache / "python" / f"cpython@{gh.sha}" / "Lib" / "urllib" / "request.py"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text(REQUEST + "import os\n")
    res = learn.run("Lib/urllib/request.py", None, github=gh.spec, gh_opts=gh.opts)
    assert res.code == learn.EXIT_BLIND and res.outcome.startswith("BLIND(pin-mismatch:"), res.outcome


def test_author_is_refused_for_github(env, tmp_path):
    gh = _gh(tmp_path)
    res = learn.run("Lib/urllib/request.py", "4Q", github=gh.spec, gh_opts=gh.opts)
    assert res.code == learn.EXIT_CONFIG and "no single author" in res.outcome


def test_cli_corpus_path_still_needs_author(env, tmp_path, capsys):
    """Review F9: as before step 7, a missing --author is a usage error (exit 2), checked first."""
    f = tmp_path / "x.py"
    f.write_text(REQUEST)
    for target in (f, tmp_path / "missing.py"):
        assert cli.main(["learn", str(target)]) == 2
        assert "the following arguments are required: --author" in capsys.readouterr().err


def test_cli_github_without_a_ratified_line_is_blind(env, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ghpool, "LIST_FILE", tmp_path / "none.jsonl")
    monkeypatch.setattr(ghpool, "SIGNERS_FILE", tmp_path / "none_signers")
    code = cli.main(["learn", "--github", "python/cpython@" + "a" * 40, "Lib/urllib/request.py"])
    assert code == learn.EXIT_BLIND and "BLIND(unsigned:" in capsys.readouterr().err


@pytest.mark.parametrize("expr,ok", [
    ("Python-2.0", True), ("MIT", True), ("Apache-2.0 OR MIT", True), ("GPL-3.0-only OR MIT", True),
    ("MIT AND BSD-3-Clause", True), ("MIT AND GPL-2.0-only", False), ("(MIT OR GPL-2.0) AND ISC", True),
    ("GPL-3.0-only", False), ("PSF-2.0", True), ("PSF-2.0 AND MIT", True), ("PSF-3.0", False),
    ("psf-2.0", False), ("NOASSERTION", False), ("MIT+", False),
    ("Apache-2.0 WITH LLVM-exception", False), ("MIT OR", False), ("(MIT", False), ("", False),
    ("mit", False), ("MIT;rm", False)])
def test_spdx_expressions(expr, ok):
    assert ghpool.spdx_allowed(expr) is ok


def test_trust_score_never_counts_unknown_as_met():
    assert ghpool.trust_score({}) == {"met": 0, "known": 0, "of": 5, "unknown": list(ghpool.THRESHOLDS)}
    s = ghpool.trust_score({"last_commit_age_days": {"value": 400}, "dependents": {"value": True}})
    assert s["met"] == 0 and s["known"] == 1 and "dependents" in s["unknown"]


def test_every_present_licence_file_must_be_the_signed_blob(env, tmp_path):
    """Review F5: a signed LICENSE beside an unsigned COPYING (say GPL) is not admitted."""
    files = {"LICENSE": LICENSE, "COPYING": "GNU GENERAL PUBLIC LICENSE Version 3\n", "Lib/urllib/request.py": REQUEST}
    assert _blind(env, tmp_path, files=files).startswith("BLIND(licence: the top-level licence file(s)")


def test_the_trust_pin_must_be_set_and_name_the_principals_key(env, tmp_path):
    """Review open question: the required signer's key id is pinned outside the repo."""
    assert "FORGEPROOF_RATIFY_PIN must be set" in _blind(env, tmp_path, opts=lambda gh: {"trust_pin_value": ""})
    (tmp_path / "gh").rename(tmp_path / "gh-1")
    assert "the host pins 0000000000000000" in _blind(
        env, tmp_path, opts=lambda gh: {"trust_pin_value": P + "=" + "0" * 16})
    (tmp_path / "gh").rename(tmp_path / "gh-2")
    assert "FORGEPROOF_RATIFY_PIN must be set" in _blind(
        env, tmp_path, opts=lambda gh: {"trust_pin_value": f"fives={gh.fives_id}"})


def test_the_trust_pin_is_read_from_the_host_config(env, tmp_path, monkeypatch):
    gh = _gh(tmp_path)
    opts = {k: v for k, v in gh.opts.items() if k != "trust_pin_value"}
    monkeypatch.delenv(ghpool.TRUST_PIN_KEY, raising=False)
    from forgeproof import config
    if config.configured(ghpool.TRUST_PIN_KEY) is None:
        with pytest.raises(ghpool.NotAdmitted, match="must be set"):
            ghpool.admit(gh.spec, "Lib/urllib/request.py", **opts)
    monkeypatch.setenv(ghpool.TRUST_PIN_KEY, gh.pin)
    assert ghpool.admit(gh.spec, "Lib/urllib/request.py", **opts).path == "Lib/urllib/request.py"


@pytest.mark.parametrize("text,blind", [
    ("#!/usr/bin/env python\n# SPDX-License-Identifier: BUSL-1.1\nimport os\n", True),
    ("/* SPDX-License-Identifier: EPL-2.0 */\nint x;\n", True),
    ("# SPDX-License-Identifier: LicenseRef-Proprietary\nx = 1\n", True),
    ("# SPDX-License-Identifier: AGPL-3.0-only\n\ndef f():\n    pass\n", True),
    ("# Licensed under the Eclipse Public License 2.0\n# SPDX-License-Identifier: MIT", True),
    ("# SPDX-License-Identifier: MIT\n# also: Business Source License\nx = 1\n", True),
    ("# SPDX-License-Identifier: Zlib\nimport os\n", True),     # an id the header list does not name (mutant P1)
    ("# SPDX-License-Identifier: MIT\nimport os\n", False),
    ("# SPDX-License-Identifier: PSF-2.0\nx = 1\n", False),     # KD-13 (d), decided 2026-11-15
    ("# SPDX-License-Identifier: PSF-3.0\nx = 1\n", True),      # an unknown id stays out
    ("# Copyright 2001 Python Software Foundation. All Rights Reserved.\nx = 1\n", False),
    ("x = 1\n", False)])
def test_file_licence_reads_every_spdx_line_and_always_scans_the_header(text, blind):
    """Review F1: SPDX lines anywhere in the first 40 lines, header scan even with an SPDX line."""
    assert bool(ghpool.file_licence(text)) is blind


def test_cli_github_success_prints_the_github_pool_line(env, tmp_path, capsys, monkeypatch):
    """Review F3: the human render of a GitHub report (no --json) names repo, pin and signers."""
    gh = _gh(tmp_path)
    monkeypatch.setattr(ghpool, "LIST_FILE", gh.list)
    monkeypatch.setattr(ghpool, "SIGNERS_FILE", gh.signers)
    monkeypatch.setattr(ghcache, "cache_root", lambda: gh.cache)
    monkeypatch.setenv(ghpool.TRUST_PIN_KEY, gh.pin)
    code = cli.main(["learn", "--github", gh.spec, "Lib/urllib/request.py"])
    out = capsys.readouterr().out
    assert code == learn.EXIT_OK, out
    assert (f"pool     github python/cpython@{gh.sha[:12]} Lib/urllib/request.py (python, Python-2.0, "
            f"ratified by {', '.join(gh.key_ids)}") in out


@pytest.mark.parametrize("suffix", ["\n", "x", "/extra", " "])
def test_the_real_pin_with_anything_after_it_is_refused(env, tmp_path, suffix):
    """Mutant P8: the spec must be exactly org/repo@sha; a prefix match of a ratified pin is not enough."""
    gh = _gh(tmp_path)
    with pytest.raises(ghpool.NotAdmitted, match="unsigned: --github must be"):
        ghpool.admit(gh.spec + suffix, "Lib/urllib/request.py", **gh.opts)
