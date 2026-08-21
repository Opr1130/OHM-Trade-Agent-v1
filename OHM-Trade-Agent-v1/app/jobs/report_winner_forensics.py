from __future__ import annotations

from collections import Counter

from app.services.daily_gainer_reconciliation import reconcile_daily_gainers
from app.services.winner_forensics import classify_winner_traces


def main() -> None:
    traces = reconcile_daily_gainers(top_n=20)
    cases = classify_winner_traces(traces)
    counts = Counter(case.classification for case in cases)

    print("===== OHM WAVE 5.1 WINNER FORENSICS =====")
    print("Top gainers checked:", len(cases))
    print("Failure/success buckets:", dict(sorted(counts.items())))
    for case in cases:
        print(
            f"{case.symbol}: Class={case.classification} Today={case.gain_today_pct:+.2f}% "
            f"FirstPhase={case.first_phase or 'NONE'} FirstGain={case.gain_when_first_seen_pct} "
            f"Remaining={case.remaining_upside_after_first_seen_pct} Liquidity=${case.liquidity_24h_usd_approx:,.0f}"
        )
        print("  Reason:", case.reason)
    print("Retrospective only: True")
    print("Production decisions changed: False")


if __name__ == "__main__":
    main()
