import json

from app.services.historical_analytics import historical_summary
from app.services.self_calibration import calibration_model
from app.services.trade_outcome_registry import get_outcomes


def main() -> None:
    outcomes = get_outcomes()
    history = historical_summary(outcomes)
    calibration = calibration_model(outcomes)

    print("===== OHM HISTORICAL ANALYTICS =====")
    print(json.dumps(history, indent=2, sort_keys=True))
    print("===== OHM SELF-CALIBRATION =====")
    print(json.dumps(calibration, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
