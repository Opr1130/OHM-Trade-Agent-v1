from __future__ import annotations

from app.services.prospective_signal_evaluation import build_movement_discovery_comparison


def _pct(value) -> str:
    return "n/a" if value is None else f"{float(value) * 100.0:+.1f}pp"


def main() -> None:
    report = build_movement_discovery_comparison()

    print("===== MOVEMENT DISCOVERY V2.1 — READY VS WATCH =====")
    print("Prospective outcome rows:", report["outcome_rows"])
    print("Minimum samples per bucket:", report["minimum_samples_per_bucket"])

    for horizon, comparison in report["horizons"].items():
        ready = comparison["ready"]
        watch = comparison["watch"]
        eligible = comparison["alert_eligible"]
        not_eligible = comparison["not_alert_eligible"]
        print(
            f"{horizon}: READY n={ready['samples']} MFE={ready['avg_mfe_pct']} MAE={ready['avg_mae_pct']} "
            f"vs WATCH n={watch['samples']} MFE={watch['avg_mfe_pct']} MAE={watch['avg_mae_pct']}"
        )
        if comparison["ready_vs_watch_sufficient"]:
            print(
                f"  READY separation: MFE={comparison['ready_mfe_delta_pct']:+.2f}pp "
                f"hit+5={_pct(comparison['ready_hit_5_delta'])}"
            )
        else:
            print("  READY separation: INSUFFICIENT DATA")
        print(
            f"  alert eligibility: eligible n={eligible['samples']} vs not-eligible n={not_eligible['samples']}"
        )
        for band, metrics in comparison["liquidity_bands"].items():
            print(
                f"  liquidity {band}: n={metrics['samples']} MFE={metrics['avg_mfe_pct']} "
                f"MAE={metrics['avg_mae_pct']} hit+5={metrics['hit_5pct_rate']}"
            )

    print("Review status:", report["status"])
    print("Auto promotion allowed:", report["auto_promotion_allowed"])
    print("Scores remain heuristic ranks, not calibrated probabilities.")
    print("Production decisions changed:", report["production_thresholds_changed"])


if __name__ == "__main__":
    main()
