"""Cross-artifact completeness reconciliation for canonical paper outcomes.

The canonical SQLite snapshot, ``paper_trading/state.json`` and the evidence-gap
spool cannot be captured in one transaction, so a replica generation can be
internally hash-valid and provenance-valid while pairing a canonical snapshot
with a *newer* lifecycle state. Concretely: the online backup captures outcomes
A..N, a new outcome N+1 then commits to the live store, state is persisted with
N+1 marked ``COMMITTED``, the gap spool is clear, and only then are state and
spool copied.

``COMMITTED`` is a *claim* that the canonical outcome exists. Before this
reconciliation the claim was trusted, so a generation could present 99 verified
canonical outcomes alongside a newer committed lifecycle - and the 99 could
remain ``FINAL_PAPER`` and primary-supervised eligible. A missing committed
canonical outcome means the whole population is incomplete, not that one row is
unusable, so every otherwise-valid row must lose supervised eligibility until
the generation is consistent.

These tests pin that behaviour, the fail-closed direction of each failure mode,
and the legacy and supersession boundaries around it.
"""
from __future__ import annotations

import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.schema import fsync_directory_required
from app.opip.contracts.paper_outcome import build_terminal_outcome_payload
from app.opip.learning import readiness as readiness_module
from app.opip.learning.linkage import (
    REASON_COMMITTED_IDENTITY_INVALID,
    REASON_COMMITTED_MISSING_CANONICAL,
    LinkageStatus,
    OutcomeSourceQuality,
    build_learning_linkage_records,
    committed_outcome_reconciliation_reasons,
    paper_outcome_population_incomplete,
    resolve_effective_outcome_ids,
)
from app.opip.learning.readiness import build_ml_data_readiness_report

ENTER = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
_CLOCK = ENTER.isoformat().replace("+00:00", "Z")


def _outcome(index: int = 0) -> dict:
    """One valid terminal outcome, unique per index."""
    return build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id=f"PAPER:{index:020d}",
        episode_id=f"EP:{index}",
        cohort_id="COH:1",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status="CLOSED",
        exit_reason="STOP",
        exit_price=98.0,
        entry_timestamp=ENTER,
        exit_timestamp=EXIT,
        capital_committed=1000.0,
        gross_pnl=-25.0,
        fees_paid=4.0,
        net_pnl=-29.0,
        net_pnl_pct=-2.9,
        final_revision=7,
        terminal_event_id="PTE:" + f"{index:024d}",
        candidate_id="CAND:1",
        decision_context_id="DI-CONTEXT:" + "c" * 32,
    )


def _canonical_row(index: int = 0) -> dict:
    return {
        "snapshot_id": f"SNAP:{index}",
        "episode_id": f"EP:{index}",
        "decision_status": "QUALIFIED",
    }


def _ml_row(index: int = 0) -> dict:
    return {
        "canonical_snapshot_id": f"SNAP:{index}",
        "ml_snapshot_id": f"ML:{index}",
        "feature_snapshot": {
            "snapshot_id": f"ML:{index}",
            "direction": "LONG",
            "decision_at_utc": _CLOCK,
            "features": [{"name": "momentum", "value": 1.0, "visible_at_utc": _CLOCK}],
        },
    }


def _lifecycle(
    index: int = 0,
    delivery: str | None = "COMMITTED",
    outcome_id: str | None = None,
    *,
    include_outbox: bool = True,
) -> dict:
    row: dict = {
        "paper_trade_id": f"PAPER:{index:020d}",
        "episode_id": f"EP:{index}",
        "status": "CLOSED",
        "revision": 8,
        "direction": "LONG",
        "closed_at": EXIT.isoformat(),
        "exit_price": 98.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "outcome": "LOSS",
    }
    if include_outbox:
        outbox: dict = {"delivery": delivery, "gap_id": None}
        if outcome_id is not None:
            outbox["outcome_id"] = outcome_id
        row["outcome_outbox"] = outbox
    return row


def _report(count: int, lifecycles: list[dict]) -> object:
    return build_ml_data_readiness_report(
        canonical_rows=[_canonical_row(i) for i in range(count)],
        ml_snapshot_rows=[_ml_row(i) for i in range(count)],
        paper_trade_rows=lifecycles,
        paper_outcome_rows=[_outcome(i) for i in range(count)],
    )


# ---------------------------------------------------------------------------
# Case 1 - control: every committed claim is backed by canonical authority
# ---------------------------------------------------------------------------


def test_control_all_committed_outcomes_present_is_complete():
    lifecycles = [
        _lifecycle(i, "COMMITTED", _outcome(i)["outcome_id"]) for i in range(3)
    ]
    report = _report(3, lifecycles)

    assert report.paper_outcome_population_incomplete is False
    assert report.paper_outcome_incomplete_reasons == ()
    assert "PAPER_OUTCOME_POPULATION_INCOMPLETE" not in report.blockers
    # A healthy generation must still be trainable - the reconciliation must not
    # be so eager that it refuses valid evidence.
    assert report.final_supervised_truth_count == 3
    assert report.primary_supervised_usable_rows == 3


# ---------------------------------------------------------------------------
# Case 2 - the blocking capture race
# ---------------------------------------------------------------------------


def test_committed_outcome_absent_from_canonical_makes_population_incomplete():
    """The race: A and B are canonical, but C is committed and absent.

    A and B individually exist, yet they must NOT remain trainable: a missing
    committed canonical outcome means the population cannot be certified whole.
    """
    lifecycles = [
        _lifecycle(0, "COMMITTED", _outcome(0)["outcome_id"]),
        _lifecycle(1, "COMMITTED", _outcome(1)["outcome_id"]),
        # Committed, well-formed identity, but absent from the canonical snapshot.
        _lifecycle(2, "COMMITTED", _outcome(2)["outcome_id"]),
    ]
    report = _report(2, lifecycles)

    assert report.paper_outcome_population_incomplete is True
    assert REASON_COMMITTED_MISSING_CANONICAL in report.paper_outcome_incomplete_reasons
    # Population-level fail-closed: the present rows lose eligibility too.
    assert report.final_supervised_truth_count == 0
    assert report.primary_supervised_usable_rows == 0
    assert "PAPER_OUTCOME_POPULATION_INCOMPLETE" in report.blockers


def test_race_removes_supervised_eligibility_from_present_rows_at_linkage():
    """The same race at the linkage layer, where FINAL_PAPER is decided."""
    records = build_learning_linkage_records(
        canonical_rows=[_canonical_row(0)],
        ml_snapshot_rows=[_ml_row(0)],
        paper_trade_rows=[
            _lifecycle(0, "COMMITTED", _outcome(0)["outcome_id"]),
            _lifecycle(1, "COMMITTED", _outcome(1)["outcome_id"]),
        ],
        paper_outcome_rows=[_outcome(0)],
        paper_outcome_incomplete_reasons=(REASON_COMMITTED_MISSING_CANONICAL,),
        paper_outcome_population_incomplete=True,
    )
    assert len(records) == 1
    # The row whose canonical outcome IS present must still be refused.
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False


# ---------------------------------------------------------------------------
# Case 3/4 - malformed committed identity
# ---------------------------------------------------------------------------


def test_committed_without_outcome_id_is_incomplete():
    report = _report(1, [_lifecycle(0, "COMMITTED")])
    assert report.paper_outcome_population_incomplete is True
    assert REASON_COMMITTED_IDENTITY_INVALID in report.paper_outcome_incomplete_reasons
    assert report.final_supervised_truth_count == 0


@pytest.mark.parametrize(
    "value",
    [pytest.param(None, id="none"), pytest.param("", id="empty"), pytest.param("   ", id="blank")],
)
def test_committed_with_unusable_outcome_id_is_incomplete(value):
    row = _lifecycle(0, "COMMITTED")
    row["outcome_outbox"]["outcome_id"] = value
    report = _report(1, [row])
    assert report.paper_outcome_population_incomplete is True
    assert REASON_COMMITTED_IDENTITY_INVALID in report.paper_outcome_incomplete_reasons
    assert report.final_supervised_truth_count == 0


def test_non_string_outcome_id_is_incomplete():
    row = _lifecycle(0, "COMMITTED")
    row["outcome_outbox"]["outcome_id"] = 12345
    reasons, missing = committed_outcome_reconciliation_reasons([row], set())
    assert REASON_COMMITTED_IDENTITY_INVALID in reasons
    assert missing == ()


# ---------------------------------------------------------------------------
# Case 5/6 - existing delivery-state failures remain
# ---------------------------------------------------------------------------


def test_pending_still_makes_population_incomplete():
    report = _report(1, [_lifecycle(0, "PENDING", _outcome(0)["outcome_id"])])
    assert report.paper_outcome_population_incomplete is True
    assert report.final_supervised_truth_count == 0


def test_permanent_failure_still_makes_population_incomplete():
    report = _report(
        1, [_lifecycle(0, "PERMANENT_FAILURE", _outcome(0)["outcome_id"])]
    )
    assert report.paper_outcome_population_incomplete is True
    assert report.final_supervised_truth_count == 0


# ---------------------------------------------------------------------------
# Case 7 - legacy compatibility
# ---------------------------------------------------------------------------


def test_legacy_row_without_outbox_is_not_treated_as_missing_canonical():
    """Pre-PR-A rows make no canonical claim, so they are not reconciled."""
    row = _lifecycle(0, include_outbox=False)
    reasons, missing = committed_outcome_reconciliation_reasons([row], set())
    assert reasons == ()
    assert missing == ()


def test_legacy_row_does_not_block_the_population():
    lifecycles = [
        _lifecycle(0, "COMMITTED", _outcome(0)["outcome_id"]),
        _lifecycle(1, include_outbox=False),  # legacy
    ]
    report = _report(1, lifecycles)
    assert report.paper_outcome_population_incomplete is False
    assert REASON_COMMITTED_MISSING_CANONICAL not in report.paper_outcome_incomplete_reasons


def test_non_terminal_rows_are_not_reconciled():
    """An open position has no terminal outcome to reconcile."""
    row = _lifecycle(0, "COMMITTED", _outcome(0)["outcome_id"])
    row["status"] = "OPEN"
    reasons, missing = committed_outcome_reconciliation_reasons([row], set())
    assert reasons == ()
    assert missing == ()


# ---------------------------------------------------------------------------
# Case 8 - governed correction / supersession
# ---------------------------------------------------------------------------


def _correction(original: dict) -> dict:
    return build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id=original["paper_trade_id"],
        episode_id=original["episode_id"],
        cohort_id="COH:1",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status="CLOSED",
        exit_reason="STOP",
        exit_price=98.0,
        entry_timestamp=ENTER,
        exit_timestamp=EXIT,
        capital_committed=1000.0,
        gross_pnl=-10.0,
        fees_paid=4.0,
        net_pnl=-14.0,
        net_pnl_pct=-1.4,
        final_revision=8,
        terminal_event_id="PTE:" + "9" * 24,
        candidate_id="CAND:1",
        decision_context_id="DI-CONTEXT:" + "c" * 32,
        correction_seq=1,
        supersedes_id=original["outcome_id"],
    )


def test_superseded_predecessor_does_not_read_as_missing():
    """Reconciliation must use effective identities.

    A guess that treated every stored record as a candidate would see two
    outcomes here and could wrongly report the lifecycle's claim as missing.
    """
    original = _outcome(0)
    corrected = _correction(original)
    effective = set(resolve_effective_outcome_ids([original, corrected]))
    assert effective == {corrected["outcome_id"]}

    reasons, missing = committed_outcome_reconciliation_reasons(
        [_lifecycle(0, "COMMITTED", corrected["outcome_id"])], effective
    )
    assert reasons == ()
    assert missing == ()


def test_claiming_a_superseded_identity_is_reported_against_effective_set():
    """The predecessor is no longer the effective record, so the claim is unbacked."""
    original = _outcome(0)
    corrected = _correction(original)
    effective = set(resolve_effective_outcome_ids([original, corrected]))

    _, missing = committed_outcome_reconciliation_reasons(
        [_lifecycle(0, "COMMITTED", original["outcome_id"])], effective
    )
    assert REASON_COMMITTED_MISSING_CANONICAL in missing


# ---------------------------------------------------------------------------
# Case 9 - the load-bearing multi-row regression
# ---------------------------------------------------------------------------


def test_one_missing_committed_outcome_blocks_the_whole_population():
    """40 canonical outcomes, 40 COMMITTED claims, one unbacked.

    This is the case the review called most dangerous: 39 valid rows must not
    survive as trainable because the 40th is unbacked.
    """
    lifecycles = [
        _lifecycle(i, "COMMITTED", _outcome(i)["outcome_id"]) for i in range(39)
    ]
    # A well-formed identity for a 40th outcome that the snapshot lacks.
    lifecycles.append(
        _lifecycle(39, "COMMITTED", "PAPER-OUTCOME:" + "f" * 32)
    )
    report = _report(40, lifecycles)

    assert report.paper_outcome_population_incomplete is True
    assert REASON_COMMITTED_MISSING_CANONICAL in report.paper_outcome_incomplete_reasons
    assert report.final_supervised_truth_count == 0
    assert report.primary_supervised_usable_rows == 0


def test_forty_consistent_rows_remain_eligible():
    """Control for the case above: without the unbacked claim all 40 survive."""
    lifecycles = [
        _lifecycle(i, "COMMITTED", _outcome(i)["outcome_id"]) for i in range(40)
    ]
    report = _report(40, lifecycles)

    assert report.paper_outcome_population_incomplete is False
    assert report.final_supervised_truth_count == 40
    assert report.primary_supervised_usable_rows == 40


# ---------------------------------------------------------------------------
# Readiness must actually call the reconciliation
# ---------------------------------------------------------------------------


def test_readiness_combines_delivery_state_and_reconciliation():
    """Pin the wiring so the reconciliation cannot be silently dropped.

    Both checks are required and independent: delivery state answers "did the
    producer acknowledge?", reconciliation answers "does canonical authority back
    that acknowledgement?". The source is inspected because a regression here
    would be invisible to the behavioural tests above only if someone removed the
    call and also stopped passing canonical outcome rows.
    """
    source = inspect.getsource(readiness_module)
    assert "paper_outcome_population_incomplete(" in source
    assert "committed_outcome_reconciliation_reasons(" in source
    assert "resolve_effective_outcome_ids(" in source


def test_unreadable_canonical_identity_graph_fails_closed():
    """A defective identity graph is unprovable completeness, not "no outcomes"."""
    original = _outcome(0)
    duplicate = dict(original)
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical_row(0)],
        ml_snapshot_rows=[_ml_row(0)],
        paper_trade_rows=[_lifecycle(0, "COMMITTED", original["outcome_id"])],
        # resolve_effective_outcome_ids rejects a duplicated identity.
        paper_outcome_rows=[original, duplicate],
    )
    assert report.paper_outcome_population_incomplete is True
    assert "CANONICAL_OUTCOME_IDENTITY_INVALID" in report.paper_outcome_incomplete_reasons
    assert report.final_supervised_truth_count == 0


def test_reason_vocabulary_stays_minimal():
    """Only two new codes were introduced for this defect."""
    assert REASON_COMMITTED_IDENTITY_INVALID == "PAPER_OUTCOME_COMMITTED_IDENTITY_INVALID"
    assert REASON_COMMITTED_MISSING_CANONICAL == "PAPER_OUTCOME_COMMITTED_MISSING_CANONICAL"


# ---------------------------------------------------------------------------
# Publication durability
# ---------------------------------------------------------------------------


def test_manifest_publication_establishes_directory_durability(tmp_path, monkeypatch):
    """``write_replica_manifest`` promises directory durability; prove it.

    The manifest is the provenance contract, so reporting publication as
    successful while the directory entry is not durable would let a crash leave
    a generation directory whose manifest is missing.
    """
    from app.opip.learning import canonical_replica as replica

    synced: list[Path] = []
    monkeypatch.setattr(
        replica, "fsync_directory_required", lambda directory: synced.append(Path(directory))
    )
    target = tmp_path / "gen" / "replica_manifest.json"
    replica.write_replica_manifest({"replica_schema_version": 1}, target)

    assert target.is_file()
    assert synced == [target.parent]


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only directory durability")
def test_generation_and_pointer_publication_are_durable(tmp_path):
    """POSIX: the real authoritative primitive runs on the publication path.

    Windows cannot prove directory durability, so the conftest seam stubs the
    primitive there. On POSIX nothing is stubbed, so this exercises the real
    ``os.fsync(dirfd)`` calls for the manifest, the generation directory and the
    ``current`` pointer - and a durability fault would surface as
    ``CanonicalDurabilityError`` rather than a silent success.
    """
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.learning import canonical_replica as replica

    # The module binds the real primitive; production is not weakened.
    assert replica.fsync_directory_required is fsync_directory_required

    live = tmp_path / "live.sqlite3"
    CanonicalWriter(live).close()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"schema_version": 1, "paper_only": True, "lifecycles": {}}),
        encoding="utf-8",
    )
    staging = tmp_path / "staged"
    replica.export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha="0cd0c30eba0d45fb97aa1032364bfd93be671657",
        paper_state_source=state,
        paper_gap_source=None,
        generation_id="gen-durable",
    )

    host = tmp_path / "host"
    summary = replica.install_replica_generation(
        staging_dir=staging,
        host_root=host,
        expected_source_release_sha="0cd0c30eba0d45fb97aa1032364bfd93be671657",
    )
    assert summary["installed"] == "gen-durable"
    assert replica.resolve_current_generation(host).name == "gen-durable"


def test_durability_failure_prevents_reported_success(tmp_path, monkeypatch):
    """Fail closed: a durability fault must not be reported as publication."""
    from app.opip.canonical.schema import CanonicalDurabilityError
    from app.opip.learning import canonical_replica as replica

    def _boom(_directory):
        raise CanonicalDurabilityError("directory durability unavailable")

    monkeypatch.setattr(replica, "fsync_directory_required", _boom)
    with pytest.raises(CanonicalDurabilityError):
        replica.write_replica_manifest(
            {"replica_schema_version": 1}, tmp_path / "gen" / "replica_manifest.json"
        )


def test_delivery_state_alone_would_have_accepted_the_race():
    """Pin the defect this reconciliation closes, so it cannot silently return.

    Delivery state answers "did the producer acknowledge?". It is a necessary
    check but not a sufficient one: here every acknowledgement is present and the
    gap spool is clear, so the delivery-state check alone reports a complete
    population while canonical authority is actually missing one committed
    outcome. Reconciliation is what refuses it.

    Asserting both halves permanently demonstrates that the two checks are not
    redundant, rather than relying on a one-off manual verification.
    """
    lifecycles = [
        _lifecycle(0, "COMMITTED", _outcome(0)["outcome_id"]),
        _lifecycle(1, "COMMITTED", _outcome(1)["outcome_id"]),
        _lifecycle(2, "COMMITTED", _outcome(2)["outcome_id"]),
    ]

    # The pre-existing check sees nothing wrong: all deliveries acknowledged.
    delivery_incomplete, delivery_reasons = paper_outcome_population_incomplete(
        lifecycles
    )
    assert delivery_incomplete is False
    assert delivery_reasons == ()

    # Reconciliation against the canonical snapshot of the same generation
    # (which contains only outcomes 0 and 1) refuses the population.
    effective = {_outcome(0)["outcome_id"], _outcome(1)["outcome_id"]}
    _, missing = committed_outcome_reconciliation_reasons(lifecycles, effective)
    assert REASON_COMMITTED_MISSING_CANONICAL in missing

    # And the composed readiness decision is incomplete.
    report = _report(2, lifecycles)
    assert report.paper_outcome_population_incomplete is True
    assert report.final_supervised_truth_count == 0
