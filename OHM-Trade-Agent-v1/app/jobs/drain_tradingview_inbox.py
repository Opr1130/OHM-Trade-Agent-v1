from app.services.tradingview_inbox import process_queued_events


MAX_BATCHES_PER_RUN = 4


def main() -> None:
    total = {"checked": 0, "processed": 0, "rejected": 0, "retried": 0, "qualified": 0, "poisoned": 0, "reclaimed": 0}
    for _ in range(MAX_BATCHES_PER_RUN):
        result = process_queued_events()
        for key in total:
            total[key] += int(result.get(key, 0) or 0)
        if int(result.get("checked", 0) or 0) == 0:
            break
    print("===== OHM TRADINGVIEW V2 INBOX DRAIN =====")
    for key, value in total.items():
        print(f"{key}: {value}")
    print("Trade authority changed: False")
    print("TradingView promotion authority: False")


if __name__ == "__main__":
    main()
