"""The pure v1 Feasibility & Safety seam (R3 F5).

One seam sits in front of the (not yet built) forecast. It evaluates one ACTIVE
F4 opportunity episode against three required pre-forecast checks in the live
hard-filter order:

* ``MARKET_DATA`` maps the existing market-data validation evidence.
* ``MARGIN_ELIGIBILITY`` reuses the thin margin adapter (SHORT-only).
* ``EXECUTION_LIQUIDITY`` reuses the thin execution adapter on its precomputed
  OFFLINE route (no exchange/network refresh).

It calls the existing vetoes and can abstain with ``INSUFFICIENT_EVIDENCE``.
Missing evidence is never favorable evidence: absent or unavailable required
evidence is ``INSUFFICIENT_EVIDENCE``, never ``FEASIBLE``. A present but
malformed required structure raises :class:`FeasibilityContractError` and is
never coerced into zero, default or pass. An explicit INELIGIBLE/INVALID result
is a policy ``VETO``; an evidence UNAVAILABLE/MISSING result is an abstention,
not a veto.

The seam is pure: it reads no clock, environment, filesystem, database or
network, and it observes no input outside the supplied arguments. ``evaluation_time``
is the only timing input, and every thin adapter is called with an explicit
``evaluated_at=evaluation_time`` so no ``GateResult`` default clock is ever read.

No new numeric threshold is introduced; the seam only re-maps existing evaluator
outcomes. It grants no production, admission, allocation, risk, paper, order or
funded authority.

SHADOW / NON-AUTHORITATIVE. This seam is a research artifact. It is not wired
into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus, and it
writes no canonical evidence.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.opip.contracts import (
    FEASIBILITY_CHECK_ORDER,
    FeasibilityCheck,
    FeasibilityCheckName,
    FeasibilityCheckStatus,
    FeasibilityContractError,
    FeasibilityDecision,
    FeasibilityDisposition,
    FeasibilityPolicy,
    OpportunityEpisode,
    OpportunityLifecycleState,
    feasibility_decision_identity,
    feasibility_evidence_fingerprint,
    require_feasibility_utc,
)
from app.opip.decision.gates import evaluate_execution_gate, evaluate_margin_gate
from app.opip.decision.models import GateName, GateStatus, ReasonCode
from app.scanner import market_data_validation as market_data
from app.scanner import execution_validation as execution_evidence
from app.scanner.models import MarketSnapshot
from app.scanner.universe import BASE_ALIASES

#: Quote suffixes stripped when comparing venue instrument tokens, so
#: ``SOL/USD`` and ``SOLUSD`` compare equal.
_QUOTE_SUFFIXES = ("USDT", "USD")

#: The live absent-evidence sentinel the scanner prints when a snapshot carries
#: no market-data validation object. It is an explicit unavailability marker, not
#: a favorable result.
MARKET_DATA_UNAVAILABLE_SENTINEL = "UNAVAILABLE"

#: The exact SHORT margin status tokens the live scanner attaches in
#: ``app/scanner/margin_eligibility.py`` (``margin_validation_status``). Reused,
#: not re-invented.
MARGIN_ELIGIBLE = "ELIGIBLE"
MARGIN_INELIGIBLE = "INELIGIBLE"
MARGIN_UNAVAILABLE = "UNAVAILABLE"

_LONG = "LONG"
_SHORT = "SHORT"


def _require_policy(policy: object) -> FeasibilityPolicy:
    if not isinstance(policy, FeasibilityPolicy):
        raise FeasibilityContractError("policy must be a FeasibilityPolicy")
    return policy


def _require_episode(episode: object) -> OpportunityEpisode:
    """F5 evaluates exactly one ACTIVE F4 episode; any other state fails closed."""
    if not isinstance(episode, OpportunityEpisode):
        raise FeasibilityContractError(
            "episode must be an ACTIVE F4 OpportunityEpisode"
        )
    if episode.lifecycle_state is not OpportunityLifecycleState.ACTIVE:
        raise FeasibilityContractError(
            "F5 evaluates only an ACTIVE F4 episode; DEFERRED and TERMINAL fail closed"
        )
    return episode


def _require_snapshot(evidence: object) -> MarketSnapshot:
    if not isinstance(evidence, MarketSnapshot):
        raise FeasibilityContractError(
            "evidence must be a MarketSnapshot candidate carrying the "
            "pre-forecast feasibility inputs"
        )
    return evidence


def _direction(snapshot: MarketSnapshot) -> str:
    raw = getattr(snapshot, "trade_direction", None)
    if not isinstance(raw, str) or raw != raw.strip():
        raise FeasibilityContractError(
            "trade_direction must be a non-empty, whitespace-free string"
        )
    # The producer emits the exact uppercase tokens; a case-folded or unknown
    # token is malformed and fails closed rather than being normalized.
    if raw not in {_LONG, _SHORT}:
        raise FeasibilityContractError(
            f"trade_direction has an unsupported token: {raw!r}"
        )
    return raw


def _number_or_none(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeasibilityContractError(f"{field_name} must be numeric or None")
    try:
        number = float(value)
    except OverflowError as exc:
        raise FeasibilityContractError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise FeasibilityContractError(f"{field_name} must be finite")
    return number


def _int_or_none(
    value: Any, *, field_name: str, allow_none: bool = False
) -> int | None:
    if value is None:
        if allow_none:
            return None
        raise FeasibilityContractError(f"{field_name} must be an integer")
    if isinstance(value, bool) or not isinstance(value, int):
        raise FeasibilityContractError(f"{field_name} must be an integer")
    if value < 0:
        raise FeasibilityContractError(f"{field_name} must not be negative")
    return value


def _measurement_or_none(
    value: Any, *, field_name: str, require_finite: bool = True
) -> float | None:
    """Validate one optional raw measurement.

    A ``None`` is allowed and a bool/non-numeric is always malformed. A
    non-finite value is malformed unless ``require_finite`` is cleared; only the
    live validator's raw non-finite ``ticker_last`` on an already-rejected record
    clears it, so every other measurement is required to be finite. A value that
    cannot be represented as a float (for example a huge integer) is malformed.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeasibilityContractError(f"{field_name} must be numeric or None")
    try:
        number = float(value)
    except OverflowError as exc:
        raise FeasibilityContractError(f"{field_name} must be finite") from exc
    if require_finite and not math.isfinite(number):
        raise FeasibilityContractError(f"{field_name} must be finite")
    return number


def _lenient_float_token(value: Any) -> float | str | None:
    """A canonical fingerprint token for one raw float, preserving non-finite state.

    A finite number is returned as-is; non-finite values map to distinct
    canonical tokens (rather than being erased to ``None``), so an accepted
    non-finite ``ticker_last`` changes the fingerprint and the decision id; a
    value that cannot be represented as a float maps to a signed overflow token;
    a non-numeric value maps to ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return "NON_FINITE_POSITIVE" if value > 0 else "NON_FINITE_NEGATIVE"
    if math.isnan(number):
        return "NON_FINITE_NAN"
    if number == math.inf:
        return "NON_FINITE_POSITIVE"
    if number == -math.inf:
        return "NON_FINITE_NEGATIVE"
    return number


def _bool_field(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise FeasibilityContractError(f"{field_name} must be a bool")
    return value


def _text_list_field(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise FeasibilityContractError(f"{field_name} must be a list of text")
    return tuple(value)


def _validate_market_fields(
    validation: Any, *, allow_non_finite_ticker: bool
) -> None:
    """Validate the concrete market-data evidence structure; malformed fails closed.

    Structural fields (counts, the timestamp, the boolean flag and the text
    lists) are always validated, and every measurement except ``ticker_last`` is
    always required to be finite. ``ticker_last`` is only excused when
    ``allow_non_finite_ticker`` is set, because the live validator stores a raw
    non-finite ``ticker_last`` on a record it has already rejected.
    """
    _int_or_none(getattr(validation, "candle_count", None), field_name="candle_count")
    _int_or_none(
        getattr(validation, "latest_candle_timestamp", None),
        field_name="latest_candle_timestamp",
        allow_none=True,
    )
    _int_or_none(
        getattr(validation, "duplicate_timestamp_count", None),
        field_name="duplicate_timestamp_count",
    )
    _int_or_none(getattr(validation, "gap_count", None), field_name="gap_count")
    _int_or_none(
        getattr(validation, "invalid_ohlc_count", None),
        field_name="invalid_ohlc_count",
    )
    _int_or_none(
        getattr(validation, "non_finite_value_count", None),
        field_name="non_finite_value_count",
    )
    for name in (
        "latest_candle_age_seconds",
        "largest_gap_seconds",
        "latest_ohlc_close",
        "ticker_vs_ohlc_difference_pct",
    ):
        _measurement_or_none(getattr(validation, name, None), field_name=name)
    _measurement_or_none(
        getattr(validation, "ticker_last", None),
        field_name="ticker_last",
        require_finite=not allow_non_finite_ticker,
    )
    _bool_field(
        getattr(validation, "suspicious_spike_detected", None),
        field_name="suspicious_spike_detected",
    )
    _text_list_field(getattr(validation, "warnings", None), field_name="warnings")
    _text_list_field(
        getattr(validation, "rejection_reasons", None),
        field_name="rejection_reasons",
    )


def _lenient_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _lenient_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _lenient_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _lenient_text_tuple(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        return tuple(value)
    return None


def _canonical_evidence_summary(snapshot: MarketSnapshot) -> dict[str, Any]:
    """The normalized F5-required evidence inputs used for the fingerprint.

    This is deliberately lenient: it never raises, so building the fingerprint
    cannot pre-empt the ordered checks and mask a proven hard veto (a proven veto
    must short-circuit and be returned). A malformed required structure is
    detected by the check itself, when - and only when - that component is
    evaluated. Only well-typed primitives and primitive sequences are kept, so
    the fingerprint never depends on an object repr; a non-primitive or
    non-finite value is recorded as ``None`` for identity purposes.
    """
    market = getattr(snapshot, "market_data_validation", None)
    if market is None:
        market_summary: dict[str, Any] | None = None
    else:
        market_summary = {
            "status": _lenient_text(getattr(market, "status", None)),
            "qualified": _lenient_bool(getattr(market, "qualified", None)),
            "candle_count": _lenient_number(getattr(market, "candle_count", None)),
            "latest_candle_timestamp": _lenient_number(
                getattr(market, "latest_candle_timestamp", None)
            ),
            "latest_candle_age_seconds": _lenient_number(
                getattr(market, "latest_candle_age_seconds", None)
            ),
            "duplicate_timestamp_count": _lenient_number(
                getattr(market, "duplicate_timestamp_count", None)
            ),
            "gap_count": _lenient_number(getattr(market, "gap_count", None)),
            "invalid_ohlc_count": _lenient_number(
                getattr(market, "invalid_ohlc_count", None)
            ),
            "non_finite_value_count": _lenient_number(
                getattr(market, "non_finite_value_count", None)
            ),
            "largest_gap_seconds": _lenient_number(
                getattr(market, "largest_gap_seconds", None)
            ),
            "ticker_last": _lenient_float_token(getattr(market, "ticker_last", None)),
            "latest_ohlc_close": _lenient_number(
                getattr(market, "latest_ohlc_close", None)
            ),
            "ticker_vs_ohlc_difference_pct": _lenient_number(
                getattr(market, "ticker_vs_ohlc_difference_pct", None)
            ),
            "suspicious_spike_detected": _lenient_bool(
                getattr(market, "suspicious_spike_detected", None)
            ),
            "warnings": _lenient_text_tuple(getattr(market, "warnings", None)),
            "rejection_reasons": _lenient_text_tuple(
                getattr(market, "rejection_reasons", None)
            ),
        }

    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        execution_summary: dict[str, Any] | None = None
    else:
        execution_summary = {
            "status": _lenient_text(getattr(execution, "status", None)),
            "book_coverage_status": _lenient_text(
                getattr(execution, "book_coverage_status", None)
            ),
            "spread_bps": _lenient_number(getattr(execution, "spread_bps", None)),
            "buy_visible_coverage_pct": _lenient_number(
                getattr(execution, "buy_visible_coverage_pct", None)
            ),
            "sell_visible_coverage_pct": _lenient_number(
                getattr(execution, "sell_visible_coverage_pct", None)
            ),
            "buy_fully_covered": _lenient_bool(
                getattr(execution, "buy_fully_covered", None)
            ),
            "sell_fully_covered": _lenient_bool(
                getattr(execution, "sell_fully_covered", None)
            ),
            "short_round_trip_drag_pct": _lenient_number(
                getattr(
                    execution,
                    "estimated_visible_short_round_trip_market_drag_pct",
                    None,
                )
            ),
            "recent_trade_status": _lenient_text(
                getattr(execution, "recent_trade_status", None)
            ),
        }

    return {
        "direction": _lenient_text(getattr(snapshot, "trade_direction", None)),
        "symbol": _lenient_text(getattr(snapshot, "symbol", None)),
        "kraken_public_symbol": _lenient_text(
            getattr(snapshot, "kraken_public_symbol", None)
        ),
        "primary_pair": _lenient_text(getattr(snapshot, "primary_pair", None)),
        "market": market_summary,
        "margin_status": _lenient_text(
            getattr(snapshot, "margin_validation_status", None)
        ),
        "margin_eligible": _lenient_bool(getattr(snapshot, "margin_eligible", None)),
        "margin_venue_symbol": _lenient_text(
            getattr(snapshot, "margin_venue_symbol", None)
        ),
        "margin_max_leverage": _lenient_number(
            getattr(snapshot, "margin_max_leverage", None)
        ),
        "execution": execution_summary,
    }


def _instrument_token(value: Any) -> str | None:
    """Normalize one venue instrument token for identity comparison.

    Uppercase alphanumerics only, then the repository's existing Kraken base
    alias normalization (``BASE_ALIASES``: ``XBT`` -> ``BTC``, ``XDG`` ->
    ``DOGE``) is applied to the base of a USD/USDT pair, so F4's Kraken
    ``altname`` (``XBTUSD``/``XDGUSD``) compares equal to the scanner's canonical
    snapshot symbol (``BTC/USD``/``DOGE/USD``). A non-text or empty value yields
    ``None``.
    """
    if not isinstance(value, str) or value.strip() == "":
        return None
    token = "".join(character for character in value.upper() if character.isalnum())
    if not token:
        return None
    for quote in _QUOTE_SUFFIXES:
        if token.endswith(quote) and len(token) > len(quote):
            base = token[: -len(quote)]
            return BASE_ALIASES.get(base, base) + quote
    return token


def _require_instrument_correspondence(
    episode: OpportunityEpisode, snapshot: MarketSnapshot
) -> None:
    """The evidence snapshot must be for the episode's venue instrument.

    This is an identity-consistency guard, not a trading threshold: a
    foreign-market snapshot with otherwise usable feasibility fields fails closed
    rather than being stamped with the episode's lineage.
    """
    venue = _instrument_token(getattr(episode, "venue_instrument_id", None))
    if venue is None:
        raise FeasibilityContractError(
            "episode venue instrument id is not comparable to a market symbol"
        )
    # Every populated identifier must agree with the episode venue instrument, so
    # a snapshot whose symbol matches but whose public/primary pair identifies a
    # different instrument is refused rather than accepted on one match. A
    # populated identifier that cannot be normalized (non-string or no
    # alphanumerics) is itself malformed and fails closed rather than being
    # discarded.
    tokens: list[str] = []
    for attribute in ("symbol", "kraken_public_symbol", "primary_pair"):
        raw = getattr(snapshot, attribute, None)
        if raw is None or raw == "":
            continue
        token = _instrument_token(raw)
        if token is None:
            raise FeasibilityContractError(
                f"snapshot {attribute} is populated but not a usable instrument token"
            )
        tokens.append(token)
    if not tokens or any(token != venue for token in tokens):
        raise FeasibilityContractError(
            "evidence snapshot does not correspond to the episode venue instrument"
        )


def _check(
    name: FeasibilityCheckName,
    status: FeasibilityCheckStatus,
    reason: str,
) -> FeasibilityCheck:
    return FeasibilityCheck(name=name, status=status, reason=reason)


def _market_check(snapshot: MarketSnapshot) -> FeasibilityCheck:
    """Map the existing market-data validation evidence (no new threshold)."""
    validation = getattr(snapshot, "market_data_validation", None)
    if validation is None:
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "required market-data evidence is absent",
        )
    if not isinstance(validation, market_data.MarketDataValidation):
        raise FeasibilityContractError(
            "market-data evidence must be a MarketDataValidation"
        )
    status = getattr(validation, "status", None)
    qualified = getattr(validation, "qualified", None)
    if isinstance(status, bool) or not isinstance(status, str):
        raise FeasibilityContractError("market-data status must be a text token")
    if not isinstance(qualified, bool):
        raise FeasibilityContractError("market-data qualified must be a bool")

    if status == MARKET_DATA_UNAVAILABLE_SENTINEL:
        _validate_market_fields(validation, allow_non_finite_ticker=True)
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "market-data evidence is explicitly unavailable",
        )

    if status not in {
        market_data.PASS,
        market_data.WARN,
        market_data.REJECT,
    }:
        raise FeasibilityContractError(
            f"market-data status has an unsupported token: {status!r}"
        )

    if status == market_data.REJECT:
        if qualified:
            raise FeasibilityContractError(
                "contradictory market-data evidence: REJECT with qualified=True"
            )
        # An explicit invalidity is a VETO. The live validator can store a raw
        # non-finite ticker_last on a record it already rejected, so finite-ness
        # is not required here; structural fields are still validated.
        _validate_market_fields(validation, allow_non_finite_ticker=True)
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.VETO,
            "market-data validation rejected the candidate",
        )

    if not qualified:
        raise FeasibilityContractError(
            f"contradictory market-data evidence: {status} with qualified=False"
        )
    # A usable record must carry structurally valid, finite measurements.
    _validate_market_fields(validation, allow_non_finite_ticker=False)
    return _check(
        FeasibilityCheckName.MARKET_DATA,
        FeasibilityCheckStatus.PASS,
        f"market-data validation is usable ({status})",
    )


def _margin_check(
    snapshot: MarketSnapshot, evaluation_time: datetime
) -> FeasibilityCheck:
    """Reuse the thin margin adapter; LONG is NOT_APPLICABLE, never PASS."""
    direction = _direction(snapshot)
    if direction == _SHORT:
        raw = getattr(snapshot, "margin_validation_status", None)
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise FeasibilityContractError(
                "SHORT margin_validation_status must be a text token"
            )
        if raw != raw.strip():
            raise FeasibilityContractError(
                "SHORT margin_validation_status must be whitespace-free"
            )
        # The producer emits exact uppercase tokens; a case-folded or unknown
        # token is malformed and fails closed rather than being normalized.
        if raw not in {
            MARGIN_ELIGIBLE,
            MARGIN_INELIGIBLE,
            MARGIN_UNAVAILABLE,
        }:
            raise FeasibilityContractError(
                f"SHORT margin_validation_status has an unsupported token: {raw!r}"
            )
        # The live filter keeps a SHORT only when margin_eligible is true, so an
        # ELIGIBLE status with margin_eligible False (or the reverse) is
        # contradictory evidence and must fail closed rather than pass.
        eligible_flag = getattr(snapshot, "margin_eligible", None)
        if not isinstance(eligible_flag, bool):
            raise FeasibilityContractError("SHORT margin_eligible must be a bool")
        if eligible_flag is not (raw == MARGIN_ELIGIBLE):
            raise FeasibilityContractError(
                "SHORT margin_validation_status contradicts the margin_eligible flag"
            )
        # The live producer writes a text venue symbol and a finite numeric
        # effective ceiling; a malformed eligible record fails closed.
        venue_symbol = getattr(snapshot, "margin_venue_symbol", None)
        if venue_symbol is not None and not isinstance(venue_symbol, str):
            raise FeasibilityContractError(
                "SHORT margin_venue_symbol must be text or None"
            )
        leverage = getattr(snapshot, "margin_max_leverage", None)
        if leverage is not None:
            _measurement_or_none(leverage, field_name="margin_max_leverage")
        if raw == MARGIN_ELIGIBLE:
            if venue_symbol is None or venue_symbol == "":
                raise FeasibilityContractError(
                    "an ELIGIBLE SHORT margin record requires a margin_venue_symbol"
                )
            if leverage is None:
                raise FeasibilityContractError(
                    "an ELIGIBLE SHORT margin record requires margin_max_leverage"
                )

    result = evaluate_margin_gate(snapshot, evaluated_at=evaluation_time)

    if result.gate is not GateName.MARGIN_ELIGIBILITY:
        raise FeasibilityContractError("margin adapter returned an unexpected gate")

    if result.status is GateStatus.SKIPPED:
        return _check(
            FeasibilityCheckName.MARGIN_ELIGIBILITY,
            FeasibilityCheckStatus.NOT_APPLICABLE,
            "LONG candidate does not use the margin venue",
        )
    if result.status is GateStatus.PASS:
        return _check(
            FeasibilityCheckName.MARGIN_ELIGIBILITY,
            FeasibilityCheckStatus.PASS,
            "SHORT candidate is margin eligible",
        )
    if result.status is GateStatus.FAIL:
        if result.reason_code is ReasonCode.MARGIN_VALIDATION_UNAVAILABLE:
            return _check(
                FeasibilityCheckName.MARGIN_ELIGIBILITY,
                FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
                "SHORT margin eligibility discovery is unavailable",
            )
        if result.reason_code is ReasonCode.MARGIN_INELIGIBLE:
            return _check(
                FeasibilityCheckName.MARGIN_ELIGIBILITY,
                FeasibilityCheckStatus.VETO,
                "SHORT candidate is margin ineligible",
            )
    raise FeasibilityContractError(
        "margin adapter returned an unrecognized outcome"
    )


def _validate_execution_fields(execution: Any) -> None:
    """Validate the F5-required execution fields; malformed fails closed."""
    if not isinstance(execution, execution_evidence.ExecutionValidation):
        raise FeasibilityContractError(
            "execution evidence must be an ExecutionValidation"
        )
    status = execution.status
    if isinstance(status, bool) or not isinstance(status, str):
        raise FeasibilityContractError("execution status must be a text token")
    if status not in {
        execution_evidence.VALID,
        execution_evidence.INVALID,
        execution_evidence.UNAVAILABLE,
    }:
        raise FeasibilityContractError(
            f"execution status has an unsupported token: {status!r}"
        )
    coverage = getattr(execution, "book_coverage_status", None)
    if status == execution_evidence.VALID:
        # A usable record must prove liquidity: an explicitly unavailable book
        # cannot be a VALID record, and the fields the quality route reads must
        # be present.
        if coverage not in {
            execution_evidence.COMPLETE,
            execution_evidence.PARTIAL,
            execution_evidence.INSUFFICIENT,
        }:
            raise FeasibilityContractError(
                "a VALID execution record must carry measured book coverage"
            )
        for field_name in (
            "spread_bps",
            "buy_visible_coverage_pct",
            "sell_visible_coverage_pct",
        ):
            if getattr(execution, field_name, None) is None:
                raise FeasibilityContractError(
                    f"a VALID execution record requires {field_name}"
                )
    else:
        if coverage != execution_evidence.UNAVAILABLE:
            raise FeasibilityContractError(
                "an UNAVAILABLE/INVALID execution record must carry UNAVAILABLE coverage"
            )
    recent = getattr(execution, "recent_trade_status", None)
    if recent not in {
        execution_evidence.FRESH,
        execution_evidence.WARN,
        execution_evidence.UNAVAILABLE,
    }:
        raise FeasibilityContractError(
            f"execution recent_trade_status has an unsupported token: {recent!r}"
        )
    buy_covered = _bool_field(
        getattr(execution, "buy_fully_covered", None),
        field_name="execution.buy_fully_covered",
    )
    sell_covered = _bool_field(
        getattr(execution, "sell_fully_covered", None),
        field_name="execution.sell_fully_covered",
    )
    # The producer assigns COMPLETE/PARTIAL only when both sides are fully
    # covered and INSUFFICIENT when either side is not; a contradictory flag set
    # fails closed rather than being accepted by the LONG adapter.
    if status == execution_evidence.VALID:
        if coverage in {execution_evidence.COMPLETE, execution_evidence.PARTIAL} and not (
            buy_covered and sell_covered
        ):
            raise FeasibilityContractError(
                "COMPLETE/PARTIAL coverage requires both sides fully covered"
            )
        if coverage == execution_evidence.INSUFFICIENT and buy_covered and sell_covered:
            raise FeasibilityContractError(
                "INSUFFICIENT coverage requires an uncovered side"
            )
    for field_name in (
        "spread_bps",
        "buy_visible_coverage_pct",
        "sell_visible_coverage_pct",
        "estimated_visible_short_round_trip_market_drag_pct",
    ):
        _number_or_none(
            getattr(execution, field_name, None), field_name=f"execution.{field_name}"
        )


def _has_btnl_venue_provenance(snapshot: MarketSnapshot) -> bool:
    """True when the snapshot carries the Bitnomial margin venue provenance.

    The live SHORT route refreshes the BTNL margin book onto the snapshot; the
    offline route must not trust spot evidence (which lacks this provenance) as
    if it were the BTNL book the SHORT quality thresholds are defined for.
    """
    venue = getattr(snapshot, "margin_venue_symbol", None)
    return isinstance(venue, str) and ":BTNL" in venue.upper()


def _execution_check(
    snapshot: MarketSnapshot, evaluation_time: datetime
) -> FeasibilityCheck:
    """Reuse the thin execution adapter on its OFFLINE route (no refresh)."""
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "required execution validation evidence is absent",
        )

    _validate_execution_fields(execution)
    status = getattr(execution, "status", None)
    if status == execution_evidence.UNAVAILABLE:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "execution validation evidence is explicitly unavailable",
        )
    # A SHORT is decided on the BTNL margin book. Without the BTNL provenance
    # marker the attached execution evidence cannot be trusted to be that book,
    # so this is missing evidence, not a favorable result. An explicit structural
    # INVALID is still a VETO below.
    if status != execution_evidence.INVALID and _direction(snapshot) == _SHORT:
        if not _has_btnl_venue_provenance(snapshot):
            return _check(
                FeasibilityCheckName.EXECUTION_LIQUIDITY,
                FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
                "SHORT execution evidence lacks BTNL venue provenance",
            )

    result = evaluate_execution_gate(snapshot, evaluated_at=evaluation_time)

    if result.gate is not GateName.EXECUTION_VALIDATION:
        raise FeasibilityContractError("execution adapter returned an unexpected gate")

    if result.status is GateStatus.PASS:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.PASS,
            f"execution evidence is usable ({status})",
        )
    if result.status is GateStatus.FAIL:
        if result.reason_code is ReasonCode.EXECUTION_VALIDATION_FAILED:
            return _check(
                FeasibilityCheckName.EXECUTION_LIQUIDITY,
                FeasibilityCheckStatus.VETO,
                "structural execution validation returned INVALID",
            )
        if result.reason_code is ReasonCode.SHORT_EXECUTION_QUALITY_FAILED:
            return _check(
                FeasibilityCheckName.EXECUTION_LIQUIDITY,
                FeasibilityCheckStatus.VETO,
                "SHORT execution-quality evaluator rejected the candidate",
            )
    if result.status is GateStatus.ERROR and (
        result.reason_code is ReasonCode.GATE_EVALUATION_ERROR
    ):
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "execution validation evidence is missing",
        )
    raise FeasibilityContractError(
        "execution adapter returned an unrecognized outcome"
    )


def _run_check(
    name: FeasibilityCheckName,
    snapshot: MarketSnapshot,
    evaluation_time: datetime,
) -> FeasibilityCheck:
    if name is FeasibilityCheckName.MARKET_DATA:
        return _market_check(snapshot)
    if name is FeasibilityCheckName.MARGIN_ELIGIBILITY:
        return _margin_check(snapshot, evaluation_time)
    if name is FeasibilityCheckName.EXECUTION_LIQUIDITY:
        return _execution_check(snapshot, evaluation_time)
    raise FeasibilityContractError(f"unknown feasibility check: {name!r}")


def evaluate_feasibility(
    episode: OpportunityEpisode,
    evidence: MarketSnapshot,
    evaluation_time: datetime,
    policy: FeasibilityPolicy,
) -> FeasibilityDecision:
    """Evaluate one ACTIVE F4 episode against the F5 required checks. Pure.

    Deterministic sequential aggregation in the recorded live hard-filter order.
    An evaluated hard ``VETO`` yields overall ``VETO`` and short-circuits the
    remaining checks, so a proven veto is never downgraded to an abstention.
    Otherwise any ``INSUFFICIENT_EVIDENCE`` yields an abstention; otherwise all
    applicable checks are ``PASS`` and the episode is ``FEASIBLE``.
    ``NOT_APPLICABLE`` never blocks.
    """
    policy = _require_policy(policy)
    evaluation_time = require_feasibility_utc(
        evaluation_time, field_name="evaluation_time"
    )
    episode = _require_episode(episode)
    snapshot = _require_snapshot(evidence)
    _require_instrument_correspondence(episode, snapshot)

    # The fingerprint is lenient by design: it must never pre-empt the ordered
    # checks (a proven hard veto must short-circuit and be returned).
    fingerprint = feasibility_evidence_fingerprint(
        _canonical_evidence_summary(snapshot)
    )

    evaluated: list[FeasibilityCheck] = []
    disposition = FeasibilityDisposition.FEASIBLE
    for name in FEASIBILITY_CHECK_ORDER:
        check = _run_check(name, snapshot, evaluation_time)
        evaluated.append(check)
        if check.status is FeasibilityCheckStatus.VETO:
            disposition = FeasibilityDisposition.VETO
            break
    else:
        if any(
            check.status is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE
            for check in evaluated
        ):
            disposition = FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    decision_id = feasibility_decision_identity(
        decision_schema_version=policy.decision_schema_version,
        episode_id=episode.episode_id,
        source_claim_id=episode.source_claim_id,
        instrument_version_id=episode.instrument_version_id,
        venue_instrument_id=episode.venue_instrument_id,
        detector_snapshot_id=episode.snapshot_id,
        evidence_fingerprint=fingerprint,
        evaluation_time=evaluation_time,
        disposition=disposition,
        checks=tuple(evaluated),
        feasibility_version=policy.feasibility_version,
        policy_version=policy.policy_version,
    )

    return FeasibilityDecision(
        decision_id=decision_id,
        decision_schema_version=policy.decision_schema_version,
        feasibility_version=policy.feasibility_version,
        policy_version=policy.policy_version,
        episode_id=episode.episode_id,
        source_claim_id=episode.source_claim_id,
        instrument_version_id=episode.instrument_version_id,
        venue_instrument_id=episode.venue_instrument_id,
        detector_snapshot_id=episode.snapshot_id,
        evaluation_time=evaluation_time,
        evidence_fingerprint=fingerprint,
        disposition=disposition,
        checks=tuple(evaluated),
    )


__all__ = [
    "MARGIN_ELIGIBLE",
    "MARGIN_INELIGIBLE",
    "MARGIN_UNAVAILABLE",
    "MARKET_DATA_UNAVAILABLE_SENTINEL",
    "evaluate_feasibility",
]
