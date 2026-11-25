"""Metamorphic testing support for ForgeProof.

Provides pattern-category-specific (transform, comparator) relations that verify
behavior instead of exact numeric trajectory.
"""
from .relations import Relation, evaluate_relations, registry
from .runner import run_metamorphic

__all__ = ["Relation", "evaluate_relations", "registry", "run_metamorphic"]
