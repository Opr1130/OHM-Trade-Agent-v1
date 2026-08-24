from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

from app.services.signal_features import (
    FeatureDerivationConfig,
    ObservationSnapshot,
    QualifyingConditions,
    derive_features_for_universe,
    derive_universe_percentiles,
    snapshot_from_mapping,
)
from app.services.signal_scoring import (
    STAGE_ACTIONABLE_REVIEW,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_EARLY_BUILDING,
    SignalQualityConfig,
    evaluate_universe,
)

VERSION = "signal-quality-phase2-replay-v1"
DEFAULT_OBSERVATION_FILE = Path("/app/data/full_market_observations.jsonl")
DEFAULT_SCAN_INTERVAL_SECONDS = 600
DEFAULT_HISTORY_SCANS = 8
DEFAULT_MAX_CARRY_SECONDS = 3600
DEFAULT_OUTCOME_HORIZON_HOURS = 24
OUTCOME_THRESHOLDS = (20.0, 50.0, 100.0, 200.0, 300.0)
STAGE_ORDER = {
    STAGE_ACTIONABLE_REVIEW: 3,
    STAGE_BREAKOUT_CANDIDATE: 2,
    STAGE_EARLY_BUILDING: 1,
}


@dataclass(frozen=True)
class ReplayObservation:
    observed_at: datetime
    symbol: str
    snapshot: ObservationSnapshot


@dataclass(frozen=True)
class ReplayDetection:
    observed_at: datetime
    symbol: str
    stage: str
    reference_price: float
    opportunity_score: int
    explosion_potential_score: int
    tradeability_score: int
    pattern_strength_score: int
    persistence_scans: int
    exhaustion_penalty: int

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


@dataclass(frozen=True)
class OutcomeLabel:
    symbol: str
    start_at: datetime
    reference_price: float
    max_future_price: float
    max_move_pct: float
    outcome_class: str
    event_sampled_proxy: bool = True

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_at"] = self.start_at.isoformat()
        return payload


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _floor_time(moment: datetime, interval_seconds: int) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % interval_seconds, tz=timezone.utc)


def read_observations(path: Path | None = None) -> list[ReplayObservation]:
    target = path or DEFAULT_OBSERVATION_FILE
    if not target.exists():
        return []
    rows: list[ReplayObservation] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("record_type") != "FULL_MARKET_OBSERVATION":
                continue
            observed_at = _as_utc(raw.get("observed_at"))
            symbol = str(raw.get("symbol") or "").upper()
            snapshot = snapshot_from_mapping(raw, observed_at=observed_at)
            if observed_at is None or not symbol or snapshot is None:
                continue
            rows.append(ReplayObservation(observed_at=observed_at, symbol=symbol, snapshot=snapshot))
    rows.sort(key=lambda row: (row.observed_at, row.symbol))
    return rows


def reconstruct_scan_frames(
    observations: Iterable[ReplayObservation],
    *,
    interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
    max_carry_seconds: int = DEFAULT_MAX_CARRY_SECONDS,
) -> list[tuple[datetime, dict[str, ObservationSnapshot]]]:
    """Reconstruct a regular grid without replaying each event as a scan.

    The persisted JSONL stream is event-sampled: active periods have many rows,
    quiet periods mostly heartbeats. Treating every row as a scan would
    overweight volatility. Fixed buckets neutralise that bias. Only events at
    or before the frame boundary are visible; future rows are never consulted.
    """
    rows = sorted(observations, key=lambda row: (row.observed_at, row.symbol))
    if not rows:
        return []
    interval_seconds = max(1, int(interval_seconds))
    start = _floor_time(rows[0].observed_at, interval_seconds)
    end = _floor_time(rows[-1].observed_at, interval_seconds)
    frames: list[tuple[datetime, dict[str, ObservationSnapshot]]] = []
    latest: dict[str, ReplayObservation] = {}
    index = 0
    cursor = start
    while cursor <= end:
        frame_end = cursor + timedelta(seconds=interval_seconds)
        while index < len(rows) and rows[index].observed_at < frame_end:
            latest[rows[index].symbol] = rows[index]
            index += 1
        active: dict[str, ObservationSnapshot] = {}
        for symbol, row in latest.items():
            age = (frame_end - row.observed_at).total_seconds()
            if 0 <= age <= max_carry_seconds:
                active[symbol] = row.snapshot
        frames.append((frame_end, active))
        cursor = frame_end
    return frames


def replay_signal_quality(
    frames: Iterable[tuple[datetime, Mapping[str, ObservationSnapshot]]],
    *,
    history_scans: int = DEFAULT_HISTORY_SCANS,
    config: SignalQualityConfig | None = None,
    interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
) -> list[ReplayDetection]:
    config = config or SignalQualityConfig(enabled=True, early_alerts_enabled=True)
    feature_config = FeatureDerivationConfig(
        nominal_interval_seconds=float(interval_seconds),
        continuity_multiplier=2.5,
        qualifying=QualifyingConditions(),
        run_up_window_scans=history_scans,
    )
    history: dict[str, deque[ObservationSnapshot]] = defaultdict(lambda: deque(maxlen=history_scans))
    last_appended: dict[str, datetime] = {}
    detections: list[ReplayDetection] = []
    for scan_at, frame in frames:
        for symbol, snapshot in frame.items():
            # Carry-forward is for universe reconstruction only. Re-appending a
            # stale persisted row would manufacture runtime persistence.
            if last_appended.get(symbol) == snapshot.observed_at:
                continue
            history[symbol].append(snapshot)
            last_appended[symbol] = snapshot.observed_at
        universe = {symbol: list(rows) for symbol, rows in history.items() if symbol in frame}
        features = derive_features_for_universe(universe, config=feature_config)
        if not features:
            continue
        percentiles = derive_universe_percentiles(features)
        for candidate in evaluate_universe(features, percentiles, config=config):
            if candidate.stage not in STAGE_ORDER:
                continue
            snapshot = frame.get(candidate.symbol)
            if snapshot is None or snapshot.last_price <= 0:
                continue
            detections.append(ReplayDetection(
                observed_at=scan_at,
                symbol=candidate.symbol,
                stage=candidate.stage,
                reference_price=snapshot.last_price,
                opportunity_score=candidate.opportunity_score,
                explosion_potential_score=candidate.explosion_potential_score,
                tradeability_score=candidate.tradeability_score,
                pattern_strength_score=candidate.pattern_strength_score,
                persistence_scans=candidate.persistence_scans,
                exhaustion_penalty=candidate.exhaustion_penalty,
            ))
    return detections


def _outcome_class(move_pct: float) -> str:
    if move_pct >= 300:
        return "MOVE_300_PLUS"
    if move_pct >= 200:
        return "MOVE_200"
    if move_pct >= 100:
        return "MOVE_100"
    if move_pct >= 50:
        return "MOVE_50"
    if move_pct >= 20:
        return "MOVE_20"
    return "NO_MAJOR_MOVE"


def label_outcomes(
    observations: Iterable[ReplayObservation],
    *,
    horizon_hours: int = DEFAULT_OUTCOME_HORIZON_HOURS,
) -> list[OutcomeLabel]:
    """Label future moves using only later persisted prices.

    This is intentionally conservative. Event sampling can miss an intraperiod
    high, so a major mover may be under-labelled; the harness reports that
    limitation and must not auto-tune production from these labels.
    """
    by_symbol: dict[str, list[ReplayObservation]] = defaultdict(list)
    for row in observations:
        by_symbol[row.symbol].append(row)
    labels: list[OutcomeLabel] = []
    horizon = timedelta(hours=horizon_hours)
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda row: row.observed_at)
        for index, row in enumerate(rows):
            reference = row.snapshot.last_price
            if not math.isfinite(reference) or reference <= 0:
                continue
            end = row.observed_at + horizon
            future_prices = [
                item.snapshot.last_price
                for item in rows[index + 1:]
                if row.observed_at < item.observed_at <= end and item.snapshot.last_price > 0
            ]
            if not future_prices:
                continue
            highest = max(future_prices)
            move = (highest / reference - 1.0) * 100.0
            labels.append(OutcomeLabel(
                symbol=symbol,
                start_at=row.observed_at,
                reference_price=reference,
                max_future_price=highest,
                max_move_pct=round(move, 6),
                outcome_class=_outcome_class(move),
            ))
    return labels


def _first_detection_after(
    detections: list[ReplayDetection],
    *,
    symbol: str,
    start_at: datetime,
    end_at: datetime,
) -> ReplayDetection | None:
    matches = [row for row in detections if row.symbol == symbol and start_at <= row.observed_at <= end_at]
    return min(matches, key=lambda row: row.observed_at) if matches else None


def build_phase2_report(
    observations: Iterable[ReplayObservation],
    detections: Iterable[ReplayDetection],
    labels: Iterable[OutcomeLabel],
    *,
    horizon_hours: int = DEFAULT_OUTCOME_HORIZON_HOURS,
) -> dict[str, Any]:
    observations = list(observations)
    detections = list(detections)
    labels = list(labels)
    horizon = timedelta(hours=horizon_hours)
    class_counts = Counter(label.outcome_class for label in labels)

    class_metrics: dict[str, dict[str, Any]] = {}
    for threshold in OUTCOME_THRESHOLDS:
        eligible = [label for label in labels if label.max_move_pct >= threshold]
        detected = 0
        before_5 = 0
        before_10 = 0
        before_20 = 0
        lead_minutes: list[float] = []
        move_completed_pct: list[float] = []
        for label in eligible:
            detection = _first_detection_after(
                detections,
                symbol=label.symbol,
                start_at=label.start_at,
                end_at=label.start_at + horizon,
            )
            if detection is None:
                continue
            detected += 1
            lead_minutes.append((detection.observed_at - label.start_at).total_seconds() / 60.0)
            detection_move = (detection.reference_price / label.reference_price - 1.0) * 100.0
            fraction = max(0.0, detection_move) / label.max_move_pct if label.max_move_pct > 0 else 0.0
            move_completed_pct.append(fraction * 100.0)
            before_5 += detection_move <= 5.0
            before_10 += detection_move <= 10.0
            before_20 += detection_move <= 20.0
        key = "MOVE_300_PLUS" if threshold >= 300 else f"MOVE_{int(threshold)}"
        class_metrics[key] = {
            "eligible_windows": len(eligible),
            "detected_windows": detected,
            "early_capture_rate_pct": round(detected / len(eligible) * 100.0, 2) if eligible else None,
            "detected_before_plus_5_pct": before_5,
            "detected_before_plus_10_pct": before_10,
            "detected_before_plus_20_pct": before_20,
            "median_lead_minutes_from_window_start": round(median(lead_minutes), 2) if lead_minutes else None,
            "median_total_move_completed_at_first_detection_pct": round(median(move_completed_pct), 2) if move_completed_pct else None,
        }

    matched_flags: list[bool] = []
    for detection in detections:
        matched_flags.append(any(
            label.symbol == detection.symbol
            and label.start_at <= detection.observed_at <= label.start_at + horizon
            and label.max_move_pct >= 20.0
            for label in labels
        ))

    score_buckets: dict[str, dict[str, Any]] = {}
    for low in range(0, 101, 10):
        rows = [
            good for detection, good in zip(detections, matched_flags)
            if low <= detection.explosion_potential_score < low + 10
        ]
        if not rows:
            continue
        positives = sum(rows)
        score_buckets[f"{low}-{min(100, low + 9)}"] = {
            "detections": len(rows),
            "major_move_matches": positives,
            "precision_pct": round(positives / len(rows) * 100.0, 2),
        }

    true_positive = sum(matched_flags)
    false_positive = len(matched_flags) - true_positive
    return {
        "version": VERSION,
        "status": "PROVISIONAL_EVENT_SAMPLED_REPLAY",
        "methodology": {
            "scan_reconstruction": "fixed-time grid with past-only bounded carry-forward",
            "no_lookahead": True,
            "outcome_source": "future persisted last_price observations only",
            "outcome_limitation": "event-sampled labels can miss intraperiod peaks; OHLC validation required before adopting calibration",
            "automatic_tuning_applied": False,
            "production_thresholds_changed": False,
            "advisory_only": True,
        },
        "observation_rows": len(observations),
        "detections": len(detections),
        "outcome_windows": len(labels),
        "major_move_windows": sum(label.max_move_pct >= 20.0 for label in labels),
        "outcome_class_counts": dict(sorted(class_counts.items())),
        "major_move_metrics": class_metrics,
        "false_positive_summary": {
            "detection_rows": len(detections),
            "matched_to_plus_20_window": true_positive,
            "unmatched": false_positive,
            "unmatched_rate_pct": round(false_positive / len(detections) * 100.0, 2) if detections else None,
        },
        "explosion_score_precision_buckets": score_buckets,
    }


def run_phase2_replay(
    *,
    observation_file: Path | None = None,
    interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
    history_scans: int = DEFAULT_HISTORY_SCANS,
    max_carry_seconds: int = DEFAULT_MAX_CARRY_SECONDS,
    horizon_hours: int = DEFAULT_OUTCOME_HORIZON_HOURS,
) -> dict[str, Any]:
    observations = read_observations(observation_file)
    frames = reconstruct_scan_frames(
        observations,
        interval_seconds=interval_seconds,
        max_carry_seconds=max_carry_seconds,
    )
    detections = replay_signal_quality(
        frames,
        history_scans=history_scans,
        interval_seconds=interval_seconds,
    )
    labels = label_outcomes(observations, horizon_hours=horizon_hours)
    report = build_phase2_report(observations, detections, labels, horizon_hours=horizon_hours)
    report["scan_frames"] = len(frames)
    return report
