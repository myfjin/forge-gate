"""§3.2 Round 5 — report.py surfaces U-codes to the reader.

Closes the last-mile gap the audit-layer had with params_aborted: a bucket
that stops at the lens never reaches the reader. Report.py is the reader-facing
skin; if it emits a bare 'COULD NOT CHECK' for an INCONCLUSIVE verdict, the
U-code we so carefully wired at the lens layer is invisible to the person
who's supposed to act on it.

RED-ARM: revert the annotation block in tools/report.py — the load-bearing
test 'test_inconclusive_verdict_surfaces_u4_coverage_gap' fires.
"""
from __future__ import annotations
import sys
from pathlib import Path

# report.py lives in tools/ — add it to path so we can import render() directly.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import report as report_mod  # noqa: E402


def _scope(judged=0, total=5):
    return {
        "params_total": total,
        "params_judged": judged,
        "params_unreachable": 0,
        "params_unexercised": total - judged,
        "params_aborted": 0,
        "functions_total": 1,
        "functions_scanned": 1,
        "mode": "UNMATCHED",
    }


def test_inconclusive_verdict_surfaces_u4_coverage_gap():
    """LOAD-BEARING — a reader must see the U4 code, not a bare string.

    Judged==0 path (the most common INCONCLUSIVE shape): the FIRST verdict-text
    branch fires and would swallow the INCONCLUSIVE line entirely. The
    annotation must apply regardless.
    """
    out = report_mod.render(
        Path("/tmp/w.py"),
        "INCONCLUSIVE 2 parameter(s) unexercised (no valid baseline)",
        _scope(judged=0, total=5),
        findings=[],
    )
    assert "U4:coverage_gap" in out, f"code missing from report:\n{out}"
    assert "Remedy" in out, f"remedy hint missing:\n{out}"
    # The remedy from the registry (partial match — full text is longer)
    assert "widen lens coverage" in out, f"registry remedy dropped:\n{out}"


def test_inconclusive_with_judged_still_annotated():
    """Second load-bearing branch: judged>0 path also annotates.

    If someone factored the annotation into ONLY the judged==0 branch, this
    fires and catches the incomplete conversion.
    """
    out = report_mod.render(
        Path("/tmp/w.py"),
        "INCONCLUSIVE some other coverage reason",
        _scope(judged=3, total=5),
        findings=[],
    )
    assert "U4:coverage_gap" in out


def test_pass_verdict_not_annotated():
    """CONTROL — PASS reports are UNCHANGED by round 5.

    Without this control, a mutant that appended U-codes to every verdict
    would still pass the two tests above. This forces the discrimination."""
    out = report_mod.render(
        Path("/tmp/w.py"),
        "PASS all NaN inputs handled",
        _scope(judged=5, total=5),
        findings=[],
    )
    assert "U4:coverage_gap" not in out, f"U-code leaked onto PASS:\n{out}"
    assert "NO DEFECT FOUND" in out


def test_fail_verdict_not_annotated():
    """CONTROL — FAIL reports are UNCHANGED by round 5."""
    out = report_mod.render(
        Path("/tmp/w.py"),
        "FAIL NaN not propagated in tanh_scaled",
        _scope(judged=5, total=5),
        findings=["tanh_scaled(nan) returned 0.0"],
    )
    assert "U4:coverage_gap" not in out
    assert "DEFECT FOUND" in out
