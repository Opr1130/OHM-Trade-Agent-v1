from __future__ import annotations

import hmac


def secret_matches(provided: str | None, expected: str | None) -> bool:
    """Compare configured shared secrets without early-exit string equality."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(str(provided), str(expected))
