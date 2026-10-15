"""B/C-4 read-only Cockpit service for the analytics plane.

This is the ASGI entry point the analytics-plane container runs. It exists so the
Cockpit analytical API is served **where the verified canonical replica lives**,
rather than on the trading host.

Why a dedicated app instead of ``app.main:app``
----------------------------------------------

The trading application's entry point starts production subsystems (schedulers,
scan orchestration, alerting, Telegram). Running that on the analytics plane would
be both unnecessary and a boundary violation. This app mounts *only* the
read-only Cockpit router, so the analytics plane physically cannot start trading
work even by misconfiguration.

It also means the process holds no exchange credentials, no Telegram authority and
no order capability: its single dependency is a read-only SQLite replica handle.

Authority: none. It serves GET requests that read a derived, non-authoritative
replica. Nothing here writes, promotes, or trades.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.cockpit import router as cockpit_router
from app.api.cockpit_semantic import router as cockpit_semantic_router

app = FastAPI(
    title="O'Pip Cockpit (read-only)",
    description=(
        "Read-only derived analytics over the verified canonical replica. "
        "No trading, exchange, promotion or write authority."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# The Cockpit router only. Deliberately not the trading router: the analytics plane
# must not be able to reach order, alert, paper-control or scheduler surfaces.
app.include_router(cockpit_router)

# The F10-B semantic read surface (System Pulse, Discovery and Regime, Decision Trace).
# Widening the mounted path set is an owner-visible decision: it is authorized for the
# `ATDD-R5-F10-dashboard-parity-implementation` increment alone, recorded in that
# increment's scope contract, and pinned by the exposure regression suite. Every route it
# adds is a GET over the same read-only replica and the same secret gate as the B/C-4
# reads; no write, trading, Telegram or promotion path is reachable through it.
app.include_router(cockpit_semantic_router)


__all__ = ["app"]
