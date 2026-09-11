"""Container healthcheck for opip-canonical-writer."""

from __future__ import annotations

import sys

from app.opip.canonical.client import CanonicalWriterClient


def main() -> int:
    try:
        payload = CanonicalWriterClient(timeout=2.0).health()
    except Exception:
        return 1
    if str(payload.get("status")) != "OK":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
