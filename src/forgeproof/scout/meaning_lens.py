"""Lens 4, language meaning (design §4a.3; build step 8b s2; DECISIONS-8b H1-H5).

"Related" means the code, its comments and its description say the same thing. Score = cosine of
two embeddings from the pinned LOCAL embedder (``embed.py``). Two scorers are built and measured
against the same Rule-2 set (H2); a scorer is switched on only if it passes EVERY case on this run,
and the first that passes (in SCORERS order) is used. None passing = BLIND(rule2-failed), with the
failing cases printed: an honest red is what Rule 2 is for.

- ``raw``: the text as written (code, comments, strings);
- ``words``: only the meaning-bearing words: comment text, and identifiers split at ``_`` and
  camelCase, lower-cased, language keywords dropped.

Members are ranked across ALL languages (the Rule-2 case only this lens gets right is a Bash and
a Python script doing one job). A scout run never embeds a pool member: member vectors come from
the cache that ``forgeproof scout --embed-fill`` writes (H4); a member missing from it makes the
lens BLIND(index-incomplete), never a partial list that looks complete. The query, the control and
the Rule-2 fixtures are short and embedded live.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import embed, rule2, shape_lens, slots
from .outcomes import Outcome, blind, clean, found

NAME = "meaning"
# Thresholds per scorer, set from the probe on the Rule-2 fixtures (DECISIONS-8b F12/F16). They are
# calibrated ON the Rule-2 set, so that set is not a held-out test of them (KD-15).
TAU = {"raw": 0.61, "words": 0.61}
SCORERS = ("raw", "words")
TEXT_MAX = 8000          # characters sent per text: deterministic truncation, not the model's
MIN_WORDS = 3            # fewer meaning-bearing words than this: nothing to compare

_KEYWORDS = frozenset("""
and as assert async await break class continue def del elif else except false finally for from global if
import in is lambda none nonlocal not or pass raise return self true try while with yield print
then fi do done case esac function local echo set eu exit
let const var new this null undefined void typeof public private static int str bool float list dict
""".split())
_COMMENT = re.compile(r"(?:^|\s)(?:#|//)\s?(.*)$", re.M)
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")


def words_text(text: str) -> str:
    """The meaning-bearing words of a text: comment text first, then split identifiers."""
    out = []
    for m in _COMMENT.finditer(text):
        if not m.group(1).startswith("!"):           # a shebang says which interpreter, not what
            out += [w.lower() for w in _IDENT.findall(m.group(1))]
    code = _COMMENT.sub(" ", text)
    for ident in _IDENT.findall(code):
        for part in ident.split("_"):
            out += [w.lower() for w in _CAMEL.findall(part)]
    return " ".join(w for w in out if w not in _KEYWORDS and len(w) > 1)


def lens_text(text: str, scorer: str) -> str:
    t = text if scorer == "raw" else words_text(text)
    return t[:TEXT_MAX]


@dataclass
class LensResult:
    outcome: Outcome
    hits: list = field(default_factory=list)
    looked_at: int = 0
    scorer: str = ""


# ---------------------------------------------------------------- Rule 2 (H3), written first

# A second same-job pair in two languages, so the only-this-lens case does not rest on one family.
ERRCOUNT_PY = """import sys
from collections import Counter
# count how many error lines each log file has
counts = Counter()
for path in sys.argv[1:]:
    with open(path) as fh:
        counts[path] = sum(1 for line in fh if "ERROR" in line)
for path, n in counts.most_common():
    print(n, path)
"""

ERRCOUNT_SH = """#!/bin/bash
# how many ERROR lines per log file, most first
grep -c ERROR -- "$@" | awk -F: '{print $2, $1}' | sort -rn
"""

POOL = dict(rule2.POOL, errcount_py=ERRCOUNT_PY, errcount_sh=ERRCOUNT_SH)
SAME_JOB = (("backup_tar", "backup_py"), ("backup_tar", "backup_rsync"), ("backup_py", "backup_rsync"),
            ("errcount_py", "errcount_sh"))
MUST_NOT = {"TAX (same look as area)": rule2.TAX, "null (Stack)": rule2.NULL,
            "keyword trap": rule2.KEYWORD_TRAP}


def rule2_cases(score, tau: float, baseline=None) -> list:
    """Failed cases for a scorer ``score(a, b) -> float`` (empty = may be switched on)."""
    fails = []

    def top(q, exclude=None):
        r = sorted(((score(q, b), n) for n, b in POOL.items() if n != exclude), key=lambda t: (-t[0], t[1]))
        return r
    # 1. same meaning / different look, and the only-this-lens case: each member of a same-job group
    #    has its partners at the top of its list, at or above tau (shape misses these: J 0.00-0.12).
    groups = {"backup": ["backup_tar", "backup_py", "backup_rsync"], "errcount": ["errcount_py", "errcount_sh"]}
    for g, names in groups.items():
        for n in names:
            want = set(names) - {n}
            r = top(POOL[n], exclude=n)
            got = {name for _, name in r[:len(want)]}
            low = [f"{name} {s:.3f}" for s, name in r[:len(want)] if s < tau]
            if got != want or low:
                fails.append(f"same-meaning/different-look: {n}'s top {len(want)} are "
                             f"{[f'{name} {s:.3f}' for s, name in r[:len(want)]]}; must be {sorted(want)} >= {tau}")
    # 2. renamed copy: meaning must survive renaming.
    s, n = top(rule2.NEAREST)[0]
    if not (n == "kmeans" and s >= tau):
        fails.append(f"renamed copy: NEAREST ranked {n} at {s:.3f}; must be kmeans >= {tau}")
    # 3. same look / different meaning, the null case and the keyword trap: nothing at or above tau.
    #    The same-look case is valid only while shape still matches TAX to area (8b crew review, 6E).
    if shape_lens.score(rule2.TAX, rule2.AREA) < shape_lens.TAU:
        fails.append("case invalid: shape no longer matches TAX to area, so the same-look case tests nothing")
    for what, q in MUST_NOT.items():
        s, n = top(q)[0]
        if s >= tau:
            fails.append(f"{what}: matched {n} at {s:.3f} >= {tau}")
    # 4. the keyword baseline, scored alongside, must separate worse than the lens; and the trap
    #    must lure the baseline, or the trap case tests nothing.
    #    NOTE (8b s2 review F1): with tau between them, the cases above force the lens's separation
    #    above 0 while the baseline's is negative on these fixtures, so 'beats the baseline' is IMPLIED
    #    by them here and is not separate evidence (KD-15 j). The trap-lure check is independent.
    if baseline is not None:
        sep_l, sep_b = separation(score), separation(baseline)
        if not sep_l > sep_b:
            fails.append(f"baseline: lens separation {sep_l:.3f} does not beat the keyword baseline's {sep_b:.3f}")
        if not sorted(((baseline(rule2.KEYWORD_TRAP, b), n) for n, b in POOL.items()), reverse=True)[0][1].startswith("backup"):
            fails.append("baseline: the keyword trap did not lure the baseline; the trap tests nothing")
    return fails


def separation(score) -> float:
    pos = [score(POOL[a], POOL[b]) for a, b in SAME_JOB] + [score(rule2.NEAREST, POOL["kmeans"])]
    neg = [score(q, b) for q in MUST_NOT.values() for b in POOL.values()]
    return min(pos) - max(neg)


def _texts_for_rule2(scorer: str) -> list:
    return [lens_text(t, scorer) for t in
            list(POOL.values()) + [rule2.NEAREST] + list(MUST_NOT.values())]


def rule2_run(emb, scorer: str) -> list:
    """Embed the fixtures live with ``emb`` and return the failed cases for ``scorer``."""
    texts = _texts_for_rule2(scorer)
    vecs = dict(zip(texts, emb.embed(texts)))

    def score(a, b):
        return embed.cosine(vecs[lens_text(a, scorer)], vecs[lens_text(b, scorer)])
    return rule2_cases(score, TAU[scorer], shape_lens.keyword_score)


def choose(emb) -> tuple:
    """(scorer, {scorer: fails}). scorer is '' when none passes. EmbedError propagates (BLIND)."""
    tried = {}
    for sc in SCORERS:
        tried[sc] = rule2_run(emb, sc)
        if not tried[sc]:
            return sc, tried
    return "", tried


# ---------------------------------------------------------------- the lens

def comparable(members: list, scorer: str) -> list:
    """Members whose lens text has at least MIN_WORDS words: the only ones the fill embeds and rank
    compares (8b s2 review F2: an empty text would stop the fill on every run, BLIND for good)."""
    return [m for m in members if len(lens_text(m["body"], scorer).split()) >= MIN_WORDS]


def rank(query: str, members: list, *, emb, cache: dict, scorer: str, pool: str, trust: str) -> LensResult:
    """members: dicts with item_id, body (any language). ``cache``: {embed.key(text): vector}."""
    qt = lens_text(query, scorer)
    if len(qt.split()) < MIN_WORDS:
        return LensResult(blind(f"no-input: the query has fewer than {MIN_WORDS} words for scorer {scorer}"),
                          scorer=scorer)
    if not members:
        return LensResult(blind(f"no-input: {pool} pool is empty"), scorer=scorer)
    usable = comparable(members, scorer)
    if not usable:
        return LensResult(blind(f"no-input: no {pool} member has {MIN_WORDS} words for scorer {scorer}"),
                          scorer=scorer)
    note = (f" ({len(members) - len(usable)} member(s) under {MIN_WORDS} words not compared)"
            if len(usable) < len(members) else "")
    keyed = [(m, embed.key(lens_text(m["body"], scorer), emb.digest)) for m in usable]
    missing = sum(1 for _, k in keyed if k not in cache)
    if missing:
        return LensResult(blind(f"index-incomplete: {len(usable) - missing} of {len(usable)} comparable members "
                                f"embedded for scorer {scorer}; run `forgeproof scout --embed-fill`"),
                          looked_at=len(usable) - missing, scorer=scorer)
    qv = emb.embed([qt])[0]
    hits = [shape_lens.Hit(m["item_id"], round(embed.cosine(qv, cache[k]), 4), pool, trust,
                           m.get("lang", ""), m.get("kind", "")) for m, k in keyed]
    hits.sort(key=lambda h: (-h.score, h.item_id))
    top, explore = slots.split(hits, lens=NAME, query=query)
    shown = top + ([explore] if explore else [])
    tau = TAU[scorer]
    over = [h for h in top if h.score >= tau]
    if over:
        return LensResult(found(f"{len(over)} at or above cos={tau} ({scorer}){note}"), shown, len(usable), scorer)
    return LensResult(clean(f"best cos={top[0].score} < {tau} ({scorer}){note}"), shown, len(usable), scorer)


def control(members: list, *, emb, cache: dict, scorer: str) -> tuple:
    """The planted case (§4a.2): the shortest cached member, embedded LIVE, must come back first at
    cos >= 0.999 against its CACHED vector. That also proves the cache matches the live model.
    Returns (ok, why)."""
    cands = sorted(comparable(members, scorer), key=lambda m: (len(lens_text(m["body"], scorer)), m["item_id"]))
    if not cands:
        return False, "no member long enough to plant"
    m = cands[0]
    k = embed.key(lens_text(m["body"], scorer), emb.digest)
    if k not in cache:
        return False, (f"index-incomplete: planted {m['item_id']} is not in the cache; "
                       "run `forgeproof scout --embed-fill`")
    live = emb.embed([lens_text(m["body"], scorer)])[0]
    s = embed.cosine(live, cache[k])
    if s < 0.999:
        return False, f"planted {m['item_id']}: live vs cached cos={s:.4f} < 0.999 (cache from another model state)"
    return True, f"planted {m['item_id']} came back at cos={s:.4f}"
