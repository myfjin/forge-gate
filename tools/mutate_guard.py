#!/usr/bin/env python3
"""Surgical defence-removal mutation harness for the Forge Gate GUARD lens.

WHY THIS EXISTS
---------------
tests/verify_guard_lens.sh asks "does the lens pass on good input and fail on
bad input?". It never asks the question that matters:

    IF I DELETE THIS DEFENCE, DOES ANY GATE GO RED?

Each mutant below removes EXACTLY ONE named defence from guard_lens.py and is
required to be KILLED by the suite. A SURVIVOR is a defence that defends
nothing -- the suite cannot tell whether it is there.

This is DIFFERENT from tools/xmt_harness.py, which mutates the signed AURA
*oracle* to test whether *fixtures* are hollow. This mutates the *lens* to test
whether the lens's own acceptance suite is hollow.

LAWS (inherited from tools/mutate_audit.py in aura-pattern-library)
-------------------------------------------------------------------
  * n = src.count(old); n != 1  ->  SKIPPED -> DARK -> exit 1.
    A mutant whose anchor no longer matches is NOT a pass. It is a defence
    that stopped being reachable by this harness, and it must fail the run.
  * A mutant that does not parse is not a result -> GENERATION-FAILED.
  * A mutant that could not be measured (timeout) is not a result ->
    INCONCLUSIVE. It never counts as KILLED.
  * SURVIVED != SKIPPED. Reported separately, never summed.
  * The file is restored and the restore is verified with THIS harness's own
    md5 against the pristine digest captured at startup.

NOTE ON THE REFORMAT CONFOUND
-----------------------------
This harness edits raw text, so unlike an ast.unparse-based runner it does not
restyle the untouched parts of the file. There is therefore no reformat
confound to control for. Instead the null arm is CONTROL-1 below: a mutant that
changes only a comment and MUST SURVIVE. If CONTROL-1 is KILLED, the suite is
sensitive to source text rather than to behaviour, and every verdict in the run
is VOID.
"""
from __future__ import annotations

import argparse
import ast
import json
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class Mutant:
    mid: str
    defence: str
    old: str
    new: str
    expect: str          # "KILLED" (a real defence) or "SURVIVED" (control)
    killer: str          # which gate we expect to notice


def _lcp(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _lcs(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(1, n + 1):
        if a[-i] != b[-i]:
            return i - 1
    return n


def _anchor_over_extends(old: str, new: str, target_text: str) -> Optional[str]:
    # Ported from aura-pattern-library/tools/mutate_audit.py (4Q rounds 31/32).
    # NOT a minimality proof (6E round 32 S4.1): detects one class only -- the
    # anchor extends past the changed region on the right. Return the trimmed
    # candidate (strictly shorter, still unique in target) if the anchor could
    # be shorter; None otherwise. Common-suffix trimming keeps a leading
    # separator (operator keyword+space, or punctuation) because splitting an
    # identifier or string would be a semantic change, not a context trim.
    import re as _re
    s = _lcs(old, new)
    if s == 0:
        return None
    common_suffix = old[len(old) - s:]
    sep = _re.match(r"((?:or|and|not|in|is)\s+|[^\w]+)", common_suffix)
    # sep=None: common suffix starts with a word char (part of a token or
    # identifier). Keep the full old -- do NOT trim into a token. 4Q
    # 2026-09-05 attack A+D: dropping the suffix here flags M13 (True vs
    # False -> common suffix "e") as if the anchor over-extended, and would
    # also over-trim any anchor whose common suffix starts with a digit or _.
    if sep is None:
        trimmed = old
    else:
        trimmed = old[:len(old) - s] + sep.group(1)
    if len(trimmed) >= len(old):
        return None
    if target_text.count(trimmed) == 1:
        return trimmed
    return None


MUTANTS: List[Mutant] = [
    # ---------------------------------------------------------------- CONTROL
    Mutant(
        "CONTROL-1",
        "NULL ARM: changes a comment only. Behaviour is identical.",
        "# Hard wall-clock timeout for a single fixture run.",
        "# Hard wall-clock timeout for a single fixture run. [null-arm marker]",
        "SURVIVED",
        "none -- if this is KILLED the suite reads source text, run is VOID",
    ),

    # ------------------------------------------------------- verdict machinery
    Mutant(
        "M01-gate-off",
        "THE GATE ITSELF: nan_objections no longer produce FAIL.",
        '    if nan_objections:\n        return "FAIL"',
        '    if False:\n        return "FAIL"',
        "KILLED",
        "red-proof: unguarded fixture rejected",
    ),
    Mutant(
        "M02-inconclusive-off",
        "FAIL-OPEN: unexercised run returns PASS instead of INCONCLUSIVE.",
        "    if not exercised:",
        "    if False:",
        "KILLED",
        "any gate asserting INCONCLUSIVE on unreachable-only fixtures",
    ),
    Mutant(
        "M03-tolerance-swallows-all",
        "Every real defect is downgraded to the tolerance bucket -> PASS.",
        "                    if is_tol:",
        "                    if True:",
        "KILLED",
        "reached_param.py / coupled_fabrication.py: lens objects",
    ),

    # ----------------------------------------------------- reachability rules
    Mutant(
        "M04-reachability-off",
        "Unreachable params no longer recognised; lens accuses independent code.",
        "                if all(_canonical(r) == _canonical(c) for c, r in shape_observations):",
        "                if False:",
        "KILLED",
        "unreached_param.py: lens accepts unreachable poisoned param",
    ),
    Mutant(
        "M05-componentwise-off",
        "Componentwise independence removed; separable results become defects.",
        "                    if all(d == 0 for d in defects_per_shape):",
        "                    if False:",
        "KILLED",
        "separable_gradient.py: lens accepts separable gradient",
    ),
    Mutant(
        "M06-propagation-is-defect",
        "Correct NaN/Inf propagation is miscounted as a defect.",
        "        if not math.isfinite(p):\n            continue",
        "        if False:\n            continue",
        "KILLED",
        "count_and_nan.py / canonical oracles stay clean",
    ),
    Mutant(
        "M07-length-mismatch-blind",
        "A NaN that changes the SHAPE of the result no longer counts.",
        "        return sum(1 for v in poisoned_floats if math.isfinite(v))",
        "        return 0",
        "KILLED",
        "coupled_fabrication.py / shape_dependent_coincidence.py",
    ),

    # ------------------------------------------------------- probe machinery
    Mutant(
        "M08-single-shape",
        "Multi-shape probing disabled; shape-dependent traps become invisible.",
        "MULTI_SHAPE_COUNT = 4",
        "MULTI_SHAPE_COUNT = 1",
        "KILLED",
        "shape_dependent_coincidence.py: lens detects shape-dependent NaN trap",
    ),
    Mutant(
        "M09-no-literal-harvest",
        "Function's own literals no longer probed; membership branches unreachable.",
        "        fn_literals = _harvest_numeric_literals(fn)",
        "        fn_literals = []",
        "KILLED",
        "literal_membership.py / retry_http.py: param seen as reached",
    ),
    Mutant(
        "M10-no-inf-probe",
        "Inf poisons removed entirely; only NaN is ever tried.",
        'POISON_INF = [float("inf"), float("-inf")]',
        "POISON_INF = []",
        "KILLED",
        "any gate recording Inf observations",
    ),

    # --------------------------------------------------- honesty / bookkeeping
    Mutant(
        "M11-u6-off",
        "U6 removed: functions with unsynthesisable siblings falsely exercised.",
        "        if has_unable_sibling:",
        "        if False:",
        "KILLED",
        "scope_fixture.py: scope counts add up and match truth",
    ),
    Mutant(
        "M12-killed-probe-counts",
        "A CPU-killed clean baseline is treated as a real observation.",
        "                            if clean_killed:",
        "                            if False:",
        "KILLED",
        "scope_fixture.py / any resource-limit gate",
    ),
    Mutant(
        "M13-raise-not-handled",
        "A correct loud failure no longer credits the param as judged.",
        "                            param_handled_raise = True",
        "                            param_handled_raise = False",
        "KILLED",
        "scope_fixture.py: scope counts add up and match truth",
    ),
    Mutant(
        "M14-degenerate-float-blind",
        "All-zero float baselines accepted as valid reachability signal.",
        "    if isinstance(obj, float):\n        return obj == 0.0",
        "    if isinstance(obj, float):\n        return False",
        "KILLED",
        "any gate relying on degenerate-baseline rejection",
    ),
    Mutant(
        "M15-degenerate-empty-blind",
        "Container degeneracy removed entirely: empty/all-zero lists accepted "
        "as valid baselines. (The narrower `if len(obj)==0` edit is a SEMANTIC "
        "NO-OP -- all([]) is True, so the next line returns True anyway. A mutant "
        "that cannot change behaviour is not evidence in either direction.)",
        "    if isinstance(obj, (list, tuple)):\n        if len(obj) == 0:\n            return True\n        return all(_is_degenerate(x) for x in obj)",
        "    if isinstance(obj, (list, tuple)):\n        return False",
        "KILLED",
        "any gate relying on degenerate-baseline rejection",
    ),
    Mutant(
        "M17-aborted-is-unreachable",
        "A resource-killed probe is filed as `unreachable` -- a bucket whose "
        "definition is 'every OBSERVED poison returned output identical to the "
        "clean baseline' -- when nothing was observed at all. Found by 6E on "
        "2026-09-04 and reproduced with controls: an aborted parameter and a "
        "genuinely unreachable one emitted BYTE-IDENTICAL scope columns, separated "
        "only by the verdict word. report.py renders those columns without "
        "recomputing them, so the two were indistinguishable downstream too.",
        "            elif param_probe_aborted:",
        "            elif param_probe_aborted and False:",
        "KILLED",
        "poison_killed.py: killed poison probe is ABORTED, never unreachable",
    ),
]


# Which named gate in verify_guard_lens.sh SHOULD go red for each mutant.
# None = "I do not yet know"; the run RECORDS what actually failed instead of
# asserting. Storing the observation rather than a guessed verdict is how the
# mapping gets earned. (verify_js_selftest.sh does the same thing for JS: it
# classifies a kill as "for the RIGHT REASON" rather than trusting exit code.)
KILLER_KEYS = {
    "M01-gate-off": "red-proof",
    "M03-tolerance-swallows-all": "reached_param.py",
    "M04-reachability-off": "unreached_param.py",
    "M05-componentwise-off": "separable_gradient.py",
    "M08-single-shape": "shape_dependent_coincidence.py",
    "M09-no-literal-harvest": "literal_membership.py",
    "M11-u6-off": "scope_fixture.py",
    "M13-raise-not-handled": "raise_only_guard.py",
    # Added 2026-09-04 with the honesty-path gates that close the 8 survivors.
    "M02-inconclusive-off": "zero_baseline.py",
    "M07-length-mismatch-blind": "shape_shift_fabrication.py",
    "M10-no-inf-probe": "Inf probes actually ran",
    "M11-u6-off": "unsynthesisable_sibling.py",
    "M12-killed-probe-counts": "killed_baseline.py",
    "M14-degenerate-float-blind": "zero_baseline.py",
    "M15-degenerate-empty-blind": "empty_baseline.py",
    "M17-aborted-is-unreachable": "poison_killed.py",
    "M04-reachability-off": "unused_scalar.py",
    "M06-propagation-is-defect": "propagate_nan.py",
}


def failing_gates(stdout: str) -> List[str]:
    """Gate names the suite marked FAIL. Exit code alone is a summary line that
    hides per-item verdicts; this reads the per-item verdicts."""
    out = []
    for line in stdout.splitlines():
        if "\u274c" in line:
            out.append(line.split("\u274c")[0].strip())
    return out


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run_test(cmd: str, cwd: Path, timeout: float):
    """Return (rc, note, stdout). rc is None when we could not measure."""
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout, start_new_session=True,
        )
        return proc.returncode, "", proc.stdout or ""
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s", ""
    except Exception as exc:  # harness failure is never a verdict
        return None, f"harness failure: {type(exc).__name__}: {exc}", ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # NOTE: --config exists because the deploy host's guard-lens-watchdog.sh
    # (ops/INVENTORY.md) SIGKILLs
    # any python3 process older than 300s whose CMDLINE contains "guard_lens",
    # by process group. Passing the target on the command line therefore gets
    # this harness killed mid-mutation, leaving the target file mutated.
    # See the session report: this is how it destroyed an xmt.py run at 21:12.
    ap.add_argument("--config", help="JSON file with target/test/cwd/timeout")
    ap.add_argument("--target")
    ap.add_argument("--test")
    ap.add_argument("--cwd")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Check anchors and mutant parseability only. No test runs.")
    ap.add_argument("--only", help="Comma-separated mutant ids")
    args = ap.parse_args(argv[1:] if argv else None)
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        args.target = cfg["target"]
        args.test = cfg["test"]
        # A relative cwd is taken from the config file's own folder, so the shipped
        # config ("cwd": "..") names this checkout wherever it lives (build step 1).
        cwd_raw = Path(cfg["cwd"])
        args.cwd = str(cwd_raw if cwd_raw.is_absolute()
                       else Path(args.config).resolve().parent / cwd_raw)
        args.timeout = float(cfg.get("timeout", args.timeout))
    if not (args.target and args.test and args.cwd):
        ap.error("need --config, or all of --target/--test/--cwd")

    cwd = Path(args.cwd).resolve()
    target = Path(args.target)
    target = (target if target.is_absolute() else cwd / target).resolve()

    # ---------------------------------------------------------- CRASH SAFETY
    # An in-place mutation tool that is SIGKILLed leaves the target MUTATED.
    # This is not hypothetical: guard-lens-watchdog.sh kill -9'd an xmt.py run
    # at 21:12 on 2026-09-03 and stranded a mutant in the live forge-gate tree.
    # So: park a pristine copy plus a lock BEFORE touching anything, and heal
    # automatically if a previous run died holding the lock.
    pristine_park = target.with_suffix(target.suffix + ".PRISTINE")
    lock = target.with_suffix(target.suffix + ".MUTATION-LOCK")
    if lock.exists() and pristine_park.exists():
        target.write_bytes(pristine_park.read_bytes())
        print(f"[heal] previous run died holding the lock; restored {target.name}")
        lock.unlink()
    # Only ARM the lock for a run that actually mutates. A --dry-run that leaves
    # a lock behind makes the next run print "previous run died holding the lock"
    # when nothing died -- a guard that cries wolf gets ignored.
    if not args.dry_run:
        pristine_park.write_bytes(target.read_bytes())
        lock.write_text("held\n")

    pristine_bytes = target.read_bytes()
    pristine_md5 = hashlib.md5(pristine_bytes).hexdigest()
    pristine_src = pristine_bytes.decode("utf-8")
    print(f"target      : {target}")
    print(f"pristine md5: {pristine_md5}")

    selected = MUTANTS
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        selected = [m for m in MUTANTS if m.mid in want]

    # ------------------------------------------------ PRE-FLIGHT: anchor audit
    # Do this for EVERY mutant before running any test, so anchor rot is a
    # single loud failure at the top rather than a slow drip through a 45-minute
    # run.
    dark: List[str] = []
    genfail: List[str] = []
    prepared = {}
    for m in selected:
        n = pristine_src.count(m.old)
        if n != 1:
            dark.append(f"{m.mid}: anchor matched {n} times (need exactly 1)")
            continue
        mutated = pristine_src.replace(m.old, m.new, 1)
        if mutated == pristine_src:
            genfail.append(f"{m.mid}: replacement was a no-op")
            continue
        try:
            ast.parse(mutated)
        except SyntaxError as exc:
            genfail.append(f"{m.mid}: mutant does not parse: {exc}")
            continue
        prepared[m.mid] = mutated

    print(f"\n--- ANCHOR AUDIT ---")
    print(f"prepared        : {len(prepared)}/{len(selected)}")
    print(f"SKIPPED (dark)  : {len(dark)}")
    for d in dark:
        print(f"  ! {d}")
    print(f"GENERATION-FAIL : {len(genfail)}")
    for g in genfail:
        print(f"  ! {g}")

    # ---- ANCHOR RIGHT-EXTENSION GUARD + BASELINE RATCHET (BRIEF S3.4) ----
    # Ported from aura-pattern-library. An anchor that is unique today can rot
    # tomorrow: the code to the right of the changed region drifts and the
    # anchor stops matching (n=0 -> SKIPPED -> DARK). This guard flags every
    # mutant whose "old" could be trimmed and still be unique. The baseline
    # file lists accepted extensions and MAY ONLY SHRINK -- growth is a bug.
    baseline_path = Path(__file__).parent / "mutate_guard.baseline"
    baseline = set()
    if baseline_path.exists():
        for _bl in baseline_path.read_text().splitlines():
            _bl = _bl.rstrip()
            if _bl and not _bl.lstrip().startswith("#"):
                baseline.add(ast.literal_eval(_bl))
    new_flags = []
    known = 0
    flagged_olds = set()
    print(f"\n--- ANCHOR RIGHT-EXTENSION GUARD (baseline ratchet) ---")
    for m in selected:
        trimmed = _anchor_over_extends(m.old, m.new, pristine_src)
        if trimmed is None:
            continue
        flagged_olds.add(m.old)
        if m.old in baseline:
            known += 1
        else:
            new_flags.append((m.mid, trimmed))
            print(f"  *** NEW FLAG: {m.mid}")
            print(f"      trimmed={trimmed[:70]!r}  (len {len(trimmed)} < {len(m.old)})")
    stale = baseline - flagged_olds
    for _s in sorted(stale):
        print(f"  *** PAID DOWN: {_s[:70]!r} no longer flagged -- remove from baseline")
    print(f"  baseline: {known} known, {len(new_flags)} new, {len(stale)} paid down")
    if new_flags or stale:
        print("*** ANCHOR GUARD FAILED ***")
        if new_flags:
            print(f"    {len(new_flags)} NEW flag(s) -- fix the anchor or deliberately add to baseline")
        if stale:
            print(f"    {len(stale)} stale baseline entry(ies) -- remove from tools/mutate_guard.baseline")
        if not args.dry_run:
            if lock.exists():
                lock.unlink()
            if pristine_park.exists():
                pristine_park.unlink()
        return 1

    if args.dry_run:
        print("\n[dry-run] no tests executed")
        return 0 if not dark and not genfail else 1

    # ------------------------------------------------------ BASELINE (null arm)
    print(f"\n--- BASELINE ---")
    rc, note, _ = run_test(args.test, cwd, args.timeout)
    if rc is None:
        print(f"BASELINE COULD NOT BE MEASURED: {note}")
        print("This is NOT a red baseline. Nothing below would mean anything.")
        return 2
    if rc != 0:
        print(f"BASELINE RED (rc={rc}). Every verdict would be meaningless.")
        return 2
    print("baseline: GREEN (rc=0)")

    # ------------------------------------------------------------- the mutants
    killed, survived, inconclusive = [], [], []
    results = []
    try:
        for m in selected:
            if m.mid not in prepared:
                continue
            target.write_text(prepared[m.mid])
            rc, note, stdout = run_test(args.test, cwd, args.timeout)
            gates = failing_gates(stdout)
            target.write_bytes(pristine_bytes)
            if md5(target) != pristine_md5:
                print(f"\n!!! RESTORE FAILED after {m.mid}. Stopping. !!!")
                return 2

            if rc is None:
                verdict = "INCONCLUSIVE"
                inconclusive.append(m.mid)
            elif rc != 0:
                verdict = "KILLED"
                killed.append(m.mid)
            else:
                verdict = "SURVIVED"
                survived.append(m.mid)
            key = KILLER_KEYS.get(m.mid)
            if verdict != "KILLED" or key is None:
                reason = "n/a" if verdict != "KILLED" else "UNSPECIFIED"
            elif any(key in g for g in gates):
                reason = "RIGHT-REASON"
            else:
                reason = "WRONG-REASON"
            results.append((m, verdict, note, gates, reason))
            flag = "" if verdict == m.expect else "   <-- UNEXPECTED"
            print(f"{m.mid:28s} {verdict:13s} reason={reason:12s} (expected {m.expect}){flag}")
            if gates:
                print(f"    gates that went RED ({len(gates)}): {gates[:4]}")
            if note:
                print(f"    note: {note}")
    finally:
        target.write_bytes(pristine_bytes)
        if lock.exists():
            lock.unlink()
        if pristine_park.exists():
            pristine_park.unlink()

    # ------------------------------------------------------------------ report
    print("\n" + "=" * 74)
    print("MUTATION REPORT -- GUARD LENS DEFENCES")
    print("=" * 74)
    for m, verdict, _, gates, reason in results:
        mark = "ok " if verdict == m.expect else "!! "
        print(f"{mark}{m.mid}")
        print(f"     defence : {m.defence}")
        print(f"     verdict : {verdict}   (expected {m.expect})  reason={reason}")
        print(f"     expected killer : {m.killer}")
        print(f"     gates RED       : {gates if gates else '(none)'}")

    print("\n" + "-" * 74)
    print(f"KILLED        : {len(killed)}")
    print(f"SURVIVED      : {len(survived)}   {survived}")
    print(f"INCONCLUSIVE  : {len(inconclusive)}   {inconclusive}")
    print(f"SKIPPED(dark) : {len(dark)}")
    print(f"GEN-FAILED    : {len(genfail)}")
    print(f"final md5     : {md5(target)}  (pristine {pristine_md5})")

    # --------------------------------------------------------------- null arm
    control = [(m, v) for m, v, _, _, _ in results if m.expect == "SURVIVED"]
    void = [m.mid for m, v in control if v != "SURVIVED"]
    if void:
        print("\n*** RUN IS VOID ***")
        print(f"Null-arm mutant(s) {void} were KILLED. The suite responds to")
        print("source text, not behaviour. No verdict above is trustworthy.")
        return 2
    if control:
        print("\nnull arm: PASS (comment-only mutant survived, as required)")

    real_survivors = [m.mid for m, v, _, _, _ in results
                      if m.expect == "KILLED" and v == "SURVIVED"]
    wrong_reason = [m.mid for m, v, _, _, r in results if r == "WRONG-REASON"]
    if wrong_reason:
        print(f"\nFINDING: {len(wrong_reason)} mutant(s) were KILLED, but NOT by the")
        print("gate that is supposed to cover them. The kill is an unrelated failure:")
        for w in wrong_reason:
            print(f"  * {w}")
    if real_survivors:
        print(f"\nFINDING: {len(real_survivors)} defence(s) are UNDEFENDED by the suite:")
        for s in real_survivors:
            print(f"  * {s}")
    if real_survivors or wrong_reason or dark or genfail or inconclusive:
        return 1
    print("\nAll defences killed. The suite genuinely tests each one.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
