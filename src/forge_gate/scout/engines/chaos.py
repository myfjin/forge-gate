"""Scout's chaos engine (build step 6b-2; design v1.4.5 §4.3; decision L3, the maintainer 2026-11-13).

A chaos PASS card makes a row **hard**. There is no human countersign in that path (§4.3). So
a scout chaos PASS must mean exactly what the validator's ``chaos_scan.py`` PASS means, and it
must never be easier to get. The semantics, read and measured, are in
``~/aura-docs/Forge Gate/CHAOS-SEMANTICS-6b2-20261113.md``. In short:

- The validator's chaos is a CLOSED list of 19 hand-written probes. Each probe is tied to one
  library file and paired with a negative control (NC), an unsafe twin that must die to the same
  fault. A file the list does not name gets no chaos card at all.
- DOMAIN. Scout probes a corpus item only when its bytes are IDENTICAL to one of those 19
  library files as pinned below (library 4b65044b). The validator would give exactly those bytes
  the same probe. Any other file gets ``BLIND(no validator chaos probe for these bytes)``.
  - Probing crew code by API shape would be more complete, but it skips the human choice of
    "this file faces this probe" that the validator's PASS rests on. It is not built
    (named conflict; the maintainer decides).
- RACE / TIMING probes (threads, sleeps, SIGKILL timing) are always BLIND: their verdict
  depends on scheduling, so parity with the validator cannot be shown.
- THE GATE. Every run executes ALL 19 NCs first, as the validator's ``main()`` does, and does it
  3 times (chaos_runner.GATE_RUNS): a race NC that dies only sometimes must die every time.
  - If any NC survived, the validator writes no report (ABORT). Scout says BLIND.
  - Scout is stricter: an NC whose "death" was a raise, a compile failure, a missing rustc or a
    timeout is not clean, and gives BLIND (see engines/chaos_runner.py).
- HOST HISTORY (parent review, 2026-11-13). The gate is a race on some hosts, so 3 clean runs
  only bound the odds; they cannot make one file's verdict identical to the validator's own run.
  Every gate run is recorded per host in ``$FORGE_GATE_SCOUT_DIR/chaos_gate_history.json``. Once
  any NC has survived on a host, chaos is BLIND for every probe there, and stays BLIND until an
  operator archives the file. A host whose gate is unreliable is an input class where parity
  cannot be shown. A history that cannot be read is BLIND too (fail closed).
- VERDICT. The card comes from the port's own ``scan()``. Validator PASS -> PASS.
  INVALID-CANDIDATE -> FAIL. FLAG (harness error) -> BLIND. Nothing else is ever PASS.
- FUNCTIONS. A card backs a function only if it is the probe's SUBJECT (the function whose
  survival the predicate measures, hand-read per probe below) AND the run recorded the probe
  reading it on the pattern module. A function the probe only uses to measure (PITR ``vals``) or to
  inject the fault (ETL ``stable_checksum``) is not a subject (6b-2 review F1). Every other
  function gets BLIND.
- WHAT RUNS. Only pinned library bytes and the validator's own NC code, in a child process with
  a time cap. Crew code that is not byte-identical to a pinned file is never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import datetime as _dt
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from ... import config
from .chaos_runner import GATE_ONLY, GATE_RUNS   # GATE_ONLY: calibration mode, the gate without a probe

KIND = "chaos"
LIBRARY_COMMIT = "4b65044b"
SOURCE = "tools/pattern-scoring/validator/chaos_scan.py"
SOURCE_BLOB = "fab0bdd27d7f56a5db534f6bca59b20b46933683"
RUNNER = Path(__file__).with_name("chaos_runner.py")
PORT = Path(__file__).with_name("chaos_port.py")
# KD-12 (j), the maintainer 2026-11-14: a corpus item that IS a pinned library file adds no evidence (its
# verdict is the library's own), so learn gives it BLIND. A constant, not config: only tests flip
# it, to exercise the parity machinery that a future engine will feed with other inputs.
LEARN_LIBRARY_COPIES = False
# KD-12 (c)/(m): clean gate runs needed per (machine, configuration) before any verdict. Measured
# 2026-11-14 (ai-lab/fg6b2/calib): the dev machine seqlock NC survived 1 of 391 gate runs, the deploy host 399 of 400.
# To catch a machine at the dev machine's rate with 95% confidence: ln(0.05)/ln(1-1/391) = 1170 -> 1200.
CALIBRATION_N = 1200
HISTORY_VERSION = 2
TIMEOUT_S = 900   # the whole child (19 NCs + 1 probe); measured ~4-10 s per run on both hosts

# probe name -> (library path, sha256 of that file at library commit 4b65044b). The 19 entries of
# the validator's PROBES list, in its order. Unchanged at library HEAD on both hosts (2026-11-13).
PINNED = {
    "atomic_write_temp_rename": ("python/system-administration/filesystem-integrity/atomic_write_temp_rename.py",
        "800f18ecd2174c74175fcb501c8b3ab8e013541ac91c171a978527fa51c8f3a8"),
    "write_ahead_log": ("python/programming/persistence/write_ahead_log.py",
        "079a678b6c5fde2f3f35f1d7e4695c5035c1250bd4b6083de3192a5e776a0431"),
    "checkpoint_resume": ("python/system-administration/data-pipeline-ops/checkpoint_resume.py",
        "21c752c27f233465cf2680893cbf147ddd1a59a302708d7e6862b44bd0f744be"),
    "segmented_wal": ("python/programming/persistence/segmented_wal.py",
        "7fcee0ce7dc7e4834e3b75924720daab85793641bd87dd199630dc33503c5159"),
    "journaled_write": ("python/system-administration/state-db-durability/journaled_write.py",
        "e19eef5fe33f12454763c1d878b6b48f40dd6e89b5e02657448fea83a3561380"),
    "point_in_time_restore": ("python/system-administration/state-db-durability/point_in_time_restore.py",
        "fb62f375e860264b889967442636e50a177c9d82ee5eb60c743c212e42e0f2ae"),
    "idempotent_etl_step": ("python/system-administration/data-pipeline-ops/idempotent_etl_step.py",
        "1ebdead816050a0f61feb14622b57a1ac652d3fa180c47e14f450f692af4daee"),
    "idempotent_replication": ("python/system-administration/mesh-distributed/idempotent_replication.py",
        "43bfd541a11359121aa5beae645e78e262fa80d5737eac5e082c8b7d9cafc1a9"),
    "idempotent_once_guard": ("python/system-administration/concurrency-locking/idempotent_once_guard.py",
        "4471c4b5cfde3c8b7bc87f6f4b8cbad77f3441d69042e083dad2bd9153c1c9a4"),
    "transaction_rollback": ("python/system-administration/state-db-durability/transaction_rollback.py",
        "dc00296c92b2d7539ccf2540191b07832340b84c5cab847c888dd2f19af870a6"),
    "migration_with_rollback": ("python/system-administration/state-db-durability/migration_with_rollback.py",
        "8d5160e6f865169113b0ab15aabd935a2b3a052005254685392ff0d274f41431"),
    "wal_append_replay": ("rust/composed-systems/wal_append_replay.rs",
        "e53348115edab0145b22b85fd754700b21caeccc49d2cd4930a55d08925e8019"),
    "tamper_evident_log": ("rust/composed-systems/tamper_evident_log.rs",
        "275af71afc55f1c0235862f364eeec40cf3cc0a5fab8c579c862c0f4429cc287"),
    "content_addressed_store": ("rust/composed-systems/content_addressed_store.rs",
        "1ab542c1f462c93e2878e91c2162e99b3391624aabebfcda2d52a46657350fb7"),
    "rotating_log_checksum": ("rust/composed-systems/rotating_log_checksum.rs",
        "0ba75b1a24fec23d7389315d6461f90792de1c980992a2557b2d4ae291134766"),
    "signed_snapshot": ("rust/composed-systems/signed_snapshot.rs",
        "c3f6692c60455c6fafaac09b8ddc892c80f5f6dcdd39f1edcd79d03ec065d1e2"),
    "hmac_signed_url": ("rust/security-crypto/hmac_signed_url.rs",
        "dd618ec1b5d810520216a4dc4aaeb3d309d38796d4ab32618c33737e90a382f8"),
    "seqlock_snapshot": ("rust/concurrency-resilience/seqlock_snapshot.rs",
        "481e93d3d5a395536f8b49466cc69f819cd5252f85ba0b83772f9ab0a8bf962d"),
    "double_buffer": ("rust/concurrency-resilience/double_buffer.rs",
        "55528afee0d59dc83a092c8c5df48d87da0c2000718099b64db376bd4082564f"),
}
# Threads, sleeps or SIGKILL timing in the probe or its helpers (AST scan of each probe's call
# closure, 2026-11-13). seqlock_snapshot is also the NC that aborts the validator on the deploy host.
RACE = frozenset({"idempotent_once_guard", "transaction_rollback", "migration_with_rollback",
                  "seqlock_snapshot", "double_buffer"})
assert RACE <= set(PINNED) and len(PINNED) == 19
_BY_SHA = {sha: name for name, (_rel, sha) in PINNED.items()}

# probe -> the top-level functions whose survival its predicate measures (read from each probe's
# body in chaos_scan.py at 4b65044b, 2026-11-13, review F1). Classes (WAL, ReplicatedStore) and the
# Rust probes have none: scout's rows are top-level functions only, so those back no row.
SUBJECT = {
    "atomic_write_temp_rename": frozenset({"atomic_write"}),          # m.atomic_write under the crash
    "write_ahead_log": frozenset(),                                    # class WAL
    "checkpoint_resume": frozenset({"process_batch_with_checkpoint"}), # crashed, then resumed
    "segmented_wal": frozenset({"recover_from_crash"}),                # recovers the torn segment
    "journaled_write": frozenset({"recover_from_journal"}),            # recovers after the crash
    "point_in_time_restore": frozenset({"restore_to"}),                # NOT vals: the measuring tool
    "idempotent_etl_step": frozenset({"idempotent_transform"}),        # NOT stable_checksum: the fault site
    "idempotent_replication": frozenset(),                             # class ReplicatedStore
    **{n: frozenset() for n in PINNED if n.startswith(("wal_", "tamper", "content", "rotating",
                                                           "signed", "hmac"))},
    **{n: frozenset() for n in RACE},                                  # never reached: BLIND first
}
assert set(SUBJECT) == set(PINNED)

# Validator card verdict -> scout verdict. FLAG and anything unknown are BLIND, never a verdict.
VERDICT_MAP = {"PASS": "PASS", "INVALID-CANDIDATE": "FAIL"}


class Blind(Exception):
    """The run cannot give a verdict; the message is the BLIND reason. ``partial_gate`` carries
    the gate runs a child finished before it died, so the caller records them (review F2)."""

    def __init__(self, reason: str = "", partial_gate: dict | None = None):
        super().__init__(reason)
        self.partial_gate = partial_gate


def probe_for(body: bytes):
    """The validator probe these exact bytes would face, or None."""
    return _BY_SHA.get(hashlib.sha256(body).hexdigest())


def stage_library(lib_dir: Path, dest: Path) -> None:
    """Copy the 19 pinned files from ``lib_dir`` into ``dest`` and check every copy's sha256.
    The NCs and probes read the library, so the whole pinned set must be present and exact."""
    for name, (rel, sha) in PINNED.items():
        src = Path(lib_dir) / rel
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(src, out)
        except OSError as e:
            raise Blind(f"library file {rel} unreadable: {type(e).__name__}") from None
        if hashlib.sha256(out.read_bytes()).hexdigest() != sha:
            raise Blind(f"library file {rel} is not at the pinned bytes (library {LIBRARY_COMMIT})")


def run_child(lib_root: Path, probe: str, work: Path, timeout: float = TIMEOUT_S) -> dict:
    """Run the runner once (19 NCs + the one probe) against ``lib_root``; return its JSON."""
    out = work / "result.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["TMPDIR"] = str(work)   # the probes' own mkdtemp() calls land here, never in /tmp
    # Own process group: on a timeout (and after every run) the whole group is killed, so no
    # rustc, probe binary or SIGKILL-family child outlives the run (review F4).
    child = subprocess.Popen([sys.executable, "-I", str(RUNNER), str(lib_root), probe, str(out)],
                             cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        child.wait()
    try:
        res = json.loads(out.read_text())
    except (OSError, ValueError):
        res = None
    if timed_out:
        raise Blind(f"chaos run timed out after {timeout:g} s", partial_gate=_partial(res))
    if not isinstance(res, dict) or ("gate" not in res and "error" not in res):
        raise Blind("chaos run wrote no result", partial_gate=_partial(res))
    return res


def _gate_shape(g) -> bool:
    return (isinstance(g, dict) and isinstance(g.get("runs"), int) and g["runs"] > 0
            and isinstance(g.get("survived"), list) and all(isinstance(x, str) for x in g["survived"])
            and isinstance(g.get("unclean"), dict))


def _partial(res):
    """The gate runs a child finished before it died or errored, if it wrote any."""
    g = res.get("partial_gate") if isinstance(res, dict) else None
    return g if _gate_shape(g) else None


def _not_clean(g: dict) -> dict:
    """A partial gate's runs and survivals count; its runs are never clean."""
    return {"ok": False, "survived": list(g["survived"]), "runs": g["runs"],
            "unclean": {**g["unclean"], "_child": "the child did not finish this gate"}}


def _run_recorded(lib: Path, probe: str, work: Path, path: Path) -> dict:
    """run_child, recording every gate run the child finished BEFORE anything raises: a full
    gate as it is, a partial one (child died, hung or errored) as not clean (review F2)."""
    try:
        res = run_child(lib, probe, work)
    except Blind as e:
        if e.partial_gate is not None:
            record_gate(path, _not_clean(e.partial_gate))
        raise
    if isinstance(res.get("gate"), dict):
        record_gate(path, res["gate"])
    elif _partial(res) is not None:
        record_gate(path, _not_clean(res["partial_gate"]))
    return res


def file_verdict(res: dict, probe: str):
    """(verdict, card) for the whole FILE from one child result, or raise Blind. The order is
    the validator's: the gate first (an ABORT writes no card at all), then the card. This is
    what the parity test compares with the validator's own run on the same file."""
    if "error" in res:
        raise Blind(f"chaos harness error: {res['error']}")
    gate = res.get("gate") or {}
    if gate.get("survived"):
        raise Blind("negative controls did not all die, so the validator ABORTs here: "
                    + ", ".join(gate["survived"]))
    if gate.get("unclean"):
        raise Blind("a negative control did not run cleanly: "
                    + "; ".join(f"{k}: {v}" for k, v in sorted(gate["unclean"].items())))
    if gate.get("ok") is not True:
        raise Blind("the negative-control gate did not pass")
    if gate.get("runs") != GATE_RUNS:
        raise Blind(f"the negative-control gate ran {gate.get('runs')!r} times, not {GATE_RUNS}")
    card = res.get("card") or {}
    if (res.get("probe") != probe or card.get("pattern") != PINNED[probe][0]
            or card.get("oracle_coverage_kind") != KIND):
        raise Blind("chaos result does not belong to this probe")
    v = card.get("verdict")
    if v == "FLAG":
        raise Blind(f"chaos harness error: {str(card.get('actual', ''))[:200]}")
    if v not in VERDICT_MAP:
        raise Blind(f"unknown validator verdict {v!r}")
    return VERDICT_MAP[v], card


def decide(res: dict, probe: str, entry: str):
    """(verdict, card) for one FUNCTION: the file's verdict, and only for the probe's subject
    that the run recorded it driving. Raises Blind otherwise."""
    verdict, card = file_verdict(res, probe)
    if entry not in SUBJECT[probe] or entry not in (res.get("touched") or ()):
        raise Blind(f"{entry} is not a subject the {probe} probe drove")
    return verdict, card


HISTORY_NAME = "chaos_gate_history.json"


def host() -> str:
    """The hostname: a LABEL in the history, never its key (it changes with a rename or a network)."""
    return socket.gethostname()


def machine_id() -> str:
    """A stable id that survives a rename (KD-12 m): /etc/machine-id on Linux, IOPlatformUUID on
    macOS. Unknown -> Blind (a machine scout cannot name is never calibrated)."""
    mid = ""
    try:
        if sys.platform.startswith("linux"):
            mid = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        elif sys.platform == "darwin":
            out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=30).stdout
            m = re.search(r'"IOPlatformUUID" = "([0-9A-Fa-f-]+)"', out)
            mid = m.group(1) if m else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        mid = ""
    if not re.fullmatch(r"[0-9A-Fa-f-]{16,64}", mid):
        raise Blind("machine identity unknown: chaos stays BLIND")
    return mid.lower()


def fingerprint() -> dict:
    """What the race NCs' behaviour depends on. A change starts a fresh calibration (KD-12 m).
    ``rustc -V`` runs from the scratch dir, where the child runs, so a ``rust-toolchain.toml``
    in the caller's cwd cannot name a compiler the child does not use (review F3)."""
    try:
        cwd = config.scratch_dir()
    except config.NotConfigured as e:
        raise Blind(f"not configured: {e}") from None
    try:
        rustc = subprocess.run(["rustc", "-V"], capture_output=True, text=True, timeout=60,
                               cwd=cwd).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rustc = ""
    return {"python": platform.python_version(), "rustc": rustc or "none",
            "port_md5": hashlib.md5(PORT.read_bytes()).hexdigest(),
            "cpu": f"{platform.machine()} x{os.cpu_count()}", "os": f"{platform.system()} {platform.release()}"}


def config_key(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]


def _history_path() -> Path:
    try:
        return config.scout_dir() / HISTORY_NAME
    except config.NotConfigured as e:
        raise Blind(f"not configured: {e}") from None


def load_history(path: Path) -> dict:
    """{"version": 2, "machines": {machine_id: {"hostname", "survived": {nc: n}, "first_survival",
    "configs": {config_key: {"fingerprint", "gate_runs", "clean_runs"}}}}}.
    Missing -> empty. Anything unreadable, or another version -> Blind (a history scout cannot read
    never says clean). A symlink at the path, dangling or not, is Blind: ``exists()`` follows links,
    so a dangling one read as "missing" and lost a recorded survival (crew review 2026-11-14, 5S F1)."""
    if path.is_symlink():
        raise Blind(f"chaos gate history {path} is a symlink: chaos stays BLIND")
    if not path.exists():
        return {"version": HISTORY_VERSION, "machines": {}}
    try:
        h = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(h, dict) and h.get("version") == HISTORY_VERSION
        assert isinstance(h.get("machines"), dict)
        for m in h["machines"].values():
            assert isinstance(m.get("survived"), dict) and isinstance(m.get("configs"), dict)
            for c in m["configs"].values():
                assert isinstance(c.get("gate_runs"), int) and isinstance(c.get("clean_runs"), int)
        return h
    except (OSError, ValueError, AssertionError, AttributeError):
        raise Blind(f"chaos gate history {path} is unreadable: chaos stays BLIND") from None


def record_gate(path: Path, gate: dict) -> None:
    """Add one child run's gate to this machine's record (atomic replace). A gate counts as clean
    only if every run was ok with no survival and no unclean NC. learn and chaos-calibrate hold
    learn's singleton lock while they run, so two writers never meet."""
    h = load_history(path)
    mid, fp = machine_id(), fingerprint()
    m = h["machines"].setdefault(mid, {"survived": {}, "configs": {}})
    m["hostname"] = host()
    c = m["configs"].setdefault(config_key(fp), {"fingerprint": fp, "gate_runs": 0, "clean_runs": 0})
    runs = int(gate.get("runs") or 0)
    c["gate_runs"] += runs
    if gate.get("ok") is True and not gate.get("survived") and not gate.get("unclean"):
        c["clean_runs"] += runs
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    for nc in gate.get("survived") or ():
        m["survived"][nc] = m["survived"].get(nc, 0) + 1
        m.setdefault("first_survival", now)
    fd, tmp = tempfile.mkstemp(prefix=HISTORY_NAME + ".", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(h, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def check_host() -> None:
    """Raise Blind unless this machine is calibrated and has never seen an NC survive.
    1. Any survival ever recorded for this MACHINE (any configuration) -> Blind (the latch).
    2. Fewer than CALIBRATION_N clean gate runs under the CURRENT configuration -> Blind."""
    h = load_history(_history_path())
    mid = machine_id()
    m = h["machines"].get(mid) or {"survived": {}, "configs": {}}
    if any(m["survived"].values()):
        seen = ", ".join(f"{k} x{v}" for k, v in sorted(m["survived"].items()))
        raise Blind(f"machine {mid[:8]} ({host()}): negative controls have survived here before "
                    f"({seen}); chaos stays BLIND on this machine until an operator archives {HISTORY_NAME}")
    c = m["configs"].get(config_key(fingerprint()))
    clean = c["clean_runs"] if c else 0
    if clean < CALIBRATION_N:
        raise Blind(f"machine {mid[:8]} ({host()}): {clean}/{CALIBRATION_N} clean gate runs under this "
                    "configuration; chaos stays BLIND until calibrated (forge-gate chaos-calibrate)")


def calibrate(runs: int = CALIBRATION_N) -> dict:
    """Run the validator's NC gate (no probe) until ``runs`` gate runs are recorded for this
    machine and configuration. Writes ONLY the history file. Stops at the first survival: the
    machine is latched and more runs cannot unlatch it."""
    lib_dir = config.library_dir()
    path = _history_path()
    work = Path(tempfile.mkdtemp(prefix="chaos-cal-", dir=config.scratch_dir()))
    done = 0
    try:
        lib = work / "lib"
        stage_library(lib_dir, lib)
        while done < runs:
            res = _run_recorded(lib, GATE_ONLY, work, path)
            gate = res.get("gate")
            if not _gate_shape(gate):
                raise Blind(f"calibration child failed: {res.get('error', 'no gate')}")
            done += gate["runs"]
            if gate.get("survived"):
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m = load_history(path)["machines"].get(machine_id(), {})
    return {"machine": machine_id(), "hostname": host(), "gate_runs_this_call": done,
            "survived": m.get("survived", {}),
            "config": m.get("configs", {}).get(config_key(fingerprint()), {})}


_CACHE: dict = {}


def result_for(body: bytes, probe: str) -> dict:
    """One child run per (bytes, probe) per process. learn asks once per function."""
    key = (hashlib.sha256(body).hexdigest(), probe)
    if key not in _CACHE:
        try:
            lib_dir = config.library_dir()
        except config.NotConfigured as e:
            raise Blind(f"not configured: {e}") from None
        path = _history_path()
        work = Path(tempfile.mkdtemp(prefix="chaos-", dir=config.scratch_dir()))
        try:
            lib = work / "lib"
            stage_library(lib_dir, lib)
            _CACHE[key] = _run_recorded(lib, probe, work, path)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return _CACHE[key]
