import json

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.models.signal import SignalDecision, TradingSignal, TradingViewSignalV2
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
from app.services.telegram_notifier import format_trade_alert
from app.services.telegram_delivery import send_tracked_telegram
from app.services.tradingview_inbox import TradingViewInboxFullError, enqueue, record_rejection


router = APIRouter()

# TradingView cannot be made to send a custom HTTP header, so the v2 bridge is
# authenticated at the network layer instead: a reverse proxy (see
# deploy/nginx/tradingview-webhook.conf.example) allowlists TradingView's
# published webhook source IPs and is the only thing permitted to reach this
# process directly, then stamps every forwarded request with this header so
# the app can also verify the request actually came through that proxy and
# not from some other process on the same host/network.
TRADINGVIEW_VERIFICATION_HEADER = "x-ohm-internal-verification"


def _classify_tradingview_policy_rejection(message: str) -> str:
    if "resubmitted faster" in message:
        return ""  # already counted inside enqueue(); do not double count
    if "schema_version" in message or "ambiguous" in message:
        return "schema"
    if "script_version" in message:
        return "version"
    if "stale" in message:
        return "stale"
    if "future-dated" in message:
        return "future"
    return "policy"


async def _read_bounded_body(request: Request, maximum_bytes: int) -> bytes:
    """Read a request incrementally so the application never buffers an
    arbitrarily large webhook body before enforcing its configured limit.
    The reverse proxy also applies its own client_max_body_size boundary.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Payload too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
        send_tracked_telegram(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            message=message,
            identity=f"TRADING_SIGNAL:{decision.symbol}",
            alert_family="TRADING_SIGNAL",
            event_type="ALERT",
            fingerprint=f"{decision.final_score}:{signal.price}:{signal.target_price}:{signal.stop_price}",
            symbol=decision.symbol,
        )
    return decision


@router.post("/webhooks/tradingview/v2", status_code=status.HTTP_202_ACCEPTED)
async def tradingview_webhook_v2(request: Request) -> dict:
    """Wave 8.2 TradingView Intelligence Bridge inbound endpoint.

    TradingView is candidate evidence only. This endpoint has no path to
    place, confirm, or override an order, a fill, or portfolio/risk state —
    it only ever writes a QUEUED row to the durable inbox
    (app/services/tradingview_inbox.py) that a later native cycle revalidates
    from scratch (price divergence, operator mode, target/economic gates)
    before it can influence anything.
    """
    settings = get_settings()
    if not settings.tradingview_v2_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    client_host = request.client.host if request.client else None
    trusted_ips = {ip.strip() for ip in settings.tradingview_trusted_proxy_ips.split(",") if ip.strip()}
    if client_host not in trusted_ips:
        record_rejection("network_origin")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not secret_matches(request.headers.get(TRADINGVIEW_VERIFICATION_HEADER), settings.tradingview_internal_verification_value):
        record_rejection("authentication")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        body = await _read_bounded_body(request, settings.tradingview_max_payload_bytes)
    except HTTPException:
        record_rejection("payload_too_large")
        raise

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        record_rejection("authentication")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc

    # A TradingView source-IP allowlist authenticates TradingView as a service,
    # not this user's TradingView account. Require a separate per-deployment
    # bearer value before returning any schema/policy detail, then remove it so
    # credentials can never enter the Pydantic model or durable inbox.
    if not isinstance(payload, dict):
        record_rejection("authentication")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    supplied_token = payload.pop("verification_token", None)
    if not secret_matches(supplied_token, settings.tradingview_webhook_token or ""):
        record_rejection("authentication")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        signal = TradingViewSignalV2.model_validate(payload)
    except ValidationError as exc:
        record_rejection("schema")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc

    try:
        row, is_new = enqueue(signal)
    except TradingViewInboxFullError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook inbox temporarily unavailable",
        ) from exc
    except ValueError as exc:
        rejection_kind = _classify_tradingview_policy_rejection(str(exc))
        if rejection_kind:
            record_rejection(rejection_kind)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {
        "status": "accepted" if is_new else "duplicate",
        "deduplication_key": row["deduplication_key"],
    }
