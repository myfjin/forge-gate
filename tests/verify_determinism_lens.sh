#!/bin/bash
# ACCEPTANCE TEST for the DETERMINISM LENS — written BEFORE the code (request-first law).
#
# Anyone can run this. It prints numbers and a single final verdict. No judgment required,
# no report to trust: the output IS the verdict. Zero LLM in the verdict path — our Law 1,
# applied to our own process.
#
#   bash "$ROOT/tests/verify_determinism_lens.sh"
#
# EXIT 0 = ACCEPTED. EXIT 1 = REJECTED (and it names which gate failed).

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "FATAL: no $ROOT"; exit 1; }
export PYTHONPATH="$ROOT/src:$ROOT/tools"
PY=python3
FAILED=0
note() { printf '%-58s %s\n' "$1" "$2"; }
gate() { if [ "$2" = "PASS" ]; then note "$1" "✅ PASS"; else note "$1" "❌ FAIL"; FAILED=1; fi; }

# THE DISTINCTION THIS RULER GOT WRONG ON 2026-08-01:
#   FAIL     = a lens OBJECTED           -> a verdict about the code
#   UNSTABLE = a lens ERRORED            -> the harness died; NOT a verdict about anything
# The first run of this script conflated them, recorded 10 subprocess failures as
# "the lens is unsound", and REJECTED a lens that returns 10/10 PASS when run alone.
# A harness error must never be reported as a finding about the code under test.
verdict_of() {   # <file> <pattern> <lenses>  -> prints first-line verdict, retries harness errors
  local f="$1" p="$2" l="$3" r=""
  for _try in 1 2 3; do
    r=$(timeout 300 $PY -m forge_gate.cli verify "$f" --pattern "$p" --lenses "$l" 2>/dev/null | head -1)
    case "$r" in UNSTABLE*|"") sleep 1 ;; *) echo "$r"; return ;; esac
  done
  echo "${r:-NO-OUTPUT} (persisted after 3 attempts)"
}

# ASK THE LENS, NOT THE VERDICT.
# A quarantined lens cannot change the verdict line, so reading "PASS:" tells you nothing
# about what the lens FOUND. The first version of the staleness check below made exactly
# that mistake -- it read a verdict of PASS on a quarantined pattern and reported the
# quarantine as stale. No objection recorded is not the same as no problem found.
# This reads the determinism row out of the per-lens table instead.
lens_status_of() {   # <file> <pattern> -> the determinism lens's OWN status
  local f="$1" p="$2" out=""
  for _try in 1 2 3; do
    out=$(timeout 300 $PY -m forge_gate.cli verify "$f" --pattern "$p" --lenses determinism 2>/dev/null \
          | grep -E "^[[:space:]]+determinism" | awk '{print $2}')
    case "$out" in ERROR|"") sleep 1 ;; *) echo "$out"; return ;; esac
  done
  echo "${out:-NO-LENS-LINE}"
}

echo "=================================================================="
echo " DETERMINISM LENS — ACCEPTANCE TEST"
echo " commit: $(git rev-parse --short HEAD 2>/dev/null)   date: $(date -u +%FT%TZ)"
echo "=================================================================="

# ---------------------------------------------------------------- fixtures
mkdir -p /tmp/dl_fixtures
# NEGATIVE CONTROL: genuinely non-deterministic, jitter BELOW metamorphic's 1e-3 tolerance.
# Measured 2026-08-01: all four existing lenses PASS this file. The lens MUST catch it.
cat > /tmp/dl_fixtures/nd_micro.py <<'PYEOF'
import random
def linear_regression_gd(X, y, learning_rate=0.01, iterations=100):
    n_features=len(X[0]) if X else 0; n=len(X)
    weights=[0.0]*n_features; bias=0.0
    for _ in range(iterations):
        preds=[bias+sum(weights[j]*X[i][j] for j in range(n_features)) for i in range(n)]
        err=[preds[i]-y[i] for i in range(n)]
        dw=[sum(err[i]*X[i][j] for i in range(n))/n for j in range(n_features)]
        db=sum(err)/n
        weights=[weights[j]-learning_rate*dw[j] for j in range(n_features)]
        bias-=learning_rate*db
    weights=[w*random.uniform(0.9999995,1.0000005) for w in weights]
    return weights, bias
PYEOF
# POSITIVE CONTROL: a correct ALTERNATIVE implementation. Must NOT be failed (Law 5).
cp tests/corpus/linreg_normal_eq.py /tmp/dl_fixtures/ 2>/dev/null

# ---------------------------------------------------------------- GATE 1
# The lens must EXIST and be selectable. If this fails, nothing else is meaningful.
echo; echo "--- GATE 1: lens exists and is wired ---"
if $PY -c "import sys; sys.path.insert(0,'src'); from forge_gate.lenses import ALL_LENSES; sys.exit(0 if 'determinism' in ALL_LENSES else 1)" 2>/dev/null; then
  gate "determinism registered in ALL_LENSES" PASS
else
  gate "determinism registered in ALL_LENSES" FAIL
  echo; echo ">>> REJECTED: lens not wired. Remaining gates not meaningful."; exit 1
fi

# ---------------------------------------------------------------- GATE 2
# THE POINT OF THE WHOLE THING. This file is a measured false-PASS today.
echo; echo "--- GATE 2: NEGATIVE CONTROL — non-deterministic code must be CAUGHT ---"
OUT=$(timeout 300 $PY -m forge_gate.cli verify /tmp/dl_fixtures/nd_micro.py \
      --pattern linear_regression_gd --lenses all 2>&1 | grep -vE "RuntimeWarning|warn\(Runtime")
echo "$OUT" | head -4
if echo "$OUT" | grep -qiE "determinism .*(FAIL|objected)" || \
   { echo "$OUT" | head -1 | grep -q "^FAIL" && echo "$OUT" | grep -qi "determinism"; }; then
  gate "nd_micro.py caught BY THE DETERMINISM LENS" PASS
else
  gate "nd_micro.py caught BY THE DETERMINISM LENS" FAIL
  echo "    (a FAIL from some other lens does NOT count — the determinism lens must be the objector)"
fi

# ---------------------------------------------------------------- GATE 3
# RELATION SOUNDNESS LAW: a lens may only gate if the canonical oracle satisfies it.
echo; echo "--- GATE 3: SOUNDNESS — all 10 canonical oracles must PASS the lens ---"
# Soundness is a PER-PATTERN fact. A pattern may fail the lens ONLY if it is declared
# in DETERMINISM_ADVISORY_PATTERNS (where the lens does not gate). An undeclared failure
# means the lens gates something its own oracle cannot satisfy = unsound.
QUAR=$($PY -c "import sys; sys.path.insert(0,'src'); from forge_gate.lenses import DETERMINISM_ADVISORY_PATTERNS as Q; print(' '.join(sorted(Q)))" 2>/dev/null)
echo "    declared quarantine: ${QUAR:-<none>}"
BAD=0; STALE=0
for f in tests/corpus/*_canonical_good.py; do
  p=$(basename "$f" _canonical_good.py)
  L=$(lens_status_of "$f" "$p")          # what the LENS found, independent of gating
  declared=0; case " $QUAR " in *" $p "*) declared=1;; esac
  case "$L" in
    PASS) if [ "$declared" = 1 ]; then echo "    STALE QUARANTINE: $p — lens now PASSES it, promote it"; STALE=$((STALE+1)); fi ;;
    FAIL) if [ "$declared" = 1 ]; then echo "    (declared advisory, lens genuinely FAILS the oracle: $p)";
          else echo "    UNDECLARED OFFENDER: $p -> lens says $L"; BAD=$((BAD+1)); fi ;;
    *)    echo "    INCONCLUSIVE: $p -> lens says $L"; BAD=$((BAD+1)) ;;
  esac
done
[ "$BAD" -eq 0 ] && gate "no undeclared oracle failures" PASS \
                 || gate "$BAD UNDECLARED oracle failure(s) — lens is unsound" FAIL
[ "$STALE" -eq 0 ] && gate "quarantine has no stale entries" PASS \
                   || gate "$STALE stale quarantine entr(ies) — promote them" FAIL

# ---------------------------------------------------------------- GATE 4
# LAW 5: a gate that fails correct code kills the tool.
echo; echo "--- GATE 4: NO FALSE FAILS on the 30 correct alternatives ---"
BAD=0; TOT=0
while IFS=$'\t' read -r f p; do
  [ -z "$f" ] && continue
  TOT=$((TOT+1))
  r=$(verdict_of "$f" "$p" determinism)
  case "$r" in PASS*) ;; *) echo "    FALSE FAIL: $f -> $r"; BAD=$((BAD+1));; esac
done < <($PY - <<'PYEOF'
import json
for r in json.load(open("tests/corpus_manifest.json")):
    if r.get("kind")=="alternative_correct":
        print(r["file"]+"\t"+r["pattern"])
PYEOF
)
echo "    checked $TOT alternative_correct files"
[ "$BAD" -eq 0 ] && gate "0 false fails on correct alternatives" PASS \
                 || gate "$BAD FALSE FAIL(S) — violates Law 5" FAIL

# ---------------------------------------------------------------- GATE 6
echo; echo "--- GATE 6: the lens itself is DETERMINISTIC (run twice, same verdict) ---"
A=$(verdict_of /tmp/dl_fixtures/nd_micro.py linear_regression_gd determinism)
B=$(verdict_of /tmp/dl_fixtures/nd_micro.py linear_regression_gd determinism)
echo "    run1: $A"; echo "    run2: $B"
[ "$A" = "$B" ] && gate "verdict stable across runs" PASS || gate "verdict UNSTABLE across runs" FAIL

# ---------------------------------------------------------------- GATE 7
echo; echo "--- GATE 7: existing test suites still green ---"
T=""
for _try in 1 2; do
  T=$(timeout 900 $PY -m pytest tests/ -q --ignore=tests/test_relation_soundness.py 2>&1 | tail -3 | grep -oE "[0-9]+ passed|[0-9]+ failed" | tr '\n' ' ')
  echo "$T" | grep -q "failed" || break
  sleep 2
done
echo "    pytest: ${T:-NO RESULT}"
echo "$T" | grep -q "failed" && gate "pytest" FAIL || { echo "$T" | grep -q "passed" && gate "pytest all passed" PASS || gate "pytest produced no result" FAIL; }

S=$(timeout 900 $PY tests/test_relation_soundness.py 2>&1 | tail -1)
echo "    soundness law: $S"
echo "$S" | grep -q "^PASS" && gate "relation soundness law holds" PASS || gate "relation soundness law BROKEN" FAIL

# ---------------------------------------------------------------- GATE 5 (last: it is the heaviest, and load was what broke this ruler)
echo; echo "--- GATE 5 (last: it is the heaviest, and load was what broke this ruler): NO REGRESSION — corpus still 100/100 ---"
C=$(timeout 1800 $PY tests/run_corpus.py 2>&1 | grep -oE "[0-9]+/100 correct" | head -1)
echo "    corpus: ${C:-NO RESULT}"
[ "$C" = "100/100 correct" ] && gate "corpus 100/100" PASS || gate "corpus NOT 100/100 (got ${C:-none})" FAIL

# ---------------------------------------------------------------- verdict
echo
echo "=================================================================="
if [ "$FAILED" -eq 0 ]; then
  echo " RESULT: ✅ ACCEPTED — all 7 gates green"
  echo "=================================================================="; exit 0
else
  echo " RESULT: ❌ REJECTED — at least one gate failed (see ❌ above)"
  echo "=================================================================="; exit 1
fi
