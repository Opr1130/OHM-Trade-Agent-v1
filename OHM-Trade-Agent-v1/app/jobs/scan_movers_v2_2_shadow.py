from app.services.movement_discovery_v2_2_shadow import (
    append_v22_shadow_results,
    scan_v22_shadow,
)


def main() -> None:
    candidates, results, stats = scan_v22_shadow()
    print("===== OHM MOVEMENT DISCOVERY V2.2 SHADOW CHALLENGER =====")
    print("Selected shadow candidates:", stats["selected"])
    print("Deep analyzed:", stats["analyzed"])
    print("Deep skipped:", stats["skipped"])
    cohort_counts = {}
    for row in candidates:
        cohort_counts[row.cohort] = cohort_counts.get(row.cohort, 0) + 1
    print("Cohorts:", cohort_counts)
    print("Trade authority changed:", False)
    print("Telegram authority changed:", False)
    print("Production v2.1 thresholds changed:", False)
    for row in results[:12]:
        print(
            f"SHADOW {row.symbol}: Cohort={row.cohort} Challenger={row.challenger_score:.1f} "
            f"TodayLift={row.lift_today_pct:+.2f}% 24hLift={row.lift_24h_pct:+.2f}% "
            f"From24hHigh={row.distance_from_24h_high_pct:.2f}% "
            f"V21={row.v21_signal_present} Stage={row.v21_stage} "
            f"Continuation={row.v21_continuation_confidence} Entry={row.v21_entry_quality} "
            f"V21AlertEligible={row.v21_alert_eligible}"
        )
    print("Shadow observations captured:", append_v22_shadow_results(results))


if __name__ == "__main__":
    main()
