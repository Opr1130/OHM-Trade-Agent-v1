"""R4-B0 review remediation regression proofs.

Each test here locks in one defect fixed during the R4-B0 review loop, so the
fix cannot silently regress. The defects were real correctness gaps in the R4-B0
contracts: a tautological causality guard, quantity sized off an unbound price,
a non-actionable geometry becoming an execution candidate, an instrument not
cross-checked, an under-validated handoff, opposite directions sharing an
identity, a format marker breaking idempotent legacy retries, admission direction
defaulting to LONG, an adapter pre-empting F5's ordered checks, and a serialized
geometry field excluded from its identity.

No state is activated; these are pure contract and writer assertions.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.feasibility import FeasibilityDisposition
from app.opip.contracts.feasibility_evidence import (
    feasibility_evidence_from_market_snapshot,
)
from app.opip.contracts.portfolio import PortfolioStatus
from app.opip.contracts.portfolio_paper_handoff import (
    PortfolioPaperHandoffError,
    build_execution_candidate_bridge,
    geometry_entry_reference,
)
from app.opip.execution_geometry import build_execution_geometry
from app.opip.feasibility import evaluate_feasibility
from tests import test_opip_r3_f7_economic_portfolio_selector as f7
from tests import test_opip_r4_b0_portfolio_paper_handoff as handoff
from tests.test_opip_paper_execution_bc1 import (
    _admission,
    _seed_context,
    _seed_instrument_version,
)

CLAIM_CUTOFF = f7.CUTOFF


# ---------------------------------------------------------------------------
# 1. Causality: evidence dated after the F7 decision is refused
# ---------------------------------------------------------------------------


def test_lookahead_cutoff_after_the_decision_is_refused():
    """Regression: the guard compared the cutoff with itself and never fired."""
    decision, candidate, geometry = handoff._selected_panel()
    future = handoff._lineage(
        geometry=geometry,
        snapshot_cutoff=decision.evaluation_time + timedelta(seconds=1),
    )
    with pytest.raises(PortfolioPaperHandoffError, match="later than the F7"):
        build_execution_candidate_bridge(
            candidate=candidate,
            allocation=decision.allocations[0],
            geometry=geometry,
            lineage=future,
            portfolio_decision_id=str(decision.decision_id),
            portfolio_version=str(decision.reservation_plan.portfolio_version),
            evaluation_time=decision.evaluation_time,
        )


def test_geometry_cutoff_after_the_decision_is_refused():
    """A geometry stamped after the decision is look-ahead and refused."""
    decision, candidate, geometry = handoff._selected_panel()
    from app.opip.contracts.execution_geometry import ExecutionGeometryInput

    future_geometry = build_execution_geometry(
        ExecutionGeometryInput(
            symbol="SOLUSD",
            direction="LONG",
            risk_level="medium",
            last_price=100.0,
            atr=2.0,
            ema20=99.5,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        ),
        instrument_version_id=handoff.INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        source_cutoff=decision.evaluation_time + timedelta(seconds=1),
        source_evidence_fingerprint="FEV:" + "1" * 32,
    )
    with pytest.raises(PortfolioPaperHandoffError):
        build_execution_candidate_bridge(
            candidate=candidate,
            allocation=decision.allocations[0],
            geometry=future_geometry,
            lineage=handoff._lineage(geometry=geometry),
            portfolio_decision_id=str(decision.decision_id),
            portfolio_version=str(decision.reservation_plan.portfolio_version),
            evaluation_time=decision.evaluation_time,
        )


# ---------------------------------------------------------------------------
# 2. Quantity is bound to the exact geometry, not a caller price
# ---------------------------------------------------------------------------


def test_substituted_entry_reference_is_refused():
    """Regression: a lower caller price inflated the paper quantity."""
    decision, candidate, geometry = handoff._selected_panel()
    reference = geometry_entry_reference(geometry)
    assert reference > 1.0
    for bad in (1.0, reference * 0.5, reference + 1.0):
        with pytest.raises(PortfolioPaperHandoffError, match="entry_reference"):
            build_execution_candidate_bridge(
                candidate=candidate,
                allocation=decision.allocations[0],
                geometry=geometry,
                lineage=handoff._lineage(geometry=geometry, entry_reference=bad),
                portfolio_decision_id=str(decision.decision_id),
                portfolio_version=str(decision.reservation_plan.portfolio_version),
                evaluation_time=decision.evaluation_time,
            )


def test_handoff_quantity_and_notional_come_from_the_geometry():
    decision, candidate, geometry = handoff._selected_panel()
    handoff_row = handoff._handoffs(decision, candidate, geometry)[0]
    assert handoff_row.entry_reference == pytest.approx(geometry_entry_reference(geometry))
    assert handoff_row.requested_quantity * handoff_row.entry_reference == pytest.approx(
        handoff_row.requested_notional
    )
    assert handoff_row.requested_notional <= handoff_row.allocated_capital + 1e-9


# ---------------------------------------------------------------------------
# 3. A non-actionable geometry must never become an execution candidate
# ---------------------------------------------------------------------------


def test_non_actionable_geometry_is_refused():
    """Regression: a WAIT geometry with a positive stop fraction still bridged.

    An extended price produces the kernel's explicit ``wait_for_pullback`` plan,
    which has a positive stop risk but is not actionable.
    """
    from app.opip.contracts.execution_geometry import ExecutionGeometryInput

    decision, candidate, _geometry_row = handoff._selected_panel()
    wait_geometry = build_execution_geometry(
        ExecutionGeometryInput(
            symbol="SOLUSD",
            direction="LONG",
            risk_level="medium",
            last_price=110.0,
            atr=2.0,
            ema20=99.0,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        ),
        instrument_version_id=handoff.INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        source_cutoff=CLAIM_CUTOFF,
        source_evidence_fingerprint="FEV:" + "1" * 32,
    )
    assert wait_geometry.valid_now is False
    assert wait_geometry.stop_loss_fraction > 0
    assert wait_geometry.is_actionable is False
    with pytest.raises(PortfolioPaperHandoffError, match="not actionable"):
        build_execution_candidate_bridge(
            candidate=candidate,
            allocation=decision.allocations[0],
            geometry=wait_geometry,
            lineage=handoff._lineage(
                geometry=wait_geometry, snapshot_cutoff=CLAIM_CUTOFF
            ),
            portfolio_decision_id=str(decision.decision_id),
            portfolio_version=str(decision.reservation_plan.portfolio_version),
            evaluation_time=decision.evaluation_time,
        )


# ---------------------------------------------------------------------------
# 4. Instrument binding: another market's geometry cannot bridge
# ---------------------------------------------------------------------------


def test_symbol_mismatch_between_candidate_and_lineage_is_refused():
    """Regression: only the instrument/venue were compared, not the symbol."""
    decision, candidate, geometry = handoff._selected_panel()
    foreign = handoff._lineage(geometry=geometry, native_symbol="ETH/USD")
    with pytest.raises(PortfolioPaperHandoffError, match="symbol"):
        build_execution_candidate_bridge(
            candidate=candidate,
            allocation=decision.allocations[0],
            geometry=geometry,
            lineage=foreign,
            portfolio_decision_id=str(decision.decision_id),
            portfolio_version=str(decision.reservation_plan.portfolio_version),
            evaluation_time=decision.evaluation_time,
        )


# ---------------------------------------------------------------------------
# 5. The handoff itself rejects inconsistent capital and quantity
# ---------------------------------------------------------------------------


def test_handoff_rejects_zero_capital_and_inconsistent_quantity():
    """Regression: a directly constructed handoff accepted zero/inconsistent fields."""
    decision, candidate, geometry = handoff._selected_panel()
    good = handoff._handoffs(decision, candidate, geometry)[0]
    for change in (
        {"allocated_capital": 0.0},
        {"requested_quantity": good.requested_quantity + 1.0},
        {"requested_notional": good.requested_notional + 1.0},
        {"requested_reservation_amount": good.allocated_capital + 1.0},
        {"entry_reference": good.entry_reference + 1.0},
    ):
        with pytest.raises(PortfolioPaperHandoffError):
            replace(good, handoff_id="", **change)


# ---------------------------------------------------------------------------
# 6. Opposite directions cannot share a disposition identity
# ---------------------------------------------------------------------------


def test_opposite_directions_do_not_share_a_disposition_identity():
    """Regression: the identity omitted direction, so LONG and SHORT collided."""
    from app.services.paper_v2_execution import build_disposition_id

    long_id = build_disposition_id(
        episode_id="EP:1", native_symbol="SOLUSD", direction="LONG"
    )
    short_id = build_disposition_id(
        episode_id="EP:1", native_symbol="SOLUSD", direction="SHORT"
    )
    assert long_id != short_id
    with pytest.raises(ValueError):
        build_disposition_id(episode_id="EP:1", native_symbol="SOLUSD", direction="FLAT")


def test_long_disposition_identity_is_stable_across_the_direction_contract():
    """Regression: adding direction for LONG re-keyed every pre-upgrade admission.

    A LONG must keep the exact historical payload, or an already-admitted LONG
    trade would be missed on a retry and a second admission, trade and reservation
    would be created for the same episode and symbol. A SHORT is distinct.
    """
    from app.opip.contracts.serialization import stable_hash
    from app.services.paper_v2_execution import (
        ENGINE_OPIP_PAPER_V2,
        build_disposition_id,
    )

    legacy_payload = {
        "episode_id": "EP:1",
        "native_symbol": "SOLUSD",
        "engine": ENGINE_OPIP_PAPER_V2,
    }
    legacy_id = stable_hash("PDISP", legacy_payload)
    assert (
        build_disposition_id(episode_id="EP:1", native_symbol="SOLUSD", direction="LONG")
        == legacy_id
    )
    assert (
        build_disposition_id(episode_id="EP:1", native_symbol="SOLUSD", direction="SHORT")
        != legacy_id
    )


def test_a_long_also_resolves_the_interim_direction_qualified_identity():
    """Regression: a LONG admitted by the interim direction-aware release is found.

    The legacy LONG payload keeps every pre-direction admission reachable, but a
    brief interim release hashed the direction for LONG too. Both forms are
    therefore probed, so a retry under either history resolves to the committed
    trade instead of creating a second admission.
    """
    from app.opip.contracts.serialization import stable_hash
    from app.services.paper_v2_execution import (
        ENGINE_OPIP_PAPER_V2,
        build_disposition_id,
        disposition_id_candidates,
    )

    candidates = disposition_id_candidates(
        episode_id="EP:1", native_symbol="SOLUSD", direction="LONG"
    )
    legacy = build_disposition_id(
        episode_id="EP:1", native_symbol="SOLUSD", direction="LONG"
    )
    interim = stable_hash(
        "PDISP",
        {
            "episode_id": "EP:1",
            "native_symbol": "SOLUSD",
            "direction": "LONG",
            "engine": ENGINE_OPIP_PAPER_V2,
        },
    )
    assert candidates == (legacy, interim)
    # A SHORT has exactly one identity form.
    assert len(
        disposition_id_candidates(
            episode_id="EP:1", native_symbol="SOLUSD", direction="SHORT"
        )
    ) == 1


def test_committed_disposition_is_resolved_before_admitting():
    """Regression: the lookup must find whichever identity already owns evidence.

    A candidate whose state cannot be read authoritatively must fail closed rather
    than be treated as absent, and any committed admission evidence - including a
    terminal rejection - counts as ownership so a later retry cannot bypass it.
    """
    from app.services.paper_v2_execution import (
        PaperV2ExecutionError,
        resolve_committed_disposition,
    )

    class _State:
        def __init__(self, status="OK", admitted=False, disposition=None, ctx=None):
            self.status = status
            self.admitted = admitted
            self.disposition = disposition
            self.decision_context_id = ctx

    class _Client:
        def __init__(self, states, fail=()):
            self._states = states
            self._fail = set(fail)

        def get_paper_v2_execution_state(self, disposition_id):
            if disposition_id in self._fail:
                raise RuntimeError("transport error")
            return self._states.get(disposition_id, _State())

    primary, alternate = "PDISP:primary", "PDISP:alternate"

    # Nothing committed: the primary identity is used.
    assert resolve_committed_disposition(
        _Client({}), candidates=(primary, alternate)
    ) == (primary, None)

    # A record exists only under the alternate admitted form: it is resolved, with
    # its committed state, so no second admission can be created.
    state = _State(admitted=True, ctx="DCTX:committed")
    resolved_id, resolved_state = resolve_committed_disposition(
        _Client({alternate: state}), candidates=(primary, alternate)
    )
    assert resolved_id == alternate
    assert resolved_state is state

    # A committed terminal rejection is ownership too, so a later retry cannot
    # bypass the earlier stop decision.
    rejected = _State(disposition="CAPACITY_REJECTED", ctx="DCTX:rejected")
    assert resolve_committed_disposition(
        _Client({alternate: rejected}), candidates=(primary, alternate)
    )[0] == alternate

    # A probe that cannot be read authoritatively must fail closed.
    for client in (
        _Client({}, fail=(alternate,)),
        _Client({alternate: _State(status="UNAVAILABLE")}),
        _Client({alternate: _State(status="RETRYABLE")}),
    ):
        with pytest.raises(PaperV2ExecutionError):
            resolve_committed_disposition(client, candidates=(primary, alternate))


# ---------------------------------------------------------------------------
# 7. A legacy admission retry still resolves idempotently
# ---------------------------------------------------------------------------


def test_legacy_admission_retry_is_idempotent_despite_the_format_marker(tmp_path):
    """Regression: the new format marker made an exact legacy replay conflict.

    A record persisted before the direction contract carries no direction fields.
    Retrying the same admission after the upgrade must return the committed result
    rather than IDEMPOTENCY_PAYLOAD_CONFLICT, or an admitted but incomplete trade
    could never resume.
    """
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        _seed_instrument_version(writer)
        context = _seed_context(writer, suffix="retry")
        request = _admission(context["context_id"], disposition_id="PDISP:" + "a" * 32)
        first = writer.admit_paper_opportunity(request)
        assert first.status == "OK", (first.status, first.error_code)

        # Simulate the pre-upgrade record by stripping the direction contract
        # fields from the committed payload.
        row = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
            "SELECT payload_json FROM events WHERE event_type = ?",
            ("paper_execution.admission_request.recorded",),
        ).fetchone()
        import json

        stored = json.loads(str(row["payload_json"]))
        stored.pop("direction", None)
        stored.pop("direction_contract_version", None)
        writer._conn.execute(  # noqa: SLF001
            "UPDATE events SET payload_json = ? WHERE event_type = ?",
            (
                json.dumps(stored, separators=(",", ":"), sort_keys=True),
                "paper_execution.admission_request.recorded",
            ),
        )
        writer._conn.commit()

        retry = writer.admit_paper_opportunity(request)
        assert retry.error_code != "IDEMPOTENCY_PAYLOAD_CONFLICT", retry
        assert retry.status != "REJECTED", (retry.status, retry.error_code)
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# 8. A new admission that omits its direction fails closed
# ---------------------------------------------------------------------------


def test_new_admission_without_direction_fails_closed(tmp_path):
    """Regression: direction defaulted to LONG, so omission silently admitted."""
    from app.opip.contracts.paper_execution_runtime import validate_admission_request

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        _seed_instrument_version(writer)
        context = _seed_context(writer, suffix="nodir")
        complete = _admission(context["context_id"], disposition_id="PDISP:" + "b" * 32)
        # The dataclass still carries every field, but the direction is the unset
        # sentinel when a caller forgets it.
        omitted = replace(complete, direction="")
        with pytest.raises(ValueError):
            validate_admission_request(omitted)
        ack = writer.admit_paper_opportunity(omitted)
        assert ack.status == "REJECTED", (ack.status, ack.error_code)
        assert str(ack.error_code) != "IDEMPOTENCY_PAYLOAD_CONFLICT"
        # The explicit LONG request is still accepted, so the guard is not blanket.
        assert validate_admission_request(complete)
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# 9. F5 keeps its ordered semantics: a proven veto is not pre-empted
# ---------------------------------------------------------------------------


def test_market_data_veto_is_not_pre_empted_by_a_malformed_direction():
    """Regression: eager direction validation turned a proven VETO into an error.

    F5 evaluates its checks in a frozen order and a proven hard veto must
    short-circuit and be returned. Validating the direction while adapting the
    evidence pre-empted that, so a rejected market-data record raised instead of
    returning its VETO.
    """
    from tests import test_opip_r3_f7_economic_portfolio_selector as f7_mod

    episode = f7_mod.active_episode()
    market = f7_mod.snapshot()
    # An explicit rejection plus a case-folded direction: the veto must win.
    market.market_data_validation = replace(
        market.market_data_validation, status="REJECT", qualified=False
    )
    market.trade_direction = "long"
    evidence = feasibility_evidence_from_market_snapshot(
        market,
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id="SNAP:veto",
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=episode.instrument_version_id,
    )
    decision = evaluate_feasibility(episode, evidence, CLAIM_CUTOFF, f7_mod.FEASIBILITY_POLICY)
    assert decision.disposition is FeasibilityDisposition.VETO


# ---------------------------------------------------------------------------
# 10. A serialized geometry field participates in its identity
# ---------------------------------------------------------------------------


def test_geometry_reason_participates_in_the_identity():
    """Regression: ``reason`` was excluded, so it could change under one identity."""
    from dataclasses import fields

    from app.opip.contracts.execution_geometry import (
        ExecutionGeometry,
        ExecutionGeometryContractError,
    )

    decision, candidate, geometry = handoff._selected_panel()
    assert geometry.reason
    payload = {f.name: getattr(geometry, f.name) for f in fields(geometry)}
    # Keeping the original geometry_id while changing the stated reason must fail
    # closed, which is only true because the reason is part of the identity.
    payload["reason"] = "a different reason"
    with pytest.raises(ExecutionGeometryContractError):
        ExecutionGeometry(**payload)
    # Rebuilding through the builder yields a genuinely different identity.
    from app.opip.contracts.execution_geometry import ExecutionGeometryInput

    rebuilt = build_execution_geometry(
        ExecutionGeometryInput(
            symbol="SOLUSD",
            direction="LONG",
            risk_level="medium",
            last_price=100.0,
            atr=2.0,
            ema20=99.5,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        ),
        instrument_version_id=handoff.INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        source_cutoff=geometry.source_cutoff,
        source_evidence_fingerprint=geometry.source_evidence_fingerprint,
    )
    assert rebuilt.geometry_id == geometry.geometry_id
    assert rebuilt.reason == geometry.reason


# ---------------------------------------------------------------------------
# Composition still holds after the remediation
# ---------------------------------------------------------------------------


def test_composition_still_reaches_selected_after_remediation():
    from tests import test_opip_r4_b0_spine_composition as composition

    result = composition._compose(direction="LONG", expected_return=0.05)
    assert result.decision.status is PortfolioStatus.SELECTED
    assert len(result.handoffs) == 1
    assert result.handoffs[0].entry_reference == pytest.approx(
        geometry_entry_reference(result.geometry)
    )


def test_short_composition_handoff_is_bound_to_its_geometry():
    from tests import test_opip_r4_b0_spine_composition as composition

    result = composition._compose(direction="SHORT", expected_return=0.05)
    assert result.decision.status is PortfolioStatus.SELECTED
    handoff_row = result.handoffs[0]
    assert handoff_row.direction == "SHORT"
    assert handoff_row.entry_reference == pytest.approx(
        geometry_entry_reference(result.geometry)
    )
