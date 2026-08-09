from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.models.signal import SignalDecision, TradingSignal
from app.services.ai_reviewer import review_signal
from app.services.journal import append_signal
from app.services.risk import build_risk_plan
from app.services.scoring import score_signal
from app.services.telegram_notifier import (
    format_trade_alert,
    send_telegram_message,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/webhooks/tradingview", response_model=SignalDecision)
def tradingview_webhook(
    signal: TradingSignal,
    x_webhook_secret: str | None = Header(default=None),
) -> SignalDecision:
    settings = get_settings()

    if x_webhook_secret != settings.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    deterministic_score, reasons = score_signal(signal)

    risk = build_risk_plan(
        signal,
        settings.account_equity,
        settings.risk_per_trade_pct,
    )

    ai_score = None
    ai_summary = "AI review disabled; deterministic paper-mode decision."

    if settings.ai_enabled:
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="AI_ENABLED is true but OPENAI_API_KEY is missing",
            )

        ai = review_signal(
            signal,
            settings.openai_model,
            settings.openai_api_key,
        )

        ai_score = ai["score"]
        ai_summary = ai["summary"]

    final_score = (
        deterministic_score
        if ai_score is None
        else round(
            (deterministic_score * 0.65)
            + (ai_score * 0.35)
        )
    )

    if not risk.allowed:
        action = "reject"
    elif final_score >= settings.min_alert_score:
        action = "alert"
    elif final_score >= 65:
        action = "watch"
    else:
        action = "reject"

    summary = (
        f"{'; '.join(reasons) or 'No qualifying confirmations'}. "
        f"{ai_summary}"
    )

    decision = SignalDecision(
        symbol=signal.symbol.upper(),
        deterministic_score=deterministic_score,
        ai_score=ai_score,
        final_score=final_score,
        action=action,
        summary=summary,
        risk=risk,
    )

    append_signal(signal, decision)

    if (
        decision.action == "alert"
        and settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        message = format_trade_alert(
            signal,
            decision,
        )

        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            message,
        )

    return decision
