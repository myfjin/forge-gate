"""Deliberately trivial oracle for the XMT negative control.

The XMT harness must flag this fixture as HOLLOW: the weak_fixture below calls
`answer(...)` but never checks the return value, so a mutant whose body is
deleted still "passes". This proves the harness actually detects SURVIVED mutants.
"""


def answer(x: int) -> int:
    return 42
