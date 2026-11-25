"""Step 7 slice 2: the signed list (design §12.5 D7-2a; DECISIONS-7-github G2).
Every key here is a throwaway test key made in tmp_path. No real key is read or used."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from forgeproof.scout import ratify

P = ratify.REQUIRED_PRINCIPAL      # the published copy renames the principal; the tests follow it
MAINTAINER_KEY_ID = "dad6972af38242fc"   # who P must be: pinned by key, which no rename touches

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

SHA = "a" * 40
BLOB = "b" * 40


def _key(d: Path, name: str) -> tuple[Path, str]:
    k = d / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)], check=True)
    return k, (d / f"{name}.pub").read_text().split()[1]


def _sign(key: Path, data: bytes, ns: str = ratify.NAMESPACE) -> str:
    f = key.parent / f"data-{key.name}"
    f.write_bytes(data)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", ns, str(f)], check=True,
                   capture_output=True)
    return Path(str(f) + ".sig").read_text()


@pytest.fixture
def world(tmp_path):
    keys = {p: _key(tmp_path, p) for p in (P, "fives", "mallory")}
    signers = tmp_path / "allowed_signers"
    signers.write_text("".join(f'{p} namespaces="forge-gate-ratify" ssh-ed25519 {keys[p][1]} {p}\n'
                               for p in (P, "fives")))
    return tmp_path, keys, signers


def _fields(**over):
    f = {"org": "python", "repo": "cpython", "commit": SHA, "licence_spdx": "Python-2.0",
         "licence_blob": BLOB, "host_set": "github-git", "trust_signals": {"first_commit": "1990"},
         "candidate_ref": "G-repo-selection-REPORT.md", "ratified_at": "2026-11-14"}
    f.update(over)
    return f


def _line(keys, fields, who=(P, "fives"), tamper=None):
    data = ratify.canonical(fields)
    sigs = [{"signer": w, "sig": _sign(keys[w if w in keys else "mallory"][0], data)} for w in who]
    out = dict(fields, signatures=sigs)
    if tamper:
        out.update(tamper)
    return json.dumps(out)


def _verify(world, text, host_sets=None):
    d, _, signers = world
    lst = d / "trusted_repos.jsonl"
    lst.write_text(text + "\n")
    return ratify.ratified(lst, signers, "python", "cpython", SHA, host_sets)


def test_a_line_signed_by_the_principal_and_a_crew_seat_is_ratified(world):
    v = _verify(world, _line(world[1], _fields()))
    assert v.ok, v.reason
    assert sorted(v.principals) == sorted(["fives", P])
    assert sorted(v.key_ids) == sorted(ratify.key_id(world[1][p][1]) for p in (P, "fives"))


def test_the_principal_alone_is_not_enough(world):
    v = _verify(world, _line(world[1], _fields(), who=(P,)))
    assert not v.ok and "distinct signer" in v.reason


def test_a_crew_seat_alone_is_not_enough(world):
    v = _verify(world, _line(world[1], _fields(), who=("fives",)))
    assert not v.ok and v.reason == f"not signed by {P}"


def test_any_changed_field_breaks_every_signature(world):
    v = _verify(world, _line(world[1], _fields(), tamper={"commit": "c" * 40}))
    assert not v.ok  # the pin we asked for is no longer on any line
    d, keys, signers = world
    (d / "l.jsonl").write_text(_line(keys, _fields(), tamper={"licence_spdx": "MIT"}) + "\n")
    (vv,) = ratify.verify_list(d / "l.jsonl", signers)
    assert not vv.ok and f"bad signature by {P}" in vv.reason and "bad signature by fives" in vv.reason


# Step-7 crew review, 5S S3/S7: the old test above changes 2 of the 9 fields. Every field, changed
# alone, must break BOTH signatures; a field left out of canonical() would sign as unchanged.
_OTHER = {"org": "python2", "repo": "cpython2", "commit": "c" * 40, "licence_spdx": "MIT",
          "licence_blob": "d" * 40, "host_set": "pypi", "trust_signals": {"first_commit": "1991"},
          "candidate_ref": "other.md", "ratified_at": "2026-11-15"}


@pytest.mark.parametrize("field", ratify.FIELDS)
def test_every_signed_field_alone_breaks_every_signature(world, field):
    d, keys, signers = world
    assert set(_OTHER) == set(ratify.FIELDS) and _OTHER[field] != _fields()[field]
    (d / "ok.jsonl").write_text(_line(keys, _fields()) + "\n")
    (control,) = ratify.verify_list(d / "ok.jsonl", signers)
    assert control.ok, control.reason
    (d / "l.jsonl").write_text(_line(keys, _fields(), tamper={field: _OTHER[field]}) + "\n")
    (v,) = ratify.verify_list(d / "l.jsonl", signers)
    assert not v.ok and f"bad signature by {P}" in v.reason and "bad signature by fives" in v.reason


# Step-7 crew review, 5S S1: ssh-keygen that cannot run or never answers is a bad signature, not a pass.
@pytest.mark.parametrize("boom", [OSError("exec failed"), subprocess.TimeoutExpired(["ssh-keygen"], 30)],
                         ids=["oserror", "timeout"])
def test_a_crashing_or_hung_ssh_keygen_is_a_bad_signature(world, monkeypatch, boom):
    text = _line(world[1], _fields())
    assert _verify(world, text).ok          # control: the same line verifies with a working ssh-keygen

    def run(*a, **k):
        raise boom
    monkeypatch.setattr(ratify.subprocess, "run", run)
    v = _verify(world, text)
    assert not v.ok and f"bad signature by {P}" in v.reason


def test_an_unknown_signer_poisons_the_line(world):
    v = _verify(world, _line(world[1], _fields(), who=(P, "fives", "mallory")))
    assert not v.ok and "unknown signer 'mallory'" in v.reason


def test_a_key_not_in_the_signer_file_cannot_sign_as_a_listed_principal(world):
    d, keys, signers = world
    fields = _fields()
    data = ratify.canonical(fields)
    forged = [{"signer": P, "sig": _sign(keys[P][0], data)},
              {"signer": "fives", "sig": _sign(keys["mallory"][0], data)}]   # mallory signs as fives
    v = _verify(world, json.dumps(dict(fields, signatures=forged)))
    assert not v.ok and "bad signature by fives" in v.reason


def test_the_same_principal_twice_counts_once(world):
    v = _verify(world, _line(world[1], _fields(), who=(P, P)))
    assert not v.ok and f"{P} signed twice" in v.reason


def test_another_namespace_does_not_verify(world):
    d, keys, signers = world
    fields = _fields()
    data = ratify.canonical(fields)
    sigs = [{"signer": w, "sig": _sign(keys[w][0], data, ns="file")} for w in (P, "fives")]
    v = _verify(world, json.dumps(dict(fields, signatures=sigs)))
    assert not v.ok and "bad signature" in v.reason


@pytest.mark.parametrize("over", [{"commit": "abc123"}, {"commit": "A" * 40}, {"licence_blob": ""},
                                  {"commit": SHA + "\n"}, {"licence_blob": BLOB + "\n"}, {"org": "python\n"},
                                  {"org": "../etc"}, {"repo": "a/b"}, {"licence_spdx": " "},
                                  {"trust_signals": []}, {"host_set": ""}])
def test_malformed_fields_are_blind_even_when_signed(world, over):
    d, keys, signers = world
    (d / "l.jsonl").write_text(_line(keys, _fields(**over)) + "\n")
    (v,) = ratify.verify_list(d / "l.jsonl", signers)
    assert not v.ok and v.reason and "signature" not in v.reason


def test_an_extra_or_missing_field_is_blind(world):
    d, keys, signers = world
    f = _fields()
    f["note"] = "x"
    (d / "l.jsonl").write_text(_line(keys, f) + "\n")
    assert "unexpected ['note']" in ratify.verify_list(d / "l.jsonl", signers)[0].reason
    g = _fields()
    del g["licence_blob"]
    (d / "l.jsonl").write_text(_line(keys, g) + "\n")
    assert "missing ['licence_blob']" in ratify.verify_list(d / "l.jsonl", signers)[0].reason


def test_an_unknown_host_set_is_blind(world):
    v = _verify(world, _line(world[1], _fields(host_set="everything")), host_sets={"github-git": 1})
    assert not v.ok and "not a named host set" in v.reason


def test_no_line_for_the_pin_is_blind(world):
    d, keys, signers = world
    v = ratify.ratified(d / "missing.jsonl", signers, "python", "cpython", SHA)
    assert not v.ok and v.reason.startswith("no line for")


@pytest.mark.parametrize("bad", [
    '{p} ssh-ed25519 {k}',                                                   # no namespace option
    '{p} namespaces="forge-gate-ratify",cert-authority ssh-ed25519 {k}',     # extra option
    '{p} namespaces="*" ssh-ed25519 {k}',                                    # any namespace
    '{p} namespaces="forge-gate-ratify" ssh-rsa {k}',                        # other key type
    '{pc} namespaces="forge-gate-ratify" ssh-ed25519 {k}',                    # principal case
    '{p} namespaces="forge-gate-ratify" ssh-ed25519 {k}\n{p} namespaces="forge-gate-ratify" ssh-ed25519 {k2}',
    '{p} namespaces="forge-gate-ratify" ssh-ed25519 {k}\nfives namespaces="forge-gate-ratify" ssh-ed25519 {k}',
])
def test_the_signer_file_is_strict(world, bad):
    d, keys, _ = world
    f = d / "bad_signers"
    f.write_text(bad.format(k=keys[P][1], k2=keys["fives"][1], p=P, pc=P.capitalize()) + "\n")
    with pytest.raises(ratify.BadSigners):
        ratify.load_signers(f)
    lst = d / "l.jsonl"
    lst.write_text(_line(keys, _fields()) + "\n")
    v = ratify.ratified(lst, f, "python", "cpython", SHA)
    assert not v.ok and v.reason.startswith("signers unusable")


def test_an_rsa_blob_under_an_ed25519_label_is_refused(world, tmp_path):
    k = tmp_path / "rsa"
    subprocess.run(["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(k)], check=True)
    blob = (tmp_path / "rsa.pub").read_text().split()[1]
    f = tmp_path / "s"
    f.write_text(f'{P} namespaces="forge-gate-ratify" ssh-ed25519 {blob}\n')
    with pytest.raises(ratify.BadSigners, match="not exactly one ssh-ed25519"):
        ratify.load_signers(f)


def test_canonical_bytes_ignore_signatures_and_key_order():
    a = ratify.canonical({"b": 1, "a": 2, "signatures": [1]})
    assert a == ratify.canonical({"a": 2, "b": 1}) == b'{"a":2,"b":1}'


def test_the_tracked_signer_file_if_present_is_well_formed():
    """The repo's own scout/allowed_signers (added when the real keys exist) must load, and must
    name the required principal WITH THE MAINTAINER'S KEY (pinned by key id: the published copy renames
    the principal, never the key). Absent until slice 5: skipped then, never green-by-default."""
    f = Path(__file__).resolve().parent.parent / "scout" / "allowed_signers"
    if not f.exists():
        pytest.skip("scout/allowed_signers not added yet (slice 5, after the ratify key)")
    s = ratify.load_signers(f)
    assert ratify.REQUIRED_PRINCIPAL in s and len(s) >= ratify.MIN_SIGNERS
    assert s[ratify.REQUIRED_PRINCIPAL].key_id == MAINTAINER_KEY_ID      # review F1: a crew seat can't be it


def test_an_ed25519_blob_with_trailing_bytes_is_refused(world, tmp_path):
    """Review F7: the blob must be exactly one 51-byte ssh-ed25519 key, decoded."""
    import base64
    _, keys, _ = world
    longer = base64.b64encode(base64.b64decode(keys[P][1]) + b"\x00\x00\x00").decode()
    f = tmp_path / "s7"
    f.write_text(f'{P} namespaces="forge-gate-ratify" ssh-ed25519 {longer}\n')
    with pytest.raises(ratify.BadSigners, match="not exactly one ssh-ed25519"):
        ratify.load_signers(f)
