"""Lens 6, experience (design §4a.5; build step 8b s5; DECISIONS-8b H8, H9, H11).

Experience is EVIDENCE, not similarity: "shown beside a result and never counted as a vote". For every
shown hit it reads the three outcome counts from the ledgers (8a's reader: oracle, person outcome with
a result id, person outcome without one) and pending marks, side by side, never blended. It may
change ORDER only inside one (score, trust) group (H9: the band is the exact score until a band width
is measured), lexicographically: oracle, then with-id, then without-id. It never changes a lens's
outcome, the k-of-n line, or which hits are FOUND.

Rule 2 for experience (H11, a named deviation: §4a.5's cases, not the similarity cases) runs on a
synthetic, in-memory ledger view on every invocation. It passes only if the real ordering passes
every case AND each of three selection-rewarding variants (counts exposures; counts pending marks;
blends the counts into one sum) FAILS: "its Rule-2 cases go red when it rewards selection" (§4a.5).
"""
from __future__ import annotations

from .. import ledger

NAME = "experience"
# §4a.4 Rule 3: library > registry > corpus / GitHub. Lower sorts first.
TRUST_RANK = {"signed": 0, "tier": 1, "raw": 2}


def _real(c: "ledger.Counts", n_exposed: int) -> tuple:
    return (-c.oracle, -c.person_with_id, -c.person_without_id)


def _rewards_exposure(c, n_exposed):          # the popularity baseline: being shown scores
    return (-n_exposed,)


def _rewards_pending(c, n_exposed):           # a mark without a passing event scores
    return (-c.oracle, -c.person_with_id, -(c.person_without_id + c.pending))


def _blended(c, n_exposed):                   # one number: any outcome is as good as any other
    return (-(c.oracle + c.person_with_id + c.person_without_id),)


VARIANTS = {"rewards-exposure": _rewards_exposure, "rewards-pending": _rewards_pending, "blended": _blended}


def order(hits: list, key, counts_of, exposed_of) -> list:
    """Stable order: score first, then trust, then experience, then the lens's own tie order (item)."""
    def k(h):
        item = f"{h['pool']}:{h['item_id']}"
        return (-h["score"], TRUST_RANK.get(h["trust"], 9), key(counts_of(item), exposed_of(item)), h["item_id"])
    return sorted(hits, key=k)


# ---------------------------------------------------------------- Rule 2 on a synthetic view

def _hit(i, score=0.9, trust="raw"):
    return {"item_id": i, "score": score, "trust": trust, "pool": "corpus"}


def synthetic_view() -> "ledger.View":
    v = ledger.View()
    t0, t1 = "2026-01-01T00:00:00.000000Z", "2026-01-02T00:00:00.000000Z"
    n = [0]

    def expose(item, k=1, slot="top"):
        rids = []
        for _ in range(k):
            n[0] += 1
            rid = f"fgr-{n[0]:012x}-shape-1"
            v.exposure[rid] = {"result_id": rid, "item": f"corpus:{item}", "slot": slot}
            rids.append(rid)
        return rids

    def mark(item, who, rid=None, event_rc=None, event_time=t1):
        n[0] += 1
        mid = f"fgm-{n[0]:012x}"
        v.marks[mid] = {"mark_id": mid, "item": f"corpus:{item}", "who": who, "result_id": rid, "time": t0}
        if event_rc is not None:
            v.events[mid] = [{"mark_id": mid, "rc": event_rc, "event_time": event_time}]

    def oracle(item, root):
        rid = expose(item)[0]
        n[0] += 1
        v.oracle[(rid, f"fgv-{n[0]:012x}")] = {"result_id": rid, "reused_at": {"root_sha256": root}}

    expose("c_a_popular", 40)                        # E1: shown often, never helped
    expose("c_a_popular", 10, slot="explore")
    mark("c_b_helped", "ann", expose("c_b_helped")[0], event_rc=0)
    mark("c_zc_pending", "ann")                      # E2: three marks, none scored
    mark("c_zc_pending", "bob", event_rc=1)          # failed event
    mark("c_zc_pending", "cyd", event_rc=0, event_time="2025-12-31T00:00:00.000000Z")   # before its mark
    for i in range(100):                             # E3: 100 person outcomes, no id
        mark("c_p_people", f"p{i}", event_rc=0)
    oracle("c_q_oracle", "r" * 64)                   # E3: one oracle outcome
    mark("c_x_noid", "ann", event_rc=0)              # E4: two without id
    mark("c_x_noid", "bob", event_rc=0)
    mark("c_y_withid", "ann", expose("c_y_withid")[0], event_rc=0)   # E4: one with id
    for _ in range(5):                               # E6: lots of history, lower score
        oracle("c_low_score", "s" * 64 + str(_))
    return v


def rule2_cases(key, v=None) -> list:
    v = synthetic_view() if v is None else v
    co = lambda item: ledger.counts(item, v=v)                               # noqa: E731
    ex = lambda item: len(ledger.exposures(item, v=v))                       # noqa: E731
    fails = []

    def first(hits):
        return order(hits, key, co, ex)[0]["item_id"]
    if first([_hit("c_a_popular"), _hit("c_b_helped")]) != "c_b_helped":
        fails.append("E1 selection only: 50 exposures without an outcome ranked ahead of one scored outcome")
    if first([_hit("c_d_none"), _hit("c_zc_pending")]) != "c_d_none":
        fails.append("E2 pending: marks without a passing later event moved an item up")
    if first([_hit("c_p_people"), _hit("c_q_oracle")]) != "c_q_oracle":
        fails.append("E3 lexicographic: 100 person outcomes ranked ahead of one oracle outcome")
    if first([_hit("c_x_noid"), _hit("c_y_withid")]) != "c_y_withid":
        fails.append("E4 lexicographic: two outcomes without an id ranked ahead of one with an id")
    if first([_hit("c_high", 0.95), _hit("c_low_score", 0.9)]) != "c_high":
        fails.append("E6 never a vote: history moved a hit across a score")
    if first([_hit("c_low_score", 0.9, "raw"), _hit("c_signed", 0.9, "signed")]) != "c_signed":
        fails.append("E6 never a vote: history moved a hit across a trust level")
    c = co("corpus:c_a_popular")
    if (c.oracle, c.person_with_id, c.person_without_id) != (0, 0, 0) or ex("corpus:c_a_popular") != 50:
        fails.append(f"E1 counts: a selection-only history must read 0 outcomes, read {c}")
    return fails


def rule2() -> list:
    """Failed cases: the real ordering's, plus one line per selection-rewarding variant that PASSES
    (a variant that passes means the set cannot see selection being rewarded)."""
    fails = rule2_cases(_real)
    for name, var in VARIANTS.items():
        if not rule2_cases(var):
            fails.append(f"red arm: variant {name!r} passed the set; the set cannot see it reward selection")
    return fails


# ---------------------------------------------------------------- in a report

HIT_LISTS = ("shape_hits", "meaning_hits", "behaviour_hits", "intent_hits")


def apply(c: dict, v: "ledger.View") -> tuple:
    """Annotate every shown hit of the corpus block ``c`` with its counts and order each list inside
    (score, trust) ties. Returns (hits with any outcome, lists whose order changed)."""
    co = lambda item: ledger.counts(item, v=v)                               # noqa: E731
    ex = lambda item: len(ledger.exposures(item, v=v))                       # noqa: E731
    with_history = moved = 0
    for key in HIT_LISTS:
        hits = c.get(key) or []
        if not hits:
            continue
        for h in hits:
            k = co(f"{h['pool']}:{h['item_id']}")
            h["experience"] = {"oracle": k.oracle, "with_id": k.person_with_id,
                               "without_id": k.person_without_id, "pending": k.pending}
            with_history += bool(k.oracle or k.person_with_id or k.person_without_id)
        top = [h for h in hits if h.get("slot", "top") == "top"]
        rest = [h for h in hits if h.get("slot", "top") != "top"]
        new = order(top, _real, co, ex)
        moved += new != top
        c[key] = new + rest
    return with_history, moved
