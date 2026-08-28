from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.opip.decision.explanations import build_zero_trade_explanation
from app.services.dashboard_read_model import build_dashboard_read_model
from app.services.operations_analytics import build_operations_summary
from app.services.secret_auth import secret_matches


router = APIRouter()
DASHBOARD_FILE = Path(__file__).with_name("dashboard.html")


def _require_secret(value: str | None) -> None:
    if not secret_matches(value, get_settings().webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid dashboard secret")


@router.get("/api/analytics/summary")
def analytics_summary(
    scope: str = "today",
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    _require_secret(x_webhook_secret)
    try:
        return build_operations_summary(scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/analytics/intelligence")
def intelligence_summary(
    scope: str = "all",
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    _require_secret(x_webhook_secret)
    try:
        return build_dashboard_read_model(scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/opip/zero-trade-explanation")
def opip_zero_trade_explanation(
    include_candidates: bool = False,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Return the O'Pip read model explaining the most recent scan's outcome.

    Read-only by construction: it opens the append-only qualification streams
    and returns their contents. There is no write path from this endpoint into
    ranking, alerting, paper admission, or any trading state.
    """
    _require_secret(x_webhook_secret)
    return build_zero_trade_explanation(include_candidates=include_candidates)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_FILE.read_text(encoding="utf-8")
