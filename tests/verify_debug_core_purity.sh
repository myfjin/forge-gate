#!/usr/bin/env bash
# Static purity check for src/forge_gate/debug_core.py — the debug core must
# stay taxonomy-agnostic and forge-gate-agnostic so a future patterns-tool
# caller (or extraction to aura-debug-core/) is copy-paste, not a rewrite.
# BRIEF-DEBUG-FEATURE-2026-09-05.md §3a rules 1 and 2; §6.5 wires this in as
# an assertion.
#
# RED = the core reached for a forge-gate concept it must not know about.
# Two forbidden things:
#   (a) any `from forge_gate.lenses` / `import forge_gate.lenses`
#       (same for forge_gate.unsupported)
#   (b) any occurrence of a U-code literal (U1:, U2:, U3:, U4:) — the core
#       must not pattern-match on codes it does not own.
#
# This script has been observed going RED on a deliberate divergence
# (temporarily added `from forge_gate.unsupported import Unsupported`, ran
# this, saw exit 1 + the offending line, removed it). See the commit body.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/src/forge_gate/debug_core.py"

if [[ ! -f "$TARGET" ]]; then
  echo "FAIL: $TARGET not found"; exit 2
fi

fail=0

# (a) forbidden imports — every known spelling that reaches forge_gate.lenses
# or forge_gate.unsupported must be caught. Anchor allows leading whitespace so
# a function-local or TYPE_CHECKING-block import cannot slip past. Bypasses
# named by 6E in round 2 (2026-09-07): indented `from forge_gate.lenses`,
# `importlib.import_module("forge_gate.lenses")`, `__import__("forge_gate.lenses")`,
# and `from forge_gate import *` (wildcard could re-export either submodule).
#
# Six shapes, one alternation. `[[:space:]]*` at the start covers indentation.
if grep -nE "^[[:space:]]*(from[[:space:]]+forge_gate\.(lenses|unsupported)|from[[:space:]]+forge_gate[[:space:]]+import[[:space:]]+(\*|.*\b(lenses|unsupported)\b)|import[[:space:]]+forge_gate\.(lenses|unsupported))" "$TARGET" \
   || grep -nE "(importlib\.import_module|__import__)[[:space:]]*\([[:space:]]*['\"]forge_gate\.(lenses|unsupported)" "$TARGET"; then
  echo "FAIL: debug_core.py imports a forge-gate concept it must not know about (rule 1)"
  fail=1
fi

# (b) U-code literals — any of U1: U2: U3: U4: as string content
if grep -nE '"U[0-9]+:|'"'"'U[0-9]+:' "$TARGET"; then
  echo "FAIL: debug_core.py contains a U-code literal (rule 2 — routing lives in the caller)"
  fail=1
fi

if [[ $fail -eq 0 ]]; then
  echo "OK: debug_core.py purity — no forge-gate imports, no U-code literals"
fi
exit $fail
