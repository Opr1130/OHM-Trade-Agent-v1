"""B/C-3A: DecisionContext schema-v2 source-truth tests.

Schema v1 is the backward-compatibility authority and must be untouched: its
validation, identity and idempotency output are pinned here as literal frozen
values. Schema v2 describes only facts the real production qualification path
produces.
"""

from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.serialization import stable_hash
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
    DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    DECISION_INTELLIGENCE_EVENT_TYPES,
    DECISION_INTELLIGENCE_INVOCATION_RECORDED,
    DECISION_INTELLIGENCE_REQUEST_RECORDED,
    DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
    DECISION_INTELLIGENCE_TRANSITION_RECORDED,
    context_idempotency_key,
    context_identity,
    context_identity_v2,
    validate_di_payload,
)
from app.opip.decision_intelligence.identity import (
    DECISION_CONTEXT_SCHEMA_VERSION,
    DECISION_CONTEXT_SCHEMA_VERSION_V2,
    DECISION_CONTEXT_V2_IDENTITY_DOMAIN,
    DecisionContext,
    DecisionContextV2,
    Provenance,
)
from app.opip.decision.versioning import (
    GATE_POLICY_VERSION,
    gate_policy_fingerprint,
)
from app.services.canonical_episode_capture import canonical_episode_snapshot_hash

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: Frozen schema-v1 pins. If any of these change, v1 backward compatibility is
#: broken and the schema-v1 contract has been altered.
V1_FROZEN_IDENTITY = "DI-CONTEXT:7fd4cf2f0b517d8e5736935e0372e848"
V1_FROZEN_IDEMPOTENCY = (
    "decision_intelligence.context.recorded:99c525d4fbf7a66a1a62540be25d7c4c"
)

_V1_FIXTURE = {
    "candidate_id": "candidate-frozen",
    "episode_id": "episode-frozen",
    "evaluation_id": "evaluation-frozen",
    "snapshot_hash": "snapshot-hash-frozen",
    "feature_version": "features-frozen",
    "policy_version": "policy-frozen",
    "supersedes_id": None,
    "supersession_reason": None,
}


def _provenance_contract() -> Provenance:
    """The real Provenance contract used by envelope and direct-construction tests."""
    return Provenance(
        producing_component="paper_v2_execution",
        artifact_or_build_id="ACF:" + "0" * 64,
        process_instance_id="proc-1",
        emitted_at=NOW,
        source_record_refs=("source:1",),
    )


def _v2_contract(payload: dict) -> DecisionContextV2:
    """Build the v2 contract directly, so field type handling is exercised."""
    return DecisionContextV2(
        context_id=payload["context_id"],
        candidate_id=payload["candidate_id"],
        episode_id=payload["episode_id"],
        instrument_version=payload["instrument_version"],
        snapshot_id=payload["snapshot_id"],
        snapshot_hash=payload["snapshot_hash"],
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        policy_version=payload["policy_version"],
        policy_fingerprint=payload["policy_fingerprint"],
        environment=payload["environment"],
        eligibility=payload["eligibility"],
        provenance=_provenance_contract(),
        schema_version=payload["schema_version"],
        supersedes_id=payload.get("supersedes_id"),
        supersession_reason=payload.get("supersession_reason"),
    )


def _v1_payload() -> dict:
    """A complete, valid schema-v1 context payload."""
    payload = {
        "context_id": "pending",
        "candidate_id": "candidate-1",
        "episode_id": "episode-1",
        "evaluation_id": "evaluation-1",
        "instrument_version": "KRAKEN:SOLUSD:v1",
        "snapshot_id": "snapshot-1",
        "snapshot_hash": "snapshot-hash-1",
        "evaluation_time": "2026-09-19T12:00:00Z",
        "evidence_cutoff": "2026-09-19T11:59:59Z",
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "feature_version": "features-v1",
        "policy_version": "policy-v1",
        "detector_version": "detector-v1",
        "forecast_version": "forecast-v1",
        "candidate_set_ref": "candidate-set-1",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "schema_version": 1,
        "supersedes_id": None,
        "supersession_reason": None,
        "provenance": {
            "producing_component": "di-test",
            "artifact_or_build_id": "ACF:" + "0" * 64,
            "process_instance_id": "proc-1",
            "emitted_at": "2026-09-19T12:00:00Z",
            "source_record_refs": ["source:1"],
            "schema_version": 1,
        },
    }
    payload["context_id"] = context_identity(payload)
    return payload


def _v2_payload(**overrides) -> dict:
    """A complete, valid schema-v2 production context payload."""
    payload = {
        "candidate_id": "candidate-1",
        "episode_id": "EP:" + "a" * 24,
        "instrument_version": "INSTR:kraken:SOL:USD:1",
        "snapshot_id": "SNAP:" + "b" * 32,
        "snapshot_hash": "PSNAP:" + "c" * 32,
        "evaluation_time": "2026-09-19T12:00:00Z",
        "evidence_cutoff": "2026-09-19T12:00:00Z",
        "policy_version": GATE_POLICY_VERSION,
        "policy_fingerprint": gate_policy_fingerprint(),
        "environment": "paper",
        "eligibility": True,
        "schema_version": 2,
        "supersedes_id": None,
        "supersession_reason": None,
        "provenance": {
            "producing_component": "paper_v2_execution",
            "artifact_or_build_id": "ACF:" + "0" * 64,
            "process_instance_id": "proc-1",
            "emitted_at": "2026-09-19T12:00:05Z",
            "source_record_refs": ["EP:" + "a" * 24, "SNAP:" + "b" * 32],
            "schema_version": 1,
        },
    }
    payload.update(overrides)
    payload["context_id"] = context_identity_v2(payload)
    return payload


# ---------------------------------------------------------------------------
# 1-3. Schema v1 unchanged
# ---------------------------------------------------------------------------


def test_schema_v1_payload_still_validates_unchanged():
    payload = _v1_payload()
    assert validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload) == payload


def test_schema_v1_identity_output_is_frozen():
    assert context_identity(_V1_FIXTURE) == V1_FROZEN_IDENTITY


def test_schema_v1_idempotency_key_is_frozen():
    assert (
        context_idempotency_key(context_id=V1_FROZEN_IDENTITY) == V1_FROZEN_IDEMPOTENCY
    )


def test_schema_v1_dataclass_still_requires_its_original_fields():
    """V1 fields are not made optional by the v2 addition."""
    from dataclasses import MISSING

    fields = DecisionContext.__dataclass_fields__
    for name in (
        "evaluation_id",
        "consumed_input_watermark",
        "feature_version",
        "detector_version",
        "forecast_version",
        "candidate_set_ref",
        "missingness",
        "source_availability_times",
        "evidence_eligibility_manifest",
    ):
        assert name in fields, name
        # Still mandatory: no default, so the v1 shape is unchanged.
        assert fields[name].default is MISSING, name
        assert fields[name].default_factory is MISSING, name


def test_schema_v1_missing_mandatory_field_still_fails():
    payload = _v1_payload()
    del payload["feature_version"]
    with pytest.raises(ValueError, match="missing fields"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_unsupported_context_schema_version_fails_closed():
    payload = _v1_payload()
    payload["schema_version"] = 3
    with pytest.raises(ValueError, match="unsupported DI context schema_version"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_non_context_event_schema_version_is_still_exactly_one():
    """Unrelated DI events keep their existing schema-version rule."""
    with pytest.raises(ValueError, match="unsupported DI payload schema_version"):
        validate_di_payload(DECISION_INTELLIGENCE_REQUEST_RECORDED, {"schema_version": 2})


# ---------------------------------------------------------------------------
# 4-8. Schema v2 contract
# ---------------------------------------------------------------------------


def test_valid_schema_v2_context_validates():
    payload = _v2_payload()
    normalized = validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)
    assert normalized["context_id"] == payload["context_id"]
    assert normalized["schema_version"] == DECISION_CONTEXT_SCHEMA_VERSION_V2


def test_schema_v2_identity_is_deterministic():
    """Two independently constructed payloads must derive one identity.

    Bound to separate names so the assertion states a real relationship rather
    than comparing an expression with itself.
    """
    first = _v2_payload()
    second = _v2_payload()
    assert context_identity_v2(first) == context_identity_v2(second)


def test_schema_v2_identity_ignores_emitter_only_facts():
    """Restart must reproduce the same identity despite a different emitting run."""
    first = _v2_payload()
    second = _v2_payload()
    second["provenance"] = {
        **second["provenance"],
        "process_instance_id": "a-different-process",
        "emitted_at": "2026-09-19T12:09:59Z",
    }
    assert context_identity_v2(first) == context_identity_v2(second)


def test_schema_v2_identity_changes_with_a_real_decision_fact():
    changed = _v2_payload()
    changed["snapshot_hash"] = "PSNAP:" + "d" * 32
    assert context_identity_v2(changed) != context_identity_v2(_v2_payload())


def test_schema_v2_identity_is_a_distinct_domain_from_v1():
    assert context_identity_v2(_v2_payload()).startswith(
        f"{DECISION_CONTEXT_V2_IDENTITY_DOMAIN}:"
    )
    assert not context_identity(_V1_FIXTURE).startswith(
        f"{DECISION_CONTEXT_V2_IDENTITY_DOMAIN}:"
    )
    assert DECISION_CONTEXT_V2_IDENTITY_DOMAIN != "DI-CONTEXT"


def test_schema_v2_never_collides_with_a_semantically_similar_v1_context():
    """Same candidate/episode/snapshot facts must not produce one identity."""
    shared = {
        "candidate_id": "candidate-shared",
        "episode_id": "episode-shared",
        "snapshot_hash": "snapshot-shared",
    }
    v1_identity = context_identity(
        {
            **shared,
            "evaluation_id": "evaluation-shared",
            "feature_version": "features-shared",
            "policy_version": "policy-shared",
        }
    )
    v2_identity = context_identity_v2(_v2_payload(**shared))
    assert v1_identity != v2_identity


@pytest.mark.parametrize(
    "placeholder_field",
    [
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
    ],
)
def test_schema_v2_rejects_concepts_it_must_not_carry(placeholder_field):
    """The committee/concept-absent fields are not part of the v2 shape at all."""
    payload = _v2_payload()
    payload[placeholder_field] = "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="unknown fields"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "candidate_id",
        "episode_id",
        "instrument_version",
        "snapshot_id",
        "snapshot_hash",
        "policy_version",
        "policy_fingerprint",
        "environment",
    ],
)
def test_missing_required_v2_fact_fails_closed(field_name):
    payload = _v2_payload()
    del payload[field_name]
    with pytest.raises(ValueError, match="missing fields"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


@pytest.mark.parametrize("field_name", ["candidate_id", "policy_fingerprint", "environment"])
def test_blank_required_v2_fact_fails_closed(field_name):
    payload = _v2_payload()
    payload[field_name] = "   "
    payload["context_id"] = context_identity_v2(payload)
    with pytest.raises(ValueError, match=field_name):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_schema_v2_rejects_tampered_content_identity():
    payload = _v2_payload()
    payload["context_id"] = "DI-CONTEXT-V2:" + "0" * 32
    with pytest.raises(ValueError, match="content-derived identity"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_schema_v2_non_boolean_eligibility_fails_closed():
    payload = _v2_payload()
    payload["eligibility"] = "yes"
    payload["context_id"] = context_identity_v2(payload)
    with pytest.raises(ValueError, match="eligibility"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


# ---------------------------------------------------------------------------
# 9-11. Temporal and eligibility rules
# ---------------------------------------------------------------------------


def test_evidence_cutoff_after_evaluation_time_fails():
    payload = _v2_payload(
        evaluation_time="2026-09-19T12:00:00Z",
        evidence_cutoff="2026-09-19T12:00:01Z",
    )
    with pytest.raises(ValueError, match="evidence_cutoff cannot be after"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_decision_boundary_is_a_valid_evidence_cutoff():
    """ARB mapping: evidence_cutoff == evaluation_time == decision_at."""
    payload = _v2_payload(
        evaluation_time="2026-09-19T12:00:00Z",
        evidence_cutoff="2026-09-19T12:00:00Z",
    )
    assert validate_di_payload(
        DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload
    )["evidence_cutoff"] == "2026-09-19T12:00:00Z"


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValueError):
        # Construction is inside the block because the identity helper now
        # rejects an unusable timestamp before validation ever runs. The
        # invariant is unchanged: a naive timestamp cannot produce a v2 context.
        payload = _v2_payload(evaluation_time="2026-09-19T12:00:00")
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


# ---------------------------------------------------------------------------
# 12-13. B/C-1 ancestry (writer integration)
# ---------------------------------------------------------------------------


@pytest.fixture
def writer(tmp_path) -> CanonicalWriter:
    instance = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _commit_v2_context(writer: CanonicalWriter, payload: dict) -> str:
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "OK", ack.detail
    return payload["context_id"]


def _commit_v2_supersession_pair(writer: CanonicalWriter) -> tuple[str, str]:
    """Commit a real predecessor and its superseding v2 context.

    A supersession pair must name a *recorded* same-kind target: the canonical
    writer validates ancestry before commit, so a pair cannot be presented without
    a genuine predecessor.
    """
    original = _v2_payload()
    _commit_v2_context(writer, original)
    superseding = _v2_payload(
        supersedes_id=original["context_id"],
        supersession_reason="corrected after a revision",
    )
    _commit_v2_context(writer, superseding)
    return original["context_id"], superseding["context_id"]


def _admission(*, disposition_id: str, context_id: str):
    from app.opip.contracts.paper_execution_runtime import PaperAdmissionRequest

    return PaperAdmissionRequest(
        disposition_id=disposition_id,
        decision_context_id=context_id,
        disposition_seq=0,
        quote_currency="USD",
        requested_capital=500.0,
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": "2026-09-19T12:00:00Z",
        },
        expected_portfolio_version=0,
        capital_policy_version="paper-capital-v1",
        portfolio_equity_limit=10_000.0,
        portfolio_position_limit=3,
        requested_reservation_amount=500.0,
    )


def test_schema_v2_context_satisfies_bc1_mandatory_ancestry(writer):
    """A committed v2 context is accepted as B/C-1 admission ancestry unchanged."""
    context_id = _commit_v2_context(writer, _v2_payload())
    ack = writer.admit_paper_opportunity(
        _admission(disposition_id="disp-v2-ok", context_id=context_id)
    )
    assert ack.status == "OK"
    assert ack.disposition == "ADMITTED"


def test_schema_v2_context_with_non_paper_environment_is_rejected(writer):
    payload = _v2_payload(environment="live")
    context_id = _commit_v2_context(writer, payload)
    ack = writer.admit_paper_opportunity(
        _admission(disposition_id="disp-v2-live", context_id=context_id)
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "DECISION_CONTEXT_NOT_PAPER"


def test_schema_v2_context_with_ineligible_eligibility_is_rejected(writer):
    payload = _v2_payload(eligibility=False)
    context_id = _commit_v2_context(writer, payload)
    ack = writer.admit_paper_opportunity(
        _admission(disposition_id="disp-v2-ineligible", context_id=context_id)
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "DECISION_CONTEXT_INELIGIBLE"


def test_schema_v2_retry_from_a_new_emitting_run_is_idempotent(writer):
    """Restart safety: emitter-only provenance drift must not look like a conflict.

    The canonical writer already treats DI ``provenance`` emitter keys
    (``artifact_or_build_id``, ``emitted_at``, ``process_instance_id``) as
    volatile for same-key comparison, and the v2 semantic identity excludes them
    too. A retry from a fresh process is therefore an idempotent replay, which is
    exactly what restart safety requires.
    """
    payload = _v2_payload()
    _commit_v2_context(writer, payload)
    retry = copy.deepcopy(payload)
    retry["provenance"]["emitted_at"] = "2026-09-19T12:00:09Z"
    retry["provenance"]["process_instance_id"] = "another-process"
    assert context_identity_v2(retry) == context_identity_v2(payload)

    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=retry,
        )
    )
    assert ack.status == "DUPLICATE_OK"
    rows = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM events WHERE event_type = ?",
        (DECISION_INTELLIGENCE_CONTEXT_RECORDED,),
    ).fetchone()[0]
    assert rows == 1


def test_schema_v2_tampered_semantic_fact_is_rejected(writer):
    """A changed decision fact cannot ride under an unchanged identity."""
    payload = _v2_payload()
    _commit_v2_context(writer, payload)
    tampered = copy.deepcopy(payload)
    tampered["snapshot_hash"] = "PSNAP:" + "e" * 32
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=tampered,
        )
    )
    assert ack.status == "REJECTED"
    # The DI validator detects the tampered content identity before persistence.
    assert ack.error_code == "INVALID_INTENT"
    assert "content-derived identity" in str(ack.detail)


def test_schema_v2_context_exact_retry_is_idempotent(writer):
    payload = _v2_payload()
    context_id = _commit_v2_context(writer, payload)
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=context_id),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "DUPLICATE_OK"


def test_writer_instrument_ancestry_rule_is_unchanged(writer):
    """Requirement 12: Paper-v2 instrument ancestry still fails closed unregistered."""
    with pytest.raises(ValueError, match="not a registered canonical instrument version"):
        writer._load_instrument_version_by_id("INSTR:kraken:SOL:USD:1")  # noqa: SLF001


# ---------------------------------------------------------------------------
# 14-17. Snapshot hash
# ---------------------------------------------------------------------------


def _snapshot_payload() -> dict:
    return {
        "schema_version": 1,
        "record_type": "CANONICAL_EPISODE_SNAPSHOT",
        "episode_id": "EP:" + "a" * 24,
        "snapshot_id": "SNAP:" + "b" * 32,
        "decision_at": "2026-09-19T12:00:00Z",
        "symbol": "SOL/USD",
        "opportunity_score": 71,
        "ml_feature_seed": {"rsi": 55.5, "atr_pct": 2.1},
    }


def test_snapshot_hash_is_deterministic():
    first = canonical_episode_snapshot_hash(_snapshot_payload())
    second = canonical_episode_snapshot_hash(_snapshot_payload())
    assert first == second
    assert first.startswith("PSNAP:")


def test_snapshot_hash_is_sensitive_to_mutation():
    base = canonical_episode_snapshot_hash(_snapshot_payload())
    for field_name, value in (
        ("symbol", "ETH/USD"),
        ("opportunity_score", 72),
        ("decision_at", "2026-09-19T12:00:01Z"),
        ("ml_feature_seed", {"rsi": 55.6, "atr_pct": 2.1}),
    ):
        mutated = _snapshot_payload()
        mutated[field_name] = value
        assert canonical_episode_snapshot_hash(mutated) != base, field_name


def test_snapshot_hash_is_independent_of_key_ordering():
    payload = _snapshot_payload()
    reordered = {key: payload[key] for key in reversed(list(payload))}
    nested = copy.deepcopy(payload)
    nested["ml_feature_seed"] = {
        key: nested["ml_feature_seed"][key]
        for key in reversed(list(nested["ml_feature_seed"]))
    }
    assert canonical_episode_snapshot_hash(reordered) == canonical_episode_snapshot_hash(
        payload
    )
    assert canonical_episode_snapshot_hash(nested) == canonical_episode_snapshot_hash(
        payload
    )


def test_snapshot_hash_differs_from_the_snapshot_identity():
    payload = _snapshot_payload()
    content_hash = canonical_episode_snapshot_hash(payload)
    assert content_hash != payload["snapshot_id"]
    assert not content_hash.startswith("SNAP:")


def test_snapshot_hash_rejects_unserializable_payload():
    with pytest.raises(ValueError, match="not canonically serializable"):
        canonical_episode_snapshot_hash({"bad": object()})
    with pytest.raises(ValueError, match="must be a mapping"):
        canonical_episode_snapshot_hash(["not", "a", "mapping"])


def test_snapshot_hash_carries_no_ambient_input():
    """Determinism across processes: the hash is a pure function of the payload.

    Recomputing the same canonical bytes under the same domain reproduces the
    digest, so no ambient input (time, randomness, process identity) can be part
    of it.
    """
    payload = _snapshot_payload()
    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    independently_derived = stable_hash("PSNAP", json.loads(canonical), length=32)
    assert canonical_episode_snapshot_hash(payload) == independently_derived


# ---------------------------------------------------------------------------
# 18. Policy sources
# ---------------------------------------------------------------------------


def test_v2_policy_version_and_fingerprint_come_from_real_qualification_policy():
    payload = _v2_payload()
    assert payload["policy_version"] == GATE_POLICY_VERSION
    assert payload["policy_fingerprint"] == gate_policy_fingerprint()
    assert payload["policy_fingerprint"].startswith("GPF:")
    # The paper capital policy is a different concept and must not appear here.
    assert payload["policy_version"] != "paper-capital-v1"


# ---------------------------------------------------------------------------
# 19-22. Boundaries
# ---------------------------------------------------------------------------


def test_no_runtime_root_gains_a_decision_intelligence_import():
    """The frozen runtime import boundary is unchanged by this slice."""
    runtime_roots = (
        APP_ROOT / "services",
        APP_ROOT / "jobs",
        APP_ROOT / "api",
        APP_ROOT / "opip" / "discovery",
        APP_ROOT / "opip" / "decision",
        APP_ROOT / "opip" / "risk",
    )
    offenders: list[str] = []
    for root in runtime_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name == "app.opip.decision_intelligence" or name.startswith(
                        "app.opip.decision_intelligence."
                    ):
                        offenders.append(f"{path.name}:{name}")
    assert offenders == []


def test_no_feature_bus_activation_or_dependency():
    """Feature Bus stays independently controlled and is never sourced here."""
    from app.opip.features.publisher import feature_bus_capture_enabled

    assert feature_bus_capture_enabled() is False
    for name in ("identity.py", "events.py", "evidence_reader.py"):
        source = (
            APP_ROOT / "opip" / "decision_intelligence" / name
        ).read_text(encoding="utf-8")
        assert "features.publisher" not in source, name
        assert "feature_bus" not in source, name
    capture = (APP_ROOT / "services" / "canonical_episode_capture.py").read_text(
        encoding="utf-8"
    )
    assert "app.opip.features" not in capture


def test_no_di_committee_or_model_authority_is_activated(writer):
    """Committing a v2 context emits only context evidence."""
    _commit_v2_context(writer, _v2_payload())
    written = {
        str(row["event_type"])
        for row in writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT DISTINCT event_type FROM events"
        ).fetchall()
    }
    assert written == {DECISION_INTELLIGENCE_CONTEXT_RECORDED}
    for event_type in sorted(DECISION_INTELLIGENCE_EVENT_TYPES):
        if event_type == DECISION_INTELLIGENCE_CONTEXT_RECORDED:
            continue
        rows = writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT COUNT(*) FROM events WHERE event_type = ?", (event_type,)
        ).fetchone()[0]
        assert rows == 0, event_type
    assert DECISION_INTELLIGENCE_REQUEST_RECORDED in DECISION_INTELLIGENCE_EVENT_TYPES
    for committee_event in (
        DECISION_INTELLIGENCE_REQUEST_RECORDED,
        DECISION_INTELLIGENCE_TRANSITION_RECORDED,
        DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
        DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
        DECISION_INTELLIGENCE_INVOCATION_RECORDED,
        DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    ):
        assert committee_event in DECISION_INTELLIGENCE_EVENT_TYPES


# ---------------------------------------------------------------------------
# Supersession pair invariant (Greptile P1)
# ---------------------------------------------------------------------------


def test_v2_context_without_supersession_is_valid():
    payload = _v2_payload(supersedes_id=None, supersession_reason=None)
    normalized = validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)
    assert normalized["supersedes_id"] is None
    assert normalized["supersession_reason"] is None


def test_v2_context_with_a_complete_supersession_pair_is_valid():
    payload = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
        supersession_reason="superseded after evidence correction",
    )
    normalized = validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)
    assert normalized["supersedes_id"] == "DI-CONTEXT-V2:" + "1" * 32
    assert normalized["supersession_reason"] == "superseded after evidence correction"


def test_supersedes_id_without_a_reason_is_rejected():
    payload = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
        supersession_reason=None,
    )
    with pytest.raises(ValueError, match="must be provided together"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_supersession_reason_without_an_id_is_rejected():
    payload = _v2_payload(
        supersedes_id=None,
        supersession_reason="corrected after a data revision",
    )
    with pytest.raises(ValueError, match="must be provided together"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_blank_supersedes_id_is_rejected_not_treated_as_absence(blank):
    payload = _v2_payload(
        supersedes_id=blank,
        supersession_reason="corrected after a data revision",
    )
    with pytest.raises(ValueError, match="must not be blank"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_blank_supersession_reason_is_rejected_not_treated_as_absence(blank):
    payload = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
        supersession_reason=blank,
    )
    with pytest.raises(ValueError, match="must not be blank"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


def test_padded_supersession_values_are_rejected():
    payload = _v2_payload(
        supersedes_id=" DI-CONTEXT-V2:" + "1" * 32 + " ",
        supersession_reason="corrected after a data revision",
    )
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


@pytest.mark.parametrize("sentinel", ["none", "NONE", "N/A", "n/a", "unknown", "UNKNOWN"])
def test_placeholder_supersession_values_are_rejected(sentinel):
    """A placeholder is not a legitimate identity or reason."""
    with pytest.raises(ValueError, match="not a placeholder"):
        validate_di_payload(
            DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            _v2_payload(
                supersedes_id=sentinel,
                supersession_reason="corrected after a data revision",
            ),
        )
    with pytest.raises(ValueError, match="not a placeholder"):
        validate_di_payload(
            DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            _v2_payload(
                supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
                supersession_reason=sentinel,
            ),
        )


def test_non_string_supersession_values_are_rejected():
    with pytest.raises(ValueError, match="canonical string or absent"):
        validate_di_payload(
            DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            _v2_payload(
                supersedes_id=123,
                supersession_reason="corrected after a data revision",
            ),
        )


def test_paired_supersession_identity_is_deterministic():
    supersedes = "DI-CONTEXT-V2:" + "1" * 32
    first = _v2_payload(
        supersedes_id=supersedes, supersession_reason="corrected after a revision"
    )
    second = _v2_payload(
        supersedes_id=supersedes, supersession_reason="corrected after a revision"
    )
    assert context_identity_v2(first) == context_identity_v2(second)


def test_paired_supersession_changes_the_identity():
    base = _v2_payload()
    superseding = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
        supersession_reason="corrected after a revision",
    )
    assert context_identity_v2(base) != context_identity_v2(superseding)


def test_unpaired_supersession_cannot_become_durable_evidence(writer):
    """The writer refuses it, so ambiguous metadata is never persisted."""
    payload = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "1" * 32,
        supersession_reason=None,
    )
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(
                context_id=payload["context_id"]
            ),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"
    assert "must be provided together" in str(ack.detail)
    rows = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM events WHERE event_type = ?",
        (DECISION_INTELLIGENCE_CONTEXT_RECORDED,),
    ).fetchone()[0]
    assert rows == 0


def test_paired_supersession_retry_is_idempotent(writer):
    _, superseding_id = _commit_v2_supersession_pair(writer)
    superseding = _v2_payload(
        supersedes_id=None,
        supersession_reason=None,
    )
    # Re-submit the exact recorded payload by reading it back from canonical
    # evidence rather than rebuilding it, so this is a true retry.
    committed = json.loads(
        writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT payload_json FROM events WHERE event_type = ? AND idempotency_key = ?",
            (
                DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                context_idempotency_key(context_id=superseding_id),
            ),
        ).fetchone()["payload_json"]
    )
    assert committed["supersedes_id"] is not None
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=superseding_id),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=committed,
        )
    )
    assert ack.status == "DUPLICATE_OK"
    assert superseding is not None


def test_evidence_reader_reconstructs_one_coherent_supersession_edge(writer):
    """A valid v2 pair resolves to exactly one same-kind supersession edge."""
    from app.opip.decision_intelligence.evidence_reader import (
        read_di_evidence_snapshot,
    )

    original_id, superseding_id = _commit_v2_supersession_pair(writer)

    snapshot = read_di_evidence_snapshot(db_path=writer.db_path)
    assert snapshot.contexts == {}
    assert set(snapshot.contexts_v2) == {original_id, superseding_id}
    edges = snapshot.supersession_edges
    assert len(edges) == 1
    assert edges[0].supersedes_id == original_id
    assert edges[0].record_id == superseding_id


def test_unrecorded_supersession_target_is_refused_before_commit(writer):
    """A pair naming an unrecorded target is refused by the writer.

    This is the first line of defence: a supersession cannot be presented without
    a genuine same-kind predecessor, so ambiguous lineage never reaches durable
    storage. The reader retains its own guard as defence in depth.
    """
    payload = _v2_payload(
        supersedes_id="DI-CONTEXT-V2:" + "9" * 32,
        supersession_reason="corrected after a revision",
    )
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"
    assert "is missing for evidence validation" in str(ack.detail)
    assert (
        writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            (DECISION_INTELLIGENCE_CONTEXT_RECORDED,),
        ).fetchone()[0]
        == 0
    )


def test_v1_context_supersession_semantics_are_unchanged():
    """The pairing rule is a v2 invariant; v1 keeps its existing behaviour."""
    from dataclasses import MISSING

    for field_name in ("supersedes_id", "supersession_reason"):
        field = DecisionContext.__dataclass_fields__[field_name]
        assert field.default is None
        assert field.default_factory is MISSING
    payload = _v1_payload()
    payload["supersedes_id"] = "DI-CONTEXT:some-earlier-context"
    payload["context_id"] = context_identity(payload)
    # v1 accepts it exactly as before: no pairing rule was added to v1.
    assert validate_di_payload(
        DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload
    )["supersedes_id"] == "DI-CONTEXT:some-earlier-context"


# ---------------------------------------------------------------------------
# Cross-version supersession is forbidden (Finding 1)
# ---------------------------------------------------------------------------


def _commit_v1_context(writer: CanonicalWriter, payload: dict) -> str:
    from app.opip.canonical.models import WriterIntent

    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "OK", ack.detail
    return payload["context_id"]


def _superseding_v2_payload(target_context_id: str) -> dict:
    return _v2_payload(
        supersedes_id=target_context_id,
        supersession_reason="corrected after a revision",
    )


def _superseding_v1_payload(target_context_id: str) -> dict:
    payload = _v1_payload()
    payload["supersedes_id"] = target_context_id
    payload["supersession_reason"] = "corrected after a revision"
    payload["context_id"] = context_identity(payload)
    return payload


def _submit_context(writer: CanonicalWriter, payload: dict):
    from app.opip.canonical.models import WriterIntent

    return writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(context_id=payload["context_id"]),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )
    )


def _context_row_count(writer: CanonicalWriter) -> int:
    return int(
        writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            (DECISION_INTELLIGENCE_CONTEXT_RECORDED,),
        ).fetchone()[0]
    )


def test_v1_to_v1_supersession_still_commits_and_reconstructs(writer):
    from app.opip.decision_intelligence.evidence_reader import (
        read_di_evidence_snapshot,
    )

    original = _v1_payload()
    _commit_v1_context(writer, original)
    superseding = _superseding_v1_payload(original["context_id"])
    assert _commit_v1_context(writer, superseding) == superseding["context_id"]

    snapshot = read_di_evidence_snapshot(db_path=writer.db_path)
    assert set(snapshot.contexts) == {original["context_id"], superseding["context_id"]}
    assert snapshot.contexts_v2 == {}
    assert len(snapshot.supersession_edges) == 1
    assert snapshot.supersession_edges[0].supersedes_id == original["context_id"]


def test_v2_to_v2_supersession_still_commits_and_reconstructs(writer):
    from app.opip.decision_intelligence.evidence_reader import (
        read_di_evidence_snapshot,
    )

    original_id, superseding_id = _commit_v2_supersession_pair(writer)
    snapshot = read_di_evidence_snapshot(db_path=writer.db_path)
    assert set(snapshot.contexts_v2) == {original_id, superseding_id}
    assert snapshot.contexts == {}
    assert len(snapshot.supersession_edges) == 1
    assert snapshot.supersession_edges[0].supersedes_id == original_id


def test_v2_superseding_a_v1_context_is_rejected_before_commit(writer):
    original = _v1_payload()
    _commit_v1_context(writer, original)
    ack = _submit_context(writer, _superseding_v2_payload(original["context_id"]))
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"
    assert "cross-version decision context supersession" in str(ack.detail)
    # Exactly the original remains: no partial durable evidence.
    assert _context_row_count(writer) == 1


def test_v1_superseding_a_v2_context_is_rejected_before_commit(writer):
    original_id, _ = _commit_v2_supersession_pair(writer)
    before = _context_row_count(writer)
    superseding_v1 = _superseding_v1_payload(original_id)
    ack = _submit_context(writer, superseding_v1)
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"
    assert "cross-version decision context supersession" in str(ack.detail)
    assert _context_row_count(writer) == before


def test_rejected_cross_version_attempt_leaves_no_evidence_and_is_retryable(writer):
    """A refused cross-version correction is not partially durable."""
    original = _v1_payload()
    _commit_v1_context(writer, original)
    before = _context_row_count(writer)
    payload = _superseding_v2_payload(original["context_id"])

    first = _submit_context(writer, payload)
    second = _submit_context(writer, payload)
    assert first.status == "REJECTED"
    assert second.status == "REJECTED"
    assert _context_row_count(writer) == before

    # Control: the same supersession against a v2 target is deterministic.
    v2_original = _v2_payload()
    _commit_v2_context(writer, v2_original)
    v2_superseding = _superseding_v2_payload(v2_original["context_id"])
    assert _submit_context(writer, v2_superseding).status == "OK"
    assert _submit_context(writer, v2_superseding).status == "DUPLICATE_OK"


# ---------------------------------------------------------------------------
# Envelope accepts both context versions (Finding 2)
# ---------------------------------------------------------------------------


def test_v1_context_envelope_is_valid():
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v1_payload()
    envelope = DIEventEnvelope(
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=payload,
        provenance=_provenance_contract(),
        payload_schema_version=1,
    )
    assert envelope.payload_schema_version == 1


def test_v2_context_envelope_is_valid():
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v2_payload()
    envelope = DIEventEnvelope(
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=payload,
        provenance=_provenance_contract(),
        payload_schema_version=2,
    )
    assert envelope.payload_schema_version == 2


@pytest.mark.parametrize(
    ("envelope_version", "payload_version"),
    [(2, 1), (1, 2)],
)
def test_envelope_and_payload_version_mismatch_fails(envelope_version, payload_version):
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v2_payload()
    payload["schema_version"] = payload_version
    with pytest.raises(ValueError, match="must match the envelope"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
            provenance=_provenance_contract(),
            payload_schema_version=envelope_version,
        )


@pytest.mark.parametrize("version", [0, 3, 99])
def test_context_envelope_rejects_unregistered_schema_versions(version):
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v2_payload()
    payload["schema_version"] = version
    with pytest.raises(ValueError, match="unsupported DI event payload_schema_version"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
            provenance=_provenance_contract(),
            payload_schema_version=version,
        )


@pytest.mark.parametrize("version", [2, 3])
def test_non_context_envelope_still_requires_schema_version_one(version):
    """No non-context DI contract is weakened by the context-aware rule."""
    from app.opip.decision_intelligence.events import DIEventEnvelope

    with pytest.raises(ValueError, match="unsupported DI event payload_schema_version"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_REQUEST_RECORDED,
            payload={"schema_version": version},
            provenance=_provenance_contract(),
            payload_schema_version=version,
        )


def test_envelope_rejects_non_integer_schema_versions():
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v2_payload()
    with pytest.raises(ValueError, match="must be an integer"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
            provenance=_provenance_contract(),
            payload_schema_version="2",
        )
    with pytest.raises(ValueError, match="must be an integer"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload={**payload, "schema_version": True},
            provenance=_provenance_contract(),
            payload_schema_version=2,
        )


def test_v2_envelope_to_writer_intent_is_canonical_and_unchanged():
    from app.opip.decision_intelligence.events import DIEventEnvelope

    payload = _v2_payload()
    envelope = DIEventEnvelope(
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=payload,
        provenance=_provenance_contract(),
        payload_schema_version=2,
    )
    intent = envelope.to_writer_intent(idempotency_key="k")
    assert intent.event_type == DECISION_INTELLIGENCE_CONTEXT_RECORDED
    assert intent.priority == "LOW"
    assert intent.ops_handoff is None
    assert intent.schema_version == SCHEMA_VERSION
    assert intent.payload["schema_version"] == 2
    assert intent.payload["context_id"] == payload["context_id"]
    # to_writer_intent renders the envelope's own provenance contract, unchanged.
    assert intent.payload["provenance"]["source_record_refs"] == ["source:1"]
    assert intent.payload["provenance"]["process_instance_id"] == "proc-1"


# ---------------------------------------------------------------------------
# V2 required string facts reject malformed types (Finding 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [123, 0, True, False, 1.5, None, ["a"], {"a": 1}, ("a",), b"bytes"],
)
@pytest.mark.parametrize(
    "field_name",
    [
        "context_id",
        "candidate_id",
        "episode_id",
        "instrument_version",
        "snapshot_id",
        "snapshot_hash",
        "policy_version",
        "policy_fingerprint",
        "environment",
    ],
)
def test_v2_required_string_facts_reject_malformed_types(field_name, malformed):
    """A non-string must never coerce into an apparently valid canonical fact."""
    payload = _v2_payload()
    payload[field_name] = malformed
    with pytest.raises(ValueError, match=f"{field_name} must be a canonical string"):
        _v2_contract(payload)


def test_v2_legitimate_string_facts_still_normalize():
    """Genuine strings keep their existing trimming behaviour."""
    payload = _v2_payload(
        candidate_id="  candidate-padded  ",
        environment="  paper  ",
    )
    context = _v2_contract(payload)
    assert context.candidate_id == "candidate-padded"
    assert context.environment == "paper"


def test_v1_required_string_behaviour_is_unchanged():
    """v1 keeps its existing coercion semantics; the new rule is v2-only."""
    # v1 accepts a non-string where it trims to something non-empty, exactly as
    # before this change.
    legacy = DecisionContext(
        context_id="ctx-1",
        candidate_id=123,  # type: ignore[arg-type]
        episode_id="episode-1",
        evaluation_id="evaluation-1",
        instrument_version="KRAKEN:SOLUSD:v1",
        snapshot_id="snapshot-1",
        snapshot_hash="snapshot-hash-1",
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        consumed_input_watermark={"history_epoch": 0, "local_sequence": 0},
        feature_version="features-v1",
        policy_version="policy-v1",
        detector_version="detector-v1",
        forecast_version="forecast-v1",
        candidate_set_ref="candidate-set-1",
        portfolio_version_ref=None,
        environment="paper",
        eligibility=True,
        missingness={},
        source_availability_times={},
        evidence_eligibility_manifest={},
        provenance=_provenance_contract(),
    )
    assert legacy.candidate_id == "123"


# ---------------------------------------------------------------------------
# V2 identity canonicalizes timestamps (Finding 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "equivalent",
    [
        "2026-09-19T12:00:00Z",
        "2026-09-19T12:00:00+00:00",
        "2026-09-19T14:00:00+02:00",
        "2026-09-19T07:00:00-05:00",
    ],
)
def test_equivalent_timestamp_representations_derive_one_identity(equivalent):
    first = _v2_payload(
        evaluation_time=equivalent,
        evidence_cutoff=equivalent,
    )
    second = _v2_payload(
        evaluation_time="2026-09-19T12:00:00Z",
        evidence_cutoff="2026-09-19T12:00:00Z",
    )
    assert context_identity_v2(first) == context_identity_v2(second)


def test_datetime_objects_and_serialized_forms_derive_one_identity():
    as_datetime = _v2_payload(evaluation_time=NOW, evidence_cutoff=NOW)
    as_string = _v2_payload(
        evaluation_time="2026-09-19T12:00:00Z",
        evidence_cutoff="2026-09-19T12:00:00Z",
    )
    assert context_identity_v2(as_datetime) == context_identity_v2(as_string)


def test_equivalent_timestamps_produce_a_payload_that_validates():
    """The derived id must survive normalization, whichever form was supplied."""
    payload = _v2_payload(
        evaluation_time="2026-09-19T14:00:00+02:00",
        evidence_cutoff="2026-09-19T14:00:00+02:00",
    )
    normalized = validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)
    assert normalized["context_id"] == payload["context_id"]
    assert normalized["evaluation_time"] == "2026-09-19T12:00:00Z"
    assert normalized["evidence_cutoff"] == "2026-09-19T12:00:00Z"


def test_materially_different_instants_still_change_identity():
    base = _v2_payload(evaluation_time=NOW, evidence_cutoff=NOW)
    later = _v2_payload(
        evaluation_time="2026-09-19T12:00:01Z",
        evidence_cutoff="2026-09-19T12:00:01Z",
    )
    assert context_identity_v2(base) != context_identity_v2(later)


def test_identity_timestamps_still_reject_unusable_values():
    with pytest.raises(ValueError, match="evaluation_time"):
        context_identity_v2(_v2_payload(evaluation_time="not-a-timestamp"))
    with pytest.raises(ValueError, match="evaluation_time"):
        context_identity_v2(_v2_payload(evaluation_time=123))


def test_evidence_cutoff_after_evaluation_time_is_still_enforced_after_normalization():
    """The ordering rule uses the normalized instants, not the supplied strings."""
    payload = _v2_payload(
        evaluation_time="2026-09-19T07:00:00-05:00",
        evidence_cutoff="2026-09-19T13:00:00+00:00",
    )
    # 07:00-05:00 == 12:00Z, and 13:00Z is later.
    with pytest.raises(ValueError, match="evidence_cutoff cannot be after"):
        validate_di_payload(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)


# ---------------------------------------------------------------------------
# Static guards against fabricated lineage
# ---------------------------------------------------------------------------


def test_v2_contract_carries_no_fabricated_lineage_values():
    """No invented version/placeholder strings exist as contract values.

    The declared supersession sentinel set is excluded from the scan because it
    exists precisely to *reject* those strings; a separate assertion pins that the
    sentinels are only ever used for rejection.
    """
    from app.opip.decision_intelligence import identity as identity_module

    declared_sentinels = {
        value.lower() for value in identity_module._SUPERSESSION_SENTINEL_VALUES
    }
    assert declared_sentinels, "the sentinel denylist must not be empty"

    source = (APP_ROOT / "opip" / "decision_intelligence" / "identity.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value.lower())

    # The denylist itself legitimately names these strings.
    scanned = literals - declared_sentinels
    for fabricated in (
        "forecast-v1",
        "detector-v1",
        "feature-v1",
        "candidate-set",
        "not-applicable",
        "not_applicable",
    ):
        assert fabricated not in scanned, fabricated

    # Any sentinel literal appears only as a member of the rejection denylist.
    sentinel_container_source = source.split("_SUPERSESSION_SENTINEL_VALUES = ")[1]
    container_body = sentinel_container_source.split("\n\n")[0]
    for sentinel in declared_sentinels:
        assert f'"{sentinel}"' in container_body, sentinel


def test_v2_contract_has_no_committee_metadata_fields():
    fields = set(DecisionContextV2.__dataclass_fields__)
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
        assert absent not in fields, absent
    assert fields == {
        "context_id",
        "candidate_id",
        "episode_id",
        "instrument_version",
        "snapshot_id",
        "snapshot_hash",
        "evaluation_time",
        "evidence_cutoff",
        "policy_version",
        "policy_fingerprint",
        "environment",
        "eligibility",
        "provenance",
        "schema_version",
        "supersedes_id",
        "supersession_reason",
    }


def test_schema_version_constants_are_explicit():
    assert DECISION_CONTEXT_SCHEMA_VERSION == 1
    assert DECISION_CONTEXT_SCHEMA_VERSION_V2 == 2
    assert Provenance is not None
