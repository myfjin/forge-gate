# Ported from aura-code-corpus src/code_corpus/shape.py at checkout c5129d2
# (md5 0350722f31814c31085d55cce603cfd6), port-and-freeze (design §2.3). Do not edit below
# the marker line; re-port instead. tests/test_scout_slice1.py checks the body's md5.
# --- ported body below ---
"""Near-duplicate shape (design §7): normalize code, shingle, MinHash + LSH, exact-Jaccard confirm."""
import hashlib
import re

TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|\$\{?\w+\}?|\d+(?:\.\d+)?|[A-Za-z_][\w-]*|\S')
KEEP = set("""
if then else elif fi for while do done case esac in function return local export def class import from as
with try except finally raise lambda yield async await pass break continue and or not is None True False
fn let mut pub impl struct enum match use mod func go defer package var const type interface switch
echo printf cat tar gzip rsync cp mv rm mkdir find grep sed awk sort uniq head tail wc cut xargs date ssh
scp sudo systemctl launchctl crontab sqlite3 curl wget git python python3 bash sh jq tee test set exit
print open read write json os sys re path subprocess run len range dict list str int self
""".split())
P = (1 << 61) - 1


def normalize(code: str) -> list[str]:
    out = []
    for line in code.split("\n"):
        s = line.strip()
        if not s or (s.startswith("#") and not s.startswith("#!")) or s.startswith("//"):
            continue
        for t in TOKEN.findall(line):
            if t[0] in "\"'":
                out.append("S")
            elif t[0] == "$":
                out.append("$V")
            elif t[0].isdigit():
                out.append("N")
            elif t[0].isalpha() or t[0] == "_":
                out.append(t if t in KEEP else "ID")
            else:
                out.append(t)
    return out


def shingles(code: str, k: int = 5) -> set[int]:
    toks = normalize(code)
    if len(toks) < k:
        return {_h(" ".join(toks))} if toks else set()
    return {_h(" ".join(toks[i:i + k])) for i in range(len(toks) - k + 1)}


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def _perms(n: int):
    rng = [_h(f"perm-{i}") for i in range(2 * n)]
    return [(rng[2 * i] % (P - 1) + 1, rng[2 * i + 1] % P) for i in range(n)]


class MinHasher:
    def __init__(self, perms: int = 64, bands: int = 16):
        self.perms, self.bands, self.rows = _perms(perms), bands, perms // bands

    def signature(self, sh: set[int]) -> list[int]:
        if not sh:
            return [P] * len(self.perms)
        return [min((a * x + b) % P for x in sh) for a, b in self.perms]

    def band_keys(self, sig: list[int]):
        for i in range(self.bands):
            yield i, tuple(sig[i * self.rows:(i + 1) * self.rows])


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def clusters(items: dict[str, set[int]], tau: float, perms: int, bands: int):
    """items: id -> shingle set. Returns (list of member-id lists, confirmed pair Jaccards, signatures)."""
    mh = MinHasher(perms, bands)
    sigs = {i: mh.signature(s) for i, s in items.items()}
    buckets = {}
    for i, sig in sigs.items():
        for key in mh.band_keys(sig):
            buckets.setdefault(key, []).append(i)
    parent = {i: i for i in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    pairs, seen = {}, set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > 500:
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                a, b = members[x], members[y]
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                j = jaccard(items[a], items[b])
                if j >= tau:
                    pairs[(a, b)] = j
                    parent[find(a)] = find(b)
    groups = {}
    for i in items:
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1], pairs, sigs
