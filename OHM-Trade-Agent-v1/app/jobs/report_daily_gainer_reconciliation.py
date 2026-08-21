from app.services.daily_gainer_reconciliation import reconcile_daily_gainers


def main() -> None:
    rows = reconcile_daily_gainers(top_n=20)
    print("===== OHM DAILY GAINER RECONCILIATION =====")
    print("Top gainers checked:", len(rows))
    print("Production decisions changed:", False)
    for row in rows:
        first = row.first_ohm_seen_at or "NEVER"
        phase = row.first_ohm_phase or "NONE"
        gain_first = (
            "n/a" if row.gain_when_first_seen_pct is None
            else f"{row.gain_when_first_seen_pct:+.2f}%"
        )
        remaining = (
            "n/a" if row.remaining_upside_after_first_seen_pct is None
            else f"{row.remaining_upside_after_first_seen_pct:+.2f}%"
        )
        print(
            f"{row.symbol}: Today={row.gain_today_pct:+.2f}% "
            f"Liquidity=${row.liquidity_24h_usd_approx:,.0f} "
            f"FirstSeen={first} Phase={phase} GainAtFirst={gain_first} "
            f"RemainingUpside={remaining} Status={row.trace_status}"
        )
        if row.first_ohm_seen_at:
            print(
                "  First-state features: "
                f"1h={row.first_momentum_1h_pct} "
                f"Accel={row.first_momentum_acceleration} "
                f"RelVol={row.first_relative_volume} "
                f"TradeAccel={row.first_trade_count_acceleration} "
                f"Aggressor={row.first_aggressor_imbalance}"
            )
    print("Retrospective audit only; no trade authority.")


if __name__ == "__main__":
    main()
