"""Subprocess runner for gate execution.

Reuses the spirit of `library_verify.run_gate`: execute a Python file with
python3, bounded by timeout, and capture stdout/stderr/exit code.

Additional constraints for user code:
  - fixed random seed (inside the fixture)
  - 60-second timeout
  - no network (best-effort via unshare when available)
  - CPU / memory cgroup limits when running as root (optional)
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class RunResult:
    """Result of executing one file in a subprocess."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    oom_killed: bool = False


_UNSHARE_OK: Optional[bool] = None


def _has_unshare() -> bool:
    global _UNSHARE_OK
    if _UNSHARE_OK is not None:
        return _UNSHARE_OK
    if shutil.which("unshare") is None:
        _UNSHARE_OK = False
        return False
    # Test whether unshare --net works in this environment.
    try:
        c = subprocess.run(
            ["unshare", "--net", "true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _UNSHARE_OK = c.returncode == 0
    except Exception:
        _UNSHARE_OK = False
    return _UNSHARE_OK


def _build_cmd(target: Path, extra_args: Optional[List[str]] = None) -> List[str]:
    from forge_gate.config import python

    runner = python()
    cmd = [runner, "-u", str(target)]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def run_file(
    target: Path,
    *,
    timeout: float = 60.0,
    extra_args: Optional[List[str]] = None,
    no_network: bool = True,
    memory_mb: int = 512,
    cpu_seconds: int = 60,
) -> RunResult:
    """Run a Python file in a fresh subprocess and capture everything.

    Args:
        target: Python file to execute.
        timeout: seconds before SIGKILL.
        extra_args: arguments to pass to the script.
        no_network: if True and `unshare` is available, drop the network namespace.
        memory_mb: soft memory limit in MB (set via RLIMIT_AS when not root).
        cpu_seconds: soft CPU limit in seconds (set via RLIMIT_CPU when not root).
    """
    cmd = _build_cmd(target, extra_args)

    env = os.environ.copy()
    # Reduce determinism-breaking env leakage.
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    def _preexec():
        # Limit resources in the child. These are soft limits; the child can
        # still be killed by the kernel if it exceeds them.
        try:
            max_mem = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (max_mem, max_mem))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except (OSError, ValueError):
            pass

    start = os.times()
    try:
        if no_network and _has_unshare():
            # Drop network namespace for the python process only.
            cmd = ["unshare", "--net", "--"] + cmd

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            preexec_fn=_preexec if sys.platform != "win32" else None,
        )
        end = os.times()
        duration_ms = int((end.elapsed - start.elapsed) * 1000)
        return RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        end = os.times()
        duration_ms = int((end.elapsed - start.elapsed) * 1000)
        return RunResult(
            exit_code=-9,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_ms=duration_ms,
            timed_out=True,
        )
    except FileNotFoundError as exc:
        return RunResult(
            exit_code=127,
            stdout="",
            stderr=f"Runner not found: {exc}",
            duration_ms=0,
            timed_out=False,
        )
