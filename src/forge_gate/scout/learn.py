"""`forge-gate learn <file> --author=<seat>` (design v1.4.5 §3 learn pipeline, §4.5, §4.4.1;
build steps 6b-1 and 6b-2, decisions L1-L4 in DECISIONS-6b-learn-20261112.md, the maintainer 2026-11-13).

6b-1 built the plumbing. 6b-2 wires ONE engine, chaos (L3). Only chaos can write a PASS card:

- The built-in chaos engine (:func:`chaos_engine`, ``engines/chaos.py``) gives PASS or FAIL
  only where the validator's ``chaos_scan.py`` gives PASS or INVALID-CANDIDATE on the same bytes.
  This is parity-tested against the validator (tests/test_scout_chaos_parity.py). Everything
  else is BLIND:
  - a file not byte-identical to a probed library file;
  - a race/timing probe;
  - a negative-control gate that did not pass cleanly;
  - a harness error;
  - a function the probe did not drive.
- Every other kind is ``BLIND(not wired)``. Metamorphic is wired later, the same way.
- :func:`_check` admits PASS only for kind ``chaos`` and only from the built-in chaos engine
  object. Any other engine, built in or passed in by a caller, may return FAIL or BLIND only. Any
  other verdict stops the run before anything is written (exit 5).

The pipeline, in order:

1. SOURCE CAPTURE. Read the file's bytes, take their sha256.
2. POOL. FOR NOW (L2, a temporary limit, not the design) the file must be a member of the
   readable corpus export: its sha256 equals the sha256 of one item's body (UTF-8) in the
   newest OK export, which ``corpus.read_export`` has re-checked against its manifest. Any
   other file reports ``BLIND(not in a pool)`` and writes nothing. A local author file or a
   GitHub file (step 7) needs its own pool and an id decision first (KNOWN-DEBT KD-11).
3. SIGNATURE. Python only (``signature.extract``); other languages are BLIND.
4. BATTERY. Every engine in :data:`ENGINES` runs on every function (6b-2: chaos wired; the
   rest BLIND(not wired)).
5. SURVIVAL GATE. A function with no engine verdict gets no row and no card.
6. APPEND + CARDS. Rows go to scout's OWN ladder files in the Brain's data folder,
   ``$FORGE_GATE_BRAIN_DATA/scout_registry{,_r,_rust,_sysadmin}.jsonl`` (6E's decision A,
   2026-11-13, for review finding F3). Only learn writes them; the Brain reads them after its
   real ladders, so no extractor, reconcile or regate run can delete a learned row. learn never
   writes a real ladder file: it reads all eight files for the duplicate check. All of it runs
   under ``scout_learn.lock`` (learn-vs-learn singleton, LOCK_NB, fails fast) AND the registry
   build's own lock (L1: the hourly build reads these files and rewrites the real ladders in
   place). The build lock is waited for, then learn fails loudly. Rows whose id is already in any
   of the eight files are skipped and reported, never duplicated. Cards go to ``$FORGE_GATE_SCOUT_DIR/learn_report.json`` through
   ``cards.stage_report`` (write_report's checks and temp file), so every check runs before
   the registry changes; then the rows are appended and the report swapped in. A failure
   after the append cuts the registry back to its old size: nothing is kept.

learn does not ratify (§4.3) and does not promote: honest_pass derives tier, basis and
is_axiom; a learned row carries their initial values (``candidate``) only.

Exit codes: 0 ran (rows may be 0), 2 nothing learnable (BLIND input), 3 a lock is held,
4 not configured / a precondition failed, 5 refused (an engine verdict 6b-1 does not admit).
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import config
from . import cards, corpus, ghpool, signature
from .engines import chaos
from .ids import BadScoutId, make_id
from .outcomes import FOUND

EXIT_OK, EXIT_BLIND, EXIT_LOCK, EXIT_CONFIG, EXIT_REFUSED = 0, 2, 3, 4, 5

LEARN_LOCK_NAME = "scout_learn.lock"
REPORT_NAME = "learn_report.json"
STAGE = "6b-2"

# Verdicts any engine's card may carry: FAIL only (the maintainer L3, 2026-11-13) ...
CARD_VERDICTS = frozenset({"FAIL"})
# ... except these, for one kind, and only from that kind's BUILT-IN engine (parity-tested).
PASS_KINDS = frozenset({"chaos"})
# Verdicts the deployed reader accepts (Brain 6be79ec, honest_pass.VERDICTS, read 2026-11-13).
# Any other verdict on a card makes the whole registry build raise, every hour.
READER_VERDICTS = frozenset({"PASS", "FAIL", "FLAG", "RATIFIED"})
assert "PASS" not in CARD_VERDICTS and "RATIFIED" not in CARD_VERDICTS
assert CARD_VERDICTS | {"PASS"} <= READER_VERDICTS & cards.VERDICTS
assert PASS_KINDS == {chaos.KIND}

# Scout's own ladder files (6E decision A, 2026-11-13, ANSWER-6E-F3.md): language -> file in
# the Brain's data folder. Only learn writes them; honest_pass.merge_registries reads them AFTER
# the real ladders, so a real row wins a duplicate id. They follow the real ladders' language split.
ROUTE = {"python": "scout_registry.jsonl", "javascript": "scout_registry.jsonl",
         "go": "scout_registry.jsonl", "cpp": "scout_registry.jsonl",
         "r": "scout_registry_r.jsonl",
         "rust": "scout_registry_rust.jsonl",
         **{k: "scout_registry_sysadmin.jsonl"
            for k in ("shell", "bash", "json", "toml", "yaml", "plist", "systemd", "ini")}}
SCOUT_FILES = tuple(dict.fromkeys(ROUTE.values()))
# The Brain's real ladders (paths.LOGIC_REGISTRY, _R, RUST, SYSADMIN; all in paths.DATA). learn
# only READS them, for the duplicate check: their extractors rebuild them wholesale (review F3).
REAL_LADDERS = ("logic_registry.jsonl", "logic_registry_r.jsonl", "rust_registry.jsonl",
                "sysadmin_registry.jsonl")
assert len(SCOUT_FILES) == 4 and not set(SCOUT_FILES) & set(REAL_LADDERS)
assert all(f.startswith("scout_registry") for f in SCOUT_FILES)
# The sphere tags the live canonical uses (PY, R, RUST, SYS read 2026-11-13); JS/GO/CPP have no
# rows yet (§4.4.0) and take the design's short tags.
SPHERE = {"python": "PY", "javascript": "JS", "go": "GO", "cpp": "CPP", "r": "R", "rust": "RUST"}
SIGNATURE_LANGS = frozenset({"python"})
_AUTHOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")


class LearnError(RuntimeError):
    code = EXIT_CONFIG


class LockBusy(LearnError):
    code = EXIT_LOCK


class Refused(LearnError):
    code = EXIT_REFUSED


@dataclass(frozen=True)
class EngineResult:
    """One engine's answer for one function: a verdict, or BLIND(reason). Never both."""
    kind: str
    verdict: Optional[str] = None
    blind_reason: str = ""
    technique: str = ""
    fixture: str = ""
    expected: str = ""
    actual: str = ""
    why: Optional[str] = None
    severity: str = "info"
    card_class: Optional[str] = None

    def label(self) -> str:
        return self.verdict if self.verdict is not None else f"BLIND({self.blind_reason})"


Engine = Callable[[dict, dict, str], EngineResult]   # (corpus item, signature, scout id)


def _not_wired(kind: str) -> Engine:
    def engine(_item: dict, _sig: dict, _sid: str) -> EngineResult:
        return EngineResult(kind, blind_reason="not wired")
    engine.__name__ = f"{kind}_not_wired"
    return engine


def chaos_engine(item: dict, sig: dict, _sid: str) -> EngineResult:
    """The validator's chaos verdict for this function, or BLIND (engines/chaos.py has the rules)."""
    body = item["body"].encode("utf-8")
    probe = chaos.probe_for(body)
    if probe is None:
        return EngineResult(chaos.KIND, blind_reason="no validator chaos probe for these bytes")
    if not chaos.LEARN_LIBRARY_COPIES:
        return EngineResult(chaos.KIND, blind_reason=f"library copy ({probe}): its evidence is the "
                                                     "library's own, not new evidence (KD-12 j)")
    if probe in chaos.RACE:
        return EngineResult(chaos.KIND, blind_reason=f"race/timing probe {probe}: parity with "
                                                     "the validator not shown")
    try:
        verdict, card = chaos.decide(chaos.result_for(body, probe), probe, sig["entry"])
        chaos.check_host()            # a host whose NCs ever survived gives no chaos verdict
    except chaos.Blind as e:
        return EngineResult(chaos.KIND, blind_reason=str(e))
    return EngineResult(chaos.KIND, verdict=verdict, technique=card["technique"],
                        fixture=card["fixture"], expected=card["expected"], actual=card["actual"],
                        why=card.get("note"), severity=card["severity"], card_class=card["class"])


# 6b-2: one engine per oracle kind; chaos is wired (parity-tested), the rest are not.
ENGINES: tuple = tuple((k, chaos_engine if k == chaos.KIND else _not_wired(k))
                       for k in sorted(cards.KINDS))
_BUILTIN = dict(ENGINES)


@dataclass
class Learned:
    code: int
    outcome: str
    report: dict = field(default_factory=dict)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def route(lang: str, data: Path) -> Path:
    """Scout's ladder file for ``lang`` in the Brain's data folder. A missing file fails loudly,
    naming it: learn never creates a registry file (the switch-on creates the four, empty,
    before the Brain's scout reader is deployed)."""
    if lang not in ROUTE:
        raise KeyError(lang)
    target = data / ROUTE[lang]
    if not target.is_file():
        raise LearnError(f"scout ladder file for {lang!r} does not exist: {target} "
                         "(learn never creates a registry file)")
    return target


def build_row(sid: str, *, lang: str, sig: dict, item: dict, export_run: str, src: Path,
              sha256: str, author: str, now: _dt.datetime, card_verdicts: list,
              github: "ghpool.Admitted | None" = None) -> dict:
    """A registry row in the §4.5 shape, with scout's initial values. honest_pass re-derives
    is_axiom, prior_is_axiom, basis and the whole ``oracle`` dict on every build; the values
    here are what it derives for a row no report card backs."""
    types = [c["type"] for c in sig["consumes"] if c.get("type")]
    if sig["produces"].get("type"):
        types.append(sig["produces"]["type"])
    entry = sig["entry"]
    if github is None:
        origin, who = f"crew-corpus item {item['item_id']}", "crew code"
        extraction = {"method": "scout", "src": str(src), "author": author,
                      "attested_at": now.isoformat().replace("+00:00", "Z"),
                      "pool": corpus.POOL, "corpus_item": item["item_id"],
                      "corpus_export": export_run, "corpus_file": item["file"],
                      "sha256": sha256, "stage": STAGE}
    else:   # §12.5 row metadata; no author (no single human claims the file)
        g = github
        origin, who = f"GitHub {g.source}@{g.commit[:12]} {g.path}", "open-source code"
        extraction = {"method": "scout-github", "src": f"github.com/{g.source}@{g.commit}/{g.path}",
                      "pool": ghpool.POOL, "license": f"{g.verdict.fields['licence_spdx']}@{g.commit}",
                      "ratified_by": sorted(g.verdict.key_ids), "repo_trust_score": g.trust,
                      "ingested_at": now.isoformat().replace("+00:00", "Z"),
                      "sha256": sha256, "stage": STAGE}
    return {
        "id": sid,
        "sphere": SPHERE.get(lang, "SYS"),
        "category": "scout",          # classifier not read yet (DECISIONS-6b "small choices")
        "signature": {"consumes": sig["consumes"], "produces": sig["produces"], "entry": entry},
        "post_conditions": [],
        "invariant_guards": [],
        "core_idea": f"{entry}: learned by scout from {origin}; no human summary",
        "belief_sentence": {"text": (f"{entry} is {who} scout has read; it survived the "
                                     "validator's chaos probe" if "PASS" in card_verdicts else
                                     f"{entry} is {who} scout has read; no oracle has backed it"),
                            "status": f"scout-{STAGE} (initial value; honest_pass derives the tier)"},
        "type_nouns_raw": list(dict.fromkeys(types)),
        # True only when a parity-tested engine ran the code green (a PASS card).
        "verified_run": "PASS" in card_verdicts,
        # §4.5: "weak" needs at least one PASS card (DECISIONS-6b); "none" silences the row
        # from fusion, which is right for a row nothing backs.
        "strength": "weak" if "PASS" in card_verdicts else "none",
        "is_axiom": False,
        "extraction": extraction,
        "oracle": {"backed": False, "kinds": [], "verdicts": {}, "tier": "candidate",
                   "checked_against": f"scout-{STAGE}", "stamp": now.date().isoformat()},
        "prior_is_axiom": False,
        "basis": "green-run-candidate",
    }


def _check(kind: str, r: EngineResult, engine=None) -> None:
    """The verdict gate. Runs on every engine result before anything is written."""
    if not isinstance(r, EngineResult) or r.kind != kind:
        raise Refused(f"engine {kind!r} returned {r!r}, not an EngineResult for its own kind")
    if r.verdict is None:
        if not r.blind_reason:
            raise Refused(f"engine {kind!r} returned neither a verdict nor a BLIND reason")
        return
    if r.blind_reason:
        raise Refused(f"engine {kind!r} returned a verdict AND a BLIND reason")
    if r.verdict in CARD_VERDICTS:
        return
    if r.verdict == "PASS" and kind in PASS_KINDS and engine is not None and engine is _BUILTIN[kind]:
        return
    raise Refused(f"engine {kind!r} returned {r.verdict!r}: step {STAGE} admits {sorted(CARD_VERDICTS)}, "
                  f"and PASS only for {sorted(PASS_KINDS)} from the built-in, parity-tested "
                  "engine. Nothing was written.")


def _ladder_ids(data: Path) -> dict:
    """id -> file, over the real ladders AND scout's own (honest_pass merges all eight, first
    writer wins, so an id in ANY of them would shadow or be shadowed)."""
    seen: dict = {}
    for f in [data / n for n in REAL_LADDERS + SCOUT_FILES]:
        if not f.is_file():
            continue
        with f.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    i = json.loads(line)["id"]
                except (ValueError, KeyError, TypeError):
                    raise LearnError(f"{f}:{n}: not a registry row (the registry build would fail "
                                     "on it too); nothing was written") from None
                seen.setdefault(i, f)
    return seen


def _singleton(scout_dir: Path) -> int:
    p = scout_dir / LEARN_LOCK_NAME
    try:
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o660)
    except OSError as e:
        raise LearnError(f"cannot open the learn lock {p}: {e}") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LockBusy(f"another `forge-gate learn` holds {p}: learn runs one at a time") from None
    return fd


def _build_lock(path: Path, wait: float) -> int:
    try:
        fd = os.open(path, os.O_RDONLY)   # never O_CREAT: config.registry_build_lock() says why
    except OSError as e:
        raise LearnError(f"cannot open the registry build lock {path}: {e}") from None
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LockBusy(f"the registry build holds {path}; waited {wait:g} s. "
                               "Nothing was written; run learn again after the build.") from None
            time.sleep(0.1)


def _append(target: Path, rows: list) -> None:
    with target.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                raise LearnError(f"{target} does not end in a newline: an append would glue "
                                 "two rows into one line. Nothing was written.")
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    with target.open("ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _cut_back(target: Path, size: int) -> None:
    """Undo learn's own append: the file goes back to the size it had before it."""
    with target.open("r+b") as fh:
        fh.truncate(size)
        fh.flush()
        os.fsync(fh.fileno())


def _commit_report(tmp: Path, report_path: Path) -> None:
    os.replace(tmp, report_path)


def run(path, author: str | None, *, engines=None, lock_wait: float = 10.0,
        export_root: Path | None = None, now: _dt.datetime | None = None,
        github: str | None = None, gh_opts: dict | None = None) -> Learned:
    """Learn one file. ``engines`` is for tests; the CLI always uses :data:`ENGINES`.

    ``github="org/repo@<commit>"`` (step 7, G5): ``path`` is then a path INSIDE that pinned repo,
    read from the private cache through ghpool's gate. A GitHub row has no author, so ``author``
    must be None. ``gh_opts`` (list/signer files, cache root, host sets) is for tests only."""
    engines = ENGINES if engines is None else tuple(engines)
    now = now or _now()
    rep: dict = {"file": str(path), "author": author, "stage": STAGE, "rows_appended": 0,
                 "cards_written": 0, "duplicates": [], "functions": []}

    def done(code, outcome):
        rep["outcome"] = outcome
        return Learned(code, outcome, rep)

    if github is not None:
        if author is not None:
            return done(EXIT_CONFIG, "--author is for crew code; a GitHub file has no single author (§12.5)")
    elif not isinstance(author, str) or not _AUTHOR.match(author):
        return done(EXIT_CONFIG, f"--author must be a seat handle ([A-Za-z0-9._@-], 1-64): {author!r}")
    try:
        bdir = config.brain_data()
        sdir = config.scout_dir()
        lock_path = config.registry_build_lock()
    except config.NotConfigured as e:
        return done(EXIT_CONFIG, f"not configured: {e}")
    rep["brain_data"], rep["scout_dir"], rep["build_lock"] = str(bdir), str(sdir), str(lock_path)

    try:
        single = _singleton(sdir)
    except LearnError as e:
        return done(e.code, str(e))
    try:
        if github is not None:
            return _run_locked(path, author, engines, lock_wait, export_root, now,
                               bdir, sdir, lock_path, rep, done, github, gh_opts or {})
        return _run_locked(Path(path), author, engines, lock_wait, export_root, now,
                           bdir, sdir, lock_path, rep, done)
    finally:
        os.close(single)


def _run_locked(path, author, engines, lock_wait, export_root, now, bdir, sdir, lock_path,
                rep, done, github=None, gh_opts=None):
    if github is not None:
        # 2. POOL: GitHub (step 7, G5). L2's "for now" is lifted for this pool only.
        try:
            adm = ghpool.admit(github, str(path), **gh_opts)
        except ghpool.NotAdmitted as e:
            return done(EXIT_BLIND, f"BLIND({e})")
        rep["sha256"] = hashlib.sha256(adm.data).hexdigest()
        rep["pool"] = {"pool": ghpool.POOL, "repo": adm.source, "commit": adm.commit, "file": adm.path,
                       "lang": adm.lang, "licence": adm.verdict.fields["licence_spdx"],
                       "ratified_by": sorted(adm.verdict.key_ids), "trust": adm.trust}
        return _battery(adm.item(), adm.lang, ghpool.POOL, adm.source, adm.commit, path, author, engines,
                        lock_wait, now, bdir, sdir, lock_path, rep, done, github=adm,
                        repro=f"forge-gate learn --github {adm.source}@{adm.commit} {adm.path}")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    rep["sha256"] = sha

    # 2. POOL (L2, for now: corpus export members only; the GitHub pool is the branch above)
    intake = corpus.read_export(export_root)
    if intake.outcome.kind != FOUND:
        return done(EXIT_BLIND, f"BLIND(not in a pool: corpus {intake.outcome.label()})")
    members = [i for i in intake.items
               if hashlib.sha256(i["body"].encode("utf-8")).hexdigest() == sha]
    if not members:
        return done(EXIT_BLIND, f"BLIND(not in a pool: sha256 matches no item of corpus export "
                                f"{intake.run_id})")
    members.sort(key=lambda i: i["item_id"])
    item = members[0]
    rep["pool"] = {"pool": corpus.POOL, "export": intake.run_id, "item_id": item["item_id"],
                   "lang": item.get("lang"), "file": item.get("file"),
                   "also": [m["item_id"] for m in members[1:]]}
    lang = item.get("lang") or ""
    if not item.get("file"):
        return done(EXIT_BLIND, f"BLIND(corpus item {item['item_id']} has no file path for its id)")
    return _battery(item, lang, corpus.POOL, item["item_id"], intake.run_id, path, author, engines,
                    lock_wait, now, bdir, sdir, lock_path, rep, done,
                    repro=f"forge-gate learn <corpus {intake.run_id} item {item['item_id']}> --author={author}")


def _battery(item, lang, pool, source, run_id, path, author, engines, lock_wait, now, bdir, sdir,
             lock_path, rep, done, *, github=None, repro: str):
    """Steps 3-6, the same for every pool: signature, battery, survival gate, append + cards."""
    try:
        target = route(lang, bdir)
    except KeyError:
        return done(EXIT_BLIND, f"BLIND(language {lang!r} has no ladder file in the routing table)")
    except LearnError as e:
        return done(e.code, str(e))
    rep["target"] = str(target)

    # 3. SIGNATURE
    if lang not in SIGNATURE_LANGS:
        return done(EXIT_BLIND, f"BLIND(no signature extractor for {lang!r} yet)")
    try:
        sigs = signature.extract(item["body"])
    except SyntaxError as e:
        return done(EXIT_BLIND, f"BLIND(not parseable as {lang}: {e.msg})")
    if not sigs:
        return done(EXIT_OK, "LOOKED-CLEAN(no public top-level function with a positional argument)")

    # 4-5. BATTERY + SURVIVAL GATE (nothing is written until every result passed the gate)
    rows, new_cards = [], []
    last = {sig["entry"]: n for n, sig in enumerate(sigs)}
    for n, sig in enumerate(sigs):
        try:
            sid = make_id(pool, source, run_id, item["file"], sig["entry"])
        except BadScoutId as e:
            return done(EXIT_BLIND, f"BLIND(no honest id for {sig['entry']}: {e})")
        fn = {"id": sid, "entry": sig["entry"], "engines": {}}
        rep["functions"].append(fn)
        if last[sig["entry"]] != n:   # Python binds the LAST def of a name (6b-1 review F6)
            fn["row"] = "shadowed by a later def of the same name: skipped"
            rep["duplicates"].append(sid)
            continue
        results = []
        for kind, engine in engines:
            r = engine(item, sig, sid)
            try:
                _check(kind, r, engine)
            except Refused as e:
                return done(e.code, str(e))
            fn["engines"][kind] = r.label()
            results.append(r)
        verdicts = [r for r in results if r.verdict is not None]
        if not verdicts:
            fn["row"] = "no engine verdict: no row"
            continue
        fn["row"] = "pending"
        rows.append(build_row(sid, lang=lang, sig=sig, item=item, export_run=run_id,
                              src=Path(path).resolve() if github is None else Path(path), sha256=rep["sha256"],
                              author=author, now=now, card_verdicts=[r.verdict for r in verdicts],
                              github=github))
        for r in verdicts:
            new_cards.append(cards.make_card(
                sid, kind=r.kind, verdict=r.verdict, technique=r.technique or f"scout-{r.kind}",
                fixture=r.fixture, expected=r.expected, actual=r.actual, why=r.why,
                severity=r.severity, card_class=r.card_class,
                repro_cmd=repro))

    if not rows:
        return done(EXIT_OK, "0 rows: no function has an engine verdict "
                             f"(step {STAGE}: only chaos is wired, and it was BLIND)")

    # 6. APPEND + CARDS, under the registry build's own lock (L1)
    try:
        bfd = _build_lock(lock_path, lock_wait)
    except LearnError as e:
        return done(e.code, str(e))
    tmp = None
    try:
        have = _ladder_ids(bdir)
        report_path = sdir / REPORT_NAME
        old_cards = cards.load_report(report_path) if report_path.exists() else []
        keep_rows, keep_ids = [], set()
        for row in rows:
            if row["id"] in have:
                rep["duplicates"].append(row["id"])
                _fn(rep, row["id"])["row"] = f"id already in {have[row['id']].name}: skipped"
            else:
                keep_rows.append(row)
                keep_ids.add(row["id"])
        keep_cards = [c for c in new_cards if c["id"] in keep_ids]
        old_keys = {(c["id"], c["oracle_coverage_kind"]) for c in old_cards}
        clash = sorted({c["id"] for c in keep_cards if (c["id"], c["oracle_coverage_kind"]) in old_keys})
        if clash:
            raise LearnError(f"{report_path} already has cards for ids that have no row: {clash}. "
                             "Nothing was written; inspect the report.")
        if keep_rows:
            # Every card check and the report's own write happen BEFORE the registry changes;
            # a failure after the append cuts the registry back (review F2, F8).
            tmp = cards.stage_report(report_path, old_cards + keep_cards)
            size = target.stat().st_size
            try:
                _append(target, keep_rows)
                _commit_report(tmp, report_path)
                tmp = None
            except BaseException:
                _cut_back(target, size)
                raise
            rep["rows_appended"] = len(keep_rows)
            rep["cards_written"] = len(keep_cards)
            rep["report"] = str(report_path)
            for row in keep_rows:
                _fn(rep, row["id"])["row"] = f"appended to {target.name}"
    except cards.BadCard as e:
        return done(EXIT_CONFIG, f"{e}. Nothing was kept.")
    except LearnError as e:
        return done(e.code, str(e))
    except OSError as e:
        return done(EXIT_CONFIG, f"I/O error: {e}. Nothing was kept (registry cut back to its old size).")
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
        os.close(bfd)
    dup = len(rep["duplicates"])
    return done(EXIT_OK, f"{rep['rows_appended']} row(s) appended, {rep['cards_written']} card(s) "
                         f"written, {dup} duplicate id(s) skipped")


def _fn(rep: dict, sid: str) -> dict:
    return next(f for f in reversed(rep["functions"]) if f["id"] == sid)   # the last def is the live one


def render(res: Learned) -> str:
    r = res.report
    out = [f"forge-gate learn  {r['file']}  --author={r['author']}  (step {STAGE}: only chaos can PASS, parity-tested)"]
    if "sha256" in r:
        out.append(f"  sha256   {r['sha256']}")
    if "pool" in r and r["pool"].get("pool") == ghpool.POOL:
        p = r["pool"]
        out.append(f"  pool     github {p['repo']}@{p['commit'][:12]} {p['file']} ({p['lang']}, {p['licence']}, "
                   f"ratified by {', '.join(p['ratified_by'])}; trust {p['trust']['met']}/{p['trust']['known']} known)")
    elif "pool" in r:
        p = r["pool"]
        out.append(f"  pool     corpus export {p['export']} item {p['item_id']} ({p['lang']})"
                   + (f"; same bytes also in {p['also']}" if p["also"] else ""))
    if "target" in r:
        out.append(f"  target   {r['target']}")
    for f in r["functions"]:
        out.append(f"  {f['entry']}: {f.get('row', '')}")
        for k, v in sorted(f["engines"].items()):
            out.append(f"      {k:<12} {v}")
    for d in r["duplicates"]:
        out.append(f"  duplicate (skipped): {d}")
    out.append(f"{res.outcome}")
    return "\n".join(out)
