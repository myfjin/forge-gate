#!/bin/bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACCEPTANCE TEST for the Forge Gate client REPORT GENERATOR.
#
# The report generator (tools/report.py, built by 4Q) consumes the guard lens
# output and emits a human-facing report following docs/REPORT_TEMPLATE.md.
#
# This test asserts the generated report:
#   1. contains all six required sections in the template's order
#   2. has Coverage before Findings (limits must precede evidence)
#   3. reports coverage numbers identical to the lens scope output
#   4. carries the UNMATCHED "not that the code is correct" disclaimer verbatim
#   5. contains no banned warranty/marketing phrases
#   6. includes the real reproduce command
#
# EXIT 0 = report accepted. EXIT 1 = rejected.

set -u

PYTHON="/usr/bin/python3"
REPORTER="$ROOT/tools/report.py"
LENS="$ROOT/tools/guard_lens.py"
FIXTURE="$ROOT/tests/userside/retry_http.py"

FAILED=0
INCONCLUSIVE=0

note() { printf '%-70s %s\n' "$1" "$2"; }
gate() {
    if [ "$2" = "PASS" ]; then
        note "$1" "✅ PASS"
    elif [ "$2" = "INCONCLUSIVE" ]; then
        note "$1" "⚠️  INCONCLUSIVE"
        INCONCLUSIVE=1
    else
        note "$1" "❌ FAIL"
        FAILED=1
    fi
}

# Generate the report. The reporter does not exist yet; absence is the red proof.
generate_report() {
    if [ ! -f "$REPORTER" ]; then
        echo "MISSING"
        return 1
    fi
    "$PYTHON" "$REPORTER" --fixture "$FIXTURE" 2>/dev/null
}

echo "=================================================================="
echo " REPORT GENERATOR — acceptance test"
echo " reporter: $REPORTER"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

report=$(generate_report)
rc=$?
if [ "$rc" -ne 0 ] || [ "$report" = "MISSING" ]; then
    echo ">>> REJECTED: report generator not found at $REPORTER."
    exit 1
fi

# Dump to a temp file for multi-pass inspection.
REPORT_FILE=$(mktemp /tmp/verify_report.XXXXXX)
if [ ! -f "$REPORT_FILE" ]; then
    echo "FATAL: could not create temp report file"
    exit 1
fi
echo "$report" > "$REPORT_FILE"
trap 'rm -f "$REPORT_FILE"' EXIT

# ---------------------------------------------------------------- 1. six sections, template order
# Sections (case-insensitive, allow decorative framing):
#   Verdict, Coverage, Mode, What Was Checked, Findings, How to Reproduce
find_section_pos() {
    grep -n -i "$1" "$REPORT_FILE" | head -1 | cut -d: -f1
}

pos_verdict=$(find_section_pos "verdict")
pos_coverage=$(find_section_pos "coverage")
pos_mode=$(find_section_pos "mode")
pos_checked=$(find_section_pos "what was checked")
pos_findings=$(find_section_pos "findings")
pos_reproduce=$(find_section_pos "how to reproduce")

if [ -n "$pos_verdict" ] && [ -n "$pos_coverage" ] && [ -n "$pos_mode" ] && [ -n "$pos_checked" ] && [ -n "$pos_findings" ] && [ -n "$pos_reproduce" ]; then
    if [ "$pos_verdict" -lt "$pos_coverage" ] && [ "$pos_coverage" -lt "$pos_mode" ] && [ "$pos_mode" -lt "$pos_checked" ] && [ "$pos_checked" -lt "$pos_findings" ] && [ "$pos_findings" -lt "$pos_reproduce" ]; then
        gate "all six sections present in template order" PASS
    else
        gate "all six sections present in template order" FAIL
        echo "       positions: verdict=$pos_verdict coverage=$pos_coverage mode=$pos_mode checked=$pos_checked findings=$pos_findings reproduce=$pos_reproduce"
    fi
else
    gate "all six sections present in template order" FAIL
    echo "       missing one of verdict/coverage/mode/what was checked/findings/how to reproduce"
fi

# ---------------------------------------------------------------- 2. Coverage appears before Findings
if [ -n "$pos_coverage" ] && [ -n "$pos_findings" ] && [ "$pos_coverage" -lt "$pos_findings" ]; then
    gate "Coverage section precedes Findings section" PASS
else
    gate "Coverage section precedes Findings section" FAIL
fi

# ---------------------------------------------------------------- 3. coverage numbers match lens scope output exactly
lens_out=$("$PYTHON" "$LENS" --fixture "$FIXTURE" 2>/dev/null)
lens_total=$(echo "$lens_out" | grep -E "^scope:params_total=" | head -1 | sed 's/^scope:params_total=//')
lens_judged=$(echo "$lens_out" | grep -E "^scope:params_judged=" | head -1 | sed 's/^scope:params_judged=//')
lens_unreachable=$(echo "$lens_out" | grep -E "^scope:params_unreachable=" | head -1 | sed 's/^scope:params_unreachable=//')
lens_unexercised=$(echo "$lens_out" | grep -E "^scope:params_unexercised=" | head -1 | sed 's/^scope:params_unexercised=//')
lens_functions_total=$(echo "$lens_out" | grep -E "^scope:functions_total=" | head -1 | sed 's/^scope:functions_total=//')
lens_functions_scanned=$(echo "$lens_out" | grep -E "^scope:functions_scanned=" | head -1 | sed 's/^scope:functions_scanned=//')
lens_mode=$(echo "$lens_out" | grep -E "^scope:mode=" | head -1 | sed 's/^scope:mode=//')

report_has_count() {
    local n="$1"
    grep -qE "(^|[^0-9])$n([^0-9]|$)" "$REPORT_FILE"
}

scope_match_ok=1
for key in total judged unreachable unexercised functions_total functions_scanned mode; do
    eval "val=\$lens_$key"
    if [ -z "$val" ]; then
        scope_match_ok=0
        echo "       lens scope missing $key"
        break
    fi
done

if [ "$scope_match_ok" -eq 1 ] && \
   report_has_count "$lens_total" && \
   report_has_count "$lens_judged" && \
   report_has_count "$lens_unreachable" && \
   report_has_count "$lens_unexercised" && \
   report_has_count "$lens_functions_total" && \
   report_has_count "$lens_functions_scanned" && \
   grep -qiF "$lens_mode" "$REPORT_FILE"; then
    gate "coverage numbers match lens scope output" PASS
else
    gate "coverage numbers match lens scope output" FAIL
    echo "       lens scope: total=$lens_total judged=$lens_judged unreachable=$lens_unreachable unexercised=$lens_unexercised fns_total=$lens_functions_total fns_scanned=$lens_functions_scanned mode=$lens_mode"
fi

# ---------------------------------------------------------------- 4. UNMATCHED disclaimer verbatim
disclaimer1="This file does not match any algorithm we hold a reference for"
disclaimer2="No defect found under this mode means we saw no robustness failures"
if grep -qF "$disclaimer1" "$REPORT_FILE" && grep -qF "$disclaimer2" "$REPORT_FILE"; then
    gate "UNMATCHED disclaimer present verbatim" PASS
else
    gate "UNMATCHED disclaimer present verbatim" FAIL
fi

# ---------------------------------------------------------------- 5. banned phrases absent
banned_phrases=("verified correct" "fully tested" "guaranteed" "no bugs" "safe to deploy")
banned_found=0
for phrase in "${banned_phrases[@]}"; do
    if grep -qiF "$phrase" "$REPORT_FILE"; then
        echo "       banned phrase found: $phrase"
        banned_found=1
    fi
done
if [ "$banned_found" -eq 0 ]; then
    gate "banned warranty phrases absent" PASS
else
    gate "banned warranty phrases absent" FAIL
fi

# ---------------------------------------------------------------- 6. reproduce command is present and real
reproduce_cmd="python3 $ROOT/tools/guard_lens.py --fixture"
if grep -qF "$reproduce_cmd" "$REPORT_FILE"; then
    gate "reproduce command present and real" PASS
else
    gate "reproduce command present and real" FAIL
fi

: "${lens_judged:=0}"
# ---------------------------------------------------------------- 7. verdict headline must not overclaim coverage
# The verdict sentence must use the JUDGED count, not params_total. Both
# "we checked 4 parameters" (overclaim) and "we checked 1 parameter" (understate)
# are misleading; the honest form is "we judged N of M parameters". This gate
# parses the number that follows "we checked" / "we judged" in the headline and
# asserts it equals scope:params_judged.
verdict_count=$(grep -iE "^\s*NO DEFECT FOUND|^\s*DEFECT FOUND" "$REPORT_FILE" | head -1 | grep -oE "(we checked|we judged)[^0-9]*[0-9]+" | grep -oE "[0-9]+")
if [ -n "$verdict_count" ] && [ "$verdict_count" -eq "$lens_judged" ]; then
    gate "verdict headline uses judged count ($lens_judged), not total" PASS
else
    gate "verdict headline uses judged count ($lens_judged), not total" FAIL
    echo "       headline count=$verdict_count, expected lens params_judged=$lens_judged"
fi

# ---------------------------------------------------------------- 8. zero-judged must be COULD NOT CHECK, not NO DEFECT FOUND
# The maintainer's boundary: judged=0 is not arbitrary; there is literally no evidence.
# The verdict must say COULD NOT CHECK and still include the counts so the client
# sees we made an honest attempt. judged=1 must still be NO DEFECT FOUND.
# We test the boundary on two real files:
#   * retry_http.py -> params_judged=1 -> headline must be NO DEFECT FOUND
#   * gd_cheat_zeros.py -> params_judged=0 -> headline must be COULD NOT CHECK
zero_report_file=$(mktemp /tmp/verify_report_zero.XXXXXX)
trap 'rm -f "$zero_report_file" "$REPORT_FILE"' EXIT
if [ -f "$REPORTER" ]; then
    "$PYTHON" "$REPORTER" --fixture "$ROOT/tests/corpus/gd_cheat_zeros.py" > "$zero_report_file" 2>/dev/null
fi
zero_headline=$(grep -iE "^\s*NO DEFECT FOUND|^\s*COULD NOT CHECK" "$zero_report_file" | head -1)
if [ -n "$zero_headline" ] && echo "$zero_headline" | grep -qiE "^\s*COULD NOT CHECK"; then
    gate "zero-judged file (gd_cheat_zeros.py) reports COULD NOT CHECK" PASS
else
    gate "zero-judged file (gd_cheat_zeros.py) reports COULD NOT CHECK" FAIL
    echo "       headline was: $zero_headline"
fi

# The current fixture (retry_http.py) has judged=1 and must stay NO DEFECT FOUND.
if [ "$lens_judged" -gt 0 ]; then
    if echo "$report" | grep -iE "^\s*NO DEFECT FOUND" >/dev/null; then
        gate "non-zero-judged file (retry_http.py) reports NO DEFECT FOUND" PASS
    else
        gate "non-zero-judged file (retry_http.py) reports NO DEFECT FOUND" FAIL
        echo "       expected NO DEFECT FOUND for retry_http.py (judged=$lens_judged)"
    fi
fi

# ---------------------------------------------------------------- 10. Coverage must explicitly name shape/length classes not tested
# The report on perceptron_shape_defect_snapshot.py currently says
# "NO DEFECT FOUND" because the guard lens and domain classifier do not
# exercise shape consistency. We DO have a shape guard (verify_shape_guard.sh)
# that catches this exact defect. Coverage must say so explicitly, and must
# distinguish "not checked in this mode, though we have a check for it" from
# "cannot be checked without a reference implementation".
shape_report_file=$(mktemp /tmp/verify_report_shape.XXXXXX)
trap 'rm -f "$zero_report_file" "$REPORT_FILE" "$shape_report_file"' EXIT
if [ -f "$REPORTER" ]; then
    "$PYTHON" "$REPORTER" --fixture "$ROOT/tests/regression/perceptron_shape_defect_snapshot.py" > "$shape_report_file" 2>/dev/null
fi

shape_coverage_required="Argument shape and length consistency was not checked in this mode; the separate shape guard (tests/verify_shape_guard.sh) can detect this class of defect."
shape_coverage_text=$(sed -n '/^═══ COVERAGE ═══/,/^═══ [A-Z]/p' "$shape_report_file" | tr -s ' \n' ' ')
if echo "$shape_coverage_text" | grep -qF "$shape_coverage_required"; then
    gate "Coverage names shape/length consistency and cites the shape guard" PASS
else
    gate "Coverage names shape/length consistency and cites the shape guard" FAIL
    echo "       required phrase: $shape_coverage_required"
fi

# ---------------------------------------------------------------- final
echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: one or more report assertions failed."
    exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
    echo ">>> INCONCLUSIVE: report could not be fully evaluated."
    exit 2
else
    echo ">>> ACCEPTED: generated report satisfies all assertions."
    exit 0
fi
