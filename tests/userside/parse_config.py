"""Load and flatten a simple INI-style configuration string."""
from typing import Dict, List, Optional


def parse_config(raw: str, section_separator: str = "\n\n") -> Dict[str, Dict[str, str]]:
    """Parse an INI-like string into nested dictionaries.

    Sections are separated by `section_separator`. Lines starting with # or ; are
    ignored. Each non-empty, non-comment line must contain a key/value pair
    separated by `=`.
    """
    result: Dict[str, Dict[str, str]] = {}
    current_section = "default"
    result[current_section] = {}

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            result.setdefault(current_section, {})
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[current_section][key.strip()] = value.strip()

    return result


def required_keys(config: Dict[str, Dict[str, str]], keys: List[str]) -> List[str]:
    """Return the keys from `keys` that are missing from all sections."""
    present = set()
    for section in config.values():
        present.update(section.keys())
    return [key for key in keys if key not in present]
