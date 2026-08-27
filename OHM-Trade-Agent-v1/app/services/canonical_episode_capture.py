"""Canonical every-pair episode capture for O'Pip learning evidence.

This module expands the existing P1 shadow outbox from ranked-candidate-only
coverage to one immutable record for every eligible Kraken spot pair observed by
the live full-market scan. It consumes already-fetched in-memory observations;
it never scans the market, performs network I/O, changes ranking, sends alerts,
or mutates trading state.

The producer remains dark by default through P1_SHADOW_OUTBOX_ENABLED and writes
to the same durable JSONL outbox used by the existing P1 evidence worker.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.services.p1_intelligence_contracts import build_live_scan_snapshot
from app.services.p1_shadow_outbox import (
    DEFAULT_DEAD_LETTER_FILE,
    DEFAULT_OUTBOX_FILE,
    _append_jsonl,
    _repair_truncated_tail_before_append,
    p1_shadow_outbox_enabled,
)
from app.services.registry_io import registry_lock


SCHEMA_VERSION = 1
RECORD_TYPE = "CANONICAL_EPISODE_SNAPSHOT"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _hash(prefix: str, value: str, *, length: int = 32) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def canonical_cohort_id(
    observations: Iterable[Any],
    *,
    decision_at: datetime,
) -> str:
    """Return a deterministic scan-cohort identity independent of input order."""
    decision = _require_utc(decision_at)
    symbols = sorted(
        {
            str(getattr(row, "symbol", "") or "").upper()
            for row in observations
            if str(getattr(row, "symbol", "") or "").strip()
        }
    )
    identity = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "decision_at_utc": decision.isoformat(),
            "symbols": symbols,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _hash("COHORT", identity, length=24)


def _candidate_by_symbol(
    candidates: Sequence[Any],
) -> dict[str, tuple[int, Any]]:
    indexed: dict[str, tuple[int, Any]] = {}
    for rank, candidate in enumerate(candidates, start=1):
        symbol = str(getattr(candidate, "symbol", "") or "").upper()
        if symbol and symbol not in indexed:
            indexed[symbol] = (rank, candidate)
    return indexed


def _build_snapshot(
    observation: Any,
    *,
    decision: datetime,
    cohort_id: str,
    cohort_position: int,
    cohort_size: int,
    candidate_index: Mapping[str, tuple[int, Any]],
    signal_quality_enabled: bool,
) -> dict[str, Any]:
    symbol = str(getattr(observation, "symbol", "") or "").upper()
    if not symbol:
        raise ValueError("market observation symbol is required")

    episode_id = _hash(
        "EP",
        f"{SCHEMA_VERSION}|{cohort_id}|{symbol}",
        length=24,
    )
    snapshot_id = _hash(
        "SNAP",
        f"{SCHEMA_VERSION}|{episode_id}",
        length=32,
    )
    ranked = candidate_index.get(symbol)

    if ranked is None:
        candidate_rank = None
        stage = "NOT_SCORED"
        pattern = None
        opportunity_score = None
        explosion_potential_score = None
        tradeability_score = None
        pattern_strength_score = None
        volume_acceleration_score = None
        relative_strength_score = None
        persistence_scans = None
        exhaustion_penalty = None
        exhaustion_band = None
        relative_strength_percentile = None
        signal_quality_universe_size = None
        suppressed = None
        reasons = ["NO_SIGNAL_QUALITY_CANDIDATE"]
        components: Mapping[str, Any] = {}
        decision_status = "NOT_SCORED"
    else:
        candidate_rank, candidate = ranked
        candidate_snapshot = build_live_scan_snapshot(
            candidate,
            decision_at=decision,
            candidate_rank=candidate_rank,
            reference_prices={
                symbol: getattr(observation, "last_price", None),
            },
        )
        candidate_payload = candidate_snapshot.as_dict()
        stage = candidate_payload["stage"]
        pattern = candidate_payload["pattern"]
        opportunity_score = candidate_payload["opportunity_score"]
        explosion_potential_score = candidate_payload["explosion_potential_score"]
        tradeability_score = candidate_payload["tradeability_score"]
        pattern_strength_score = candidate_payload["pattern_strength_score"]
        volume_acceleration_score = candidate_payload["volume_acceleration_score"]
        relative_strength_score = candidate_payload["relative_strength_score"]
        persistence_scans = candidate_payload["persistence_scans"]
        exhaustion_penalty = candidate_payload["exhaustion_penalty"]
        exhaustion_band = candidate_payload["exhaustion_band"]
        relative_strength_percentile = candidate_payload[
            "relative_strength_percentile"
        ]
        signal_quality_universe_size = candidate_payload["universe_size"]
        suppressed = bool(candidate_payload["suppressed"])
        reasons = list(candidate_payload["reasons"])
        components = candidate_payload["components"]
        decision_status = (
            "SCORED_SUPPRESSED" if suppressed else "SCORED_ELIGIBLE"
        )

    payload = {
        "record_type": RECORD_TYPE,
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "episode_id": episode_id,
        "cohort_id": cohort_id,
        "cohort_position": cohort_position,
        "cohort_size": cohort_size,
        "decision_at_utc": decision.isoformat(),
        "symbol": symbol,
        "base_asset": str(
            getattr(observation, "base_asset", "") or ""
        ).upper(),
        "kraken_public_symbol": str(
            getattr(observation, "kraken_public_symbol", "") or ""
        ),
        "reference_price": _finite(getattr(observation, "last_price", None)),
        "last_price": _finite(getattr(observation, "last_price", None)),
        "volume_24h": _finite(getattr(observation, "volume_24h", None)),
        "liquidity_24h_usd_approx": _finite(
            getattr(observation, "notional_24h_usd_approx", None)
        ),
        "high_24h": _finite(getattr(observation, "high_24h", None)),
        "low_24h": _finite(getattr(observation, "low_24h", None)),
        "lift_from_24h_low_pct": _finite(
            getattr(observation, "lift_from_24h_low_pct", None)
        ),
        "distance_from_24h_high_pct": _finite(
            getattr(observation, "distance_from_24h_high_pct", None)
        ),
        "signal_quality_enabled": bool(signal_quality_enabled),
        "decision_status": decision_status,
        "candidate_rank": candidate_rank,
        "signal_quality_universe_size": signal_quality_universe_size,
        "stage": stage,
        "pattern": pattern,
        "opportunity_score": opportunity_score,
        "explosion_potential_score": explosion_potential_score,
        "tradeability_score": tradeability_score,
        "pattern_strength_score": pattern_strength_score,
        "volume_acceleration_score": volume_acceleration_score,
        "relative_strength_score": relative_strength_score,
        "persistence_scans": persistence_scans,
        "exhaustion_penalty": exhaustion_penalty,
        "exhaustion_band": exhaustion_band,
        "relative_strength_percentile": relative_strength_percentile,
        "suppressed": suppressed,
        "reasons": reasons,
        "components": components,
        "source_exchange": "KRAKEN_SPOT",
        "scan_source": "LIVE_FULL_MARKET",
        "measurement_only": True,
        "advisory_only": True,
        "affects_ranking": False,
        "affects_telegram": False,
        "affects_pending_setup": False,
        "trade_authority_changed": False,
        "production_execution_gate_changed": False,
    }
    json.dumps(payload, sort_keys=True, allow_nan=False)
    return payload


def build_canonical_episode_snapshots(
    observations: Iterable[Any],
    *,
    candidates: Iterable[Any] = (),
    decision_at: datetime,
    signal_quality_enabled: bool,
) -> list[dict[str, Any]]:
    """Build one immutable record for every valid observed pair."""
    decision = _require_utc(decision_at)
    observation_rows = list(observations)
    candidate_rows = list(candidates)
    cohort_id = canonical_cohort_id(observation_rows, decision_at=decision)
    cohort_size = len(observation_rows)
    candidate_index = _candidate_by_symbol(candidate_rows)

    return [
        _build_snapshot(
            observation,
            decision=decision,
            cohort_id=cohort_id,
            cohort_position=cohort_position,
            cohort_size=cohort_size,
            candidate_index=candidate_index,
            signal_quality_enabled=signal_quality_enabled,
        )
        for cohort_position, observation in enumerate(
            sorted(
                observation_rows,
                key=lambda row: str(
                    getattr(row, "symbol", "") or ""
                ).upper(),
            ),
            start=1,
        )
    ]


def append_canonical_episode_snapshots(
    observations: Iterable[Any],
    *,
    candidates: Iterable[Any] = (),
    decision_at: datetime,
    signal_quality_enabled: bool,
    path: Path | None = None,
    dead_letter_path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Append every valid pair while isolating row-level failures.

    A malformed observation is dead-lettered and does not suppress other rows in
    the same cohort. The cohort_size remains the number of pairs presented by
    the live scanner, so downstream coverage checks detect any missing row.
    """
    active = p1_shadow_outbox_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0

    try:
        decision = _require_utc(decision_at)
        observation_rows = list(observations)
        candidate_rows = list(candidates)
        cohort_id = canonical_cohort_id(observation_rows, decision_at=decision)
        cohort_size = len(observation_rows)
        candidate_index = _candidate_by_symbol(candidate_rows)
    except Exception as exc:
        dead_letter = dead_letter_path or DEFAULT_DEAD_LETTER_FILE
        try:
            _append_jsonl(
                dead_letter,
                {
                    "dead_letter_source": "CANONICAL_EPISODE_PRODUCER",
                    "decision_at_utc": (
                        decision_at.isoformat()
                        if isinstance(decision_at, datetime)
                        else str(decision_at)
                    ),
                    "error_type": type(exc).__name__,
                    "measurement_only": True,
                    "affects_live_decisions": False,
                },
            )
        except Exception:
            pass
        return 0

    if not observation_rows:
        return 0

    target = path or DEFAULT_OUTBOX_FILE
    dead_letter = dead_letter_path or DEFAULT_DEAD_LETTER_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    written = 0

    ordered = sorted(
        observation_rows,
        key=lambda row: str(getattr(row, "symbol", "") or "").upper(),
    )

    try:
        with registry_lock(lock):
            _repair_truncated_tail_before_append(target)
            with target.open("a", encoding="utf-8") as handle:
                for cohort_position, observation in enumerate(ordered, start=1):
                    try:
                        row = _build_snapshot(
                            observation,
                            decision=decision,
                            cohort_id=cohort_id,
                            cohort_position=cohort_position,
                            cohort_size=cohort_size,
                            candidate_index=candidate_index,
                            signal_quality_enabled=signal_quality_enabled,
                        )
                        handle.write(
                            json.dumps(row, sort_keys=True, allow_nan=False)
                            + "\n"
                        )
                        written += 1
                    except Exception as exc:
                        _append_jsonl(
                            dead_letter,
                            {
                                "dead_letter_source": "CANONICAL_EPISODE_PRODUCER",
                                "cohort_id": cohort_id,
                                "cohort_position": cohort_position,
                                "cohort_size": cohort_size,
                                "symbol": str(
                                    getattr(observation, "symbol", "") or ""
                                ).upper(),
                                "decision_at_utc": decision.isoformat(),
                                "error_type": type(exc).__name__,
                                "measurement_only": True,
                                "affects_live_decisions": False,
                            },
                        )
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        return written
    except Exception:
        return 0
