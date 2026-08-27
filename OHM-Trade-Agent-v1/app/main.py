from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from app.api.dashboard import router as dashboard_router
from app.api.routes import router
from app.core.config import get_settings
from app.services.legacy_tradingview_guard import evaluate_legacy_tradingview_request
from app.services.freqtrade_signal_bridge import ensure_bridge_files, mirror_control
from app.services.paper_trade_control import get_paper_trade_control
from app.services.telegram_callback_listener import (
    start_telegram_callback_listener,
    stop_telegram_callback_listener,
)

app = FastAPI(
    title="OHM Trade Agent v1",
    version="0.1.0",
    description="Alert-only trade signal scoring and risk-validation service.",
)


@app.middleware("http")
async def fence_legacy_tradingview(request: Request, call_next):
    if request.url.path == "/webhooks/tradingview":
        settings = get_settings()
        decision = evaluate_legacy_tradingview_request(
            app_env=settings.app_env,
            supplied_legacy_secret=request.headers.get("x-legacy-tradingview-secret"),
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=decision.status_code,
                content={"detail": decision.reason},
            )
        if decision.inject_operator_secret:
            # The legacy third-party secret is checked above. The old route's
            # operator-secret dependency is then satisfied only inside the app,
            # so an external legacy sender never receives the operator secret.
            headers = MutableHeaders(scope=request.scope)
            headers["x-webhook-secret"] = settings.webhook_secret

    return await call_next(request)


app.include_router(router)
app.include_router(dashboard_router)


@app.on_event("startup")
def startup_event() -> None:
    try:
        ensure_bridge_files()
        paper = get_paper_trade_control()
        if paper.updated_at:
            from datetime import datetime
            updated_at = datetime.fromisoformat(paper.updated_at.replace("Z", "+00:00"))
        else:
            from datetime import datetime, timezone
            updated_at = datetime.now(timezone.utc)
        mirror_control(
            enabled=paper.enabled,
            updated_at=updated_at,
            updated_by=paper.updated_by,
        )
    except Exception:
        # The bridge is paper-only and must never prevent the advisory app
        # from starting. Freqtrade itself starts fail-safe OFF.
        pass
    start_telegram_callback_listener()


@app.on_event("shutdown")
def shutdown_event() -> None:
    stop_telegram_callback_listener()
