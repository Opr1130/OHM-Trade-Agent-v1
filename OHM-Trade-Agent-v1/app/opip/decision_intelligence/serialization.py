from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from decimal import Decimal
from typing import Any, Mapping


def require_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def canonical_data(value: Any) -> Any:
    if isinstance(value, Enum):
        return canonical_data(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("binary floats are not allowed in canonical identity data")
    if isinstance(value, datetime):
        return require_utc(value, field_name="timestamp").isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in cleaned:
                raise ValueError("mapping keys collide after NFC normalization")
            cleaned[normalized_key] = canonical_data(item)
        return {key: cleaned[key] for key in sorted(cleaned)}
    if isinstance(value, (list, tuple)):
        return [canonical_data(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return canonical_data(value.as_dict())
    raise TypeError(f"unsupported canonical data type: {type(value).__name__}")


def canonicalize_nested(value: Any) -> Any:
    """Return a JSON-safe, UTC-normalized copy of nested DI data."""
    return canonical_data(value)


def canonical_serialize(value: Any) -> str:
    return json.dumps(
        canonical_data(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_serialize(value).encode("utf-8")


def stable_hash(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:32]
    return f"{prefix}:{digest}"
