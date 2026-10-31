"""Drain the bounded Stage-2 Independent Research & Risk Review queue.

This is the ONLY process that performs DeepSeek network I/O. It is a separate,
bounded, non-overlapping job on the existing scheduler, so a slow or hung
provider can never delay a scan, a Chief review, a Telegram delivery or an F11
protection cycle.

It performs no scanning, no ranking, no alerting and no trade/execution action.
"""

from app.services.independent_research_review import drain_queue


def main() -> None:
    result = drain_queue()
    print("IRR shadow drain status:", result.get("status"))
    print("IRR shadow requests processed:", result.get("processed", 0))
    print("IRR shadow queue rows:", result.get("queue_total_rows", 0))
    print("IRR shadow next row:", result.get("next_line", 0))
    for row in result.get("results") or []:
        print(
            "IRR shadow disposition:",
            row.get("symbol"),
            row.get("direction"),
            row.get("status"),
            row.get("error_type"),
            f"charged_tokens={row.get('charged_tokens')}",
            f"charged_cost_usd={row.get('charged_cost_usd')}",
        )
    print("Trade authority changed:", False)
    print("Advisory only:", True)


if __name__ == "__main__":
    main()
