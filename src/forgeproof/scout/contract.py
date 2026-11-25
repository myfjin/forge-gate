"""The contract gate (design §4a.3, lens 3): takes and returns the same kinds of things.

A gate filters, it does not vote. Behaviour calls the candidate with the query's REQUIRED
positional arguments, so the candidate must accept that many. Types are compared only where
BOTH sides are annotated; an unannotated side is unknown, and unknown passes (the behaviour run
then decides). Returns (fits, reason).
"""
from __future__ import annotations

import re

_ALIASES = {"List": "list", "Dict": "dict", "Tuple": "tuple", "Set": "set", "Sequence": "list",
            "Iterable": "list", "Optional": "", "typing.": ""}


def norm(t: str | None) -> str | None:
    if not t:
        return None
    s = t.replace(" ", "")
    for k, v in _ALIASES.items():
        s = s.replace(k, v)
    s = re.sub(r"\[.*\]", "", s)          # compare the outer kind only: list[float] ~ list
    return s.lower() or None


def fits(q: dict, c: dict) -> tuple[bool, str]:
    n = q["required"]
    if not (c["required"] <= n <= c["total"]):
        return False, f"arity: query passes {n}, {c['entry']} takes {c['required']}-{c['total']}"
    for i in range(n):
        tq, tc = norm(q["consumes"][i]["type"]), norm(c["consumes"][i]["type"])
        if tq and tc and tq != tc:
            return False, f"type: arg {i} {tq} vs {tc}"
    rq, rc = norm(q["produces"]["type"]), norm(c["produces"]["type"])
    if rq and rc and rq != rc:
        return False, f"type: returns {rq} vs {rc}"
    return True, "fits"
