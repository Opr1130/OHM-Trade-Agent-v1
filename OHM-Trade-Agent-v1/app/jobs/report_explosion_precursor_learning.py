from __future__ import annotations

from app.services.explosion_precursor_learning import read_precursor_observations
from app.services.prospective_signal_evaluation import build_precursor_watch_comparison


def _pct(value) -> str:
    return "n/a" if value is None else f"{float(value) * 100.0:+.1f}pp"


def main() -> None:
    observations = read_precursor_observations()
    report = build_precursor_watch_comparison()

    print("===== OHM WAVE 5.1 PRECURSOR — WATCH VS NO_WATCH =====")
    print("Precursor observations:", len(observations))
    print("Prospectively joined outcomes:", report["joined_outcome_rows"])
    print("Minimum samples per WATCH/NO_WATCH bucket:", report["minimum_samples_per_bucket"])
    print("Same-cohort baseline:", report["same_cohort_baseline"])

    for horizon, comparison in report["horizons"].items():
        watch = comparison["watch"]
        baseline = comparison["no_watch"]
        print(
            f"{horizon}: WATCH n={watch['samples']} MFE={watch['avg_mfe_pct']} MAE={watch['avg_mae_pct']} "
            f"vs NO_WATCH n={baseline['samples']} MFE={baseline['avg_mfe_pct']} MAE={baseline['avg_mae_pct']}"
        )
        if comparison["sufficient_for_comparison"]:
            print(
                f"  separation: MFE={comparison['mfe_delta_pct']:+.2f}pp "
                f"hit+5={_pct(comparison['hit_5_delta'])} hit+10={_pct(comparison['hit_10_delta'])}"
            )
        else:
            print("  separation: INSUFFICIENT DATA")

    print("Promotion status:", report["status"])
    print("Auto promotion allowed:", report["auto_promotion_allowed"])
    print("Production phase thresholds changed:", report["production_thresholds_changed"])
    print("Trade authority changed: False")


if __name__ == "__main__":
    main()
