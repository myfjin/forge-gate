#!/usr/bin/env python3
"""CLI entry point for Forge Gate."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .catalog import list_patterns, resolve_pattern, CatalogError
from .lenses import ALL_LENSES, DEFAULT_LENSES, evaluate, parse_lens_selection, render
from .runner import run_file
from .verifier import verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_TIMEOUT = 60.0         # verify's defaults; a run above either is looser (8a crew review, 6E D3)
VERIFY_MEMORY_MB = 512
CORPUS_ITEM = re.compile(r"corpus:c_[0-9a-f]{16}", re.ASCII)   # D2: content-addressed corpus item
METAMORPHIC_TOOLS_DIR = REPO_ROOT / "tools"


def _fixture_script(name: str) -> Path:
    """Return the path to a fixture script inside the package."""
    return Path(__file__).resolve().parent / "fixtures" / f"{name}.py"


def cmd_list(_args: argparse.Namespace) -> int:
    """List gate-ready patterns."""
    patterns = list_patterns()
    print(f"Forge Gate v1 catalog: {len(patterns)} patterns ready")
    print("-" * 50)
    for name, meta in sorted(patterns.items()):
        print(f"  {name:30}  {meta['category']:15}  {meta['description']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a user file against a pattern oracle."""
    user_file = Path(args.user_file).expanduser().resolve()
    if not user_file.exists():
        print(f"FAIL: user file not found: {user_file}", file=sys.stderr)
        return 2

    try:
        pattern_meta = resolve_pattern(args.pattern)
    except CatalogError as exc:
        print(f"UNSUPPORTED: {exc}", file=sys.stderr)
        return 3

    fixture_script = _fixture_script(pattern_meta["fixture"])
    if not fixture_script.exists():
        print(f"UNSUPPORTED: fixture missing for {args.pattern}", file=sys.stderr)
        return 3

    # The bytes the oracle hook may credit are read ONCE, before anything runs (8a s3 review F3).
    try:
        judged = user_file.read_bytes()
    except OSError:
        judged = None

    # Run the user's implementation through the fixture harness.
    user_result = run_file(
        fixture_script,
        extra_args=[str(user_file)],
        timeout=args.timeout,
        no_network=not args.allow_network,
        memory_mb=args.memory_mb,
    )

    # Run the signed AURA oracle itself as the ground-truth reference.
    oracle_result = run_file(
        pattern_meta["aura_path"],
        timeout=args.timeout,
        no_network=not args.allow_network,
        memory_mb=args.memory_mb,
    )

    v = verdict(
        pattern=args.pattern,
        user_file=user_file,
        oracle_result=oracle_result,
        user_result=user_result,
        tolerance=args.tolerance,
    )

    # KEYSTONE: one verdict from every selected lens. See forge_gate/lenses.py for
    # the algebra. Supplementary lenses may only DOWNGRADE a property-primary PASS;
    # they can never overturn a property-primary FAIL.
    try:
        selected = parse_lens_selection(args.lenses)
    except ValueError as exc:
        print(f"UNSUPPORTED: {exc}", file=sys.stderr)
        return 3

    gate = evaluate(
        pattern=args.pattern,
        user_file=user_file,
        base_status=v.status,
        base_reason=v.reason,
        base_metrics=v.metrics,
        lenses=selected,
        timeout=args.timeout,
        short_circuit=not args.no_short_circuit,
    )

    if args.json:
        print(json.dumps(gate.as_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(render(gate, show_table=not args.quiet))

    note = _oracle_outcome(user_file, args.pattern, gate.status, _looser_than_default(args, selected), judged)
    if note:
        print(f"forge-gate verify: {note}", file=sys.stderr)
    return 0 if gate.status == "PASS" else 1


# Writer ``oracle-run`` waits at most this long for another ledger writer.
ORACLE_WAIT = 10.0


def _looser_than_default(args, selected) -> str:
    """'' for a run at least as strict as the default, else how it was looser (review F4)."""
    why = []
    if args.tolerance > 1e-5:
        why.append(f"tolerance {args.tolerance:g}")
    if args.allow_network:
        why.append("network allowed")
    if args.timeout > VERIFY_TIMEOUT:
        why.append(f"timeout {args.timeout:g} s")
    if args.memory_mb > VERIFY_MEMORY_MB:
        why.append(f"memory {args.memory_mb} MB")
    missing = [x for x in DEFAULT_LENSES if x not in selected]
    if missing:
        why.append("lenses off: " + ",".join(missing))
    return "; ".join(why)


def _oracle_outcome(user_file: Path, pattern: str, status: str, looser: str, judged) -> str:
    """Writer ``oracle-run`` (design §4a.5 V4-2, build step 8a s3, DECISIONS-8a G4 a).

    After the final verdict: a PASS on a file that carries ``forge-gate-result`` ids (handed out by
    ``scout --show``) appends one oracle outcome per id, all with this run's id. The verdict and
    exit code never depend on the ledger. Returns a note for stderr ("" when there is nothing to
    say). Called only from cmd_verify (tests/test_ledger_routes.py)."""
    from . import ledger

    if not ledger.enabled():
        return ""                                   # ledgers off: verify behaves exactly as before 8a
    try:
        return "ledger: " + _oracle_notes(user_file, pattern, status, looser, judged)
    except Exception as e:                          # the verdict is printed already; never break it (F5)
        return f"ledger: BLIND(unexpected {type(e).__name__}: {e}); outcomes not all recorded"


def _oracle_notes(user_file: Path, pattern: str, status: str, looser: str, judged) -> str:
    import hashlib
    import time

    from . import config, ledger
    from .scout import report

    if status != "PASS":
        return f"verdict {status} is not an outcome; nothing recorded"
    if looser:
        return f"run looser than the default ({looser}): not an outcome; nothing recorded"
    if judged is None:
        return f"could not read {user_file.name} before the run; nothing recorded"
    text = judged.decode("utf-8", errors="replace")
    rids = ledger.result_ids_in(text)
    if not rids:
        return "no forge-gate-result id in the file; nothing recorded"
    run_id, digest = ledger.new_run_id(), hashlib.sha256(judged).hexdigest()
    deadline = time.monotonic() + ORACLE_WAIT       # one wait for all ids (review F7)
    notes = []
    for rid in rids:
        why = report.snippet_missing(rid, text)
        if why:
            notes.append(f"{rid}: refused: {why}")
            continue
        try:
            ledger.record_oracle(result_id=rid, run_id=run_id, oracle=f"verify:{pattern}", verdict=status,
                                 verified_file=user_file, content_sha256=digest,
                                 wait=max(0.0, deadline - time.monotonic()))
            notes.append(f"{rid}: oracle outcome recorded (run {run_id})")
        except ledger.Refused as e:
            notes.append(f"{rid}: refused: {e}")
        except (ledger.LedgerError, config.NotConfigured, OSError) as e:
            notes.append(f"{rid}: BLIND(ledger-unwritable: {e})")
    return "; ".join(notes)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `ledger event ... -- <cmd...>`: the person's command is split off HERE, before argparse, so
    # none of its words can be read as our options (argparse's REMAINDER after a positional rejects
    # `event MARK --project P -- cmd`, measured on 3.10 and 3.11).
    event_cmd = None
    if argv[:2] == ["ledger", "event"] and "--" in argv:
        cut = argv.index("--")
        argv, event_cmd = argv[:cut], argv[cut + 1:]
    ap = argparse.ArgumentParser(
        prog="forge-gate",
        description="Deterministic regression gate for code-generating agents.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list gate-ready patterns")
    list_parser.set_defaults(func=cmd_list)

    verify_parser = sub.add_parser("verify", help="verify a user file against a pattern oracle")
    verify_parser.add_argument("user_file", help="Python file to verify")
    verify_parser.add_argument("--pattern", required=True, help="pattern name from catalog")
    verify_parser.add_argument("--timeout", type=float, default=VERIFY_TIMEOUT, help="seconds (default 60)")
    verify_parser.add_argument("--tolerance", type=float, default=1e-5, help="numeric comparison tolerance")
    verify_parser.add_argument("--allow-network", action="store_true", help="do not drop network namespace")
    verify_parser.add_argument("--memory-mb", type=int, default=VERIFY_MEMORY_MB, help="memory limit in MB")
    verify_parser.add_argument(
        "--lenses",
        default="default",
        help="'default'/'all' = every lens ("
        + ", ".join(ALL_LENSES)
        + "), or a comma-separated subset",
    )
    verify_parser.add_argument(
        "--show-lenses", action="store_true", help="(deprecated: the per-lens table is now shown by default)"
    )
    verify_parser.add_argument(
        "--quiet", action="store_true", help="suppress the per-lens table, print the verdict only"
    )
    verify_parser.add_argument(
        "--no-short-circuit",
        action="store_true",
        help="run every lens even after property-primary fails (evidence gathering; verdict unchanged)",
    )
    verify_parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    verify_parser.set_defaults(func=cmd_verify)

    scout_parser = sub.add_parser(
        "scout", help="advisory: what is this code related to? (no verdict; slice 1: python, corpus pool, shape lens)")
    scout_parser.add_argument("file", nargs="?", help="file to scout (read as text, never run)")
    scout_parser.add_argument("--show", metavar="RESULT_ID",
                              help="print the code behind a result id, its id as the first line (step 8a)")
    scout_parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    scout_parser.add_argument("--intent", metavar="TEXT",
                              help="what the code is FOR, in your words: the intent lens compares it with each "
                                   "pool member's purpose text (step 8b)")
    scout_parser.add_argument("--embed-fill", action="store_true",
                              help="fill the meaning lens's vector cache from the corpus export (the cache's "
                                   "only writer; refuses while the lens's Rule-2 set is red; step 8b)")
    scout_parser.set_defaults(func=cmd_scout)

    learn_parser = sub.add_parser(
        "learn", help="learn a corpus file into the logic registry (step 6b-1: plumbing; no engine can PASS)")
    learn_parser.add_argument("file", help="a file whose bytes are an item of the corpus export; with "
                              "--github, a path inside that pinned repo (read from the private cache)")
    learn_parser.add_argument("--author", help="crew code only: the seat that wrote it, e.g. 4Q")
    learn_parser.add_argument("--github", metavar="ORG/REPO@COMMIT",
                              help="learn from the GitHub pool: a ratified repo at its signed pin (step 7)")
    learn_parser.add_argument("--lock-wait", type=float, default=10.0,
                              help="seconds to wait for the registry build's lock before failing (default 10)")
    learn_parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    learn_parser.set_defaults(func=cmd_learn)

    cal_parser = sub.add_parser(
        "chaos-calibrate",
        help="run the validator's chaos negative-control gate on THIS machine and record it "
             "(writes only the scout's chaos_gate_history.json; KD-12 c/m)")
    cal_parser.add_argument("--runs", type=int, default=None,
                            help="gate runs to record (default: the calibration size, CALIBRATION_N)")
    cal_parser.set_defaults(func=cmd_chaos_calibrate)

    box_parser = sub.add_parser(
        "box-selftest", help="run the isolation box's red arm in the open: each attempt, boxed and unboxed")
    box_parser.add_argument("--json", action="store_true")
    box_parser.set_defaults(func=cmd_box_selftest)

    facts_parser = sub.add_parser(
        "github-facts",
        help="measure whether a ratified repo's own tests notice planted bugs, per function, in the box "
             "(writes only $FORGE_GATE_SCOUT_DIR/github_facts.jsonl; a fact row, never a PASS)")
    facts_parser.add_argument("spec", metavar="ORG/REPO@SHA", help="a ratified repo at its signed pin")
    facts_parser.add_argument("--functions", nargs="+", metavar="NAME",
                              help="only these functions: NAME, PATH::NAME or MODULE:NAME (default: every "
                                   "admitted top-level function)")
    facts_parser.add_argument("--json", action="store_true", help="print the rows as JSON lines")
    facts_parser.set_defaults(func=cmd_github_facts)

    led = sub.add_parser(
        "ledger", help="the outcome ledgers (step 8a): mark kept code, run its build/tests, show counts")
    lsub = led.add_subparsers(dest="ledger_cmd", required=True)
    mark_p = lsub.add_parser("mark", help="you kept a scout result and it worked: a pending mark (prints its id)")
    mark_p.add_argument("result_id", nargs="?", help="the forge-gate-result id the code came with")
    mark_p.add_argument("--item", help="without an id: the corpus item, e.g. corpus:c_0123456789abcdef")
    mark_p.add_argument("--who", required=True, help="who kept it")
    event_p = lsub.add_parser(
        "event", help="run your project's build or tests for a mark; the tool records the exit code")
    event_p.add_argument("mark_id", help="the id `ledger mark` printed")
    event_p.add_argument("--project", required=True, help="your project's folder (the command runs there)")
    event_p.add_argument("--kind", required=True, choices=("build", "tests"))
    event_p.epilog = "The command follows `--`: forge-gate ledger event MARK --project DIR --kind tests -- pytest -q"
    show_p = lsub.add_parser("show", help="the counts for an item or a result id (read-only)")
    show_p.add_argument("target", help="corpus:<item_id> or a forge-gate-result id")
    show_p.add_argument("--json", action="store_true")
    led.set_defaults(func=cmd_ledger)

    args = ap.parse_args(argv)
    args.event_cmd = event_cmd
    return args.func(args)


def cmd_box_selftest(args) -> int:
    """Anyone can run this as themselves (design §4a.6; 6E's request 2026-11-11): the box needs
    no root. Exit 0 only when every boxed attempt is blocked AND every unboxed attempt leaks
    (the unboxed run proves the targets are real, so "blocked" is not vacuous)."""
    import getpass
    import json as _json

    from .scout import box

    why = box.available()
    if why:
        print(f"BLIND({why})")
        return 2
    cannot = box.start_failure()
    if cannot:
        print(f"BLIND(box-unavailable: bwrap cannot start: {cannot})")
        return 2
    boxed, unboxed = box.red_arm("run"), box.red_arm("run", unboxed=True)
    keys = ("secret", "write", "network")
    # The network control is our own loopback listener (B4-1): it must be reached unboxed, so the
    # verdict does not depend on this host having internet. Public hosts are extra evidence only.
    ok = (boxed["ok"] and unboxed["secret"] == "LEAKED" and unboxed["write"] == "LEAKED"
          and unboxed["control_target"] in unboxed["network"])
    if args.json:
        print(_json.dumps({"user": getpass.getuser(), "box_user": box.box_user(), "boxed": boxed,
                           "unboxed_control": unboxed, "ok": ok}, indent=1))
    else:
        print(f"box self-test  user={getpass.getuser()}  box_user={box.box_user() or '(none: bwrap as this user)'}")
        for k in keys:
            print(f"  {k:<8} boxed: {boxed[k]:<28} unboxed control: {unboxed[k]}")
        print(f"  probe said (boxed): {boxed['probe']}")
        user = box.box_user()
        depth = (f"box user {user}" if user else
                 f"namespaces only; FORGE_GATE_BOX_USER unset, so an escape lands as {getpass.getuser()}")
        print(f"  control: unboxed reached our own listener {unboxed['control_target']}")
        print(f"PASS: every boxed attempt blocked, every unboxed attempt leaked ({depth})" if ok else
              "FAIL: " + ("a boxed attempt got through" if not boxed["ok"] else "the unboxed control did not leak: targets not real"))
    return 0 if ok else 1


def cmd_github_facts(args) -> int:
    """Exit 0 when at least one function was MEASURED, 3 when every row is BLIND, 2 when there
    is no place for the rows ($FORGE_GATE_SCOUT_DIR)."""
    from . import config
    from .scout import upstream_facts

    try:
        res = upstream_facts.run(args.spec, args.functions)
    except config.NotConfigured as e:
        print(f"github-facts: {e}", file=sys.stderr)
        return 2
    for row in res.rows:
        if args.json:
            print(json.dumps(row, sort_keys=True))
        else:
            what = row.get("function") or "(run)"
            counts = f" m={row['m']} k={row['k']} s={row['s']} u={row['u']}" if "m" in row else ""
            print(f"{row['outcome']:<40} {row.get('file') or ''}::{what}{counts}")
    print(f"github-facts: run {res.run_id}: {res.measured} MEASURED of {len(res.rows)} rows -> {res.facts_file}",
          file=sys.stderr)
    return 0 if res.measured else 3


def cmd_learn(args) -> int:
    from .scout import learn

    if args.github is not None:   # a path INSIDE the pinned repo, not a local file
        res = learn.run(args.file, args.author, lock_wait=args.lock_wait, github=args.github)
        print(json.dumps(res.report, indent=1, sort_keys=True) if args.json else learn.render(res))
        if res.code not in (learn.EXIT_OK,):
            print(f"forge-gate learn: {res.outcome}", file=sys.stderr)
        return res.code

    if args.author is None:   # as before step 7: a usage error (exit 2), not a run (review F9)
        print("forge-gate learn: error: the following arguments are required: --author "
              "(crew code; a GitHub file takes --github instead)", file=sys.stderr)
        return 2
    p = Path(args.file)
    if not p.is_file():
        print(f"forge-gate learn: {p}: not a file", file=sys.stderr)
        return learn.EXIT_BLIND
    res = learn.run(p, args.author, lock_wait=args.lock_wait)
    print(json.dumps(res.report, indent=1, sort_keys=True) if args.json else learn.render(res))
    if res.code not in (learn.EXIT_OK,):
        print(f"forge-gate learn: {res.outcome}", file=sys.stderr)
    return res.code


def cmd_chaos_calibrate(args) -> int:
    """Record N gate runs (no probe, no registry write) under learn's singleton lock, so it never
    meets a learn writing the same history. Stops early at the first survival (the machine is
    then latched BLIND, and more runs cannot unlatch it)."""
    import os

    from . import config
    from .scout import learn
    from .scout.engines import chaos

    runs = args.runs if args.runs is not None else chaos.CALIBRATION_N
    if runs < 1:
        print("forge-gate chaos-calibrate: --runs must be at least 1", file=sys.stderr)
        return learn.EXIT_CONFIG
    try:
        fd = learn._singleton(config.scout_dir())
    except config.NotConfigured as e:
        print(f"forge-gate chaos-calibrate: {e}", file=sys.stderr)
        return learn.EXIT_CONFIG
    except learn.LearnError as e:          # LockBusy -> EXIT_LOCK; a lock it cannot open -> config
        print(f"forge-gate chaos-calibrate: {e}", file=sys.stderr)
        return e.code
    try:
        res = chaos.calibrate(runs)
    except config.NotConfigured as e:
        print(f"forge-gate chaos-calibrate: {e}", file=sys.stderr)
        return learn.EXIT_CONFIG
    except chaos.Blind as e:
        print(f"forge-gate chaos-calibrate: {e}", file=sys.stderr)
        return learn.EXIT_BLIND
    except OSError as e:
        print(f"forge-gate chaos-calibrate: {e}", file=sys.stderr)
        return learn.EXIT_CONFIG
    finally:
        os.close(fd)
    print(json.dumps(res, indent=1, sort_keys=True))
    clean = (res.get("config") or {}).get("clean_runs", 0)
    if res.get("survived") or clean < chaos.CALIBRATION_N:     # review F1: never "ok" when not calibrated
        print(f"forge-gate chaos-calibrate: NOT calibrated: survived={res.get('survived') or {}}, "
              f"{clean}/{chaos.CALIBRATION_N} clean gate runs; chaos stays BLIND here", file=sys.stderr)
        return learn.EXIT_BLIND
    return learn.EXIT_OK


def cmd_scout(args) -> int:
    from .scout import report

    if args.embed_fill:
        if args.file is not None or args.show is not None:
            print("forge-gate scout: --embed-fill takes no file and no --show", file=sys.stderr)
            return 2
        rc, text = report.embed_fill()
        print(f"forge-gate scout --embed-fill: {text}", file=sys.stderr if rc else sys.stdout)
        return rc
    if args.show is not None:
        if args.file is not None:
            print("forge-gate scout: give a file OR --show, not both", file=sys.stderr)
            return 2
        rc, text = report.show(args.show)
        if rc:
            print(f"forge-gate scout: {text}", file=sys.stderr)
        else:
            print(text, end="")
        return rc
    if args.file is None:
        print("forge-gate scout: a file to scout (or --show RESULT_ID) is required", file=sys.stderr)
        return 2
    p = Path(args.file)
    if not p.is_file():
        print(f"forge-gate scout: {p}: not a file", file=sys.stderr)
        return 2
    rep = report.run(p, intent=args.intent)
    print(report.to_json(rep) if args.json else report.render(rep))
    return 0  # advisory: a report is a success, whatever it found


def cmd_ledger(args) -> int:
    """``forge-gate ledger mark|event|show`` (step 8a; DECISIONS-8a G5 a, G7). ``mark`` and
    ``event`` are the person-mark writer's only named place; ``show`` only reads.

    Exit codes: 2 = refused or off, nothing written (or, for ``event``, nothing recorded); a
    recorded event exits 0/1 with its command's pass/fail; ``show`` exits 1 when the ledgers
    cannot be read (BLIND: counts unknown, never printed as zeros)."""
    from . import config, ledger

    def refuse(msg: str) -> int:
        print(f"forge-gate ledger {args.ledger_cmd}: {msg}", file=sys.stderr)
        return 2

    if not ledger.enabled():
        return refuse(f"{config.LEDGER_KEY} is not set: the ledgers are off; nothing was read or written")
    errors = (ledger.LedgerError, config.NotConfigured, OSError)

    if args.ledger_cmd == "mark":
        try:                                                    # record_mark refuses both/neither
            row = ledger.record_mark(who=args.who, result_id=args.result_id, item=args.item)
        except errors as e:
            return refuse(f"{e}; nothing was recorded")
        if args.item is not None:
            try:
                seen = bool(ledger.exposures(row["item"]))
            except errors:
                seen = True                                     # cannot tell: say nothing
            if not seen:
                print(f"forge-gate ledger mark: note: {row['item']} was never shown on this host; "
                      "the mark is kept but counts only under that exact item", file=sys.stderr)
        print(row["mark_id"])
        return 0

    if args.ledger_cmd == "event":
        if not args.event_cmd:
            return refuse("give the command to run after `--`, e.g. -- pytest -q; nothing was run")
        try:
            row = ledger.record_event(mark_id=args.mark_id, project=args.project, cmd=args.event_cmd,
                                      event_kind=args.kind)
        except errors as e:
            return refuse(f"{e} (no event recorded)")
        print(f"{row['event_id']}: {row['event_kind']} rc={row['rc']} recorded for {row['mark_id']}")
        return 0 if row["rc"] == 0 else 1

    item = args.target                                          # show: read-only
    # A mistyped name must not read as the clean zero of "no evidence" (8a s4 review F3).
    if item.startswith("fgr-") and not ledger.RESULT_ID.fullmatch(item):
        return refuse(f"{item!r} is not a well-formed result id (fgr-<12 hex>-<lens>-<rank>)")
    if item.startswith("corpus:") and not CORPUS_ITEM.fullmatch(item):
        return refuse(f"{item!r} is not a well-formed corpus item (corpus:c_<16 hex>)")
    try:
        v = ledger.view()
    except errors as e:
        why = f"the ledgers could not be read ({e}); the counts are unknown, not zero"
        print(json.dumps({"blind": why}) if args.json else f"BLIND: {why}")
        return 1
    if ledger.RESULT_ID.fullmatch(item):
        if item not in v.exposure:
            return refuse(f"{item} is not in this host's exposure ledger")
        item = v.exposure[item]["item"]
    c = ledger.counts(item, v=v)
    if not ledger.exposures(item, v=v):
        print(f"forge-gate ledger show: note: {item} was never shown on this host", file=sys.stderr)
    out = {"item": item, "exposures": len(ledger.exposures(item, v=v)), "oracle": c.oracle,
           "person_with_id": c.person_with_id, "person_without_id": c.person_without_id,
           "pending": c.pending,
           "ledger": {"refused": [list(x) for x in v.refused], "torn": [list(x) for x in v.torn],
                      "orphans": len(v.orphans)}}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    lines = [f"item       {item}",
             f"exposures  {out['exposures']} (shown on this host)",
             f"oracle     {c.oracle}   person with id {c.person_with_id}   "
             f"person without id {c.person_without_id}   (three counts, never added up)",
             f"pending    {c.pending} (marked, no passing event yet; not counted)"]
    if v.refused or v.torn or v.orphans:
        lines.append(f"ledger     {len(v.refused)} refused row(s), {len(v.torn)} torn line(s), "
                     f"{len(v.orphans)} orphan(s): left out of every count")
        lines += [f"  refused {f}:{n}: {why}" for f, n, why in v.refused]
        lines += [f"  torn    {f}:{n}" for f, n in v.torn]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
