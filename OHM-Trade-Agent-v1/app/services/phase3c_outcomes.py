"""Offline forward-outcome labels for Phase 3C.

Two independent episode concepts are kept explicit:

* ``signal_episode_id`` groups one contiguous run of non-suppressed OHM
  decisions for a symbol. This is the primary unit for signal-conditioned edge
  and false-positive analysis, so repeated scans of the same setup are not
  pseudo-replicated.
* ``move_episode_id`` maps the decision into Phase 2's distinct major-move
  ``MoveEpisode`` model when one exists. This supports movement-conditioned
  timing/capture analysis without pretending every signal was followed by a
  +20% move.

Forward prices are labels only. This module is offline and has no live import
path into scoring, Telegram, PendingSetup, or execution.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable, Mapping, Sequence

from app.services.signal_quality_phase2 import (
    EpisodeConfig,
    MoveEpisode,
    ReplayObservation,
    build_all_episodes,
    build_timelines,
)
from app.services.signal_timing_v2 import compute_forward_outcome


DEFAULT_CONTINUITY_SECONDS = 1500.0  # 600s nominal scan * 2.5 continuity multiplier


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


def _signal_episode_id(symbol: str, started_at: datetime) -> str:
    raw = f"{symbol.upper()}|{started_at.astimezone(timezone.utc).isoformat()}"
    return "SIG:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _move_episode_id(episode: MoveEpisode) -> str:
    raw = (
        f"{episode.symbol.upper()}|{episode.baseline_at.astimezone(timezone.utc).isoformat()}|"
        f"{episode.peak_at.astimezone(timezone.utc).isoformat()}"
    )
    return "MOVE:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def assign_signal_episode_ids(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    continuity_seconds: float = DEFAULT_CONTINUITY_SECONDS,
) -> dict[str, str]:
    """Map non-suppressed snapshot_id -> deterministic contiguous signal episode.

    An explicit suppressed row ends an active signal episode. Canonical Build 2
    pair-per-scan episode IDs are stored on snapshots separately and do not
    depend on this signal-run grouping. A gap longer than the continuity budget
    also starts a new signal episode, so a missing scan cannot bridge two
    setups indefinitely.
    """
    if continuity_seconds <= 0:
        raise ValueError("continuity_seconds must be > 0")

    by_symbol: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = defaultdict(list)
    for snapshot in snapshots:
        symbol = str(snapshot.get("symbol", "") or "").upper()
        at = _parse_utc(snapshot.get("decision_at_utc"))
        snapshot_id = str(snapshot.get("snapshot_id", "") or "")
        if symbol and at is not None and snapshot_id:
            by_symbol[symbol].append((at, snapshot))

    assigned: dict[str, str] = {}
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda item: item[0])
        active_id: str | None = None
        prior_at: datetime | None = None
        for at, snapshot in rows:
            stage = str(snapshot.get("stage", "") or "").upper()
            decision_status = str(
                snapshot.get("decision_status", "") or ""
            ).upper()
            suppressed = bool(snapshot.get("suppressed", False)) or stage == "SUPPRESSED"
            not_scored = stage == "NOT_SCORED" or decision_status == "NOT_SCORED"
            if suppressed or not_scored:
                active_id = None
                prior_at = at
                continue

            gap = (at - prior_at).total_seconds() if prior_at is not None else None
            if active_id is None or gap is None or gap > continuity_seconds:
                active_id = _signal_episode_id(symbol, at)
            assigned[str(snapshot["snapshot_id"])] = active_id
            prior_at = at
    return assigned


def map_move_episodes(
    snapshots: Sequence[Mapping[str, Any]],
    episodes: Sequence[MoveEpisode],
) -> dict[str, str]:
    """Map snapshot_id into the Phase 2 major-move episode active at decision time."""
    by_symbol: dict[str, list[MoveEpisode]] = defaultdict(list)
    for episode in episodes:
        by_symbol[episode.symbol.upper()].append(episode)
    for rows in by_symbol.values():
        rows.sort(key=lambda row: row.baseline_at)

    mapped: dict[str, str] = {}
    for snapshot in snapshots:
        snapshot_id = str(snapshot.get("snapshot_id", "") or "")
        symbol = str(snapshot.get("symbol", "") or "").upper()
        at = _parse_utc(snapshot.get("decision_at_utc"))
        if not snapshot_id or not symbol or at is None:
            continue
        for episode in by_symbol.get(symbol, ()):
            if episode.baseline_at <= at <= episode.end_at:
                mapped[snapshot_id] = _move_episode_id(episode)
                break
    return mapped


def build_forward_outcome_labels(
    snapshots: Sequence[Mapping[str, Any]],
    observations: Iterable[ReplayObservation],
    *,
    continuity_seconds: float = DEFAULT_CONTINUITY_SECONDS,
    episode_config: EpisodeConfig | None = None,
) -> list[dict[str, Any]]:
    """Build point-in-time forward labels for every snapshot with usable history.

    Event-sampled observation timelines are explicitly marked provisional. The
    labeler never alters a snapshot and never feeds its output into live code.
    """
    observation_rows = list(observations)
    timelines = build_timelines(observation_rows)
    episodes = build_all_episodes(
        timelines,
        config=episode_config or EpisodeConfig(),
    )
    signal_ids = assign_signal_episode_ids(
        snapshots,
        continuity_seconds=continuity_seconds,
    )
    move_ids = map_move_episodes(snapshots, episodes)

    labels: list[dict[str, Any]] = []
    for snapshot in snapshots:
        snapshot_id = str(snapshot.get("snapshot_id", "") or "")
        symbol = str(snapshot.get("symbol", "") or "").upper()
        at = _parse_utc(snapshot.get("decision_at_utc"))
        try:
            reference_price = float(snapshot.get("reference_price"))
        except (TypeError, ValueError):
            reference_price = 0.0
        if not snapshot_id or not symbol or at is None or reference_price <= 0:
            continue

        timeline = timelines.get(symbol)
        outcome = (
            compute_forward_outcome(
                timeline,
                reference_at=at,
                reference_price=reference_price,
            )
            if timeline is not None and len(timeline) > 0
            else None
        )
        canonical_episode_id = str(snapshot.get("episode_id", "") or "") or None
        signal_episode_id = signal_ids.get(snapshot_id)
        payload: dict[str, Any] = {
            "label_schema_version": 1,
            "snapshot_id": snapshot_id,
            "symbol": symbol,
            "reference_at": at.isoformat(),
            "reference_price": reference_price,
            # Build 2 canonical pair-per-scan identity is authoritative when
            # present. Legacy candidate snapshots retain their contiguous
            # signal episode identity for backward-compatible analysis.
            "episode_id": canonical_episode_id or signal_episode_id,
            "canonical_episode_id": canonical_episode_id,
            "signal_episode_id": signal_episode_id,
            "move_episode_id": move_ids.get(snapshot_id),
            "within_major_move_episode": snapshot_id in move_ids,
            "outcome_source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
            "measurement_only": True,
            "offline_label_only": True,
            "affects_ranking": False,
            "affects_telegram": False,
            "trade_authority_changed": False,
            "production_execution_gate_changed": False,
        }
        if outcome is None:
            payload.update(
                {
                    "horizon_returns_pct": {},
                    "horizon_observed": {},
                    "mfe_pct": None,
                    "mfe_at": None,
                    "time_to_mfe_seconds": None,
                    "mae_pct": None,
                    "mae_at": None,
                    "time_to_mae_seconds": None,
                    "max_adverse_excursion_pct": None,
                    "window_complete": False,
                    "maturation_status": "NO_FORWARD_DATA",
                }
            )
        else:
            payload.update(outcome.as_dict())
            payload["maturation_status"] = (
                "MATURE_24H"
                if bool(payload.get("window_complete"))
                else "PARTIAL_FORWARD_WINDOW"
            )
            # Keep immutable identity from the snapshot as the authoritative
            # reference even though ForwardOutcome repeats these fields.
            payload["reference_at"] = at.isoformat()
            payload["reference_price"] = reference_price
        labels.append(payload)

    return sorted(
        labels,
        key=lambda row: (row["reference_at"], row["symbol"], row["snapshot_id"]),
    )
