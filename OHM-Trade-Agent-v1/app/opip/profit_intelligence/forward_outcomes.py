"""Profit Intelligence point-in-time forward-outcome projection (read-only).

A forward outcome answers "what happened *after* this sealed decision point?".
That question is only safe if the decision-time facts and the future facts stay
in separate fields and the evaluation window is explicit. This module projects
the two canonical forward-outcome evidence families into that form without
mixing them and without ever rewriting the historical decision context.

Evidence families (both already produced today)
-----------------------------------------------
``PHASE3C``
    ``app.services.phase3c_outcomes`` matured by
    ``app.jobs.build_phase3c_forward_outcomes`` into
    ``/app/data/phase3c_forward_outcomes.jsonl``; keyed by ``snapshot_id``,
    ``label_schema_version`` 2, horizons 5m/15m/30m/60m/4h/8h/12h/24h.

``DISCOVERY``
    ``app.opip.discovery.outcomes`` matured by
    ``app.jobs.build_discovery_forward_outcomes`` into
    ``app.opip.discovery.store.FORWARD_OUTCOMES_FILE``; keyed by
    ``observation_id``, horizons 1h/4h/12h.

The two families are **not** merged: they are different producers with different
identities, horizons and schema versions, and the projection carries the source
so a consumer can never confuse them. Labels are published unchanged - ``60m``
and ``1h`` are the same duration but remain distinct canonical labels and are
never renamed into one another.

Anti-hindsight contract
-----------------------
Hard boundary, enforced structurally and by validation:

* decision-time context (``reference_at``, ``reference_price``, direction) is
  separate from every horizon's future fields;
* a forward window's ``window_end`` must be strictly after its ``reference_at``;
  :func:`forward_window_is_point_in_time` proves it, and a violation marks the
  horizon ``UNAVAILABLE`` rather than being silently trusted (the check proves the
  declared window arithmetic given ``reference_at``; it does not independently
  validate the upstream observations, which remain the producers' responsibility);
* incomplete windows stay incomplete and unavailable market data stays
  unavailable - neither is ever written as a ``0`` return;
* the projection has no policy authority and its output is ``EVIDENCE_ONLY``:
  nothing here can change a threshold, a strategy or a trading decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.opip.cockpit.trust import (
    Completeness,
    Freshness,
    TrustEnvelope,
    unavailable as unavailable_trust,
)
from app.opip.profit_intelligence.semantics import FactAvailability

#: Bumped whenever a derived definition in this projection changes.
FORWARD_OUTCOME_PROJECTION_VERSION = "profit-intelligence-forward-outcomes-v1"

#: Envelope schema version for this projection's serialised form.
FORWARD_OUTCOME_SCHEMA_VERSION = 1

#: What this projection can and cannot prove, stated once for every consumer.
FORWARD_OUTCOME_SCOPE = (
    "Post-decision forward evidence for a sealed observation/decision point: the "
    "evaluation window, the outcome horizon(s), the forward return and the maximum "
    "favourable/adverse excursion, with the outcome calculation version and its "
    "source. Decision-time facts and future facts are kept separate. This is "
    "market-outcome evidence, not executable profit."
)

#: The population this projection counts over.
FORWARD_OUTCOME_POPULATION_SEMANTICS = (
    "One population per source family. PHASE3C rows are keyed by snapshot_id; "
    "DISCOVERY rows are keyed by observation_id. The two populations are never "
    "added together and are never de-duplicated against each other."
)

#: How to read availability in this projection.
FORWARD_OUTCOME_AVAILABILITY_SEMANTICS = (
    "A horizon is KNOWN only when its window is complete and a real forward "
    "observation produced its value. A partially observed window is INCOMPLETE and "
    "its return/excursions are DERIVED, never KNOWN; an unobserved horizon is "
    "UNAVAILABLE with null values. Neither an incomplete nor an unavailable horizon "
    "is ever written as a measured zero."
)

#: Canonical horizon labels -> duration, for window arithmetic only. The label
#: vocabulary is owned by the producer families; ``60m`` and ``1h`` are distinct
#: canonical labels for the same duration and are deliberately NOT renamed. An
#: unrecognised label resolves to ``None`` (unavailable) rather than a guess.
_HORIZON_DURATION_SECONDS: Mapping[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "1h": 3600,
    "4h": 14_400,
    "8h": 28_800,
    "12h": 43_200,
    "24h": 86_400,
}


class ForwardOutcomeSource(str, Enum):
    """Which canonical producer family a forward-outcome record came from."""

    PHASE3C = "PHASE3C_FORWARD_OUTCOME"
    DISCOVERY = "DISCOVERY_FORWARD_OUTCOME"


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def horizon_duration_seconds(label: Any) -> float | None:
    """Return the canonical duration for a horizon label, or ``None``.

    ``None`` means "this projection has no duration for that label", which is
    surfaced as unavailable window arithmetic rather than a guessed duration.
    """
    key = _text(label)
    if key is None:
        return None
    seconds = _HORIZON_DURATION_SECONDS.get(key)
    return float(seconds) if seconds is not None else None


def forward_window_is_point_in_time(
    *,
    reference_at: datetime | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    """Whether a forward window is anchored strictly after the sealed cutoff.

    The sealed decision-time boundary is ``reference_at``. A forward window must
    start at or after that boundary and end strictly after it; otherwise the
    "future" evidence would overlap the moment it claims to follow.
    """
    if reference_at is None or window_start is None or window_end is None:
        return False
    return window_start >= reference_at and window_end > reference_at


@dataclass(frozen=True)
class ForwardOutcomeHorizon:
    """One forward evaluation horizon anchored to a sealed decision point."""

    horizon_id: str
    horizon_seconds: float | None
    window_start: datetime | None
    window_end: datetime | None
    observed: bool
    window_complete: bool
    point_in_time: bool
    maturation_status: str
    return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    availability: FactAvailability

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_id": self.horizon_id,
            "horizon_seconds": self.horizon_seconds,
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "observed": self.observed,
            "window_complete": self.window_complete,
            "point_in_time": self.point_in_time,
            "maturation_status": self.maturation_status,
            "return_pct": self.return_pct,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "availability": self.availability.value,
        }


@dataclass(frozen=True)
class ForwardOutcomeRecord:
    """A sealed decision point and its (separate) future evidence."""

    source: ForwardOutcomeSource
    identity: str
    identity_kind: str
    reference_at: datetime | None
    reference_price: float | None
    direction: str | None = None
    symbol: str | None = None
    venue_instrument_id: str | None = None
    canonical_underlying_asset: str | None = None
    canonical_episode_id: str | None = None
    signal_episode_id: str | None = None
    move_episode_id: str | None = None
    outcome_definition: str | None = None
    outcome_calculation_version: str | int | None = None
    outcome_source: str | None = None
    primary_horizon: str | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    window_complete: bool = False
    maturation_status: str = ""
    producer: str = ""
    availability: FactAvailability = FactAvailability.KNOWN
    unavailable_fields: tuple[str, ...] = ()
    horizons: tuple[ForwardOutcomeHorizon, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "identity": self.identity,
            "identity_kind": self.identity_kind,
            "reference_at": _iso(self.reference_at),
            "reference_price": self.reference_price,
            "direction": self.direction,
            "symbol": self.symbol,
            "venue_instrument_id": self.venue_instrument_id,
            "canonical_underlying_asset": self.canonical_underlying_asset,
            "canonical_episode_id": self.canonical_episode_id,
            "signal_episode_id": self.signal_episode_id,
            "move_episode_id": self.move_episode_id,
            "outcome_definition": self.outcome_definition,
            "outcome_calculation_version": self.outcome_calculation_version,
            "outcome_source": self.outcome_source,
            "primary_horizon": self.primary_horizon,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "window_complete": self.window_complete,
            "maturation_status": self.maturation_status,
            "producer": self.producer,
            "availability": self.availability.value,
            "unavailable_fields": list(self.unavailable_fields),
            "horizons": [horizon.to_dict() for horizon in self.horizons],
        }


def _horizon_availability(
    *,
    observed: bool,
    window_complete: bool,
    point_in_time: bool,
    has_return: bool,
) -> FactAvailability:
    if not point_in_time:
        return FactAvailability.UNAVAILABLE
    if not observed:
        return FactAvailability.UNAVAILABLE
    if window_complete and has_return:
        return FactAvailability.KNOWN
    return FactAvailability.DERIVED


def _build_horizon(
    *,
    label: str,
    reference_at: datetime | None,
    observed: bool,
    window_complete: bool,
    maturation_status: str,
    return_pct: float | None,
    mfe_pct: float | None,
    mae_pct: float | None,
) -> ForwardOutcomeHorizon:
    seconds = horizon_duration_seconds(label)
    window_start = reference_at
    window_end = (
        reference_at + timedelta(seconds=seconds)
        if reference_at is not None and seconds is not None
        else None
    )
    point_in_time = forward_window_is_point_in_time(
        reference_at=reference_at,
        window_start=window_start,
        window_end=window_end,
    )
    return ForwardOutcomeHorizon(
        horizon_id=label,
        horizon_seconds=seconds,
        window_start=window_start,
        window_end=window_end,
        observed=observed,
        window_complete=window_complete,
        point_in_time=point_in_time,
        maturation_status=maturation_status,
        return_pct=return_pct,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        availability=_horizon_availability(
            observed=observed,
            window_complete=window_complete,
            point_in_time=point_in_time,
            has_return=return_pct is not None,
        ),
    )


def _phase3c_record(row: Mapping[str, Any]) -> ForwardOutcomeRecord:
    identity = _text(row.get("snapshot_id")) or ""
    reference_at = _parse_utc(row.get("reference_at") or row.get("decision_at_utc"))
    reference_price = _finite(row.get("reference_price"))
    returns = row.get("horizon_returns_pct")
    observed_map = row.get("horizon_observed")
    returns = returns if isinstance(returns, Mapping) else {}
    observed_map = observed_map if isinstance(observed_map, Mapping) else {}

    labels = sorted({str(key) for key in list(returns) + list(observed_map)})
    window_complete = bool(row.get("window_complete", False))
    maturation = str(row.get("maturation_status") or "")
    horizons = tuple(
        _build_horizon(
            label=label,
            reference_at=reference_at,
            observed=bool(observed_map.get(label, False)),
            window_complete=window_complete,
            maturation_status=maturation,
            return_pct=_finite(returns.get(label)),
            # Phase 3C records one MFE/MAE pair for its MFE_MAE horizon, not per
            # horizon, so attaching it to every horizon would misattribute it.
            # The canonical values are surfaced once at record level instead.
            mfe_pct=None,
            mae_pct=None,
        )
        for label in labels
    )

    missing = tuple(
        name
        for name, value in (
            ("snapshot_id", identity or None),
            ("reference_at", reference_at),
            ("reference_price", reference_price),
        )
        if value is None
    )
    version = row.get("label_schema_version")
    return ForwardOutcomeRecord(
        source=ForwardOutcomeSource.PHASE3C,
        identity=identity,
        identity_kind="snapshot_id",
        reference_at=reference_at,
        reference_price=reference_price,
        direction=None,
        symbol=_text(row.get("symbol")),
        canonical_episode_id=_text(row.get("canonical_episode_id")),
        signal_episode_id=_text(row.get("signal_episode_id")),
        move_episode_id=_text(row.get("move_episode_id")),
        outcome_calculation_version=version if version is not None else None,
        outcome_source=_text(row.get("outcome_source")),
        primary_horizon=None,
        mfe_pct=_finite(row.get("mfe_pct")),
        mae_pct=_finite(row.get("mae_pct")),
        window_complete=window_complete,
        maturation_status=maturation,
        producer="app.jobs.build_phase3c_forward_outcomes",
        availability=(
            FactAvailability.KNOWN if not missing else FactAvailability.UNAVAILABLE
        ),
        unavailable_fields=missing,
        horizons=horizons,
    )


def _discovery_record(row: Mapping[str, Any]) -> ForwardOutcomeRecord:
    identity = _text(row.get("observation_id")) or ""
    reference_at = _parse_utc(row.get("reference_at") or row.get("observed_at"))
    reference_price = _finite(row.get("reference_price"))
    horizons_map = row.get("horizons")
    horizons_map = horizons_map if isinstance(horizons_map, Mapping) else {}

    horizons: list[ForwardOutcomeHorizon] = []
    for label in sorted(str(key) for key in horizons_map):
        payload = horizons_map.get(label)
        payload = payload if isinstance(payload, Mapping) else {}
        horizons.append(
            _build_horizon(
                label=label,
                reference_at=reference_at,
                observed=bool(payload.get("horizon_observed", False)),
                window_complete=bool(payload.get("window_complete", False)),
                maturation_status=str(payload.get("maturation_status") or ""),
                return_pct=_finite(payload.get("horizon_return_pct")),
                mfe_pct=_finite(payload.get("mfe_pct")),
                mae_pct=_finite(payload.get("mae_pct")),
            )
        )

    missing = tuple(
        name
        for name, value in (
            ("observation_id", identity or None),
            ("reference_at", reference_at),
            ("reference_price", reference_price),
        )
        if value is None
    )
    version = row.get("label_schema_version")
    return ForwardOutcomeRecord(
        source=ForwardOutcomeSource.DISCOVERY,
        identity=identity,
        identity_kind="observation_id",
        reference_at=reference_at,
        reference_price=reference_price,
        direction=_text(
            row.get("production_preferred_direction") or row.get("direction")
        ),
        symbol=_text(row.get("canonical_underlying_asset")),
        venue_instrument_id=_text(row.get("venue_instrument_id")),
        canonical_underlying_asset=_text(row.get("canonical_underlying_asset")),
        outcome_definition=_text(row.get("outcome_definition")),
        outcome_calculation_version=version if version is not None else None,
        primary_horizon=_text(row.get("primary_horizon")),
        mfe_pct=_finite(row.get("mfe_pct")),
        mae_pct=_finite(row.get("mae_pct")),
        window_complete=bool(row.get("window_complete", False)),
        maturation_status=str(row.get("maturation_status") or ""),
        producer="app.jobs.build_discovery_forward_outcomes",
        availability=(
            FactAvailability.KNOWN if not missing else FactAvailability.UNAVAILABLE
        ),
        unavailable_fields=missing,
        horizons=tuple(horizons),
    )


_RECORD_BUILDERS = {
    ForwardOutcomeSource.PHASE3C: _phase3c_record,
    ForwardOutcomeSource.DISCOVERY: _discovery_record,
}


@dataclass(frozen=True)
class ForwardOutcomeCompletion:
    """How much of the projected population is complete versus pending."""

    records: int
    complete_windows: int
    incomplete_windows: int
    maturation_status_counts: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "complete_windows": self.complete_windows,
            "incomplete_windows": self.incomplete_windows,
            "maturation_status_counts": dict(self.maturation_status_counts),
        }


@dataclass(frozen=True)
class ForwardOutcomeProjection:
    """Versioned, point-in-time-annotated view of one forward-outcome family."""

    source: ForwardOutcomeSource
    generated_at: datetime
    records: tuple[ForwardOutcomeRecord, ...] = ()
    schema_version: int = FORWARD_OUTCOME_SCHEMA_VERSION
    projection_version: str = FORWARD_OUTCOME_PROJECTION_VERSION
    producer_version: str | None = None
    source_provenance: str = ""
    scope: str = FORWARD_OUTCOME_SCOPE
    population_semantics: str = FORWARD_OUTCOME_POPULATION_SEMANTICS
    availability_semantics: str = FORWARD_OUTCOME_AVAILABILITY_SEMANTICS
    window_start: datetime | None = None
    window_end: datetime | None = None
    horizon_ids: tuple[str, ...] = ()
    duplicate_rows_ignored: int = 0
    completion: ForwardOutcomeCompletion | None = None
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_version": self.projection_version,
            "source": self.source.value,
            "producer_version": self.producer_version,
            "source_provenance": self.source_provenance,
            "scope": self.scope,
            "population_semantics": self.population_semantics,
            "availability_semantics": self.availability_semantics,
            "generated_at": _iso(self.generated_at),
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "horizon_ids": list(self.horizon_ids),
            "duplicate_rows_ignored": self.duplicate_rows_ignored,
            "completion": self.completion.to_dict() if self.completion else None,
            "trust": self.trust.to_dict(),
            "records": [record.to_dict() for record in self.records],
            "details": list(self.details),
        }


def _completion(records: Sequence[ForwardOutcomeRecord]) -> ForwardOutcomeCompletion:
    complete = sum(1 for record in records if record.window_complete)
    statuses: dict[str, int] = {}
    for record in records:
        key = record.maturation_status or "UNKNOWN"
        statuses[key] = statuses.get(key, 0) + 1
    return ForwardOutcomeCompletion(
        records=len(records),
        complete_windows=complete,
        incomplete_windows=len(records) - complete,
        maturation_status_counts=dict(sorted(statuses.items())),
    )


def _trust_for(
    records: Sequence[ForwardOutcomeRecord],
    completion: ForwardOutcomeCompletion,
) -> TrustEnvelope:
    """Trust envelope for a healthy read, degraded by any untrusted record.

    Completeness is INCOMPLETE when any window has not matured *or* any record
    could not be read (for example a missing reference price). An unreadable
    record is named in ``reasons`` rather than left to look complete.
    """
    reasons: list[str] = []
    if completion.incomplete_windows:
        reasons.append("FORWARD_WINDOWS_NOT_MATURED")
    if any(
        record.availability is FactAvailability.UNAVAILABLE for record in records
    ):
        reasons.append("FORWARD_RECORDS_UNAVAILABLE")
    if reasons:
        return TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.INCOMPLETE,
            reasons=tuple(reasons),
        )
    return TrustEnvelope(freshness=Freshness.LIVE, completeness=Completeness.COMPLETE)


def _producer_version(records: Sequence[ForwardOutcomeRecord]) -> str | None:
    versions = sorted(
        {
            str(record.outcome_calculation_version)
            for record in records
            if record.outcome_calculation_version is not None
        }
    )
    if not versions:
        return None
    if len(versions) > 1:
        return f"MIXED[{'|'.join(versions)}]"
    return versions[0]


def build_forward_outcome_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: ForwardOutcomeSource | str,
    generated_at: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    trust: TrustEnvelope | None = None,
) -> ForwardOutcomeProjection:
    """Project one canonical forward-outcome family into a versioned read model.

    Deterministic and read-only. Duplicate identities are collapsed so a replayed
    append cannot inflate the population; the latest record for an identity is
    kept, matching the producers' append-only revision semantics.
    """
    try:
        resolved_source = ForwardOutcomeSource(source)
    except ValueError as exc:
        raise ValueError(f"unsupported forward-outcome source: {source}") from exc

    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    builder = _RECORD_BUILDERS[resolved_source]
    parsed = [builder(row) for row in rows if isinstance(row, Mapping)]

    latest: dict[str, ForwardOutcomeRecord] = {}
    order: list[str] = []
    duplicates = 0
    for record in parsed:
        if record.identity in latest:
            duplicates += 1
        else:
            order.append(record.identity)
        latest[record.identity] = record
    records = tuple(latest[key] for key in order)

    horizon_ids = sorted(
        {horizon.horizon_id for record in records for horizon in record.horizons}
    )
    completion = _completion(records)

    provenance = {
        ForwardOutcomeSource.PHASE3C: (
            "canonical Phase 3C forward-outcome rows "
            "(app.jobs.build_phase3c_forward_outcomes -> "
            "/app/data/phase3c_forward_outcomes.jsonl)"
        ),
        ForwardOutcomeSource.DISCOVERY: (
            "canonical Discovery V2-01 forward-outcome rows "
            "(app.jobs.build_discovery_forward_outcomes -> "
            "app.opip.discovery.store.FORWARD_OUTCOMES_FILE)"
        ),
    }[resolved_source]

    details: list[str] = []
    if duplicates:
        details.append(
            f"{duplicates} duplicate forward-outcome row(s) ignored by identity"
        )

    resolved_trust = trust or _trust_for(records, completion)

    return ForwardOutcomeProjection(
        source=resolved_source,
        generated_at=moment,
        records=records,
        producer_version=_producer_version(records),
        source_provenance=provenance,
        window_start=window_start,
        window_end=window_end,
        horizon_ids=tuple(horizon_ids),
        duplicate_rows_ignored=duplicates,
        completion=completion,
        trust=resolved_trust,
        details=tuple(details),
    )


def unavailable_forward_outcomes(
    reason: str,
    *,
    source: ForwardOutcomeSource | str,
    generated_at: datetime | None = None,
) -> ForwardOutcomeProjection:
    """The projection for evidence that could not be read.

    Same structure as a healthy projection, explicitly ``UNAVAILABLE``/``UNKNOWN``,
    with no fabricated completion counts.
    """
    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return ForwardOutcomeProjection(
        source=ForwardOutcomeSource(source),
        generated_at=moment.astimezone(timezone.utc),
        records=(),
        trust=unavailable_trust(str(reason)),
        details=(str(reason),),
    )


def read_forward_outcome_projection(
    *,
    source: ForwardOutcomeSource | str,
    path: Any = None,
    generated_at: datetime | None = None,
) -> ForwardOutcomeProjection:
    """Read one canonical forward-outcome family and project it (read-only)."""
    try:
        resolved_source = ForwardOutcomeSource(source)
    except ValueError as exc:
        raise ValueError(f"unsupported forward-outcome source: {source}") from exc

    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    from pathlib import Path

    # The canonical readers return [] for an absent file, so an absent canonical
    # stream must be reported UNAVAILABLE rather than as a healthy empty family.
    if resolved_source is ForwardOutcomeSource.PHASE3C:
        expected = Path(path) if path is not None else _default_phase3c_path()
    else:
        from app.opip.discovery.store import FORWARD_OUTCOMES_FILE

        expected = Path(path) if path is not None else Path(FORWARD_OUTCOMES_FILE)

    if not expected.exists():
        return unavailable_forward_outcomes(
            f"FORWARD_OUTCOME_EVIDENCE_ABSENT:{resolved_source.value}",
            source=resolved_source,
            generated_at=moment,
        )

    try:
        if resolved_source is ForwardOutcomeSource.PHASE3C:
            from app.services.signal_quality_phase3c import read_jsonl

            rows = read_jsonl(expected)
        else:
            from app.opip.discovery.store import read_discovery_forward_outcomes

            rows = read_discovery_forward_outcomes(path=expected)
    except OSError:
        return unavailable_forward_outcomes(
            f"FORWARD_OUTCOME_EVIDENCE_UNREADABLE:{resolved_source.value}",
            source=resolved_source,
            generated_at=moment,
        )

    return build_forward_outcome_projection(
        rows, source=resolved_source, generated_at=moment
    )


def _default_phase3c_path() -> Any:
    from pathlib import Path

    return Path("/app/data/phase3c_forward_outcomes.jsonl")


__all__ = [
    "FORWARD_OUTCOME_AVAILABILITY_SEMANTICS",
    "FORWARD_OUTCOME_POPULATION_SEMANTICS",
    "FORWARD_OUTCOME_PROJECTION_VERSION",
    "FORWARD_OUTCOME_SCHEMA_VERSION",
    "FORWARD_OUTCOME_SCOPE",
    "ForwardOutcomeCompletion",
    "ForwardOutcomeHorizon",
    "ForwardOutcomeProjection",
    "ForwardOutcomeRecord",
    "ForwardOutcomeSource",
    "build_forward_outcome_projection",
    "forward_window_is_point_in_time",
    "horizon_duration_seconds",
    "read_forward_outcome_projection",
    "unavailable_forward_outcomes",
]
