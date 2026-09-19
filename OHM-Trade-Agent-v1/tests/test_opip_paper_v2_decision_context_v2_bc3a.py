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
    assert context_identity_v2(_v2_payload()) == context_identity_v2(_v2_payload())


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
    payload = _v2_payload(evaluation_time="2026-09-19T12:00:00")
    with pytest.raises(ValueError):
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
# Static guards against fabricated lineage
# ---------------------------------------------------------------------------


def test_v2_contract_carries_no_fabricated_lineage_values():
    """No invented version/placeholder strings exist in the v2 contract."""
    source = (APP_ROOT / "opip" / "decision_intelligence" / "identity.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value.lower())
    for fabricated in (
        "forecast-v1",
        "detector-v1",
        "feature-v1",
        "candidate-set",
        "not-applicable",
        "not_applicable",
        "n/a",
        "none",
        "unknown",
    ):
        assert fabricated not in literals, fabricated


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
