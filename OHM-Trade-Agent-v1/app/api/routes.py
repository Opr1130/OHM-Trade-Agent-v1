from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.signal import SignalDecision, TradingSignal
from app.services.active_trade_registry import close_trade, get_active_trades, get_trade
from app.services.ai_reviewer import review_signal
from app.services.fee_pnl import calculate_fee_aware_pnl
from app.services.journal import append_signal
from app.services.operator_control import set_override_mode, status_payload
from app.services.order_intent_registry import (
    OrderIntent,
    cancel_order_intent,
    list_order_intents,
    mark_order_filled,
    register_order_intent,
    update_limit_order,
)
from app.services.risk import build_risk_plan
from app.services.scoring import score_signal
from app.services.secret_auth import secret_matches
from app.services.telegram_notifier import format_trade_alert, send_telegram_message


router = APIRouter()


class OrderIntentRequest(BaseModel):
    symbol: str
    direction: str = "LONG"
    limit_price: float = Field(gt=0)
    capital: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_1: float | None = Field(default=None, gt=0)
    target_2: float | None = Field(default=None, gt=0)
    margin_leverage: float = Field(default=1.0, gt=0, le=3.0)


class OrderIntentUpdateRequest(BaseModel):
    limit_price: float | None = Field(default=None, gt=0)
    capital: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_1: float | None = Field(default=None, gt=0)
    target_2: float | None = Field(default=None, gt=0)


class FillRequest(BaseModel):
    fill_price: float = Field(gt=0)
    actual_entry_fee: float | None = Field(default=None, ge=0)


class CloseTradeRequest(BaseModel):
    close_price: float = Field(gt=0)
    actual_exit_fee: float | None = Field(default=None, ge=0)
    financing_fee: float | None = Field(default=None, ge=0)
    reason: str = "manual_close"


class PnLRequest(BaseModel):
    current_price: float = Field(gt=0)


def _require_operator_secret(x_webhook_secret: str | None) -> None:
    if not secret_matches(x_webhook_secret, get_settings().webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid operator secret")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/operator/status")
def operator_status(x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    return status_payload()


@router.post("/operator/mode/{mode}")
def operator_mode(mode: str, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    try:
        set_override_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return status_payload()


@router.get("/operator/orders")
def operator_orders(x_webhook_secret: str | None = Header(default=None)) -> list[dict]:
    _require_operator_secret(x_webhook_secret)
    return [item.__dict__ for item in list_order_intents()]


@router.post("/operator/orders")
def operator_create_order(request: OrderIntentRequest, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    try:
        intent = register_order_intent(OrderIntent(**request.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return intent.__dict__


@router.patch("/operator/orders/{trade_id}")
def operator_update_order(trade_id: str, request: OrderIntentUpdateRequest, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    try:
        intent = update_limit_order(trade_id, **request.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Order intent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return intent.__dict__


@router.post("/operator/orders/{trade_id}/cancel")
def operator_cancel_order(trade_id: str, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    try:
        intent = cancel_order_intent(trade_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Order intent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return intent.__dict__


@router.post("/operator/orders/{trade_id}/fill")
def operator_fill_order(trade_id: str, request: FillRequest, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    try:
        trade = mark_order_filled(trade_id, fill_price=request.fill_price, actual_entry_fee=request.actual_entry_fee)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Order intent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return trade.__dict__


@router.get("/operator/trades")
def operator_trades(x_webhook_secret: str | None = Header(default=None)) -> list[dict]:
    _require_operator_secret(x_webhook_secret)
    return [item.__dict__ for item in get_active_trades()]


@router.post("/operator/trades/{symbol}/pnl")
def operator_trade_pnl(symbol: str, request: PnLRequest, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    trade = get_trade(symbol.upper())
    if trade is None or trade.status != "active":
        raise HTTPException(status_code=404, detail="Active trade not found")
    if trade.capital is None:
        raise HTTPException(status_code=409, detail="Trade capital is unknown; fee-aware P/L cannot be computed")
    if trade.direction.upper() == "SHORT" and not trade.financing_fee_known:
        raise HTTPException(status_code=409, detail="Short financing cost is not yet known; exact net P/L is unavailable")
    return calculate_fee_aware_pnl(
        direction=trade.direction,
        entry_price=trade.entry_price,
        current_or_exit_price=request.current_price,
        capital=trade.capital,
        leverage=trade.margin_leverage,
        actual_entry_fee=trade.actual_entry_fee,
        financing_fee=trade.financing_fee,
    ).as_dict()


@router.post("/operator/trades/{symbol}/close")
def operator_close_trade(symbol: str, request: CloseTradeRequest, x_webhook_secret: str | None = Header(default=None)) -> dict:
    _require_operator_secret(x_webhook_secret)
    trade = get_trade(symbol.upper())
    if trade is None or trade.status != "active":
        raise HTTPException(status_code=404, detail="Active trade not found")
    try:
        closed = close_trade(
            symbol.upper(),
            close_price=request.close_price,
            actual_exit_fee=request.actual_exit_fee,
            financing_fee=request.financing_fee,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not closed:
        raise HTTPException(status_code=404, detail="Active trade not found")
    return {"status": "closed", "symbol": symbol.upper(), "reason": request.reason}


@router.post("/webhooks/tradingview", response_model=SignalDecision)
def tradingview_webhook(signal: TradingSignal, x_webhook_secret: str | None = Header(default=None)) -> SignalDecision:
    settings = get_settings()
    if not secret_matches(x_webhook_secret, settings.webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")

    deterministic_score, reasons = score_signal(signal)
    risk = build_risk_plan(signal, settings.account_equity, settings.risk_per_trade_pct)
    ai_score = None
    ai_summary = "AI review disabled; deterministic paper-mode decision."

    if settings.ai_enabled:
        if not settings.openai_api_key:
            raise HTTPException(status_code=500, detail="AI_ENABLED is true but OPENAI_API_KEY is missing")
        ai = review_signal(signal, settings.openai_model, settings.openai_api_key)
        ai_score = ai["score"]
        ai_summary = ai["summary"]

    final_score = deterministic_score if ai_score is None else round((deterministic_score * 0.65) + (ai_score * 0.35))
    if not risk.allowed:
        action = "reject"
    elif final_score >= settings.min_alert_score:
        action = "alert"
    elif final_score >= 65:
        action = "watch"
    else:
        action = "reject"

    summary = f"{'; '.join(reasons) or 'No qualifying confirmations'}. {ai_summary}"
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

    if decision.action == "alert" and settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id:
        message = format_trade_alert(signal, decision)
        send_telegram_message(settings.telegram_bot_token, settings.telegram_chat_id, message)
    return decision