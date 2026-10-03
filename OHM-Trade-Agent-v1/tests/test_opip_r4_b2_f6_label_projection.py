"""F6 prospective label projection (R4-B2 Slice 3B).

Proves the frozen canonical -> F6 label mapping, its fail-closed behaviour on
ambiguous or unmapped canonical evidence, and that the projection is pure,
read-only and holds no authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.opip.contracts.forecast import EntryExecutionOutcome, PostFillPathOutcome
from app.opip.forecast_evaluation import ForecastFidelityGrade, ForecastLabelState
from app.opip.forecast_labels import (
    FORECAST_EXIT_REASON_PATH_MAP,
    ForecastEvidencePopulation,
    NON_ADMISSION_DISPOSITIONS,
    NON_TERMINAL_EXECUTION_STATES,
    NO_FILL_EXECUTION_STATES,
    project_forecast_labels,
)

_MODULE = (
    Path(__file__).resolve().parents[1] / "app" / "opip" / "forecast_labels.py"
)


def _filled(**overrides):
    base = dict(
        disposition="ADMITTED",
        entry_execution_state="FILLED",
        intended_quantity=100.0,
        accepted_quantity=100.0,
        exit_reason="TARGET_2",
        terminal_status="CLOSED",
        net_pnl=12.5,
        capital_committed=1000.0,
        lineage_completeness="COMPLETE",
    )
    base.update(overrides)
    return project_forecast_labels(**base)


@pytest.mark.acceptance
def test_ac_023_full_and_partial_fill_resolve() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-023: the projection resolves canonical fills onto the exact F6 entry-execution family."""
    full = _filled()
    assert full.entry_outcome is EntryExecutionOutcome.FULL_FILL
    assert full.entry_label_state is ForecastLabelState.RESOLVED
    assert full.population is ForecastEvidencePopulation.FULL_FILL

    partial = _filled(entry_execution_state="PARTIALLY_FILLED", accepted_quantity=40.0)
    assert partial.entry_outcome is EntryExecutionOutcome.PARTIAL_FILL
    assert partial.entry_label_state is ForecastLabelState.RESOLVED
    assert partial.population is ForecastEvidencePopulation.PARTIAL_FILL


@pytest.mark.acceptance
def test_ac_023_no_fill_variants_resolve() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-023: an unfilled or expired entry resolves to NO_FILL, never to a fabricated fill."""
    expired = project_forecast_labels(disposition="NO_FILL_EXPIRED", accepted_quantity=0.0)
    assert expired.entry_outcome is EntryExecutionOutcome.NO_FILL
    assert expired.population is ForecastEvidencePopulation.NO_FILL

    cancelled = project_forecast_labels(
        disposition="ADMITTED",
        entry_execution_state="CANCELLED",
        accepted_quantity=0.0,
    )
    assert cancelled.entry_outcome is EntryExecutionOutcome.NO_FILL
    assert cancelled.population is ForecastEvidencePopulation.NO_FILL


@pytest.mark.acceptance
def test_ac_023_non_admission_is_cash_no_trade() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-023: a deliberately unadmitted opportunity is retained as cash/no-trade, not scored as a fill."""
    for disposition in sorted(NON_ADMISSION_DISPOSITIONS):
        label = project_forecast_labels(disposition=disposition)
        assert label.population is ForecastEvidencePopulation.CASH_NO_TRADE
        assert label.entry_outcome is None
        assert label.post_fill_outcome is None


@pytest.mark.acceptance
def test_ac_023_ambiguous_entry_fails_closed() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-023: an entry the canonical vocabulary does not unambiguously support fails closed to UNRESOLVED."""
    # FILLED but nothing was accepted.
    assert _filled(accepted_quantity=0.0).entry_label_state is ForecastLabelState.UNRESOLVED
    # PARTIALLY_FILLED but nothing was accepted.
    partial_zero = _filled(entry_execution_state="PARTIALLY_FILLED", accepted_quantity=0.0)
    assert partial_zero.entry_label_state is ForecastLabelState.UNRESOLVED
    # PARTIALLY_FILLED that actually met the intended quantity.
    partial_full = _filled(entry_execution_state="PARTIALLY_FILLED", accepted_quantity=100.0)
    assert partial_full.entry_label_state is ForecastLabelState.UNRESOLVED
    # A non-terminal entry state.
    for state in sorted(NON_TERMINAL_EXECUTION_STATES):
        pending = project_forecast_labels(
            disposition="ADMITTED", entry_execution_state=state, accepted_quantity=0.0
        )
        assert pending.entry_label_state is ForecastLabelState.UNRESOLVED
    # An unknown execution state.
    unknown = project_forecast_labels(
        disposition="ADMITTED", entry_execution_state="SOMETHING_NEW", accepted_quantity=0.0
    )
    assert unknown.entry_label_state is ForecastLabelState.UNRESOLVED
    # An expired admission that nonetheless accepted quantity.
    inconsistent = project_forecast_labels(
        disposition="NO_FILL_EXPIRED", accepted_quantity=5.0
    )
    assert inconsistent.entry_label_state is ForecastLabelState.UNRESOLVED
    for state in sorted(NO_FILL_EXECUTION_STATES):
        with_qty = project_forecast_labels(
            disposition="ADMITTED", entry_execution_state=state, accepted_quantity=5.0
        )
        assert with_qty.entry_label_state is ForecastLabelState.UNRESOLVED


@pytest.mark.acceptance
def test_ac_024_path_family_maps_exactly() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-024: canonical exit reasons map onto the exact F6 post-fill path family, and a risk trigger maps to RISK_EXIT."""
    assert _filled(exit_reason="TARGET_2").post_fill_outcome is PostFillPathOutcome.TARGET
    assert _filled(exit_reason="STOP").post_fill_outcome is PostFillPathOutcome.STOP
    assert _filled(exit_reason="ENTRY_CANDLE_STOP").post_fill_outcome is PostFillPathOutcome.STOP
    assert _filled(exit_reason="TIME_EXIT").post_fill_outcome is PostFillPathOutcome.TIMEOUT
    risk = _filled(protection_trigger_type="EMERGENCY")
    assert risk.post_fill_outcome is PostFillPathOutcome.RISK_EXIT
    assert risk.post_fill_label_state is ForecastLabelState.RESOLVED


@pytest.mark.acceptance
def test_ac_024_no_fill_has_no_path_label() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-024: a NO_FILL entry carries no post-fill path label and is never given a fabricated path."""
    no_fill = project_forecast_labels(
        disposition="ADMITTED", entry_execution_state="EXPIRED", accepted_quantity=0.0
    )
    assert no_fill.post_fill_outcome is None
    assert no_fill.post_fill_label_state is ForecastLabelState.INSUFFICIENT_EVIDENCE


@pytest.mark.acceptance
def test_ac_024_unresolved_and_incomplete_are_never_negative() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-024: a gap is INCOMPLETE_COVERAGE and an unmapped or non-market reason is UNRESOLVED; neither becomes a negative or a fabricated path."""
    gap = _filled(exit_reason="OHLC_GAP")
    assert gap.post_fill_outcome is None
    assert gap.post_fill_label_state is ForecastLabelState.INCOMPLETE_COVERAGE
    assert gap.population is ForecastEvidencePopulation.INCOMPLETE_COVERAGE

    for reason in ("OPERATOR_OFF", "UNRESOLVED", "SOMETHING_NEW"):
        label = _filled(exit_reason=reason)
        assert label.post_fill_outcome is None
        assert label.post_fill_label_state is ForecastLabelState.UNRESOLVED

    # A terminal UNRESOLVED status asserts nothing at all.
    terminal = _filled(terminal_status="UNRESOLVED", exit_reason="TARGET_2")
    assert terminal.entry_label_state is ForecastLabelState.UNRESOLVED
    assert terminal.realized_net_return is None
    assert terminal.realized_return_label_state is ForecastLabelState.UNRESOLVED


@pytest.mark.acceptance
def test_ac_024_no_family_token_is_invented() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-024: the frozen mapping table only ever yields members of the frozen F6 path enum or an explicit non-label."""
    for outcome, state in FORECAST_EXIT_REASON_PATH_MAP.values():
        assert outcome is None or isinstance(outcome, PostFillPathOutcome)
        assert isinstance(state, ForecastLabelState)


@pytest.mark.acceptance
def test_ac_025_fidelity_is_never_inferred_as_a() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-025: grade A is never inferred; native paper is B and an ambiguous path or unproven lineage is C."""
    assert _filled().fidelity is ForecastFidelityGrade.B
    assert _filled(exit_reason="OHLC_GAP").fidelity is ForecastFidelityGrade.C
    # Only the exact canonical COMPLETE lineage token grades B; an incomplete,
    # absent or unknown lineage value grades C rather than silently grading B.
    assert _filled(lineage_completeness="LINEAGE_INCOMPLETE").fidelity is ForecastFidelityGrade.C
    assert _filled(lineage_completeness=None).fidelity is ForecastFidelityGrade.C
    assert _filled(lineage_completeness="something-else").fidelity is ForecastFidelityGrade.C
    for lineage in ("COMPLETE", "LINEAGE_INCOMPLETE", None, "unknown"):
        for reason in ("TARGET_2", "OHLC_GAP"):
            assert _filled(lineage_completeness=lineage, exit_reason=reason).fidelity is not (
                ForecastFidelityGrade.A
            )


@pytest.mark.acceptance
def test_ac_025_realized_return_is_dimensionless_and_zero_without_capital() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-025: the realized net return is a dimensionless net-over-capital ratio, and a no-capital no-trade is exactly zero rather than a loss."""
    assert _filled(net_pnl=12.5, capital_committed=1000.0).realized_net_return == pytest.approx(0.0125)
    assert _filled(net_pnl=-5.0, capital_committed=1000.0).realized_net_return == pytest.approx(-0.005)

    no_fill = project_forecast_labels(
        disposition="ADMITTED", entry_execution_state="EXPIRED", accepted_quantity=0.0
    )
    assert no_fill.realized_net_return == 0.0
    assert no_fill.realized_return_label_state is ForecastLabelState.RESOLVED

    cash = project_forecast_labels(disposition="CAPITAL_REJECTED")
    assert cash.realized_net_return == 0.0

    missing = _filled(net_pnl=None, capital_committed=None)
    assert missing.realized_net_return is None
    assert missing.realized_return_label_state is ForecastLabelState.UNRESOLVED


@pytest.mark.acceptance
def test_ac_025_timeout_scores_its_recorded_return() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-025: a TIMEOUT is scored from its recorded realized return and is not forced negative."""
    positive = _filled(exit_reason="TIME_EXIT", net_pnl=8.0, capital_committed=1000.0)
    assert positive.post_fill_outcome is PostFillPathOutcome.TIMEOUT
    assert positive.realized_net_return == pytest.approx(0.008)
    negative = _filled(exit_reason="TIME_EXIT", net_pnl=-3.0, capital_committed=1000.0)
    assert negative.post_fill_outcome is PostFillPathOutcome.TIMEOUT
    assert negative.realized_net_return == pytest.approx(-0.003)


@pytest.mark.acceptance
def test_ac_026_projection_is_pure_and_deterministic() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-026: the projection is a pure function of its inputs and reads no clock, environment or store."""
    first = _filled()
    second = _filled()
    assert first == second

    source = _MODULE.read_text(encoding="utf-8")
    for forbidden in (
        "datetime.now",
        "utcnow",
        "time.time",
        "os.environ",
        "sqlite3",
        "CanonicalWriter",
        "WriterIntent",
        "open(",
        "requests",
        "subprocess",
    ):
        assert forbidden not in source, forbidden


@pytest.mark.acceptance
def test_ac_026_malformed_token_fails_closed_per_record() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-026: a malformed canonical token fails closed to UNRESOLVED without raising, so one bad field cannot abort a batch."""
    label = project_forecast_labels(disposition=" ADMITTED ")
    assert label.entry_label_state is ForecastLabelState.UNRESOLVED
    assert label.population is ForecastEvidencePopulation.UNRESOLVED
    assert "MALFORMED_TOKEN" in label.reason

    non_string = project_forecast_labels(entry_execution_state=123)
    assert non_string.entry_label_state is ForecastLabelState.UNRESOLVED

    # A terminal status outside the frozen canonical set is not evidence.
    unknown_status = _filled(terminal_status="OPEN")
    assert unknown_status.entry_label_state is ForecastLabelState.UNRESOLVED
    assert unknown_status.population is ForecastEvidencePopulation.UNRESOLVED


@pytest.mark.acceptance
def test_ac_026_no_favorable_label_without_supporting_evidence() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-026: across a cross-product of canonical inputs the projection never yields a favorable label, path or return without the exact supporting evidence, and is deterministic."""
    import itertools

    dispositions = [None, "ADMITTED", "NO_FILL_EXPIRED", "CAPITAL_REJECTED", "CANCELLED"]
    exec_states = [None, "FILLED", "PARTIALLY_FILLED", "REJECTED", "CANCELLED", "EXPIRED", "WORKING", "UNKNOWN_X"]
    quantities = [(None, None), (100.0, 100.0), (100.0, 40.0), (100.0, 0.0), (100.0, 150.0), (0.0, 0.0)]
    exit_reasons = [None, "TARGET_2", "STOP", "ENTRY_CANDLE_STOP", "TIME_EXIT", "OHLC_GAP", "OPERATOR_OFF", "UNRESOLVED", "NEW_REASON"]
    terminal_statuses = [None, "CLOSED", "CANCELLED", "UNRESOLVED", "OPEN"]
    economics = [(None, None), (12.5, 1000.0), (-5.0, 1000.0), (5.0, 0.0)]
    triggers = [None, "EMERGENCY"]
    expected = (
        len(dispositions)
        * len(exec_states)
        * len(quantities)
        * len(exit_reasons)
        * len(terminal_statuses)
        * len(economics)
        * len(triggers)
    )

    checked = 0
    for (
        disp,
        state,
        (intended, accepted),
        reason,
        status,
        (net, capital),
        trigger,
    ) in itertools.product(
        dispositions,
        exec_states,
        quantities,
        exit_reasons,
        terminal_statuses,
        economics,
        triggers,
    ):
        label = project_forecast_labels(
            disposition=disp,
            entry_execution_state=state,
            intended_quantity=intended,
            accepted_quantity=accepted,
            exit_reason=reason,
            protection_trigger_type=trigger,
            terminal_status=status,
            net_pnl=net,
            capital_committed=capital,
            lineage_completeness="COMPLETE",
        )
        checked += 1

        # Determinism: an identical input replays an identical label.
        again = project_forecast_labels(
            disposition=disp,
            entry_execution_state=state,
            intended_quantity=intended,
            accepted_quantity=accepted,
            exit_reason=reason,
            protection_trigger_type=trigger,
            terminal_status=status,
            net_pnl=net,
            capital_committed=capital,
            lineage_completeness="COMPLETE",
        )
        assert again == label

        # Grade A is never inferred.
        assert label.fidelity is not ForecastFidelityGrade.A

        # A full/partial fill requires an actual positive accepted quantity.
        if label.entry_outcome is EntryExecutionOutcome.FULL_FILL:
            assert state == "FILLED" and accepted is not None and intended is not None
            assert accepted > 0 and accepted >= intended
        if label.entry_outcome is EntryExecutionOutcome.PARTIAL_FILL:
            assert accepted is not None and accepted > 0
            assert intended is None or accepted < intended

        # A path label requires fill exposure.
        if label.post_fill_outcome is not None:
            assert label.entry_outcome in (
                EntryExecutionOutcome.PARTIAL_FILL,
                EntryExecutionOutcome.FULL_FILL,
            )
        if label.post_fill_outcome is PostFillPathOutcome.TARGET:
            assert reason == "TARGET_2"
        if label.post_fill_outcome is PostFillPathOutcome.TIMEOUT:
            assert reason == "TIME_EXIT"
        if label.post_fill_outcome is PostFillPathOutcome.RISK_EXIT:
            # A risk exit is only ever produced by a real risk trigger.
            assert trigger == "EMERGENCY"

        # A NO_FILL entry never carries a path label.
        if label.entry_outcome is EntryExecutionOutcome.NO_FILL:
            assert label.post_fill_outcome is None
            assert label.post_fill_label_state is ForecastLabelState.INSUFFICIENT_EVIDENCE
            assert label.realized_net_return == 0.0

        # A nonzero realized return requires real capital and matching economics.
        if label.realized_net_return not in (None, 0.0):
            assert net is not None and capital is not None and capital > 0.0
            assert label.realized_net_return == pytest.approx(net / capital)

        # A terminal UNRESOLVED status asserts nothing at all.
        if status == "UNRESOLVED":
            assert label.entry_outcome is None
            assert label.post_fill_outcome is None
            assert label.realized_net_return is None
    assert checked == expected == 86_400
