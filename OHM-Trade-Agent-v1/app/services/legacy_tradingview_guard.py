from __future__ import annotations

import os
from dataclasses import dataclass

from app.services.secret_auth import secret_matches


@dataclass(frozen=True)
class LegacyTradingViewGuardDecision:
    allowed: bool
    status_code: int
    reason: str
    inject_operator_secret: bool = False


def evaluate_legacy_tradingview_request(
    *,
    app_env: str,
    supplied_legacy_secret: str | None,
) -> LegacyTradingViewGuardDecision:
    """Fence the legacy v1 webhook without changing the v2 bridge.

    Development remains backward compatible unless TRADINGVIEW_V1_ENABLED is
    explicitly set. Production is disabled by default and, when deliberately
    enabled, requires a separate secret that is never the operator secret.
    """
    configured = os.getenv("TRADINGVIEW_V1_ENABLED")
    if configured is None:
        enabled = app_env.strip().casefold() != "production"
    else:
        enabled = configured.strip().casefold() in {"1", "true", "yes", "on"}
    if not enabled:
        return LegacyTradingViewGuardDecision(False, 404, "legacy TradingView webhook disabled")

    if app_env.strip().casefold() != "production":
        return LegacyTradingViewGuardDecision(True, 200, "development compatibility")

    secret = os.getenv("TRADINGVIEW_V1_WEBHOOK_SECRET", "").strip()
    if len(secret) < 32:
        return LegacyTradingViewGuardDecision(False, 503, "legacy TradingView secret not safely configured")
    if not secret_matches(supplied_legacy_secret, secret):
        return LegacyTradingViewGuardDecision(False, 401, "invalid legacy TradingView secret")
    return LegacyTradingViewGuardDecision(True, 200, "legacy TradingView separately authenticated", True)
