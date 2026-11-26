"""The GitHub fact tool: would this project's own tests notice a bug in this function?
(GitHub-lane loop S3, DESIGN github-lane-2026-11 §5 rules R0-R8.)

``forgeproof github-facts ORG/REPO@SHA [--functions ...]``. For every admitted top-level function
(admission is ``ghpool.admit``, read only), it plants up to 12 single-node bugs (``plant``, R5),
runs the repo's OWN unittest suite in the run-mode isolation box once per bug, and appends one
FACT ROW per function to ``$FORGEPROOF_SCOUT_DIR/github_facts.jsonl``. A fact row is never a PASS
and never a card: this module imports neither ``learn`` nor ``cards`` and writes nothing else
(its own throwaway staging folder under the box root is removed after each module).

Rules, as built:
- R0: no ``FORGEPROOF_BOX_USER`` -> one run row BLIND(box-user-unset); nothing is admitted or run.
- R1: the suite runs unmutated before a function's mutants and after them (the "after" control
  of one function is the "before" control of the next in the same module). Both must exit 0 with
  >= 1 test and the same set of passing test ids, else BLIND(control-red | control-unstable |
  no-tests). A red "before" control skips that function's mutants.
- R2: every box run is ``env PYTHONHASHSEED=0 python3 -m fg_unittest_json --module M`` in
  ``/work`` (the repo root), the runner copied to the work root.
- R3: CAUGHT only when the box outcome is FOUND, rc == 1, the runner's JSON names >= 1 test that
  failed or errored AND passed in both controls, no failing test's error is a MemoryError or
  RecursionError, and one re-run of the same mutant fails that same test again (rc 1).
  SURVIVED = rc 0 and every control-passing test passed. Everything else is UNCLEAR(reason).
- R4: every run's import proof (start and end of the suite) must name a file under ``/work`` whose
  sha256 equals the bytes we wrote; any run that fails it makes the function BLIND(wrong-module).
- R6: mutant timeout = 3 x the median control time, clamped to [60, 300] s; controls get 300 s.
  Every box run waits while the 1-minute load average > 4.0. After the first control, the
  projected wall time sum((1 + 2 m_f) x median control) (every mutant re-run, the worst case)
  over the selected functions is compared with 4 h; over it, only the first N functions in source
  order that fit are run, the rest are BLIND(budget-cut ...). Before each function the elapsed
  time plus that function's projected cost is also checked against 4 h (BLIND(budget-cut:
  elapsed ...)), so a projection that was too low cannot run past the budget.
- R7: ``ghcache.check_cache(full=True)`` once before each module's runs; its result is in every
  row of that module.
- R8: the row schema below; ``outcome`` is MEASURED or BLIND(reason). k, s, u are recomputable
  from the per-mutant records.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import config
from . import box, ghcache, ghpool, plant

TOOL_VERSION = "github-facts/1 (S3)"
FACTS_NAME = "github_facts.jsonl"
RUNNER = "fg_unittest_json"
TIMEOUT_FACTOR, TIMEOUT_MIN, TIMEOUT_MAX = 3, 60.0, 300.0
CONTROL_TIMEOUT = TIMEOUT_MAX
LOAD_MAX = 4.0
LOAD_POLL_S = 15.0
LOAD_WAIT_MAX_S = 3600.0
BUDGET_S = 4 * 3600.0
RESOURCE_ERRORS = frozenset({"MemoryError", "RecursionError"})
MEASURED = "MEASURED"
CAUGHT, SURVIVED, UNCLEAR = "CAUGHT", "SURVIVED", "UNCLEAR"


@dataclass
class FactsResult:
    run_id: str
    rows: list = field(default_factory=list)
    facts_file: str = ""
    run_blind: str | None = None        # a run-level BLIND (R0, admission, box unavailable)

    @property
    def measured(self) -> int:
        return sum(1 for r in self.rows if r.get("outcome") == MEASURED)


def runner_file() -> Path | None:
    h = config.home()
    if h is not None and (Path(h) / "tools" / f"{RUNNER}.py").is_file():
        return Path(h) / "tools" / f"{RUNNER}.py"
    p = Path(__file__).resolve().parents[3] / "tools" / f"{RUNNER}.py"
    return p if p.is_file() else None


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_argv(module: str) -> list[str]:
    return ["env", "PYTHONHASHSEED=0", "python3", "-m", RUNNER, "--module", module]


def mutant_timeout(control_seconds: list[float]) -> float:
    med = statistics.median(control_seconds) if control_seconds else TIMEOUT_MAX / TIMEOUT_FACTOR
    return float(min(TIMEOUT_MAX, max(TIMEOUT_MIN, TIMEOUT_FACTOR * med)))


def _default_run_box(stage: Path, argv: list[str], timeout: float, label: str) -> box.BoxResult:
    return box.run(stage, argv, mode="run", timeout=timeout)


class _Ctx:
    def __init__(self, run_id, facts, run_box, loadavg, sleep, clock):
        self.run_id, self.facts, self.run_box = run_id, facts, run_box
        self.loadavg, self.sleep, self.clock = loadavg, sleep, clock
        self.control_seconds: list[float] = []
        self.started = clock()
        self.box_user = box.box_user()


def run(spec: str, functions: list[str] | None = None, *, gh_opts: dict | None = None, run_box=None,
        loadavg=os.getloadavg, sleep=time.sleep, clock=time.monotonic) -> FactsResult:
    """Measure every admitted function of ``spec`` (or only ``functions``); append the rows.
    ``gh_opts`` (list_file, signers_file, cache_root, trust_pin_value, host_sets_file) and
    ``run_box`` / ``loadavg`` / ``sleep`` / ``clock`` exist for tests."""
    facts = config.scout_dir() / FACTS_NAME   # NotConfigured propagates: no place for the rows
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
    ctx = _Ctx(run_id, facts, run_box or _default_run_box, loadavg, sleep, clock)
    res = FactsResult(run_id, facts_file=str(facts))
    opts = dict(gh_opts or {})

    def run_row(reason: str) -> FactsResult:
        res.run_blind = reason
        row = _base_row(ctx, spec, None, None, None)
        row["outcome"] = f"BLIND({reason})"
        row["scope"] = "run"
        _append(facts, row)
        res.rows.append(row)
        return res

    # R0: before anything is read or run.
    if not ctx.box_user:
        return run_row("box-user-unset")
    why = box.available()
    if why:
        return run_row(why)
    runner = runner_file()
    if runner is None:
        return run_row("runner-missing")
    m = ghpool._SPEC.fullmatch(spec or "")
    if not m:
        return run_row(f"unsigned: not org/repo@<40 hex>: {spec!r}")
    org, repo, commit = m.groups()
    root = Path(opts["cache_root"]) if opts.get("cache_root") is not None else ghcache.cache_root()
    try:
        store, tree_dir, _pin = ghcache._paths(root, org, repo, commit)
        tree = ghcache.tree_files(store, commit)
    except Exception as e:   # noqa: BLE001 - an unreadable cache is BLIND, never a traceback
        return run_row(f"pin-mismatch: cache unreadable: {type(e).__name__}: {e}"[:300])

    # Admission, read only: every .py path in the pinned tree goes through ghpool.admit.
    admitted: dict[str, bytes] = {}
    excluded: dict[str, int] = {}
    for path in sorted(p for p in tree if p.endswith(".py")):
        try:
            a = ghpool.admit(spec, path, **opts)
        except ghpool.NotAdmitted as e:
            reason = str(e)
            if reason.startswith(("unsigned", "pin-mismatch", "licence:")):
                return run_row(reason[:300])   # repo-level: nothing is measured
            excluded[reason.split(":")[0]] = excluded.get(reason.split(":")[0], 0) + 1
            continue
        admitted[path] = a.data
    ctx.admission = {"admitted_files": sorted(admitted), "excluded": excluded}

    # Selection in source order: files by path, functions as they appear.
    selected: list[tuple[str, str]] = []
    unparsable: list[str] = []
    for path, data in admitted.items():
        try:
            names = plant.functions(data)
        except plant.PlantError:
            unparsable.append(path)
            continue
        selected += [(path, n) for n in names]
    if functions:
        want = list(functions)
        keep = [(p, n) for p, n in selected if n in want or f"{p}::{n}" in want
                or f"{module_name(p)}:{n}" in want]
        matched = {n for _p, n in keep} | {f"{p}::{n}" for p, n in keep} | {f"{module_name(p)}:{n}" for p, n in keep}
        for w in want:
            if w not in matched:
                row = _base_row(ctx, spec, None, None, w)
                row["outcome"] = "BLIND(function-not-admitted)"
                _append(facts, row)
                res.rows.append(row)
        selected = keep

    plans: dict[tuple[str, str], object] = {}
    for path, name in selected:
        try:
            plans[(path, name)] = plant.plant(admitted[path], name)
        except plant.PlantError as e:
            plans[(path, name)] = e

    by_module: dict[str, list[str]] = {}
    for path, name in selected:
        by_module.setdefault(path, []).append(name)
    budget = {"n": None}
    order = list(selected)
    for path in sorted(by_module):
        _measure_module(ctx, res, spec, org, repo, commit, tree, tree_dir, root, path, admitted[path],
                        by_module[path], plans, runner, budget, order, opts)
    return res


def _measure_module(ctx, res, spec, org, repo, commit, tree, tree_dir, root, path, data, names, plans,
                    runner, budget, order, opts) -> None:
    mod = module_name(path)

    def emit(row):
        _append(ctx.facts, row)
        res.rows.append(row)

    def blind_all(reason, todo, extra=None):
        for n in todo:
            row = _base_row(ctx, spec, path, mod, n, extra)
            row["outcome"] = f"BLIND({reason})"
            emit(row)

    # The package chain must be importable from /work.
    parts = PurePosixPath(path).parts
    missing = [str(PurePosixPath(*parts[:i], "__init__.py")) for i in range(1, len(parts))
               if str(PurePosixPath(*parts[:i], "__init__.py")) not in tree]
    if missing:
        return blind_all(f"not-importable: no {missing[0]}", names)
    if f"{RUNNER}.py" in tree:
        return blind_all(f"runner-name-taken: the repo has its own {RUNNER}.py", names)
    # R7: the full tree check once per module, before its runs.
    try:
        verdict = ghpool.admit(spec, path, **opts).verdict
        pin = ghcache.check_cache(org, repo, commit, verdict, full=True, root=root)
        r7 = {"ok": True, "full": True, "tree": pin.get("tree")}
    except (ghpool.NotAdmitted, ghcache.PinMismatch) as e:
        return blind_all(f"pin-mismatch: {e}"[:300], names, {"tree_check": {"ok": False, "error": str(e)[:300]}})

    stage_root = box.box_root()
    stage_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="facts-", dir=stage_root))
    try:
        stage = tmp / "t"
        box._plain_copy(tree_dir, stage)
        shutil.copyfile(runner, stage / f"{RUNNER}.py")
        target = stage.joinpath(*PurePosixPath(path).parts)
        argv = test_argv(mod)
        prev_control = None
        for name in names:
            plan = plans[(path, name)]
            extra = {"tree_check": r7, "test_argv": argv}
            if isinstance(plan, plant.PlantError):
                row = _base_row(ctx, spec, path, mod, name, extra)
                row["outcome"] = f"BLIND(not-plantable: {plan})"[:300]
                emit(row)
                continue
            if budget["n"] is not None and order.index((path, name)) >= budget["n"]:
                row = _base_row(ctx, spec, path, mod, name, extra)
                row["outcome"] = f"BLIND(budget-cut: first {budget['n']} of {len(order)} functions fit {int(BUDGET_S)} s)"
                row["budget"] = dict(budget)
                emit(row)
                continue
            if not plan.mutants:
                row = _row_with_plan(ctx, spec, path, mod, name, plan, extra)
                row["outcome"] = "BLIND(no-mutants)"
                emit(row)
                continue
            if budget.get("median_control_s") is not None:
                elapsed = ctx.clock() - ctx.started
                need = (1 + 2 * len(plan.mutants)) * budget["median_control_s"]
                if elapsed + need > BUDGET_S:
                    row = _base_row(ctx, spec, path, mod, name, extra)
                    row["outcome"] = f"BLIND(budget-cut: elapsed {int(elapsed)} s + {int(need)} s > {int(BUDGET_S)} s)"
                    row["budget"] = dict(budget, elapsed_s=round(elapsed, 1))
                    emit(row)
                    continue
            before = prev_control or _box_run(ctx, stage, target, data, argv, CONTROL_TIMEOUT, "control")
            if budget["n"] is None:
                _budget(ctx, budget, order, plans)
                if budget["n"] is not None and order.index((path, name)) >= budget["n"]:
                    row = _base_row(ctx, spec, path, mod, name, extra)
                    row["outcome"] = f"BLIND(budget-cut: first {budget['n']} of {len(order)} functions fit {int(BUDGET_S)} s)"
                    row["budget"] = dict(budget)
                    emit(row)
                    prev_control = before
                    continue
            row = _row_with_plan(ctx, spec, path, mod, name, plan, extra)
            bad = _control_bad(before)
            if bad:
                row["controls"] = [_control_view(before), None]
                row["outcome"] = f"BLIND({bad})"
                emit(row)
                prev_control = None   # the next function takes a fresh control
                continue
            tmo = mutant_timeout(ctx.control_seconds)
            row["timeout_s"] = tmo
            records = []
            for mu in plan.mutants:
                first = _box_run(ctx, stage, target, mu.source, argv, tmo, f"mutant:{mu.id}")
                rerun = None
                if _wants_rerun(first, before):
                    rerun = _box_run(ctx, stage, target, mu.source, argv, tmo, f"rerun:{mu.id}")
                records.append((mu, first, rerun))
            target.write_bytes(data)
            after = _box_run(ctx, stage, target, data, argv, CONTROL_TIMEOUT, "control")
            prev_control = after
            row["controls"] = [_control_view(before), _control_view(after)]
            row["control_times"] = [before.get("seconds"), after.get("seconds")]
            row["box_python"] = (before.get("json") or {}).get("python")
            muts = [_classify(mu, first, rerun, before, after) for mu, first, rerun in records]
            row["mutants"] = muts
            row["k"] = sum(1 for x in muts if x["outcome"] == CAUGHT)
            row["s"] = sum(1 for x in muts if x["outcome"] == SURVIVED)
            row["u"] = sum(1 for x in muts if x["outcome"].startswith(UNCLEAR))
            row["outcome"] = _row_outcome(before, after, records, muts)
            emit(row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # our own staging folder, created above


# --- one box run -------------------------------------------------------------------------------------

def _wait_load(ctx) -> str | None:
    waited = 0.0
    while ctx.loadavg()[0] > LOAD_MAX:
        if waited >= LOAD_WAIT_MAX_S:
            return f"load-wait-exceeded: 1-min load > {LOAD_MAX} for {int(waited)} s"
        ctx.sleep(LOAD_POLL_S)
        waited += LOAD_POLL_S
    return None


def _box_run(ctx, stage: Path, target: Path, data: bytes, argv, timeout: float, label: str) -> dict:
    """Write ``data`` as the module, run the suite once in the box, parse and judge the proof.
    kind: ok (a parsed report), box-timeout, box-blind, no-json."""
    target.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    rec = {"label": label, "expected_sha": expected, "timeout_s": timeout}
    why = _wait_load(ctx)
    if why:
        rec.update(kind="box-blind", box=f"BLIND({why})", rc=None, seconds=None)
        return rec
    t0 = ctx.clock()
    r = ctx.run_box(stage, list(argv), timeout, label)
    secs = round(ctx.clock() - t0, 3)
    rec.update(box=r.outcome.label(), rc=r.returncode, seconds=secs)
    if r.outcome.kind != "FOUND":
        rec["kind"] = "box-timeout" if r.outcome.reason.startswith("timeout") else "box-blind"
        return rec
    try:
        rep = json.loads((r.stdout or "").strip())
        if not isinstance(rep, dict) or not isinstance(rep.get("results"), dict):
            raise ValueError("not a runner report")
    except ValueError:
        rec["kind"] = "no-json"
        rec["stderr_tail"] = (r.stderr or "")[-500:]
        return rec
    rec["kind"] = "ok"
    rec["json"] = rep
    rec["tests_run"] = rep.get("tests_run")
    res = rep["results"]
    rec["pass_set"] = sorted(t for t, v in res.items() if v.get("outcome") == "pass")
    rec["pass_set_sha256"] = hashlib.sha256("\n".join(rec["pass_set"]).encode()).hexdigest()
    rec["failing"] = sorted(t for t, v in res.items() if v.get("outcome") in ("fail", "error"))
    rec["error_classes"] = {t: res[t].get("exc") for t in rec["failing"]}
    rec["resource_errors"] = sorted(t for t in rec["failing"]
                                    if RESOURCE_ERRORS & set(res[t].get("exc_mro") or [res[t].get("exc")]))
    # R4: start and end proof; report the first one that fails (or the end one).
    module = rep.get("module") or {}
    proofs = [module.get("start") or {}, module.get("end") or {}]
    bad = [p for p in proofs if not _proof_ok(p, expected)]
    shown = bad[0] if bad else proofs[1]
    rec["module_file"], rec["module_sha"] = shown.get("file"), shown.get("sha256")
    rec["module_ok"] = not bad
    # review F2: the module did not import at all (a mutant broke it): no copy was loaded, so this
    # is not evidence of another copy; the mutant is UNCLEAR(import-error), the row unaffected.
    rec["import_error"] = module.get("import_error") if not (module.get("start") or {}).get("file") else None
    if label == "control" and r.returncode == 0:
        ctx.control_seconds.append(secs)
    return rec


def _proof_ok(p: dict, expected: str) -> bool:
    f = p.get("file")
    return (isinstance(f, str) and os.path.normpath(f).startswith("/work/") and p.get("sha256") == expected)


# --- judgement ------------------------------------------------------------------------------------------

def _control_bad(c: dict) -> str | None:
    if c["kind"] != "ok":
        return f"control-red: {c['kind']} {c.get('box', '')}".strip()
    if c.get("import_error"):
        return "control-red: import-error"
    if not c["module_ok"]:
        return "wrong-module"
    if c["rc"] != 0:
        return f"control-red: rc {c['rc']}"
    if not isinstance(c.get("tests_run"), int) or c["tests_run"] < 1 or not c["pass_set"]:
        return "no-tests"
    return None


def _control_view(c: dict) -> dict:
    return {"rc": c.get("rc"), "tests_run": c.get("tests_run"), "pass_set_sha256": c.get("pass_set_sha256"),
            "seconds": c.get("seconds"), "box": c.get("box"), "kind": c.get("kind"),
            "module_file": c.get("module_file"), "module_sha": c.get("module_sha"),
            "expected_sha": c.get("expected_sha")}


def _wants_rerun(first: dict, before: dict) -> bool:
    return (first["kind"] == "ok" and first["rc"] == 1 and first["module_ok"]
            and bool(set(first["failing"]) & set(before.get("pass_set") or [])))


def _classify(mu, first: dict, rerun: dict | None, before: dict, after: dict) -> dict:
    out = {"id": mu.id, "op": mu.op, "line": mu.line, "col": mu.col, "diff": mu.diff,
           "rc": first.get("rc"), "box": first.get("box"), "seconds": first.get("seconds"),
           "failing_ids": first.get("failing", []), "error_classes": first.get("error_classes", {}),
           "rerun_failing_ids": (rerun or {}).get("failing", []) if rerun else None,
           "rerun_rc": rerun.get("rc") if rerun else None,
           "module_file": first.get("module_file"), "module_sha": first.get("module_sha"),
           "expected_sha": first.get("expected_sha")}
    if rerun is not None:
        out["rerun_module_file"], out["rerun_module_sha"] = rerun.get("module_file"), rerun.get("module_sha")
    ctrl = set(before.get("pass_set") or []) & set(after.get("pass_set") or [])
    if first["kind"] == "box-timeout":
        out["outcome"] = "UNCLEAR(timeout)"
    elif first["kind"] == "box-blind":
        out["outcome"] = f"UNCLEAR(box: {first.get('box')})"
    elif first["kind"] == "no-json":
        out["outcome"] = "UNCLEAR(no-runner-report)"
    elif first.get("import_error"):
        out["outcome"] = "UNCLEAR(import-error)"
    elif not first["module_ok"] or (rerun is not None and rerun.get("kind") == "ok" and not rerun["module_ok"]):
        out["outcome"] = "UNCLEAR(wrong-module)"
    elif first["rc"] == 0:
        passed = set(first["pass_set"])
        out["outcome"] = SURVIVED if ctrl <= passed else "UNCLEAR(control-tests-not-passed)"
    elif first["rc"] != 1:
        out["outcome"] = f"UNCLEAR(rc {first['rc']})"
    elif first["resource_errors"]:
        cls = sorted({first["error_classes"][t] for t in first["resource_errors"]})
        out["outcome"] = f"UNCLEAR(resource-error: {','.join(cls)})"
    else:
        hit = set(first["failing"]) & ctrl
        if not hit:
            out["outcome"] = "UNCLEAR(failed-only-outside-control)"
        elif rerun is None or rerun.get("kind") != "ok" or rerun.get("rc") != 1:
            out["outcome"] = "UNCLEAR(rerun-not-failing)"
        elif rerun.get("resource_errors"):
            out["outcome"] = "UNCLEAR(resource-error-on-rerun)"
        elif not (hit & set(rerun["failing"])):
            out["outcome"] = "UNCLEAR(rerun-not-failing)"
        else:
            out["outcome"] = CAUGHT
    return out


def _row_outcome(before, after, records, muts) -> str:
    runs = [before, after] + [r for _mu, a, b in records for r in (a, b) if r is not None]
    if any(r.get("kind") == "ok" and not r["module_ok"] and not r.get("import_error") for r in runs):
        return "BLIND(wrong-module)"
    bad = _control_bad(after)
    if bad:
        return f"BLIND({bad})"
    if before["pass_set_sha256"] != after["pass_set_sha256"]:
        return "BLIND(control-unstable)"
    boxbad = [r for r in runs if r.get("kind") == "box-blind"]
    if boxbad:
        return f"BLIND(box: {boxbad[0].get('box')})"[:300]
    if not muts:
        return "BLIND(no-mutants)"
    if not any(x["outcome"] in (CAUGHT, SURVIVED) for x in muts):
        # review F1: nothing was decided, so nothing was measured (e.g. every mutant timed out)
        return "BLIND(all-mutants-unclear)"
    return MEASURED


def _budget(ctx, budget: dict, order, plans) -> None:
    """R6: after the first control, project the run; over BUDGET_S, keep the first N that fit."""
    if not ctx.control_seconds:
        return
    med = statistics.median(ctx.control_seconds)
    cost = []
    for key in order:
        p = plans.get(key)
        m = len(p.mutants) if isinstance(p, plant.Plan) else 0
        cost.append((1 + 2 * m) * med if m else 0.0)   # review F3: a caught mutant is re-run
    projected = med + sum(cost)
    budget["projected_s"] = round(projected, 1)
    budget["median_control_s"] = med
    if projected <= BUDGET_S:
        budget["n"] = len(order)
        return
    total, n = med, 0
    for c in cost:
        if total + c > BUDGET_S:
            break
        total += c
        n += 1
    budget["n"] = n
    budget["cut"] = True


# --- rows -------------------------------------------------------------------------------------------------

def _base_row(ctx, spec, path, mod, function, extra=None) -> dict:
    row = {"run_id": ctx.run_id, "tool_version": TOOL_VERSION, "spec": spec, "file": path, "module": mod,
           "function": function, "box_user": ctx.box_user, "host_python": platform.python_version(),
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "trust": "raw", "kind": "fact"}
    if getattr(ctx, "admission", None) is not None:
        row["admission"] = ctx.admission
    if extra:
        row.update(extra)
    return row


def _row_with_plan(ctx, spec, path, mod, name, plan, extra) -> dict:
    row = _base_row(ctx, spec, path, mod, name, extra)
    row.update(m=len(plan.mutants), k=0, s=0, u=0, not_planted=plan.not_planted,
               version_gated=plan.version_gated, candidates=plan.candidates, capped=plan.capped,
               dropped=[list(d) for d in plan.dropped], controls=[None, None], mutants=[])
    return row


def _append(facts: Path, row: dict) -> None:
    with open(facts, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
