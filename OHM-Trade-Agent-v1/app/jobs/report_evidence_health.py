from app.services.tradingview_evidence_diagnostics import load_tradingview_evidence_diagnostics


def main() -> None:
    report = load_tradingview_evidence_diagnostics()
    print("===== OHM EVIDENCE HEALTH =====")
    print("TradingView bridge enabled:", report.get("bridge_enabled"))
    print("TradingView coverage status:", report.get("coverage_status"))
    print("Accepted webhooks:", report.get("accepted_webhooks", 0))
    print("Queue depth:", report.get("queue_depth", 0))
    print("Processed qualified events:", report.get("processed_qualified_total", 0))
    print("Recent qualified events:", report.get("recent_qualified", 0))
    print("Stale/future qualified events:", report.get("stale_or_future_qualified", 0))
    print("Freshness window sec:", report.get("freshness_seconds"))
    print("Qualified pairs:", report.get("qualified_pairs") or [])
    print("Qualified directions:", report.get("qualified_directions") or {})
    for item in report.get("recent_evidence") or []:
        print(
            "RECENT TV EVIDENCE:",
            f"Pair={item.get('pair')}",
            f"Direction={item.get('direction')}",
            f"Age={item.get('age_seconds')}s",
        )


if __name__ == "__main__":
    main()
