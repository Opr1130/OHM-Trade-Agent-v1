from app.scanner.models import MarketSnapshot


MIN_TECHNICAL_SCORE = 80
MAX_CANDIDATES = 8


def select_candidates(
    snapshots: list[MarketSnapshot],
    min_score: int = MIN_TECHNICAL_SCORE,
    limit: int = MAX_CANDIDATES,
) -> list[MarketSnapshot]:
    qualified = [
        snapshot
        for snapshot in snapshots
        if snapshot.technical_score >= min_score
    ]

    qualified.sort(
        key=lambda item: item.technical_score,
        reverse=True,
    )

    return qualified[:limit]
