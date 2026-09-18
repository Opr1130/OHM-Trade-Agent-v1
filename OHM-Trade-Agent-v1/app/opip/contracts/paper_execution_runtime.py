"""B/C-1 runtime contracts for Paper v2 admission and quote evidence.

This module adds only the runtime seams required by B/C-1.  It does not activate
Paper v2 and it does not add protection behavior.

The frozen B/C-0 disposition/order/attempt/fill contracts remain authoritative.
Admission requests are internal commands recorded canonically so the writer can
own the portfolio-version check and capital/capacity decision without a TOCTOU
gap.  Level-1 quote evidence is a separate canonical fact; aggregate/OHLC market
observations are never promoted to quote fidelity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
from typing import Any, Mapping

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    EvaluationPopulation,
    TemporalBasis,
    TemporalEvidence,
    TemporalPrecision,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    validate_paper_evidence_payload,
)
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES

PAPER_ADMISSION_REQUEST_RECORDED = "paper_execution.admission_request.recorded"
PAPER_QUOTE_EVIDENCE_RECORDED = "paper_execution.quote_evidence.recorded"

#: Events producers may submit through the ordinary WriterIntent path in B/C-1.
#: Opportunity disposition remains writer-owned through the admission RPC.
#: Protection and reconciliation remain B/C-2.
PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES = frozenset(
    {
        PAPER_QUOTE_EVIDENCE_RECORDED,
        PAPER_ORDER_INTENT_RECORDED,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        PAPER_FILL_RECORDED,
    }
)

_ADMISSION_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "engine",
        "disposition_id",
        "decision_context_id",
        "disposition_seq",
        "evaluation_population",
        "quote_currency",
        "requested_capital",
        "disposition_time",
        "expected_portfolio_version",
        "capital_policy_version",
        "portfolio_equity_limit",
        "portfolio_position_limit",
        "requested_reservation_amount",
    }
)

_ADMISSION_RECORD_FIELDS = _ADMISSION_REQUEST_FIELDS | frozenset(
    {"guard_result", "observed_portfolio_version"}
)

_QUOTE_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "quote_evidence_id",
        "instrument_version",
        "venue",
        "native_symbol",
        "quote_currency",
        "source_kind",
        "best_bid",
        "best_ask",
        "bid_quantity",
        "ask_quantity",
        "quote_time",
        "execution_model_version",
    }
)


def _canonical_identity(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return value


def _finite_positive(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return number


def _parse_iso(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _validate_quote_time(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("quote_time must be a temporal evidence object")
    try:
        precision = TemporalPrecision(str(raw.get("precision")))
        basis = TemporalBasis(str(raw.get("basis")))
    except ValueError as exc:
        raise ValueError("quote_time temporal evidence is unsupported") from exc

    if precision is TemporalPrecision.UNKNOWN:
        raise ValueError("Level-1 quote evidence requires exact or bounded quote_time")
    if precision is TemporalPrecision.EXACT:
        evidence = TemporalEvidence(
            precision=precision,
            basis=basis,
            occurred_at=_parse_iso(raw.get("occurred_at"), field_name="quote_time.occurred_at"),
            reason=raw.get("reason"),
        )
    else:
        evidence = TemporalEvidence(
            precision=precision,
            basis=basis,
            window_start=_parse_iso(raw.get("window_start"), field_name="quote_time.window_start"),
            window_end=_parse_iso(raw.get("window_end"), field_name="quote_time.window_end"),
            reason=raw.get("reason"),
        )
    canonical = evidence.as_dict()
    if canonical != dict(raw):
        raise ValueError("quote_time must use canonical TemporalEvidence serialization")
    return canonical


def admission_result_identities(disposition_id: str) -> tuple[str, str]:
    """Derive immutable downstream identities before the admission event is written."""

    canonical = _canonical_identity(disposition_id, field_name="disposition_id")
    token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"PTV2:{token}", f"RSV2:{token}"


@dataclass(frozen=True)
class PaperAdmissionRequest:
    disposition_id: str
    decision_context_id: str
    disposition_seq: int
    quote_currency: str
    requested_capital: float
    disposition_time: Mapping[str, Any]
    expected_portfolio_version: int
    capital_policy_version: str
    portfolio_equity_limit: float
    portfolio_position_limit: int
    requested_reservation_amount: float
    evaluation_population: str = EvaluationPopulation.QUALIFIED_INTENT.value
    engine: str = ENGINE_OPIP_PAPER_V2
    schema_version: int = PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "disposition_id": self.disposition_id,
            "decision_context_id": self.decision_context_id,
            "disposition_seq": self.disposition_seq,
            "evaluation_population": self.evaluation_population,
            "quote_currency": self.quote_currency,
            "requested_capital": self.requested_capital,
            "disposition_time": dict(self.disposition_time),
            "expected_portfolio_version": self.expected_portfolio_version,
            "capital_policy_version": self.capital_policy_version,
            "portfolio_equity_limit": self.portfolio_equity_limit,
            "portfolio_position_limit": self.portfolio_position_limit,
            "requested_reservation_amount": self.requested_reservation_amount,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PaperAdmissionRequest":
        if set(raw) != _ADMISSION_REQUEST_FIELDS:
            missing = sorted(_ADMISSION_REQUEST_FIELDS - set(raw))
            extra = sorted(set(raw) - _ADMISSION_REQUEST_FIELDS)
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("extra=" + ",".join(extra))
            raise ValueError("invalid PaperAdmissionRequest fields: " + "; ".join(details))
        request = cls(
            schema_version=raw["schema_version"],
            engine=raw["engine"],
            disposition_id=raw["disposition_id"],
            decision_context_id=raw["decision_context_id"],
            disposition_seq=raw["disposition_seq"],
            evaluation_population=raw["evaluation_population"],
            quote_currency=raw["quote_currency"],
            requested_capital=raw["requested_capital"],
            disposition_time=raw["disposition_time"],
            expected_portfolio_version=raw["expected_portfolio_version"],
            capital_policy_version=raw["capital_policy_version"],
            portfolio_equity_limit=raw["portfolio_equity_limit"],
            portfolio_position_limit=raw["portfolio_position_limit"],
            requested_reservation_amount=raw["requested_reservation_amount"],
        )
        validate_admission_request(request)
        return request


@dataclass(frozen=True)
class PaperAdmissionAck:
    status: str
    disposition: str | None = None
    request_event_id: str | None = None
    disposition_event_id: str | None = None
    history_epoch: int | None = None
    local_sequence: int | None = None
    portfolio_version: int | None = None
    paper_trade_id: str | None = None
    reservation_id: str | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "disposition": self.disposition,
            "request_event_id": self.request_event_id,
            "disposition_event_id": self.disposition_event_id,
            "history_epoch": self.history_epoch,
            "local_sequence": self.local_sequence,
            "portfolio_version": self.portfolio_version,
            "paper_trade_id": self.paper_trade_id,
            "reservation_id": self.reservation_id,
            "error_code": self.error_code,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PaperAdmissionAck":
        return cls(
            status=str(raw["status"]),
            disposition=(str(raw["disposition"]) if raw.get("disposition") else None),
            request_event_id=(
                str(raw["request_event_id"]) if raw.get("request_event_id") else None
            ),
            disposition_event_id=(
                str(raw["disposition_event_id"])
                if raw.get("disposition_event_id")
                else None
            ),
            history_epoch=(
                int(raw["history_epoch"]) if raw.get("history_epoch") is not None else None
            ),
            local_sequence=(
                int(raw["local_sequence"]) if raw.get("local_sequence") is not None else None
            ),
            portfolio_version=(
                int(raw["portfolio_version"])
                if raw.get("portfolio_version") is not None
                else None
            ),
            paper_trade_id=(
                str(raw["paper_trade_id"]) if raw.get("paper_trade_id") else None
            ),
            reservation_id=(
                str(raw["reservation_id"]) if raw.get("reservation_id") else None
            ),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


def validate_admission_request(request: PaperAdmissionRequest) -> dict[str, Any]:
    """Validate admission input against the frozen B/C-0 ADMITTED contract."""

    payload = request.as_dict()
    paper_trade_id, reservation_id = admission_result_identities(request.disposition_id)
    probe = {
        **payload,
        "disposition": "ADMITTED",
        "reason_code": "ADMISSION_REQUEST",
        "paper_trade_id": paper_trade_id,
        "reservation_id": reservation_id,
    }
    validate_paper_evidence_payload(PAPER_OPPORTUNITY_DISPOSITION_RECORDED, probe)
    return payload


def validate_admission_request_record_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _ADMISSION_RECORD_FIELDS:
        raise ValueError("invalid canonical admission request record fields")

    request_payload = {
        field_name: payload[field_name]
        for field_name in _ADMISSION_REQUEST_FIELDS
    }
    request = PaperAdmissionRequest.from_dict(request_payload)
    guard_result = payload.get("guard_result")
    if guard_result not in {"ELIGIBLE", "STALE_PORTFOLIO_VERSION"}:
        raise ValueError("unsupported admission guard_result")
    observed_version = payload.get("observed_portfolio_version")
    if type(observed_version) is not int or observed_version < 0:
        raise ValueError("observed_portfolio_version must be a nonnegative integer")
    if guard_result == "ELIGIBLE":
        if request.expected_portfolio_version != observed_version:
            raise ValueError("eligible admission guard must match expected portfolio version")
    elif request.expected_portfolio_version == observed_version:
        raise ValueError("stale admission guard cannot match expected portfolio version")

    return {
        **request.as_dict(),
        "guard_result": guard_result,
        "observed_portfolio_version": observed_version,
    }


def admission_request_idempotency_key(disposition_id: str) -> str:
    return (
        f"{PAPER_ADMISSION_REQUEST_RECORDED}:"
        f"{_canonical_identity(disposition_id, field_name='disposition_id')}"
    )


def validate_quote_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("quote evidence payload must be a mapping")
    if set(payload) != _QUOTE_EVIDENCE_FIELDS:
        missing = sorted(_QUOTE_EVIDENCE_FIELDS - set(payload))
        extra = sorted(set(payload) - _QUOTE_EVIDENCE_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ValueError("invalid quote evidence fields: " + "; ".join(details))

    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported quote evidence schema version")
    _canonical_identity(payload.get("quote_evidence_id"), field_name="quote_evidence_id")
    _canonical_identity(payload.get("instrument_version"), field_name="instrument_version")
    _canonical_identity(payload.get("venue"), field_name="venue")
    _canonical_identity(payload.get("native_symbol"), field_name="native_symbol")
    quote_currency = _canonical_identity(
        payload.get("quote_currency"), field_name="quote_currency"
    )
    if quote_currency not in QUOTE_CURRENCIES:
        raise ValueError("unsupported quote_currency")
    if payload.get("source_kind") != "LEVEL_1_BOOK":
        raise ValueError("quote evidence source_kind must be LEVEL_1_BOOK")
    if payload.get("execution_model_version") != PAPER_EXECUTION_MODEL_VERSION:
        raise ValueError("unsupported execution model version")

    best_bid = _finite_positive(payload.get("best_bid"), field_name="best_bid")
    best_ask = _finite_positive(payload.get("best_ask"), field_name="best_ask")
    if best_ask < best_bid:
        raise ValueError("best_ask cannot be below best_bid")
    _finite_positive(payload.get("bid_quantity"), field_name="bid_quantity")
    _finite_positive(payload.get("ask_quantity"), field_name="ask_quantity")
    quote_time = _validate_quote_time(payload.get("quote_time"))

    normalized = dict(payload)
    normalized["quote_time"] = quote_time
    return normalized


def quote_evidence_idempotency_key(payload: Mapping[str, Any]) -> str:
    normalized = validate_quote_evidence_payload(payload)
    return f"{PAPER_QUOTE_EVIDENCE_RECORDED}:{normalized['quote_evidence_id']}"


__all__ = [
    "PAPER_ADMISSION_REQUEST_RECORDED",
    "PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES",
    "PAPER_QUOTE_EVIDENCE_RECORDED",
    "PaperAdmissionAck",
    "PaperAdmissionRequest",
    "admission_request_idempotency_key",
    "admission_result_identities",
    "quote_evidence_idempotency_key",
    "validate_admission_request",
    "validate_admission_request_record_payload",
    "validate_quote_evidence_payload",
]
