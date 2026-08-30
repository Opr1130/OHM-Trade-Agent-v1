"""Deterministic JSON serialization for O'Pip Decision V2 evidence."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Any, Mapping


def canonical_data(value: Any) -> Any:
    """Return a deterministic JSON-safe representation or fail closed."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are prohibited in canonical evidence")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical evidence timestamps must be timezone-aware")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, Enum):
        return canonical_data(value.value)
    if is_dataclass(value):
        return {
            field.name: canonical_data(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical evidence mapping keys must be strings")
            normalized[key] = canonical_data(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (list, tuple)):
        return [canonical_data(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("unordered sets are prohibited in canonical evidence")
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return canonical_data(as_dict())
    raise TypeError(f"unsupported canonical evidence type: {type(value).__name__}")


def canonical_serialize(value: Any) -> str:
    """Return compact stable JSON suitable for identity hashing."""
    return json.dumps(
        canonical_data(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
