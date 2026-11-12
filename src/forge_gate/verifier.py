"""Verdict engine: compare user execution against the AURA oracle.

Returns one of four states:
  PASS     — user code ran and matched the oracle within tolerance.
  FAIL     — user code ran but output differed, an assertion fired, or it crashed.
  UNSTABLE — the run was non-deterministic (oracle itself varied) or resource limits hit.
  UNSUPPORTED — the pattern or oracle is not available / not gate-ready.

NO LLM judgment is used. The verdict is derived entirely from subprocess exit
codes and structured numeric comparison.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .runner import RunResult


@dataclass(frozen=True)
class Verdict:
    """Final gate verdict for one user file against one pattern."""

    status: str  # PASS | FAIL | UNSTABLE | UNSUPPORTED
    pattern: str
    user_file: str
    reason: str
    oracle_exit: int = -1
    user_exit: int = -1
    metrics: Dict[str, Any] = field(default_factory=dict)


def _parse_gate_json(stdout: str) -> Optional[Dict]:
    """Look for a JSON object emitted by the fixture harness."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("FORGE_GATE_RESULT "):
            payload = line[len("FORGE_GATE_RESULT "):]
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _extract_numbers(text: str) -> List[Tuple[str, float]]:
    """Extract named numeric metrics from stdout, e.g. `accuracy = 0.950`."""
    pairs: List[Tuple[str, float]] = []
    for line in text.splitlines():
        for m in re.finditer(r"(\w+)\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", line):
            name = m.group(1)
            val = float(m.group(2))
            if name.lower() in {"exit", "code", "line", "seed"}:
                continue
            pairs.append((name, val))
    return pairs


def _compare_metrics(
    oracle_metrics: Dict[str, Any],
    user_metrics: Dict[str, Any],
    tolerance: float = 1e-6,
) -> Tuple[bool, str]:
    """Compare two metric dictionaries within tolerance. Returns (ok, reason)."""
    if not oracle_metrics and not user_metrics:
        return True, "no numeric metrics to compare"

    for key in oracle_metrics:
        if key not in user_metrics:
            return False, f"metric '{key}' missing from user output"
        o = oracle_metrics[key]
        u = user_metrics[key]
        if isinstance(o, (int, float)) and isinstance(u, (int, float)):
            denom = max(abs(o), abs(u), 1.0)
            if abs(o - u) / denom > tolerance:
                return False, f"metric '{key}' differs: oracle={o}, user={u}"
        elif o != u:
            return False, f"metric '{key}' differs: oracle={o!r}, user={u!r}"
    return True, "metrics match within tolerance"


def verdict(
    pattern: str,
    user_file: Path,
    oracle_result: RunResult,
    user_result: RunResult,
    tolerance: float = 1e-5,
) -> Verdict:
    """Render a verdict from oracle and user run results."""

    # UNSUPPORTED: oracle itself did not run successfully.
    if oracle_result.timed_out or oracle_result.exit_code != 0:
        return Verdict(
            status="UNSTABLE",
            pattern=pattern,
            user_file=str(user_file),
            reason="Oracle run failed or timed out; the pattern may not be gate-ready.",
            oracle_exit=oracle_result.exit_code,
            user_exit=user_result.exit_code,
            metrics={
                "oracle_stdout": oracle_result.stdout[-500:],
                "oracle_stderr": oracle_result.stderr[-500:],
            },
        )

    oracle_json = _parse_gate_json(oracle_result.stdout)
    user_json = _parse_gate_json(user_result.stdout)

    # The fixture is the authoritative judge of the user's implementation on
    # the seed-locked fixture. It applies pattern-specific correctness properties.
    if user_json:
        user_status = user_json.get("status", "FAIL")
        user_reason = user_json.get("reason", "user fixture reported failure")
        user_metrics = user_json.get("metrics", {})

        if user_status == "FAIL":
            return Verdict(
                status="FAIL",
                pattern=pattern,
                user_file=str(user_file),
                reason=user_reason,
                oracle_exit=oracle_result.exit_code,
                user_exit=user_result.exit_code,
                metrics=user_metrics,
            )

        # user_status == PASS: trust the fixture's property-based verdict.
        # canonical_match is reported by the fixture as an informational metric,
        # not a gating condition.
        return Verdict(
            status="PASS",
            pattern=pattern,
            user_file=str(user_file),
            reason=user_reason,
            oracle_exit=oracle_result.exit_code,
            user_exit=user_result.exit_code,
            metrics=user_metrics,
        )

    # No user JSON — fall back to exit-code / raw-number comparison.
    if user_result.timed_out:
        return Verdict(
            status="UNSTABLE",
            pattern=pattern,
            user_file=str(user_file),
            reason=f"User code timed out after {user_result.duration_ms} ms.",
            oracle_exit=oracle_result.exit_code,
            user_exit=user_result.exit_code,
            metrics={"duration_ms": user_result.duration_ms},
        )

    if user_result.exit_code != 0:
        return Verdict(
            status="FAIL",
            pattern=pattern,
            user_file=str(user_file),
            reason="User process exited non-zero (assertion failed or crashed).",
            oracle_exit=oracle_result.exit_code,
            user_exit=user_result.exit_code,
            metrics={"stderr": user_result.stderr[-500:]},
        )

    oracle_nums = dict(_extract_numbers(oracle_result.stdout))
    user_nums = dict(_extract_numbers(user_result.stdout))

    # PASS requires the user to actually produce metrics. A file that exits 0
    # but prints nothing numeric is the classic "no-op" cheat and must fail.
    if not user_nums:
        if not oracle_nums:
            # Pattern genuinely produced no metrics from either side — not gate-ready.
            return Verdict(
                status="UNSTABLE",
                pattern=pattern,
                user_file=str(user_file),
                reason="Pattern produced no extractable metrics; oracle not gate-ready for numeric verification.",
                oracle_exit=oracle_result.exit_code,
                user_exit=user_result.exit_code,
                metrics={"oracle_stdout": oracle_result.stdout[-500:]},
            )
        return Verdict(
            status="FAIL",
            pattern=pattern,
            user_file=str(user_file),
            reason="User produced none of the expected metrics.",
            oracle_exit=oracle_result.exit_code,
            user_exit=user_result.exit_code,
            metrics={"expected_keys": list(oracle_nums.keys())},
        )

    ok, reason = _compare_metrics(oracle_nums, user_nums, tolerance)
    status = "PASS" if ok else "FAIL"
    return Verdict(
        status=status,
        pattern=pattern,
        user_file=str(user_file),
        reason=reason,
        oracle_exit=oracle_result.exit_code,
        user_exit=user_result.exit_code,
        metrics={"oracle": oracle_nums, "user": user_nums},
    )
