from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    ProviderFamily,
)
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.committee.shadow_case_bridge import (
    ShadowCaseEnvelopeError,
    case_from_envelope,
    load_case_envelopes,
)
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 23, 11, 59, tzinfo=timezone.utc)


def _expected_case() -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="committee-shadow-policy-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee-system",
        prompt_version="v1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=500_000,
    )
    item = build_evidence_item(
        evidence_id="evidence-1",
        source_id="canonical-learning-replica",
        available_at=CUTOFF,
        payload={"symbol": "BTC/USD", "signal": "neutral"},
        evidence_cutoff_at=CUTOFF,
    )
    snapshot = build_evidence_snapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=NOW,
        items=(item,),
        source_refs=("canonical:decision-1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="BTC/USD",
        strategy_context_id="ctx-1",
    )
    return CommitteeCase(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="tests.shadow-case-producer",
            artifact_or_build_id="build-1",
            process_instance_id="producer-1",
            emitted_at=NOW,
            source_record_refs=("canonical:decision-1",),
        ),
        instrument_id="BTC/USD",
        strategy_context_id="ctx-1",
    )


def _envelope() -> dict[str, object]:
    expected = _expected_case()
    return {
        "schema_version": 1,
        "case_id": expected.case_id,
        "case_type": expected.case_type.value,
        "created_at": NOW.isoformat(),
        "evidence_cutoff_at": CUTOFF.isoformat(),
        "assembled_at": NOW.isoformat(),
        "instrument_id": "BTC/USD",
        "strategy_context_id": "ctx-1",
        "canonical_binding": None,
        "source_refs": ["canonical:decision-1"],
        "evidence_items": [
            {
                "evidence_id": "evidence-1",
                "source_id": "canonical-learning-replica",
                "available_at": CUTOFF.isoformat(),
                "payload": {"symbol": "BTC/USD", "signal": "neutral"},
            }
        ],
        "policy": {
            "policy_version": "committee-shadow-policy-v1",
            "seated_providers": ["openai", "anthropic"],
            "prompt_template_id": "committee-system",
            "prompt_version": "v1",
            "max_attempts_per_seat": 1,
            "max_estimated_cost_microunits": 500_000,
        },
        "provenance": {
            "producing_component": "tests.shadow-case-producer",
            "artifact_or_build_id": "build-1",
            "process_instance_id": "producer-1",
            "emitted_at": NOW.isoformat(),
            "source_record_refs": ["canonical:decision-1"],
        },
        "expected_snapshot_hash": expected.snapshot.snapshot_hash,
        "expected_policy_hash": expected.policy.policy_hash,
        "expected_case_hash": expected.case_hash,
    }


def test_valid_envelope_reconstructs_existing_contract_exactly() -> None:
    expected = _expected_case()
    actual = case_from_envelope(_envelope())
    assert actual.case_hash == expected.case_hash
    assert actual.snapshot.snapshot_hash == expected.snapshot.snapshot_hash
    assert actual.policy.policy_hash == expected.policy.policy_hash


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("expected_snapshot_hash", "tampered-snapshot"),
        ("expected_policy_hash", "tampered-policy"),
        ("expected_case_hash", "tampered-case"),
    ],
)
def test_identity_mismatch_fails_closed(field: str, replacement: str) -> None:
    envelope = _envelope()
    envelope[field] = replacement
    with pytest.raises(ShadowCaseEnvelopeError, match="identity"):
        case_from_envelope(envelope)


def test_evidence_after_cutoff_is_refused() -> None:
    envelope = _envelope()
    evidence = envelope["evidence_items"][0]
    evidence["available_at"] = NOW.isoformat()
    with pytest.raises(ShadowCaseEnvelopeError, match="after the cutoff"):
        case_from_envelope(envelope)


def test_binary_float_in_evidence_is_refused() -> None:
    envelope = _envelope()
    evidence = envelope["evidence_items"][0]
    evidence["payload"] = {"score": 0.5}
    with pytest.raises(ShadowCaseEnvelopeError, match="binary float"):
        case_from_envelope(envelope)


def test_unknown_schema_is_refused() -> None:
    envelope = _envelope()
    envelope["schema_version"] = 2
    with pytest.raises(ShadowCaseEnvelopeError, match="unsupported"):
        case_from_envelope(envelope)


def test_duplicate_case_id_in_stream_is_refused(tmp_path: Path) -> None:
    envelope = _envelope()
    path = tmp_path / "committee_case_inputs.jsonl"
    encoded = json.dumps(envelope, sort_keys=True)
    path.write_text(encoded + "\n" + encoded + "\n", encoding="utf-8")
    with pytest.raises(ShadowCaseEnvelopeError, match="duplicate case_id"):
        load_case_envelopes(path)


def test_malformed_stream_row_is_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "committee_case_inputs.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(ShadowCaseEnvelopeError, match="not valid JSON"):
        load_case_envelopes(path)


def test_missing_input_stream_is_an_empty_population(tmp_path: Path) -> None:
    assert load_case_envelopes(tmp_path / "absent.jsonl") == ()


def test_tampering_payload_without_resealing_is_refused() -> None:
    envelope = deepcopy(_envelope())
    envelope["evidence_items"][0]["payload"]["signal"] = "supportive"
    with pytest.raises(ShadowCaseEnvelopeError, match="snapshot identity"):
        case_from_envelope(envelope)
