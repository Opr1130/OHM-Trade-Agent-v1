"""Canonical terminal paper-outcome vocabulary (PR-A).

A terminal paper outcome is authoritative economic evidence about exactly one
finished paper lifecycle. It is append-only: a correction appends a superseding
event and never rewrites committed economics.

This module owns the event name, stream, priority, identity construction and
payload validation so none of them is scattered as a string literal across
producers. Renaming any committed value here is a contract change, because
already-written evidence carries the old name.

Scope boundary: this contract carries only what the paper simulator can
truthfully assert today - a flat fee rate and fixed slippage. Spread, latency,
partial fills, market impact and break-even economics are deliberately absent;
claiming them would be invented precision.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Mapping

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

#: Canonical event type for a terminal paper outcome.
PAPER_OUTCOME_TERMINAL_RECORDED = "paper_outcome.terminal.recorded"

PAPER_OUTCOME_EVENT_TYPES: frozenset[str] = frozenset({PAPER_OUTCOME_TERMINAL_RECORDED})

#: Canonical watermark stream. Separate from every other stream so paper
#: outcome progress can never rewind alert-governor or feature-bus progress.
PAPER_OUTCOME_STREAM = "paper_outcome.v1"

#: Terminal economic evidence is not droppable. LOW is a scheduling class in
#: the canonical writer, not an eviction policy: accepted events are committed
#: or the producer is told they were not, and the producer spools the gap.
PAPER_OUTCOME_PRIORITY = "LOW"

#: Payload schema version, independent of the canonical DB schema version.
PAPER_OUTCOME_SCHEMA_VERSION = 1

#: Version of the economic model that produced the numbers. PR-B will add
#: richer models; keeping this explicit means old evidence is never silently
#: reinterpreted under a newer cost model.
PAPER_SIM_ECONOMIC_MODEL_VERSION = "paper-sim-flat-fee-v1"

#: Execution engines are never conflated: the same economic opportunity can be
#: simulated by more than one engine, and they must not alias onto one outcome.
ENGINE_OHM_PAPER_SIM = "OHM_PAPER_SIM_V1"
ENGINE_FREQTRADE_DRY_RUN = "FREQTRADE_DRY_RUN_V1"
EXECUTION_ENGINES: frozenset[str] = frozenset(
    {ENGINE_OHM_PAPER_SIM, ENGINE_FREQTRADE_DRY_RUN}
)

#: Terminal lifecycle statuses. Non-terminal lifecycles have no outcome.
CLOSED = "CLOSED"
CANCELLED = "CANCELLED"
UNRESOLVED = "UNRESOLVED"
TERMINAL_STATUSES: frozenset[str] = frozenset({CLOSED, CANCELLED, UNRESOLVED})

#: Terminal reasons this simulator can emit. A free-text reason would make the
#: evidence unqueryable and would silently absorb new paths.
EXIT_REASONS: frozenset[str] = frozenset(
    {
        "TARGET_2",
        "STOP",
        "TIME_EXIT",
        "ENTRY_CANDLE_STOP",
        "PENDING_TTL_EXPIRED",
        "DO_NOT_CHASE",
        "OPERATOR_OFF",
        "OHLC_GAP",
        "UNRESOLVED",
    }
)

#: Trade direction. Direction-scoped learning buckets are meaningless without
#: it, so an outcome that cannot state its side is not a supervised label.
DIRECTIONS: frozenset[str] = frozenset({"LONG", "SHORT"})

#: USD and USDT are distinct quote currencies. There is no trusted conversion
#: source for realised P/L in this repository, so they are never combined into
#: one canonical figure.
QUOTE_CURRENCIES: frozenset[str] = frozenset({"USD", "USDT"})

#: Longest suffix first, so ``BTCUSDT`` resolves to USDT and never to USD.
_QUOTE_SUFFIX_ORDER: tuple[str, ...] = ("USDT", "USD")

LINEAGE_COMPLETE = "COMPLETE"
LINEAGE_INCOMPLETE = "LINEAGE_INCOMPLETE"

#: Lineage slots the target model wants. Absent ones are *named*, never filled
#: with a guess, so a consumer can see precisely what is missing.
OPTIONAL_LINEAGE_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "decision_context_id",
)

_REQUIRED_KEYS: tuple[str, ...] = (
    "schema_version",
    "outcome_id",
    "engine",
    "paper_trade_id",
    "episode_id",
    "cohort_id",
    "strategy_version",
    "exchange",
    "native_symbol",
    "base_asset",
    "direction",
    "quote_currency",
    "intended_entry_low",
    "intended_entry_high",
    "intended_entry_limit",
    "simulated_entry_price",
    "quantity_initial",
    "executed_notional",
    "entry_timestamp",
    "exit_price",
    "exit_timestamp",
    "exit_reason",
    "terminal_status",
    "capital_committed",
    "gross_pnl",
    "fees_paid",
    "net_pnl",
    "net_pnl_pct",
    "economic_model_version",
    "final_revision",
    "terminal_event_id",
    "lineage_completeness",
    "lineage_missing",
)

#: Realised economic figures. Zero for a cancelled setup (nothing was realised)
#: and absent for an unresolvable one (the result is unknown, and zero would be
#: a false claim of break-even).
_REALISED_ECONOMIC_KEYS: tuple[str, ...] = (
    "gross_pnl",
    "fees_paid",
    "net_pnl",
    "net_pnl_pct",
)

#: Capital the lifecycle committed. This is a *planned* amount, not a realised
#: result, so it stays known and non-zero even when the setup was cancelled.
CAPITAL_KEY = "capital_committed"

#: Floats are compared with a tolerance because the net figure is derived.
_NET_PNL_TOLERANCE = 1e-6


def resolve_quote_currency(symbol: str, *, quote_currency: str | None = None) -> str | None:
    """Resolve the quote currency, or ``None`` when it cannot be proven.

    An explicit quote currency is honoured only when it is a known one. A
    symbol suffix is matched longest-first so ``BTCUSDT`` cannot be mistaken
    for a USD pair. ``None`` means "unknown", which callers must fail closed
    on rather than defaulting to USD.
    """
    if quote_currency is not None:
        normalized = str(quote_currency).strip().upper()
        return normalized if normalized in QUOTE_CURRENCIES else None
    normalized_symbol = str(symbol or "").strip().upper()
    for quote in _QUOTE_SUFFIX_ORDER:
        if normalized_symbol.endswith(quote):
            return quote
    return None


def terminal_outcome_id(
    *,
    engine: str,
    paper_trade_id: str,
    terminal_status: str,
    exit_reason: str,
) -> str:
    """Deterministic identity of one terminal economic outcome.

    Deliberately excludes revision, economics and clocks: a lifecycle reaches a
    given terminal state once, so a retry must reproduce the same identity even
    if it carries a later revision. Including economics would make every
    correction a *new* outcome instead of a correction *of* one.

    ``engine`` is included so two engines simulating the same opportunity can
    never alias onto a single outcome.
    """
    return stable_hash(
        "PAPER-OUTCOME",
        {
            "engine": str(engine),
            "paper_trade_id": str(paper_trade_id),
            "terminal_status": str(terminal_status),
            "exit_reason": str(exit_reason),
        },
    )


def terminal_outcome_idempotency_key(outcome_id: str) -> str:
    """Canonical writer idempotency key for one terminal outcome."""
    return f"{PAPER_OUTCOME_TERMINAL_RECORDED}:{outcome_id}"


def _require_finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    return require_utc(parsed, field_name=field_name)


def _validate_economics(payload: Mapping[str, Any], terminal_status: str) -> None:
    """Enforce the economics the simulator is actually allowed to assert.

    The three terminal states carry genuinely different truths, so they are not
    allowed to share one form:

    * ``CLOSED``    - realised economics, with net consistent with gross - fees.
    * ``CANCELLED`` - nothing was realised, so every realised figure is zero.
      The committed capital was still committed, so it stays as recorded.
    * ``UNRESOLVED``- the result is unknowable, so every economic figure is
      ``None``. Zero would be a false claim of break-even.
    """
    realised = {key: payload.get(key) for key in _REALISED_ECONOMIC_KEYS}
    capital = payload.get(CAPITAL_KEY)

    if terminal_status == UNRESOLVED:
        offending = sorted(
            [key for key, value in realised.items() if value is not None]
            + ([CAPITAL_KEY] if capital is not None else [])
        )
        if offending:
            raise ValueError(
                "UNRESOLVED outcomes must not assert economics: " + ", ".join(offending)
            )
        return

    committed = _require_finite_number(capital, field_name=CAPITAL_KEY)
    if committed < 0:
        raise ValueError(f"{CAPITAL_KEY} must not be negative")

    numbers = {
        key: _require_finite_number(value, field_name=key)
        for key, value in realised.items()
    }
    if terminal_status == CANCELLED:
        non_zero = sorted(key for key, value in numbers.items() if value != 0.0)
        if non_zero:
            raise ValueError(
                "CANCELLED outcomes must have zero realised economics: "
                + ", ".join(non_zero)
            )
        return

    expected_net = numbers["gross_pnl"] - numbers["fees_paid"]
    if abs(numbers["net_pnl"] - expected_net) > _NET_PNL_TOLERANCE:
        raise ValueError("net_pnl must equal gross_pnl - fees_paid for a CLOSED outcome")


def validate_terminal_outcome_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a terminal outcome payload, returning a normalized copy.

    Raises ``ValueError`` for any defect. The canonical writer turns that into
    ``REJECTED``, so malformed economic evidence can never be committed.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("paper outcome payload must be a mapping")

    missing = sorted(key for key in _REQUIRED_KEYS if key not in payload)
    if missing:
        raise ValueError("paper outcome payload missing keys: " + ", ".join(missing))

    if int(payload["schema_version"]) != PAPER_OUTCOME_SCHEMA_VERSION:
        raise ValueError("unsupported paper outcome schema version")

    engine = str(payload["engine"])
    if engine not in EXECUTION_ENGINES:
        raise ValueError(f"unsupported execution engine: {engine}")

    terminal_status = str(payload["terminal_status"])
    if terminal_status not in TERMINAL_STATUSES:
        raise ValueError(f"unsupported terminal status: {terminal_status}")

    exit_reason = str(payload["exit_reason"])
    if exit_reason not in EXIT_REASONS:
        raise ValueError(f"unsupported exit reason: {exit_reason}")

    if not str(payload["paper_trade_id"]).strip():
        raise ValueError("paper_trade_id is required")

    quote_currency = str(payload["quote_currency"])
    if quote_currency not in QUOTE_CURRENCIES:
        raise ValueError(f"unknown quote currency: {quote_currency}")

    direction = str(payload["direction"])
    if direction not in DIRECTIONS:
        raise ValueError(f"unsupported direction: {direction}")

    expected_id = terminal_outcome_id(
        engine=engine,
        paper_trade_id=str(payload["paper_trade_id"]),
        terminal_status=terminal_status,
        exit_reason=exit_reason,
    )
    if str(payload["outcome_id"]) != expected_id:
        raise ValueError("outcome_id does not match its identity")

    if str(payload["economic_model_version"]) != PAPER_SIM_ECONOMIC_MODEL_VERSION:
        raise ValueError("unsupported economic model version")

    entry_at = _parse_timestamp(payload["entry_timestamp"], field_name="entry_timestamp")
    exit_at = _parse_timestamp(payload["exit_timestamp"], field_name="exit_timestamp")
    if exit_at < entry_at:
        raise ValueError("exit_timestamp cannot precede entry_timestamp")

    revision = payload["final_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("final_revision must be a positive integer")

    completeness = str(payload["lineage_completeness"])
    if completeness not in {LINEAGE_COMPLETE, LINEAGE_INCOMPLETE}:
        raise ValueError(f"unsupported lineage completeness: {completeness}")
    lineage_missing = payload["lineage_missing"]
    if not isinstance(lineage_missing, (list, tuple)):
        raise ValueError("lineage_missing must be a list")
    if completeness == LINEAGE_COMPLETE and lineage_missing:
        raise ValueError("COMPLETE lineage cannot list missing fields")
    if completeness == LINEAGE_INCOMPLETE and not lineage_missing:
        raise ValueError("INCOMPLETE lineage must name the missing fields")
    unknown_missing = sorted(set(str(item) for item in lineage_missing) - set(OPTIONAL_LINEAGE_FIELDS))
    if unknown_missing:
        raise ValueError(
            "lineage_missing names unsupported fields: " + ", ".join(unknown_missing)
        )

    _validate_economics(payload, terminal_status)

    for key in ("intended_entry_low", "intended_entry_high", "intended_entry_limit",
                "simulated_entry_price", "quantity_initial", "executed_notional",
                "exit_price"):
        value = payload.get(key)
        if value is not None:
            _require_finite_number(value, field_name=key)

    return dict(payload)


def _lineage(payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    missing = sorted(
        field for field in OPTIONAL_LINEAGE_FIELDS if not str(payload.get(field) or "").strip()
    )
    return (LINEAGE_INCOMPLETE if missing else LINEAGE_COMPLETE), missing


def build_terminal_outcome_payload(
    *,
    engine: str,
    paper_trade_id: str,
    episode_id: str,
    cohort_id: str,
    strategy_version: str | None,
    exchange: str,
    native_symbol: str,
    base_asset: str,
    direction: str,
    quote_currency: str,
    terminal_status: str,
    exit_reason: str,
    exit_price: float | None,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    capital_committed: float | None,
    gross_pnl: float | None,
    fees_paid: float | None,
    net_pnl: float | None,
    net_pnl_pct: float | None,
    final_revision: int,
    terminal_event_id: str,
    intended_entry_low: float | None = None,
    intended_entry_high: float | None = None,
    intended_entry_limit: float | None = None,
    simulated_entry_price: float | None = None,
    quantity_initial: float | None = None,
    executed_notional: float | None = None,
    candidate_id: str | None = None,
    decision_context_id: str | None = None,
    strategy_id: str | None = None,
    learning_version: str | None = None,
    supersedes_id: str | None = None,
    supersession_reason: str | None = None,
) -> dict[str, Any]:
    """Construct and validate a terminal outcome payload.

    Missing optional lineage is *recorded as missing*, never fabricated. A
    strategy version that was not captured at enrollment is reported as
    ``UNKNOWN`` rather than being back-filled with the currently deployed
    version, which would attribute an old trade to a new strategy.
    """
    resolved_quote = resolve_quote_currency(native_symbol, quote_currency=quote_currency)
    if resolved_quote is None:
        raise ValueError(f"unknown quote currency for symbol {native_symbol!r}")

    payload: dict[str, Any] = {
        "schema_version": PAPER_OUTCOME_SCHEMA_VERSION,
        "outcome_id": terminal_outcome_id(
            engine=engine,
            paper_trade_id=paper_trade_id,
            terminal_status=terminal_status,
            exit_reason=exit_reason,
        ),
        "engine": engine,
        "paper_trade_id": paper_trade_id,
        "episode_id": episode_id,
        "cohort_id": cohort_id,
        "candidate_id": candidate_id,
        "decision_context_id": decision_context_id,
        "strategy_id": strategy_id,
        "strategy_version": (str(strategy_version).strip() if strategy_version else "UNKNOWN"),
        "learning_version": learning_version,
        "exchange": exchange,
        "native_symbol": native_symbol,
        "base_asset": base_asset,
        "direction": str(direction).strip().upper(),
        "quote_currency": resolved_quote,
        "intended_entry_low": intended_entry_low,
        "intended_entry_high": intended_entry_high,
        "intended_entry_limit": intended_entry_limit,
        "simulated_entry_price": simulated_entry_price,
        "quantity_initial": quantity_initial,
        "executed_notional": executed_notional,
        "entry_timestamp": iso_z(entry_timestamp, field_name="entry_timestamp"),
        "exit_price": exit_price,
        "exit_timestamp": iso_z(exit_timestamp, field_name="exit_timestamp"),
        "exit_reason": exit_reason,
        "terminal_status": terminal_status,
        "capital_committed": capital_committed,
        "gross_pnl": gross_pnl,
        "fees_paid": fees_paid,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "economic_model_version": PAPER_SIM_ECONOMIC_MODEL_VERSION,
        "final_revision": final_revision,
        "terminal_event_id": terminal_event_id,
        "supersedes_id": supersedes_id,
        "supersession_reason": supersession_reason,
    }
    completeness, missing = _lineage(payload)
    payload["lineage_completeness"] = completeness
    payload["lineage_missing"] = missing
    return validate_terminal_outcome_payload(payload)


__all__ = [
    "CANCELLED",
    "CLOSED",
    "DIRECTIONS",
    "ENGINE_FREQTRADE_DRY_RUN",
    "ENGINE_OHM_PAPER_SIM",
    "EXECUTION_ENGINES",
    "EXIT_REASONS",
    "LINEAGE_COMPLETE",
    "LINEAGE_INCOMPLETE",
    "OPTIONAL_LINEAGE_FIELDS",
    "PAPER_OUTCOME_EVENT_TYPES",
    "PAPER_OUTCOME_PRIORITY",
    "PAPER_OUTCOME_SCHEMA_VERSION",
    "PAPER_OUTCOME_STREAM",
    "PAPER_OUTCOME_TERMINAL_RECORDED",
    "PAPER_SIM_ECONOMIC_MODEL_VERSION",
    "QUOTE_CURRENCIES",
    "TERMINAL_STATUSES",
    "UNRESOLVED",
    "build_terminal_outcome_payload",
    "resolve_quote_currency",
    "terminal_outcome_id",
    "terminal_outcome_idempotency_key",
    "validate_terminal_outcome_payload",
]
