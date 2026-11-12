"""Gate fixtures — one per curated pattern.

Each fixture is a standalone script that:
  1. Loads the user's implementation from a file path (argv[1]).
  2. Loads the signed AURA oracle for the same pattern.
  3. Runs both on the same seed-locked fixture.
  4. Emits a `FORGE_GATE_RESULT` JSON line.

Fixtures are executed by `forge_gate.runner.run_file` in a sandboxed subprocess.
"""
from pathlib import Path
from typing import Any, Dict


def emit(result: Dict[str, Any]) -> None:
    """Print the structured gate result line."""
    import json

    print("FORGE_GATE_RESULT " + json.dumps(result, sort_keys=True, default=str))
