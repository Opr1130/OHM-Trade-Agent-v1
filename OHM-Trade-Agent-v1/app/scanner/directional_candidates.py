from dataclasses import replace
import logging
import math
from typing import Callable

from app.scanner.candidates import MIN_TECHNICAL_SCORE, MAX_CANDIDATES
from app.scanner.models import MarketSnapshot
from app.scanner.short_technical_scorer import score_short_snapshot


MAX_PER_DIRECTION = 5


logger = logging.getLogger(__name__)


EvaluationCallback = Callable[[MarketSnapshot, int | None, int | None, str | None], None]


def _emit_evaluation_fail_soft(
    callback: EvaluationCallback | None,
    snapshot: MarketSnapshot,
    long_score: int | None,
    short_score: int | None,
    advanced_direction: str | None,
    scan_id: str | None,
) -> None:
    if callback is None:
        return
    try:
        # A copy prevents an observational callback from mutating the object
        # that production later ranks or qualifies.
        callback(
            replace(snapshot),
            int(long_score) if long_score is not None else None,
            int(short_score) if short_score is not None else None,
            advanced_direction,
        )
    except Exception as exc:
        logger.warning(
            "O'Pip screening callback failed open scanner_type=BROAD_SEARCH "
            "scan_id=%s raw=%s operation=record_screening_evaluation error=%s",
            scan_id or "UNKNOWN",
            getattr(snapshot, "symbol", "UNKNOWN"),
            type(exc).__name__,
        )


def select_directional_candidates(
    snapshots: list[MarketSnapshot],
    *,
    min_score: int = MIN_TECHNICAL_SCORE,
    limit: int = MAX_CANDIDATES,
    on_evaluated: EvaluationCallback | None = None,
    scan_id: str | None = None,
    scoring_version: str = "v1",
) -> list[MarketSnapshot]:
    """Return a maximum-eight mixed LONG/SHORT shortlist.

    A single underlying asset may appear only once. If both directional scores
    qualify, the stronger direction wins. This prevents contradictory signals
    and keeps the single Chief call bounded to the existing maximum.
    """
    if scoring_version not in {"v1", "v2"}:
        raise ValueError("UNSUPPORTED_SCORING_VERSION")
    best_by_asset: dict[str, MarketSnapshot] = {}

    for snapshot in snapshots:
        asset = snapshot.underlying_asset or snapshot.symbol
        if scoring_version == "v2":
            if getattr(snapshot, "v2_feature_status", None) != "READY":
                _emit_evaluation_fail_soft(on_evaluated, snapshot, None, None, None, scan_id)
                continue
            values = (getattr(snapshot, "v2_long_score", None), getattr(snapshot, "v2_short_score", None))
            if any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100
                for v in values
            ):
                _emit_evaluation_fail_soft(on_evaluated, snapshot, None, None, None, scan_id)
                continue
            long_score, short_score = (int(v) for v in values)
        else:
            long_score = snapshot.technical_score
            short_score = score_short_snapshot(snapshot).score

        options: list[MarketSnapshot] = []
        if long_score >= min_score:
            options.append(
                replace(
                    snapshot,
                    trade_direction="LONG",
                    technical_score=int(long_score),
                    directional_long_score=int(long_score),
                    directional_short_score=int(short_score),
                    technical_score_version=scoring_version,
                )
            )
        if short_score >= min_score:
            options.append(
                replace(
                    snapshot,
                    trade_direction="SHORT",
                    technical_score=short_score,
                    directional_long_score=int(long_score),
                    directional_short_score=int(short_score),
                )
            )
        if not options:
            _emit_evaluation_fail_soft(
                on_evaluated,
                snapshot,
                long_score,
                short_score,
                None,
                scan_id,
            )
            continue

        chosen = max(
            options,
            key=lambda item: (
                item.technical_score,
                item.trade_direction == "LONG",
            ),
        )
        _emit_evaluation_fail_soft(
            on_evaluated,
            snapshot,
            long_score,
            short_score,
            chosen.trade_direction,
            scan_id,
        )
        previous = best_by_asset.get(asset)
        if previous is None or chosen.technical_score > previous.technical_score:
            best_by_asset[asset] = chosen

    all_candidates = sorted(
        best_by_asset.values(),
        key=lambda item: (-item.technical_score, item.symbol, item.trade_direction),
    )

    # Avoid one direction crowding out all evidence while still honoring score.
    selected: list[MarketSnapshot] = []
    counts = {"LONG": 0, "SHORT": 0}
    for candidate in all_candidates:
        direction = candidate.trade_direction
        if counts[direction] >= MAX_PER_DIRECTION:
            continue
        selected.append(candidate)
        counts[direction] += 1
        if len(selected) >= limit:
            break

    return selected



def qualifying_long_alternatives(
    snapshots: list[MarketSnapshot],
    *,
    excluded_assets: set[str] | None = None,
    min_score: int = MIN_TECHNICAL_SCORE,
    scoring_version: str = "v1",
) -> list[MarketSnapshot]:
    """Return every independently qualifying LONG option in deterministic rank order."""
    if scoring_version not in {"v1", "v2"}:
        raise ValueError("UNSUPPORTED_SCORING_VERSION")
    excluded = {str(asset) for asset in (excluded_assets or set())}
    best_by_asset: dict[str, MarketSnapshot] = {}

    for snapshot in snapshots:
        asset = str(snapshot.underlying_asset or snapshot.symbol)
        if asset in excluded:
            continue
        if scoring_version == "v2":
            if getattr(snapshot, "v2_feature_status", None) != "READY":
                continue
            long_value = getattr(snapshot, "v2_long_score", None)
            short_value = getattr(snapshot, "v2_short_score", None)
            if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100
                   for v in (long_value, short_value)):
                continue
            long_score, short_score = int(long_value), int(short_value)
        else:
            long_score = int(snapshot.technical_score)
            short_score = int(score_short_snapshot(snapshot).score)
        if long_score < int(min_score):
            continue
        candidate = replace(
            snapshot,
            trade_direction="LONG",
            technical_score=long_score,
            directional_long_score=long_score,
            directional_short_score=short_score,
            technical_score_version=scoring_version,
        )
        previous = best_by_asset.get(asset)
        if (
            previous is None
            or candidate.technical_score > previous.technical_score
            or (
                candidate.technical_score == previous.technical_score
                and candidate.symbol < previous.symbol
            )
        ):
            best_by_asset[asset] = candidate

    return sorted(
        best_by_asset.values(),
        key=lambda item: (-item.technical_score, item.symbol),
    )
