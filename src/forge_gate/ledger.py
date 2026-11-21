"""Forge Gate's two ledgers: exposure and outcome (design v1.4.5 §4a.5, build step 8a).

Decisions: ``~/aura-docs/Forge Gate/DECISIONS-8a-ledgers-20261115.md`` G1-G8 (the maintainer, 2026-11-15).

* **exposure** (``exposure.jsonl``): one row per result scout showed (query, result id, rank,
  the lens that surfaced it, time). Writer ``scout-search``.
* **outcome** (``outcome.jsonl``): three kinds, each with ONE named writer (V4-2):
  ``oracle`` by ``oracle-run`` (an oracle run passing on the reused code where it was reused);
  ``person-mark`` and ``person-event`` by ``person-mark`` (a person's "kept and it worked" mark,
  which scores only once an event the tool itself ran and measured passes after it).

Only outcomes score; being shown never does. This module holds the only code that appends to
either ledger. Which modules may call which writer is pinned by ``tests/test_ledger_routes.py``
(an AST scan), so scout's search path cannot write the outcome ledger. A writer sets ``writer``
itself; callers cannot pass it. Every row is checked by the same :func:`check_row` when written
and again when read, so a hand-appended row with the wrong writer never scores.

Files and locking (G6, the 3b/6b lessons):

* ``FORGE_GATE_LEDGER_DIR`` has no default. Unset = the ledgers are switched OFF and nothing is
  written anywhere. The folder, both ledger files and ``ledger.lock`` must already exist: the
  switch-on creates them with their owner and ACL, never a writer (a typo'd folder must not get a
  fresh ledger nobody reads, and the first writer must not decide the owner).
* All writers live here and take the same ``flock`` on ``ledger.lock``, so the lock is shared by
  construction.
* Append only: ``O_APPEND``, never ``O_CREAT``, never ``os.replace``, never a rewrite. The file
  keeps its inode, owner and ACL. All rows of one call go in one buffer, then ``fsync``; a
  failure that raises cuts the file back to its old size (still under the lock).
* Readers take no lock. A last line without ``\\n`` is an append in flight: skipped, reported as
  torn, never parsed.

Privacy (G2): no query text, no file path text and no code in either ledger: hashes and ids only.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from . import paths

SCHEMA = "fg-ledger/v1"
EXPOSURE_NAME = "exposure.jsonl"
OUTCOME_NAME = "outcome.jsonl"
LOCK_NAME = "ledger.lock"

# writer names (G3)
W_SEARCH = "scout-search"
W_ORACLE = "oracle-run"
W_PERSON = "person-mark"
# row kinds
K_EXPOSURE = "exposure"
K_ORACLE = "oracle"
K_MARK = "person-mark"
K_EVENT = "person-event"

WRITER_OF = {K_EXPOSURE: W_SEARCH, K_ORACLE: W_ORACLE, K_MARK: W_PERSON, K_EVENT: W_PERSON}
FILE_OF = {K_EXPOSURE: EXPOSURE_NAME, K_ORACLE: OUTCOME_NAME, K_MARK: OUTCOME_NAME, K_EVENT: OUTCOME_NAME}
COMMON = ("schema", "writer", "kind", "host", "time")
FIELDS = {
    K_EXPOSURE: ("query_id", "query_sha256", "query_lang", "result_id", "pool", "trust", "item",
                 "lens", "rank", "score", "band", "slot"),
    K_ORACLE: ("result_id", "run_id", "oracle", "verdict", "reused_at"),
    K_MARK: ("mark_id", "result_id", "item", "who"),
    K_EVENT: ("mark_id", "event_id", "event_time", "event_kind", "rc", "cmd_sha256", "root_sha256"),
}
SLOTS = ("top", "explore")          # 8a writes only "top"; 8b adds the one-in-ten explore slot
EVENT_KINDS = ("build", "tests")

_HEX12 = r"[0-9a-f]{12}"
# Every pattern is ASCII-only and used with fullmatch (review F4: `$` takes a trailing newline,
# `\d` takes non-ASCII digits; either would let one id or time be spelt two ways).
_A = re.ASCII
QUERY_ID = re.compile(_HEX12, _A)
LENS = re.compile(r"[a-z][a-z0-9]{0,23}", _A)
RESULT_ID = re.compile(rf"fgr-({_HEX12})-([a-z][a-z0-9]{{0,23}})-([1-9][0-9]{{0,5}})", _A)
RUN_ID = re.compile(rf"fgv-{_HEX12}", _A)
MARK_ID = re.compile(rf"fgm-{_HEX12}", _A)
EVENT_ID = re.compile(rf"fge-{_HEX12}", _A)
SHA256 = re.compile(r"[0-9a-f]{64}", _A)
TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", _A)  # fixed width: compares as text
# The one-line comment a handed-out snippet carries (V41-2). Python/shell `#`, C-family `//`.
RESULT_COMMENT = re.compile(r"^[ \t]*(?:#|//)[ \t]*forge-gate-result:[ \t]*(fgr-[0-9a-z-]+)[ \t]*$", re.M | re.A)
TEXT_MAX = 512


class LedgerError(RuntimeError):
    """The ledger cannot be written as asked. Nothing was kept."""


class Refused(LedgerError):
    """A writer's own rule refused the row (independence, unknown id, wrong verdict...)."""


class LockBusy(LedgerError):
    """Another writer holds ``ledger.lock`` longer than we waited."""


# ---------------------------------------------------------------- ids, time, the folder

def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_query_id() -> str:
    return secrets.token_hex(6)


def new_run_id() -> str:
    """A run id for an oracle run (``forge-gate verify`` mints one per run)."""
    return "fgv-" + secrets.token_hex(6)


def make_result_id(query_id: str, lens: str, rank: int) -> str:
    rid = f"fgr-{query_id}-{lens}-{rank}"
    m = RESULT_ID.fullmatch(rid)
    if m is None or m.groups() != (query_id, lens, str(rank)):
        raise Refused(f"cannot make a result id from {query_id!r}, {lens!r}, {rank!r}")
    return rid


def result_ids_in(text: str) -> list[str]:
    """Result ids named by ``forge-gate-result:`` comment lines, in order, valid ones only."""
    out = []
    for m in RESULT_COMMENT.finditer(text):
        rid = m.group(1)
        if RESULT_ID.fullmatch(rid) and rid not in out:
            out.append(rid)
    return out


def enabled() -> bool:
    """True when ``FORGE_GATE_LEDGER_DIR`` is set (it may still be wrong: writers check)."""
    return config.configured(config.LEDGER_KEY) is not None


def _dir(d) -> Path:
    return Path(d) if d is not None else config.ledger_dir()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _text(v, what: str) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > TEXT_MAX or "\n" in v:
        raise Refused(f"{what} must be one non-empty line of at most {TEXT_MAX} characters")
    return v


# ---------------------------------------------------------------- one row check, both ways

def check_row(row, file_name: str) -> str:
    """'' when ``row`` is a well-formed row that belongs in ``file_name``, else the reason."""
    if not isinstance(row, dict):
        return "not a JSON object"
    if row.get("schema") != SCHEMA:
        return f"schema is {row.get('schema')!r}, not {SCHEMA!r}"
    kind = row.get("kind")
    if not isinstance(kind, str) or kind not in WRITER_OF:      # review F1: a list is unhashable
        return f"unknown kind {kind!r}"
    if row.get("writer") != WRITER_OF[kind]:
        return f"kind {kind!r} has writer {WRITER_OF[kind]!r}, not {row.get('writer')!r}"
    if FILE_OF[kind] != file_name:
        return f"kind {kind!r} belongs in {FILE_OF[kind]}, not {file_name}"
    want = set(COMMON) | set(FIELDS[kind])
    if set(row) != want:
        return f"fields {sorted(set(row) ^ want)} missing or unexpected"
    if not isinstance(row["host"], str) or not row["host"]:
        return "host empty"
    if not isinstance(row["time"], str) or not TIME.fullmatch(row["time"]):
        return f"time {row['time']!r} not in the fixed UTC form"
    return _check_kind(kind, row)


def _check_kind(kind: str, r: dict) -> str:
    def ok_text(v):
        return isinstance(v, str) and v.strip() and len(v) <= TEXT_MAX and "\n" not in v

    if kind == K_EXPOSURE:
        m = RESULT_ID.fullmatch(r["result_id"]) if isinstance(r["result_id"], str) else None
        if not (isinstance(r["query_id"], str) and QUERY_ID.fullmatch(r["query_id"])):
            return "bad query_id"
        if not (isinstance(r["query_sha256"], str) and SHA256.fullmatch(r["query_sha256"])):
            return "bad query_sha256"
        if not (isinstance(r["lens"], str) and LENS.fullmatch(r["lens"])):
            return "bad lens"
        if not (isinstance(r["rank"], int) and not isinstance(r["rank"], bool) and r["rank"] >= 1):
            return "bad rank"
        if m is None or (m.group(1), m.group(2), int(m.group(3))) != (r["query_id"], r["lens"], r["rank"]):
            return "result_id does not name this query, lens and rank"
        if not all(ok_text(r[k]) for k in ("query_lang", "pool", "trust", "item")):
            return "query_lang, pool, trust and item must be non-empty one-line text"
        if not isinstance(r["score"], (int, float)) or isinstance(r["score"], bool):
            return "bad score"
        if r["band"] is not None:
            return "band must be null until 8b"
        if r["slot"] not in SLOTS:
            return f"slot {r['slot']!r} not in {SLOTS}"
        return ""
    if kind == K_ORACLE:
        if not (isinstance(r["result_id"], str) and RESULT_ID.fullmatch(r["result_id"])):
            return "bad result_id"
        if not (isinstance(r["run_id"], str) and RUN_ID.fullmatch(r["run_id"])):
            return "bad run_id"
        if r["verdict"] != "PASS":
            return "an oracle outcome is a PASS, nothing else"
        if not ok_text(r["oracle"]):
            return "bad oracle"
        ra = r["reused_at"]
        if not (isinstance(ra, dict) and set(ra) == {"file_sha256", "root_sha256", "content_sha256"}
                and all(isinstance(v, str) and SHA256.fullmatch(v) for v in ra.values())):
            return "bad reused_at"
        return ""
    if kind == K_MARK:
        if not (isinstance(r["mark_id"], str) and MARK_ID.fullmatch(r["mark_id"])):
            return "bad mark_id"
        if r["result_id"] is not None and not (isinstance(r["result_id"], str) and RESULT_ID.fullmatch(r["result_id"])):
            return "bad result_id"
        if not ok_text(r["item"]) or not ok_text(r["who"]):
            return "item and who must be non-empty one-line text"
        return ""
    # K_EVENT
    if not (isinstance(r["mark_id"], str) and MARK_ID.fullmatch(r["mark_id"])):
        return "bad mark_id"
    if not (isinstance(r["event_id"], str) and EVENT_ID.fullmatch(r["event_id"])):
        return "bad event_id"
    if not (isinstance(r["event_time"], str) and TIME.fullmatch(r["event_time"])):
        return "bad event_time"
    if r["event_kind"] not in EVENT_KINDS:
        return f"event_kind not in {EVENT_KINDS}"
    if not isinstance(r["rc"], int) or isinstance(r["rc"], bool):
        return "rc must be an integer"
    if not all(isinstance(r[k], str) and SHA256.fullmatch(r[k]) for k in ("cmd_sha256", "root_sha256")):
        return "cmd_sha256 and root_sha256 must be sha256 hex"     # 8a F5 (decided 2026-11-16)
    return ""


_run = subprocess.run      # the event's measurement; module-private (review F3), tests patch it


def _row(kind: str, now: str | None, **fields) -> dict:
    row = {"schema": SCHEMA, "writer": WRITER_OF[kind], "kind": kind,
           "host": socket.gethostname(), "time": now or now_utc(), **fields}
    why = check_row(row, FILE_OF[kind])
    if why:
        raise Refused(f"{kind} row refused before writing: {why}")
    return row


# ---------------------------------------------------------------- lock and append

@contextmanager
def _locked(d: Path, wait: float):
    if not d.is_dir():
        raise LedgerError(f"ledger folder {d} does not exist (the switch-on creates it)")
    lock = d / LOCK_NAME
    try:
        fd = os.open(lock, os.O_RDONLY)       # never O_CREAT: the switch-on creates the lock
    except OSError as e:
        raise LedgerError(f"cannot open {lock}: {e} (the switch-on creates it)") from None
    try:
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(f"another ledger writer holds {lock}; waited {wait:g} s. "
                                   "Nothing was written.") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _append(path: Path, rows: list) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)   # never O_CREAT, never a new inode
    except OSError as e:
        raise LedgerError(f"cannot open {path}: {e} (the switch-on creates it)") from None
    try:
        size = os.fstat(fd).st_size
        if size:
            with open(path, "rb") as fh:
                fh.seek(size - 1)
                if fh.read(1) != b"\n":
                    raise LedgerError(f"{path} does not end in a newline: an append would glue two "
                                      "rows into one line. Nothing was written.")
        data = b"".join(json.dumps(r, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
                        for r in rows)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, size)                      # our own rows only: we hold the lock
            os.fsync(fd)
            raise
    finally:
        os.close(fd)


# ---------------------------------------------------------------- reading (no lock)

@dataclass
class Contents:
    rows: dict = field(default_factory=lambda: {EXPOSURE_NAME: [], OUTCOME_NAME: []})
    line: dict = field(default_factory=dict)       # id(row) -> its line number (review F6)
    refused: list = field(default_factory=list)    # (file, line, reason)
    torn: list = field(default_factory=list)       # (file, line): an append in flight


def read(d=None) -> Contents:
    """Both ledgers, every line checked by :func:`check_row`. Raises LedgerError if unreadable."""
    d = _dir(d)
    out = Contents()
    for name in (EXPOSURE_NAME, OUTCOME_NAME):
        p = d / name
        try:
            data = p.read_bytes()
        except OSError as e:
            raise LedgerError(f"cannot read {p}: {e}") from None
        lines = data.split(b"\n")
        last = lines.pop()
        if last:
            out.torn.append((name, len(lines) + 1))
        for n, raw in enumerate(lines, 1):
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):   # review F1
                out.refused.append((name, n, "not a JSON line"))
                continue
            why = check_row(row, name)
            if why:
                out.refused.append((name, n, why))
            else:
                out.rows[name].append(row)
                out.line[id(row)] = n
    return out


@dataclass
class View:
    """The ledgers joined: result_id -> exposure, and the outcomes that can be placed."""
    exposure: dict = field(default_factory=dict)    # result_id -> first exposure row
    oracle: dict = field(default_factory=dict)      # (result_id, run_id) -> row
    marks: dict = field(default_factory=dict)       # mark_id -> row
    events: dict = field(default_factory=dict)      # mark_id -> [event rows]
    refused: list = field(default_factory=list)
    torn: list = field(default_factory=list)
    orphans: list = field(default_factory=list)     # outcome rows naming an id unknown here


def view(d=None) -> View:
    c = read(d)
    v = View(refused=list(c.refused), torn=list(c.torn))
    for r in c.rows[EXPOSURE_NAME]:
        old = v.exposure.get(r["result_id"])
        if old is None:
            v.exposure[r["result_id"]] = r
        elif old["item"] != r["item"]:
            v.refused.append((EXPOSURE_NAME, c.line[id(r)], f"{r['result_id']} names a second item"))
    for r in c.rows[OUTCOME_NAME]:
        if r["kind"] == K_ORACLE:
            if r["result_id"] not in v.exposure:
                v.orphans.append(r)
            else:
                v.oracle.setdefault((r["result_id"], r["run_id"]), r)
        elif r["kind"] == K_MARK:
            rid = r["result_id"]
            if rid is not None and rid not in v.exposure:
                v.orphans.append(r)
            elif rid is not None and v.exposure[rid]["item"] != r["item"]:
                v.refused.append((OUTCOME_NAME, c.line[id(r)], f"{r['mark_id']} item differs from {rid}'s"))
            else:
                v.marks.setdefault(r["mark_id"], r)
    for r in c.rows[OUTCOME_NAME]:
        if r["kind"] == K_EVENT:
            if r["mark_id"] not in v.marks:
                v.orphans.append(r)
            else:
                v.events.setdefault(r["mark_id"], []).append(r)
    return v


def _scores(mark: dict, events: list) -> bool:
    """A mark scores once an event the tool ran passed AFTER the mark was made."""
    return any(e["rc"] == 0 and e["event_time"] >= mark["time"] for e in events)


@dataclass(frozen=True)
class Counts:
    """Three counts side by side, never blended (V42-2), plus pending marks (never scored)."""
    oracle: int = 0
    person_with_id: int = 0
    person_without_id: int = 0
    pending: int = 0
    blind: str = ""                 # non-empty: the ledger could not be read; counts mean nothing


def uncounted_outcomes(v: "View") -> int:
    """Outcome rows that were written but are not counted: refused rows and torn lines of the outcome
    ledger, plus orphans (8b s6, re-check N1/N2; torn lines: 8b crew review, 6E T1). A reader shows BLIND instead of a 0 it cannot vouch for; exposure
    refusals do not touch the counts and are not included. Read-only."""
    return (sum(1 for f, _, _ in v.refused if f == OUTCOME_NAME) + len(v.orphans)
            + len(torn_outcome_lines(v)))   # 8b crew review, 6E T1


def torn_outcome_lines(v: "View") -> list:
    """Line numbers of the outcome ledger's torn lines: an append in flight, or one a killed writer
    left (then every later append is refused until the file is repaired). Read-only."""
    return [n for f, n in v.torn if f == OUTCOME_NAME]


def counts(item: str, d=None, *, v: View | None = None) -> Counts:
    if v is None:
        try:
            v = view(d)
        except (LedgerError, config.NotConfigured) as e:
            return Counts(blind=str(e))
    # D1 as amended after the crew review (6E A9, A8) and N3 (the maintainer 2026-11-16): one reuse SITE is
    # one PROJECT ROOT, whatever files and ids it holds and however often verify ran in it; one
    # PERSON counts once per item, "with id" if any of their scored marks named a result id.
    oracle = len({r["reused_at"]["root_sha256"] for (rid, _run), r in v.oracle.items()
                  if v.exposure[rid]["item"] == item})
    scored, waiting = {}, set()
    for mid, m in v.marks.items():
        if m["item"] != item:
            continue
        who = m["who"]
        if _scores(m, v.events.get(mid, [])):
            scored[who] = scored.get(who, False) or m["result_id"] is not None
        else:
            waiting.add(who)
    with_id = sum(1 for has_id in scored.values() if has_id)
    without_id = len(scored) - with_id
    return Counts(oracle, with_id, without_id, len(waiting - set(scored)))


def exposures(item: str, d=None, *, v: View | None = None) -> list:
    v = view(d) if v is None else v
    return [r for r in v.exposure.values() if r["item"] == item]


# ---------------------------------------------------------------- the three writers

def record_exposure(*, query_sha256: str, query_lang: str, hits: list, d=None,
                    wait: float = 10.0) -> tuple[str, list]:
    """Writer ``scout-search``. ``hits``: dicts with lens, rank, score, pool, trust, item.

    Returns ``(query_id, rows)``; each row's ``result_id`` is what scout prints and hands out.
    No hits: nothing is written (nothing was shown)."""
    qid = new_query_id()
    rows = [_row(K_EXPOSURE, None, query_id=qid, query_sha256=query_sha256, query_lang=query_lang,
                 result_id=make_result_id(qid, h["lens"], h["rank"]), pool=h["pool"], trust=h["trust"],
                 item=h["item"], lens=h["lens"], rank=h["rank"], score=h["score"], band=None,
                 slot=h.get("slot", "top"))
            for h in hits]
    if rows:
        d = _dir(d)
        with _locked(d, wait):
            _append(d / EXPOSURE_NAME, rows)
    return qid, rows


_real = paths.real
_canon = paths.canonical
_under = paths.is_under


def _surfaced_roots(d: Path) -> list:
    """Folders whose files are pool copies scout surfaced, not code where it was reused."""
    roots = [_real(d)]
    for key in (config.CORPUS_KEY, config.SCOUT_DIR_KEY):   # the GitHub cache is under the scout dir
        raw = config.configured(key)
        if raw is not None:
            roots.append(_real(config._as_path(raw)))
    return roots


def _project_root(p: Path) -> Path:
    for parent in p.parents:
        if (parent / ".git").exists():
            return parent
    return p.parent


def _folder_root(p: Path) -> Path:
    """The project root of a FOLDER: itself or its nearest ancestor holding ``.git``; else itself.
    In a git project, a file anywhere inside it gets the same answer from :func:`_project_root`
    (both hashed through :func:`_canon`),
    so an event and an oracle row in one project carry one ``root_sha256`` (without ``.git`` the
    two can differ: a file's fallback is its own folder)."""
    for q in (p, *p.parents):
        if (q / ".git").exists():
            return q
    return p


def cmd_sha256(cmd: list) -> str:
    """The event's command as a hash of its argv in canonical JSON (8a F5): auditable by anyone who
    knows the claimed command. The text is not stored, but a guessable argv is confirmable from its
    hash (unsalted, KD-14 h). ensure_ascii stays on: an undecodable POSIX argv byte (surrogateescape)
    is escaped, never an encode error that would lose the row."""
    return _sha(json.dumps(cmd, separators=(",", ":")))


def record_oracle(*, result_id: str, run_id: str, oracle: str, verdict: str, verified_file,
                  content_sha256: str, d=None, wait: float = 10.0) -> dict:
    """Writer ``oracle-run``: an oracle run PASSED on reused code where it was reused (V4-2).

    Refused: any verdict but PASS; a file inside the ledger folder, the corpus export or scout's
    folder (that is code where scout surfaced it, not where it was reused); a result id the local
    exposure ledger does not hold; the same (result_id, run_id) twice; a file with more than one
    hard link; a file whose bytes are not ``content_sha256`` (the bytes the run judged, read before
    it ran). The hook's snippet and looseness checks (D3) are NOT repeated here (KD-14 a)."""
    if verdict != "PASS":
        raise Refused(f"only a PASS is an oracle outcome, not {verdict!r}")
    d = _dir(d)
    f = _real(verified_file)
    if not f.is_file():
        raise Refused(f"verified file {verified_file} is not a file")
    links = f.stat().st_nlink
    if links > 1:                                   # crew review 6E A3: a hard link has no surfaced
        raise Refused(f"verified file {verified_file} has {links} hard links, so it cannot be shown "
                      "not to be a surfaced copy; verify a copy of its own")   # ancestor to find
    if hashlib.sha256(f.read_bytes()).hexdigest() != content_sha256:          # 6E A1
        raise Refused("the file's bytes are not the bytes the run judged (changed during the run, or "
                      "content_sha256 was not computed from this file); nothing was recorded")
    for root in _surfaced_roots(d):
        if _under(f, root):
            raise Refused("the verified file lies where scout surfaced the code, not where it was "
                          "reused: not independent (design §4a.5)")
    row = _row(K_ORACLE, None, result_id=result_id, run_id=run_id, oracle=_text(oracle, "oracle"),
               verdict="PASS",
               reused_at={"file_sha256": _sha(str(_canon(f))), "root_sha256": _sha(str(_canon(_project_root(f)))),
                          "content_sha256": content_sha256})
    with _locked(d, wait):
        v = view(d)
        if result_id not in v.exposure:
            raise Refused(f"{result_id} is not in this host's exposure ledger")
        if (result_id, run_id) in v.oracle:
            raise Refused(f"{result_id} already has an outcome from run {run_id}")
        _append(d / OUTCOME_NAME, [row])
    return row


def record_mark(*, who: str, result_id: str | None = None, item: str | None = None, d=None,
                wait: float = 10.0) -> dict:
    """Writer ``person-mark``: "kept and it worked". Pending until :func:`record_event` passes.

    Name the result (``result_id``) or, without one, the code itself (``item``), never both."""
    if (result_id is None) == (item is None):
        raise Refused("name exactly one: the result id, or (without one) the item")
    d = _dir(d)
    with _locked(d, wait):
        if result_id is not None:
            v = view(d)
            if result_id not in v.exposure:
                raise Refused(f"{result_id} is not in this host's exposure ledger")
            item = v.exposure[result_id]["item"]
        row = _row(K_MARK, None, mark_id="fgm-" + secrets.token_hex(6), result_id=result_id,
                   item=_text(item, "item"), who=_text(who, "who"))
        _append(d / OUTCOME_NAME, [row])
    return row


def record_event(*, mark_id: str, project, cmd: list, event_kind: str, d=None,
                 wait: float = 60.0) -> dict:
    """Writer ``person-mark``: the external event, RUN and MEASURED here, never typed in (G5 a).

    Runs ``cmd`` (the reusing project's build or tests) in ``project`` as the calling user, and
    records its exit code. The command is the person's own, with the trust of typing it in a
    shell; it is never pool code (KD-14). Refused before running: an unknown mark, a mark dated
    after now, a project inside a surfaced location. Checked again after the run, under the lock:
    the event is not older than its mark (a clock stepped back during the run). The command
    runs BEFORE the lock is taken (a test run can be long); if another writer then holds the lock
    longer than ``wait``, the result is dropped with LockBusy and the event must be run again.
    Neither the time nor the exit code can be passed in (review F3). The row keeps the command and
    the project only as hashes, ``cmd_sha256`` and ``root_sha256`` (8a F5): for audit, not scoring."""
    if event_kind not in EVENT_KINDS:
        raise Refused(f"event kind {event_kind!r} not in {EVENT_KINDS}")
    if not cmd or not all(isinstance(a, str) for a in cmd):
        raise Refused("no command to run")
    d = _dir(d)
    proj = Path(project)
    if not proj.is_dir():
        raise Refused(f"project {project} is not a folder")
    for root in _surfaced_roots(d):                 # never pool code (G5; 8a s4 review F1)
        if _under(_real(proj), root):
            raise Refused(f"project {project} lies where scout surfaced code ({root}), not where it "
                          "was reused: pool code is never run; nothing was run")
    mark = view(d).marks.get(mark_id)
    if mark is None:
        raise Refused(f"{mark_id} is not a mark in this host's outcome ledger; nothing was run")
    if now_utc() < mark["time"]:                    # 8a crew review (5S C5): refused BEFORE the run
        raise Refused(f"the mark is dated {mark['time']}, after now: an event now would be older "
                      "than its mark; nothing was run")
    # hashed BEFORE the run (8a s7 review F3): the command must not choose its own audit root
    argv_hash, root_hash = cmd_sha256(cmd), _sha(str(_canon(_folder_root(_real(proj)))))
    try:
        # the command's own output goes to stderr (fd 2): stdout carries only forge-gate's line,
        # so nothing the command prints can pass for a recorded result (8a s4 review F4)
        rc = _run(cmd, cwd=str(proj), stdout=2).returncode
    except OSError as e:
        raise LedgerError(f"could not run {cmd[0]!r}: {e}. Nothing was recorded.") from None
    t = now_utc()
    row = _row(K_EVENT, t, mark_id=mark_id, event_id="fge-" + secrets.token_hex(6), event_time=t,
               event_kind=event_kind, rc=rc, cmd_sha256=argv_hash, root_sha256=root_hash)
    with _locked(d, wait):
        v = view(d)
        mark = v.marks.get(mark_id)
        if mark is None:
            raise Refused(f"{mark_id} vanished from the outcome ledger; nothing was recorded")
        if t < mark["time"]:
            raise Refused(f"the event ({t}) is older than its mark ({mark['time']})")
        _append(d / OUTCOME_NAME, [row])
    return row
