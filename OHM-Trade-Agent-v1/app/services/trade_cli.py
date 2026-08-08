import argparse

from app.services.active_trade_registry import (
    get_active_trades,
    close_trade,
)
from app.services.register_trade import register_trade


def add_command(args):
    trade = register_trade(
        symbol=args.symbol,
        entry_price=args.entry,
        stop_price=args.stop,
        target_1=args.target1,
        target_2=args.target2,
        risk_level=args.risk,
    )

    print("Trade registered successfully")
    print("Symbol:", trade.symbol)
    print("Entry:", trade.entry_price)
    print("Stop:", trade.stop_price)
    print("Target 1:", trade.target_1)
    print("Target 2:", trade.target_2)
    print("Risk:", trade.risk_level)


def list_command(_args):
    trades = get_active_trades()

    print("Active trades:", len(trades))

    for trade in trades:
        print()
        print("Symbol:", trade.symbol)
        print("Entry:", trade.entry_price)
        print("Stop:", trade.stop_price)
        print("Target 1:", trade.target_1)
        print("Target 2:", trade.target_2)
        print("Risk:", trade.risk_level)
        print("Opened:", trade.opened_at)


def close_command(args):
    symbol = args.symbol.upper()

    if close_trade(symbol):
        print(f"{symbol} marked CLOSED")
    else:
        print(f"{symbol} not found")


def main():
    parser = argparse.ArgumentParser(
        description="OHM AI Active Trade Manager"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("symbol")
    add_parser.add_argument("entry", type=float)
    add_parser.add_argument("stop", type=float)
    add_parser.add_argument("target1", type=float)
    add_parser.add_argument("target2", type=float)
    add_parser.add_argument(
        "risk",
        choices=["low", "medium"],
    )
    add_parser.set_defaults(func=add_command)

    list_parser = subparsers.add_parser("list")
    list_parser.set_defaults(func=list_command)

    close_parser = subparsers.add_parser("close")
    close_parser.add_argument("symbol")
    close_parser.set_defaults(func=close_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
