"""F10-B read-only semantic API for the analytics plane.

Increment: ``ATDD-R5-F10-dashboard-parity-implementation``. Mission step ``F10-B`` of the
owner-approved F10 mission: the read-only API and dashboard surface for the first F10
slice - System Pulse, Discovery and Regime, Decision Trace.

What this module is
-------------------

The mounted read surface of the F10-A semantic read service
(``app.opip.cockpit.semantic``, through the F10-B page read model
``app.opip.cockpit.semantic_pages``). The routes it mounts are exactly the routes the
service declares; they are mounted *here* rather than in ``app.main`` so the trading host
never gains the surface, and they are served by the same analytics-plane ASGI app as the
B/C-4 cockpit reads.

Mounted on the analytics plane, not the trading host
---------------------------------------------------

``app.api.cockpit_service`` includes this router for the same reason it includes the B/C-4
cockpit router: the verified canonical replica lives on the analytics plane, and the
trading application's entry point starts production subsystems this surface must never
reach. Widening that plane's reachable path set is an owner-visible change; it is
authorized for this increment alone, recorded in the increment's scope contract, and
pinned by the exposure regression suite rather than performed as a side effect of
declaring a route.

Read-only by construction
-------------------------

Every route is a ``GET``. This module imports no writer, no scheduler and no order path,
holds no trading or Telegram credential, and reads a read-only replica handle; read-only
authority is enforced at the data boundary, not hidden in a UI. Nothing here can place,
modify or cancel an order, mutate protection, change a risk setting, promote a strategy or
release, or activate a paper cutover.

Fail closed on every axis
-------------------------

An unsupported page, panel, metric or fact is a 404; an unsupported or invalid filter is a
400 rather than a silently different scope; an unreadable replica is an explicit
``UNAVAILABLE`` reading rather than an empty page or a zero; and an unset cockpit secret
refuses every read rather than degrading to open access. A value appears only where the
service's own panel disposition allows one.

Authority: none. These routes publish what the projections already hold, and no value they
publish may override a deterministic risk or execution gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

# Reused, not re-implemented: the cockpit module owns the analytics plane's single replica
# read path and its own fail-closed secret gate. A second replica reader here would be a
# second read path for the same replica.
from app.api.cockpit import _load_ledger, _require_secret
from app.opip.cockpit.semantic import (
    InvalidFilterValue,
    SemanticReadService,
    SUPPORTED_FILTERS,
    UnsupportedFact,
    UnsupportedFilter,
    UnsupportedMetric,
    UnsupportedPage,
)
from app.opip.cockpit.semantic_pages import PageReadModel, UnsupportedSlicePage

router = APIRouter()

SEMANTIC_PAGE_FILE = Path(__file__).with_name("cockpit_semantic.html")

#: The routes the semantic service declares and this router mounts. Declared here as the
#: mounted set so a test can assert the mounting is exactly the declaration - no more, no
#: less - without reading framework internals.
MOUNTED_PATHS: tuple[str, ...] = (
    "/api/cockpit/semantic/pages",
    "/api/cockpit/semantic/pages/{page_id}",
    "/api/cockpit/semantic/panels/{panel_id}",
    "/api/cockpit/semantic/metrics/{metric_id}",
    "/api/cockpit/semantic/facts/{fact_id}",
)

#: The page asset route: a static repository asset, served with ``FileResponse`` so no
#: request data can reach the response body.
PAGE_ROUTE = "/cockpit/f10"

#: Query parameters that are not filters: the paging bound and the resume cursor.
_PAGING_PARAMETERS: tuple[str, ...] = ("limit", "cursor")

#: Every accepted query parameter name. Anything else is refused with 400: silently
#: ignoring an unknown filter would answer a differently-scoped question under the
#: caller's assumed scope.
ACCEPTED_PARAMETERS: tuple[str, ...] = tuple(SUPPORTED_FILTERS) + _PAGING_PARAMETERS


class UnsupportedQueryFilter(Exception):
    """A query parameter that is neither a declared filter nor a paging control."""


def _filters(query: Mapping[str, Any]) -> dict[str, Any]:
    """The typed filters a request declares, or a refusal.

    Parameter names are checked against the service's declared filter allowlist before any
    value is parsed, so an undeclared name is refused as a name rather than ignored.
    """
    filters: dict[str, Any] = {}
    for name in query:
        if name not in ACCEPTED_PARAMETERS:
            raise UnsupportedQueryFilter(name)
        value = query[name]
        if name == "limit":
            try:
                filters["limit"] = int(value)
            except (TypeError, ValueError) as exc:
                raise InvalidFilterValue(f"limit is not an integer: {value}") from exc
            continue
        filters[name] = value
    return filters


def _service() -> SemanticReadService:
    """The semantic service over the current replica read, with its unavailability."""

    ledger, _reason = _load_ledger()
    return SemanticReadService(ledger, as_of=datetime.now(timezone.utc))


def _read_model() -> PageReadModel:
    """The page read model over a fresh service for this request."""
    return PageReadModel(_service())


def _refuse(exc: Exception) -> HTTPException:
    """Map one service refusal onto its HTTP status, without reinterpreting it."""
    if isinstance(exc, (UnsupportedQueryFilter, UnsupportedFilter)):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"UNSUPPORTED_FILTER: {exc}"
        )
    if isinstance(exc, InvalidFilterValue):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"INVALID_FILTER_VALUE: {exc}"
        )
    if isinstance(
        exc, (UnsupportedPage, UnsupportedSlicePage, UnsupportedMetric, UnsupportedFact)
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"UNSUPPORTED_SUBJECT: {exc}"
        )
    raise exc


# --------------------------------------------------------------------------- #
# Declared read routes
# --------------------------------------------------------------------------- #


@router.get("/api/cockpit/semantic/pages")
def semantic_pages(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """The F10 page index: every slice page with its state and its panel counts.

    The index carries no panel values, so it is cheap, and it states the parity posture
    with every response: no page is verified, no page is claimed, and the thresholds stay
    an owner decision. It also republishes the read model's declared mounting posture
    instead of a claim of its own, so the surface a reader is looking at is stated by the
    component that mounts it and not inferred by the page.
    """
    _require_secret(x_webhook_secret)
    try:
        _filters(request.query_params)
        model = _read_model()
        entries = model.index()
        envelope = model.page(entries[0].page_id, with_values=False).to_dict()
    except Exception as exc:  # noqa: BLE001 - a refusal is reported, never raised raw
        raise _refuse(exc) from exc
    return {
        "kind": "PAGE_INDEX",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "pages": [entry.to_dict() for entry in entries],
        "count": len(entries),
        "authority": envelope["authority"],
        "parity_status": envelope["parity_status"],
        "parity_verified_pages": envelope["parity_verified_pages"],
        "thresholds": envelope["thresholds"],
        "counts_and_pnl_reconciliation": envelope["counts_and_pnl_reconciliation"],
        "read_model_version": envelope["read_model_version"],
        "watermark": envelope["watermark"],
        "read_surface": envelope["read_surface"],
        "mounted_paths": list(MOUNTED_PATHS),
    }


@router.get("/api/cockpit/semantic/pages/{page_id}")
def semantic_page(
    page_id: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """One page of the slice: panels, withheld sections, annexes and reconciliation."""
    _require_secret(x_webhook_secret)
    try:
        filters = _filters(request.query_params)
        return _read_model().page(page_id, **filters).to_dict()
    except Exception as exc:  # noqa: BLE001 - a refusal is reported, never raised raw
        raise _refuse(exc) from exc


@router.get("/api/cockpit/semantic/panels/{panel_id}")
def semantic_panel(
    panel_id: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """One panel, with its declaration and whatever value the service allows it."""
    _require_secret(x_webhook_secret)
    try:
        filters = _filters(request.query_params)
        reading = _read_model().panel(panel_id, **filters)
    except Exception as exc:  # noqa: BLE001 - a refusal is reported, never raised raw
        raise _refuse(exc) from exc
    return {
        "kind": "PANEL",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "panel": reading.to_dict(),
    }


@router.get("/api/cockpit/semantic/metrics/{metric_id}")
def semantic_metric(
    metric_id: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """One registered metric, answered from the projections or refused."""
    _require_secret(x_webhook_secret)
    try:
        filters = _filters(request.query_params)
        reading = _service().metric(metric_id, **filters)
    except Exception as exc:  # noqa: BLE001 - a refusal is reported, never raised raw
        raise _refuse(exc) from exc
    return {
        "kind": "METRIC",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "reading": reading.to_dict(),
    }


@router.get("/api/cockpit/semantic/facts/{fact_id}")
def semantic_fact(
    fact_id: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """One declared fact: a population, a paged row-fact group or a read-model posture."""
    _require_secret(x_webhook_secret)
    try:
        filters = _filters(request.query_params)
        reading = _service().population(fact_id, **filters)
    except Exception as exc:  # noqa: BLE001 - a refusal is reported, never raised raw
        raise _refuse(exc) from exc
    return {
        "kind": "FACT",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "reading": reading.to_dict(),
    }


@router.get(PAGE_ROUTE)
def semantic_page_asset() -> FileResponse:
    """Serve the F10 dashboard page as a static asset.

    The page shell is public, as the B/C-4 cockpit page is, but every endpoint it calls
    requires the operator secret, so the data stays protected. The page holds no authority
    and performs no arithmetic of its own: it formats and links the values the read model
    supplies, and it renders a withheld fact as withheld.
    """
    return FileResponse(SEMANTIC_PAGE_FILE, media_type="text/html")


def route_methods() -> dict[str, set[str]]:
    """The HTTP methods each semantic route accepts.

    Exposed so a test can assert the surface is read-only without relying on framework
    internals.
    """
    return {route.path: set(route.methods or set()) for route in router.routes}


__all__ = [
    "ACCEPTED_PARAMETERS",
    "MOUNTED_PATHS",
    "PAGE_ROUTE",
    "SEMANTIC_PAGE_FILE",
    "UnsupportedQueryFilter",
    "route_methods",
    "router",
]
