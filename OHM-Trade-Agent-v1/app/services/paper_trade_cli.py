from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.services.paper_trade_control import (
    get_paper_trade_control,
    set_paper_trade_enabled,
)
from app.services.paper_trade_engine import PaperTradeConfig
from app.services.paper_trade_registry import account_summary


def _print_status() -> None:
    control = get_paper_trade_control()
    settings = get_settings()
    config = PaperTradeConfig.from_settings(settings)
    summary = account_summary(config.starting_equity)

    print("OHM Paper Trade v1")
    print("Mode:", "ON" if control.enabled else "OFF")
    print("Control status:", control.status)
    print("Updated at:", control.updated_at or "N/A")
    print("Policy: SPOT LONG ONLY")
    print("Exchange writes: DISABLED BY ARCHITECTURE")
    print("Starting equity:", f"$${summary.starting_equity:,.2f}")
    print("Realized net P/L:", f"$${summary.realized_net_pnl:,.2f}")
    print("Closed equity:", f"$${summary.closed_equity:,.2f}")
    print("Reserved capital:", f"$${summary.reserved_capital:,.2f}")
    print("Available capital:", f"$${summary.available_capital:,.2f}")
    print("Pending entries:", summary.pending_entries)
    print("Open positions:", summary.open_positions)
    print("Closed trades:", summary.closed_trades)
    print("Cancelled setups:", summary.cancelled_setups)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OHM isolated paper-trading operator control"
    )
    parser.add_argument("command", choices=["on", "off", "status"])
    args = parser.parse_args()

    if args.command == "on":
        state = set_paper_trade_enabled(True)
        print("Paper Trade v1: ON")
        print("Updated:", state.updated_at)
        print("New qualified LONG opportunities may enter the paper ledger.")
        return

    if args.command == "off":
        state = set_paper_trade_enabled(False)
        print("Paper Trade v1: OFF")
        print("Updated:", state.updated_at)
        print(
            "No new paper exposure will be opened. Pending paper entries are "
            "cancelled by the next monitor pass; already-open paper positions "
            "continue to a terminal outcome."
        )
        return

    _print_status()


if __name__ == "__main__":
    main()
