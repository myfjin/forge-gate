#!/usr/bin/env bash
# verify_no_duplicate_bodies.sh — Forge Gate duplicate-body detector.
#
# For each source file passed as an argument the gate:
#   1. derives the entry function name (for .js: the target of the
#      `const linearRegressionGD = <NAME>;` harness alias)
#   2. extracts the entry function BODY (the text from the matching '{' to its
#      matching '}')
#   3. normalises WHITESPACE ONLY: every run of whitespace becomes a single space
#   4. groups files by the resulting hash
#
# Exit code:
#   0 — all entry bodies are distinct
#   1 — at least one group of duplicate bodies was found
#
# The function DECLARATION line (including its name) is excluded, so two files
# whose only difference is the entry name will hash to the same body.  Comments
# inside the body are preserved; docstrings above the function are excluded.

set -euo pipefail

usage() {
    echo "Usage: $0 <file> [<file> ...]"
    exit 2
}

if [[ $# -eq 0 ]]; then
    usage
fi

EXTRACTOR=$(mktemp)
trap 'rm -f "$EXTRACTOR"' EXIT

cat > "$EXTRACTOR" <<'PY'
import argparse, hashlib, re, sys
from collections import defaultdict

ALIAS_NAME = "linearRegressionGD"


def derive_entry_name(text, alias_name):
    """For JS files: find the name that the harness alias points to."""
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
    """Return (body_start, body_end) for the first function declaration of name."""
    decl_re = re.compile(r'function\s+' + re.escape(name) + r'\s*\(')
    m = decl_re.search(text)
    if not m:
        return None
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
    return body_start, body_end


def extract_body(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    entry = derive_entry_name(text, ALIAS_NAME)
    if not entry:
        sys.stderr.write(f"ERROR: {path}: missing alias const {ALIAS_NAME} = <NAME>;\n")
        return None

    bounds = find_function_bounds(text, entry)
    if not bounds:
        sys.stderr.write(f"ERROR: {path}: could not locate entry function {entry}\n")
        return None

    body_start, body_end = bounds
    body = text[body_start:body_end]
    # Whitespace-only normalisation: collapse every run of whitespace to one space
    normalised = re.sub(r'\s+', ' ', body).strip()
    return normalised


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()

    groups = defaultdict(list)
    errors = 0
    for path in args.files:
        body = extract_body(path)
        if body is None:
            errors += 1
            continue
        h = hashlib.sha256(body.encode('utf-8')).hexdigest()
        groups[h].append(path)

    if errors:
        sys.exit(2)

    duplicates = {h: paths for h, paths in groups.items() if len(paths) > 1}
    if not duplicates:
        print("OK: 0 duplicate-body groups")
        sys.exit(0)

    print(f"FAIL: {len(duplicates)} duplicate-body group(s)")
    for h, paths in sorted(duplicates.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  group ({len(paths)} files)  sha256={h}")
        for p in paths:
            print(f"    {p}")
    sys.exit(1)


if __name__ == '__main__':
    main()
PY

python3 "$EXTRACTOR" "$@"
