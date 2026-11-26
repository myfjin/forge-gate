"""The one-in-ten explore slot (design §4a.5; build step 8b s4; DECISIONS-8b H10 a).

Every similarity list shows TOP ranked hits plus, in slot 10, one hit drawn from OUTSIDE the top
ranks, labelled ``explore``: a counterfactual, so the outcome ledger does not only ever learn about
what the lens already liked. The draw is seeded by the query text and the lens name, so it can be
reproduced. An explore hit never counts toward FOUND or agreement, and experience never reorders it.
A list with nothing beyond the top ranks has no explore slot.
"""
from __future__ import annotations

import hashlib
import random

TOP = 9          # shown top ranks; slot 10 is the explore slot


def split(ranked: list, *, lens: str, query: str) -> tuple:
    """(top hits, explore hit or None) from a fully ranked list (best first). Marks the slots."""
    top, rest = list(ranked[:TOP]), list(ranked[TOP:])
    for h in top:
        _mark(h, "top")
    return top, pick(rest, lens=lens, query=query)


def pick(rest: list, *, lens: str, query: str):
    """The explore hit: one of ``rest`` (hits outside the top ranks), seeded, marked; None if empty."""
    if not rest:
        return None
    seed = hashlib.sha256(f"{lens}\0{query}".encode("utf-8", "surrogatepass")).digest()
    h = rest[random.Random(int.from_bytes(seed[:8], "big")).randrange(len(rest))]
    _mark(h, "explore")
    return h


def _mark(h, slot):
    if isinstance(h, dict):
        h["slot"] = slot
    else:
        h.slot = slot
