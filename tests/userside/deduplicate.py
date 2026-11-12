"""Deduplicate records by a chosen key while preserving order."""
from typing import Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")
K = TypeVar("K")


def deduplicate(
    records: List[T],
    key: Callable[[T], K],
    keep: str = "first",
) -> List[T]:
    """Return `records` with duplicates removed, keeping either the first or last
    occurrence as determined by `keep`.

    `key` extracts the deduplication identifier from each record.
    """
    if keep not in ("first", "last"):
        raise ValueError("keep must be 'first' or 'last'")

    seen: Dict[K, T] = {}
    order: List[K] = []
    for record in records:
        k = key(record)
        if k not in seen:
            order.append(k)
        if keep == "last" or k not in seen:
            seen[k] = record

    return [seen[k] for k in order]


def unique_values(records: List[Dict[str, str]], field: str) -> List[str]:
    """Return the unique values of `field` across `records`, preserving order."""
    result: List[str] = []
    seen = set()
    for record in records:
        value = record.get(field)
        if value is None:
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
