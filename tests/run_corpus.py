#!/usr/bin/env python3
"""Run the 100-test Forge Gate corpus and score correct verdicts.

Usage:
    python tests/run_corpus.py

Reads tests/corpus_manifest.json (format: {"count": N, "items": [...]})
and runs `python3 -m forge_gate.cli verify` on each entry. Prints the number
of matching verdicts and lists any mismatches. Exits 0 when the score is >= 98/100.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "corpus_manifest.json"
REPO_ROOT = MANIFEST.parent.parent


def run_one(file_path: Path, pattern: str, timeout: float = 60.0, lenses: str = "default") -> str:
    """Run forge-gate verify and return the verdict status."""
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT / "tools")
        + os.pathsep + env.get("PYTHONPATH", "")
    )
    cmd = [
        sys.executable,
        "-m",
        "forge_gate.cli",
        "verify",
        str(file_path),
        "--pattern",
        pattern,
        "--timeout",
        str(timeout),
        "--lenses",
        lenses,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "UNSTABLE"

    first_line = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    if ":" in first_line:
        status = first_line.split(":", 1)[0].strip()
        if status in ("PASS", "FAIL", "UNSTABLE", "UNSUPPORTED"):
            return status

    stderr = (proc.stderr or "").strip()
    if "UNSUPPORTED" in stderr:
        return "UNSUPPORTED"
    if "UNSTABLE" in stderr:
        return "UNSTABLE"
    return "FAIL"


def main() -> int:
    # Optional: --lenses all  (measure the corpus under every lens, not just the default two)
    lenses = "default"
    argv = sys.argv[1:]
    if "--lenses" in argv:
        lenses = argv[argv.index("--lenses") + 1]

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = manifest.get("items", manifest) if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        print("Manifest must contain a list of {file, pattern, expect}", file=sys.stderr)
        return 2

    results = []
    mismatch_details = []
    kind_totals = Counter()
    kind_correct = Counter()

    for item in items:
        file_path = REPO_ROOT / item["file"]
        pattern = item["pattern"]
        expect = item["expect"]
        kind = item.get("kind", "unknown")
        actual = run_one(file_path, pattern, lenses=lenses)
        correct = actual == expect
        kind_totals[kind] += 1
        if correct:
            kind_correct[kind] += 1
        results.append({
            "file": item["file"],
            "pattern": pattern,
            "kind": kind,
            "expect": expect,
            "actual": actual,
            "correct": correct,
            "note": item.get("note", ""),
        })
        if not correct:
            mismatch_details.append(
                f"{item['file']}: expected {expect}, got {actual} ({kind}) - {item.get('note', '')}"
            )

    total = len(results)
    correct_total = sum(1 for r in results if r["correct"])

    print(f"\nForge Gate corpus [lenses={lenses}]: {correct_total}/{total} correct verdicts\n")
    print("Breakdown by kind:")
    for kind in sorted(kind_totals.keys()):
        c = kind_correct[kind]
        t = kind_totals[kind]
        print(f"  {kind:25} {c:3}/{t:3}  ({100*c/t:5.1f}%)")
    print()

    if mismatch_details:
        print("Mismatches:")
        for line in mismatch_details:
            print(f"  {line}")
        print()

    results_path = REPO_ROOT / "tests" / "corpus_results.json"
    results_path.write_text(
        json.dumps({"correct": correct_total, "total": total, "results": results}, indent=2),
        encoding="utf-8",
    )
    print(f"Detailed results written to {results_path}")

    return 0 if correct_total >= 98 else 1


if __name__ == "__main__":
    sys.exit(main())
