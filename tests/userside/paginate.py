"""Paginate a sequence into fixed-size chunks with optional overlap."""
from typing import Generator, List, TypeVar

T = TypeVar("T")


def paginate(
    items: List[T],
    page_size: int,
    overlap: int = 0,
) -> Generator[List[T], None, None]:
    """Yield successive pages from `items`.

    Each page contains up to `page_size` items. If `overlap` is greater than 0,
    the last `overlap` items of a page are repeated as the first items of the
    next page.
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if overlap < 0 or overlap >= page_size:
        raise ValueError("overlap must be in [0, page_size)")

    step = page_size - overlap
    index = 0
    while index < len(items):
        yield items[index : index + page_size]
        index += step


def count_pages(total_items: int, page_size: int) -> int:
    """Return the number of pages needed to display `total_items` at `page_size`."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return (total_items + page_size - 1) // page_size
