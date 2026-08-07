from app.scanner.models import MarketSnapshot


def classify(snapshot: MarketSnapshot) -> str:
    if snapshot.technical_score >= 80:
        return "ALERT"

    if snapshot.technical_score >= 65:
        return "WATCH"

    return "REJECT"


def top_candidates(
    snapshots: list[MarketSnapshot],
    limit: int = 5,
) -> list[MarketSnapshot]:
    ranked = sorted(
        snapshots,
        key=lambda item: item.technical_score,
        reverse=True,
    )

    return ranked[:limit]
