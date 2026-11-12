"""`forge-gate scout <file>`, slice 1 (design v1.4.5 §5, build step 5): advisory only.

Python files; the corpus pool; the shape lens. No verdict, no `learn`, no registry write,
and no pool or query code executed: bodies are read as text. Every check ends FOUND,
LOOKED-CLEAN or BLIND(reason) (P1), and every run carries a control that must come back
FOUND.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import behaviour, box, corpus, rule2, shape, shape_lens
from .outcomes import BLIND, FOUND, blind, clean, found

# §4a.4 rule 1: n is fixed BEFORE the query as the similarity lenses that apply to the
# pool. For the corpus pool: shape + language meaning (they read the same bytes and count
# once together), behaviour, intent. Only shape is built in slice 1.
CORPUS_LENSES = ("shape (+ language meaning, counted once)", "behaviour", "intent")
OTHER_POOLS = (("library", "signed"), ("registry", "tier"), ("github", "raw"))
LANG_BY_SUFFIX = {".py": "python"}


def _control_member(members):
    """A deterministic pool member long enough to shingle: the run's planted case."""
    for m in sorted(members, key=lambda m: m["item_id"]):
        if len(shape.normalize(m["body"])) >= 40:
            return m
    return None


def run(path: Path, export_root: Path | None = None) -> dict:
    data = Path(path).read_bytes()
    lang = LANG_BY_SUFFIX.get(Path(path).suffix)
    rep = {"file": str(path), "language": lang or "unknown",
           "sha256": hashlib.sha256(data).hexdigest(), "checks": {}, "related": {}, "change": None}
    checks = rep["checks"]

    # Rule 2: the lens is tested before it is trusted, on every run.
    fails = rule2.evaluate(shape_lens.score, shape_lens.TAU, shape_lens.keyword_score)
    checks["rule2-shape"] = (found(f"{len(fails)} case(s) failed", *fails) if fails
                             else clean("all known cases hold; lens may be switched on"))

    intake = corpus.read_export(export_root)
    checks["corpus-intake"] = intake.outcome
    if intake.outcome.kind == FOUND:
        checks["corpus-sha-recheck"] = clean(f"every file of {intake.run_id} matches its manifest")
        checks["corpus-unreadable-items"] = (
            found(f"{intake.unreadable} unreadable, over 10%: run flagged") if intake.flagged
            else clean(f"{intake.unreadable} unreadable"))
    elif intake.outcome.reason.startswith("sha-recheck"):
        checks["corpus-sha-recheck"] = found(intake.outcome.reason)
    else:
        checks["corpus-sha-recheck"] = blind("no export read")

    try:
        query = data.decode("utf-8")
    except UnicodeDecodeError:
        query = None

    members = [m for m in intake.items if m.get("lang") == lang] if lang else []
    if fails:
        shape_out = shape_lens.LensResult(blind("rule2-failed: lens not switched on"))
    elif intake.outcome.kind != FOUND:
        shape_out = shape_lens.LensResult(blind(f"corpus: {intake.outcome.reason}"))
    elif lang is None:
        shape_out = shape_lens.LensResult(blind(f"language: {Path(path).suffix or 'none'} not in slice 1 (python only)"))
    elif query is None:
        shape_out = shape_lens.LensResult(blind("unreadable: not UTF-8"))
    else:
        ctl = _control_member(members)
        if ctl is None:
            checks["control"] = blind("no pool member long enough to plant")
            shape_out = shape_lens.LensResult(blind("control could not be planted"))
        else:
            c = shape_lens.rank(ctl["body"], members, pool=corpus.POOL, trust=corpus.TRUST)
            ok = c.outcome.kind == FOUND and c.hits and c.hits[0].score == 1.0
            checks["control"] = (found(f"planted {ctl['item_id']} came back at J=1.0") if ok
                                 else blind(f"control-failed: planted {ctl['item_id']} came back {c.outcome.label()}"))
            shape_out = (shape_lens.rank(query, members, pool=corpus.POOL, trust=corpus.TRUST) if ok
                         else shape_lens.LensResult(blind("control-failed")))
    if "control" not in checks:
        checks["control"] = blind("not run: " + shape_out.outcome.label())

    # Behaviour (build step 6a): runs code, so only inside the isolation box, and only after its
    # own Rule-2 set passes on this run.
    beh = behaviour.BehaviourResult(blind("not run"))
    why_box = box.available()
    if why_box:
        checks["rule2-behaviour"] = blind(why_box)
        beh = behaviour.BehaviourResult(blind(why_box))
    else:
        bfails = behaviour.rule2()
        checks["rule2-behaviour"] = (found(f"{len(bfails)} case(s) failed", *bfails) if bfails
                                     else clean("all known cases hold; lens may be switched on"))
        if bfails:
            beh = behaviour.BehaviourResult(blind("rule2-failed: lens not switched on"))
        elif intake.outcome.kind != FOUND:
            beh = behaviour.BehaviourResult(blind(f"corpus: {intake.outcome.reason}"))
        elif lang != "python" or query is None:
            beh = behaviour.BehaviourResult(blind("query: not readable Python"))
        else:
            beh = behaviour.rank(query, members, pool=corpus.POOL, trust=corpus.TRUST)
    lenses = {"shape": shape_out.outcome, "behaviour": beh.outcome,
              "intent": blind("not-built (build step 8b)")}
    agree = sum(1 for o in lenses.values() if o.kind == FOUND)
    n_blind = sum(1 for o in lenses.values() if o.kind == BLIND)
    rep["related"]["corpus"] = {
        "export": intake.run_id, "lenses": lenses, "n": len(CORPUS_LENSES), "agree": agree, "blind": n_blind,
        "shape_hits": [vars(h) for h in shape_out.hits], "looked_at": shape_out.looked_at,
        "behaviour_hits": [vars(h) for h in beh.hits], "behaviour_gates": beh.gates,
        "skipped": [blind(f"newer export {r} not used: {w}") for r, w in intake.skipped]}
    for pool, trust in OTHER_POOLS:
        rep["related"][pool] = {"trust": trust, "outcome": blind("pool not in slice 1")}
    rep["change"] = blind("no baseline: not built in slice 1")
    return rep


def _lbl(o):
    return o.label() + (f"  {'; '.join(map(str, o.evidence))}" if o.evidence else "")


def render(rep: dict) -> str:
    out = ["FORGE-GATE SCOUT REPORT  —  no verdict, advisory only",
           "=" * 54, "",
           f"file:    {rep['file']}  ({rep['language']})",
           f"sha256:  {rep['sha256']}", "", "CHECKS"]
    for name, o in rep["checks"].items():
        out.append(f"  {name:<24} {_lbl(o)}")
    out.append("RELATED")
    c = rep["related"]["corpus"]
    out.append(f"  corpus (raw), export {c['export'] or '-'}: {c['agree']} of {c['n']} lenses agree, {c['blind']} BLIND")
    for o in c["skipped"]:   # 6E review S1-2: "newest usable", never silently "newest"
        out.append(f"    skipped    {_lbl(o)}")
    for name, o in c["lenses"].items():
        out.append(f"    {name:<10} {_lbl(o)}")
    for h in c["shape_hits"]:
        out.append(f"      J={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']} kind={h['kind']}  reason: shape shingles")
    for h in c.get("behaviour_hits", []):   # 6E 6a pre-note: the pair is named, so it can be re-run
        out.append(f"      B={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']} kind={h['kind']}  "
                   f"reason: same outputs on {h['agree']}/{h['considered']} inputs ({h['query_fn']} vs {h['cand_fn']})")
    for pool in ("library", "registry", "github"):
        r = rep["related"][pool]
        out.append(f"  {pool} ({r['trust']}): {r['outcome'].label()}")
    out.append(f"CHANGE   {rep['change'].label()}")
    out += ["", "NO VERDICT. This report tells you what the code DOES."]
    return "\n".join(out)


def to_json(rep: dict) -> str:
    def enc(o):
        if hasattr(o, "label"):
            return {"outcome": o.kind, "reason": o.reason, "label": o.label(), "evidence": list(o.evidence)}
        raise TypeError(type(o))
    return json.dumps(rep, default=enc, indent=1, ensure_ascii=False)


def check_report_outcomes(text: str) -> list[str]:
    """§10.2 Rule 6 checker: every check/lens/pool line carries one of the three outcomes,
    and no line prints an empty result."""
    bad = []
    in_block = False
    for line in text.splitlines():
        if line in ("CHECKS", "RELATED"):
            in_block = True
            continue
        if line.startswith("CHANGE"):
            in_block = False
            if not any(k in line for k in ("FOUND", "LOOKED-CLEAN", "BLIND(")):
                bad.append(line)
            continue
        if not in_block or not line.strip() or line.lstrip().startswith(("J=", "B=")):
            continue
        if line.strip().endswith(":") or "no related" in line.lower() or line.rstrip().endswith("[]"):
            bad.append(line)
        elif not any(k in line for k in ("FOUND", "LOOKED-CLEAN", "BLIND(")) and "lenses agree" not in line:
            bad.append(line)
    return bad
