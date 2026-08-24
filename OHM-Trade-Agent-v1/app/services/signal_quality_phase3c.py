"""Phase 3C — Verified Edge offline validation harness.

This module is deliberately offline. It never imports Telegram, PendingSetup,
private Kraken execution, or production configuration writers. It evaluates
already-recorded point-in-time snapshots against already-computed forward
outcomes and reports evidence; it cannot promote a feature automatically.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Iterable, Mapping, Sequence


HORIZONS = ("15m", "30m", "60m", "4h")
MIN_BUCKET_EPISODES = 30
MIN_HOLDOUT_EPISODES = 100
BOOTSTRAP_RESAMPLES = 1000


def _parse_utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Phase3CRow:
    episode_id: str
    snapshot_id: str
    symbol: str
    decision_at_utc: datetime
    candidate_rank: int
    reference_price: float | None
    liquidity_24h_usd_approx: float
    stage: str
    opportunity_score: int
    tradeability_score: int
    explosion_potential_score: int
    persistence_scans: int
    exhaustion_penalty: int
    suppressed: bool
    structure_status: str | None = None
    structure_bias: str | None = None
    retest_state: str | None = None
    chase_risk_score: int | None = None
    chase_risk_band: str | None = None
    return_15m_pct: float | None = None
    return_30m_pct: float | None = None
    return_60m_pct: float | None = None
    return_4h_pct: float | None = None
    mfe_24h_pct: float | None = None
    mae_24h_pct: float | None = None
    window_complete: bool = False
    top8_structure_cohort: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision_at_utc"] = self.decision_at_utc.isoformat()
        return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _key(symbol: str, at: str | datetime) -> tuple[str, str]:
    return str(symbol).upper(), _parse_utc(at).isoformat()


def join_point_in_time_evidence(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    phase3b_rows: Iterable[Mapping[str, Any]] = (),
    outcomes: Iterable[Mapping[str, Any]] = (),
    episode_ids: Mapping[tuple[str, str], str] | None = None,
) -> list[Phase3CRow]:
    """Join evidence strictly on canonical symbol + original decision time.

    `outcomes` are offline labels and are never copied into any live feature
    contract. A missing episode id is marked ``UNASSIGNED`` and is excluded
    from episode-level statistics; Phase 3C never treats repeated snapshots as
    independent episodes merely because episode mapping is unavailable.
    """
    structure_by_key = {
        _key(row.get("symbol", ""), row.get("recorded_at", row.get("decision_at_utc", ""))): row
        for row in phase3b_rows
        if row.get("symbol") and (row.get("recorded_at") or row.get("decision_at_utc"))
    }
    outcome_by_key = {
        _key(row.get("symbol", ""), row.get("reference_at", row.get("decision_at_utc", ""))): row
        for row in outcomes
        if row.get("symbol") and (row.get("reference_at") or row.get("decision_at_utc"))
    }

    joined: list[Phase3CRow] = []
    for snapshot in snapshots:
        symbol = str(snapshot.get("symbol", "") or "").upper()
        at_raw = snapshot.get("decision_at_utc")
        snapshot_id = str(snapshot.get("snapshot_id", "") or "")
        if not symbol or not at_raw or not snapshot_id:
            continue
        at = _parse_utc(at_raw)
        join_key = (symbol, at.isoformat())
        structure = structure_by_key.get(join_key, {})
        outcome = outcome_by_key.get(join_key, {})

        horizon = outcome.get("horizon_returns_pct", {})
        if not isinstance(horizon, Mapping):
            horizon = {}

        episode_id = (
            (episode_ids or {}).get(join_key)
            or str(outcome.get("episode_id", "") or "")
            or f"UNASSIGNED:{snapshot_id}"
        )

        joined.append(
            Phase3CRow(
                episode_id=episode_id,
                snapshot_id=snapshot_id,
                symbol=symbol,
                decision_at_utc=at,
                candidate_rank=int(snapshot.get("candidate_rank", 0) or 0),
                reference_price=_finite(snapshot.get("reference_price")),
                liquidity_24h_usd_approx=float(
                    snapshot.get("liquidity_24h_usd_approx", 0.0) or 0.0
                ),
                stage=str(snapshot.get("stage", "") or ""),
                opportunity_score=int(snapshot.get("opportunity_score", 0) or 0),
                tradeability_score=int(snapshot.get("tradeability_score", 0) or 0),
                explosion_potential_score=int(
                    snapshot.get("explosion_potential_score", 0) or 0
                ),
                persistence_scans=int(snapshot.get("persistence_scans", 0) or 0),
                exhaustion_penalty=int(snapshot.get("exhaustion_penalty", 0) or 0),
                suppressed=bool(snapshot.get("suppressed", False)),
                structure_status=structure.get("structure_status"),
                structure_bias=structure.get("structure_bias"),
                retest_state=structure.get("retest_state"),
                chase_risk_score=(
                    int(structure["chase_risk_score"])
                    if structure.get("chase_risk_score") is not None
                    else None
                ),
                chase_risk_band=structure.get("chase_risk_band"),
                return_15m_pct=_finite(horizon.get("15m", outcome.get("return_15m_pct"))),
                return_30m_pct=_finite(horizon.get("30m", outcome.get("return_30m_pct"))),
                return_60m_pct=_finite(horizon.get("60m", outcome.get("return_60m_pct"))),
                return_4h_pct=_finite(horizon.get("4h", outcome.get("return_4h_pct"))),
                mfe_24h_pct=_finite(outcome.get("mfe_pct", outcome.get("mfe_24h_pct"))),
                mae_24h_pct=_finite(
                    outcome.get(
                        "max_adverse_excursion_pct",
                        outcome.get("mae_24h_pct", outcome.get("mae_pct")),
                    )
                ),
                window_complete=bool(outcome.get("window_complete", False)),
                top8_structure_cohort=bool(
                    structure
                    and int(snapshot.get("candidate_rank", 0) or 0) in range(1, 9)
                ),
            )
        )
    return sorted(joined, key=lambda row: (row.decision_at_utc, row.symbol, row.candidate_rank))


def deduplicate_first_per_episode(rows: Sequence[Phase3CRow]) -> list[Phase3CRow]:
    """Return the earliest decision per independently mapped episode.

    Rows without a Phase-2/forward-outcome episode identity are deliberately
    excluded rather than silently counted as independent observations.
    """
    first: dict[str, Phase3CRow] = {}
    for row in sorted(rows, key=lambda item: (item.decision_at_utc, item.symbol)):
        if not row.episode_id or row.episode_id.startswith("UNASSIGNED:"):
            continue
        current = first.get(row.episode_id)
        if current is None or row.decision_at_utc < current.decision_at_utc:
            first[row.episode_id] = row
    return sorted(first.values(), key=lambda item: (item.decision_at_utc, item.episode_id))


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[Phase3CRow, ...]
    validation: tuple[Phase3CRow, ...]
    test: tuple[Phase3CRow, ...]

    def as_dict(self) -> dict[str, int]:
        return {
            "train_episodes": len(self.train),
            "validation_episodes": len(self.validation),
            "test_episodes": len(self.test),
        }


def chronological_split(
    rows: Sequence[Phase3CRow],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> ChronologicalSplit:
    if train_fraction <= 0 or validation_fraction < 0:
        raise ValueError("invalid split fractions")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("test fraction must be positive")

    episodes = deduplicate_first_per_episode(rows)
    total = len(episodes)
    train_end = int(total * train_fraction)
    validation_end = train_end + int(total * validation_fraction)
    return ChronologicalSplit(
        train=tuple(episodes[:train_end]),
        validation=tuple(episodes[train_end:validation_end]),
        test=tuple(episodes[validation_end:]),
    )


def bootstrap_mean_ci(
    values: Iterable[float | None],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None}
    if resamples < 1:
        raise ValueError("resamples must be >= 1")

    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample = [clean[rng.randrange(len(clean))] for _ in range(len(clean))]
        means.append(statistics.fmean(sample))
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    low_index = min(len(means) - 1, max(0, int(alpha * len(means))))
    high_index = min(
        len(means) - 1,
        max(0, int((1.0 - alpha) * len(means)) - 1),
    )
    return {
        "n": len(clean),
        "mean": statistics.fmean(clean),
        "ci_low": means[low_index],
        "ci_high": means[high_index],
    }


def _liquidity_bucket(row: Phase3CRow) -> str:
    value = row.liquidity_24h_usd_approx
    if value < 500_000:
        return "LT_500K"
    if value <= 2_000_000:
        return "500K_TO_2M"
    return "GT_2M"


def _rank_bucket(row: Phase3CRow) -> str:
    rank = row.candidate_rank
    if 1 <= rank <= 8:
        return "TOP_8"
    if 9 <= rank <= 25:
        return "RANK_9_25"
    return "RANK_26_PLUS_OR_UNKNOWN"


def _summarize_rows(
    rows: Sequence[Phase3CRow],
    *,
    min_episodes: int,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    unique = deduplicate_first_per_episode(rows)
    count = len(unique)
    status = "SUFFICIENT_SAMPLE" if count >= min_episodes else "INSUFFICIENT_SAMPLE"
    observed_4h = [row for row in unique if row.return_4h_pct is not None]
    return {
        "status": status,
        "episodes": count,
        "returns": {
            "15m": bootstrap_mean_ci(
                (row.return_15m_pct for row in unique), resamples=resamples, seed=seed + 15
            ),
            "30m": bootstrap_mean_ci(
                (row.return_30m_pct for row in unique), resamples=resamples, seed=seed + 30
            ),
            "60m": bootstrap_mean_ci(
                (row.return_60m_pct for row in unique), resamples=resamples, seed=seed + 60
            ),
            "4h": bootstrap_mean_ci(
                (row.return_4h_pct for row in unique), resamples=resamples, seed=seed + 240
            ),
        },
        "mfe_24h": bootstrap_mean_ci(
            (row.mfe_24h_pct for row in unique), resamples=resamples, seed=seed + 241
        ),
        "mae_24h": bootstrap_mean_ci(
            (row.mae_24h_pct for row in unique), resamples=resamples, seed=seed + 242
        ),
        "win_rate_4h": (
            sum(1 for row in observed_4h if row.return_4h_pct > 0) / len(observed_4h)
            if observed_4h
            else None
        ),
    }


def _bucket_report(
    rows: Sequence[Phase3CRow],
    *,
    key_fn,
    min_episodes: int,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    groups: dict[str, list[Phase3CRow]] = {}
    for row in rows:
        key = str(key_fn(row) or "UNAVAILABLE")
        groups.setdefault(key, []).append(row)
    return {
        key: _summarize_rows(
            group,
            min_episodes=min_episodes,
            resamples=resamples,
            seed=seed + index * 997,
        )
        for index, (key, group) in enumerate(sorted(groups.items()))
    }


def build_phase3c_report(
    rows: Sequence[Phase3CRow],
    *,
    min_bucket_episodes: int = MIN_BUCKET_EPISODES,
    min_holdout_episodes: int = MIN_HOLDOUT_EPISODES,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = 42,
) -> dict[str, Any]:
    unassigned_snapshot_rows = sum(
        1 for row in rows
        if not row.episode_id or row.episode_id.startswith("UNASSIGNED:")
    )
    episodes = deduplicate_first_per_episode(rows)
    split = chronological_split(episodes)
    test_count = len(split.test)
    test_primary_outcomes = sum(
        1 for row in split.test if row.return_4h_pct is not None
    )

    required_baseline = [
        row
        for row in episodes
        if row.symbol
        and row.candidate_rank >= 1
        and row.reference_price is not None
        and row.stage
    ]
    baseline_completeness = len(required_baseline) / len(episodes) if episodes else 0.0
    complete_outcomes = sum(1 for row in episodes if row.window_complete)
    top8_count = sum(1 for row in episodes if row.top8_structure_cohort)

    gate0_ready = bool(
        episodes
        and baseline_completeness >= 0.95
        and test_count >= min_holdout_episodes
        and test_primary_outcomes >= min_holdout_episodes
    )

    return {
        "phase": "3C_VERIFIED_EDGE",
        "status": "GATE0_READY_FOR_EVIDENCE_REVIEW" if gate0_ready else "BUILDING_EVIDENCE",
        "provisional": True,
        "score_is_probability": False,
        "auto_promotion_allowed": False,
        "trade_authority_changed": False,
        "production_execution_gate_changed": False,
        "episodes": len(episodes),
        "split": split.as_dict(),
        "data_quality": {
            "baseline_completeness": baseline_completeness,
            "unassigned_snapshot_rows": unassigned_snapshot_rows,
            "test_primary_outcome_episodes": test_primary_outcomes,
            "complete_24h_outcomes": complete_outcomes,
            "top8_structure_cohort_episodes": top8_count,
            "top8_structure_cohort_fraction": top8_count / len(episodes) if episodes else 0.0,
            "selection_bias_note": (
                "Phase 3B live structure is top-8 non-suppressed cohort data; "
                "population conclusions must be rank-stratified."
            ),
            "minimum_bucket_episodes": min_bucket_episodes,
            "minimum_holdout_episodes": min_holdout_episodes,
            "bootstrap_resamples": bootstrap_resamples,
        },
        "overall": _summarize_rows(
            episodes,
            min_episodes=min_bucket_episodes,
            resamples=bootstrap_resamples,
            seed=seed,
        ),
        "buckets": {
            "liquidity": _bucket_report(
                episodes,
                key_fn=_liquidity_bucket,
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 1000,
            ),
            "rank": _bucket_report(
                episodes,
                key_fn=_rank_bucket,
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 2000,
            ),
            "structure_bias": _bucket_report(
                episodes,
                key_fn=lambda row: row.structure_bias or "UNAVAILABLE",
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 3000,
            ),
            "retest_state": _bucket_report(
                episodes,
                key_fn=lambda row: row.retest_state or "UNAVAILABLE",
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 4000,
            ),
            "chase_risk_band": _bucket_report(
                episodes,
                key_fn=lambda row: row.chase_risk_band or "UNAVAILABLE",
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 5000,
            ),
            "stage": _bucket_report(
                episodes,
                key_fn=lambda row: row.stage,
                min_episodes=min_bucket_episodes,
                resamples=bootstrap_resamples,
                seed=seed + 6000,
            ),
        },
        "promotion_gate": {
            "gate0_ready": gate0_ready,
            "gate1_feature_validation_performed": False,
            "validated_for_shadow": [],
            "note": (
                "This report cannot promote a feature. Gate 1 requires a predeclared "
                "baseline comparison, ablation, uncertainty and economic-significance review."
            ),
        },
    }
