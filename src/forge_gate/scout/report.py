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

from .. import config, ledger
from . import behaviour, box, corpus, embed, experience, intent_lens, meaning_lens, rule2, shape, shape_lens
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


def run(path: Path, export_root: Path | None = None, intent: str | None = None) -> dict:
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
    mean = _meaning(query, intake, checks)
    # Intent (8b s3): the person's stated purpose vs each member's purpose text. The corpus export
    # carries none (DECISIONS-8b F8), so members reach the lens with no ``intent`` field: BLIND(no-input).
    if intake.outcome.kind == FOUND or not intent:
        inten = intent_lens.run_for(intent, intake.items, pool=corpus.POOL, trust=corpus.TRUST, checks=checks)
    else:                                         # 8b s3 review F5: the check row says why, on every path
        checks["rule2-intent"] = blind(f"not run: corpus: {intake.outcome.reason}")
        inten = intent_lens.LensResult(blind(f"corpus: {intake.outcome.reason}"))
    rep["intent"] = intent
    lenses = {"shape": shape_out.outcome, "meaning": mean.outcome, "behaviour": beh.outcome,
              "intent": inten.outcome}
    groups = {"shape+meaning": group_outcome(shape_out.outcome, mean.outcome), "behaviour": beh.outcome,
              "intent": lenses["intent"]}
    agree = sum(1 for o in groups.values() if o.kind == FOUND)
    n_blind = sum(1 for o in groups.values() if o.kind == BLIND)
    rep["related"]["corpus"] = {
        "export": intake.run_id, "lenses": lenses, "groups": groups, "n": len(CORPUS_LENSES), "agree": agree,
        "blind": n_blind, "shape_hits": [vars(h) for h in shape_out.hits], "looked_at": shape_out.looked_at,
        "meaning_hits": [vars(h) for h in mean.hits], "meaning_scorer": mean.scorer,
        "intent_hits": [vars(h) for h in inten.hits],
        "behaviour_hits": [vars(h) for h in beh.hits], "behaviour_gates": beh.gates,
        "skipped": [blind(f"newer export {r} not used: {w}") for r, w in intake.skipped]}
    for pool, trust in OTHER_POOLS:
        rep["related"][pool] = {"trust": trust, "outcome": blind("pool not in slice 1")}
    rep["change"] = blind("no baseline: not built in slice 1")
    checks["rule2-experience"], checks["experience"] = _experience(rep)
    checks["exposure"] = _expose(rep)
    return rep


def _experience(rep: dict):
    """Lens 6 (8b s5): evidence beside each shown hit, never a vote (DECISIONS-8b H8, H9, H11).
    Returns (its Rule-2 check, its outcome). Its Rule-2 set runs on every invocation, in memory."""
    fails = experience.rule2()
    r2 = (found(f"{len(fails)} case(s) failed: lens not switched on", *fails) if fails
          else clean("all known cases hold and every selection-rewarding variant is red"))
    if fails:
        return r2, blind("rule2-failed: lens not switched on")
    if not ledger.enabled():
        return r2, blind(f"ledger-off: {config.LEDGER_KEY} unset; no outcome history to show")
    try:
        v = ledger.view()
    except (ledger.LedgerError, config.NotConfigured) as e:
        return r2, blind(f"ledger-unreadable: {e}")
    # 8b s5 review F3 + re-check N1/N2: outcome rows that were written but not counted (refused or
    # orphaned) with none accepted would read as 0 outcomes, which is a lie. Exposure refusals do not
    # touch the counts, so they do not blind the lens.
    lost = ledger.uncounted_outcomes(v)
    if lost and not (v.marks or v.oracle):
        return r2, blind(f"ledger-rows-refused: {lost} outcome row(s) refused, torn or orphaned, none accepted; "
                         "no count is shown", *(f"{f}:{n} {why}" for f, n, why in v.refused[:5]),
                         *(f"outcome line {n}: torn (an append in flight, or left by a killed writer; later "
                           "writes are refused until it is repaired)" for n in ledger.torn_outcome_lines(v)[:5]))
    n, moved = experience.apply(rep["related"]["corpus"], v)
    notes = [f"{len(v.orphans)} orphan, {len(v.refused)} refused, {len(v.torn)} torn row(s) not counted"]
    if n:
        return r2, found(f"{n} shown hit(s) have outcomes; {moved} list(s) reordered inside score+trust ties", *notes)
    return r2, clean("no shown hit has an outcome yet", *notes)


def group_outcome(shape_o, meaning_o):
    """Rule 1 / DECISIONS-8b H5: shape and language meaning read the same bytes and count ONCE
    together. The group agrees if either is FOUND; it is BLIND only if both are BLIND."""
    if FOUND in (shape_o.kind, meaning_o.kind):
        return found("shape or meaning found", *(f"{n}: {o.label()}" for n, o in
                                                 (("shape", shape_o), ("meaning", meaning_o))))
    if shape_o.kind == BLIND and meaning_o.kind == BLIND:
        return blind("both shape and meaning blind")
    return clean("neither shape nor meaning found")


def _meaning(query, intake, checks) -> meaning_lens.LensResult:
    """Language meaning (build step 8b s2): its Rule-2 set runs on every invocation against the live
    embedder; only a scorer that passes every case is switched on (DECISIONS-8b H2)."""
    L = meaning_lens.LensResult
    try:
        emb = embed.get()
        scorer, tried = meaning_lens.choose(emb)
    except embed.EmbedError as e:
        checks["rule2-meaning"] = blind(str(e))
        return L(blind(str(e)))
    if not scorer:
        checks["rule2-meaning"] = found(
            "every scorer failed: lens not switched on",
            *(f"{sc}: {f}" for sc, fs in tried.items() for f in fs))
        return L(blind("rule2-failed: lens not switched on (" +
                       ", ".join(f"{sc} {len(fs)} case(s)" for sc, fs in tried.items()) + ")"))
    checks["rule2-meaning"] = clean(f"all known cases hold for scorer {scorer}; lens may be switched on")
    if intake.outcome.kind != FOUND:
        return L(blind(f"corpus: {intake.outcome.reason}"), scorer=scorer)
    if query is None:
        return L(blind("unreadable: not UTF-8"), scorer=scorer)
    try:
        cache = embed.read_cache(digest=emb.digest)     # the file this embedder's fill wrote
        ok, why = meaning_lens.control(intake.items, emb=emb, cache=cache, scorer=scorer)
        checks["control-meaning"] = found(why) if ok else blind(f"control-failed: {why}")
        if not ok:
            return L(blind(f"control-failed: {why}"), scorer=scorer)
        return meaning_lens.rank(query, intake.items, emb=emb, cache=cache, scorer=scorer,
                                 pool=corpus.POOL, trust=corpus.TRUST)
    except embed.EmbedError as e:
        return L(blind(str(e)), scorer=scorer)


def embed_fill(export_root=None) -> tuple[int, str]:
    """`forge-gate scout --embed-fill`: the ONLY writer of the vector cache (DECISIONS-8b H4). Fills
    the switched-on scorer's text of every corpus item; refuses while the lens's Rule-2 set is red."""
    try:
        emb = embed.get()
        scorer, tried = meaning_lens.choose(emb)
    except embed.EmbedError as e:
        return 2, f"embedder: {e}"
    if not scorer:
        return 2, ("the meaning lens is off (its Rule-2 set is red: " +
                   "; ".join(f"{sc}: {', '.join(fs)}" for sc, fs in tried.items()) + "): nothing to fill")
    intake = corpus.read_export(export_root)
    if intake.outcome.kind != FOUND:
        return 2, f"corpus: {intake.outcome.label()}"
    try:
        use = meaning_lens.comparable(intake.items, scorer)
        added, have = embed.fill([meaning_lens.lens_text(m["body"], scorer) for m in use], emb)
    except (embed.EmbedError, config.NotConfigured) as e:
        return 2, f"fill: {e}"
    return 0, (f"export {intake.run_id}, scorer {scorer}: {added} embedded, {have} already cached, "
               f"{len(intake.items) - len(use)} under {meaning_lens.MIN_WORDS} words skipped")


# Lens name in the ledger -> where its ranked hits sit in the report.
EXPOSED = (("shape", "shape_hits"), ("meaning", "meaning_hits"), ("behaviour", "behaviour_hits"),
           ("intent", "intent_hits"))
# Scout is advisory: it waits at most this long for another ledger writer (8a s2 review F5).
EXPOSE_WAIT = 2.0
# The comment a handed-out snippet carries (V41-2): `#` unless the language uses `//`.
SLASH_LANGS = frozenset({"c", "cpp", "javascript", "typescript", "go", "rust", "java"})


def _expose(rep: dict):
    """Writer ``scout-search`` (design §4a.5, build step 8a s2): every result this report SHOWS
    gets one exposure row and a result id. Advisory: a ledger that is off or cannot be written
    never changes the report's exit code; it shows as BLIND here (DECISIONS-8a G6)."""
    c = rep["related"].get("corpus", {})
    hits = [(h, {"lens": lens, "rank": rank, "score": h["score"], "pool": h["pool"], "trust": h["trust"],
                 "item": f"{h['pool']}:{h['item_id']}", "slot": h.get("slot", "top")})
            for lens, key in EXPOSED for rank, h in enumerate(c.get(key) or [], 1)]
    if not ledger.enabled():
        return blind(f"ledger-off: {config.LEDGER_KEY} unset; nothing recorded")
    if not hits:
        return clean("no result shown; nothing to record")
    try:
        qid, rows = ledger.record_exposure(query_sha256=rep["sha256"], query_lang=rep["language"],
                                           hits=[e for _, e in hits], wait=EXPOSE_WAIT)
    except (ledger.LedgerError, config.NotConfigured, OSError) as e:
        return blind(f"ledger-unwritable: {e}")
    for (h, _), row in zip(hits, rows):
        h["result_id"] = row["result_id"]
    return found(f"query {qid}: {len(rows)} result(s) recorded")


def show(rid: str) -> tuple[int, str]:
    """`forge-gate scout --show <result-id>`: hand out the code behind a result, with its result id
    as the first line (V41-2). Reads only; showing or copying never scores (§4a.5)."""
    if not ledger.RESULT_ID.fullmatch(rid or ""):
        return 2, f"{rid!r} is not a result id (fgr-<query>-<lens>-<rank>)"
    if not ledger.enabled():
        return 2, f"the ledgers are off ({config.LEDGER_KEY} unset): result ids exist only in a ledger"
    try:
        row = ledger.view().exposure.get(rid)
    except (ledger.LedgerError, config.NotConfigured) as e:
        return 2, f"cannot read the exposure ledger: {e}"
    if row is None:
        return 2, f"{rid} is not in this host's exposure ledger"
    pool, _, item_id = row["item"].partition(":")
    if pool != corpus.POOL:
        return 2, f"{rid}: pool {pool!r} is not handed out yet (8a: corpus only)"
    intake = corpus.read_export()
    if intake.outcome.kind != FOUND:
        return 2, f"{rid}: corpus {intake.outcome.label()}"
    m = next((i for i in intake.items if i["item_id"] == item_id), None)
    if m is None:
        return 2, f"{rid}: item {item_id} is not in the newest OK export {intake.run_id}"
    if not _id_matches(item_id, m["body"]):
        return 2, (f"{rid}: item {item_id} does not match its body's sha256 in export {intake.run_id}: "
                   "not handed out (the code might not be what was shown)")
    mark = "//" if m.get("lang") in SLASH_LANGS else "#"
    # An older id line inside the body (code handed out before, kept, and exported again) would
    # credit that older result too: drop it, so the snippet names exactly one result (review F1).
    lines = [ln for ln in m["body"].splitlines(keepends=True)
             if not ledger.RESULT_COMMENT.fullmatch(ln.rstrip("\r\n"))]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    head = f"{mark} forge-gate-result: {rid}\n"
    at = 1 if lines and lines[0].startswith("#!") else 0      # keep a shebang first (review F6)
    return 0, "".join(lines[:at] + [head] + lines[at:])


def _code_lines(text: str) -> list:
    """Non-blank lines, stripped of indentation and trailing space, id comment lines dropped."""
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ledger.RESULT_COMMENT.fullmatch(ln.rstrip("\r\n"))]


def snippet_missing(rid: str, text: str) -> str:
    """'' when the code behind ``rid`` is in ``text``, else why not (8a s3 review F1).

    An oracle outcome credits the CODE, so the id comment alone is not enough: the handed-out
    body's lines must appear in the file, contiguous, in order (indentation and blank lines may
    differ, an edited line may not). Corpus items only; anything else cannot be checked."""
    try:
        row = ledger.view().exposure.get(rid)
    except (ledger.LedgerError, config.NotConfigured) as e:
        return f"cannot read the exposure ledger: {e}"
    if row is None:
        return f"{rid} is not in this host's exposure ledger"
    pool, _, item_id = row["item"].partition(":")
    if pool != corpus.POOL:
        return f"pool {pool!r}: cannot check that its code is in the file"
    intake = corpus.read_export()
    if intake.outcome.kind != FOUND:
        return f"cannot check that the code is in the file: corpus {intake.outcome.label()}"
    m = next((i for i in intake.items if i["item_id"] == item_id), None)
    if m is None or not _id_matches(item_id, m["body"]):
        return f"cannot check that the code is in the file: item {item_id} not readable in {intake.run_id}"
    want, have = _code_lines(m["body"]), _code_lines(text)
    if not want:
        return f"item {item_id} has no code lines"
    n = len(want)
    if any(have[i:i + n] == want for i in range(len(have) - n + 1)):
        return ""
    return f"the code of {item_id} is not in the file (the id comment alone does not count)"


def _id_matches(item_id: str, body: str) -> bool:
    """A corpus item id is ``c_`` + the first 16 hex of sha256(body) (CODE-CORPUS-DESIGN §3; measured
    on export 20261115T102505Z: 1431 of 1431)."""
    return item_id == "c_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _tail(h) -> str:
    """What every hit line ends with: its slot label, its experience counts, its result id."""
    t = "  [explore: outside the top ranks]" if h.get("slot") == "explore" else ""
    if h.get("_explore_none"):
        t += f"  [explore: none, only {h['_explore_none']} ranked, all shown]"
    e = h.get("experience")
    if e:
        t += (f"  experience: oracle {e['oracle']} · with-id {e['with_id']} · without-id {e['without_id']}"
              f" · pending {e['pending']}")
    return t + (f"  id={h['result_id']}" if h.get("result_id") else "")


def _noted(hits) -> list:
    """Copies of one shown list. A list with no explore hit says so on its last line (DECISIONS-8b
    H10: a lens with too few ranked members has no explore slot AND SAYS SO; 8b crew review, 6E).
    Copies, so rendering never changes the report."""
    out = [dict(h) for h in hits or []]
    if out and not any(h.get("slot") == "explore" for h in out):
        out[-1]["_explore_none"] = len(out)
    return out


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
    for h in _noted(c["shape_hits"]):
        out.append(f"      J={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']} kind={h['kind']}  reason: shape shingles"
                   + _tail(h))
    for h in _noted(c.get("meaning_hits")):
        out.append(f"      M={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']} lang={h['lang']}  "
                   f"reason: same meaning ({c.get('meaning_scorer')} embedding)"
                   + _tail(h))
    for h in _noted(c.get("intent_hits")):
        out.append(f"      I={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']}  "
                   "reason: same stated purpose (intent embedding)" + _tail(h))
    for h in _noted(c.get("behaviour_hits")):   # 6E 6a pre-note: the pair is named, so it can be re-run
        out.append(f"      B={h['score']:.2f}  {h['item_id']}  pool={h['pool']} trust={h['trust']} kind={h['kind']}  "
                   f"reason: same outputs on {h['agree']}/{h['considered']} inputs ({h['query_fn']} vs {h['cand_fn']})"
                   + _tail(h))
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
        if not in_block or not line.strip() or line.lstrip().startswith(("J=", "B=", "M=", "I=")):
            continue
        if line.strip().endswith(":") or "no related" in line.lower() or line.rstrip().endswith("[]"):
            bad.append(line)
        elif not any(k in line for k in ("FOUND", "LOOKED-CLEAN", "BLIND(")) and "lenses agree" not in line:
            bad.append(line)
    return bad
