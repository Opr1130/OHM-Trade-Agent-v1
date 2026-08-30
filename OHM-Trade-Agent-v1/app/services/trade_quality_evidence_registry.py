from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.opip.ml.contracts import FeatureSnapshot
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.registry_io import registry_lock
from app.services.trade_quality_assessor import TradePlanAssessment


EVIDENCE_FILE = Path("/app/data/opip_trade_quality_evidence_v1.jsonl")


def _lock_file(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _canonical_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return "W9Q:" + hashlib.sha256(raw).hexdigest()[:32]


def record_trade_quality_evidence(
    *,
    feature_snapshot: FeatureSnapshot,
    assessment: TradePlanAssessment,
    plan: EntryExitPlan,
    candidate: dict[str, Any],
    decision_at: datetime,
    market_regime: str | None,
    path: Path = EVIDENCE_FILE,
) -> str:
    """Append immutable point-in-time quality evidence for later maturation.

    Measurement only: this function has no exchange, Telegram, ranking, or
    lifecycle authority. Failure must be handled fail-soft by callers.
    """
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    decision = decision_at.astimezone(timezone.utc)
    if assessment.snapshot_id != feature_snapshot.snapshot_id:
        raise ValueError("assessment snapshot identity mismatch")

    identity_payload = {
        "snapshot_id": feature_snapshot.snapshot_id,
        "candidate_id": feature_snapshot.candidate_id,
        "decision_at_utc": decision.isoformat(),
        "symbol": plan.symbol.upper(),
        "direction": str(candidate.get("direction") or plan.direction or "LONG").upper(),
    }
    evidence_id = _canonical_id(identity_payload)
    row = {
        "schema_version": 1,
        "record_type": "OPIP_TRADE_QUALITY_EVIDENCE",
        "evidence_id": evidence_id,
        **identity_payload,
        "market_regime": market_regime,
        "feature_snapshot": feature_snapshot.to_dict(),
        "continuation": asdict(assessment.continuation),
        "entry": asdict(assessment.entry),
        "trade_quality_actionable": bool(assessment.actionable),
        "trade_quality_decision": assessment.decision,
        "plan": {
            "valid_now": bool(plan.valid_now),
            "entry_style": plan.entry_style,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "chase_limit": plan.chase_limit,
            "stop_price": plan.stop_price,
            "target_1": plan.target_1,
            "target_2": plan.target_2,
            "reward_to_risk_1": plan.reward_to_risk_1,
            "reward_to_risk_2": plan.reward_to_risk_2,
            "risk_level": plan.risk_level,
        },
        "upstream": {
            "technical_score": candidate.get("technical_score"),
            "chief_setup_score": candidate.get("confidence"),
            "chief_decision": candidate.get("decision"),
            "price_movement": candidate.get("price_movement"),
        },
        "outcome_contract": {
            "primary_event": "TARGET_1_BEFORE_STOP_WITHIN_HORIZON",
            "secondary_event": "TARGET_2_BEFORE_STOP_WITHIN_HORIZON",
            "supporting_labels": [
                "MFE",
                "MAE",
                "TIME_TO_TARGET_1",
                "TIME_TO_TARGET_2",
                "TIME_TO_STOP",
                "NET_RETURN_AFTER_FEES_SLIPPAGE",
            ],
            "probability_claimed": False,
        },
        "measurement_only": True,
        "affects_live_decisions": False,
        "trade_authority_changed": False,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with registry_lock(_lock_file(path)):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                    default=str,
                )
                + "\n"
            )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    return evidence_id
