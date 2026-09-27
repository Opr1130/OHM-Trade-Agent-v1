"""Profit Intelligence / Dashboard v2 tests.

These tests assert *behaviour and truthfulness*, not implementation shape:

* availability semantics are never collapsed - a fact that cannot exist is
  ``NOT_APPLICABLE`` and a fact that should exist but was not readable is
  ``UNAVAILABLE``, and neither is ever rendered as a measured ``0``;
* conservation is re-checked rather than trusted, so a self-inconsistent
  economic row is reported instead of displayed;
* the lineage scope boundary is declared, not inferred from a missing key;
* the read-only surface is GET-only and reaches canonical evidence only through
  a read-only reader;
* an unreadable store is reported, never disguised as an empty healthy result.

No network, no exchange, no live provider and no wall-clock dependency: every
fixture is deterministic.
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.api import cockpit
from app.opip.canonical.models import PaperV2Ledger, PaperV2LedgerEntry
from app.opip.cockpit.ledger import (
    PaperLedger,
    ReconciledPaperTrade,
    build_ledger,
    build_trade_row,
)
from app.opip.cockpit.trust import Completeness
from app.opip.contracts.paper_metrics import PAPER_METRICS
from app.opip.profit_intelligence import (
    EVIDENCE_GAPS,
    LINEAGE_STAGE_ORDER,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_SAFEGUARDS,
    EconomicComponent,
    FactAvailability,
    LineageStage,
    MissedOpportunityCause,
    build_economic_integrity,
    build_lineage_for_trade,
    build_profit_intelligence_overview,
    build_trade_lineage,
    net_pnl_reconciles,
    unavailable_profit_intelligence,
)
from app.opip.profit_intelligence import semantics as pi_semantics
from app.opip.profit_intelligence.lineage import LINEAGE_SCOPE
from app.opip.profit_intelligence.read_model import (
    PROFIT_INTELLIGENCE_READ_MODEL_VERSION,
)

_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _package() -> pathlib.Path:
    """The Profit Intelligence package directory under test."""
    return (
        pathlib.Path(cockpit.__file__).parent.parent
        / "opip"
        / "profit_intelligence"
    )


def _exact(offset_seconds: int) -> dict:
    """Canonical EXACT temporal evidence, the only precision that yields a point."""
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": (_NOW + timedelta(seconds=offset_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _entry(
    *,
    trade_suffix: str = "a" * 64,
    quote_currency: str = "USD",
    direction: str = "LONG",
    fully_filled: bool = True,
    final_verified: bool = True,
    gross: float = 50.0,
    costs: float = 0.2,
    net: float | None = None,
    fee_cost: float = 0.1,
    spread_cost: float = 0.0,
    slippage_cost: float = 0.0,
    other_cost: float = 0.0,
    with_decision_context: bool = True,
    with_policy: bool = True,
    with_intent_time: bool = True,
    with_attempt_time: bool = True,
    with_protection: bool = True,
    with_exit: bool = True,
    event_ids: tuple[str, ...] = ("e1", "e2", "e3"),
) -> PaperV2LedgerEntry:
    """One canonical Paper-v2 ledger entry, deterministic and JSON-safe."""
    realised_net = gross - costs if net is None else net
    entry_qty = 5.0 if fully_filled else 2.5
    return PaperV2LedgerEntry(
        paper_trade_id="PTV2:" + trade_suffix,
        quote_currency=quote_currency,
        native_symbol="BTC/USD",
        decision_context_id="DCTX:1" if with_decision_context else None,
        policy_version="gate-v1" if with_policy else None,
        policy_fingerprint="fp1" if with_policy else None,
        evaluation_time=_exact(0)["occurred_at"],
        entry_quantity=entry_qty,
        exited_quantity=(entry_qty if with_exit else 0.0),
        remaining_quantity=0.0 if with_exit else entry_qty,
        entry_fills=(
            {
                "side": "BUY",
                "quantity": entry_qty,
                "price": 100.0,
                "fee_cost": fee_cost,
                "spread_cost": spread_cost,
                "slippage_cost": slippage_cost,
                "other_supported_cost": other_cost,
                "fill_time": _exact(0),
                "economic_model_version": "opip-paper-economics-v2",
            },
        ),
        exit_fills=(
            (
                {
                    "side": "SELL",
                    "quantity": entry_qty,
                    "price": 110.0,
                    "fee_cost": fee_cost,
                    "spread_cost": 0.0,
                    "slippage_cost": 0.0,
                    "other_supported_cost": 0.0,
                    "fill_time": _exact(600),
                    "economic_model_version": "opip-paper-economics-v2",
                },
            )
            if with_exit
            else ()
        ),
        first_entry_fill_time=_exact(0),
        last_exit_fill_time=_exact(600) if with_exit else None,
        entry_intent_time=_exact(-5) if with_intent_time else None,
        entry_attempt_time=_exact(-3) if with_attempt_time else None,
        entry_order_intent={"requested_quantity": 5.0},
        protection_plan=(
            {
                "protection_plan_id": "PPLAN:1",
                "plan_seq": 0,
                "stop_price": 90.0,
                "max_hold_seconds": 3600,
                "targets": [{"target_id": "TP1", "price": 110.0, "fraction": 1.0}],
            }
            if with_protection
            else None
        ),
        latest_reconciliation={
            "reconciliation_seq": 1,
            "terminal_reconciliation_state": "FINAL_VERIFIED",
            "position_state": "FLAT",
            "realized_gross_pnl": gross,
            "recorded_execution_costs": costs,
            "realized_net_pnl": realised_net,
        },
        final_verified=final_verified,
        event_ids=event_ids,
    )


def _row(**kwargs) -> ReconciledPaperTrade:
    return build_trade_row(_entry(**kwargs))


def _no_fill_row(
    *,
    suffix: str = "0" * 64,
    quote_currency: str = "USD",
    execution_costs: float = 0.0,
    net_pnl: float = 0.0,
) -> ReconciledPaperTrade:
    """A genuine no-fill trade: no fills, no entry quantity, no exit.

    Built directly (rather than through a fixture that still records a fill) so a
    no-fill assertion actually exercises the no-fill population.
    """
    return ReconciledPaperTrade(
        paper_trade_id="PTV2:" + suffix,
        quote_currency=quote_currency,
        entry_quantity=0.0,
        exited_quantity=0.0,
        remaining_quantity=0.0,
        first_entry_fill_at=None,
        gross_pnl=0.0,
        execution_costs=execution_costs,
        net_pnl=net_pnl,
    )


def _canonical(*entries: PaperV2LedgerEntry) -> PaperV2Ledger:
    """A healthy canonical ledger over ``PaperV2LedgerEntry`` facts.

    This is what a read-only reader returns, so it is what ``_ReadOnlyClient``
    wraps and what the cockpit derivation consumes.
    """
    return PaperV2Ledger(status="OK", entries=entries)


def _ledger(*entries: PaperV2LedgerEntry) -> PaperLedger:
    """The derived analytical ledger the read model consumes.

    Built through the production derivation rather than hand-constructed, so the
    tests exercise the same path the API uses.
    """
    return build_ledger(_canonical(*entries))


class _ReadOnlyClient:
    """A canonical read-only reader stub, mirroring ``CanonicalWriter.for_reads``."""

    def __init__(self, ledger: PaperV2Ledger) -> None:
        self._ledger = ledger
        self.closed = False

    def paper_v2_ledger(self) -> PaperV2Ledger:
        return self._ledger

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _bypass_secret(monkeypatch):
    monkeypatch.setattr(cockpit, "_require_secret", lambda value: None)


# ---------------------------------------------------------------------------
# Semantic contract
# ---------------------------------------------------------------------------


def test_fact_availability_keeps_four_distinct_values():
    """Known/derived/absent/impossible must stay four separate truths."""
    assert {item.value for item in FactAvailability} == {
        "KNOWN",
        "DERIVED",
        "UNAVAILABLE",
        "NOT_APPLICABLE",
    }


def test_lineage_stage_order_is_complete_and_stable():
    assert LINEAGE_STAGE_ORDER[0] is LineageStage.DECISION_CONTEXT
    assert LINEAGE_STAGE_ORDER[-1] is LineageStage.TRACEABILITY
    assert len(set(LINEAGE_STAGE_ORDER)) == len(LINEAGE_STAGE_ORDER)
    assert len(LINEAGE_STAGE_ORDER) == len(LineageStage)


def test_lineage_scope_declares_the_pre_trade_boundary():
    """The epic asks about opportunity detection; this plane must say it cannot."""
    assert "not carried by the canonical replica" in LINEAGE_SCOPE
    assert "opportunity" in LINEAGE_SCOPE.lower()


def test_net_pnl_reconciles_matches_the_canonical_relationship():
    assert net_pnl_reconciles(gross_pnl=50.0, execution_costs=0.2, net_pnl=49.8)
    assert not net_pnl_reconciles(gross_pnl=50.0, execution_costs=0.2, net_pnl=50.0)


def test_evidence_gaps_are_machine_readable_and_name_their_owner():
    assert EVIDENCE_GAPS, "the epic's unreachable facts must be declared"
    for gap in EVIDENCE_GAPS:
        payload = gap.to_dict()
        for key in (
            "fact",
            "availability",
            "producer",
            "plane",
            "reason",
            "frozen_boundary",
            "recommended_increment",
        ):
            assert payload[key], f"gap {gap.fact} is missing {key}"
        # A gap is by definition not answerable from the canonical replica.
        assert gap.availability is FactAvailability.UNAVAILABLE
        assert gap.plane.value != "CANONICAL_REPLICA"


def test_evidence_gaps_cover_the_epic_goals_that_cannot_be_answered():
    facts = {gap.fact for gap in EVIDENCE_GAPS}
    for required in (
        "QUALIFICATION_FUNNEL_PROGRESSION",
        "REJECTION_REASON_AND_GATE_ATTRIBUTION",
        "MISSED_PROFITABLE_OPPORTUNITY",
        "FORWARD_OUTCOME_HORIZON_EVIDENCE",
        "UNREALIZED_MARK_TO_MARKET_PNL",
    ):
        assert required in facts, f"undeclared evidence gap: {required}"


def test_missed_opportunity_increment_is_explicitly_blocked_not_skipped():
    """Increment 4 must not be silently absent.

    Its required evidence is produced on the trading host and the learning plane,
    neither of which the read-only analytics plane reads, so the disposition is
    recorded rather than left to be inferred from a missing feature.
    """
    assert MISSED_OPPORTUNITY_DISPOSITION == "BLOCKED_AT_FROZEN_BOUNDARY"


def test_missed_opportunity_safeguards_are_pre_registered():
    """The safeguards must be declared before any implementation exists."""
    safeguards = MISSED_OPPORTUNITY_SAFEGUARDS
    assert safeguards.decision_time_facts_separate_from_future_outcomes is True
    assert safeguards.no_hindsight_leakage is True
    assert safeguards.forward_window_provenance_required is True
    assert safeguards.outcome_calculation_version_required is True
    assert safeguards.cause_classes_distinct is True
    assert safeguards.classification_requires_canonical_evidence is True
    assert safeguards.uncertainty_and_incomplete_data_preserved is True
    assert safeguards.no_threshold_or_strategy_change_authority is True
    # Evidence, never policy: this plane cannot promote or change a strategy.
    assert safeguards.consumption == "EVIDENCE_ONLY"


def test_missed_opportunity_cause_classes_stay_distinct():
    """Rejected / risk-blocked / capacity-blocked / failed must not collapse."""
    causes = {cause.value for cause in MissedOpportunityCause}
    assert causes == {
        "REJECTED",
        "FILTERED",
        "TECHNICALLY_UNAVAILABLE",
        "RISK_BLOCKED",
        "CAPACITY_BLOCKED",
        "EXECUTION_FAILED",
        "UNKNOWN",
    }


def test_missed_opportunity_safeguards_serialise_with_the_cause_classes():
    payload = MISSED_OPPORTUNITY_SAFEGUARDS.to_dict()
    assert payload["cause_classes"] == [cause.value for cause in MissedOpportunityCause]
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_overview_publishes_the_missed_opportunity_disposition():
    payload = build_profit_intelligence_overview(_ledger()).to_dict()
    assert payload["missed_opportunity"]["disposition"] == (
        MISSED_OPPORTUNITY_DISPOSITION
    )
    assert payload["missed_opportunity"]["safeguards"][
        "no_threshold_or_strategy_change_authority"
    ] is True


def test_metric_authority_only_names_registered_metrics():
    for owner, metric_id in pi_semantics.METRIC_AUTHORITY.items():
        assert metric_id in PAPER_METRICS, (
            f"{owner} names unregistered metric {metric_id}"
        )


def test_supported_cost_components_match_the_canonical_fill_contract_order():
    assert [item.value for item in pi_semantics.SUPPORTED_COST_COMPONENTS] == [
        "FEE_COST",
        "SPREAD_COST",
        "SLIPPAGE_COST",
        "OTHER_SUPPORTED_COST",
    ]


def test_supported_costs_sum_is_order_independent():
    high_low = {
        EconomicComponent.FEE_COST: 0.1,
        EconomicComponent.SPREAD_COST: 0.2,
        EconomicComponent.SLIPPAGE_COST: 0.0,
        EconomicComponent.OTHER_SUPPORTED_COST: 0.0,
    }
    low_high = {
        EconomicComponent.OTHER_SUPPORTED_COST: 0.0,
        EconomicComponent.SLIPPAGE_COST: 0.0,
        EconomicComponent.SPREAD_COST: 0.2,
        EconomicComponent.FEE_COST: 0.1,
    }
    assert pi_semantics.supported_costs_sum(
        high_low
    ) == pi_semantics.supported_costs_sum(low_high)


def test_supported_costs_sum_refuses_to_treat_a_missing_cost_as_zero():
    """The canonical fill contract carries all four; a missing one is not a zero."""
    with pytest.raises(ValueError):
        pi_semantics.supported_costs_sum({EconomicComponent.FEE_COST: 0.1})


def test_net_pnl_tolerance_matches_the_canonical_writer():
    """A stricter tolerance would report a conserving row as a violation."""
    assert pi_semantics.PNL_TOLERANCE == 1e-6
    # A row the canonical writer accepts must be accepted here too.
    assert net_pnl_reconciles(gross_pnl=50.0, execution_costs=0.2, net_pnl=49.8 + 1e-7)
    assert not net_pnl_reconciles(gross_pnl=50.0, execution_costs=0.2, net_pnl=49.9)


def test_gross_profit_borrows_no_registered_metric_id():
    """`paper.realized_net_pnl` is gross - costs; gross is not that metric."""
    assert "gross_pnl" not in pi_semantics.METRIC_AUTHORITY


# ---------------------------------------------------------------------------
# Decision / Trade Explorer lineage
# ---------------------------------------------------------------------------


def test_lineage_emits_every_stage_in_canonical_order():
    lineage = build_trade_lineage(_row())
    assert [stage.stage for stage in lineage.stages] == list(LINEAGE_STAGE_ORDER)


def test_lineage_carries_contract_and_projection_versions():
    lineage = build_trade_lineage(_row())
    payload = lineage.to_dict()
    assert payload["contract_version"]
    assert payload["projection_version"]
    assert payload["scope"] == LINEAGE_SCOPE


def test_lineage_fully_evidenced_trade_is_known_at_every_applicable_stage():
    lineage = build_trade_lineage(_row())
    availability = {s.stage: s.availability for s in lineage.stages}
    for stage in (
        LineageStage.DECISION_CONTEXT,
        LineageStage.POLICY_PROVENANCE,
        LineageStage.INTENT,
        LineageStage.ATTEMPT,
        LineageStage.FILL,
        LineageStage.PROTECTION,
        LineageStage.EXIT,
        LineageStage.RECONCILIATION,
        LineageStage.TRACEABILITY,
    ):
        assert availability[stage] is FactAvailability.KNOWN, stage
    # Definitive economics are canonical evidence, so KNOWN rather than DERIVED.
    assert availability[LineageStage.ECONOMICS] is FactAvailability.KNOWN


def test_lineage_no_fill_is_not_applicable_and_not_a_zero():
    """A trade that never filled cannot have fill/protection/exit evidence."""
    lineage = build_trade_lineage(_no_fill_row())
    for stage in (
        LineageStage.FILL,
        LineageStage.PROTECTION,
        LineageStage.EXIT,
    ):
        assert lineage.stage(stage).availability is FactAvailability.NOT_APPLICABLE, (
            stage
        )
        assert lineage.stage(stage).note


def test_lineage_fill_without_a_point_instant_is_still_known():
    """A committed fill evidenced by quantity must not be denied as UNAVAILABLE.

    Canonical temporal evidence can legitimately be BOUNDED or UNKNOWN precision,
    in which case no single instant exists even though the fill is readable.
    """
    row = build_trade_row(_entry(fully_filled=False, with_exit=False))
    row_no_instant = ReconciledPaperTrade(
        paper_trade_id=row.paper_trade_id,
        quote_currency=row.quote_currency,
        entry_quantity=row.entry_quantity,
        first_entry_fill_at=None,
        execution_result=row.execution_result,
    )
    stage = build_trade_lineage(row_no_instant).stage(LineageStage.FILL)
    assert stage.availability is FactAvailability.KNOWN
    assert stage.occurred_at is None


def test_lineage_missing_entry_intent_is_unavailable_not_zero():
    lineage = build_trade_lineage(_row(with_intent_time=False))
    stage = lineage.stage(LineageStage.INTENT)
    assert stage.availability is FactAvailability.UNAVAILABLE
    assert stage.note
    assert "intent" in stage.note.lower()


def test_lineage_attempt_is_not_applicable_when_no_intent_exists():
    """An attempt cannot exist without an intent - an impossibility, not a gap."""
    lineage = build_trade_lineage(_row(with_intent_time=False, with_attempt_time=False))
    assert lineage.stage(LineageStage.ATTEMPT).availability is (
        FactAvailability.NOT_APPLICABLE
    )


def test_lineage_attempt_is_unavailable_when_intent_exists_but_attempt_does_not():
    lineage = build_trade_lineage(_row(with_attempt_time=False))
    assert lineage.stage(LineageStage.ATTEMPT).availability is (
        FactAvailability.UNAVAILABLE
    )


def test_lineage_open_position_exit_is_not_applicable():
    row = build_trade_row(_entry(with_exit=False))
    lineage = build_trade_lineage(row)
    assert lineage.stage(LineageStage.EXIT).availability is (
        FactAvailability.NOT_APPLICABLE
    )
    assert lineage.stage(LineageStage.EXIT).note


def test_lineage_indicative_economics_are_derived_never_known():
    row = build_trade_row(_entry(final_verified=False))
    assert row.net_pnl_definitive is False
    lineage = build_trade_lineage(row)
    stage = lineage.stage(LineageStage.ECONOMICS)
    assert stage.availability is FactAvailability.DERIVED
    assert stage.note and "indicative" in stage.note


def test_lineage_economics_surfaces_conservation_flag():
    row = build_trade_row(_entry(gross=50.0, costs=0.2, net=999.0))
    stage = build_trade_lineage(row).stage(LineageStage.ECONOMICS)
    assert stage.facts["conserves_net_equals_gross_minus_costs"] is False


def test_lineage_traceability_carries_canonical_event_ids():
    lineage = build_trade_lineage(_row(event_ids=("e1", "e2", "e3")))
    stage = lineage.stage(LineageStage.TRACEABILITY)
    assert stage.evidence_event_ids == ("e1", "e2", "e3")
    assert stage.facts["event_id_count"] == 3


def test_lineage_without_event_ids_reports_unavailable():
    lineage = build_trade_lineage(_row(event_ids=()))
    assert lineage.stage(LineageStage.TRACEABILITY).availability is (
        FactAvailability.UNAVAILABLE
    )


def test_lineage_names_unavailable_stages_in_details():
    lineage = build_trade_lineage(_row(with_policy=False))
    assert any("POLICY_PROVENANCE" in detail for detail in lineage.details)


def test_lineage_details_never_list_a_not_applicable_stage_as_a_gap():
    """An impossible fact is not a missing fact and must not be reported as one."""
    lineage = build_trade_lineage(_row(with_intent_time=False))
    assert not any("NOT_APPLICABLE" in detail for detail in lineage.details)
    assert not any("ATTEMPT" in detail for detail in lineage.details)


def test_lineage_is_deterministic():
    row = _row()
    first = build_trade_lineage(row).to_dict()
    second = build_trade_lineage(row).to_dict()
    assert first == second


def test_lineage_rejects_a_non_row_input():
    with pytest.raises(TypeError):
        build_trade_lineage({"paper_trade_id": "PTV2:x"})  # type: ignore[arg-type]


def test_lineage_does_not_invent_pre_trade_stages():
    """No opportunity-detection/qualification stage may be fabricated."""
    lineage = build_trade_lineage(_row())
    stage_names = {stage.stage.value for stage in lineage.stages}
    for forbidden in ("OPPORTUNITY", "SCREENING", "QUALIFICATION", "REJECTION"):
        assert forbidden not in stage_names


def test_lineage_stage_lookup_rejects_unknown_stage():
    lineage = build_trade_lineage(_row())
    with pytest.raises(KeyError):
        lineage.stage("NOT_A_STAGE")  # type: ignore[arg-type]


def test_lineage_is_json_serialisable_with_no_nan():
    payload = build_trade_lineage(_row()).to_dict()
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    assert isinstance(json.loads(encoded), dict)


def test_lineage_leaks_no_credential_material():
    serialized = json.dumps(build_trade_lineage(_row()).to_dict()).lower()
    for forbidden in ("secret", "api_key", "api-secret", "password", "private_key"):
        assert forbidden not in serialized


# ---------------------------------------------------------------------------
# Economic integrity
# ---------------------------------------------------------------------------


def test_economic_integrity_groups_by_currency_and_never_sums_them():
    usd = _row(trade_suffix="a" * 64, quote_currency="USD")
    usdt = _row(trade_suffix="b" * 64, quote_currency="USDT")
    summaries = build_economic_integrity([usd, usdt])
    assert [item.quote_currency for item in summaries] == ["USD", "USDT"]
    for item in summaries:
        assert item.population == 1


def test_costs_without_any_fill_are_not_applicable_not_a_measured_zero():
    """A zero with no fill is an impossibility, not a measured cost of zero."""
    summary = build_economic_integrity([_no_fill_row()])[0]
    evidence = summary.component(EconomicComponent.FEE_COST, realized=False)
    assert evidence.availability is FactAvailability.NOT_APPLICABLE
    assert evidence.total == 0.0
    assert any("NOT_APPLICABLE" in detail for detail in summary.details)


def test_costs_with_fills_are_known_even_when_the_total_is_zero():
    row = _row(fee_cost=0.0, spread_cost=0.0, slippage_cost=0.0, other_cost=0.0)
    summary = build_economic_integrity([row])[0]
    for component in (
        EconomicComponent.FEE_COST,
        EconomicComponent.SPREAD_COST,
        EconomicComponent.SLIPPAGE_COST,
        EconomicComponent.OTHER_SUPPORTED_COST,
    ):
        evidence = summary.component(component, realized=True)
        assert evidence.availability is FactAvailability.KNOWN
        assert evidence.total == 0.0


def test_indicative_population_costs_are_derived_never_known():
    """The same value in the unverified population is not a settled fact."""
    unverified = _row(final_verified=False, fee_cost=0.25)
    summary = build_economic_integrity([unverified])[0]
    evidence = summary.component(EconomicComponent.FEE_COST, realized=False)
    assert evidence.availability is FactAvailability.DERIVED
    assert evidence.total == pytest.approx(2 * 0.25)
    # Nothing verified exists, so the realized list carries no evidence.
    assert summary.component(
        EconomicComponent.FEE_COST, realized=True
    ).availability is FactAvailability.NOT_APPLICABLE


def test_realized_net_pnl_metric_id_is_never_attached_to_a_mixed_total():
    """A total containing unverified rows must not claim the realized metric.

    `paper.realized_net_pnl` declares its eligible population as
    ACTUAL_REALIZED and FINAL_VERIFIED and excludes UNRESOLVED_EVIDENCE, so a
    mixed total carrying that metric id would be a competing definition.
    """
    verified = _row(trade_suffix="a" * 64, final_verified=True, gross=50.0, costs=0.2)
    unverified = _row(
        trade_suffix="b" * 64, final_verified=False, gross=10.0, costs=0.1
    )
    summary = build_economic_integrity([verified, unverified])[0]

    realized = summary.component(EconomicComponent.NET_PNL, realized=True)
    assert realized.metric_id == "paper.realized_net_pnl"
    assert realized.total == pytest.approx(verified.net_pnl)

    indicative = summary.component(EconomicComponent.NET_PNL, realized=False)
    assert indicative.metric_id is None
    assert indicative.availability is FactAvailability.DERIVED
    assert indicative.total == pytest.approx(unverified.net_pnl)

    # The realized subtotal must not silently contain the unverified row, and the
    # two subtotals must never be presented as one blended number.
    assert realized.total != pytest.approx(verified.net_pnl + unverified.net_pnl)


def test_gross_profit_carries_no_registered_metric_id():
    summary = build_economic_integrity([_row()])[0]
    assert summary.component(EconomicComponent.GROSS_PNL, realized=True).metric_id is None


def test_an_entirely_unverified_population_is_not_complete_or_healthy():
    """Indicative economics must never read as settled, complete or healthy."""
    summary = build_economic_integrity(
        [_row(final_verified=False), _row(trade_suffix="c" * 64, final_verified=False)]
    )[0]
    assert summary.definitive == 0
    assert summary.indicative == 2
    assert summary.trust.completeness is Completeness.INCOMPLETE
    assert summary.trust.is_healthy is False
    assert "ECONOMICS_NOT_FINAL_VERIFIED" in summary.trust.reasons


def test_a_fully_verified_population_is_complete_and_healthy():
    summary = build_economic_integrity([_row()])[0]
    assert summary.indicative == 0
    assert summary.trust.completeness is Completeness.COMPLETE
    assert summary.trust.is_healthy is True
    assert summary.trust.reasons == ()


def test_definitive_and_indicative_population_stay_separate():
    verified = _row(trade_suffix="a" * 64, final_verified=True)
    unverified = _row(trade_suffix="b" * 64, final_verified=False)
    summary = build_economic_integrity([verified, unverified])[0]
    assert summary.population == 2
    assert summary.definitive == 1
    assert summary.indicative == 1


def test_conservation_violation_is_reported_and_marks_the_envelope_incomplete():
    bad = _row(gross=50.0, costs=0.2, net=999.0)
    summary = build_economic_integrity([bad])[0]
    assert summary.conservation_violations == (bad.paper_trade_id,)
    assert summary.trust.completeness is Completeness.INCOMPLETE
    assert "ECONOMIC_CONSERVATION_VIOLATION" in summary.trust.reasons
    assert summary.trust.is_healthy is False


def test_a_consistent_population_is_complete_and_healthy():
    summary = build_economic_integrity([_row()])[0]
    assert summary.conservation_violations == ()
    assert summary.trust.completeness is Completeness.COMPLETE


def test_unmodelled_cost_residual_is_surfaced_not_hidden():
    """Recorded aggregate cost above the four supported components is disclosed."""
    row = _row(gross=50.0, costs=0.5, fee_cost=0.1)
    summary = build_economic_integrity([row])[0]
    supported = (
        row.fee_cost + row.spread_cost + row.slippage_cost + row.other_cost
    )
    assert summary.unmodelled_cost_residual == pytest.approx(
        row.execution_costs - supported
    )
    assert summary.unmodelled_cost_residual > 0.0
    assert any("unmodelled" in detail for detail in summary.details)


def test_no_phantom_residual_when_no_cost_evidence_can_exist():
    stripped = ReconciledPaperTrade(
        paper_trade_id="PTV2:" + "c" * 64,
        quote_currency="USD",
        execution_costs=0.0,
        fee_cost=0.0,
    )
    summary = build_economic_integrity([stripped])[0]
    assert summary.unmodelled_cost_residual == 0.0


def test_economic_integrity_of_an_empty_population_is_empty():
    assert build_economic_integrity([]) == ()


def test_economic_integrity_rejects_non_row_input():
    with pytest.raises(TypeError):
        build_economic_integrity([{"quote_currency": "USD"}])  # type: ignore[list-item]


def test_economic_integrity_is_deterministic():
    rows = [_row(trade_suffix="a" * 64), _row(trade_suffix="b" * 64)]
    assert [item.to_dict() for item in build_economic_integrity(rows)] == [
        item.to_dict() for item in build_economic_integrity(rows)
    ]


def test_economic_integrity_component_lookup_rejects_unknown_component():
    summary = build_economic_integrity([_row()])[0]
    with pytest.raises(KeyError):
        summary.component("NOT_A_COMPONENT")  # type: ignore[arg-type]


def test_economic_integrity_is_json_safe():
    payload = build_economic_integrity([_row()])[0].to_dict()
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_economic_components_never_use_a_float_nan_placeholder():
    """Missing evidence must be availability, never NaN silently serialised."""
    summary = build_economic_integrity([_no_fill_row()])[0]
    for component in (*summary.realized_components, *summary.indicative_components):
        assert not (component.total != component.total), "NaN leaked into a total"


def test_costs_recorded_without_fill_evidence_are_reported_not_zeroed():
    """A cost with no fill is a contradiction, not a residual of zero."""
    contradictory = _no_fill_row(suffix="e" * 64, execution_costs=1.5, net_pnl=-1.5)
    summary = build_economic_integrity([contradictory])[0]
    assert any("no fill" in detail for detail in summary.details)
    # The recorded cost is still surfaced rather than disappearing.
    assert summary.indicative_cost_residual == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def test_overview_composes_integrity_and_populations():
    overview = build_profit_intelligence_overview(
        _ledger(_entry(trade_suffix="a" * 64), _entry(trade_suffix="b" * 64))
    )
    payload = overview.to_dict()
    assert payload["populations"]["trades"] == 2
    assert payload["populations"]["quote_currencies"] == ["USD"]
    assert len(payload["economic_integrity"]) == 1


def test_overview_carries_versions_and_scope():
    payload = build_profit_intelligence_overview(_ledger()).to_dict()
    assert payload["contract_version"]
    assert payload["read_model_version"] == PROFIT_INTELLIGENCE_READ_MODEL_VERSION
    assert payload["economic_integrity_version"]
    assert payload["scope"] == LINEAGE_SCOPE


def test_overview_publishes_the_evidence_gap_registry():
    payload = build_profit_intelligence_overview(_ledger()).to_dict()
    assert len(payload["evidence_gaps"]) == len(EVIDENCE_GAPS)
    assert all(
        gap["availability"] == "UNAVAILABLE" for gap in payload["evidence_gaps"]
    )


def test_overview_population_counts_are_separated_by_dimension():
    payload = build_profit_intelligence_overview(
        _ledger(
            _entry(trade_suffix="a" * 64, final_verified=True),
            _entry(trade_suffix="b" * 64, final_verified=False),
        )
    ).to_dict()
    assert sum(payload["populations"]["by_lifecycle_status"].values()) == 2
    assert payload["populations"]["settled"] == 1


def test_healthy_and_unavailable_overviews_have_the_same_shape():
    healthy = build_profit_intelligence_overview(_ledger()).to_dict()
    unavailable_payload = unavailable_profit_intelligence(
        "CANONICAL_REPLICA_UNAVAILABLE"
    ).to_dict()
    assert set(healthy) == set(unavailable_payload)


def test_unavailable_overview_is_never_healthy_or_complete():
    payload = unavailable_profit_intelligence("CANONICAL_REPLICA_UNAVAILABLE").to_dict()
    assert payload["trust"]["is_healthy"] is False
    assert payload["trust"]["completeness"] == Completeness.UNKNOWN.value
    assert payload["details"]


def test_unavailable_overview_fabricates_no_numeric_zero():
    """'Could not read' must never be presented as a measured count of zero."""
    payload = unavailable_profit_intelligence("CANONICAL_REPLICA_UNAVAILABLE").to_dict()
    assert payload["populations"] == {}
    assert payload["economic_integrity"] == []


def test_overview_rejects_a_non_ledger_input():
    with pytest.raises(TypeError):
        build_profit_intelligence_overview("nope")  # type: ignore[arg-type]


def test_build_lineage_for_trade_returns_none_when_the_trade_is_absent():
    assert build_lineage_for_trade(_ledger(), "PTV2:missing") is None


def test_build_lineage_for_trade_rejects_a_blank_identifier():
    with pytest.raises(ValueError):
        build_lineage_for_trade(_ledger(), "   ")


def test_build_lineage_for_trade_finds_the_matching_row():
    entry = _entry(trade_suffix="e" * 64)
    lineage = build_lineage_for_trade(_ledger(entry), entry.paper_trade_id)
    assert lineage is not None
    assert lineage.paper_trade_id == entry.paper_trade_id


# ---------------------------------------------------------------------------
# Frozen Cockpit exposure contract preserved
# ---------------------------------------------------------------------------


def test_cockpit_exposure_contract_is_unchanged():
    """Profit Intelligence must not widen the Cockpit's tested exposure surface.

    The Cockpit is reached through a TLS reverse proxy that forwards exactly four
    paths, and the deployment README states that those four paths are the whole
    surface. Publishing Profit Intelligence over HTTP would have widened an
    edge-enforced exposure contract without an owner decision, so the plane ships
    as a library and the HTTP surface is left exactly as it was.
    """
    from app.api import cockpit_service

    paths = set(cockpit_service.app.openapi().get("paths", {}) or {})
    assert paths == {
        "/cockpit",
        "/api/cockpit/overview",
        "/api/cockpit/trades",
        "/api/cockpit/trades/{paper_trade_id}",
    }, paths


def test_profit_intelligence_declares_no_http_surface():
    """No route may exist until the exposure contract is widened deliberately."""
    methods = cockpit.route_methods()
    assert not any("profit-intelligence" in path for path in methods)


def test_cockpit_api_still_exposes_no_write_surface():
    source = pathlib.Path(cockpit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned in (
        "submit",
        "place_order",
        "cancel_order",
        "admit_paper_opportunity",
        "trigger_paper_protection_action",
    ):
        assert banned not in called
    for decorator in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert decorator not in source


# ---------------------------------------------------------------------------
# Authority and boundary invariants
# ---------------------------------------------------------------------------


def test_profit_intelligence_plane_has_no_writer_coupling():
    """The plane must not construct a writable writer or import a write path."""
    for module in sorted(_package().glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "CanonicalWriter(" not in source, f"{module.name} constructs a writer"
        for banned_module in (
            "app.services.paper_v2_execution",
            "app.services.paper_v2_protection_runtime",
            "app.services.register_trade",
        ):
            assert banned_module not in source, (
                f"{module.name} imports trading writer path {banned_module}"
            )


def test_profit_intelligence_does_not_import_the_production_canonical_path():
    """Analytics must resolve through the replica seam, never the live store."""
    for module in sorted(_package().glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "app.opip.canonical.paths" not in source, (
            f"{module.name} resolves the production canonical path"
        )


def test_profit_intelligence_plane_has_no_http_framework_dependency():
    """The projections stay surface-agnostic pure read models.

    Keeping the plane free of an HTTP framework is what allows it to be served by
    whichever surface the owner later authorises, including the trading host's
    authenticated analytics router, without reworking the semantics.
    """
    for module in sorted(_package().glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "fastapi" not in source, f"{module.name} depends on an HTTP framework"


def test_profit_intelligence_plane_imports_no_trading_authority():
    """No execution, risk, alert, ranking, promotion or exchange authority."""
    banned = (
        "app.exchanges",
        "app.opip.risk",
        "app.services.alert_governor",
        "app.services.paper_trade_engine",
        "app.services.profit_ranking",
        "app.opip.committee",
    )
    for module in sorted(_package().glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for banned_module in banned:
            assert banned_module not in source, (
                f"{module.name} imports authority module {banned_module}"
            )
