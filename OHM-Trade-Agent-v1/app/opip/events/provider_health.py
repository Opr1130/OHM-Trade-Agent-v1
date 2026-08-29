"""Durable provider health and freshness state for O'Pip intelligence.

Provider health is evidence about the evidence pipeline. It never participates
in trade qualification, ranking, alerts, paper admission, or execution.

The durable state distinguishes:
HEALTHY / NO_EVENT / STALE / UNAVAILABLE / RATE_LIMITED /
MISSING_CREDENTIALS / DEGRADED.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from app.opip.events.contract import parse_utc, require_utc
from app.services.registry_io import (
    RegistryIOError,
    load_json,
    registry_lock,
    save_json_atomic,
)


PROVIDER_HEALTH_FILE = Path("/app/data/opip/events/provider_health.json")
PROVIDER_HEALTH_SCHEMA_VERSION = 1


class ProviderHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    NO_EVENT = "NO_EVENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    MISSING_CREDENTIALS = "MISSING_CREDENTIALS"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    state: ProviderHealthState
    checked_at_utc: datetime
    configured: bool
    expected_interval_seconds: int
    request_count: int = 0
    events_received: int = 0
    fresh_events: int = 0
    stale_events: int = 0
    last_attempt_at_utc: datetime | None = None
    last_success_at_utc: datetime | None = None
    last_event_ingested_at_utc: datetime | None = None
    last_event_source_time_utc: datetime | None = None
    last_observation_at_utc: datetime | None = None
    last_error_at_utc: datetime | None = None
    consecutive_failures: int = 0
    latest_event_lag_seconds: float | None = None
    freshness_age_seconds: float | None = None
    reason: str | None = None
    error_kind: str | None = None
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if not str(self.provider or "").strip():
            raise ValueError("provider is required")
        require_utc(self.checked_at_utc, field_name="checked_at_utc")
        if self.expected_interval_seconds < 1:
            raise ValueError("expected_interval_seconds must be positive")
        for name in (
            "last_attempt_at_utc",
            "last_success_at_utc",
            "last_event_ingested_at_utc",
            "last_event_source_time_utc",
            "last_observation_at_utc",
            "last_error_at_utc",
        ):
            value = getattr(self, name)
            if value is not None:
                require_utc(value, field_name=name)
        if self.consecutive_failures < 0:
            raise ValueError("consecutive_failures cannot be negative")
        for name in (
            "request_count",
            "events_received",
            "fresh_events",
            "stale_events",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        for name in (
            "checked_at_utc",
            "last_attempt_at_utc",
            "last_success_at_utc",
            "last_event_ingested_at_utc",
            "last_event_source_time_utc",
            "last_error_at_utc",
        ):
            value = getattr(self, name)
            payload[name] = value.isoformat() if value is not None else None
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProviderHealthSnapshot":
        checked = parse_utc(payload.get("checked_at_utc"), field_name="checked_at_utc")
        if checked is None:
            raise ValueError("checked_at_utc is required")
        return cls(
            provider=str(payload["provider"]),
            state=ProviderHealthState(str(payload["state"])),
            checked_at_utc=checked,
            configured=bool(payload.get("configured")),
            expected_interval_seconds=int(payload.get("expected_interval_seconds") or 300),
            request_count=int(payload.get("request_count") or 0),
            events_received=int(payload.get("events_received") or 0),
            fresh_events=int(payload.get("fresh_events") or 0),
            stale_events=int(payload.get("stale_events") or 0),
            last_attempt_at_utc=parse_utc(
                payload.get("last_attempt_at_utc"),
                field_name="last_attempt_at_utc",
            ),
            last_success_at_utc=parse_utc(
                payload.get("last_success_at_utc"),
                field_name="last_success_at_utc",
            ),
            last_event_ingested_at_utc=parse_utc(
                payload.get("last_event_ingested_at_utc"),
                field_name="last_event_ingested_at_utc",
            ),
            last_event_source_time_utc=parse_utc(
                payload.get("last_event_source_time_utc"),
                field_name="last_event_source_time_utc",
            ),
            last_observation_at_utc=parse_utc(
                payload.get("last_observation_at_utc"),
                field_name="last_observation_at_utc",
            ),
            last_error_at_utc=parse_utc(
                payload.get("last_error_at_utc"),
                field_name="last_error_at_utc",
            ),
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
            latest_event_lag_seconds=(
                float(payload["latest_event_lag_seconds"])
                if payload.get("latest_event_lag_seconds") is not None
                else None
            ),
            freshness_age_seconds=(
                float(payload["freshness_age_seconds"])
                if payload.get("freshness_age_seconds") is not None
                else None
            ),
            reason=(
                str(payload["reason"]) if payload.get("reason") is not None else None
            ),
            error_kind=(
                str(payload["error_kind"])
                if payload.get("error_kind") is not None
                else None
            ),
            retry_after_seconds=(
                int(payload["retry_after_seconds"])
                if payload.get("retry_after_seconds") is not None
                else None
            ),
        )


class ProviderHealthStore:
    def __init__(self, path: Path = PROVIDER_HEALTH_FILE) -> None:
        self.path = path
        self.lock_path = path.parent / f".{path.name}.lock"

    def _load_payload(self) -> dict[str, Any]:
        try:
            payload = load_json(self.path)
        except (OSError, TimeoutError, RegistryIOError):
            return {
                "schema_version": PROVIDER_HEALTH_SCHEMA_VERSION,
                "providers": {},
            }
        if not isinstance(payload, dict):
            return {
                "schema_version": PROVIDER_HEALTH_SCHEMA_VERSION,
                "providers": {},
            }
        providers = payload.get("providers")
        if not isinstance(providers, dict):
            payload["providers"] = {}
        payload["schema_version"] = PROVIDER_HEALTH_SCHEMA_VERSION
        return payload

    def _existing(
        self,
        payload: dict[str, Any],
        provider: str,
    ) -> ProviderHealthSnapshot | None:
        providers = payload.get("providers")
        raw = providers.get(provider) if isinstance(providers, dict) else None
        if not isinstance(raw, dict):
            return None
        try:
            return ProviderHealthSnapshot.from_dict(raw)
        except (ValueError, KeyError, TypeError):
            return None

    def _persist(self, snapshot: ProviderHealthSnapshot) -> ProviderHealthSnapshot:
        with registry_lock(self.lock_path):
            payload = self._load_payload()
            providers = payload.setdefault("providers", {})
            providers[snapshot.provider] = snapshot.to_dict()
            payload["updated_at_utc"] = snapshot.checked_at_utc.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def record_missing_credentials(
        self,
        *,
        provider: str,
        checked_at: datetime,
        expected_interval_seconds: int,
        reason: str = "provider credential is not configured",
    ) -> ProviderHealthSnapshot:
        now = require_utc(checked_at, field_name="checked_at")
        with registry_lock(self.lock_path):
            payload = self._load_payload()
            prior = self._existing(payload, provider)
            snapshot = ProviderHealthSnapshot(
                provider=provider,
                state=ProviderHealthState.MISSING_CREDENTIALS,
                checked_at_utc=now,
                configured=False,
                expected_interval_seconds=expected_interval_seconds,
                last_attempt_at_utc=now,
                last_success_at_utc=prior.last_success_at_utc if prior else None,
                last_event_ingested_at_utc=(
                    prior.last_event_ingested_at_utc if prior else None
                ),
                last_event_source_time_utc=(
                    prior.last_event_source_time_utc if prior else None
                ),
                last_observation_at_utc=(
                    prior.last_observation_at_utc if prior else None
                ),
                last_error_at_utc=prior.last_error_at_utc if prior else None,
                consecutive_failures=0,
                latest_event_lag_seconds=(
                    prior.latest_event_lag_seconds if prior else None
                ),
                reason=reason,
            )
            providers = payload.setdefault("providers", {})
            providers[provider] = snapshot.to_dict()
            payload["updated_at_utc"] = now.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def record_unavailable(
        self,
        *,
        provider: str,
        checked_at: datetime,
        expected_interval_seconds: int,
        request_count: int,
        rate_limited: bool = False,
        retry_after_seconds: int | None = None,
        reason: str = "provider request unavailable",
        error_kind: str | None = None,
    ) -> ProviderHealthSnapshot:
        now = require_utc(checked_at, field_name="checked_at")
        with registry_lock(self.lock_path):
            payload = self._load_payload()
            prior = self._existing(payload, provider)
            snapshot = ProviderHealthSnapshot(
                provider=provider,
                state=(
                    ProviderHealthState.RATE_LIMITED
                    if rate_limited
                    else ProviderHealthState.UNAVAILABLE
                ),
                checked_at_utc=now,
                configured=True,
                expected_interval_seconds=expected_interval_seconds,
                request_count=max(0, int(request_count)),
                last_attempt_at_utc=now,
                last_success_at_utc=prior.last_success_at_utc if prior else None,
                last_event_ingested_at_utc=(
                    prior.last_event_ingested_at_utc if prior else None
                ),
                last_event_source_time_utc=(
                    prior.last_event_source_time_utc if prior else None
                ),
                last_observation_at_utc=(
                    prior.last_observation_at_utc if prior else None
                ),
                last_error_at_utc=now,
                consecutive_failures=(
                    (prior.consecutive_failures if prior else 0) + 1
                ),
                latest_event_lag_seconds=(
                    prior.latest_event_lag_seconds if prior else None
                ),
                reason=reason,
                error_kind=error_kind,
                retry_after_seconds=retry_after_seconds,
            )
            providers = payload.setdefault("providers", {})
            providers[provider] = snapshot.to_dict()
            payload["updated_at_utc"] = now.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def record_success(
        self,
        *,
        provider: str,
        checked_at: datetime,
        expected_interval_seconds: int,
        request_count: int,
        event_source_times: Iterable[datetime] = (),
        event_ingest_times: Iterable[datetime] = (),
        stale_events: int = 0,
        degraded_reason: str | None = None,
        latest_event_lag_seconds: float | None = None,
    ) -> ProviderHealthSnapshot:
        now = require_utc(checked_at, field_name="checked_at")
        source_times = [
            require_utc(item, field_name="event_source_time")
            for item in event_source_times
        ]
        ingest_times = [
            require_utc(item, field_name="event_ingest_time")
            for item in event_ingest_times
        ]
        event_count = max(len(source_times), len(ingest_times))
        stale_count = max(0, min(int(stale_events), event_count))
        fresh_count = max(0, event_count - stale_count)

        if degraded_reason:
            state = ProviderHealthState.DEGRADED
        elif event_count == 0:
            state = ProviderHealthState.NO_EVENT
        elif fresh_count == 0:
            state = ProviderHealthState.STALE
        else:
            state = ProviderHealthState.HEALTHY

        with registry_lock(self.lock_path):
            payload = self._load_payload()
            prior = self._existing(payload, provider)
            latest_source = max(source_times) if source_times else (
                prior.last_event_source_time_utc if prior else None
            )
            latest_ingest = max(ingest_times) if ingest_times else (
                prior.last_event_ingested_at_utc if prior else None
            )
            freshness_age = None
            if latest_ingest is not None:
                freshness_age = max(0.0, (now - latest_ingest).total_seconds())
            snapshot = ProviderHealthSnapshot(
                provider=provider,
                state=state,
                checked_at_utc=now,
                configured=True,
                expected_interval_seconds=expected_interval_seconds,
                request_count=max(0, int(request_count)),
                events_received=event_count,
                fresh_events=fresh_count,
                stale_events=stale_count,
                last_attempt_at_utc=now,
                last_success_at_utc=now,
                last_event_ingested_at_utc=latest_ingest,
                last_event_source_time_utc=latest_source,
                last_observation_at_utc=latest_ingest or now,
                last_error_at_utc=prior.last_error_at_utc if prior else None,
                consecutive_failures=0,
                latest_event_lag_seconds=latest_event_lag_seconds,
                freshness_age_seconds=freshness_age,
                reason=degraded_reason,
            )
            providers = payload.setdefault("providers", {})
            providers[provider] = snapshot.to_dict()
            payload["updated_at_utc"] = now.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def record_context_success(
        self,
        *,
        provider: str,
        checked_at: datetime,
        expected_interval_seconds: int,
        request_count: int = 1,
        source_observed_at: datetime | None = None,
        degraded_reason: str | None = None,
    ) -> ProviderHealthSnapshot:
        """Record non-event context/reference provider health."""
        now = require_utc(checked_at, field_name="checked_at")
        observed = (
            require_utc(source_observed_at, field_name="source_observed_at")
            if source_observed_at is not None
            else now
        )
        with registry_lock(self.lock_path):
            payload = self._load_payload()
            prior = self._existing(payload, provider)
            age = max(0.0, (now - observed).total_seconds())
            stale_threshold = max(1, int(expected_interval_seconds)) * 3
            snapshot = ProviderHealthSnapshot(
                provider=provider,
                state=(
                    ProviderHealthState.DEGRADED
                    if degraded_reason
                    else (
                        ProviderHealthState.STALE
                        if age > stale_threshold
                        else ProviderHealthState.HEALTHY
                    )
                ),
                checked_at_utc=now,
                configured=True,
                expected_interval_seconds=expected_interval_seconds,
                request_count=max(0, int(request_count)),
                last_attempt_at_utc=now,
                last_success_at_utc=now,
                last_event_ingested_at_utc=(
                    prior.last_event_ingested_at_utc if prior else None
                ),
                last_event_source_time_utc=(
                    prior.last_event_source_time_utc if prior else None
                ),
                last_observation_at_utc=observed,
                last_error_at_utc=prior.last_error_at_utc if prior else None,
                consecutive_failures=0,
                freshness_age_seconds=age,
                reason=degraded_reason,
            )
            providers = payload.setdefault("providers", {})
            providers[provider] = snapshot.to_dict()
            payload["updated_at_utc"] = now.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def record_degraded(
        self,
        *,
        provider: str,
        checked_at: datetime,
        expected_interval_seconds: int,
        configured: bool,
        reason: str,
        request_count: int = 0,
    ) -> ProviderHealthSnapshot:
        now = require_utc(checked_at, field_name="checked_at")
        with registry_lock(self.lock_path):
            payload = self._load_payload()
            prior = self._existing(payload, provider)
            snapshot = ProviderHealthSnapshot(
                provider=provider,
                state=ProviderHealthState.DEGRADED,
                checked_at_utc=now,
                configured=configured,
                expected_interval_seconds=expected_interval_seconds,
                request_count=max(0, int(request_count)),
                last_attempt_at_utc=now,
                last_success_at_utc=prior.last_success_at_utc if prior else None,
                last_event_ingested_at_utc=(
                    prior.last_event_ingested_at_utc if prior else None
                ),
                last_event_source_time_utc=(
                    prior.last_event_source_time_utc if prior else None
                ),
                last_observation_at_utc=(
                    prior.last_observation_at_utc if prior else None
                ),
                last_error_at_utc=prior.last_error_at_utc if prior else None,
                consecutive_failures=prior.consecutive_failures if prior else 0,
                latest_event_lag_seconds=(
                    prior.latest_event_lag_seconds if prior else None
                ),
                reason=reason,
            )
            providers = payload.setdefault("providers", {})
            providers[provider] = snapshot.to_dict()
            payload["updated_at_utc"] = now.isoformat()
            save_json_atomic(self.path, payload)
        return snapshot

    def read(
        self,
        provider: str,
        *,
        as_of: datetime | None = None,
        stale_multiplier: int = 3,
    ) -> ProviderHealthSnapshot | None:
        payload = self._load_payload()
        snapshot = self._existing(payload, provider)
        if snapshot is None:
            return None
        if as_of is None:
            return snapshot
        now = require_utc(as_of, field_name="as_of")
        success = snapshot.last_success_at_utc
        if success is None:
            return snapshot
        age = max(0.0, (now - success).total_seconds())
        threshold = max(
            snapshot.expected_interval_seconds,
            snapshot.expected_interval_seconds * max(1, int(stale_multiplier)),
        )
        if (
            age > threshold
            and snapshot.state
            in {ProviderHealthState.HEALTHY, ProviderHealthState.NO_EVENT}
        ):
            return replace(
                snapshot,
                state=ProviderHealthState.STALE,
                checked_at_utc=now,
                freshness_age_seconds=age,
                reason=(
                    "provider has not produced a successful health observation "
                    f"within {threshold}s"
                ),
            )
        return replace(snapshot, freshness_age_seconds=age)

    def read_all(
        self,
        *,
        as_of: datetime | None = None,
        stale_multiplier: int = 3,
    ) -> tuple[ProviderHealthSnapshot, ...]:
        payload = self._load_payload()
        providers = payload.get("providers")
        if not isinstance(providers, dict):
            return ()
        result: list[ProviderHealthSnapshot] = []
        for provider in sorted(providers):
            item = self.read(
                provider,
                as_of=as_of,
                stale_multiplier=stale_multiplier,
            )
            if item is not None:
                result.append(item)
        return tuple(result)
