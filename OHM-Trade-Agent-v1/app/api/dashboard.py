from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.opip.decision.explanations import build_zero_trade_explanation
from app.services.dashboard_read_model import build_dashboard_read_model
from app.services.operations_analytics import build_operations_summary
from app.services.secret_auth import secret_matches
from app.services.technical_scoring_comparison import read_recent_comparisons
from app.services.strategy_admin_drafts import (
    StrategyDraftConflict, read_policy_draft, save_policy_draft,
    activate_policy_draft, rollback_active_policy, read_active_policy_status,
)


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


@router.get("/api/opip/scoring-comparison")
def scoring_comparison(x_webhook_secret: str | None = Header(default=None)) -> dict:
    """Read-only V1/V2 paired-scoring evidence. Never activates a strategy."""
    _require_secret(x_webhook_secret)
    return read_recent_comparisons()


@router.get("/dashboard/scoring", response_class=HTMLResponse)
def scoring_comparison_page() -> str:
    """Static read-only page; comparison data requires dashboard auth."""
    return Path(__file__).with_name("scoring_comparison.html").read_text(encoding="utf-8")


def _require_strategy_admin(operator_secret: str | None, admin_secret: str | None) -> None:
    # Deliberately unavailable until a separate >=32-character admin secret
    # has been provisioned outside the dashboard/browser and differs from
    # the operator secret. Both credentials are required for draft writes.
    settings = get_settings()
    configured = (settings.strategy_admin_secret or "").strip()
    if len(configured) < 32 or secret_matches(configured, settings.webhook_secret):
        raise HTTPException(status_code=404, detail="Strategy administration unavailable")
    _require_secret(operator_secret)
    if not secret_matches(admin_secret, configured):
        raise HTTPException(status_code=401, detail="Invalid strategy admin credentials")


@router.get("/api/opip/strategy-admin/draft")
def strategy_admin_read_draft(
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict:
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    try:
        return read_policy_draft()
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Strategy draft unavailable")


@router.put("/api/opip/strategy-admin/draft")
def strategy_admin_save_draft(
    proposed: dict = Body(...),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
    x_expected_profile_sha256: str | None = Header(default=None),
) -> dict:
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    try:
        return save_policy_draft(proposed, expected_digest=x_expected_profile_sha256 or "")
    except StrategyDraftConflict:
        raise HTTPException(status_code=409, detail="Draft changed; refresh before saving")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError:
        raise HTTPException(status_code=503, detail="Unable to save strategy draft")


@router.get("/dashboard/strategy-admin", response_class=HTMLResponse)
def strategy_admin_page() -> str:
    """Page contains no secrets and cannot write without dual headers."""
    return Path(__file__).with_name("strategy_admin.html").read_text(encoding="utf-8")


@router.get("/api/opip/strategy-admin/active")
def strategy_admin_current_active(
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict:
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    try:
        return read_active_policy_status()
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Active strategy policy unavailable")


def _require_strategy_activation() -> None:
    settings = get_settings()
    if not (bool(getattr(settings, "strategy_admin_activation_enabled", False))
            and bool(getattr(settings, "opip_advisory_profile_enabled", False))):
        raise HTTPException(status_code=404, detail="Strategy activation not enabled")


@router.post("/api/opip/strategy-admin/activate")
def strategy_admin_activate(
    expected_digest: str = Body(..., embed=True),
    acknowledgment: str = Body(..., embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict:
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    _require_strategy_activation()
    if acknowledgment != "ACTIVATE_ADVISORY_ONLY":
        raise HTTPException(status_code=422, detail="Explicit advisory-only acknowledgment required")
    try:
        return activate_policy_draft(expected_digest=expected_digest)
    except StrategyDraftConflict:
        raise HTTPException(status_code=409, detail="Draft version changed; refresh")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError:
        raise HTTPException(status_code=503, detail="Strategy activation unavailable")


@router.post("/api/opip/strategy-admin/rollback")
def strategy_admin_rollback(
    expected_digest: str = Body(..., embed=True),
    acknowledgment: str = Body(..., embed=True),
    x_webhook_secret: str | None = Header(default=None),
    x_strategy_admin_secret: str | None = Header(default=None),
) -> dict:
    _require_strategy_admin(x_webhook_secret, x_strategy_admin_secret)
    _require_strategy_activation()
    if acknowledgment != "ROLLBACK_ADVISORY_ONLY":
        raise HTTPException(status_code=422, detail="Explicit advisory rollback acknowledgment required")
    try:
        return rollback_active_policy(expected_digest=expected_digest)
    except StrategyDraftConflict:
        raise HTTPException(status_code=409, detail="Active policy changed; refresh")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError:
        raise HTTPException(status_code=503, detail="Strategy rollback unavailable")
