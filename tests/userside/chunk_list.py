"""Split a list into evenly sized chunks with configurable padding."""
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")


def chunk_list(
    items: List[T],
    chunk_size: int,
    pad: bool = False,
    pad_value: Optional[T] = None,
) -> List[List[T]]:
    """Return `items` divided into chunks of length `chunk_size`.

    The final chunk is shorter unless `pad` is True, in which case `pad_value`
    is appended until the final chunk reaches `chunk_size`.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    chunks: List[List[T]] = []
    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        if pad and len(chunk) < chunk_size and pad_value is not None:
            chunk = chunk + [pad_value] * (chunk_size - len(chunk))
        chunks.append(chunk)
    return chunks


def chunk_map(
    items: List[T],
    chunk_size: int,
    fn: Callable[[List[T]], List[T]],
) -> List[T]:
    """Apply `fn` to each chunk of `items` and concatenate the results."""
    result: List[T] = []
    for chunk in chunk_list(items, chunk_size):
        result.extend(fn(chunk))
    return result
