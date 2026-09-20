"""B/C-4D read-only cockpit analytics API.

Exposes the reconciled Paper-v2 ledger and owner overview over the existing
read-only HTTP surface, using the same secret-header authentication and router
conventions as the other analytics endpoints.

Read-only by construction
-------------------------

This module contains **no** write path. It imports only read projections and the
canonical *read* client, and every route is a ``GET``. Read-only authority is
therefore enforced at the data boundary - a caller cannot reach a mutation through
this surface even by sending a crafted body - rather than being merely hidden in a
UI. Nothing here can place, modify or cancel an order, mutate protection, change a
risk setting, or promote a strategy or model.

Every response carries the semantic and trust metadata a consumer needs to
interpret it: the ledger/portfolio projection versions, the four independent trust
dimensions, the valuation posture, and the quote-currency scope. Responses are
explicit about what could *not* be computed rather than substituting a zero.

Filters fail safely: an unsupported filter value produces a 400 rather than being
silently ignored, because silently ignoring a filter would present a
differently-scoped population under the caller's assumed scope.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.opip.cockpit.ledger import read_paper_ledger
from app.opip.cockpit.portfolio import build_overview
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES
from app.services.secret_auth import secret_matches

router = APIRouter()

#: Upper bound on rows returned by the trade list, so a wide query cannot produce an
#: unbounded response. The ledger itself is bounded by committed trade count.
MAX_TRADES = 500


def _require_secret(value: str | None) -> None:
    if not secret_matches(value, get_settings().webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard secret",
        )


def _writer_client():
    """The canonical *read* client, or ``None`` when it cannot be constructed.

    Returns ``None`` rather than raising so the caller can report an unreadable
    store as an explicit trust state instead of a 500 that hides the cause.
    """
    try:
        from app.services.paper_v2_scan_router import _writer_client as resolve

        return resolve()
    except Exception:  # noqa: BLE001 - an unavailable writer is reported, not raised
        return None


def _parse_currency(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    normalized = value.strip().upper()
    if normalized not in QUOTE_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported quote_currency",
        )
    return normalized


def _parse_limit(value: int) -> int:
    if value < 1 or value > MAX_TRADES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"limit must be between 1 and {MAX_TRADES}",
        )
    return value


def _empty_envelope(reason: str) -> dict:
    """The payload for an unreadable store.

    Deliberately not an empty-but-healthy payload: it names the failure so a
    consumer cannot read "unavailable" as "nothing happened".
    """
    from app.opip.cockpit.trust import unavailable

    return {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trust": unavailable(reason).to_dict(),
        "valuation": {"status": "UNKNOWN", "is_known": False},
        "entries": [],
        "details": [reason],
    }


@router.get("/api/cockpit/overview")
def cockpit_overview(
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Owner overview: per-currency portfolio analytics and attention items."""
    _require_secret(x_webhook_secret)
    client = _writer_client()
    if client is None:
        return _empty_envelope("CANONICAL_WRITER_UNAVAILABLE")

    ledger = read_paper_ledger(client)
    overview = build_overview(ledger, now=datetime.now(timezone.utc))
    payload = overview.to_dict()
    payload["as_of"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["timezone"] = "UTC"
    payload["source"] = "canonical_paper_v2_ledger"
    return payload


@router.get("/api/cockpit/trades")
def cockpit_trades(
    quote_currency: str | None = None,
    limit: int = 100,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Recent reconciled Paper-v2 trades, optionally scoped to one currency."""
    _require_secret(x_webhook_secret)
    currency = _parse_currency(quote_currency)
    _parse_limit(limit)

    client = _writer_client()
    if client is None:
        return _empty_envelope("CANONICAL_WRITER_UNAVAILABLE")

    ledger = read_paper_ledger(client)
    rows = [
        row
        for row in ledger.entries
        if currency is None or row.quote_currency == currency
    ]
    rows.sort(
        key=lambda row: (row.last_exit_fill_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": "UTC",
        "trust": ledger.trust.to_dict(),
        "projection_version": ledger.projection_version,
        "filters": {"quote_currency": currency, "limit": limit},
        "population": "canonical Paper-v2 trades",
        "count": len(rows[:limit]),
        "entries": [row.to_dict() for row in rows[:limit]],
        "details": list(ledger.details),
    }


@router.get("/api/cockpit/trades/{paper_trade_id}")
def cockpit_trade_detail(
    paper_trade_id: str,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Trade Detail evidence dossier for one canonical Paper-v2 trade."""
    _require_secret(x_webhook_secret)

    wanted = str(paper_trade_id or "").strip()
    if not wanted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="paper_trade_id is required",
        )

    client = _writer_client()
    if client is None:
        return _empty_envelope("CANONICAL_WRITER_UNAVAILABLE")

    ledger = read_paper_ledger(client)
    match = next(
        (row for row in ledger.entries if row.paper_trade_id == wanted), None
    )
    if match is None:
        # A missing trade in an *unhealthy* ledger is not evidence of absence, so
        # that case is reported distinctly rather than as a plain 404.
        if not ledger.trust.is_healthy:
            return {
                "as_of": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "trust": ledger.trust.to_dict(),
                "found": False,
                "details": list(ledger.details),
            }
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unknown paper trade"
        )

    return {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": "UTC",
        "projection_version": ledger.projection_version,
        "found": True,
        "trade": match.to_dict(),
    }


def route_methods() -> dict[str, set[str]]:
    """The HTTP methods each cockpit route accepts.

    Exposed so a test can assert the surface is read-only without relying on
    framework internals.
    """
    return {
        route.path: set(route.methods or set()) for route in router.routes
    }


__all__ = ["MAX_TRADES", "route_methods", "router"]
