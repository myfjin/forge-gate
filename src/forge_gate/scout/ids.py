"""Scout's namespaced ids (design v1.4.5 §4.4.1 item 3, build step 3a).

A scout id names exactly one function at one commit of one source:

    scout:<pool>:<source>@<commit>:<path>::<function>

Every component is percent-encoded with nothing left safe, so ``/``, ``:`` and ``@``
never appear inside a component. Two consequences, both tested:

1. The id has no ``/`` at all. The Brain's honest_pass joins validator cards to rows
   by ``stem()`` = basename minus the last extension (honest_pass.py:21-23). On a raw
   ``…/Lib/urllib/request.py:urlopen`` that yields ``request`` and the card would back
   any library row called ``request`` (F41's new hazard). On a scout id, ``stem()``
   can only trim text after the last dot; the ``scout:`` prefix always survives, so a
   scout id never reduces to a bare stem, even through code that ignores D7-5.
2. Ids are compared verbatim (D7-5 item 2). ``parse_id`` is exact: it rejects anything
   ``make_id`` would not produce, so two ids that differ by one character never match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

PREFIX = "scout"
POOLS = frozenset({"corpus", "github"})  # §4a: the corpus lane and the GitHub lane (step 7)
# A full git object id (40 hex, or 64 in a SHA-256 repo) or a corpus export stamp. Never an
# abbreviated hash: one commit must have exactly one id, or a card and its row stop joining
# (6E review B3a-1).
_COMMIT = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$|^[0-9]{8}T[0-9]{6}Z$")
_ID = re.compile(r"^scout:([^:/@]+):([^:/@]+)@([^:/@]+):([^:/@]+)::([^:/@]+)$")


class BadScoutId(ValueError):
    """An id or id component scout must not emit or accept."""


@dataclass(frozen=True)
class ScoutId:
    pool: str      # "corpus" | "github"
    source: str    # corpus id, or GitHub "owner/repo"
    commit: str    # git commit hash, or corpus export stamp (e.g. 20261111T130229Z)
    path: str      # file path inside the source, "/"-separated, relative
    function: str  # qualified function name, e.g. "Request.get_method"

    def __str__(self) -> str:
        return make_id(self.pool, self.source, self.commit, self.path, self.function)


def _enc(part: str, what: str) -> str:
    if not isinstance(part, str) or not part or part != part.strip():
        raise BadScoutId(f"{what} must be a non-empty string without outer spaces: {part!r}")
    return quote(part, safe="")


def make_id(pool: str, source: str, commit: str, path: str, function: str) -> str:
    if pool not in POOLS:
        raise BadScoutId(f"pool {pool!r} not in {sorted(POOLS)}")
    if not _COMMIT.match(commit or ""):
        raise BadScoutId(f"commit {commit!r} is neither a git hash nor an export stamp")
    if path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
        raise BadScoutId(f"path {path!r} must be relative, '/'-separated, without '.' or '..' parts")
    return (f"{PREFIX}:{_enc(pool, 'pool')}:{_enc(source, 'source')}@{_enc(commit, 'commit')}"
            f":{_enc(path, 'path')}::{_enc(function, 'function')}")


def parse_id(text: str) -> ScoutId:
    m = _ID.match(text) if isinstance(text, str) else None
    if not m:
        raise BadScoutId(f"not a scout id: {text!r}")
    sid = ScoutId(*(unquote(g) for g in m.groups()))
    if make_id(sid.pool, sid.source, sid.commit, sid.path, sid.function) != text:
        raise BadScoutId(f"not in canonical form: {text!r}")  # e.g. '%2f' vs '%2F'
    return sid


def is_scout_id(text: str) -> bool:
    try:
        parse_id(text)
        return True
    except BadScoutId:
        return False
