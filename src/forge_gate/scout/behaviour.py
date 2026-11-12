"""The behaviour lens (design v1.4.5 §4a.3 lens 2, build step 6a): same inputs -> same outputs.

Pipeline for a query file against a pool, each stage a gate that filters and never votes:
  1. signature (§3a, text only) -> the query's and each member's public functions;
  2. environment fit (Q11): every import must resolve INSIDE the box (facts measured there);
  3. contract: the candidate accepts the query's required arguments, kinds agree where annotated;
  4. behaviour, in the isolation box: query at /src, candidates at /ref (read-only, 6E pre-note),
     each pair in a FRESH interpreter; inputs = the ported executor's baseline + attack fixtures
     (engines/executor_port.py) + seeded variants of the baseline. Score = agreeing inputs /
     inputs where at least one side returned (both raising says nothing about meaning).

Rule 2 (§4a.4) runs before the lens is trusted: see ``rule2()``.
Known limit, stated: a candidate runs in the same interpreter as its pair's harness, so it can
forge ITS OWN pair's result line; it cannot touch any other pair (fresh process per pair).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import box, contract, envfit, shape_lens, signature
from .outcomes import Outcome, blind, clean, found

NAME = "behaviour"
TAU = 0.9            # a hit agrees on at least 90% of the inputs where someone answered
MIN_CONSIDERED = 4   # fewer answered inputs than this: no score for the pair
TOP_K = 5
MAX_PAIRS = 80       # per query; more are reported, never silently dropped
SEED = 20261111
PORT = Path(__file__).parent / "engines" / "executor_port.py"

PAIR = r'''
import contextlib, copy, importlib.util, inspect, io, json, math, random, signal, sys
sys.path.insert(0, "/src")
import executor_port as X
WORDS = ["alpha", "beta", "gamma", "delta", "fox", "data", "x", "lorem", "zeta", "omega"]
class TO(Exception): pass
def _h(*a): raise TO()
signal.signal(signal.SIGALRM, _h)
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        signal.alarm(5); spec.loader.exec_module(m); signal.alarm(0)
    return m
def call(fn, args):
    try:
        signal.alarm(3)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = fn(*copy.deepcopy(args))
        signal.alarm(0); return ("ok", out)
    except BaseException as e:
        signal.alarm(0); return ("raise", type(e).__name__)
def eq(a, b, d=0):
    if d > 50: return False
    if isinstance(a, bool) or isinstance(b, bool): return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b): return True
        try: return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
        except Exception: return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(eq(x, y, d + 1) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(eq(a[k], b[k], d + 1) for k in a)
    try: return bool(a == b)
    except BaseException: return False
def variant(v, rnd):
    if isinstance(v, bool): return rnd.random() < 0.5
    if isinstance(v, int): return rnd.randint(-3, 20)
    if isinstance(v, float): return round(rnd.uniform(-10, 10), 3)
    if isinstance(v, str): return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 5)))
    if isinstance(v, list):
        if v and isinstance(v[0], list): return [[round(rnd.uniform(-5, 5), 3) for _ in v[0]] for _ in range(rnd.randint(1, 4))]
        if v and isinstance(v[0], int) and not isinstance(v[0], bool): return [rnd.randint(-5, 9) for _ in range(rnd.randint(1, 7))]
        return [round(rnd.uniform(-10, 10), 3) for _ in range(rnd.randint(1, 7))]
    if isinstance(v, tuple): return tuple(variant(x, rnd) for x in v)
    if isinstance(v, dict): return {k: variant(x, rnd) for k, x in v.items()}
    return v
def main():
    qpath, qfn, cpath, cfn, seed = sys.argv[1:6]
    out = {}
    try:
        q = getattr(load(qpath, "q_mod"), qfn); c = getattr(load(cpath, "c_mod"), cfn)
    except BaseException as e:
        out["error"] = "load: " + type(e).__name__; print("BEHAV " + json.dumps(out)); return
    params = [p for p in inspect.signature(q).parameters.values()
              if p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY) and p.default is p.empty]
    base, ok = X.build_baseline(params)
    if not ok:
        out["error"] = "no-baseline"; print("BEHAV " + json.dumps(out)); return
    inputs = [base] + [a for _r, a, _e, _i in X.attack_fixtures(params, base)]
    rnd = random.Random(int(seed))
    inputs += [[variant(v, rnd) for v in base] for _ in range(8)]
    agree = considered = 0
    for args in inputs:
        a, b = call(q, args), call(c, args)
        if a[0] == "raise" and b[0] == "raise":
            continue
        considered += 1
        agree += a[0] == b[0] == "ok" and eq(a[1], b[1])
    out.update(inputs=len(inputs), considered=considered, agree=agree)
    print("BEHAV " + json.dumps(out))
main()
'''

DRIVER = r'''
import importlib.util, json, subprocess, sys
job = json.load(open("/src/job.json"))
if job["mode"] == "facts":
    print("MOD " + json.dumps({n: importlib.util.find_spec(n) is not None for n in job["modules"]}))
    sys.exit(0)
for p in job["pairs"]:
    try:
        r = subprocess.run([sys.executable, "/src/pair.py", p["q"], p["qfn"], p["c"], p["cfn"], str(job["seed"])],
                           capture_output=True, text=True, timeout=30)
        line = next((l for l in r.stdout.splitlines() if l.startswith("BEHAV ")), None)
        res = json.loads(line[6:]) if line else {"error": "no-result, exit %s" % r.returncode}
    except subprocess.TimeoutExpired:
        res = {"error": "timeout"}
    print("PAIR " + json.dumps({"key": p["key"], **res}), flush=True)
'''


@dataclass
class BHit:
    score: float
    item_id: str
    pool: str
    trust: str
    kind: str
    query_fn: str
    cand_fn: str
    agree: int
    considered: int


@dataclass
class BehaviourResult:
    outcome: Outcome
    hits: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)     # item_id -> best pair score (every compared item)
    gates: dict = field(default_factory=dict)
    looked_at: int = 0


def _file(item_id: str) -> str:
    return "m_" + hashlib.sha1(item_id.encode()).hexdigest()[:16] + ".py"


def _box_job(job: dict, query: str | None, members: dict[str, str], timeout: float):
    root = config.scratch_dir() / "behaviour"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as d:
        src, ref = Path(d) / "src", Path(d) / "ref"
        src.mkdir(); ref.mkdir()
        (src / "pair.py").write_text(PAIR)
        (src / "driver.py").write_text(DRIVER)
        (src / "executor_port.py").write_bytes(PORT.read_bytes())
        (src / "job.json").write_text(json.dumps(job))
        if query is not None:
            (src / "query.py").write_text(query)
        for name, body in members.items():
            (ref / name).write_text(body)
        return box.run(src, ["python3", "/src/driver.py"], timeout=timeout, ref=ref)


def env_facts(modules: set[str]) -> tuple[dict | None, Outcome | None]:
    if not modules:
        return {}, None
    r = _box_job({"mode": "facts", "modules": sorted(modules)}, None, {}, 60)
    if r.outcome.kind != "FOUND":
        return None, r.outcome
    line = next((l for l in r.stdout.splitlines() if l.startswith("MOD ")), None)
    if line is None:
        return None, blind(f"environment-fit: no facts from the box (exit {r.returncode})")
    return json.loads(line[4:]), None


def rank(query: str, members: list, *, pool: str, trust: str) -> BehaviourResult:
    try:
        qsigs = signature.extract(query)
        q_imp = envfit.imports(query)
    except SyntaxError:
        return BehaviourResult(blind("query: not parseable as Python"))
    if not qsigs:
        return BehaviourResult(blind("query: no public top-level function with arguments"))
    if envfit.runs_at_import(query):
        return BehaviourResult(blind(f"query: runs code at import ({envfit.runs_at_import(query)}); "
                                     "behaviour compares functions, not scripts"))
    parsed, gates = [], {"unparseable": 0, "no-function": 0, "runs-at-import": 0, "env-fit": 0, "contract": 0}
    for m in members:
        try:
            sigs, imp = signature.extract(m["body"]), envfit.imports(m["body"])
        except SyntaxError:
            gates["unparseable"] += 1; continue
        if not sigs:
            if signature.async_only(m["body"]):   # 6E B6-3: not supported yet, not "no function"
                gates["async-skipped"] = gates.get("async-skipped", 0) + 1
            else:
                gates["no-function"] += 1
            continue
        if envfit.runs_at_import(m["body"]):
            gates["runs-at-import"] += 1; continue
        parsed.append((m, sigs, imp))
    facts, bad = env_facts(q_imp[0].union(*(imp[0] for _m, _s, imp in parsed)) if parsed else q_imp[0])
    if bad is not None:
        return BehaviourResult(bad, gates=gates)
    ok, why = envfit.fits(q_imp, facts)
    if not ok:
        return BehaviourResult(blind(f"environment-fit: the query does not run in the box ({why})"), gates=gates)
    pairs, files = [], {}
    for m, sigs, imp in parsed:
        if not envfit.fits(imp, facts)[0]:
            gates["env-fit"] += 1; continue
        mp = [(q, c) for q in qsigs for c in sigs if contract.fits(q, c)[0]]
        if not mp:
            gates["contract"] += 1; continue
        files[m["item_id"]] = _file(m["item_id"])
        for q, c in mp:
            pairs.append((m["item_id"], q["entry"], c["entry"], m))
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    gates["pairs"] = len(pairs)
    if len(pairs) > MAX_PAIRS:
        gates["over-cap"] = len(pairs) - MAX_PAIRS
        pairs = pairs[:MAX_PAIRS]
    if not pairs:
        return BehaviourResult(clean(f"no member passed the gates ({_gate_text(gates)})"), gates=gates,
                               looked_at=len(members))
    job = {"mode": "pairs", "seed": SEED, "pairs": [
        {"key": i, "q": "/src/query.py", "qfn": qf, "c": "/ref/" + files[iid], "cfn": cf}
        for i, (iid, qf, cf, _m) in enumerate(pairs)]}
    bodies = {files[iid]: m["body"] for iid, _q, _c, m in pairs}
    r = _box_job(job, query, bodies, 30 * len(pairs) + 60)
    if r.outcome.kind != "FOUND":
        return BehaviourResult(r.outcome, gates=gates, looked_at=len(members))
    results = {}
    for line in r.stdout.splitlines():
        if line.startswith("PAIR "):
            d = json.loads(line[5:]); results[d["key"]] = d
    best, errors, kinds, no_answer = {}, 0, {}, 0
    for i, (iid, qf, cf, m) in enumerate(pairs):
        d = results.get(i)
        if d is None or "error" in d:
            errors += 1
            k = (d or {}).get("error", "no-result")
            kinds[k] = kinds.get(k, 0) + 1
            continue
        if d["considered"] < MIN_CONSIDERED:   # 6E B6-1: "could not compare" is not "unrelated"
            no_answer += 1
            continue
        s = d["agree"] / d["considered"]
        if iid not in best or s > best[iid].score:
            best[iid] = BHit(round(s, 3), iid, pool, trust, m.get("kind", "?"), qf, cf, d["agree"], d["considered"])
    gates["pair-errors"] = errors
    gates["no-answer"] = no_answer
    if kinds:
        gates["error-kinds"] = "; ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])[:4])
    hits = sorted((h for h in best.values() if h.score >= TAU), key=lambda h: (-h.score, h.item_id))[:TOP_K]
    scores = {iid: h.score for iid, h in best.items()}
    text = f"{len(pairs)} pair(s) run, {_gate_text(gates)}"
    unlooked = errors + no_answer
    if unlooked == len(pairs):
        return BehaviourResult(blind(f"no pair could be compared ({text})"), [], scores, gates, len(members))
    if unlooked > 0.1 * len(pairs):   # P1: more than 10% of the run could not look -> the run is flagged
        text = f"FLAGGED: {unlooked} of {len(pairs)} pairs BLIND (over 10%); " + text
    out = found(f"{len(hits)} hit(s) at agreement >= {TAU}; {text}") if hits else clean(f"none at agreement >= {TAU}; {text}")
    return BehaviourResult(out, hits, scores, gates, len(members))


def _gate_text(g: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in g.items() if v)


# ---------------- Rule 2 (§4a.4): the known-case set, run before the lens is trusted ----------------
SQ_LOOP = """def sum_of_squares(xs):
    total = 0
    for x in xs:
        total += x * x
    return total
"""
SQ_GEN = """def energy(signal):
    return sum(s * s for s in signal)
"""
SUM_FORK = """def sum_of_squares(xs):
    total = 0
    for x in xs:
        total += x
    return total
"""
MEAN = """def mean(xs):
    return sum(xs) / len(xs)
"""
KW_TRAP = """def squares_total(nums: list) -> float:
    # squares total of nums: counts the nums, acc of squares not computed here
    acc = 0.0
    for n in nums:
        acc = acc + 1
    return acc
"""
POOL = {"sq_loop": SQ_LOOP, "sq_gen": SQ_GEN, "sum_fork": SUM_FORK, "mean": MEAN, "kw_trap": KW_TRAP}
Q_SQ = """def squares_total(nums: list) -> float:
    acc = 0.0
    for n in nums:
        acc = acc + n ** 2
    return acc
"""
Q_NULL = """def word_count(text: str) -> int:
    return len(text.split())
"""
RELATED = {"sq_loop", "sq_gen"}


def _pool():
    return [{"item_id": f"rule2:{k}", "body": v, "lang": "python", "kind": "rule2"} for k, v in POOL.items()]


def rule2(rank_fn=None) -> list[str]:
    """Each case says WHY it exists. Returns the failures; empty means the lens may be switched on."""
    rank_fn = rank_fn or rank
    fails = []
    def ids(r): return {h.item_id.split(":", 1)[1] for h in r.hits}
    def score(r, k): return r.scores.get(f"rule2:{k}", 0.0)
    r_ctl = rank_fn(SQ_LOOP, _pool(), pool="rule2", trust="fixture")
    if r_ctl.outcome.kind != "FOUND" or score(r_ctl, "sq_loop") != 1.0:
        fails.append(f"control: the query's own copy must agree 1.0 (got {score(r_ctl, 'sq_loop')}, {r_ctl.outcome.label()})")
    # same look, different meaning: shape matches the fork, behaviour must not
    if shape_lens.score(SQ_LOOP, SUM_FORK) < shape_lens.TAU:
        fails.append("case invalid: shape no longer matches the fork, so it tests nothing")
    if "sum_fork" in ids(r_ctl):
        fails.append(f"same-look/different-meaning: the buggy fork was returned (score {score(r_ctl, 'sum_fork')})")
    r = rank_fn(Q_SQ, _pool(), pool="rule2", trust="fixture")
    # same meaning, different look; only behaviour gets it (shape and keyword miss)
    if shape_lens.score(Q_SQ, SQ_GEN) >= shape_lens.TAU:
        fails.append("case invalid: shape already matches sq_gen, so behaviour is not the only lens")
    if "sq_gen" not in ids(r):
        fails.append(f"same-meaning/different-look: sq_gen missed (score {score(r, 'sq_gen')}, {r.outcome.label()})")
    if "sq_loop" not in ids(r):
        fails.append(f"same-meaning: sq_loop missed (score {score(r, 'sq_loop')})")
    if ids(r) - RELATED:
        fails.append(f"unrelated returned: {sorted(ids(r) - RELATED)} (keyword trap / mean / fork)")
    # keyword baseline: behaviour must separate related from unrelated better than keywords do
    rel = [score(r, k) for k in RELATED]
    unrel = [score(r, k) for k in POOL if k not in RELATED]
    kw = {k: shape_lens.keyword_score(Q_SQ, v) for k, v in POOL.items()}
    sep_b = min(rel) - max(unrel)
    sep_k = min(kw[k] for k in RELATED) - max(kw[k] for k in POOL if k not in RELATED)
    # 6E B6-2: the case must be able to fail: the keyword baseline has to MIS-rank (a trap above a
    # true match), otherwise "beaten" is undefined
    if not sep_k < 0:
        fails.append(f"case invalid: keyword baseline separation {sep_k:.2f} is not negative, so it ranks nothing wrongly")
    if not sep_b > sep_k:
        fails.append(f"keyword baseline not beaten: behaviour separation {sep_b:.2f} vs keyword {sep_k:.2f}")
    # null: a query with no related member returns nothing
    r0 = rank_fn(Q_NULL, _pool(), pool="rule2", trust="fixture")
    if r0.hits:
        fails.append(f"null: returned {sorted(ids(r0))}")
    return fails
