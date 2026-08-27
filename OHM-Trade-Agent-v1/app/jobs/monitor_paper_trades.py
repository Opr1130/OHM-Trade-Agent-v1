from app.core.config import get_settings
from app.services.paper_trade_engine import PaperTradeConfig
from app.services.paper_trade_monitor import run_paper_trade_monitor


def main() -> None:
    settings = get_settings()
    summary = run_paper_trade_monitor(PaperTradeConfig.from_settings(settings))
    print("OHM Paper Trade v1")
    print("Control enabled:", summary.control_enabled)
    print("Tracked:", summary.tracked)
    print("Checked:", summary.checked)
    print("Opened:", summary.opened)
    print("TP1 hits:", summary.tp1_hits)
    print("Closed:", summary.closed)
    print("Cancelled:", summary.cancelled)
    print("Failures:", len(summary.failures))
    for failure in summary.failures[:5]:
        print("PAPER FAILURE:", failure)


if __name__ == "__main__":
    main()
