#!/bin/bash
# STATIC ACCEPTANCE TEST: no hardcoded repo checkout paths.
#
# The test scripts and tools were full of absolute paths that hardcode the
# checkout location of this repo (e.g. $HOME/forge-gate). That makes the whole
# suite only run on the machine where that exact path exists.
#
# This gate catches the class by static inspection: any absolute path under
# /home/ or /Users/ that locates the forge-gate checkout is a violation.
# Runtime locations (engine binaries, external data, log dirs) are not the
# target; the defect is hardcoding the *checkout location* of this repo.
#
# EXIT 0 = no hardcoded checkout paths found. EXIT 1 = found.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILED=0

note() { printf '%-62s %s\n' "$1" "$2"; }
gate() {
    if [ "$2" = "PASS" ]; then
        note "$1" "✅ PASS"
    else
        note "$1" "❌ FAIL"
        FAILED=1
    fi
}

echo "=================================================================="
echo " PORTABLE PATHS — static check"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

# Match absolute paths that place the forge-gate checkout under /home/ or /Users/.
violations=()
while IFS= read -r match; do
    violations+=("$match")
done < <(grep -R -I --exclude-dir=__pycache__ --exclude='*.pyc' -nE '/(home|Users)/[^/]*/forge-gate' "$ROOT/tests" "$ROOT/tools" 2>/dev/null || true)

if [ "${#violations[@]}" -eq 0 ]; then
    gate "no hardcoded repo checkout paths" PASS
else
    gate "no hardcoded repo checkout paths" FAIL
    for v in "${violations[@]}"; do
        echo "       $v"
    done
fi

echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: hardcoded checkout path found."
    exit 1
else
    echo ">>> ACCEPTED: no hardcoded checkout paths found."
    exit 0
fi
