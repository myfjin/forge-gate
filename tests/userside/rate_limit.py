"""A simple in-memory token-bucket rate limiter."""
import time
from typing import Dict, Optional


def allow_request(
    buckets: Dict[str, Dict[str, float]],
    key: str,
    capacity: float,
    refill_rate_per_second: float,
    now: Optional[float] = None,
) -> bool:
    """Token-bucket check for `key`. Returns True if the request is allowed.

    `buckets` is mutated in place and tracks tokens and last-update timestamp
    per key. `capacity` is the maximum token balance; `refill_rate_per_second`
    is the rate at which tokens are restored.
    """
    if capacity <= 0 or refill_rate_per_second <= 0:
        raise ValueError("capacity and refill_rate_per_second must be positive")

    if now is None:
        now = time.monotonic()

    bucket = buckets.setdefault(key, {"tokens": float(capacity), "last": now})
    elapsed = now - bucket["last"]
    bucket["tokens"] = min(capacity, bucket["tokens"] + elapsed * refill_rate_per_second)
    bucket["last"] = now

    if bucket["tokens"] >= 1.0:
        bucket["tokens"] -= 1.0
        return True
    return False


def reset_bucket(buckets: Dict[str, Dict[str, float]], key: str) -> None:
    """Remove the bucket for `key`, allowing a fresh start."""
    buckets.pop(key, None)
