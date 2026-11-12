"""Rule 2 (design v1.4.5 §4a.4): a lens is tested before it is trusted.

The known-case set for the shape lens. Scout runs it on every invocation; a lens that
fails any case is not switched on (it reports BLIND(rule2-failed)). The cases are written
here, not taken from the corpus: corpus bodies come from private conversations and never
enter this repo. The seed is aura-code-corpus KNOWN-DEBT #1 (three backup scripts doing
one job, shape Jaccard 0.03-0.11); these three are new scripts of the same kind.

Each lens is a scorer ``f(a, b) -> float in [0, 1]`` plus its threshold.
"""
from __future__ import annotations

# --- the pool every case is scored against -------------------------------------------
BACKUP_TAR = """#!/bin/bash
set -eu
src="$1"; dest="$2"
stamp=$(date +%Y%m%d-%H%M%S)
tar -czf "$dest/home-$stamp.tar.gz" -C "$src" .
find "$dest" -name 'home-*.tar.gz' -mtime +14 -delete
echo "backup written: $dest/home-$stamp.tar.gz"
"""

BACKUP_PY = """import shutil, sys, time, pathlib
def main():
    source, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    name = target / time.strftime("home-%Y%m%d-%H%M%S")
    archive = shutil.make_archive(str(name), "gztar", root_dir=source)
    cutoff = time.time() - 14 * 86400
    for old in target.glob("home-*.tar.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    print("backup written:", archive)
main()
"""

BACKUP_RSYNC = """#!/bin/bash
set -eu
SRC=$1
DST=$2/snapshots/$(date +%F)
mkdir -p "$DST"
rsync -a --delete --link-dest="$2/snapshots/latest" "$SRC/" "$DST/"
ln -sfn "$DST" "$2/snapshots/latest"
"""

AREA = """def area(width, height):
    if width < 0 or height < 0:
        raise ValueError("negative side")
    return width * height
"""

KMEANS = """def assign(points, centres):
    labels = []
    for p in points:
        best, best_d = 0, float("inf")
        for i, c in enumerate(centres):
            d = sum((a - b) ** 2 for a, b in zip(p, c))
            if d < best_d:
                best, best_d = i, d
        labels.append(best)
    return labels
"""

JSON_STATS = """import json
def summary(path):
    with open(path) as fh:
        rows = json.load(fh)
    total = sum(r["amount"] for r in rows)
    return {"rows": len(rows), "total": total, "mean": total / max(len(rows), 1)}
"""

POOL = {"backup_tar": BACKUP_TAR, "backup_py": BACKUP_PY, "backup_rsync": BACKUP_RSYNC,
        "area": AREA, "kmeans": KMEANS, "json_stats": JSON_STATS}

# --- queries ----------------------------------------------------------------------------
# Same look, different meaning: AREA's exact token shape, a tax computation.
TAX = """def tax(price, rate):
    if price < 0 or rate < 0:
        raise ValueError("negative input")
    return price * rate
"""

# Same meaning, same look, every name changed: a renamed copy of KMEANS. Only shape sees it;
# the keyword baseline loses most of its words.
NEAREST = """def nearest(samples, means):
    out = []
    for s in samples:
        idx, dist = 0, float("inf")
        for j, m in enumerate(means):
            e = sum((u - v) ** 2 for u, v in zip(s, m))
            if e < dist:
                idx, dist = j, e
        out.append(idx)
    return out
"""

# Only shape can get this right: a fork of KMEANS with a bug (ties go to the LAST centre and
# labels are 1-based). Behaviour differs, so a behaviour lens would call it unrelated; the
# copy lineage is visible only in the shape.
KMEANS_FORK = """def assign(points, centres):
    labels = []
    for p in points:
        best, best_d = 0, float("inf")
        for i, c in enumerate(centres):
            d = sum((a - b) ** 2 for a, b in zip(p, c))
            if d <= best_d:
                best, best_d = i, d
        labels.append(best + 1)
    return labels
"""

# Null: related to nothing in the pool.
NULL = """class Stack:
    def __init__(self):
        self._items = []
    def push(self, x):
        self._items.append(x)
    def pop(self):
        return self._items.pop()
"""

# Keyword trap: shares the backup scripts' words but is a different job and a different shape.
KEYWORD_TRAP = """# list backup home archives by size
for f in home-*.tar.gz; do
  du -h "$f"
done | sort -h
"""


def _ranked(scorer, query):
    return sorted(((scorer(query, body), name) for name, body in POOL.items()), key=lambda t: (-t[0], t[1]))


def evaluate(scorer, tau: float, baseline=None) -> list[str]:
    """Return the failed cases (empty = the lens may be switched on)."""
    fails = []
    # 1. same meaning / different look: shape alone must MISS (the lens's documented limit).
    for a, b in (("backup_tar", "backup_py"), ("backup_tar", "backup_rsync"), ("backup_py", "backup_rsync")):
        s = scorer(POOL[a], POOL[b])
        if s >= tau:
            fails.append(f"same-meaning/different-look: {a}~{b} scored {s:.2f} >= {tau}; shape must miss it")
    # 2. same look / different meaning: shape alone must WRONGLY match.
    top_s, top_n = _ranked(scorer, TAX)[0]
    if not (top_n == "area" and top_s >= tau):
        fails.append(f"same-look/different-meaning: TAX ranked {top_n} at {top_s:.2f}; must match area >= {tau}")
    # 3. null case: no member at or above tau.
    top_s, top_n = _ranked(scorer, NULL)[0]
    if top_s >= tau:
        fails.append(f"null: Stack matched {top_n} at {top_s:.2f} >= {tau}")
    # 4. the cases the lens must find: the renamed copy, and the buggy fork only shape can see.
    for q, want, what in ((NEAREST, "kmeans", "renamed copy"), (KMEANS_FORK, "kmeans", "buggy fork (shape-only)")):
        top_s, top_n = _ranked(scorer, q)[0]
        if not (top_n == want and top_s >= tau):
            fails.append(f"find: {what} ranked {top_n} at {top_s:.2f}; must be {want} >= {tau}")
    # 5. the keyword trap must not lure the lens.
    ls_s, ls_n = _ranked(scorer, KEYWORD_TRAP)[0]
    if ls_s >= tau:
        fails.append(f"keyword trap: the lens matched {ls_n}, a different job, at {ls_s:.2f} >= {tau}")
    # 6. the keyword baseline, scored alongside, must separate worse than the lens.
    if baseline is not None:
        sep_l, sep_b = separation(scorer), separation(baseline)
        if not sep_l > sep_b:
            fails.append(f"baseline: lens separation {sep_l:.2f} does not beat the keyword baseline's {sep_b:.2f}")
        if not _ranked(baseline, KEYWORD_TRAP)[0][1].startswith("backup"):
            fails.append("baseline: the keyword trap did not lure the baseline; the trap tests nothing")
    return fails


def separation(scorer) -> float:
    """Lowest score on a pair that must match, minus the highest on a pair that must not.
    Must-match: NEAREST~kmeans, KMEANS_FORK~kmeans. Must-not: every query's other members,
    NULL and KEYWORD_TRAP against everything, and the three backup pairs (shape is known to
    miss those, so they count as must-not for a shape-type lens)."""
    pos = [scorer(NEAREST, KMEANS), scorer(KMEANS_FORK, KMEANS)]
    neg = [scorer(q, b) for q in (NULL, KEYWORD_TRAP) for b in POOL.values()]
    neg += [scorer(q, b) for q in (NEAREST, KMEANS_FORK) for n, b in POOL.items() if n != "kmeans"]
    neg += [scorer(POOL[a], POOL[b]) for a, b in (("backup_tar", "backup_py"), ("backup_tar", "backup_rsync"), ("backup_py", "backup_rsync"))]
    return min(pos) - max(neg)
