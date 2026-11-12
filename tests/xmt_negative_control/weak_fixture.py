#!/usr/bin/env python3
"""Deliberately weak fixture for the XMT negative control.

This fixture loads the user's `answer` function and calls it, but does not
assert anything about the result. A gutted oracle body therefore still produces a
PASS, which the XMT harness must detect as a HOLLOW (survived mutant).
"""
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[2]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from forge_gate.fixture_engine import emit, get_fn, load_module


def main():
    if len(sys.argv) < 2:
        print("usage: weak_fixture.py <user_file.py>", file=sys.stderr)
        sys.exit(2)

    user_path = sys.argv[1]
    user_mod = load_module(user_path, "user_impl")
    answer_fn = get_fn(user_mod, "answer")

    # Deliberately weak: we call the function but ignore the result.
    answer_fn(1)

    emit({
        "status": "PASS",
        "pattern": "xmt-negative-control",
        "reason": "weak fixture always passes (result intentionally unchecked)",
    })


if __name__ == "__main__":
    main()
