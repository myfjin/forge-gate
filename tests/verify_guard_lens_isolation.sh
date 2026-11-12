#!/bin/bash
# macOS ships no setsid. Its role here is to give `timeout` its own process
# group to reap; the Python side already isolates via start_new_session, so
# running without it is bounded, just one less belt. Empty expands to nothing.
SETSID="$(command -v setsid || true)"
# ADVERSARIAL HANG / RESOURCE ISOLATION TEST for the GUARD LENS.
#
# G17 must ensure submitted AI-generated code cannot take the lens (and the deploy host) with
# it. A lens that never returns is worse than a lens that is wrong. This test
# defines the minimum bar: four adversarial fixtures, each must yield
# verdict:INCONCLUSIVE and leave ZERO surviving python3 guard_lens processes.
#
# This test is expected to go RED against a lens that executes fixtures
# in-process without a hard timeout / process-group cleanup. It is GREEN only
# when the lens properly sandboxes fixture execution.
#
# EXIT 0 = lens isolation ACCEPTED.  EXIT 1 = lens isolation REJECTED.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GUARD_LENS="$ROOT/tools/guard_lens.py"
PYTHON="/usr/bin/python3"

# Probe bounds: the test must never become the next thing that cooks the box.
# 45s, not 15s. The lens bounds a hanging fixture correctly on BOTH platforms —
# verified on macOS: rc=0, INCONCLUSIVE verdict, zero survivors — but it needs
# ~25s there because it runs several probes sequentially and process spawning is
# slower off Linux. A 15s budget failed a lens that was working, which is a test
# too tight, not a defect. Kept finite so a genuine hang still fails.
PROBE_TIMEOUT=45
KILL_AFTER=5

# ---------------------------------------------------------------- temp dir
FIXTURE_DIR=$(mktemp -d /tmp/guard_lens_isolation.XXXXXX)
if [ ! -d "$FIXTURE_DIR" ]; then
    echo "FATAL: mktemp failed to create fixture directory"
    exit 1
fi

# The task's required assertion is `pgrep -cf 'python3 .*guard_lens'`. In this
# agent environment that regex also matches the agent's own cmdline (it contains
# the task text). We therefore run the exact command, then verify each match is
# actually a python3 process executing guard_lens.py. False positives are not
# counted as survivors.
_count_real_guard_lens() {
    local real=0
    local pid exe cmd
    for pid in $(pgrep -f 'python3 .*guard_lens' 2>/dev/null); do
        # Never count the test runner itself.
        if [ "$pid" -eq $$ ]; then continue; fi
        exe=$(readlink "/proc/$pid/exe" 2>/dev/null || echo "")
        case "$exe" in
            */python*|python*) : ;;
            *) continue ;;
        esac
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
        if ! echo "$cmd" | grep -qE 'guard_lens\.py'; then
            continue
        fi
        real=$((real + 1))
    done
    echo "$real"
}

_kill_real_guard_lens() {
    local pid exe cmd
    for pid in $(pgrep -f 'python3 .*guard_lens' 2>/dev/null); do
        if [ "$pid" -eq $$ ]; then continue; fi
        exe=$(readlink "/proc/$pid/exe" 2>/dev/null || echo "")
        case "$exe" in
            */python*|python*) : ;;
            *) continue ;;
        esac
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
        if echo "$cmd" | grep -qE 'guard_lens\.py'; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
}

cleanup() {
    # Kill any real guard_lens tree that outlived a probe, then remove temp files.
    _kill_real_guard_lens
    rm -rf "$FIXTURE_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------- helpers
note() { printf '%-62s %s\n' "$1" "$2"; }

write_fixture() {
    local name="$1"
    local path="$FIXTURE_DIR/$name"
    cat > "$path"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "FATAL: failed to write fixture $path (exit $rc)"
        exit 1
    fi
    if [ ! -f "$path" ] || [ ! -s "$path" ]; then
        echo "FATAL: fixture $path missing or empty after write"
        exit 1
    fi
    printf '%s  %s\n' "$(md5sum "$path" | awk '{print $1}')" "$path"
}

# Load-bearing assertion: a verdict that arrives while an orphan keeps burning
# a core is a false all-clear.
assert_no_lens_processes() {
    local survivors
    survivors=$(_count_real_guard_lens)
    if [ "$survivors" -ne 0 ]; then
        note "survivors: $survivors python3 guard_lens process(es) still running" "❌ FAIL"
        _kill_real_guard_lens
        return 1
    fi
    return 0
}

# Run one fixture through the lens with hard timeout + process-group cleanup.
# Each probe runs in its own session so timeout can kill the whole tree, and
# --kill-after guarantees SIGKILL if SIGTERM is ignored.
run_probe() {
    local label="$1"
    local fixture="$2"
    local out="$FIXTURE_DIR/${label}.out"
    local err="$FIXTURE_DIR/${label}.err"
    local verdict=""
    local rc=0

    echo
    echo "--- probe: $label ---"

    # No leftover lens processes from a previous probe.
    assert_no_lens_processes || return 1

    # setsid gives timeout its own process group to reap. --kill-after is the
    # hard ceiling. rc is captured on its own line, never in a pipeline.
    $SETSID timeout --kill-after="${KILL_AFTER}s" "${PROBE_TIMEOUT}s" \
        "$PYTHON" "$GUARD_LENS" --fixture "$fixture" > "$out" 2> "$err"
    rc=$?

    echo "lens exit code: $rc"
    if [ -s "$out" ]; then
        head -5 "$out"
    fi
    if [ -s "$err" ]; then
        head -5 "$err"
    fi

    # After the probe, no guard_lens process may survive.
    assert_no_lens_processes || return 1

    # Timeout delivered SIGTERM (124) or SIGKILL (137) — the lens did not
    # return on its own. That is a hang, regardless of any partial output.
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        note "$label: lens did not return within ${PROBE_TIMEOUT}s" "❌ FAIL"
        return 1
    fi

    if [ "$rc" -ne 0 ]; then
        note "$label: lens exited non-zero (rc=$rc)" "❌ FAIL"
        return 1
    fi

    verdict=$(grep -E '^verdict:' "$out" | head -1 | sed 's/^verdict://' | awk '{print $1}')
    if [ -z "$verdict" ]; then
        note "$label: no verdict line in output" "❌ FAIL"
        return 1
    fi

    echo "verdict: $verdict"
    if [ "$verdict" != "INCONCLUSIVE" ]; then
        note "$label: expected INCONCLUSIVE, got $verdict" "❌ FAIL"
        return 1
    fi

    note "$label: lens returned INCONCLUSIVE and left no processes" "✅ PASS"
    return 0
}

# ---------------------------------------------------------------- fixtures
# Each fixture exposes a scalar float parameter so the lens tries to call it
# with poison. The adversarial behavior lives in the function body, where the
# current (pre-G17) lens executes in-process.

write_fixture "infinite_loop.py" <<'PY'
def loop_forever(x: float) -> float:
    # Pre-G17 lens calls this in-process and never returns.
    while True:
        pass
PY

write_fixture "unbounded_recursion.py" <<'PY'
def recurse_forever(x: float) -> float:
    # Pre-G17 lens executes this in-process; raises or crashes.
    return recurse_forever(x)
PY

write_fixture "large_allocation.py" <<'PY'
def allocate_forever(x: float) -> float:
    # Large but not obviously fatal allocation. A sandboxed lens should
    # detect resource pressure and report INCONCLUSIVE, not silently PASS.
    _ = [0.0] * (2 ** 25)
    return 1.0
PY

write_fixture "orphan_child.py" <<'PY'
import os
import sys

def spawn_orphan(x: float) -> float:
    # Fork a child that escapes the lens process group so a bare p.kill()
    # (or even a killpg that misses the escape) leaves it spinning.
    # The parent side then loops forever, so the lens call itself hangs.
    pid = os.fork()
    if pid == 0:
        # Grandchild: new process group, no stdin/stdout, burn a core forever.
        os.setpgrp()
        try:
            sys.stdout.close()
            sys.stderr.close()
            sys.stdin.close()
        except Exception:
            pass
        while True:
            pass
    # Parent (lens process): never return.
    while True:
        pass
    return 1.0
PY

# ---------------------------------------------------------------- run probes
FAILED=0

echo "=================================================================="
echo " GUARD LENS ISOLATION TEST — adversarial hang / resource probes"
echo " date: $(date -u +%FT%TZ)"
echo " lens: $GUARD_LENS"
echo "=================================================================="

run_probe "infinite_loop" "$FIXTURE_DIR/infinite_loop.py" || FAILED=1
run_probe "unbounded_recursion" "$FIXTURE_DIR/unbounded_recursion.py" || FAILED=1
run_probe "large_allocation" "$FIXTURE_DIR/large_allocation.py" || FAILED=1
run_probe "orphan_child" "$FIXTURE_DIR/orphan_child.py" || FAILED=1

# ---------------------------------------------------------------- final
echo
echo "=================================================================="
if [ "$FAILED" -ne 0 ]; then
    echo ">>> REJECTED: guard_lens isolation failed one or more adversarial probes."
    exit 1
else
    echo ">>> ACCEPTED: guard_lens survived all adversarial probes with INCONCLUSIVE."
    exit 0
fi
