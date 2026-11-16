"""The signed list of ratified GitHub repos (design v1.4.5 §12.5 D7-2a; build step 7 slice 2).

A ratified repo is one append-only JSON line of ``scout/trusted_repos.jsonl``, carrying its
pinned commit. Each line must be signed by the maintainer and by at least one crew seat
(DECISIONS-7-github G2, the maintainer 2026-11-14). Before any fetch, scout verifies every line against
the keys named in ``scout/allowed_signers`` and logs the verified key ids. An unsigned, unknown
or malformed line is BLIND(unsigned): never fetched, never read, never run.

Signatures: ``ssh-keygen -Y sign`` with Ed25519 keys, namespace ``forge-gate-ratify``. There is
no new Python dependency, and both hosts have OpenSSH >= 8.9.

What is signed: the line's CANONICAL bytes, the JSON of every field except ``signatures``
(sorted keys, no whitespace, ASCII). Any change to any field breaks every signature.

``allowed_signers`` is stricter than OpenSSH's own format. Every line must be exactly
``<principal> namespaces="forge-gate-ratify" ssh-ed25519 <base64> [comment]``. No other key
type, no other option (no cert-authority, no validity window), one key per principal, and one
principal per key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

NAMESPACE = "forge-gate-ratify"
REQUIRED_PRINCIPAL = "operator"     # G2: one of the signatures must be the maintainer's
MIN_SIGNERS = 2                  # D7-2a: the maintainer + at least one crew seat
FIELDS = ("org", "repo", "commit", "licence_spdx", "licence_blob", "host_set", "trust_signals",
          "candidate_ref", "ratified_at")
_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PRINCIPAL = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SIGNER_LINE = re.compile(r'^([a-z][a-z0-9-]{0,31}) namespaces="forge-gate-ratify" ssh-ed25519 ([A-Za-z0-9+/]+={0,2})(?: .*)?$')


class BadSigners(ValueError):
    """allowed_signers is malformed or breaks the one-key-per-principal rule."""


@dataclass(frozen=True)
class Signer:
    principal: str
    key_b64: str
    key_id: str      # sha256 of the key blob, first 16 hex: the id written into rows (ratified_by)


@dataclass
class Verdict:
    line_no: int
    ok: bool
    reason: str = ""                       # why it is BLIND(unsigned), when not ok
    fields: dict = field(default_factory=dict)
    key_ids: list = field(default_factory=list)
    principals: list = field(default_factory=list)

    @property
    def pin(self) -> str:
        return f"{self.fields.get('org')}/{self.fields.get('repo')}@{self.fields.get('commit')}"


def key_id(key_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:16]


def load_signers(path: Path) -> dict[str, Signer]:
    signers: dict[str, Signer] = {}
    seen_keys: set[str] = set()
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SIGNER_LINE.fullmatch(line)
        if not m:
            raise BadSigners(f"{path}:{n}: not '<principal> namespaces=\"{NAMESPACE}\" ssh-ed25519 <key>'")
        principal, key = m.groups()
        try:
            blob = base64.b64decode(key, validate=True)
        except ValueError as e:
            raise BadSigners(f"{path}:{n}: key is not base64: {e}") from None
        if not blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20") or len(blob) != 51:
            raise BadSigners(f"{path}:{n}: key blob is not exactly one ssh-ed25519 key (review F7)")
        if principal in signers:
            raise BadSigners(f"{path}:{n}: principal {principal!r} named twice (one key per principal)")
        if blob in seen_keys:      # decoded bytes, not the base64 text (review F7)
            raise BadSigners(f"{path}:{n}: the same key under two principals")
        seen_keys.add(blob)
        signers[principal] = Signer(principal, key, key_id(key))
    if not signers:
        raise BadSigners(f"{path}: no signers")
    return signers


def canonical(fields: dict) -> bytes:
    """The bytes every signature covers: all fields except 'signatures'."""
    body = {k: v for k, v in fields.items() if k != "signatures"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def check_fields(fields: dict, host_sets: dict | None = None) -> str:
    """'' when the line's fields are well formed, else the reason."""
    if not isinstance(fields, dict):
        return "not a JSON object"
    extra = set(fields) - set(FIELDS) - {"signatures"}
    missing = [k for k in FIELDS if k not in fields]
    if extra or missing:
        return f"fields: missing {sorted(missing)}, unexpected {sorted(extra)}"
    for k in ("org", "repo"):
        if not isinstance(fields[k], str) or not _NAME.fullmatch(fields[k]) or ".." in fields[k]:
            return f"{k} is not a plain GitHub name: {fields[k]!r}"
    for k in ("commit", "licence_blob"):
        if not isinstance(fields[k], str) or not _HEX40.fullmatch(fields[k]):
            return f"{k} must be a full 40-hex sha: {fields[k]!r}"
    if not isinstance(fields["licence_spdx"], str) or not fields["licence_spdx"].strip():
        return "licence_spdx is empty"
    if not isinstance(fields["trust_signals"], dict):
        return "trust_signals must be an object"
    for k in ("host_set", "candidate_ref", "ratified_at"):
        if not isinstance(fields[k], str) or not fields[k]:
            return f"{k} is empty"
    if host_sets is not None and fields["host_set"] not in host_sets:
        return f"host_set {fields['host_set']!r} is not a named host set"
    return ""


def _ssh_verify(signers_file: Path, principal: str, data: bytes, sig: str) -> bool:
    if shutil.which("ssh-keygen") is None:
        return False
    with tempfile.TemporaryDirectory() as d:
        sf = Path(d) / "line.sig"
        sf.write_text(sig)
        try:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers_file), "-I", principal,
                                "-n", NAMESPACE, "-s", str(sf)], input=data, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return r.returncode == 0


def verify_line(text: str, line_no: int, signers_file: Path, signers: dict[str, Signer],
                host_sets: dict | None = None) -> Verdict:
    try:
        fields = json.loads(text)
    except ValueError as e:
        return Verdict(line_no, False, f"not JSON: {e}")
    why = check_fields(fields, host_sets)
    if why:
        return Verdict(line_no, False, why, fields if isinstance(fields, dict) else {})
    sigs = fields.get("signatures")
    if not isinstance(sigs, list) or not sigs:
        return Verdict(line_no, False, "no signatures", fields)
    data = canonical(fields)
    principals, key_ids, bad = [], [], []
    for s in sigs:
        if not isinstance(s, dict) or set(s) != {"signer", "sig"} or not isinstance(s.get("sig"), str):
            bad.append("malformed signature entry")
            continue
        who = s["signer"]
        if not isinstance(who, str) or who not in signers:
            bad.append(f"unknown signer {who!r}")
            continue
        if who in principals:
            bad.append(f"{who} signed twice")
            continue
        if _ssh_verify(signers_file, who, data, s["sig"]):
            principals.append(who)
            key_ids.append(signers[who].key_id)
        else:
            bad.append(f"bad signature by {who}")
    if bad:   # any bad entry poisons the line: a signer list is not a vote
        return Verdict(line_no, False, "; ".join(bad), fields, key_ids, principals)
    if REQUIRED_PRINCIPAL not in principals:
        return Verdict(line_no, False, f"not signed by {REQUIRED_PRINCIPAL}", fields, key_ids, principals)
    if len(set(key_ids)) < MIN_SIGNERS:
        return Verdict(line_no, False, f"{len(set(key_ids))} distinct signer(s), need {MIN_SIGNERS}",
                       fields, key_ids, principals)
    return Verdict(line_no, True, "", fields, key_ids, principals)


def verify_list(list_file: Path, signers_file: Path, host_sets: dict | None = None) -> list[Verdict]:
    """Every line of the list, verified. Raises BadSigners when the signer file is unusable (no
    line can then be trusted). A missing list file is an empty list."""
    signers = load_signers(signers_file)
    if not Path(list_file).exists():
        return []
    out = []
    for n, raw in enumerate(Path(list_file).read_text().splitlines(), 1):
        if raw.strip():
            out.append(verify_line(raw, n, signers_file, signers, host_sets))
    return out


def ratified(list_file: Path, signers_file: Path, org: str, repo: str, commit: str,
             host_sets: dict | None = None) -> Verdict:
    """The verified line for exactly org/repo@commit. A Verdict that is not ok when there is
    none, or when the list or the signer file cannot be trusted (BLIND(unsigned))."""
    try:
        verdicts = verify_list(list_file, signers_file, host_sets)
    except (OSError, BadSigners) as e:
        return Verdict(0, False, f"signers unusable: {e}")
    want = (org, repo, commit)
    hits = [v for v in verdicts if (v.fields.get("org"), v.fields.get("repo"), v.fields.get("commit")) == want]
    good = [v for v in hits if v.ok]
    if good:
        return good[-1]
    if hits:
        return hits[-1]
    return Verdict(0, False, f"no line for {org}/{repo}@{commit}")
