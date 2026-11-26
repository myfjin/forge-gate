"""Lens 5, intent (design §4a.3; build step 8b s3; DECISIONS-8b H6, H7).

"Related" means written for the same purpose. Intent compares PURPOSE TEXTS only, never code: the
query's purpose is what the person states (``forgeproof scout FILE --intent "TEXT"``); a member's is
its pool's intent field (corpus = the scrubbed request, library = the pattern's description, GitHub =
docstrings + commit message). Today no pool scout searches carries one (DECISIONS-8b F7, F8): the
corpus export has no request text, and the library and GitHub are not scout pools yet. So on a real
run this lens is BLIND: rule2-failed on the deploy host whenever --intent is given (no tau separates its
set on the pinned model, KD-15 m), and no-input for every pool until a pool supplies purpose text.

Score = cosine of the pinned local embedder's vectors of the two purpose texts. The Rule-2 set
(written first, H7) runs before the lens is used; a red set means BLIND(rule2-failed).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import embed, meaning_lens, rule2, shape_lens, slots
from .outcomes import Outcome, blind, clean, found

NAME = "intent"
# NOT calibrated: no tau passes this set on the pinned model. Live on the deploy host (2026-11-16,
# digest 0a109f422b47): min same-purpose cos 0.579 (backup_py~backup_rsync) < max must-not cos 0.599
# (keyword trap -> backup_rsync), separation -0.020. The set is red at every tau and the lens ships OFF
# (BLIND rule2-failed), like meaning (KD-15 m). 0.75 is the builder's prior, kept so the red stays loud.
TAU = 0.75
MIN_WORDS = 3
TEXT_MAX = 2000

# ---------------------------------------------------------------- Rule 2 (H7), written first

DISKALERT_PY = """import shutil
for mount in ("/", "/home"):
    u = shutil.disk_usage(mount)
    if u.used / u.total > 0.9:
        print("ALERT", mount)
"""

DISKALERT_SH = """df -P | awk 'NR>1 && $5+0 > 90 {print "ALERT", $6}'
"""

# The code each purpose belongs to (shape and the keyword baseline are measured on it in the tests,
# to show the only-intent pair is invisible to them); intent itself reads only REQUESTS.
CODE = dict(meaning_lens.POOL, diskalert_py=DISKALERT_PY, diskalert_sh=DISKALERT_SH)

# One request per pool member, written by the builder (the crew review should attack these).
REQUESTS = {
    "backup_tar": "back up my home folder every night into a dated tarball and throw away copies older than two weeks",
    "backup_py": "python script that archives a source directory to a timestamped gzip file in a target "
                 "folder and deletes archives more than fourteen days old",
    "backup_rsync": "keep a daily backup copy of a directory using rsync snapshots with hard links",
    "area": "return the area of a rectangle given its width and height, rejecting negative sides",
    "kmeans": "assign each point to its nearest cluster centre",
    "json_stats": "summarise a JSON file of transactions: row count, total and mean amount",
    "errcount_py": "count the ERROR lines in each log file and list the files with the most errors first",
    "errcount_sh": "for every log I pass, tell me how many errors it has, worst first",
    "diskalert_py": "warn me when a disk is almost full",
    "diskalert_sh": "send an alert if any filesystem goes above ninety percent used",
}
GROUPS = {"backup": ("backup_tar", "backup_py", "backup_rsync"), "errcount": ("errcount_py", "errcount_sh"),
          "diskalert": ("diskalert_py", "diskalert_sh")}
MUST_NOT = {
    "same look, other purpose (tax vs area)": "compute the sales tax owed on a price at a given rate, rejecting negative inputs",
    "null (a stack)": "a last-in first-out stack with push and pop",
    "keyword trap (lists backups)": "list my backup archives by size",
}


def rule2_cases(score, tau: float, baseline=None) -> list:
    """Failed cases for ``score(a, b)`` over purpose texts (empty = may be switched on)."""
    fails = []

    def ranked(q, exclude=None):
        return sorted(((score(q, t), n) for n, t in REQUESTS.items() if n != exclude), key=lambda x: (-x[0], x[1]))
    # 1. same purpose / different look (and different words): partners first, at or above tau. The
    #    diskalert pair is the only-intent case: its code shares nothing (shape and keyword miss it).
    for g, names in GROUPS.items():
        for n in names:
            want = set(names) - {n}
            r = ranked(REQUESTS[n], exclude=n)[:len(want)]
            if {name for _, name in r} != want or any(s < tau for s, _ in r):
                fails.append(f"same-purpose: {n}'s top {len(want)} are {[f'{name} {s:.3f}' for s, name in r]}; "
                             f"must be {sorted(want)} >= {tau}")
    # 2. same look / different purpose, the null case, the keyword trap: nothing at or above tau.
    for what, q in MUST_NOT.items():
        s, n = ranked(q)[0]
        if s >= tau:
            fails.append(f"{what}: matched {n} at {s:.3f} >= {tau}")
    # 3. the keyword baseline over the same purpose texts must separate worse; the trap must lure it.
    #    NOTE (8b s2 review F1): with tau between them, the cases above force the lens's separation
    #    above 0 while the baseline's is negative on these fixtures, so 'beats the baseline' is IMPLIED
    #    by them here and is not separate evidence (KD-15 j). The trap-lure check is independent.
    if baseline is not None:
        sep_l, sep_b = separation(score), separation(baseline)
        if not sep_l > sep_b:
            fails.append(f"baseline: lens separation {sep_l:.3f} does not beat the keyword baseline's {sep_b:.3f}")
        trap = MUST_NOT["keyword trap (lists backups)"]
        if not max((baseline(trap, t), n) for n, t in REQUESTS.items())[1].startswith("backup"):
            fails.append("baseline: the keyword trap did not lure the baseline; the trap tests nothing")
    return fails


def separation(score) -> float:
    pos = [score(REQUESTS[a], REQUESTS[b]) for names in GROUPS.values()
           for i, a in enumerate(names) for b in names[i + 1:]]
    neg = [score(q, t) for q in MUST_NOT.values() for t in REQUESTS.values()]
    return min(pos) - max(neg)


def _texts() -> list:
    return [t[:TEXT_MAX] for t in list(REQUESTS.values()) + list(MUST_NOT.values())]


def rule2_run(emb) -> list:
    texts = _texts()
    vecs = dict(zip(texts, emb.embed(texts)))
    return rule2_cases(lambda a, b: embed.cosine(vecs[a[:TEXT_MAX]], vecs[b[:TEXT_MAX]]), TAU,
                       shape_lens.keyword_score)


# ---------------------------------------------------------------- the lens

@dataclass
class LensResult:
    outcome: Outcome
    hits: list = field(default_factory=list)
    looked_at: int = 0


def rank(intent: str, members: list, *, emb, pool: str, trust: str) -> LensResult:
    """members: dicts with item_id and ``intent`` (the pool's purpose text). A member without one is
    not compared; if none has one, the pool gives BLIND(no-input), never 'unrelated'."""
    if len((intent or "").split()) < MIN_WORDS:
        return LensResult(blind(f"no-input: the stated intent has fewer than {MIN_WORDS} words"))
    usable = [m for m in members if isinstance(m.get("intent"), str) and len(m["intent"].split()) >= MIN_WORDS]
    if not usable:
        return LensResult(blind(f"no-input: no {pool} member carries purpose text"))
    vecs = emb.embed([intent[:TEXT_MAX]] + [m["intent"][:TEXT_MAX] for m in usable])
    q, rest = vecs[0], vecs[1:]
    hits = [shape_lens.Hit(m["item_id"], round(embed.cosine(q, v), 4), pool, trust, m.get("lang", ""),
                           m.get("kind", "")) for m, v in zip(usable, rest)]
    hits.sort(key=lambda h: (-h.score, h.item_id))
    top, explore = slots.split(hits, lens=NAME, query=intent)
    over = [h for h in top if h.score >= TAU]
    note = f" ({len(members) - len(usable)} member(s) without purpose text not compared)" if len(usable) < len(members) else ""
    shown = top + ([explore] if explore else [])
    if over:
        return LensResult(found(f"{len(over)} at or above cos={TAU}{note}"), shown, len(usable))
    return LensResult(clean(f"best cos={top[0].score} < {TAU}{note}"), shown, len(usable))


def run_for(intent, members: list, *, pool: str, trust: str, checks: dict) -> LensResult:
    """The report's entry point: stated intent -> embedder -> Rule 2 -> the pool's purpose texts."""
    if not intent:
        checks["rule2-intent"] = blind("not run: no stated intent (give --intent \"TEXT\")")
        return LensResult(blind("no-input: no stated intent (give --intent \"TEXT\")"))
    try:
        emb = embed.get()
        fails = rule2_run(emb)
    except embed.EmbedError as e:
        checks["rule2-intent"] = blind(str(e))
        return LensResult(blind(str(e)))
    if fails:
        checks["rule2-intent"] = found(f"{len(fails)} case(s) failed: lens not switched on", *fails)
        return LensResult(blind(f"rule2-failed: lens not switched on ({len(fails)} case(s))"))
    checks["rule2-intent"] = clean("all known cases hold; lens may be switched on")
    try:
        return rank(intent, members, emb=emb, pool=pool, trust=trust)
    except embed.EmbedError as e:
        return LensResult(blind(str(e)))
