"""Fail-closed screening for anything permitted to leave the process boundary.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Model providers are external systems. Everything the committee sends them is
constructed by screening an explicit allowlist, and any material that looks
like a credential class - by key name or by value form - raises
:class:`OutboundPolicyError` rather than being sent.

The key-name fragment set deliberately mirrors
``app.services.system_incidents._FORBIDDEN_METADATA_KEY_FRAGMENTS`` so the
repository keeps one vocabulary for "this must never be persisted or exported".
"""

from __future__ import annotations

import re
from typing import Any, Mapping

#: Key-name fragments that mark a field as prohibited outbound material.
PROHIBITED_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "authorization",
    "auth_header",
    "signature",
    "nonce",
    "credential",
)

#: Whole-field names that indicate an environment or secret dump rather than
#: evidence, even when no fragment above matches.
PROHIBITED_FIELD_NAMES = (
    "env",
    "environ",
    "environment",
    "env_vars",
    "envvars",
    "os_environ",
    "dotenv",
    "secrets",
    "ssh_key",
    "pem",
)

#: Value forms that identify a credential even under an innocuous key name.
#: These are intentionally vendor-specific: a broad high-entropy rule would
#: reject the committee's own content hashes.
PROHIBITED_VALUE_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI-compatible
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub token
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack token
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),  # PEM material
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),  # Authorization header
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
)

#: Prohibited material never travels, even inside an otherwise allowed field.
MAX_MODEL_BOUND_STRING_LENGTH = 8_192
MAX_MODEL_BOUND_DEPTH = 6
MAX_MODEL_BOUND_ITEMS = 512


class OutboundPolicyError(ValueError):
    """Raised when material that may not leave the process was about to be sent."""


def _is_prohibited_key(key: str) -> bool:
    lowered = key.strip().lower()
    if not lowered:
        return True
    if lowered in PROHIBITED_FIELD_NAMES:
        return True
    return any(fragment in lowered for fragment in PROHIBITED_KEY_FRAGMENTS)


def assert_no_prohibited_value(value: str, *, path: str) -> None:
    """Raise if a string carries a recognizable credential form."""
    if "\x00" in value:
        raise OutboundPolicyError(
            f"prohibited outbound material at {path}: NUL byte in value"
        )
    for pattern in PROHIBITED_VALUE_PATTERNS:
        if pattern.search(value):
            raise OutboundPolicyError(
                f"prohibited outbound material at {path}: value matches "
                f"{pattern.pattern!r}"
            )


def screen_model_bound_view(view: Mapping[str, Any]) -> dict[str, Any]:
    """Return a screened, JSON-safe copy of ``view`` or raise.

    The function is fail-closed: an unknown key form, a prohibited key name, a
    credential-formed value, a non-finite number, or excessive depth, size, or
    item count all raise rather than being dropped silently.
    """
    if not isinstance(view, Mapping):
        raise OutboundPolicyError("model-bound view must be a mapping")
    return _screen_mapping(view, path="view", depth=0)


def _screen_mapping(
    value: Mapping[str, Any], *, path: str, depth: int
) -> dict[str, Any]:
    if depth > MAX_MODEL_BOUND_DEPTH:
        raise OutboundPolicyError(f"prohibited outbound material at {path}: too deep")
    if len(value) > MAX_MODEL_BOUND_ITEMS:
        raise OutboundPolicyError(
            f"prohibited outbound material at {path}: too many fields"
        )
    screened: dict[str, Any] = {}
    for key in value:
        if not isinstance(key, str):
            raise OutboundPolicyError(
                f"prohibited outbound material at {path}: non-string key"
            )
    for key in sorted(value):
        if _is_prohibited_key(key):
            raise OutboundPolicyError(
                f"prohibited outbound material at {path}.{key}: key is not allowed"
            )
        screened[key] = _screen_value(
            value[key], path=f"{path}.{key}", depth=depth + 1
        )
    return screened


def _screen_value(value: Any, *, path: str, depth: int) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        assert_no_prohibited_value(value, path=path)
        if len(value) > MAX_MODEL_BOUND_STRING_LENGTH:
            raise OutboundPolicyError(
                f"prohibited outbound material at {path}: string exceeds "
                f"{MAX_MODEL_BOUND_STRING_LENGTH} characters"
            )
        return value
    if isinstance(value, Mapping):
        return _screen_mapping(value, path=path, depth=depth)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_MODEL_BOUND_ITEMS:
            raise OutboundPolicyError(
                f"prohibited outbound material at {path}: too many items"
            )
        return [
            _screen_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    # Floats are rejected because canonical committee identity forbids binary
    # floats; money and metrics travel as integers or decimal strings.
    raise OutboundPolicyError(
        f"prohibited outbound material at {path}: unsupported type "
        f"{type(value).__name__}"
    )


__all__ = [
    "MAX_MODEL_BOUND_DEPTH",
    "MAX_MODEL_BOUND_ITEMS",
    "MAX_MODEL_BOUND_STRING_LENGTH",
    "OutboundPolicyError",
    "PROHIBITED_FIELD_NAMES",
    "PROHIBITED_KEY_FRAGMENTS",
    "PROHIBITED_VALUE_PATTERNS",
    "assert_no_prohibited_value",
    "screen_model_bound_view",
]
