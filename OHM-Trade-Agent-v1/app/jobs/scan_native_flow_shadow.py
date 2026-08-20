from app.services.movement_discovery_v2 import scan_early_movers
from app.services.native_flow_evidence import append_native_flow_shadow, evaluate_native_flow_shadow


def main() -> None:
    coarse, signals = scan_early_movers()
    mover_by_asset = {row.base_asset.upper(): row for row in coarse}
    results = []
    for signal in signals[:10]:
        mover = mover_by_asset.get(signal.base_asset.upper())
        if mover is None:
            continue
        result = evaluate_native_flow_shadow(mover.kraken_public_symbol)
        results.append(result)

    print("===== OHM NATIVE FLOW INTELLIGENCE — SHADOW =====")
    print("Candidates evaluated:", len(results))
    print("Production scores changed:", False)
    print("Trade authority changed:", False)
    for row in results:
        metrics = row.metrics
        if metrics is None:
            print(f"FLOW {row.symbol}: UNAVAILABLE Warnings={list(row.warnings)}")
            continue
        print(
            f"FLOW {row.symbol}: Bias={row.evidence.bias} Strength={row.evidence.strength}/10 "
            f"BuyShare={metrics.buy_share:.2f} TradeAccel={metrics.trade_count_acceleration} "
            f"LargePrintShare={metrics.large_print_concentration:.2f} "
            f"BookImbalance={metrics.book_notional_imbalance:+.2f}"
        )
    print("Shadow flow observations captured:", append_native_flow_shadow(results))


if __name__ == "__main__":
    main()
