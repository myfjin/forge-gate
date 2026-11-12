"""Retry a callable with exponential backoff and jitter."""
import random
import time
from typing import Callable, Optional, Tuple, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 8.0,
    retryable_exceptions: Tuple[type, ...] = (Exception,),
    jitter: bool = True,
) -> Optional[T]:
    """Call `fn` repeatedly until it succeeds or `max_attempts` is exhausted.

    Delay between attempts is `min(base_delay * 2**attempt, max_delay)`, with
    optional random jitter up to 25% of the delay. Returns the first successful
    result, or None if all attempts failed.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except retryable_exceptions as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            delay = min(base_delay_seconds * (2 ** attempt), max_delay_seconds)
            if jitter:
                delay *= random.uniform(0.75, 1.25)
            time.sleep(delay)
    return None


def is_transient_status(status_code: int) -> bool:
    """Return True for HTTP status codes that are safe to retry."""
    return status_code in (408, 429, 500, 502, 503, 504)
