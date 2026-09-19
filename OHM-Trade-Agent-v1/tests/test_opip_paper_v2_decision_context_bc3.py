"""B/C-3 increment 3b: canonical decision-context bridge tests.

Proves the dependency-inversion boundary: runtime code obtains the canonical
``decision_context_id`` B/C-1 requires through the canonical adapter only, without
importing the decision-intelligence plane, and with fail-closed behaviour at every
step.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.opip.canonical.decision_context_bridge import (
    COMMITTED_ACK_STATUSES,
    CanonicalCommitError,
    DecisionContextFacts,
    build_decision_context_payload,
    commit_decision_context,
    decision_context_intent,
    require_canonical_commit,
    submit_decision_context,
)
from app.opip.canonical.models import WriterAck
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.identity import InstrumentVersion
from app.opip.decision.versioning import GATE_POLICY_VERSION, gate_policy_fingerprint
from app.services.paper_v2_instrument_registration import (
    ensure_instrument_version_registered,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

#: The exact runtime roots the frozen boundary test scans.
RUNTIME_ROOTS = (
    APP_ROOT / "services",
    APP_ROOT / "jobs",
    APP_ROOT / "api",
    APP_ROOT / "opip" / "discovery",
    APP_ROOT / "opip" / "decision",
    APP_ROOT / "opip" / "risk",
)

DI_ROOT = "app.opip.decision_intelligence"

#: B/C-3 runtime modules: everything this branch added to a runtime root.
BC3_RUNTIME_MODULES = (
    APP_ROOT / "services" / "paper_v2_activation.py",
    APP_ROOT / "services" / "paper_v2_quote_evidence.py",
    APP_ROOT / "services" / "paper_v2_instrument_registration.py",
)


def _version(**overrides) -> InstrumentVersion:
    fields = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOL/USD",
        "version": 1,
        "reference_data_version": "kraken-ref-1",
        "observed_at_utc": NOW - timedelta(minutes=5),
    }
    fields.update(overrides)
    return InstrumentVersion(**fields)


def _facts(**overrides) -> DecisionContextFacts:
    fields = {
        "candidate_id": "candidate-1",
        "episode_id": "episode-1",
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "instrument_registration_event_id": "EVT:instrument-proof-1",
        "snapshot_id": "snapshot-1",
        "snapshot_hash": "snapshot-hash-1",
        "evaluation_time": NOW - timedelta(seconds=30),
        "evidence_cutoff": NOW - timedelta(seconds=60),
        "policy_version": GATE_POLICY_VERSION,
        "policy_fingerprint": gate_policy_fingerprint(),
        "producing_component": "bc3-test",
        "artifact_or_build_id": "build-bc3",
        "process_instance_id": "proc-bc3",
        "emitted_at": NOW - timedelta(seconds=20),
        "source_record_refs": ("source:bc3",),
    }
    fields.update(overrides)
    return DecisionContextFacts(**fields)


@pytest.fixture
def writer(tmp_path) -> CanonicalWriter:
    instance = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _rows(writer: CanonicalWriter, event_type: str) -> list[dict]:
    import json

    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


# ---------------------------------------------------------------------------
# 1. Frozen runtime-import boundary
# ---------------------------------------------------------------------------


def test_runtime_roots_do_not_import_decision_intelligence_directly():
    """The frozen rule, re-proven over the runtime roots including B/C-3 modules.

    This must hold without editing the frozen acceptance test: no runtime module -
    and in particular no B/C-3 module - may import the DI plane directly.
    """
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name == DI_ROOT or name.startswith(DI_ROOT + "."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert offenders == [], f"runtime roots must not import DI: {offenders}"


def test_bc3_runtime_modules_import_the_adapter_instead_of_di():
    """B/C-3 runtime modules are DI-free; the adapter carries the DI dependency."""
    for path in BC3_RUNTIME_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        assert not any(
            name == DI_ROOT or name.startswith(DI_ROOT + ".") for name in names
        ), f"{path.name} must not import the DI plane"
        source = path.read_text(encoding="utf-8").lower()
        assert DI_ROOT.lower() not in source, f"{path.name} mentions the DI plane"


def test_instrument_registration_uses_the_adapter_for_commit_proof(writer):
    """The registration module shares the adapter's commit-proof rule."""
    ref = ensure_instrument_version_registered(_version(), client=writer)
    assert ref.status in COMMITTED_ACK_STATUSES
    # The adapter is a canonical-layer module, so the services layer may use it
    # without importing DI.
    assert CanonicalCommitError.__module__.startswith("app.opip.canonical")


# ---------------------------------------------------------------------------
# 2/3. Valid context
# ---------------------------------------------------------------------------


def test_context_payload_is_built_by_the_frozen_contract():
    from app.opip.decision_intelligence.events import context_identity_v2

    payload = build_decision_context_payload(_facts())
    # The identity is the DI helper's output, not a reimplementation.
    assert payload["context_id"] == context_identity_v2(payload)
    assert payload["context_id"] != "pending"
    assert payload["environment"] == "paper"
    assert payload["eligibility"] is True
    # The contract version is explicit, so a v1 and a v2 context are never
    # confused for one another.
    assert payload["schema_version"] == 2


def test_context_commits_canonically_and_returns_the_stored_id(writer):
    context_id, proof = commit_decision_context(_facts(), client=writer)
    rows = _rows(writer, "decision_intelligence.context.recorded")
    assert len(rows) == 1
    assert rows[0]["context_id"] == context_id
    assert proof.status == "OK"
    assert proof.event_id
    assert proof.idempotency_key.startswith("decision_intelligence.context.recorded:")


def test_context_references_the_registered_instrument_lineage(writer):
    """End-to-end: registration first, then a context naming that exact version."""
    registered = ensure_instrument_version_registered(_version(), client=writer)
    assert registered.event_id

    context_id, _proof = commit_decision_context(
        _facts(instrument_registration_event_id=registered.event_id),
        client=writer,
    )
    rows = _rows(writer, "decision_intelligence.context.recorded")
    assert rows[0]["instrument_version"] == INSTRUMENT_VERSION_ID
    assert context_id == rows[0]["context_id"]
    # The writer can resolve the instrument the context names.
    resolved = writer._load_instrument_version_by_id(INSTRUMENT_VERSION_ID)  # noqa: SLF001
    assert resolved["instrument_version_id"] == INSTRUMENT_VERSION_ID


def test_context_carries_no_fabricated_v1_committee_facts():
    """B/C-3A removed the v1 committee facts; the adapter must not reintroduce them.

    Each of these either has no truthful production source or was conceptually
    something else (the registration coordinate is not an input watermark), so a
    schema-v2 context must omit them rather than carry a placeholder.
    """
    payload = build_decision_context_payload(_facts())
    for absent in (
        "evaluation_id",
        "consumed_input_watermark",
        "feature_version",
        "detector_version",
        "forecast_version",
        "candidate_set_ref",
        "portfolio_version_ref",
        "missingness",
        "source_availability_times",
        "evidence_eligibility_manifest",
    ):
        assert absent not in payload, absent


def test_context_policy_identity_is_the_real_qualification_policy():
    """The committed policy identity is the live gate policy, not a caller string."""
    payload = build_decision_context_payload(_facts())
    assert payload["policy_version"] == GATE_POLICY_VERSION
    assert payload["policy_fingerprint"] == gate_policy_fingerprint()
    assert payload["policy_fingerprint"].startswith("GPF:")


def test_committed_context_is_reconstructed_as_a_schema_v2_context(writer):
    """The reader reconstructs the adapter's context as v2, never as v1.

    This is the integration the schema version exists for: the adapter emits the
    context event under ``schema_version`` 2, so the reader hydrates it into the
    v2 store and never into the v1 store.
    """
    from app.opip.decision_intelligence.evidence_reader import (
        read_di_evidence_snapshot,
    )

    context_id, _proof = commit_decision_context(_facts(), client=writer)
    snapshot = read_di_evidence_snapshot(db_path=writer.db_path)
    assert set(snapshot.contexts_v2) == {context_id}
    assert snapshot.contexts == {}


def test_intent_uses_the_canonical_context_idempotency_key():
    from app.opip.decision_intelligence.events import context_idempotency_key

    payload = build_decision_context_payload(_facts())
    intent = decision_context_intent(payload)
    assert intent.idempotency_key == context_idempotency_key(
        context_id=payload["context_id"]
    )
    assert intent.priority == "LOW"


# ---------------------------------------------------------------------------
# 4. Exact retry
# ---------------------------------------------------------------------------


def test_repeat_commit_is_duplicate_ok_with_no_second_row(writer):
    first_id, first = commit_decision_context(_facts(), client=writer)
    second_id, second = commit_decision_context(_facts(), client=writer)
    assert first.status == "OK"
    assert second.status == "DUPLICATE_OK"
    assert second_id == first_id
    assert second.event_id == first.event_id
    assert len(_rows(writer, "decision_intelligence.context.recorded")) == 1


def test_restart_commit_is_duplicate_ok(tmp_path):
    db_path = tmp_path / "canonical.sqlite3"
    first_writer = CanonicalWriter(db_path)
    try:
        first_id, first = commit_decision_context(_facts(), client=first_writer)
    finally:
        first_writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        second_id, second = commit_decision_context(_facts(), client=reopened)
        assert second_id == first_id
        assert second.status == "DUPLICATE_OK"
        assert second.event_id == first.event_id
        assert len(_rows(reopened, "decision_intelligence.context.recorded")) == 1
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 5. Changed payload conflict
# ---------------------------------------------------------------------------


def test_changed_payload_under_the_same_identity_fails_closed(writer):
    """Different semantic facts can never silently reuse a committed context."""
    original = build_decision_context_payload(_facts())
    submit_decision_context(original, client=writer)

    # Same raw context identity is impossible to force while changing facts -
    # the identity is derived - so the conflict is proven the canonical way: the
    # writer rejects a second payload claiming a committed identity.
    mutated = dict(original)
    mutated["snapshot_hash"] = "different-snapshot-hash"
    ack = writer.submit(decision_context_intent(mutated))
    assert ack.status == "REJECTED"
    assert ack.error_code in {
        "IDEMPOTENCY_PAYLOAD_CONFLICT",
        "INVALID_INTENT",
    }
    assert len(_rows(writer, "decision_intelligence.context.recorded")) == 1


def test_changed_facts_produce_a_different_context_identity(writer):
    """Control: because identity is derived from facts, changed facts are a new id."""
    first_id, _ = commit_decision_context(_facts(), client=writer)
    second_id, second = commit_decision_context(
        _facts(snapshot_hash="snapshot-hash-2"), client=writer
    )
    assert second_id != first_id
    assert second.status == "OK"
    assert len(_rows(writer, "decision_intelligence.context.recorded")) == 2


# ---------------------------------------------------------------------------
# 6. Missing instrument
# ---------------------------------------------------------------------------


def test_missing_registration_proof_fails_closed_before_commit(writer):
    """An unregistered instrument cannot produce context evidence."""
    for missing in ("", "   ", None):
        with pytest.raises(ValueError, match="instrument_registration_event_id"):
            build_decision_context_payload(
                _facts(instrument_registration_event_id=missing)
            )
    assert _rows(writer, "decision_intelligence.context.recorded") == []


def test_unregistered_instrument_still_fails_closed_in_the_writer(writer):
    """The frozen writer check that makes registration mandatory."""
    with pytest.raises(ValueError, match="not a registered canonical instrument version"):
        writer._load_instrument_version_by_id("INSTR:kraken:ETH:USD:1")  # noqa: SLF001


# ---------------------------------------------------------------------------
# 7. Missing source field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "candidate_id",
        "episode_id",
        "snapshot_id",
        "snapshot_hash",
        "policy_version",
        "policy_fingerprint",
        "producing_component",
        "artifact_or_build_id",
        "process_instance_id",
    ],
)
def test_missing_required_source_fact_fails_closed(writer, field_name):
    for missing in ("", "   "):
        with pytest.raises(ValueError, match=field_name):
            build_decision_context_payload(_facts(**{field_name: missing}))
    assert _rows(writer, "decision_intelligence.context.recorded") == []


def test_missing_source_record_refs_fails_closed():
    with pytest.raises(ValueError, match="source_record_refs"):
        build_decision_context_payload(_facts(source_record_refs=()))


def test_non_canonical_instrument_version_is_rejected():
    with pytest.raises(ValueError, match="instrument_version_id"):
        build_decision_context_payload(
            _facts(instrument_version_id=" INSTR:kraken:SOL:USD:1")
        )


def test_evidence_cutoff_after_evaluation_time_fails_closed():
    with pytest.raises(ValueError, match="evidence_cutoff"):
        build_decision_context_payload(
            _facts(
                evaluation_time=NOW - timedelta(seconds=90),
                evidence_cutoff=NOW - timedelta(seconds=10),
            )
        )


def test_invalid_facts_type_fails_closed():
    with pytest.raises(ValueError, match="DecisionContextFacts"):
        build_decision_context_payload({"candidate_id": "x"})


# ---------------------------------------------------------------------------
# 8. Writer failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["REJECTED", "RETRYABLE", "SPOOLED", "DISABLED"])
def test_non_committed_ack_fails_closed(status):
    class _Client:
        def submit(self, intent):
            return WriterAck(status=status, error_code="NOT_COMMITTED")

    with pytest.raises(CanonicalCommitError, match="not proven committed"):
        commit_decision_context(_facts(), client=_Client())


def test_missing_or_malformed_ack_fails_closed():
    class _Silent:
        def submit(self, intent):
            return None

    class _Bare:
        def submit(self, intent):
            return WriterAck(status="OK", event_id=None)

    with pytest.raises(CanonicalCommitError, match="not acknowledged"):
        commit_decision_context(_facts(), client=_Silent())
    with pytest.raises(CanonicalCommitError, match="missing canonical identity"):
        commit_decision_context(_facts(), client=_Bare())


def test_writer_exception_propagates(writer):
    class _Unavailable:
        def submit(self, intent):
            raise RuntimeError("canonical writer unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        commit_decision_context(_facts(), client=_Unavailable())


def test_ack_helper_requires_canonical_position():
    ack = WriterAck(status="OK", event_id="EVT:1", history_epoch=None, local_sequence=1)
    with pytest.raises(CanonicalCommitError, match="missing canonical identity"):
        require_canonical_commit(ack, idempotency_key="k", what="thing")


# ---------------------------------------------------------------------------
# 9. Authority isolation
# ---------------------------------------------------------------------------


def test_only_context_evidence_is_written(writer):
    """The adapter writes exactly one event type: no DI authority is activated."""
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_EVENT_TYPES

    commit_decision_context(_facts(), client=writer)

    written = {
        str(row["event_type"])
        for row in writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT DISTINCT event_type FROM events"
        ).fetchall()
    }
    assert written == {"decision_intelligence.context.recorded"}

    # Explicitly: no committee/AI authority event was emitted.
    for event_type in sorted(DECISION_INTELLIGENCE_EVENT_TYPES):
        if event_type == "decision_intelligence.context.recorded":
            continue
        assert _rows(writer, event_type) == [], f"unexpected {event_type}"


def test_adapter_module_holds_no_decision_or_execution_authority():
    """The adapter is evidence plumbing, not the DI runtime plane.

    Scans referenced identifiers and attribute/call names rather than raw text, so
    the module's documentation of what it deliberately does *not* do is not
    confused with capability.
    """
    path = APP_ROOT / "opip" / "canonical" / "decision_context_bridge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            referenced.add(node.name.lower())

    for token in (
        "place_order",
        "create_order",
        "submit_order",
        "kraken_private",
        "invoke",
        "request_committee",
        "admission_decision",
        "risk_decision",
    ):
        assert not any(token in name for name in referenced), (
            f"adapter must not reference {token}"
        )


def test_adapter_imports_only_context_evidence_from_the_di_plane():
    """The adapter's DI surface is the context contract alone."""
    path = APP_ROOT / "opip" / "canonical" / "decision_context_bridge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    di_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == DI_ROOT or node.module.startswith(DI_ROOT + "."):
                di_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            di_imports.update(
                alias.name
                for alias in node.names
                if alias.name == DI_ROOT or alias.name.startswith(DI_ROOT + ".")
            )
    assert di_imports == {
        "DECISION_INTELLIGENCE_CONTEXT_RECORDED",
        "context_idempotency_key",
        "context_identity_v2",
        "DecisionContextV2",
        "Provenance",
    }
