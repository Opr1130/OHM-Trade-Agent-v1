"""Tradevisor V2 webhook ingress (``POST /webhooks/tradevisor/v1``).

Default-disabled and deliberately separate from the Wave 8.2 TradingView v2
route: different path, different schema, different verification header,
different inbox. Nothing here can change the v2 bridge's behaviour.

Trust model (matches what TradingView actually supports)
-------------------------------------------------------
TradingView documents an IP allowlist, ports 80/443 only, no custom headers and
no credentials in the webhook URL for webhook requests. It also documents an SSL
client certificate presented by the webhook server on HTTPS, whose verification
is performed entirely by the recipient (see ``TRADEVISOR_V2.md``). This route
does NOT verify that certificate, so it is treated as unavailable here. Because
the certificate is not checked, authenticity is established by three independent
things, none of which the payload can assert about itself:

1. the immediate peer must be the trusted edge (``OPIP_TRADEVISOR_TRUSTED_PROXY_IPS``);
2. the edge must stamp the internal verification header, proving the request
   actually crossed that proxy and matched the source allowlist;
3. the body must carry this deployment's ingestion token, because every
   TradingView customer shares the same source IPs.

A successful response means "durably received". It never means "authorized".
No market, AI or Telegram call happens on this path.
"""
from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app.api.routes import _read_bounded_body
from app.core.config import get_settings
from app.services.secret_auth import secret_matches
from app.services.tradevisor_inbox import TradevisorInboxFullError, enqueue
from app.services.tradevisor_ingress import (
    TradevisorIngressError,
    admit,
    config_from_settings,
)

router = APIRouter()

#: Kept distinct from the Wave 8.2 header so a leaked value for one bridge
#: cannot satisfy the other.
TRADEVISOR_VERIFICATION_HEADER = "x-ohm-tradevisor-verification"
#: The ingestion credential rides inside the body because TradingView cannot
#: send a header. It is removed before validation and never persisted.
TRADEVISOR_TOKEN_FIELD = "ingest_token"  # nosec B105 - a JSON field name, not a credential value

RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_TRACKED_SOURCES = 512


class _SlidingWindowLimiter:
    """Bounded in-process request limiter.

    Defense in depth only: the trusted edge owns the real rate limit. State is
    per process and intentionally tiny, and a new source can never grow it past
    ``MAX_TRACKED_SOURCES``.
    """

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = float(window_seconds)
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else float(now)
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= MAX_TRACKED_SOURCES:
                # Evict the oldest-inserted source; bounded memory beats an
                # unbounded map driven by remote input.
                self._hits.pop(next(iter(self._hits)), None)
            hits = deque()
            self._hits[key] = hits
        cutoff = timestamp - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(timestamp)
        return True


_limiter: _SlidingWindowLimiter | None = None
_limiter_limit: int | None = None


def _rate_limited(client_host: str, limit: int) -> bool:
    global _limiter, _limiter_limit
    if _limiter is None or _limiter_limit != limit:
        _limiter = _SlidingWindowLimiter(
            limit=limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS
        )
        _limiter_limit = limit
    return not _limiter.allow(client_host)


def reset_rate_limiter_for_tests() -> None:
    global _limiter, _limiter_limit
    _limiter = None
    _limiter_limit = None



@router.post("/webhooks/tradevisor/v1", status_code=status.HTTP_202_ACCEPTED)
async def tradevisor_webhook_v1(request: Request) -> dict:
    settings = get_settings()
    if not settings.opip_tradevisor_enabled:
        # Identical to a nonexistent route: a disabled ingress advertises
        # nothing, not even that it exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    client_host = request.client.host if request.client else None
    trusted_ips = {
        ip.strip()
        for ip in settings.opip_tradevisor_trusted_proxy_ips.split(",")
        if ip.strip()
    }
    if client_host not in trusted_ips:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not secret_matches(
        request.headers.get(TRADEVISOR_VERIFICATION_HEADER),
        settings.opip_tradevisor_internal_verification_value,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if _rate_limited(
        str(client_host), int(settings.opip_tradevisor_max_requests_per_minute)
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests"
        )

    body = await _read_bounded_body(
        request, settings.opip_tradevisor_max_payload_bytes
    )

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    supplied_token = payload.pop(TRADEVISOR_TOKEN_FIELD, None)
    if not secret_matches(supplied_token, settings.opip_tradevisor_webhook_token or ""):
        # Authentication precedes every schema detail, so an unauthenticated
        # sender learns nothing about the contract.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        signal = admit(
            payload,
            config=config_from_settings(settings),
            received_at=datetime.now(timezone.utc),
        )
    except TradevisorIngressError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason
        ) from exc

    try:
        row, is_new = enqueue(signal)
    except TradevisorInboxFullError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingress inbox temporarily unavailable",
        ) from exc

    return {
        "status": "accepted" if is_new else "duplicate",
        "dedup_id": row["dedup_id"],
        "trade_authority_changed": False,
    }
