from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.services.freqtrade_result_ingest import freqtrade_dry_run_status
from app.services.paper_trade_control import (
    PaperTradeActivationError,
    get_paper_trade_control,
    set_paper_trade_enabled,
)
from app.services.paper_trade_engine import PaperTradeConfig
from app.services.paper_trade_registry import account_summary


def _print_status() -> None:
    control = get_paper_trade_control()
    authoritative = freqtrade_dry_run_status()
    settings = get_settings()
    config = PaperTradeConfig.from_settings(settings)
    shadow = account_summary(config.starting_equity)
    pnl = authoritative.get("realized_pnl_by_currency") or {}

    print("OHM Paper Trade")
    print("Mode:", "ON" if control.enabled else "OFF")
    print("Control status:", control.status)
    print("Updated at:", control.updated_at or "N/A")
    print("Authoritative engine: FREQTRADE DRY-RUN")
    print("Freqtrade status:", authoritative.get("status", "UNKNOWN"))
    print(
        "Freqtrade open/closed:",
        f"{authoritative.get('open_trades', 0)}/"
        f"{authoritative.get('closed_trades', 0)}",
    )
    print(
        "Freqtrade realized P/L:",
        f"${float(pnl.get('USD') or 0.0):,.2f} USD | "
        f"{float(pnl.get('USDT') or 0.0):,.2f} USDT",
    )
    print("Shadow/control engine: OHM INTERNAL SIMULATOR")
    print("Shadow realized net P/L:", f"${shadow.realized_net_pnl:,.2f}")
    print(
        "Shadow pending/open:",
        f"{shadow.pending_entries}/{shadow.open_positions}",
    )
    print("Policy: SPOT LONG ONLY")
    print("Kraken exchange writes: NONE")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OHM isolated paper-trading operator control"
    )
    parser.add_argument("command", choices=["on", "off", "status"])
    args = parser.parse_args()

    if args.command == "on":
        try:
            state = set_paper_trade_enabled(True)
        except PaperTradeActivationError as exc:
            parser.error(f"paper activation refused: {exc}")
        print("Paper Trade: ON")
        print("Authoritative engine: FREQTRADE DRY-RUN")
        print("Updated:", state.updated_at)
        print("New qualified LONG opportunities may enter forward testing.")
        return

    if args.command == "off":
        state = set_paper_trade_enabled(False)
        print("Paper Trade: OFF")
        print("Updated:", state.updated_at)
        print(
            "No new paper exposure will be opened. Pending entries are "
            "cancelled by the paper engines; already-open paper positions "
            "continue to a terminal outcome."
        )
        return

    _print_status()


if __name__ == "__main__":
    main()
