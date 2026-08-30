"""Binance USDⓈ-M public streaming adapter for BUILD 4.3.

Market data only. The adapter connects to the public combined-stream endpoint,
subscribes to aggregate trades and per-symbol liquidation snapshots, and
normalizes them into the provider-neutral BUILD 4.1/4.2 contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any
from uuid import uuid4

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
from app.opip.streaming.instruments import (
    initial_symbols,
    resolve_streaming_instrument,
)
from app.opip.streaming.sequencing import (
    NoSequenceTracker,
    StrictIncrementingSequenceTracker,
)


DEFAULT_BINANCE_PUBLIC_STREAM_URL = "wss://fstream.binance.com/public/stream"


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


class BinancePublicAdapter:
    """Public USDⓈ-M aggregate-trade/liquidation adapter.

    No credentials or trading endpoints are accepted by this class.
    """

    provider = StreamProvider.BINANCE

    def __init__(
        self,
        *,
        url: str = DEFAULT_BINANCE_PUBLIC_STREAM_URL,
        symbols: tuple[str, ...] | None = None,
    ) -> None:
        if not str(url or "").startswith("wss://"):
            raise ValueError("Binance public stream URL must use wss://")
        self.url = str(url)
        self.symbols = tuple(
            str(item).upper() for item in (symbols or initial_symbols(self.provider))
        )
        if not self.symbols or len(self.symbols) > 5:
            raise ValueError("Binance adapter requires 1..5 symbols")
        self._ws = None
        self._connection_id: str | None = None
        self._reconnect_epoch = -1
        self._trade_trackers: dict[str, StrictIncrementingSequenceTracker] = {
            symbol: StrictIncrementingSequenceTracker() for symbol in self.symbols
        }
        self._liquidation_trackers: dict[str, NoSequenceTracker] = {
            symbol: NoSequenceTracker() for symbol in self.symbols
        }

    async def connect(self, *, connection_id: str, reconnect_epoch: int) -> None:
        self._connection_id = str(connection_id)
        self._reconnect_epoch = int(reconnect_epoch)
        self._ws = await connect(
            self.url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            max_queue=16,
            max_size=1_048_576,
        )

    async def subscribe(self) -> None:
        if self._ws is None:
            raise ConnectionError("Binance adapter is not connected")
        params = []
        for symbol in self.symbols:
            lower = symbol.lower()
            params.extend((f"{lower}@aggTrade", f"{lower}@forceOrder"))
        await self._ws.send(
            orjson.dumps(
                {
                    "method": "SUBSCRIBE",
                    "params": params,
                    "id": uuid4().hex,
                }
            )
        )

    async def receive(self) -> RawProviderFrame:
        if self._ws is None:
            raise ConnectionError("Binance adapter is not connected")
        while True:
            message = await self._ws.recv()
            if isinstance(message, str):
                raw = message.encode("utf-8")
            elif isinstance(message, bytes):
                raw = message
            else:
                raise TypeError("unsupported Binance websocket frame type")
            payload = orjson.loads(raw)
            if not isinstance(payload, dict):
                continue
            if "result" in payload and "id" in payload:
                continue
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            if not isinstance(data, dict):
                continue
            if data.get("st") not in (None, 1):
                continue
            event = str(data.get("e") or "")
            symbol = str(
                data.get("s")
                or (
                    data.get("o", {}).get("s")
                    if isinstance(data.get("o"), dict)
                    else ""
                )
                or ""
            ).upper()
            if symbol not in self.symbols:
                continue
            if event == "aggTrade":
                stream_type = StreamType.AGG_TRADE
            elif event == "forceOrder":
                stream_type = StreamType.LIQUIDATION
            else:
                continue
            return RawProviderFrame(
                stream_type=stream_type,
                provider_symbol=symbol,
                payload=orjson.dumps(data),
            )

    async def heartbeat(self) -> None:
        if self._ws is None:
            raise ConnectionError("Binance adapter is not connected")
        pong_waiter = await self._ws.ping()
        await pong_waiter

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()

    def normalize(self, frame: QueuedRawFrame) -> NormalizedStreamObservation:
        data = orjson.loads(frame.frame.payload)
        if not isinstance(data, dict):
            raise ValueError("Binance payload must be an object")
        symbol = frame.frame.provider_symbol.upper()
        identity_status, canonical_id, canonical_name = resolve_streaming_instrument(
            self.provider, symbol
        )

        if frame.frame.stream_type is StreamType.AGG_TRADE:
            price = _finite_positive(data.get("p"), field="p")
            quantity = _finite_positive(data.get("q"), field="q")
            trade_id = str(data.get("a") or "")
            if not trade_id:
                raise ValueError("Binance aggregate trade id is required")
            seq = self._trade_trackers[symbol].observe(
                trade_id,
                reconnect_epoch=frame.reconnect_epoch,
            )
            buyer_is_maker = data.get("m")
            if not isinstance(buyer_is_maker, bool):
                raise ValueError("Binance aggTrade m must be boolean")
            aggressor = "SELL" if buyer_is_maker else "BUY"
            provider_ts = _utc_from_ms(data.get("T"), field="T")
            payload = {
                "event_id": f"agg:{symbol}:{trade_id}",
                "price": price,
                "base_quantity": quantity,
                "notional_usd": price * quantity,
                "aggressor_side": aggressor,
                "canonical_asset_name": canonical_name,
            }
            is_aggregate = True
        elif frame.frame.stream_type is StreamType.LIQUIDATION:
            order = data.get("o")
            if not isinstance(order, dict):
                raise ValueError("Binance forceOrder o object is required")
            price = _finite_positive(
                order.get("ap") or order.get("p"),
                field="o.ap/o.p",
            )
            quantity = _finite_positive(
                order.get("z") or order.get("q"),
                field="o.z/o.q",
            )
            side = str(order.get("S") or "").upper()
            if side not in {"BUY", "SELL"}:
                liquidation_side = "UNKNOWN"
            else:
                liquidation_side = (
                    "LONG_LIQUIDATION" if side == "SELL" else "SHORT_LIQUIDATION"
                )
            provider_ts = _utc_from_ms(
                order.get("T") or data.get("E"),
                field="o.T/E",
            )
            event_id = (
                f"liq:{symbol}:{int(provider_ts.timestamp() * 1000)}:"
                f"{side}:{quantity:.12g}:{price:.12g}"
            )
            seq = self._liquidation_trackers[symbol].observe(
                None,
                reconnect_epoch=frame.reconnect_epoch,
            )
            payload = {
                "event_id": event_id,
                "price": price,
                "base_quantity": quantity,
                "notional_usd": price * quantity,
                "liquidation_side": liquidation_side,
                "canonical_asset_name": canonical_name,
            }
            is_aggregate = True
        else:
            raise ValueError("unsupported Binance stream type")

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
            payload=payload,
            **_sequence_flags(seq.status),
        )
        return NormalizedStreamObservation(envelope=envelope, sequence=seq)
