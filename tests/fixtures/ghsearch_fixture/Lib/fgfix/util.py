"""Fixture: import-time side effect. If scout ever ran this file, the canary would appear."""
import os

open(os.environ.get("FG_GHSEARCH_CANARY", os.devnull), "w").write("ran")


def clamp_all(xs, lo, hi):
    result = []
    for x in xs:
        if x < lo:
            result.append(lo)
        elif x > hi:
            result.append(hi)
        else:
            result.append(x)
    return result
