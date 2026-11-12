#!/usr/bin/env bash
# verify_js_selftest.sh — Forge Gate ruler for JS pattern self-tests.
#
# For each pattern file passed as an argument the ruler checks:
#   1. the file actually contains a self-test (assert / throw / process.exit)
#   2. `node <file>` on the clean file exits 0
#   3. two mechanically-generated mutants of the entry point are KILLED,
#      i.e. the self-test detects them and the mutant exits non-zero for the
#      RIGHT REASON.  If either mutant survives, or crashes for an unrelated
#      reason, the self-test is cosmetic and the pattern is REJECTED.
#
# Exit code:
#   0 — every file accepted (all three gates pass)
#   1 — one or more files rejected
#
# Entry-point derivation:
#   The file MUST contain a harness-contract alias of the form
#       const linearRegressionGD = <NAME>;
#   The ruler mutates <NAME>, not the alias.  If the alias is missing, or
#   the name it points to is not defined in the file, the file is REJECTED.
#   Use --entry <NAME> as a manual override only (never an automatic default).
#
# Mutation strategy:
#   The original entry function declaration is renamed to __fg_orig_NAME and
#   a wrapper named NAME is appended.  The wrapper runs the original code,
#   then returns a corrupted copy of the result tree:
#     * ZERO mutant — every numeric leaf becomes 0 (shape preserved)
#     * DOUBLE mutant — every numeric leaf is multiplied by 2 (shape preserved)
#   This is uniform, family-agnostic, and harder to fake than a single
#   constant-return mutant: a self-test that only checks non-zero output will
#   survive the zero mutant but be caught by the double mutant; one that only
#   checks property existence will be caught by both.

set -euo pipefail

SELFTEST_RE='(\bassert\b|\bthrow\b|process\.exit\()'
ALIAS_NAME='linearRegressionGD'

usage() {
    echo "Usage: $0 [--entry NAME] <file.js> [<file.js> ...]"
    echo "  --entry NAME  manual mutation target (optional, overrides alias derivation)"
    exit 2
}

manual_entry=''
files=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --entry)
            shift
            if [[ $# -eq 0 ]]; then usage; fi
            manual_entry=$1
            shift
            ;;
        --help|-h)
            usage
            ;;
        --)
            shift
            files+=("$@")
            break
            ;;
        -*)
            usage
            ;;
        *)
            files+=("$1")
            shift
            ;;
    esac
done

if [[ ${#files[@]} -eq 0 ]]; then
    usage
fi

MUTATOR=$(mktemp)
trap 'rm -f "$MUTATOR"' EXIT

cat > "$MUTATOR" <<'PY'
import argparse, re, sys

ALIAS_NAME = "linearRegressionGD"

ZERO_WRAPPER = r'''
function __fg_zero(v) {{
  if (v === null || v === undefined) return v;
  if (Array.isArray(v)) return v.map(__fg_zero);
  if (typeof v === 'object') {{
    const o = {{}};
    for (const k in v) {{
      if (Object.prototype.hasOwnProperty.call(v, k)) o[k] = __fg_zero(v[k]);
    }}
    return o;
  }}
  if (typeof v === 'number') return 0;
  return v;
}}
function {entry}(...args) {{ return __fg_zero(__fg_orig_{entry}.apply(this, args)); }}
'''

DOUBLE_WRAPPER = r'''
function __fg_double(v) {{
  if (v === null || v === undefined) return v;
  if (Array.isArray(v)) return v.map(__fg_double);
  if (typeof v === 'object') {{
    const o = {{}};
    for (const k in v) {{
      if (Object.prototype.hasOwnProperty.call(v, k)) o[k] = __fg_double(v[k]);
    }}
    return o;
  }}
  if (typeof v === 'number') return v * 2;
  return v;
}}
function {entry}(...args) {{ return __fg_double(__fg_orig_{entry}.apply(this, args)); }}
'''


def derive_entry_name(text, alias_name):
    """Find the name that the harness alias points to, or None."""
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


def find_function_bounds(text, name):
    """Return (decl_start, body_end) for the first function declaration of name."""
    decl_re = re.compile(r'function\s+' + re.escape(name) + r'\s*\(')
    m = decl_re.search(text)
    if not m:
        return None
    decl_start = m.start()
    i = m.end()          # after the opening '('
    # match parameter list parentheses (ignore nested parens)
    depth = 1
    while i < len(text) and depth > 0:
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        i += 1
    # skip whitespace to opening brace
    while i < len(text) and text[i] in ' \t\r\n':
        i += 1
    if i >= len(text) or text[i] != '{':
        return None
    body_start = i
    # brace-match the function body, ignoring comments and strings
    depth = 1
    j = i + 1
    state = 'normal'
    while j < len(text) and depth > 0:
        c = text[j]
        if state == 'normal':
            if c == '/' and j + 1 < len(text):
                nxt = text[j + 1]
                if nxt == '/':
                    state = 'line_comment'
                    j += 2
                    continue
                elif nxt == '*':
                    state = 'block_comment'
                    j += 2
                    continue
            elif c in '"\'`':
                state = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
        elif state == 'line_comment':
            if c == '\n':
                state = 'normal'
        elif state == 'block_comment':
            if c == '*' and j + 1 < len(text) and text[j + 1] == '/':
                state = 'normal'
                j += 2
                continue
        else:  # inside string whose closing quote == state
            if c == '\\':
                j += 2
                continue
            elif c == state:
                state = 'normal'
        j += 1
    if depth != 0:
        return None
    body_end = j  # index just after the matching '}'
    return decl_start, body_end


def rename_declaration(text, name, new_name):
    """Rename the first function declaration of `name` to `new_name`.

    Returns (new_text, body_end_after_rename).  The function body is not
    rewritten, only the identifier in the declaration.
    """
    decl_re = re.compile(r'function\s+' + re.escape(name) + r'\s*\(')
    m = decl_re.search(text)
    if not m:
        return None
    matched = m.group(0)
    name_offset = matched.index(name)
    name_start = m.start() + name_offset
    name_end = name_start + len(name)
    new_text = text[:name_start] + new_name + text[name_end:]
    shift = len(new_name) - len(name)
    # body_end from the OLD text; shift it by the length difference
    bounds = find_function_bounds(new_text, new_name)
    if bounds is None:
        return None
    return new_text, bounds[1]


def write_mutants(infile, outdir, entry):
    with open(infile, 'r', encoding='utf-8') as f:
        text = f.read()
    orig_name = f'__fg_orig_{entry}'
    result = rename_declaration(text, entry, orig_name)
    if result is None:
        sys.stderr.write(f"ERROR: could not locate entry function {entry} in {infile}\n")
        sys.exit(3)
    renamed_text, body_end = result
    zero = renamed_text[:body_end] + ZERO_WRAPPER.format(entry=entry) + renamed_text[body_end:]
    double = renamed_text[:body_end] + DOUBLE_WRAPPER.format(entry=entry) + renamed_text[body_end:]
    with open(f"{outdir}/mutant_zero.js", 'w', encoding='utf-8') as f:
        f.write(zero)
    with open(f"{outdir}/mutant_double.js", 'w', encoding='utf-8') as f:
        f.write(double)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--in', dest='infile', required=True)
    parser.add_argument('--outdir', required=True)
    parser.add_argument('--entry', default='')
    args = parser.parse_args()

    with open(args.infile, 'r', encoding='utf-8') as f:
        text = f.read()

    entry = args.entry or derive_entry_name(text, ALIAS_NAME)
    if not entry:
        sys.stderr.write(f"ERROR: missing alias: const {ALIAS_NAME} = <NAME>; not found in {args.infile}\n")
        sys.exit(4)

    if not find_function_bounds(text, entry):
        sys.stderr.write(f"ERROR: derived entry function {entry} is not defined in {args.infile}\n")
        sys.exit(5)

    write_mutants(args.infile, args.outdir, entry)
    print(entry)


if __name__ == '__main__':
    main()
PY

has_selftest() {
    local f=$1
    grep -Eq "$SELFTEST_RE" "$f"
}

run_node() {
    local f=$1 rc
    local stderr_file=$2
    set +e
    node "$f" >/dev/null 2>"$stderr_file"
    rc=$?
    set -e
    echo $rc
}

# Classify a mutant run.  A non-zero exit is only a kill if stderr proves the
# self-test detected the corruption.  Crashes (RangeError / ReferenceError /
# SyntaxError) or exits with no evidence are NOT counted as kills.
classify_mutant() {
    local rc=$1
    local stderr_file=$2
    if [[ $rc -eq 0 ]]; then
        echo "SURVIVED"
        return
    fi
    if [[ -s $stderr_file ]]; then
        # The self-test fired — every real fixture uses this marker.
        if grep -Eq 'self-test:' "$stderr_file"; then
            echo "DETECTED"
            return
        fi
        # Real JS engine crash, not a self-test failure.
        if grep -Eq 'RangeError|ReferenceError|SyntaxError|TypeError' "$stderr_file"; then
            echo "CRASH"
            return
        fi
        # Non-zero exit with stderr, but no recognised signal.
        echo "UNKNOWN"
        return
    fi
    # Non-zero exit with empty stderr is NOT evidence the assert fired.
    echo "UNKNOWN"
}

accepted=0
rejected=0
fail_g1=0
fail_g2=0
fail_alias=0
derived_mismatch=0
fail_g3_zero_survived=0
fail_g3_double_survived=0
fail_g3_zero_crash=0
fail_g3_double_crash=0
fail_g3_zero_unknown=0
fail_g3_double_unknown=0

for f in "${files[@]}"; do
    echo "== $f =="
    if [[ ! -f $f ]]; then
        echo "  SKIP: not a file"
        ((rejected++)) || true
        continue
    fi

    # Gate 1: self-test present
    if ! has_selftest "$f"; then
        echo "  GATE 1 FAIL: no self-test detected (assert/throw/process.exit)"
        ((fail_g1++)) || true
        ((rejected++)) || true
        continue
    fi
    echo "  GATE 1 PASS: self-test present"

    # Gate 2: clean file exits 0
    clean_stderr=$(mktemp)
    clean_rc=$(run_node "$f" "$clean_stderr")
    rm -f "$clean_stderr"
    if [[ $clean_rc -ne 0 ]]; then
        echo "  GATE 2 FAIL: node clean file exited $clean_rc"
        ((fail_g2++)) || true
        ((rejected++)) || true
        continue
    fi
    echo "  GATE 2 PASS: node clean file exited 0"

    # Gate 3: both mutants must be killed for the right reason
    tmp=$(mktemp -d)

    entry_args=()
    if [[ -n $manual_entry ]]; then
        entry_args+=(--entry "$manual_entry")
    fi

    mutator_out=$(python3 "$MUTATOR" --in "$f" --outdir "$tmp" "${entry_args[@]}" 2>&1) || {
        echo "  GATE 3 FAIL: mutation setup failed"
        echo "    $mutator_out"
        rm -rf "$tmp"
        case "$mutator_out" in
            *"missing alias"*)
                ((fail_alias++)) || true
                ;;
            *"derived entry function"*)
                ((derived_mismatch++)) || true
                ;;
        esac
        ((rejected++)) || true
        continue
    }

    derived_entry=$(echo "$mutator_out" | tail -n 1)
    echo "  derived entry: $derived_entry"

    zero_stderr=$(mktemp)
    double_stderr=$(mktemp)
    zero_rc=$(run_node "$tmp/mutant_zero.js" "$zero_stderr")
    double_rc=$(run_node "$tmp/mutant_double.js" "$double_stderr")

    zero_class=$(classify_mutant "$zero_rc" "$zero_stderr")
    double_class=$(classify_mutant "$double_rc" "$double_stderr")

    echo "  zero mutant  rc=$zero_rc class=$zero_class"
    echo "  double mutant rc=$double_rc class=$double_class"

    rm -f "$zero_stderr" "$double_stderr"
    rm -rf "$tmp"

    if [[ $zero_class == "DETECTED" && $double_class == "DETECTED" ]]; then
        echo "  GATE 3 PASS: both mutants killed by self-test"
        ((accepted++)) || true
    else
        echo "  GATE 3 FAIL"
        if [[ $zero_class == "SURVIVED" ]]; then
            echo "    zero mutant survived"
            ((fail_g3_zero_survived++)) || true
        elif [[ $zero_class == "CRASH" ]]; then
            echo "    zero mutant crashed"
            ((fail_g3_zero_crash++)) || true
        elif [[ $zero_class == "UNKNOWN" ]]; then
            echo "    zero mutant exit reason unknown"
            ((fail_g3_zero_unknown++)) || true
        fi
        if [[ $double_class == "SURVIVED" ]]; then
            echo "    double mutant survived"
            ((fail_g3_double_survived++)) || true
        elif [[ $double_class == "CRASH" ]]; then
            echo "    double mutant crashed"
            ((fail_g3_double_crash++)) || true
        elif [[ $double_class == "UNKNOWN" ]]; then
            echo "    double mutant exit reason unknown"
            ((fail_g3_double_unknown++)) || true
        fi
        ((rejected++)) || true
    fi
done

echo ""
echo "SUMMARY: accepted=$accepted rejected=$rejected"
echo "  gate-1 failures (no self-test): $fail_g1"
echo "  gate-2 failures (clean rc!=0): $fail_g2"
echo "  alias-missing failures: $fail_alias"
echo "  alias-name-not-defined failures: $derived_mismatch"
echo "  gate-3 zero survived: $fail_g3_zero_survived"
echo "  gate-3 double survived: $fail_g3_double_survived"
echo "  gate-3 zero crashed: $fail_g3_zero_crash"
echo "  gate-3 double crashed: $fail_g3_double_crash"
echo "  gate-3 zero unknown: $fail_g3_zero_unknown"
echo "  gate-3 double unknown: $fail_g3_double_unknown"

if [[ $rejected -eq 0 ]]; then
    exit 0
else
    exit 1
fi
