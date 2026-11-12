#!/bin/bash
# STATIC ACCEPTANCE TEST: Python AST API compatibility.
#
# Several AST node types (ast.Num, ast.Str, ast.Bytes, ast.NameConstant,
# ast.Ellipsis) were deprecated in Python 3.8 and removed in Python 3.12.
# the deploy host runs Python 3.10 so the code still executes, but a macOS/Python 3.12+
# machine crashes with AttributeError. This gate catches the class by static
# inspection since the deploy host cannot catch it at runtime.
#
# EXIT 0 = no banned AST APIs found. EXIT 1 = found.

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

BANNED=(
    "ast\.Num"
    "ast\.Str"
    "ast\.Bytes"
    "ast\.NameConstant"
    "ast\.Ellipsis"
)

echo "=================================================================="
echo " PYTHON AST API COMPATIBILITY — static check"
echo " date: $(date -u +%FT%TZ)"
echo "=================================================================="

violations=()
for pattern in "${BANNED[@]}"; do
    # Only tools/ and src/ are in scope; tests/ may legitimately mention them.
    while IFS= read -r match; do
        violations+=("$match")
    done < <(grep -R -nE "$pattern" "$ROOT/tools" "$ROOT/src" 2>/dev/null || true)
done

if [ "${#violations[@]}" -eq 0 ]; then
    gate "no Python 3.12-removed AST APIs in tools/ or src/" PASS
else
    gate "no Python 3.12-removed AST APIs in tools/ or src/" FAIL
    for v in "${violations[@]}"; do
        echo "       $v"
    done
fi

echo; echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: banned AST API usage found."
    exit 1
else
    echo ">>> ACCEPTED: no banned AST APIs found."
    exit 0
fi
