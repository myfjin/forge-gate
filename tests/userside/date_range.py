"""Compute inclusive date ranges and ISO week buckets."""
from datetime import date, datetime, timedelta
from typing import Iterator, List, Optional, Tuple


def date_range(start: date, end: date, inclusive: bool = True) -> Iterator[date]:
    """Yield dates from `start` to `end`.

    If `inclusive` is True, `end` is included. Otherwise iteration stops before
    `end`.
    """
    if start > end:
        return iter([])
    current = start
    stop = end if inclusive else end - timedelta(days=1)
    while current <= stop:
        yield current
        current += timedelta(days=1)


def week_buckets(start: date, end: date) -> List[Tuple[date, date]]:
    """Return (week_start, week_end) pairs covering [start, end] inclusively.

    Weeks start on Monday and end on Sunday.
    """
    if start > end:
        return []
    monday = start - timedelta(days=start.weekday())
    buckets: List[Tuple[date, date]] = []
    current_start = monday
    while current_start <= end:
        current_end = current_start + timedelta(days=6)
        buckets.append((current_start, min(current_end, end)))
        current_start += timedelta(days=7)
    return buckets


def parse_iso_date(value: str) -> Optional[date]:
    """Parse an ISO-8601 date string (YYYY-MM-DD)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
