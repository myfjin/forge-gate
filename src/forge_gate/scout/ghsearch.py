"""Scout over the GitHub pool (GitHub-lane loop 2026-11, slice S2; DESIGN §5 row S2).

`forge-gate scout <file>` shows functions from ratified GitHub repos that LOOK like the query,
through the same shape lens as the corpus pool. GitHub code is read as TEXT only: ``ast.parse``
turns it into a tree to find function spans, and nothing is imported, compiled to code or run.

Per query, in this order (the admission rules of ``ghpool.admit``, paid once instead of per file):
1. the cache root and the signed list must exist; else BLIND(no-cache) / BLIND(unsigned);
2. the signed list is verified ONCE (``ratify.verify_list``: every signature of every line is
   checked once per query); no verified line → BLIND(unsigned);
3. the host's trust pin (``FORGE_GATE_RATIFY_PIN``) must name the key that signed each line as
   the required principal; ANY line that fails 2 or 3 makes the whole pool BLIND(unsigned) (review
   F2: a dropped line must not read as a clean search);
4. per verified line: ``ghcache.check_cache`` ONCE for that cache and one ``ls-tree``. Any
   failure → the WHOLE pool is BLIND(pin-mismatch): a cache that fails its pin is not searched
   around;
5. the repo licence (allowlist + signed licence blob of every top-level licence file), as admit;
   a repo that fails it is left out and listed;
6. per file read: its git blob sha, recomputed from the bytes on disk, must equal the tree's
   entry at the pin, else the WHOLE pool is BLIND(pin-mismatch); then inclusion and the file's
   own licence marker, as admit (a file that fails them is left out and counted);
7. a planted control: a deterministic member of the pool is ranked against the pool and must
   come back FIRST, as itself, at J=1.0; else the pool is BLIND(control-failed);
8. the shape lens ranks every member; its FOUND / LOOKED-CLEAN is the pool's outcome.

Each member is one function, named by its scout id ``scout:github:<org/repo>@<commit>:<path>::<qualname>``.
Hits print as ``pool=github trust=raw``. Wall time is measured and carried in the result.
"""
from __future__ import annotations

import ast
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import config
from . import fetchproxy, ghcache, ghpool, ratify, shape, shape_lens
from .ids import BadScoutId, make_id
from .outcomes import FOUND, Outcome, blind

POOL = ghpool.POOL            # "github"
TRUST = "raw"                 # report.OTHER_POOLS: the GitHub pool's trust label
LANGS = {"python": ".py"}     # the shape lens reads Python members only in this slice
CONTROL_MIN_TOKENS = 40       # as the corpus control: long enough to shingle reliably


@dataclass
class GhResult:
    outcome: Outcome
    hits: list = field(default_factory=list)
    looked_at: int = 0
    control: Outcome = field(default_factory=lambda: blind("not run"))
    repos: list = field(default_factory=list)      # one dict per verified line: what was read / left out
    seconds: float = 0.0


def _functions(tree: ast.AST):
    """(qualname, node) for every function, classes and enclosing functions joined by '.'."""
    stack = [("", tree)]
    while stack:
        prefix, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + child.name
                yield q, child
                stack.append((q + ".", child))
            elif isinstance(child, ast.ClassDef):
                stack.append((prefix + child.name + ".", child))
            else:
                stack.append((prefix, child))


def members_of(text: str, org: str, repo: str, commit: str, path: str) -> list:
    """The file's functions as pool members (text only). Raises SyntaxError and friends."""
    tree = ast.parse(text, filename=path)
    lines = text.splitlines(keepends=True)
    out, seen = [], set()
    for q, node in sorted(_functions(tree), key=lambda qn: (qn[1].lineno, qn[0])):
        name = q if q not in seen else f"{q}#L{node.lineno}"     # a redefined name keeps its own id
        seen.add(q)
        body = "".join(lines[node.lineno - 1:node.end_lineno])
        out.append({"item_id": make_id(POOL, f"{org}/{repo}", commit, path, name), "body": body,
                    "lang": "python", "kind": "function"})
    return out


def _read_pinned(tree_dir: Path, path: str, want_sha: str) -> bytes:
    """One exported file's bytes, checked against its pinned blob (raises PinMismatch)."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or path != p.as_posix():
        raise ghcache.PinMismatch(f"not a plain relative path: {path!r}")
    f = tree_dir.joinpath(*p.parts)
    try:
        st = os.lstat(f)
        if not stat.S_ISREG(st.st_mode):
            raise ghcache.PinMismatch(f"{path} is not a regular file in the exported tree")
        data = f.read_bytes()
    except OSError as e:
        raise ghcache.PinMismatch(f"{path}: unreadable: {e}") from None
    if ghcache.blob_sha(data) != want_sha:
        raise ghcache.PinMismatch(f"{path}: bytes differ from the pinned blob")
    return data


def _repo_members(v: ratify.Verdict, root: Path, suffix: str, info: dict) -> list:
    """Members of one verified line's cache. Raises PinMismatch for any pin failure; returns []
    with ``info['left_out']`` set when the repo's licence does not admit it."""
    org, repo, commit = v.fields["org"], v.fields["repo"], v.fields["commit"]
    record = ghcache.check_cache(org, repo, commit, v, root=root)          # ONCE per cache
    store = root / ".objects" / org / f"{repo}.git"
    tree_dir = root / org / f"{repo}@{commit}"
    tree = ghcache.tree_files(store, commit)                               # one ls-tree
    spdx = v.fields.get("licence_spdx")
    present = [n for n in ghpool.LICENCE_FILES if n in tree]
    if not ghpool.spdx_allowed(spdx or ""):
        info["left_out"] = f"licence: {spdx!r} is not on the allowlist"
        return []
    if not present or any(tree[n][1] != v.fields.get("licence_blob") for n in present):
        info["left_out"] = f"licence: top-level licence file(s) {present} are not all the signed blob"
        return []
    skipped = set(record.get("skipped") or [])
    out, excluded, unparsable = [], {}, 0
    for path in sorted(tree):
        if PurePosixPath(path).suffix != suffix or path in skipped:
            continue
        why = ghpool.included(path, b"")                    # path rules first: no read needed
        if not why:
            data = _read_pinned(tree_dir, path, tree[path][1])               # per-file pin check
            why = ghpool.included(path, data)
            if not why:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    why = "not-included: not UTF-8 text"
                else:
                    why = ghpool.file_licence(text)
        if why:
            k = why.split(":", 1)[0]
            excluded[k] = excluded.get(k, 0) + 1
            continue
        info["files_read"] += 1
        try:
            out.extend(members_of(text, org, repo, commit, path))
        except (SyntaxError, ValueError, RecursionError, MemoryError, BadScoutId):
            unparsable += 1
    info.update(members=len(out), excluded=excluded, unparsable=unparsable)
    return out


def _control_member(members):
    """The run's planted control: the first member by id that is long enough to shingle."""
    for m in sorted(members, key=lambda m: m["item_id"]):
        if len(shape.normalize(m["body"])) >= CONTROL_MIN_TOKENS:
            return m
    return None


def _memo_score():
    """shape_lens.score with each text's shingles computed once per query (control + query share them)."""
    memo: dict = {}

    def sh(t):
        s = memo.get(t)
        if s is None:
            s = memo[t] = shape.shingles(t)
        return s
    return lambda a, b: shape.jaccard(sh(a), sh(b))


def search(query: str | None, *, lang: str | None = "python", list_file: Path | None = None,
           signers_file: Path | None = None, cache_root: Path | None = None,
           host_sets_file: Path | None = None, trust_pin_value: str | None = None) -> GhResult:
    t0 = time.monotonic()
    res = _search(query, lang, list_file, signers_file, cache_root, host_sets_file, trust_pin_value)
    res.seconds = round(time.monotonic() - t0, 3)
    return res


def _search(query, lang, list_file, signers_file, cache_root, host_sets_file, trust_pin_value) -> GhResult:
    R = GhResult
    if lang not in LANGS:
        return R(blind(f"language: {lang or 'unknown'} not searched in the GitHub pool (python only)"))
    if query is None:
        return R(blind("unreadable: not UTF-8"))
    try:
        root = Path(cache_root) if cache_root is not None else ghcache.cache_root()
    except config.NotConfigured as e:
        return R(blind(f"no-cache: {e}"))
    if not root.is_dir():
        return R(blind(f"no-cache: {root} does not exist on this host"))
    if list_file is None or signers_file is None:
        try:
            found_list, found_signers = ghpool.list_files()
        except config.NotConfigured as e:
            return R(blind(f"unsigned: no signed list: {e}"))
        list_file, signers_file = list_file or found_list, signers_file or found_signers
    try:
        sets, _ = fetchproxy.load_host_sets(host_sets_file or fetchproxy.HOST_SETS_FILE)
        verdicts = ratify.verify_list(list_file, signers_file, sets)         # ONCE per query
    except (OSError, ValueError) as e:          # BadSigners is a ValueError
        return R(blind(f"unsigned: list or signers unusable: {e}"))
    pin = ghpool.trust_pin(trust_pin_value if trust_pin_value is not None
                           else config.configured(ghpool.TRUST_PIN_KEY))
    if pin is None or pin[0] != ratify.REQUIRED_PRINCIPAL:
        return R(blind(f"unsigned: {ghpool.TRUST_PIN_KEY} must be set to "
                       f"'{ratify.REQUIRED_PRINCIPAL}=<key id>' in the host's config"))
    use, refused = {}, []
    for v in verdicts:
        if not v.ok:
            refused.append(f"line {v.line_no}: {v.reason}")
        elif dict(zip(v.principals, v.key_ids)).get(pin[0]) != pin[1]:
            refused.append(f"line {v.line_no}: {pin[0]}'s key is not the host's pinned {pin[1]}")
        else:   # every line passed (any refused line blinds the pool below); the last for a pin wins
            use[(v.fields["org"], v.fields["repo"], v.fields["commit"])] = v
    if not use:
        return R(blind(f"unsigned: no verified line ({len(verdicts)} in the list)", *refused[:5]))
    if refused:   # review F2/F7: a refused line is never searched around; it would read as clean
        return R(blind(f"unsigned: {len(refused)} line(s) of the list refused; the pool is not searched "
                       "until the list verifies", *refused[:5]))
    members, repos = [], []
    for (org, repo, commit), v in sorted(use.items()):
        info = {"repo": f"{org}/{repo}@{commit}", "line": v.line_no, "files_read": 0}
        repos.append(info)
        try:
            members += _repo_members(v, root, LANGS[lang], info)
        except ghcache.PinMismatch as e:
            return R(blind(f"pin-mismatch: {org}/{repo}@{commit[:12]}: {e}"), repos=repos)
    ev = [f"{i['repo'][:-28]}: " + (i.get("left_out") or
                                     f"{i['members']} functions from {i['files_read']} files, "
                                     f"{i['unparsable']} unparsable, left out {i['excluded']}")
          for i in repos] + [f"not used: {r}" for r in refused[:5]]
    ctl = _control_member(members)
    if ctl is None:
        return R(blind("control-failed: no pool member long enough to plant", *ev),
                 control=blind("control-failed: no pool member long enough to plant"), repos=repos)
    sc = _memo_score()
    c = shape_lens.rank(ctl["body"], members, pool=POOL, trust=TRUST, scorer=sc)
    ok = (c.outcome.kind == FOUND and c.hits and c.hits[0].item_id == ctl["item_id"]
          and c.hits[0].score == 1.0)
    if not ok:
        why = f"control-failed: planted {ctl['item_id']} came back {c.outcome.label()}"
        return R(blind(why, *ev), control=blind(why), repos=repos)
    control = Outcome(FOUND, "", (f"planted {ctl['item_id']} came back first at J=1.0",))
    out = shape_lens.rank(query, members, pool=POOL, trust=TRUST, scorer=sc)
    return R(Outcome(out.outcome.kind, out.outcome.reason, out.outcome.evidence + tuple(ev)),
             out.hits, out.looked_at, control, repos)
