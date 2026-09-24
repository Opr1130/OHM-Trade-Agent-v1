"""Fail-closed durable ingress for Committee SHADOW cases."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from app.opip.committee.case_ingress import (
    CaseIngressError,
    envelope_from_dict,
    load_case_envelopes,
)
from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _case() -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="policy-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee-v1",
        prompt_version="prompt-v1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=500_000,
    )
    item = EvidenceItem(
        evidence_id="fact-1",
        source_id="canonical:1",
        available_at=NOW,
        payload={
            "instrument_id": "BTCUSD",
            "metric_name": "example",
            "metric_value": "1",
        },
    )
    snapshot = EvidenceSnapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=(item,),
        source_refs=("canonical:1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="BTCUSD",
    )
    return CommitteeCase(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="test.case_ingress",
            artifact_or_build_id="test",
            process_instance_id="test-1",
            emitted_at=NOW,
            source_record_refs=("canonical:1",),
        ),
        instrument_id="BTCUSD",
    )


def _row() -> dict:
    case = _case()
    return {
        "schema_version": 1,
        "evidence_id": "queue-1",
        "evidence_snapshot_hash": case.snapshot.snapshot_hash,
        "committee_policy_version": case.policy.policy_version,
        "committed": True,
        "sealed": True,
        "available_at": NOW.isoformat(),
        "expires_at": None,
        "estimated_cost_microunits": 400_000,
        "case": {
            "case_id": case.case_id,
            "case_type": case.case_type.value,
            "created_at": case.created_at.isoformat(),
            "instrument_id": case.instrument_id,
            "strategy_context_id": None,
            "canonical_binding": None,
            "policy": {
                "policy_version": case.policy.policy_version,
                "seated_providers": [item.value for item in case.policy.seated_providers],
                "prompt_template_id": case.policy.prompt_template_id,
                "prompt_version": case.policy.prompt_version,
                "max_attempts_per_seat": case.policy.max_attempts_per_seat,
                "max_estimated_cost_microunits": (
                    case.policy.max_estimated_cost_microunits
                ),
            },
            "snapshot": {
                "case_id": case.snapshot.case_id,
                "evidence_cutoff_at": case.snapshot.evidence_cutoff_at.isoformat(),
                "assembled_at": case.snapshot.assembled_at.isoformat(),
                "items": [
                    {
                        "evidence_id": item.evidence_id,
                        "source_id": item.source_id,
                        "available_at": item.available_at.isoformat(),
                        "payload": dict(item.payload),
                    }
                    for item in case.snapshot.items
                ],
                "source_refs": list(case.snapshot.source_refs),
                "committee_policy_version": case.snapshot.committee_policy_version,
                "prompt_template_id": case.snapshot.prompt_template_id,
                "prompt_version": case.snapshot.prompt_version,
                "instrument_id": case.snapshot.instrument_id,
                "strategy_context_id": case.snapshot.strategy_context_id,
            },
            "provenance": {
                "producing_component": case.provenance.producing_component,
                "artifact_or_build_id": case.provenance.artifact_or_build_id,
                "process_instance_id": case.provenance.process_instance_id,
                "emitted_at": case.provenance.emitted_at.isoformat(),
                "source_record_refs": list(case.provenance.source_record_refs),
            },
        },
    }


def test_round_trip_reconstructs_the_exact_snapshot_identity():
    row = _row()
    envelope = envelope_from_dict(row)
    assert envelope.case.snapshot.snapshot_hash == row["evidence_snapshot_hash"]
    assert envelope.case.policy.policy_version == row["committee_policy_version"]
    item = envelope.scheduler_item
    assert item.evidence_snapshot_hash == envelope.case.snapshot.snapshot_hash
    assert item.case_id == envelope.case.case_id


@pytest.mark.parametrize("field", ["committed", "sealed"])
def test_boolean_commit_state_is_not_coerced(field: str):
    row = _row()
    row[field] = 1
    with pytest.raises(CaseIngressError, match="committed and sealed must be booleans"):
        envelope_from_dict(row)


def test_snapshot_hash_is_recomputed_not_trusted():
    row = _row()
    row["evidence_snapshot_hash"] = "COMMITTEE-SNAPSHOT:forged"
    with pytest.raises(CaseIngressError, match="snapshot_hash"):
        envelope_from_dict(row)


def test_tampering_with_payload_breaks_the_declared_snapshot_identity():
    row = _row()
    row["case"]["snapshot"]["items"][0]["payload"]["metric_value"] = "999"
    with pytest.raises(CaseIngressError, match="snapshot_hash"):
        envelope_from_dict(row)


def test_policy_version_is_recomputed_not_trusted():
    row = _row()
    row["committee_policy_version"] = "other-policy"
    with pytest.raises(CaseIngressError, match="policy_version"):
        envelope_from_dict(row)


def test_snapshot_and_case_instrument_must_agree():
    row = _row()
    row["case"]["instrument_id"] = "ETHUSD"
    with pytest.raises(CaseIngressError, match="instrument_id"):
        envelope_from_dict(row)


def test_post_cutoff_availability_is_refused():
    row = _row()
    row["available_at"] = "2026-09-23T12:00:01+00:00"
    with pytest.raises(CaseIngressError, match="post-date"):
        envelope_from_dict(row)


def test_malformed_json_fails_the_whole_file(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_row()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(CaseIngressError, match="line 2 is not valid JSON"):
        load_case_envelopes(path)


def test_duplicate_queue_evidence_id_is_refused(tmp_path):
    path = tmp_path / "cases.jsonl"
    row = _row()
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(copy.deepcopy(row)) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseIngressError, match="duplicates evidence_id"):
        load_case_envelopes(path)


def test_missing_file_is_an_empty_population(tmp_path):
    assert load_case_envelopes(tmp_path / "missing.jsonl") == ()
