"""Tradevisor V2 ingress validation and normalization.

MEASUREMENT ONLY - NO PRODUCTION DECISION OR EXECUTION AUTHORITY.

This module turns one inbound alert into a normalized, source-attributed
evidence record and an explicit admission disposition. It performs no I/O, no
network call, no persistence and no notification: the caller owns durability.
That keeps the security-critical parsing independently testable and makes it
impossible for a webhook request to authorize anything by itself.

Timestamp semantics (the defect class this guards against):

    {{time}}     = bar OPEN time (UTC)
    {{timenow}}  = alert TRIGGER time (UTC)
    expected_close = bar_opened_at + interval

A trigger that precedes the expected close is refused, because accepting it
would treat an unfinished bar as closed evidence (look-ahead leakage).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping

from pydantic import ValidationError

from app.models.tradevisor import TradevisorIngressV1

DISPOSITION_ACCEPTED = "ACCEPTED_SHADOW"
DISPOSITION_REJECTED = "REJECTED"

TIMEFRAME_ALIASES = {"1H": 60, "60": 60, "4H": 240, "240": 240}

#: Provenance labels. The external indicator is invite-only: O'Pip receives a
#: claimed BUY/SELL event and can never inspect the Pine script that produced
#: it, so algorithm verification is recorded as UNVERIFIED by construction.
PROVENANCE_SCHEMA_VERSION = "tradevisor-provenance-v1"
ALGORITHM_VERIFICATION_UNVERIFIED = "UNVERIFIED_EXTERNAL_SCRIPT"
DEFAULT_INTEGRATION_VERSION = "tradevisor-ingress-v1"


class TradevisorIngressError(ValueError):
    """A refused inbound alert with a stable machine-readable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TradevisorConfig:
    """Operator-approved ingestion policy for one deployment."""

    interval_minutes: int = 60
    allowed_exchanges: frozenset[str] = frozenset({"COINBASE", "BINANCE"})
    # (exchange, raw symbol) -> (normalized instrument, execution venue market)
    instruments: Mapping[tuple[str, str], tuple[str, str]] = None  # type: ignore[assignment]
    alert_configs: Mapping[str, str] = None  # type: ignore[assignment]
    max_source_age_seconds: int = 300
    close_tolerance_seconds: int = 60
    future_skew_seconds: int = 5
    #: Operator-declared version of THIS integration contract (ours). It never
    #: describes the external script's contents.
    integration_version: str = DEFAULT_INTEGRATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instruments", dict(self.instruments or {})
        )
        object.__setattr__(self, "alert_configs", dict(self.alert_configs or {}))


@dataclass(frozen=True)
class TradevisorSignal:
    """Normalized, source-attributed evidence for downstream evaluation."""

    dedup_id: str
    signal: str
    instrument: str
    market: str
    source_exchange: str
    source_symbol: str
    timeframe_minutes: int
    bar_opened_at: datetime
    bar_closed_at: datetime
    triggered_at: datetime
    received_at: datetime
    signal_price: float
    alert_config_version: str
    disposition: str = DISPOSITION_ACCEPTED
    #: Observed external-observation provenance. Recorded, never trusted as
    #: proof of the external script's algorithm; excluded from dedup identity.
    provenance: dict = field(default_factory=dict)

    def as_evidence(self) -> dict:
        return {
            "schema_version": "tradevisor-signal-v1",
            "provider": "TRADEVISOR",
            "indicator": "TRADEVISOR_V2",
            "strategy_family": "TRADEVISOR_SWING_V1",
            "measurement_only": True,
            "trade_authority_changed": False,
            "provenance": dict(self.provenance),
            "dedup_id": self.dedup_id,
            "signal": self.signal,
            "instrument": self.instrument,
            "market": self.market,
            "source_exchange": self.source_exchange,
            "source_symbol": self.source_symbol,
            "timeframe_minutes": self.timeframe_minutes,
            "bar_opened_at": self.bar_opened_at.isoformat(),
            "bar_closed_at": self.bar_closed_at.isoformat(),
            "triggered_at": self.triggered_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "signal_price": self.signal_price,
            "alert_config_version": self.alert_config_version,
            "disposition": self.disposition,
        }


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TradevisorIngressError(f"{name}_NOT_UTC")
    return value.astimezone(timezone.utc)


def dedup_id_for(event: TradevisorIngressV1) -> str:
    """Stable identity over source facts only.

    The receipt timestamp is deliberately excluded so a replayed alert, or one
    redelivered with a new receipt time, cannot create a second observation.
    """
    material = "|".join(
        (
            event.source,
            event.indicator,
            event.exchange.upper(),
            event.symbol.upper(),
            event.timeframe.upper(),
            event.signal,
            event.bar_opened_at.astimezone(timezone.utc).isoformat(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def provenance_for(
    event: TradevisorIngressV1,
    *,
    integration_version: str,
    bar_opened_at: datetime,
    timeframe_minutes: int,
) -> dict:
    """Describe the externally observed observation, without over-claiming.

    Three separate facts are kept apart on purpose:

    * ``integration_version``  - the operator-declared version of THIS contract;
    * ``external_indicator``   - the vendor identity the alert asserts;
    * ``declared_indicator_version`` - a string supplied by the alert body.

    The last one is *unverified*: TradingView delivers only the alert message,
    so no field can prove what the invite-only Pine script contains. Recording
    it as anything stronger would be a fabricated certification.
    """
    declared = (event.indicator_version or "").strip() or None
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "external_indicator": event.indicator,
        "integration_version": integration_version,
        "declared_indicator_version": declared,
        "declared_version_verified": False,
        "algorithm_verification": ALGORITHM_VERIFICATION_UNVERIFIED,
        "source_exchange": event.exchange.strip().upper(),
        "source_symbol": event.symbol.strip().upper(),
        "timeframe_minutes": int(timeframe_minutes),
        "signal": event.signal,
        "source_bar_opened_at": bar_opened_at.isoformat(),
    }


def parse_payload(raw: object) -> TradevisorIngressV1:
    if not isinstance(raw, Mapping):
        raise TradevisorIngressError("PAYLOAD_NOT_OBJECT")
    try:
        return TradevisorIngressV1.model_validate(dict(raw))
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ())) or "payload"
        raise TradevisorIngressError(f"SCHEMA_INVALID:{loc}") from exc


def timeframe_minutes(value: str) -> int:
    key = value.strip().upper()
    minutes = TIMEFRAME_ALIASES.get(key)
    if minutes is None:
        raise TradevisorIngressError("UNSUPPORTED_TIMEFRAME")
    return minutes


def evaluate_ingress(
    event: TradevisorIngressV1,
    *,
    config: TradevisorConfig,
    received_at: datetime,
) -> TradevisorSignal:
    """Admit or refuse one alert. Every refusal is an explicit disposition."""
    received = _utc(received_at, "received_at")
    exchange = event.exchange.strip().upper()
    if exchange not in {item.upper() for item in config.allowed_exchanges}:
        raise TradevisorIngressError("UNSUPPORTED_EXCHANGE")

    minutes = timeframe_minutes(event.timeframe)
    if minutes != config.interval_minutes:
        raise TradevisorIngressError("UNSUPPORTED_TIMEFRAME")

    opened = _utc(event.bar_opened_at, "bar_opened_at")
    epoch_minutes = int(opened.timestamp()) // 60
    if epoch_minutes % minutes:
        raise TradevisorIngressError("BAR_TIME_MISALIGNED")
    closed = opened + timedelta(minutes=minutes)

    triggered = _utc(event.triggered_at, "triggered_at")
    if triggered < closed - timedelta(seconds=config.close_tolerance_seconds):
        # Accepting this would treat an unfinished bar as closed evidence.
        raise TradevisorIngressError("TRIGGER_BEFORE_BAR_CLOSE")
    if triggered > received + timedelta(seconds=config.future_skew_seconds):
        raise TradevisorIngressError("FUTURE_DATED_EVENT")
    if (received - triggered).total_seconds() > config.max_source_age_seconds:
        raise TradevisorIngressError("STALE_EVENT")

    expected_signal = config.alert_configs.get(event.alert_config_version)
    if expected_signal is None:
        raise TradevisorIngressError("UNKNOWN_ALERT_CONFIG")
    if expected_signal.strip().upper() != event.signal:
        raise TradevisorIngressError("CONFIG_SIGNAL_CONFLICT")

    mapping = config.instruments.get((exchange, event.symbol.upper()))
    if mapping is None:
        raise TradevisorIngressError("UNSUPPORTED_INSTRUMENT")
    instrument, market = mapping

    return TradevisorSignal(
        dedup_id=dedup_id_for(event),
        signal=event.signal,
        instrument=instrument,
        market=market,
        source_exchange=exchange,
        source_symbol=event.symbol.upper(),
        timeframe_minutes=minutes,
        bar_opened_at=opened,
        bar_closed_at=closed,
        triggered_at=triggered,
        received_at=received,
        signal_price=event.signal_price,
        alert_config_version=event.alert_config_version,
        provenance=provenance_for(
            event,
            integration_version=config.integration_version,
            bar_opened_at=opened,
            timeframe_minutes=minutes,
        ),
    )


def admit(
    raw: object,
    *,
    config: TradevisorConfig,
    received_at: datetime,
) -> TradevisorSignal:
    """Parse then evaluate. Raises TradevisorIngressError on any refusal."""
    return evaluate_ingress(parse_payload(raw), config=config, received_at=received_at)


def _csv_items(value: str, *, field: str) -> list[str]:
    items = [item.strip() for item in str(value or "").split(",")]
    return [item for item in items if item]


def signal_from_record(record: Mapping[str, object]) -> TradevisorSignal:
    """Rebuild a normalized signal from its durable representation.

    Used only inside O'Pip (drain/learning). Anything that cannot be rebuilt
    exactly is refused, so a corrupted row is quarantined rather than evaluated
    against reconstructed guesses.
    """
    if not isinstance(record, Mapping):
        raise TradevisorIngressError("RECORD_NOT_OBJECT")
    try:
        signal = str(record["signal"]).strip().upper()
        if signal not in {"BUY", "SELL"}:
            raise TradevisorIngressError("RECORD_INVALID")
        rebuilt = TradevisorSignal(
            dedup_id=str(record["dedup_id"]),
            signal=signal,
            instrument=str(record["instrument"]),
            market=str(record["market"]),
            source_exchange=str(record["source_exchange"]),
            source_symbol=str(record["source_symbol"]),
            timeframe_minutes=int(record["timeframe_minutes"]),
            bar_opened_at=datetime.fromisoformat(str(record["bar_opened_at"])),
            bar_closed_at=datetime.fromisoformat(str(record["bar_closed_at"])),
            triggered_at=datetime.fromisoformat(str(record["triggered_at"])),
            received_at=datetime.fromisoformat(str(record["received_at"])),
            signal_price=float(record["signal_price"]),
            alert_config_version=str(record["alert_config_version"]),
            disposition=str(record.get("disposition") or DISPOSITION_ACCEPTED),
            provenance=(
                dict(record["provenance"])
                if isinstance(record.get("provenance"), Mapping)
                else {}
            ),
        )
    except TradevisorIngressError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TradevisorIngressError("RECORD_INVALID") from exc
    for stamp in (
        rebuilt.bar_opened_at,
        rebuilt.bar_closed_at,
        rebuilt.triggered_at,
        rebuilt.received_at,
    ):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise TradevisorIngressError("RECORD_INVALID")
    if not (rebuilt.signal_price > 0):
        raise TradevisorIngressError("RECORD_INVALID")
    return rebuilt


def config_from_settings(settings: object) -> TradevisorConfig:
    """Resolve the operator's declared ingestion policy.

    The CSV encodings are the deployment surface; every entry is parsed here so
    a single malformed row fails closed at boot instead of silently narrowing
    what O'Pip can observe.
    """

    def _int(name: str, default: int) -> int:
        try:
            return int(getattr(settings, name, default))
        except (TypeError, ValueError) as exc:
            raise TradevisorIngressError(f"CONFIG_INVALID:{name}") from exc

    exchanges = frozenset(
        item.upper() for item in _csv_items(getattr(settings, "opip_tradevisor_allowed_exchanges", ""), field="exchanges")
    )

    alert_configs: dict[str, str] = {}
    for row in _csv_items(getattr(settings, "opip_tradevisor_alert_configs", ""), field="alert_configs"):
        if "=" not in row:
            raise TradevisorIngressError("CONFIG_INVALID:ALERT_CONFIG_ROW")
        key, _, direction = row.partition("=")
        key = key.strip()
        direction = direction.strip().upper()
        if not key or direction not in {"BUY", "SELL"}:
            raise TradevisorIngressError("CONFIG_INVALID:ALERT_CONFIG_ROW")
        alert_configs[key] = direction

    instruments: dict[tuple[str, str], tuple[str, str]] = {}
    for row in _csv_items(getattr(settings, "opip_tradevisor_instruments", ""), field="instruments"):
        source, _, target = row.partition("=")
        exchange, _, symbol = source.partition(":")
        instrument, _, market = target.partition(":")
        exchange = exchange.strip().upper()
        symbol = symbol.strip().upper()
        instrument = instrument.strip().upper()
        market = market.strip().upper()
        if not all((exchange, symbol, instrument, market)):
            raise TradevisorIngressError("CONFIG_INVALID:INSTRUMENT_ROW")
        # An explicit mapping must never imply quote parity: the operator states
        # the execution market, this module never derives it.
        instruments[(exchange, symbol)] = (instrument, market)

    return TradevisorConfig(
        interval_minutes=_int("opip_tradevisor_interval_minutes", 60),
        allowed_exchanges=exchanges,
        instruments=instruments,
        alert_configs=alert_configs,
        max_source_age_seconds=_int("opip_tradevisor_max_event_age_seconds", 300),
        close_tolerance_seconds=_int("opip_tradevisor_close_tolerance_seconds", 60),
        future_skew_seconds=_int("opip_tradevisor_future_skew_seconds", 5),
        integration_version=_integration_version(settings),
    )


def _integration_version(settings: object) -> str:
    """Resolve the operator-declared integration contract version.

    This labels OUR contract, so a malformed value is a boot-time configuration
    error rather than a silently relabelled provenance record.
    """
    raw = str(
        getattr(settings, "opip_tradevisor_integration_version", "") or ""
    ).strip()
    if not raw or len(raw) > 64 or not re.fullmatch(r"[A-Za-z0-9._+-]+", raw):
        raise TradevisorIngressError("CONFIG_INVALID:INTEGRATION_VERSION")
    return raw


