#!/usr/bin/env python3
"""CLI entry point for Forge Gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import list_patterns, resolve_pattern, CatalogError
from .lenses import ALL_LENSES, evaluate, parse_lens_selection, render
from .runner import run_file
from .verifier import verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
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

    return 0 if gate.status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
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
    verify_parser.add_argument("--timeout", type=float, default=60.0, help="seconds (default 60)")
    verify_parser.add_argument("--tolerance", type=float, default=1e-5, help="numeric comparison tolerance")
    verify_parser.add_argument("--allow-network", action="store_true", help="do not drop network namespace")
    verify_parser.add_argument("--memory-mb", type=int, default=512, help="memory limit in MB")
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
    scout_parser.add_argument("file", help="file to scout (read as text, never run)")
    scout_parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    scout_parser.set_defaults(func=cmd_scout)

    box_parser = sub.add_parser(
        "box-selftest", help="run the isolation box's red arm in the open: each attempt, boxed and unboxed")
    box_parser.add_argument("--json", action="store_true")
    box_parser.set_defaults(func=cmd_box_selftest)

    args = ap.parse_args(argv)
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


def cmd_scout(args) -> int:
    from .scout import report

    p = Path(args.file)
    if not p.is_file():
        print(f"forge-gate scout: {p}: not a file", file=sys.stderr)
        return 2
    rep = report.run(p)
    print(report.to_json(rep) if args.json else report.render(rep))
    return 0  # advisory: a report is a success, whatever it found


if __name__ == "__main__":
    sys.exit(main())
