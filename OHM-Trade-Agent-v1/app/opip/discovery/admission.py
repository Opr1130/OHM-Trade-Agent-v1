"""Post-selection admission evidence for Broad Search.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

The production selector is not replaced. This module consumes the shortlist
the selector already returned and the observational callback rows, then
writes reconstructable Stage-0 reasons:

* below directional threshold
* threshold cleared but ranked outside budget/cap
* admitted into the production shortlist
* data unavailable / excluded market

A failure in this module must be swallowed by the scan job so a legitimate
candidate is never dropped.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.opip.decision.screening import (
    ScannerType,
    ScreeningEvaluation,
    ScreeningOutcome,
)
from app.opip.discovery.constants import (
    DISCOVERY_ADMISSION_SCHEMA_VERSION,
    DISCOVERY_FEATURE_SCHEMA_VERSION,
    DISCOVERY_VENUE,
    EXCLUSION_GLOBAL_CAP,
    EXCLUSION_PER_DIRECTION_CAP,
    EXCLUSION_UNDERLYING_DEDUP,
    PENDING_FINALIZATION,
    PRODUCTION_SELECTOR_VERSION,
)
from app.opip.discovery.features import decision_features_from_snapshot
from app.opip.early.point_in_time import assert_point_in_time_safe
from app.opip.early.stage0_evidence import (
    SELECTOR_PRODUCTION_COARSE,
    CoarseRankContext,
    ScoreComponents,
    build_advanced_metadata,
    build_below_threshold_metadata,
    build_coarse_status_metadata,
    build_rank_limit_metadata,
    rank_contexts_for_ranked,
)
from app.opip.identity import (
    InstrumentClass,
    resolve_venue_instrument_identity,
)
from app.scanner.candidates import MAX_CANDIDATES, MIN_TECHNICAL_SCORE
from app.scanner.directional_candidates import MAX_PER_DIRECTION

AssetCanonicalizer = Callable[[str | None], str]
PairSplitter = Callable[[str | None], tuple[str, str] | None]


def _default_canonicalize(asset: str | None) -> str:
    text = str(asset or "").strip().upper()
    return {"XBT": "BTC", "XDG": "DOGE"}.get(text, text)


def _default_split_pair(raw: str | None) -> tuple[str, str] | None:
    value = "".join(ch for ch in str(raw or "").upper() if ch not in " /-_")
    for quote in ("USDT", "USDC", "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    return None


def observation_join_id(
    *,
    scan_id: str,
    scanner_type: str,
    venue_instrument_id: str,
    observed_at: str,
) -> str:
    """Deterministic join key shared by decision rows and offline labels."""
    raw = "|".join(
        (str(scan_id), str(scanner_type), str(venue_instrument_id), str(observed_at))
    )
    return "OBS:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _raw_identifier(snapshot: Any) -> str:
    return str(
        getattr(snapshot, "primary_pair", None) or getattr(snapshot, "symbol", "") or ""
    )


def _identity(
    raw: str,
    observed_at: datetime,
    *,
    canonicalize_asset: AssetCanonicalizer | None = None,
    split_canonical_pair: PairSplitter | None = None,
):
    return resolve_venue_instrument_identity(
        raw,
        canonicalize_asset=canonicalize_asset or _default_canonicalize,
        split_canonical_pair=split_canonical_pair or _default_split_pair,
        resolved_at_utc=observed_at,
    )


def _stronger_direction(long_score: float | None, short_score: float | None) -> str | None:
    long_ok = long_score is not None
    short_ok = short_score is not None
    if not long_ok and not short_ok:
        return None
    if long_ok and not short_ok:
        return "LONG"
    if short_ok and not long_ok:
        return "SHORT"
    if float(long_score) > float(short_score):
        return "LONG"
    if float(short_score) > float(long_score):
        return "SHORT"
    return "LONG"


def _candidate_score(long_score: float | None, short_score: float | None) -> float | None:
    direction = _stronger_direction(long_score, short_score)
    if direction == "SHORT":
        return float(short_score) if short_score is not None else None
    if direction == "LONG":
        return float(long_score) if long_score is not None else None
    return None


def _threshold_passed(
    long_score: float | None,
    short_score: float | None,
    min_score: int,
) -> bool:
    long_ok = long_score is not None and float(long_score) >= min_score
    short_ok = short_score is not None and float(short_score) >= min_score
    return bool(long_ok or short_ok)


def _selected_instrument_ids(
    selected: Sequence[Any],
    *,
    observed_at: datetime,
    canonicalize_asset: AssetCanonicalizer | None = None,
    split_canonical_pair: PairSplitter | None = None,
) -> set[str]:
    ids: set[str] = set()
    for snapshot in selected:
        raw = _raw_identifier(snapshot)
        if not raw:
            continue
        try:
            ids.add(
                _identity(
                    raw,
                    observed_at,
                    canonicalize_asset=canonicalize_asset,
                    split_canonical_pair=split_canonical_pair,
                ).venue_instrument_id
            )
        except Exception:
            symbol = str(getattr(snapshot, "symbol", "") or "").strip().upper()
            if symbol:
                ids.add(symbol)
    return ids


def _production_rank_key(score: float, symbol: str, direction: str) -> tuple:
    """Match ``select_directional_candidates`` ordering. Explanation only."""
    return (-float(score), str(symbol or ""), str(direction or ""))


def reconstruct_selector_exclusion_reasons(
    rows: Sequence[Mapping[str, Any]],
    *,
    selected_ids: set[str],
    min_score: int,
    limit: int,
    max_per_direction: int = MAX_PER_DIRECTION,
) -> dict[str, str]:
    """Replay production selector rules for explanation only.

    Mirrors ``select_directional_candidates``: stronger direction per
    underlying asset, then per-direction cap, then mixed global cap.
    """
    best_by_asset: dict[str, tuple[str, float, str, str]] = {}
    reasons: dict[str, str] = {}
    for row in rows:
        venue_id = str(row.get("venue_instrument_id") or "")
        if not venue_id:
            continue
        long_score = row.get("long_score")
        short_score = row.get("short_score")
        if not _threshold_passed(long_score, short_score, min_score):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        identity = row.get("venue_instrument") if isinstance(row.get("venue_instrument"), Mapping) else {}
        asset = str(
            (metadata or {}).get("canonical_underlying_asset")
            or (identity or {}).get("canonical_asset_id")
            or venue_id
        )
        stronger = _stronger_direction(long_score, short_score) or "LONG"
        score = _candidate_score(long_score, short_score)
        if score is None:
            continue
        sort_symbol = str(
            (identity or {}).get("raw_identifier")
            or venue_id
        )
        previous = best_by_asset.get(asset)
        if previous is None or score > previous[1]:
            if previous is not None:
                reasons[previous[0]] = EXCLUSION_UNDERLYING_DEDUP
            best_by_asset[asset] = (venue_id, float(score), stronger, sort_symbol)
        else:
            reasons[venue_id] = EXCLUSION_UNDERLYING_DEDUP

    ranked = sorted(
        best_by_asset.values(),
        key=lambda item: _production_rank_key(item[1], item[3], item[2]),
    )
    counts = {"LONG": 0, "SHORT": 0}
    taken = 0
    for venue_id, _score, direction, _symbol in ranked:
        if venue_id in reasons:
            continue
        if counts.get(direction, 0) >= max_per_direction:
            if venue_id not in selected_ids:
                reasons[venue_id] = EXCLUSION_PER_DIRECTION_CAP
            continue
        if taken >= limit:
            if venue_id not in selected_ids:
                reasons[venue_id] = EXCLUSION_GLOBAL_CAP
            continue
        counts[direction] = counts.get(direction, 0) + 1
        taken += 1
    return reasons


def _symbol_from_scan_message(message: str) -> str | None:
    text = str(message or "").strip()
    if not text or ":" not in text:
        return None
    symbol = text.split(":", 1)[0].strip()
    return symbol or None


def _base_identity_metadata(identity, *, analysis_pair: str | None = None) -> dict[str, Any]:
    return {
        "venue": DISCOVERY_VENUE,
        "raw_instrument": identity.raw_identifier,
        "canonical_underlying_asset": identity.canonical_asset_id,
        "analysis_pair": analysis_pair or identity.venue_instrument_symbol,
        "feature_schema_version": DISCOVERY_FEATURE_SCHEMA_VERSION,
        "production_selector": PRODUCTION_SELECTOR_VERSION,
        "v2_01_admission_schema_version": DISCOVERY_ADMISSION_SCHEMA_VERSION,
    }


def _attach_observation_id(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload.get("venue_instrument")
    venue_id = str(payload.get("venue_instrument_id") or "")
    if not venue_id and isinstance(identity, Mapping):
        venue_id = str(identity.get("venue_instrument_id") or "")
    metadata = dict(payload.get("metadata") or {})
    obs_id = observation_join_id(
        scan_id=str(payload.get("scan_id") or ""),
        scanner_type=str(payload.get("scanner_type") or ""),
        venue_instrument_id=venue_id,
        observed_at=str(payload.get("observed_at") or ""),
    )
    metadata["observation_id"] = obs_id
    assert_point_in_time_safe(metadata)
    payload["metadata"] = metadata
    return payload


def _score_components(
    long_score: float | None,
    short_score: float | None,
    *,
    min_score: int,
    blocking_reason: str | None,
) -> ScoreComponents:
    achieved = _candidate_score(long_score, short_score)
    failed: tuple[str, ...] = ()
    if blocking_reason:
        failed = (blocking_reason,)
    return ScoreComponents(
        achieved_score=achieved,
        required_score=float(min_score),
        components={
            "long_technical_score": long_score,
            "short_technical_score": short_score,
        },
        failed_predicates=failed,
        blocking_reason=blocking_reason,
    )


def build_callback_evaluation(
    snapshot: Any,
    long_score: int,
    short_score: int,
    advanced_direction: str | None,
    *,
    observed_at: datetime,
    scan_id: str,
    universe_count: int,
    min_score: int = MIN_TECHNICAL_SCORE,
    canonicalize_asset: AssetCanonicalizer | None = None,
    split_canonical_pair: PairSplitter | None = None,
) -> dict[str, Any] | None:
    """Build one observational row at callback time (before rank finalisation)."""
    raw = _raw_identifier(snapshot)
    identity = _identity(
        raw,
        observed_at,
        canonicalize_asset=canonicalize_asset,
        split_canonical_pair=split_canonical_pair,
    )
    features = decision_features_from_snapshot(
        snapshot,
        observed_at=observed_at,
        decision_at=observed_at,
    )
    passed = _threshold_passed(long_score, short_score, min_score)
    stronger = (
        str(advanced_direction).strip().upper()
        if advanced_direction
        else _stronger_direction(long_score, short_score)
    )
    if stronger not in {"LONG", "SHORT"}:
        stronger = None
    score = _score_components(
        long_score,
        short_score,
        min_score=min_score,
        blocking_reason=None if passed else "directional_technical_threshold",
    )
    metadata = build_below_threshold_metadata(
        score=score,
        universe_count=universe_count,
        features=features,
        selector=SELECTOR_PRODUCTION_COARSE,
        base_metadata={
            **_base_identity_metadata(identity, analysis_pair=raw or None),
            "stronger_direction": stronger,
            "production_preferred_direction": stronger,
            "threshold_passed": passed,
            "applicable_technical_threshold": int(min_score),
            "shortlist_selected": False,
            "production_admission_result": PENDING_FINALIZATION,
            "finalization_status": PENDING_FINALIZATION,
            "canonical_stage0": False,
            "reference_price": features.last_price,
            "recent_24h_high": features.high_24h,
            "recent_24h_low": features.low_24h,
            "momentum_6h_pct": features.momentum_6h_pct,
            "momentum_24h_pct": features.momentum_24h_pct,
            "momentum_72h_pct": features.momentum_72h_pct,
            "affects_ranking": False,
            "affects_trade_authority": False,
        },
    )
    row = ScreeningEvaluation(
        observed_at=observed_at,
        scan_id=scan_id,
        scanner_type=ScannerType.BROAD_SEARCH,
        venue_instrument=identity,
        outcome=ScreeningOutcome.PENDING_FINALIZATION,
        long_score=long_score,
        short_score=short_score,
        advanced_direction=None,
        reason="pending shortlist finalization",
        metadata=metadata,
    )
    return _attach_observation_id(row.to_dict())


def finalize_broad_search_evaluations(
    rows: Sequence[Mapping[str, Any]],
    *,
    selected: Sequence[Any],
    observed_at: datetime,
    universe_count: int,
    min_score: int = MIN_TECHNICAL_SCORE,
    limit: int = MAX_CANDIDATES,
    canonicalize_asset: AssetCanonicalizer | None = None,
    split_canonical_pair: PairSplitter | None = None,
) -> list[dict[str, Any]]:
    """Reclassify threshold-cleared rows using the actual production shortlist.

    Selected snapshots are the authority for ADMITTED. Rank/cutoff are
    reconstructed from recorded scores for explanation only.
    """
    selected_ids = _selected_instrument_ids(
        selected,
        observed_at=observed_at,
        canonicalize_asset=canonicalize_asset,
        split_canonical_pair=split_canonical_pair,
    )
    exclusion_reasons = reconstruct_selector_exclusion_reasons(
        rows,
        selected_ids=selected_ids,
        min_score=min_score,
        limit=limit,
    )
    cutoff_score: float | None = None
    if selected:
        try:
            cutoff_score = float(selected[-1].technical_score)
        except (TypeError, ValueError, AttributeError):
            cutoff_score = None

    qualifying: list[tuple[str, float, str, str, dict[str, Any]]] = []
    finalized: list[dict[str, Any]] = []

    for raw_row in rows:
        row = dict(raw_row)
        venue_id = str(row.get("venue_instrument_id") or "")
        long_score = row.get("long_score")
        short_score = row.get("short_score")
        passed = _threshold_passed(long_score, short_score, min_score)
        stronger = _stronger_direction(long_score, short_score)
        metadata = dict(row.get("metadata") or {})
        metadata["stronger_direction"] = stronger
        metadata["production_preferred_direction"] = stronger
        metadata["threshold_passed"] = passed
        metadata["applicable_technical_threshold"] = int(min_score)
        metadata["selected_count"] = len(selected)
        metadata["universe_count"] = int(universe_count)
        metadata["production_selector"] = PRODUCTION_SELECTOR_VERSION
        metadata["selector"] = SELECTOR_PRODUCTION_COARSE
        metadata["measurement_only"] = True
        metadata["affects_ranking"] = False
        metadata["affects_trade_authority"] = False
        metadata["v2_01_admission_schema_version"] = DISCOVERY_ADMISSION_SCHEMA_VERSION
        metadata["canonical_stage0"] = True
        metadata["finalization_status"] = "FINALIZED"
        metadata.pop("production_admission_result", None)

        admitted = venue_id in selected_ids
        candidate_score = _candidate_score(long_score, short_score)
        venue_instrument = row.get("venue_instrument")
        if (
            passed
            and candidate_score is not None
            and isinstance(venue_instrument, Mapping)
        ):
            sort_symbol = str(
                venue_instrument.get("raw_identifier") or venue_id
            )
            qualifying.append(
                (
                    venue_id,
                    float(candidate_score),
                    stronger or "LONG",
                    sort_symbol,
                    row,
                )
            )

        if admitted:
            outcome = ScreeningOutcome.ADVANCED
            admission = "ADMITTED"
            reason = "admitted into production shortlist"
            direction = stronger
            metadata["production_exclusion_reason"] = None
        elif passed:
            outcome = ScreeningOutcome.COARSE_RANK_LIMIT
            admission = "RANKED_OUTSIDE_BUDGET"
            exclusion = exclusion_reasons.get(venue_id) or EXCLUSION_GLOBAL_CAP
            reason = (
                "passed directional threshold but ranked outside available "
                f"budget/cap ({exclusion})"
            )
            direction = None
            metadata["production_exclusion_reason"] = exclusion
        else:
            outcome = ScreeningOutcome.BELOW_THRESHOLD
            admission = "BELOW_THRESHOLD"
            reason = "neither directional technical score cleared the threshold"
            direction = None
            metadata["production_exclusion_reason"] = None

        metadata["shortlist_selected"] = admitted
        metadata["production_admission_result"] = admission
        metadata["cutoff_score"] = cutoff_score
        if candidate_score is not None and cutoff_score is not None:
            metadata["margin_to_cutoff"] = float(candidate_score) - float(cutoff_score)
        else:
            metadata["margin_to_cutoff"] = None

        features = metadata.get("decision_features")
        score = _score_components(
            long_score,
            short_score,
            min_score=min_score,
            blocking_reason=None if passed else "directional_technical_threshold",
        )
        identity_meta = {
            key: metadata.get(key)
            for key in (
                "venue",
                "raw_instrument",
                "canonical_underlying_asset",
                "analysis_pair",
                "feature_schema_version",
                "production_selector",
                "v2_01_admission_schema_version",
                "observation_id",
                "reference_price",
                "recent_24h_high",
                "recent_24h_low",
                "momentum_6h_pct",
                "momentum_24h_pct",
                "momentum_72h_pct",
                "stronger_direction",
                "production_preferred_direction",
                "threshold_passed",
                "applicable_technical_threshold",
                "shortlist_selected",
                "production_admission_result",
                "production_exclusion_reason",
                "selected_count",
                "cutoff_score",
                "margin_to_cutoff",
                "canonical_stage0",
                "finalization_status",
                "affects_ranking",
                "affects_trade_authority",
            )
        }

        if outcome is ScreeningOutcome.ADVANCED:
            rebuilt = build_advanced_metadata(
                universe_count=universe_count,
                score=score,
                selector=SELECTOR_PRODUCTION_COARSE,
                base_metadata=identity_meta,
            )
        elif outcome is ScreeningOutcome.COARSE_RANK_LIMIT:
            rebuilt = build_rank_limit_metadata(
                rank_context=CoarseRankContext(
                    selector=SELECTOR_PRODUCTION_COARSE,
                    rank_position=0,
                    selected_count=len(selected),
                    universe_count=universe_count,
                    ranked_count=0,
                    candidate_score=candidate_score,
                    cutoff_score=cutoff_score,
                ),
                selector=SELECTOR_PRODUCTION_COARSE,
                base_metadata=identity_meta,
            )
        else:
            rebuilt = build_below_threshold_metadata(
                score=score,
                universe_count=universe_count,
                selector=SELECTOR_PRODUCTION_COARSE,
                base_metadata=identity_meta,
            )
        if isinstance(features, Mapping):
            rebuilt["decision_features"] = dict(features)
        rebuilt.update({k: v for k, v in identity_meta.items() if v is not None or k in rebuilt})
        assert_point_in_time_safe(rebuilt)

        venue_instrument = row.get("venue_instrument")
        if not isinstance(venue_instrument, Mapping):
            continue
        evaluation = ScreeningEvaluation.from_dict(
            {
                **row,
                "outcome": outcome.value,
                "advanced_direction": direction,
                "reason": reason,
                "metadata": rebuilt,
            }
        )
        finalized.append(_attach_observation_id(evaluation.to_dict()))

    rank_inputs = [
        (venue_id, score)
        for venue_id, score, direction, sort_symbol, _row in sorted(
            qualifying,
            key=lambda item: _production_rank_key(item[1], item[3], item[2]),
        )
    ]
    contexts = rank_contexts_for_ranked(
        ranked_scores=rank_inputs,
        selected_count=min(limit, len(selected)),
        universe_count=universe_count,
        selector=SELECTOR_PRODUCTION_COARSE,
    )
    # Per-direction cap is part of the production budget. Record it on rank
    # context without changing the exclusive admission bucket.
    by_id = {row.get("venue_instrument_id"): row for row in finalized}
    for venue_id, context in contexts.items():
        row = by_id.get(venue_id)
        if not isinstance(row, dict):
            continue
        metadata = dict(row.get("metadata") or {})
        rank_payload = context.as_dict()
        rank_payload["max_per_direction"] = int(MAX_PER_DIRECTION)
        rank_payload["max_candidates"] = int(limit)
        metadata["coarse_rank"] = rank_payload
        metadata["rank_position"] = context.rank_position
        metadata["ranked_count"] = context.ranked_count
        if cutoff_score is not None:
            rank_payload["cutoff_score"] = cutoff_score
            candidate = rank_payload.get("candidate_score")
            if candidate is not None:
                rank_payload["margin_to_cutoff"] = float(candidate) - float(
                    cutoff_score
                )
            else:
                rank_payload["margin_to_cutoff"] = None
        assert_point_in_time_safe(metadata)
        row["metadata"] = metadata

    return finalized


def unavailable_instrument_evaluations(
    *,
    scan: Any,
    observed_at: datetime,
    scan_id: str,
    universe_count: int,
    already_recorded: Iterable[str] = (),
    canonicalize_asset: AssetCanonicalizer | None = None,
    split_canonical_pair: PairSplitter | None = None,
) -> list[dict[str, Any]]:
    """DATA_UNAVAILABLE / EXCLUDED_MARKET rows for scan skips and failures."""
    known = {str(item) for item in already_recorded}
    rows: list[dict[str, Any]] = []

    def _emit(symbol: str, outcome: ScreeningOutcome, reason: str) -> None:
        try:
            identity = _identity(
                symbol,
                observed_at,
                canonicalize_asset=canonicalize_asset,
                split_canonical_pair=split_canonical_pair,
            )
        except Exception:
            return
        venue_id = identity.venue_instrument_id
        if not venue_id or venue_id in known:
            return
        known.add(venue_id)
        if identity.instrument_class is not InstrumentClass.SPOT:
            outcome = ScreeningOutcome.EXCLUDED_MARKET
            reason = "excluded non-spot instrument class"
        metadata = build_coarse_status_metadata(
            universe_count=universe_count,
            extra={
                **_base_identity_metadata(identity, analysis_pair=symbol),
                "threshold_passed": False,
                "shortlist_selected": False,
                "production_admission_result": (
                    "EXCLUDED_MARKET"
                    if outcome is ScreeningOutcome.EXCLUDED_MARKET
                    else "DATA_UNAVAILABLE"
                ),
                "canonical_stage0": True,
                "finalization_status": "FINALIZED",
                "affects_ranking": False,
                "affects_trade_authority": False,
            },
        )
        row = ScreeningEvaluation(
            observed_at=observed_at,
            scan_id=scan_id,
            scanner_type=ScannerType.BROAD_SEARCH,
            venue_instrument=identity,
            outcome=outcome,
            reason=reason,
            metadata=metadata,
        )
        rows.append(_attach_observation_id(row.to_dict()))

    for message in list(getattr(scan, "failures", None) or []):
        symbol = _symbol_from_scan_message(str(message))
        if symbol:
            _emit(symbol, ScreeningOutcome.DATA_UNAVAILABLE, "instrument analysis failed")
    for message in list(getattr(scan, "skips", None) or []):
        symbol = _symbol_from_scan_message(str(message))
        if symbol:
            _emit(symbol, ScreeningOutcome.DATA_UNAVAILABLE, "insufficient market data")
    for message in list(getattr(scan, "data_quality_rejections", None) or []):
        symbol = _symbol_from_scan_message(str(message))
        if symbol:
            _emit(symbol, ScreeningOutcome.DATA_UNAVAILABLE, "market data rejected")
    return rows
