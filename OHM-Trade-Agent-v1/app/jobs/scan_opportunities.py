from app.core.config import get_settings
from app.scanner.candidates import select_candidates
from app.scanner.market_scanner import scan_market
from app.services.chief_alert_notifier import send_trade_plan
from app.services.chief_analyst import review_candidates
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.recommendation_gate import qualified_alerts


def main():
    settings = get_settings()

    scan = scan_market(limit=100)
    candidates = select_candidates(scan.snapshots)

    print("OHM AI Opportunity Scan")
    print("Requested:", scan.requested)
    print("Analyzed:", scan.analyzed)
    print("Skipped:", scan.skipped)
    print("Failed:", scan.failed)
    print("Technical shortlist:", len(candidates))

    if not candidates:
        print("No technical candidates.")
        return

    review = review_candidates(
        candidates,
        settings.openai_model,
        settings.openai_api_key,
    )

    alerts = qualified_alerts(review)

    print("AI top candidates:", len(review.get("top_candidates", [])))
    print("Qualified alerts:", len(alerts))

    snapshot_by_symbol = {
        snapshot.symbol: snapshot
        for snapshot in scan.snapshots
    }

    sent = 0

    for alert in alerts:
        snapshot = snapshot_by_symbol.get(alert["symbol"])

        if snapshot is None:
            continue

        plan = build_entry_exit_plan(
            snapshot,
            alert["risk_level"],
        )

        if send_trade_plan(
            candidate=alert,
            plan=plan,
            summary=review.get("summary", ""),
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ):
            sent += 1

    print("Telegram notifications sent:", sent)


if __name__ == "__main__":
    main()
