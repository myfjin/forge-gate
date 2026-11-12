#!/usr/bin/env bash
# verify_input_robustness.sh — Forge Gate input-robustness reporting gate.
#
# For each JS pattern passed as an argument the gate runs its entry function on
# a set of generated inputs BEYOND the pattern's own fixture:
#   uncentred        — features with a large non-zero mean
#   wildly_scaled    — one column many orders of magnitude larger than another
#   near_collinear   — two columns almost linearly dependent
#   rank_deficient   — one column is an exact duplicate of another
#   single_row       — only one sample
#   constant_column  — one feature is constant across all samples
#
# It reports, per input, the most severe outcome seen in any numeric leaf of
# the returned value tree:
#   FINITE   — every numeric leaf is finite and within the divergence threshold
#   NaN-Inf  — at least one numeric leaf is NaN or Infinity
#   DIVERGED — at least one numeric leaf has magnitude above the threshold
#   THREW    — the call threw an exception (e.g. rank-deficient rejection)
#
# This is a REPORTING gate, not a pass/fail gate.  Exit code:
#   0 — all files were exercised and reported
#   1 — a setup or alias error prevented a file from being exercised
#
# Divergence threshold justification:
#   1e6.  Correct linear-regression coefficients and k-means coordinates for the
#   small, deliberately-constructed perturbation datasets below are O(1) to
#   O(100).  A value exceeding 1e6 therefore signals unbounded growth (Jacobi
#   on uncentred data is observed to reach ~1e53) rather than a legitimate
#   large-but-finite answer.

set -euo pipefail

usage() {
    echo "Usage: $0 <file.js> [<file.js> ...]"
    exit 2
}

if [[ $# -eq 0 ]]; then
    usage
fi

DRIVER=$(mktemp)
trap 'rm -f "$DRIVER"' EXIT

cat > "$DRIVER" <<'PY'
import argparse, hashlib, json, os, re, subprocess, sys, tempfile
from collections import defaultdict

ALIAS_NAME = "linearRegressionGD"
DIVERGENCE_THRESHOLD = 1e6

PERTURBATIONS_LINREG = {
    "uncentred": {
        "X": [[1000.0, 2000.0], [1001.0, 1999.0], [1002.0, 2001.0], [1003.0, 2000.0]],
        "y": [3000.0, 3001.0, 3002.0, 3003.0],
    },
    "wildly_scaled": {
        "X": [[1.0, 1.0e6], [2.0, 2.0e6], [3.0, 3.0e6], [4.0, 4.0e6]],
        "y": [3.0, 5.0, 7.0, 9.0],
    },
    "near_collinear": {
        "X": [[1.0, 1.0001], [2.0, 2.0001], [3.0, 3.0001], [4.0, 4.0001]],
        "y": [3.0, 5.0, 7.0, 9.0],
    },
    "rank_deficient": {
        "X": [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]],
        "y": [3.0, 5.0, 7.0, 9.0],
    },
    "single_row": {
        "X": [[1.0, 2.0]],
        "y": [3.0],
    },
    "constant_column": {
        "X": [[1.0, 5.0], [2.0, 5.0], [3.0, 5.0], [4.0, 5.0]],
        "y": [3.0, 5.0, 7.0, 9.0],
    },
}

PERTURBATIONS_KMEANS = {
    "uncentred": {
        "X": [
            [1000.0, 1000.0], [1001.0, 1000.0], [1002.0, 1000.0],
            [1000.0, 1005.0], [1001.0, 1005.0], [1002.0, 1005.0],
            [1000.0, 1010.0], [1001.0, 1010.0], [1002.0, 1010.0],
        ],
        "y": [],
    },
    "wildly_scaled": {
        "X": [
            [1.0, 1.0e6], [2.0, 1.0e6], [3.0, 1.0e6],
            [1.0, 2.0e6], [2.0, 2.0e6], [3.0, 2.0e6],
            [1.0, 3.0e6], [2.0, 3.0e6], [3.0, 3.0e6],
        ],
        "y": [],
    },
    "near_collinear": {
        "X": [
            [1.0, 1.0001], [2.0, 2.0001], [3.0, 3.0001],
            [1.0, 2.0001], [2.0, 3.0001], [3.0, 4.0001],
            [1.0, 3.0001], [2.0, 4.0001], [3.0, 5.0001],
        ],
        "y": [],
    },
    "rank_deficient": {
        "X": [
            [1.0, 1.0], [2.0, 2.0], [3.0, 3.0],
            [4.0, 4.0], [5.0, 5.0], [6.0, 6.0],
            [7.0, 7.0], [8.0, 8.0], [9.0, 9.0],
        ],
        "y": [],
    },
    "single_row": {
        "X": [[1.0, 2.0]],
        "y": [],
    },
    "constant_column": {
        "X": [
            [1.0, 5.0], [2.0, 5.0], [3.0, 5.0],
            [4.0, 5.0], [5.0, 5.0], [6.0, 5.0],
            [7.0, 5.0], [8.0, 5.0], [9.0, 5.0],
        ],
        "y": [],
    },
}


def derive_family(text):
    m = re.search(r'@forge-family\s+(\S+)', text)
    return m.group(1) if m else 'linreg'


def derive_entry_name(text, alias_name):
    pattern = re.compile(
        r'\bconst\s+' + re.escape(alias_name) + r'\s*=\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*;'
    )
    for line in text.splitlines():
        idx = line.find('//')
        if idx >= 0:
            line = line[:idx]
        m = pattern.search(line)
        if m:
            return m.group(1)
    return None


def strip_iife(source):
    """Remove the trailing self-test IIFE from a Forge Gate pattern file."""
    idx = source.find('(function () {')
    if idx < 0:
        return source
    # Match the outer parenthesised function expression.
    depth = 1
    i = idx + 1
    while i < len(source) and depth > 0:
        if source[i] == '(':
            depth += 1
        elif source[i] == ')':
            depth -= 1
        i += 1
    # Skip the IIFE call parentheses '()' if present, then any trailing semicolon.
    while i < len(source) and source[i] in ' \t\r\n':
        i += 1
    if i < len(source) and source[i] == '(':
        depth = 1
        i += 1
        while i < len(source) and depth > 0:
            if source[i] == '(':
                depth += 1
            elif source[i] == ')':
                depth -= 1
            i += 1
    while i < len(source) and source[i] in ' \t\r\n;':
        i += 1
    return source[:idx] + source[i:]


def make_harness(source, entry_name, perturbation_name, data):
    stripped = strip_iife(source)
    harness = f"""
{stripped}

const __fg_entry = typeof {entry_name} !== 'undefined' ? {entry_name} : (typeof globalThis.{entry_name} !== 'undefined' ? globalThis.{entry_name} : null);
if (!__fg_entry) {{
    console.error('harness-error: entry function {entry_name} not found');
    process.exit(3);
}}

const __fg_data = {json.dumps(data)};
const __fg_threshold = {DIVERGENCE_THRESHOLD};

function __fg_maxAbs(v) {{
    if (v === null || v === undefined) return 0;
    if (Array.isArray(v)) {{
        let m = 0;
        for (let i = 0; i < v.length; i++) {{
            const x = __fg_maxAbs(v[i]);
            if (x > m) m = x;
        }}
        return m;
    }}
    if (typeof v === 'object') {{
        let m = 0;
        for (const k in v) {{
            if (Object.prototype.hasOwnProperty.call(v, k)) {{
                const x = __fg_maxAbs(v[k]);
                if (x > m) m = x;
            }}
        }}
        return m;
    }}
    if (typeof v === 'number') return Math.abs(v);
    return 0;
}}

function __fg_hasNonFinite(v) {{
    if (v === null || v === undefined) return false;
    if (Array.isArray(v)) return v.some(__fg_hasNonFinite);
    if (typeof v === 'object') {{
        for (const k in v) {{
            if (Object.prototype.hasOwnProperty.call(v, k) && __fg_hasNonFinite(v[k])) return true;
        }}
        return false;
    }}
    if (typeof v === 'number') return !isFinite(v);
    return false;
}}

let __fg_outcome;
let __fg_peak = 0;
let __fg_thrown = null;
try {{
    const result = __fg_entry(__fg_data.X, __fg_data.y, 0.01, 200);
    __fg_peak = __fg_maxAbs(result);
    if (__fg_hasNonFinite(result)) {{
        __fg_outcome = 'NaN-Inf';
    }} else if (__fg_peak > __fg_threshold) {{
        __fg_outcome = 'DIVERGED';
    }} else {{
        __fg_outcome = 'FINITE';
    }}
}} catch (e) {{
    __fg_outcome = 'THREW';
    __fg_thrown = e.message;
}}
console.log(JSON.stringify({{ "name": {json.dumps(perturbation_name)}, "outcome": __fg_outcome, "maxAbs": __fg_peak, "thrown": __fg_thrown }}));
"""
    return harness


def run_pattern(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    entry = derive_entry_name(text, ALIAS_NAME)
    if not entry:
        return None, f"missing alias: const {ALIAS_NAME} = <NAME>;"

    family = derive_family(text)
    perturbations = PERTURBATIONS_KMEANS if family == 'kmeans' else PERTURBATIONS_LINREG

    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in perturbations.items():
            harness_path = os.path.join(tmp, f'{name}.js')
            with open(harness_path, 'w', encoding='utf-8') as f:
                f.write(make_harness(text, entry, name, data))

            try:
                proc = subprocess.run(
                    ['node', harness_path],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                results[name] = {"outcome": "THREW", "maxAbs": 0, "thrown": "timeout"}
                continue

            if proc.returncode != 0:
                err = (proc.stderr or '').strip() or '(empty stderr)'
                return None, f"harness failed for {name}: {err}"
            try:
                res = json.loads(proc.stdout.strip().splitlines()[-1])
                results[name] = {"outcome": res["outcome"], "maxAbs": res["maxAbs"], "thrown": res["thrown"]}
            except Exception as e:
                return None, f"could not parse results for {name}: {e}"

    return results, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()

    names = ["uncentred", "wildly_scaled", "near_collinear", "rank_deficient", "single_row", "constant_column"]

    rows = []
    errors = []
    for path in args.files:
        results, err = run_pattern(path)
        if err:
            errors.append((path, err))
            rows.append((os.path.basename(path), {n: 'ERROR' for n in names}, err))
            continue
        rows.append((os.path.basename(path), results, None))

    # Header
    print(f"threshold={DIVERGENCE_THRESHOLD}")
    header = "file".ljust(40) + "".join(n.ljust(16) for n in names)
    print(header)
    print("-" * len(header))

    # Rows
    for basename, results, err in rows:
        line = basename[:39].ljust(40)
        for name in names:
            if err:
                cell = 'ERROR'
            else:
                cell = results.get(name, {}).get('outcome', 'MISSING')
            line += cell.ljust(16)
        print(line)
        if err:
            print(f"  error: {err}")

    if errors:
        print("\nERRORS encountered during runs:")
        for path, err in errors:
            print(f"  {path}: {err}")
        sys.exit(1)

    print("\nOK: all files exercised and reported.")
    sys.exit(0)


if __name__ == '__main__':
    main()
PY

python3 "$DRIVER" "$@"
