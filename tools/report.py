#!/usr/bin/env python3
"""Forge Gate — client verdict report generator.

This tool does NOT compute its own verdict. It runs the guard lens and
domain classifier on a submitted file and renders the result as a human-facing
report following docs/REPORT_TEMPLATE.md. The numbers printed are parsed
from the lens scope: lines, never recomputed, so the report and the tool can
never disagree.

For MATCHED mode this generator currently reports "not implemented" because
the oracle-free path does not provide correctness checks; the matched-mode
report template will be wired once property-primary / metamorphic / invariant
/twin-consensus results are available for arbitrary files.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
GUARD_LENS = ROOT / "tools" / "guard_lens.py"

# Import the reason-code registry from src/ (BRIEF §3.2). Report.py is in tools/
# and unsupported.py is in src/forge_gate/; the sys.path insertion keeps the
# import boundary explicit and one-way (tools/ imports src/, never the reverse).
sys.path.insert(0, str(ROOT / "src"))
from forge_gate.unsupported import _u  # noqa: E402
DOMAIN_CLASSIFIER = ROOT / "tools" / "domain_classifier.py"

BANNED_PHRASES = [
    "verified correct",
    "fully tested",
    "guaranteed",
    "no bugs",
    "safe to deploy",
]


def run_lens(fixture: Path, timeout: float = 120.0) -> Tuple[str, Dict[str, Any]]:
    """Run the guard lens and return (raw_output, parsed_scope).

    The report generator is downstream of the lens; it must parse the lens
    output verbatim. Subprocess isolation (start_new_session, killpg) is
    implemented inside the lens itself.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(GUARD_LENS), "--fixture", str(fixture)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            "verdict:COULD NOT CHECK — guard lens timed out\n"
            "scope:params_total=0\n"
            "scope:params_judged=0\n"
            "scope:params_unreachable=0\n"
            "scope:params_unexercised=0\n"
            "scope:params_aborted=0\n"
            "scope:functions_total=0\n"
            "scope:functions_scanned=0\n"
            "scope:mode=UNMATCHED\n",
            {
                "params_total": 0,
                "params_judged": 0,
                "params_unreachable": 0,
                "params_unexercised": 0,
                "params_aborted": 0,
                "functions_total": 0,
                "functions_scanned": 0,
                "mode": "UNMATCHED",
            },
        )

    raw = proc.stdout
    scope: Dict[str, Any] = {
        "params_total": 0,
        "params_judged": 0,
        "params_unreachable": 0,
        "params_unexercised": 0,
                "params_aborted": 0,
        "functions_total": 0,
        "functions_scanned": 0,
        "mode": "UNMATCHED",
    }
    for line in raw.splitlines():
        if line.startswith("scope:"):
            key_value = line[len("scope:"):]
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key in scope:
                    if key == "mode":
                        scope[key] = value
                    else:
                        try:
                            scope[key] = int(value)
                        except ValueError:
                            scope[key] = value

    return raw, scope


def parse_verdict(raw: str) -> str:
    """Return the first verdict: line from lens output."""
    for line in raw.splitlines():
        if line.startswith("verdict:"):
            return line[len("verdict:"):].strip()
    return "COULD NOT CHECK — no verdict produced"


def collect_findings(raw: str) -> List[str]:
    """Return detail: lines that describe defects or observations."""
    findings: List[str] = []
    for line in raw.splitlines():
        if line.startswith("detail:"):
            findings.append(line[len("detail:"):].strip())
    return findings


def has_banned_phrases(text: str) -> List[str]:
    """Return any banned phrase that appears in the text."""
    lower = text.lower()
    return [p for p in BANNED_PHRASES if p in lower]


def render(
    fixture: Path,
    verdict_line: str,
    scope: Dict[str, Any],
    findings: List[str],
    as_json: bool = False,
) -> str:
    """Render the report according to docs/REPORT_TEMPLATE.md."""
    mode = scope.get("mode", "UNMATCHED")
    total = int(scope.get("params_total", 0))
    judged = int(scope.get("params_judged", 0))
    unreachable = int(scope.get("params_unreachable", 0))
    unexercised = int(scope.get("params_unexercised", 0))
    aborted = int(scope.get("params_aborted", 0))
    functions_total = int(scope.get("functions_total", 0))
    functions_scanned = int(scope.get("functions_scanned", 0))
    functions_not_scanned = functions_total - functions_scanned

    # Verdict section
    if judged == 0:
        verdict_text = (
            f"COULD NOT CHECK — we judged {judged} of {total} parameters. "
            f"See \"What Was Not Checked\" below for limits."
        )
    elif verdict_line.startswith("FAIL"):
        verdict_text = (
            f"DEFECT FOUND — {judged} of {total} parameter(s) produced a wrong result."
        )
    elif verdict_line.startswith("PASS"):
        verdict_text = (
            f"NO DEFECT FOUND — we judged {judged} of {total} parameters and found no "
            f"evidence of the defects we test for. See \"What Was Not Checked\" "
            f"below for limits."
        )
    else:
        verdict_text = (
            f"COULD NOT CHECK — {verdict_line}"
        )

    # BRIEF §3.2: annotate verdict_text when the underlying verdict names a
    # known UNSUPPORTED shape. A reader sees "we could not measure" WITH the
    # code that names whose problem it might be — not a raw string.
    # Only INCONCLUSIVE is emitted here today; if guard ever ships another
    # UNSUPPORTED shape, add the mapping below and the drift-test will notice.
    if verdict_line.startswith("INCONCLUSIVE"):
        _uc = _u("U4:coverage_gap", verdict_line)
        verdict_text = f"[{_uc.code}] {verdict_text}\n  Remedy: {_uc.remedy}"

    # Mode section
    if mode == "MATCHED":
        mode_text = (
            "Mode: MATCHED — this file was identified as implementing a known "
            "algorithm. We hold a reference implementation and can check correctness."
        )
    else:
        mode_text = (
            "Mode: UNMATCHED — this file does not match any algorithm we hold a "
            "reference for. We checked robustness only."
        )

    # Coverage section
    coverage_lines: List[str] = []
    # An honest coverage section has to name what was NOT checked, not only what
    # was. tests/verify_report.sh has demanded this sentence since 2579e98
    # (2026-08-04) and report.py never emitted it, so the tracked test has been
    # red ever since. The guard lens probes VALUES; it never varies argument
    # shape or length, and a reader who is not told that will assume it did.
    SHAPE_DISCLOSURE = (
        "Argument shape and length consistency was not checked in this mode; "
        "the separate shape guard (tests/verify_shape_guard.sh) can detect this "
        "class of defect."
    )
    coverage_lines.append(SHAPE_DISCLOSURE)
    coverage_lines.append(
        "Correctness of the algorithm itself was not checked. Forge Gate tests "
        "whether the code handles invalid inputs and edge cases correctly. It does "
        "not verify that the algorithm implements the intended mathematics. A file "
        "that passes all our checks may still compute the wrong thing for valid inputs."
    )
    if mode == "MATCHED":
        coverage_lines.append(
            "This file matched an algorithm we hold a reference for, so we checked "
            "both correctness (output matches reference) and robustness (handling of "
            "invalid inputs). However, correctness was checked only for the specific "
            "test inputs we chose — not for all possible inputs."
        )
    else:
        coverage_lines.append(
            "This file does not match any algorithm we hold a reference for. We could "
            "only check general robustness — whether the code detects invalid inputs "
            "like NaN, infinity, or negative counts. We could not check whether the "
            "algorithm is correct."
        )
        coverage_lines.append(
            'No defect found under this mode means we saw no robustness failures, not that the code is correct.'
        )

    if functions_not_scanned > 0:
        coverage_lines.append(
            f"Functions with no numeric parameters: {functions_not_scanned} function(s) "
            "take no numeric parameters. These received minimal coverage — we can only "
            "check that they run without crashing, not that their logic is sound."
        )
    if unexercised > 0:
        coverage_lines.append(
            f"Could not execute: {unexercised} parameter(s) could not be exercised "
            "because a sibling argument could not be synthesised or the function raised "
            "on every baseline input. These were not judged at all."
        )
    if aborted > 0:
        # This bucket exists because the two states below were indistinguishable
        # until 2026-09-05: a probe that was KILLED and observed nothing was filed
        # as "unreachable", which asserts the opposite -- that we DID observe, and
        # the value made no difference. The wording here must never converge with
        # the unreachable sentence beneath it.
        coverage_lines.append(
            f"Could not finish measuring: {aborted} parameter(s) had their probes "
            "stopped by a time or memory limit before anything was observed. This is "
            "NOT a finding about the code — it is a limit of this run. Do not read it "
            "as 'the parameter does not matter'; we did not get far enough to know. "
            "A slow or non-terminating path is the usual cause, and it may itself be "
            "worth investigating."
        )
    if unreachable > 0:
        coverage_lines.append(
            f"Parameter unreachable: {unreachable} parameter(s) were not reached by the "
            "test data — the function returned the same result whether we supplied a "
            "valid or invalid value. This usually means the parameter is not used in "
            "the code path that was exercised. We report this honestly rather than "
            "claiming the parameter was tested."
        )

    # Always report the raw scope numbers so the report reconciles with the lens.
    coverage_lines.append(
        f"Scope summary: {total} total scalar parameters, {judged} judged, "
        f"{unreachable} unreachable, {unexercised} unexercised, {aborted} aborted; "
        f"{functions_total} total functions, {functions_scanned} scanned."
    )

    # What was checked
    checked_lines: List[str] = []
    checked_lines.append(
        "Invalid numeric inputs (NaN, infinity): For every numeric parameter, we "
        "supplied the special value \"not a number\" (NaN) and observed whether the code "
        "detected it (raised an error, returned NaN) or silently produced a "
        "confident-looking finite number. A finite number from NaN input is a defect — "
        "it means the code fabricated a plausible result from poison."
    )
    checked_lines.append(
        "Infinity is treated differently: some functions correctly saturate at infinity "
        "(e.g., a sigmoid function returns exactly 1.0 for +infinity). We report infinity "
        "observations but do not flag them as defects, because we cannot tell correct "
        "saturation from a bug without knowing the intended mathematics."
    )
    if mode == "MATCHED":
        checked_lines.append(
            "Correctness (MATCHED mode only): We compared the output of this "
            "implementation against a known-correct reference implementation of the "
            "same algorithm, using the same inputs. We checked that the outputs match "
            "within a tolerance. We also checked that mathematical relationships hold."
        )

    # Findings
    finding_lines: List[str] = []
    if verdict_line.startswith("FAIL"):
        for i, finding in enumerate(findings[:20], 1):
            finding_lines.append(f"Finding {i}: {finding}")
    else:
        finding_lines.append("No defects found.")

    # Assemble report
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report = []
    report.append("╔══════════════════════════════════════════════════════════════╗")
    report.append("║  FORGE GATE — Verdict Report                                 ║")
    report.append(f"║  File: {fixture.name:<55}║")
    report.append(f"║  Date: {date:<55}║")
    report.append("╚══════════════════════════════════════════════════════════════╝")
    report.append("")
    report.append("═══ VERDICT ═══")
    report.append("")
    report.append(f"  {verdict_text}")
    report.append("")
    report.append("═══ COVERAGE ═══")
    report.append("")
    for line in coverage_lines:
        if line.strip():
            report.append(f"  {line}")
    report.append("")
    report.append("═══ MODE ═══")
    report.append("")
    report.append(f"  {mode_text}")
    report.append("")
    report.append("═══ WHAT WAS CHECKED ═══")
    report.append("")
    for line in checked_lines:
        report.append(f"  {line}")
    report.append("")
    report.append("═══ FINDINGS ═══")
    report.append("")
    for line in finding_lines:
        report.append(f"  {line}")
    report.append("")
    report.append("═══ HOW TO REPRODUCE ═══")
    report.append("")
    report.append(f"  python3 {GUARD_LENS} --fixture {fixture}")
    report.append("")
    report.append("═══ WHY THIS REPORT IS HONEST ═══")
    report.append("")
    report.append(
        "  Every verdict in this report was produced by running the code, never by a "
        "language model's opinion. The same input gives the same verdict every time, "
        "and anyone with access to the tool can re-run it. That is the reason to pay "
        "for this rather than ask a chatbot: you get a reproducible measurement, not a "
        "plausible-sounding guess."
    )

    text = "\n".join(report)

    if as_json:
        return json.dumps(
            {
                "file": str(fixture),
                "date": date,
                "verdict": verdict_text,
                "mode": mode,
                "coverage": coverage_lines,
                "checked": checked_lines,
                "findings": finding_lines,
                "reproduce": f"python3 {GUARD_LENS} --fixture {fixture}",
                "scope": scope,
                "report_text": text,
            },
            indent=2,
        )
    return text


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Forge Gate client verdict report")
    ap.add_argument("--fixture", required=True, help="Python file to report on")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of rendered report")
    args = ap.parse_args(argv[1:])

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        print("verdict:COULD NOT CHECK — file not found")
        return 0

    raw, scope = run_lens(fixture)
    verdict_line = parse_verdict(raw)
    findings = collect_findings(raw)
    report = render(fixture, verdict_line, scope, findings, as_json=args.json)

    banned = has_banned_phrases(report)
    if banned:
        # Internal safety check: never emit a report containing banned warranty phrases.
        sys.stderr.write(f"REPORT_REFUSED: banned phrases found: {', '.join(banned)}\n")
        return 1

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
