"""R4-B2 Slice 3A: durable FeasibilityEvidence record codec (AC-017).

Proves the durable wire schema round-trips the exact typed F5 evidence, and that
the exact-content ``payload_hash`` catches mutations the frozen F5 semantic
``evidence_fingerprint`` deliberately ignores.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from app.opip.contracts.feasibility_evidence import (
    FeasibilityEvidence,
    feasibility_evidence_fingerprint,
)
from app.opip.feasibility_evidence_record import (
    FeasibilityEvidenceRecordError,
    build_feasibility_evidence_payload,
    feasibility_evidence_from_payload,
    feasibility_evidence_payload_hash,
    validate_feasibility_evidence_payload,
)
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_data_validation import MarketDataValidation

pytestmark = pytest.mark.acceptance

EVAL = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 10, 2, 11, 59, tzinfo=timezone.utc)


def _market() -> MarketDataValidation:
    return MarketDataValidation(
        status="WARN",
        qualified=True,
        warnings=["thin 24h volume"],
        rejection_reasons=[],
        candle_count=200,
        latest_candle_timestamp=1759400000,
        latest_candle_age_seconds=120.0,
        duplicate_timestamp_count=0,
        gap_count=1,
        largest_gap_seconds=120.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=100.0,
        latest_ohlc_close=100.1,
        ticker_vs_ohlc_difference_pct=0.1,
        suspicious_spike_detected=False,
    )


def _execution(*, best_bid: float = 99.9, buy_vwap: float = 100.2) -> ExecutionValidation:
    return ExecutionValidation(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        best_bid=best_bid,
        best_ask=100.1,
        mid_price=100.0,
        absolute_spread=0.2,
        spread_pct=0.2,
        spread_bps=20.0,
        visible_bid_notional=5000.0,
        visible_ask_notional=5000.0,
        bid_depth_025_usd=2500.0,
        ask_depth_025_usd=2500.0,
        bid_depth_025_complete=True,
        ask_depth_025_complete=True,
        bid_depth_050_usd=5000.0,
        ask_depth_050_usd=5000.0,
        bid_depth_050_complete=True,
        ask_depth_050_complete=True,
        validation_notional_usd=500.0,
        buy_vwap=buy_vwap,
        sell_vwap=99.8,
        buy_market_impact_pct=0.3,
        sell_market_impact_pct=0.3,
        buy_visible_coverage_pct=100.0,
        sell_visible_coverage_pct=100.0,
        buy_fully_covered=True,
        sell_fully_covered=True,
        estimated_visible_round_trip_market_drag_pct=0.4,
        estimated_visible_short_round_trip_market_drag_pct=0.5,
        recent_trade_status="FRESH",
        latest_trade_price=100.0,
        latest_trade_age_seconds=5.0,
        recent_trade_count=42,
    )


def _evidence(
    *,
    direction: str = "LONG",
    availability: str = "AVAILABLE",
    market: object | None = None,
    execution: object | None = None,
    margin_status: str | None = None,
    margin_eligible: bool | None = None,
    margin_venue_symbol: str | None = None,
    margin_max_leverage: float | None = None,
    missingness: tuple[str, ...] = (),
    exec_override: ExecutionValidation | None = None,
) -> FeasibilityEvidence:
    return FeasibilityEvidence(
        instrument_version_id="IV:SOLUSD:1",
        venue_instrument_id="SOLUSD",
        direction=direction,
        evaluation_time=EVAL,
        source_cutoff=CUTOFF,
        source_snapshot_id="EPSNAP:abc123",
        source_evidence_refs=("OBS:1", "OBS:2"),
        market_data_validation=_market() if market is None else market,
        margin_validation_status=margin_status,
        margin_eligible=margin_eligible,
        margin_venue_symbol=margin_venue_symbol,
        margin_max_leverage=margin_max_leverage,
        execution_validation=(exec_override if exec_override is not None else _execution())
        if execution is None
        else execution,
        availability=availability,
        missingness=missingness,
        kraken_public_symbol="SOLUSD",
        primary_pair="SOLUSD",
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_ac_017_long_round_trip_is_exact():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a LONG record round-trips to the exact typed evidence."""
    evidence = _evidence(direction="LONG")
    payload = build_feasibility_evidence_payload(evidence)
    restored = feasibility_evidence_from_payload(payload)
    assert isinstance(restored, FeasibilityEvidence)
    assert restored == evidence


def test_ac_017_short_round_trip_is_exact():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a SHORT record (with margin + BTNL venue identity) round-trips exactly."""
    evidence = _evidence(
        direction="SHORT",
        margin_status="ELIGIBLE",
        margin_eligible=True,
        margin_venue_symbol="SOLUSD:BTNL",
        margin_max_leverage=2.0,
    )
    payload = build_feasibility_evidence_payload(evidence)
    restored = feasibility_evidence_from_payload(payload)
    assert restored == evidence
    assert restored.margin_venue_symbol == "SOLUSD:BTNL"


def test_ac_017_fingerprint_survives_round_trip():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: the F5 fingerprint is preserved exactly across the durable round-trip."""
    evidence = _evidence()
    payload = build_feasibility_evidence_payload(evidence)
    restored = feasibility_evidence_from_payload(payload)
    assert restored.evidence_fingerprint == evidence.evidence_fingerprint
    assert feasibility_evidence_fingerprint(restored) == feasibility_evidence_fingerprint(evidence)
    assert payload["evidence_fingerprint"] == evidence.evidence_fingerprint


def test_ac_017_non_finite_ticker_last_round_trips():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a non-finite ticker_last F5 admits as a token is persisted and reconstructed, not silently dropped."""
    evidence = _evidence(market=dataclasses.replace(_market(), ticker_last=float("inf")))
    payload = build_feasibility_evidence_payload(evidence)
    # Non-finite numbers cannot be canonically serialized, so the token is stored.
    assert payload["evidence"]["market_data_validation"]["ticker_last"] == "inf"
    restored = feasibility_evidence_from_payload(payload)
    assert restored.market_data_validation.ticker_last == float("inf")
    assert feasibility_evidence_fingerprint(restored) == feasibility_evidence_fingerprint(evidence)
    assert build_feasibility_evidence_payload(restored)["payload_hash"] == payload["payload_hash"]


# ---------------------------------------------------------------------------
# The core distinction: exact-content hash vs semantic fingerprint
# ---------------------------------------------------------------------------

#: Fields the frozen F5 summary deliberately ignores, each mutated to a value
#: distinct from the baseline.
_MUTATED_EXECUTION_VALUES: dict[str, object] = {
    "best_bid": 1.0,
    "mid_price": 1.0,
    "bid_depth_025_usd": 1.0,
    "bid_depth_025_complete": False,
    "buy_vwap": 1.0,
    "warnings": ["mutated"],
}


@pytest.mark.parametrize("field_name", sorted(_MUTATED_EXECUTION_VALUES))
def test_ac_017_payload_hash_catches_summary_excluded_mutation(field_name):
    """ATDD-R4-B2-controlled-paper-activation/AC-017: mutating a field OUTSIDE the F5 summary changes payload_hash and is rejected, while the recomputed F5 fingerprint is unchanged."""
    baseline = _evidence(exec_override=_execution())
    mutated_exec = dataclasses.replace(
        _execution(), **{field_name: _MUTATED_EXECUTION_VALUES[field_name]}
    )
    mutated = _evidence(exec_override=mutated_exec)

    # Genuinely outside the summary: both fingerprints are recomputed and equal.
    assert feasibility_evidence_fingerprint(baseline) == feasibility_evidence_fingerprint(mutated)

    payload = build_feasibility_evidence_payload(baseline)
    original_hash = payload["payload_hash"]
    mutated_payload = build_feasibility_evidence_payload(mutated)
    assert mutated_payload["payload_hash"] != original_hash

    # The exact-content hash rejects the in-place mutation.
    payload["evidence"]["execution_validation"][field_name] = (
        mutated_payload["evidence"]["execution_validation"][field_name]
    )
    with pytest.raises(FeasibilityEvidenceRecordError, match="payload_hash"):
        validate_feasibility_evidence_payload(payload)
    assert build_feasibility_evidence_payload(baseline)["payload_hash"] == original_hash


def test_ac_017_fingerprint_ignores_best_bid_but_hash_does_not():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: two evidences differing only in an execution field the F5 summary ignores share a fingerprint but not a payload_hash."""
    a = _evidence(exec_override=_execution(best_bid=99.9))
    b = _evidence(exec_override=_execution(best_bid=98.0))
    assert feasibility_evidence_fingerprint(a) == feasibility_evidence_fingerprint(b)
    assert build_feasibility_evidence_payload(a)["payload_hash"] != build_feasibility_evidence_payload(b)["payload_hash"]


# ---------------------------------------------------------------------------
# Tamper + structural rejection
# ---------------------------------------------------------------------------


def test_ac_017_tampered_margin_evidence_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: tampering the margin evidence is rejected."""
    payload = build_feasibility_evidence_payload(
        _evidence(direction="SHORT", margin_status="ELIGIBLE", margin_eligible=True, margin_venue_symbol="SOLUSD:BTNL", margin_max_leverage=2.0)
    )
    payload["evidence"]["margin_eligible"] = False
    with pytest.raises(FeasibilityEvidenceRecordError, match="payload_hash"):
        validate_feasibility_evidence_payload(payload)


def test_ac_017_missing_required_field_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a missing body field is rejected, never defaulted."""
    payload = build_feasibility_evidence_payload(_evidence())
    del payload["evidence"]["execution_validation"]
    with pytest.raises(FeasibilityEvidenceRecordError, match="unexpected field set"):
        validate_feasibility_evidence_payload(payload)


def test_ac_017_unknown_field_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: an unknown field is rejected."""
    payload = build_feasibility_evidence_payload(_evidence())
    payload["evidence"]["surprise"] = 1
    with pytest.raises(FeasibilityEvidenceRecordError, match="unexpected field set"):
        validate_feasibility_evidence_payload(payload)

    wrapper_extra = build_feasibility_evidence_payload(_evidence())
    wrapper_extra["extra"] = 1
    with pytest.raises(FeasibilityEvidenceRecordError, match="unexpected field set"):
        validate_feasibility_evidence_payload(wrapper_extra)


def test_ac_017_wrong_type_and_non_finite_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a wrong-typed or non-finite persisted value is rejected."""
    wrong_type = build_feasibility_evidence_payload(_evidence())
    wrong_type["evidence"]["execution_validation"]["best_bid"] = "99.9"
    with pytest.raises(FeasibilityEvidenceRecordError):
        validate_feasibility_evidence_payload(wrong_type)

    non_finite = build_feasibility_evidence_payload(_evidence())
    non_finite["evidence"]["execution_validation"]["best_bid"] = float("inf")
    with pytest.raises(FeasibilityEvidenceRecordError, match="finite"):
        validate_feasibility_evidence_payload(non_finite)


@pytest.mark.parametrize(
    ("nested", "field_name"),
    [
        ("market_data_validation", "status"),
        ("execution_validation", "status"),
        ("execution_validation", "book_coverage_status"),
        ("execution_validation", "recent_trade_status"),
    ],
)
def test_ac_017_invalid_enum_and_status_tokens_rejected(nested, field_name):
    """ATDD-R4-B2-controlled-paper-activation/AC-017: an invalid enum/status token is rejected even when the content hash is re-signed to match."""
    payload = build_feasibility_evidence_payload(_evidence())
    payload["evidence"][nested][field_name] = "GARBAGE"
    payload["payload_hash"] = feasibility_evidence_payload_hash(payload["evidence"])
    with pytest.raises(FeasibilityEvidenceRecordError, match="token"):
        validate_feasibility_evidence_payload(payload)


def test_ac_017_bad_fingerprint_and_bad_hash_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: an invalid fingerprint or payload_hash is rejected."""
    # Body fingerprint mutated alone -> the wrapper disagrees.
    body_only = build_feasibility_evidence_payload(_evidence())
    body_only["evidence"]["evidence_fingerprint"] = "FEV:deadbeef"
    with pytest.raises(FeasibilityEvidenceRecordError, match="wrapper evidence_fingerprint"):
        validate_feasibility_evidence_payload(body_only)

    # Body and wrapper fingerprints agree but are wrong -> the exact-content hash
    # still catches the change.
    both = build_feasibility_evidence_payload(_evidence())
    both["evidence"]["evidence_fingerprint"] = "FEV:deadbeef"
    both["evidence_fingerprint"] = "FEV:deadbeef"
    with pytest.raises(FeasibilityEvidenceRecordError, match="payload_hash"):
        validate_feasibility_evidence_payload(both)

    bad_wrapper_fingerprint = build_feasibility_evidence_payload(_evidence())
    bad_wrapper_fingerprint["evidence_fingerprint"] = "FEV:deadbeef"
    with pytest.raises(FeasibilityEvidenceRecordError, match="wrapper evidence_fingerprint"):
        validate_feasibility_evidence_payload(bad_wrapper_fingerprint)

    bad_hash = build_feasibility_evidence_payload(_evidence())
    bad_hash["payload_hash"] = "FEVH:deadbeef"
    with pytest.raises(FeasibilityEvidenceRecordError, match="payload_hash"):
        validate_feasibility_evidence_payload(bad_hash)


def test_ac_017_bad_datetime_rejected():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a malformed or naive timestamp is rejected by the timestamp guard itself, not by a stale hash."""
    naive = build_feasibility_evidence_payload(_evidence())
    naive["evidence"]["evaluation_time"] = "2026-10-02T12:00:00"
    naive["payload_hash"] = feasibility_evidence_payload_hash(naive["evidence"])
    with pytest.raises(FeasibilityEvidenceRecordError, match="evaluation_time"):
        validate_feasibility_evidence_payload(naive)

    malformed = build_feasibility_evidence_payload(_evidence())
    malformed["evidence"]["source_cutoff"] = "not-a-timestamp"
    malformed["payload_hash"] = feasibility_evidence_payload_hash(malformed["evidence"])
    with pytest.raises(FeasibilityEvidenceRecordError, match="source_cutoff"):
        validate_feasibility_evidence_payload(malformed)


def test_ac_017_non_canonical_timestamp_rejected_consistently():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: a non-canonical instant fails closed on both the validator and reconstruction, never accepted on write and rejected on read."""
    payload = build_feasibility_evidence_payload(_evidence())
    payload["evidence"]["evaluation_time"] = "2026-10-02T12:00:00+00:00"
    payload["payload_hash"] = feasibility_evidence_payload_hash(payload["evidence"])
    with pytest.raises(FeasibilityEvidenceRecordError):
        validate_feasibility_evidence_payload(payload)
    with pytest.raises(FeasibilityEvidenceRecordError):
        feasibility_evidence_from_payload(payload)


def test_ac_017_reconstruction_fails_closed_on_hash_mismatch():
    """ATDD-R4-B2-controlled-paper-activation/AC-017: reconstruction re-derives the payload hash and rejects a stale one even if structure validates."""
    payload = build_feasibility_evidence_payload(_evidence())
    # A structurally valid but content-changed record whose stored hash is stale.
    payload["evidence"]["execution_validation"]["best_bid"] = 1.234
    with pytest.raises(FeasibilityEvidenceRecordError):
        feasibility_evidence_from_payload(payload)
