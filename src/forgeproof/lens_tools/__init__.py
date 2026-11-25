"""The lens tools: metamorphic, invariant, twin-consensus and guard.

They ship inside the package (KNOWN-DEBT KD-2, done 2026-11-25), so an installed ForgeProof
runs every lens without a source checkout and without FORGE_GATE_HOME. ``forgeproof.lenses``
imports metamorphic, invariant_gate and twin_consensus from here, and runs guard_lens.py as a
script by its file path (``GUARD_LENS``) in a child process.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD_LENS = HERE / "guard_lens.py"
