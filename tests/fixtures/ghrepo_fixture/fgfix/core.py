"""Fixture module: one function per case the fact tool must tell apart (GitHub-lane S3)."""
import sys


def clamp(x, lo, hi):
    """Clamp x into [lo, hi]. Well tested, with a doctest.

    >>> clamp(5, 0, 3)
    3
    """
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def untested_sum(a, b):
    return a + b


def flaky_fn(x):
    return x


def label(n):
    return "größer" if n > 0 else "kleiner"


def gated(x):
    if sys.version_info >= (3, 99):
        return x - 1
    return x + 1


def guarded(n):
    if n > 10:
        raise MemoryError("fixture: stands in for an allocation the box refuses")
    return n * 2
