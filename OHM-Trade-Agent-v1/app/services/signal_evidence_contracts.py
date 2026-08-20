from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal


EvidenceBias = Literal["BULLISH", "BEARISH", "NEUTRAL"]
ProviderStatus = Literal["AVAILABLE", "UNAVAILABLE", "UNSUPPORTED", "AMBIGUOUS", "STALE", "ERROR"]
IdentityStatus = Literal["CONFIRMED", "AMBIGUOUS", "UNAVAILABLE", "NOT_REQUIRED"]
SignalStage = Literal["DISCOVERED", "WATCH", "READY", "USER_ACCEPTED", "ACTIVE", "CLOSED", "EXPIRED", "REJECTED"]


@dataclass(frozen=True)
class EvidenceEnvelope:
    source: str
    evidence_type: str
    symbol: str
    bias: EvidenceBias = "NEUTRAL"
    strength: int = 0
    provider_status: ProviderStatus = "UNAVAILABLE"
    identity_status: IdentityStatus = "NOT_REQUIRED"
    observed_at: str = ""
    received_at: str = ""
    freshness_seconds: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if not 0 <= int(self.strength) <= 10:
            raise ValueError("Evidence strength must be 0..10")
        if self.provider_status != "AVAILABLE" and self.bias != "NEUTRAL":
            raise ValueError("Unavailable/ambiguous/stale evidence cannot carry directional bias")
        if self.identity_status == "AMBIGUOUS" and self.bias != "NEUTRAL":
            raise ValueError("Ambiguous identity cannot provide positive or negative confirmation")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MovementSignalEnvelope:
    detector: str
    detector_version: str
    symbol: str
    direction: str
    stage: SignalStage
    observed_at: str
    reference_price: float
    score_rank: int | None = None
    entry_quality: int | None = None
    expires_at: str | None = None
    source_fingerprint: str = ""
    actionable: bool = False
    evidence: tuple[EvidenceEnvelope, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def signal_id(self) -> str:
        raw = "|".join(
            (
                self.detector,
                self.detector_version,
                self.symbol.upper(),
                self.direction.upper(),
                self.observed_at,
                self.source_fingerprint,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["signal_id"] = self.signal_id
        return payload


LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"WATCH", "READY", "EXPIRED", "REJECTED"},
    "WATCH": {"READY", "EXPIRED", "REJECTED"},
    "READY": {"WATCH", "USER_ACCEPTED", "EXPIRED", "REJECTED"},
    "USER_ACCEPTED": {"ACTIVE", "EXPIRED", "REJECTED"},
    "ACTIVE": {"CLOSED"},
    "CLOSED": set(),
    "EXPIRED": set(),
    "REJECTED": set(),
}


def transition_allowed(current: str, target: str) -> bool:
    return target.upper() in LEGAL_TRANSITIONS.get(current.upper(), set())


def normalize_evidence(
    *,
    source: str,
    evidence_type: str,
    symbol: str,
    available: bool,
    bias: str = "NEUTRAL",
    strength: int = 0,
    freshness_seconds: float | None = None,
    metrics: dict[str, Any] | None = None,
    identity_status: str = "NOT_REQUIRED",
    observed_at: str | None = None,
) -> EvidenceEnvelope:
    now = observed_at or datetime.now(timezone.utc).isoformat()
    status: ProviderStatus = "AVAILABLE" if available else "UNAVAILABLE"
    normalized_bias: EvidenceBias = bias.upper() if available and bias.upper() in {"BULLISH", "BEARISH", "NEUTRAL"} else "NEUTRAL"
    return EvidenceEnvelope(
        source=source,
        evidence_type=evidence_type,
        symbol=symbol.upper(),
        bias=normalized_bias,
        strength=max(0, min(10, int(strength))) if available else 0,
        provider_status=status,
        identity_status=identity_status.upper(),
        observed_at=now,
        received_at=datetime.now(timezone.utc).isoformat(),
        freshness_seconds=freshness_seconds,
        metrics=metrics or {},
    )


def serialize_envelope(envelope: MovementSignalEnvelope) -> str:
    return json.dumps(envelope.as_dict(), sort_keys=True, default=str)
