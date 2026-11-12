"""Normalise and validate a username string."""
import re
from typing import Optional, Tuple


def normalize_username(raw: str, max_length: int = 32) -> Optional[str]:
    """Return a lowercase, trimmed, hyphen-normalised username, or None if invalid.

    Rules: must be 1 to `max_length` characters; letters, digits, underscores and
    single internal hyphens only; no leading/trailing hyphen; collapses multiple
    underscores or hyphens into one.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    cleaned = re.sub(r"[_\-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    if not cleaned or len(cleaned) > max_length:
        return None
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cleaned):
        return None
    return cleaned


def split_username(username: str) -> Tuple[str, ...]:
    """Split a normalised username into its word parts."""
    return tuple(username.split("-"))
