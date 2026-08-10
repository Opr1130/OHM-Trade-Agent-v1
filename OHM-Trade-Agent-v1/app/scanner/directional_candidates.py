from dataclasses import replace

from app.scanner.candidates import MIN_TECHNICAL_SCORE, MAX_CANDIDATES
from app.scanner.models import MarketSnapshot
from app.scanner.short_technical_scorer import score_short_snapshot


MAX_PER_DIRECTION = 5


def select_directional_candidates(
    snapshots: list[MarketSnapshot],
    *,
    min_score: int = MIN_TECHNICAL_SCORE,
    limit: int = MAX_CANDIDATES,
) -> list[MarketSnapshot]:
    """Return a maximum-eight mixed LONG/SHORT shortlist.

    A single underlying asset may appear only once. If both directional scores
    qualify, the stronger direction wins. This prevents contradictory signals
    and keeps the single Chief call bounded to the existing maximum.
    """
    best_by_asset: dict[str, MarketSnapshot] = {}

    for snapshot in snapshots:
        asset = snapshot.underlying_asset or snapshot.symbol
        long_score = snapshot.technical_score
        short_score = score_short_snapshot(snapshot).score

        options: list[MarketSnapshot] = []
        if long_score >= min_score:
            options.append(replace(snapshot, trade_direction="LONG"))
        if short_score >= min_score:
            options.append(
                replace(
                    snapshot,
                    trade_direction="SHORT",
                    technical_score=short_score,
                )
            )
        if not options:
            continue

        chosen = max(
            options,
            key=lambda item: (
                item.technical_score,
                item.trade_direction == "LONG",
            ),
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
