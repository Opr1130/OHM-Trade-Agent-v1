"""Bybit V5 public linear streaming adapter for BUILD 4.4."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import math
from typing import Any

import orjson
from websockets.asyncio.client import connect

from app.opip.streaming.adapter import (
    NormalizedStreamObservation,
    QueuedRawFrame,
    RawProviderFrame,
)
from app.opip.streaming.contract import (
    SequenceStatus,
    StreamProvider,
    StreamType,
)
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.instruments import initial_symbols, resolve_streaming_instrument
from app.opip.streaming.sequencing import (
    NoSequenceTracker,
    NonDecreasingSequenceTracker,
)


DEFAULT_BYBIT_PUBLIC_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"


def _utc_from_ms(value: Any, *, field: str) -> datetime:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer millisecond timestamp") from exc
    if milliseconds < 0:
        raise ValueError(f"{field} cannot be negative")
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


def _finite_positive(value: Any, *, field: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return numeric


def _sequence_flags(status: SequenceStatus) -> dict[str, bool]:
    return {
        "gap_before": status is SequenceStatus.GAP,
        "out_of_order": status is SequenceStatus.OUT_OF_ORDER,
        "duplicate": status is SequenceStatus.DUPLICATE,
    }


class _BoundedEventDeduper:
    def __init__(self, max_ids: int = 8192) -> None:
        self._max_ids = int(max_ids)
        self._order: deque[str] = deque()
        self._ids: set[str] = set()

    def seen(self, event_id: str) -> bool:
        if event_id in self._ids:
            return True
        self._ids.add(event_id)
        self._order.append(event_id)
        while len(self._order) > self._max_ids:
            self._ids.discard(self._order.popleft())
        return False


class BybitPublicAdapter:
    """Public V5 linear trade/liquidation adapter; no private API surface."""

    provider = StreamProvider.BYBIT

    def __init__(
        self,
        *,
        url: str = DEFAULT_BYBIT_PUBLIC_LINEAR_URL,
        symbols: tuple[str, ...] | None = None,
    ) -> None:
        if not str(url or "").startswith("wss://"):
            raise ValueError("Bybit public stream URL must use wss://")
        self.url = str(url)
        self.symbols = tuple(
            str(item).upper() for item in (symbols or initial_symbols(self.provider))
        )
        if not self.symbols or len(self.symbols) > 5:
            raise ValueError("Bybit adapter requires 1..5 symbols")
        self._ws = None
        self._pending: deque[RawProviderFrame] = deque()
        self._trade_trackers: dict[str, NonDecreasingSequenceTracker] = {}
        self._liq_trackers: dict[str, NoSequenceTracker] = {}
        self._deduper = _BoundedEventDeduper()

    async def connect(self, *, connection_id: str, reconnect_epoch: int) -> None:
        self._pending.clear()
        self._trade_trackers = {
            symbol: NonDecreasingSequenceTracker() for symbol in self.symbols
        }
        self._liq_trackers = {
            symbol: NoSequenceTracker() for symbol in self.symbols
        }
        self._deduper = _BoundedEventDeduper()
        self._ws = await connect(
            self.url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,
            max_queue=16,
            max_size=2_097_152,
        )

    async def subscribe(self) -> None:
        if self._ws is None:
            raise ConnectionError("Bybit adapter is not connected")
        topics = []
        for symbol in self.symbols:
            topics.extend(
                (f"publicTrade.{symbol}", f"allLiquidation.{symbol}")
            )
        await self._ws.send(orjson.dumps({"op": "subscribe", "args": topics}))

    async def receive(self) -> RawProviderFrame:
        if self._ws is None:
            raise ConnectionError("Bybit adapter is not connected")
        while True:
            if self._pending:
                return self._pending.popleft()

            message = await self._ws.recv()
            raw = message.encode("utf-8") if isinstance(message, str) else message
            if not isinstance(raw, bytes):
                raise TypeError("unsupported Bybit websocket frame type")
            payload = orjson.loads(raw)
            if not isinstance(payload, dict):
                continue
            if payload.get("op") in {"ping", "pong", "subscribe"}:
                continue
            if "success" in payload and payload.get("op") == "subscribe":
                continue

            topic = str(payload.get("topic") or "")
            data = payload.get("data")
            if not isinstance(data, list):
                continue

            if topic.startswith("publicTrade."):
                stream_type = StreamType.AGG_TRADE
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("s") or "").upper()
                    event_id = str(item.get("i") or "")
                    if symbol not in self.symbols or not event_id:
                        continue
                    if self._deduper.seen(f"trade:{symbol}:{event_id}"):
                        continue
                    self._pending.append(
                        RawProviderFrame(
                            stream_type=stream_type,
                            provider_symbol=symbol,
                            payload=orjson.dumps(
                                {
                                    "topic": topic,
                                    "ts": payload.get("ts"),
                                    "item": item,
                                }
                            ),
                        )
                    )
            elif topic.startswith("allLiquidation."):
                stream_type = StreamType.LIQUIDATION
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("s") or "").upper()
                    if symbol not in self.symbols:
                        continue
                    event_id = (
                        f"{item.get('T')}:{symbol}:{item.get('S')}:"
                        f"{item.get('v')}:{item.get('p')}"
                    )
                    if self._deduper.seen(f"liq:{event_id}"):
                        continue
                    self._pending.append(
                        RawProviderFrame(
                            stream_type=stream_type,
                            provider_symbol=symbol,
                            payload=orjson.dumps(
                                {
                                    "topic": topic,
                                    "ts": payload.get("ts"),
                                    "item": item,
                                }
                            ),
                        )
                    )
            if self._pending:
                return self._pending.popleft()

    async def heartbeat(self) -> None:
        if self._ws is None:
            raise ConnectionError("Bybit adapter is not connected")
        await self._ws.send(orjson.dumps({"op": "ping"}))

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        self._pending.clear()
        if ws is not None:
            await ws.close()

    def normalize(self, frame: QueuedRawFrame) -> NormalizedStreamObservation:
        payload = orjson.loads(frame.frame.payload)
        if not isinstance(payload, dict):
            raise ValueError("Bybit payload must be an object")
        item = payload.get("item")
        if not isinstance(item, dict):
            raise ValueError("Bybit item is required")
        symbol = frame.frame.provider_symbol.upper()
        identity_status, canonical_id, canonical_name = resolve_streaming_instrument(
            self.provider, symbol
        )

        if frame.frame.stream_type is StreamType.AGG_TRADE:
            price = _finite_positive(item.get("p"), field="p")
            quantity = _finite_positive(item.get("v"), field="v")
            side = str(item.get("S") or "").upper()
            aggressor = side if side in {"BUY", "SELL"} else "UNKNOWN"
            provider_ts = _utc_from_ms(item.get("T"), field="T")
            event_id = str(item.get("i") or "")
            if not event_id:
                raise ValueError("Bybit trade id is required")
            seq = self._trade_trackers[symbol].observe(
                str(item.get("seq")) if item.get("seq") is not None else None,
                reconnect_epoch=frame.reconnect_epoch,
            )
            canonical_payload = {
                "event_id": f"trade:{symbol}:{event_id}",
                "price": price,
                "base_quantity": quantity,
                "notional_usd": price * quantity,
                "aggressor_side": aggressor,
                "canonical_asset_name": canonical_name,
            }
            is_aggregate = False
        elif frame.frame.stream_type is StreamType.LIQUIDATION:
            price = _finite_positive(item.get("p"), field="p")
            quantity = _finite_positive(item.get("v"), field="v")
            position_side = str(item.get("S") or "").upper()
            liquidation_side = {
                "BUY": "LONG_LIQUIDATION",
                "SELL": "SHORT_LIQUIDATION",
            }.get(position_side, "UNKNOWN")
            provider_ts = _utc_from_ms(item.get("T"), field="T")
            seq = self._liq_trackers[symbol].observe(
                None,
                reconnect_epoch=frame.reconnect_epoch,
            )
            canonical_payload = {
                "event_id": (
                    f"liq:{symbol}:{item.get('T')}:{position_side}:"
                    f"{quantity:.12g}:{price:.12g}"
                ),
                "price": price,
                "base_quantity": quantity,
                "notional_usd": price * quantity,
                "liquidation_side": liquidation_side,
                "canonical_asset_name": canonical_name,
            }
            is_aggregate = True
        else:
            raise ValueError("unsupported Bybit stream type")

        envelope = StreamEnvelope(
            provider=self.provider,
            stream_type=frame.frame.stream_type,
            provider_symbol=symbol,
            provider_timestamp_utc=provider_ts,
            ingest_timestamp_utc=frame.ingest_timestamp_utc,
            connection_id=frame.connection_id,
            reconnect_epoch=frame.reconnect_epoch,
            provider_sequence=seq.sequence_value,
            sequence_status=seq.status,
            is_aggregate=is_aggregate,
            identity_status=identity_status,
            canonical_asset_id=canonical_id,
            payload=canonical_payload,
            **_sequence_flags(seq.status),
        )
        return NormalizedStreamObservation(envelope=envelope, sequence=seq)
