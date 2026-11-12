"""Lens 1, shape (design v1.4.5 §4a.3): "related" means the code looks alike.

Score = exact Jaccard of 5-gram shape shingles (``shape.py``, ported from the corpus).
FOUND when at least one pool member scores >= TAU, LOOKED-CLEAN when the lens ran and none
did, BLIND(reason) when it could not look. Its own ranked list with its own reason; no
combined score (§4a.4 rule 1). Bodies are compared as text only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import shape
from .outcomes import Outcome, blind, clean, found

NAME = "shape"
# The corpus's near-copy threshold (aura-code-corpus config.toml [cluster] tau = 0.5,
# calibrated there by `code-corpus calibrate`). The score band for trust tie-breaks is
# not set yet: [NEED: band width per lens, measured when the lens is calibrated] (§4a.4).
TAU = 0.5
TOP_K = 5
MIN_TOKENS = 5  # fewer normalized tokens than one shingle: nothing to compare


@dataclass
class Hit:
    item_id: str
    score: float
    pool: str
    trust: str
    lang: str = ""
    kind: str = ""


@dataclass
class LensResult:
    outcome: Outcome
    hits: list = field(default_factory=list)   # ranked, best first, top TOP_K
    looked_at: int = 0


def score(a: str, b: str) -> float:
    return shape.jaccard(shape.shingles(a), shape.shingles(b))


def keyword_score(a: str, b: str) -> float:
    """The keyword baseline the lens must beat (Rule 2): Jaccard of the raw word sets."""
    wa = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", a.lower()))
    wb = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", b.lower()))
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def rank(query: str, members: list, *, pool: str, trust: str, scorer=score,
         tau: float = TAU) -> LensResult:
    """members: dicts with item_id, body (and optionally lang, kind)."""
    if len(shape.normalize(query)) < MIN_TOKENS:
        return LensResult(blind("no-input: query has fewer tokens than one shingle"))
    if not members:
        return LensResult(blind(f"no-input: {pool} pool has no member in this language"))
    q = shape.shingles(query) if scorer is score else None
    hits = []
    for m in members:
        s = shape.jaccard(q, shape.shingles(m["body"])) if q is not None else scorer(query, m["body"])
        hits.append(Hit(m["item_id"], round(s, 4), pool, trust, m.get("lang", ""), m.get("kind", "")))
    hits.sort(key=lambda h: (-h.score, h.item_id))
    top = hits[:TOP_K]
    over = [h for h in top if h.score >= tau]
    if over:
        return LensResult(found(f"{len(over)} at or above J={tau}"), top, len(members))
    return LensResult(clean(f"best J={top[0].score} < {tau}"), top, len(members))
