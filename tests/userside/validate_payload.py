"""Validate a JSON-like payload against a shallow schema."""
from typing import Any, Dict, List, Tuple, Union


def validate_payload(
    payload: Dict[str, Any],
    schema: Dict[str, Tuple[type, bool]],
) -> List[str]:
    """Validate `payload` against `schema`.

    Each schema entry maps a key to (expected_type, required). Returns a list of
    human-readable error messages; an empty list means the payload is valid.
    """
    errors: List[str] = []
    for key, (expected_type, required) in schema.items():
        if key not in payload or payload[key] is None:
            if required:
                errors.append(f"missing required field: {key}")
            continue
        value = payload[key]
        if not isinstance(value, expected_type):
            errors.append(f"field {key} must be {expected_type.__name__}, got {type(value).__name__}")
    for key in payload:
        if key not in schema:
            errors.append(f"unexpected field: {key}")
    return errors


def coerce_int(value: Union[int, float, str]) -> int:
    """Coerce a value to an int if it is a whole number or numeric string."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"expected whole number, got {value}")
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to int")
