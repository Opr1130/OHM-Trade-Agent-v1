from pathlib import Path

from app.services.compact_alerts import format_watch_alert
from app.services.price_movement_notifier import format_price_movement_message


FORBIDDEN_USER_ALERT_PREFIXES = (
    "🚀 OHM",
    "🔥 OHM",
    "🎯 OHM",
    "🚨 OHM",
    "⚪ OHM",
    "👁 OHM",
    "🔎 OHM",
    "⚠️ OHM",
)


def test_compact_alert_title_has_no_ohm_branding():
    message = format_watch_alert(
        symbol="KASUSD",
        potential_low_pct=10,
        potential_high_pct=25,
        confidence_pct=70,
        risk_pct=30,
        downside_pct=9,
        reason="1h momentum is positive",
        action="WATCH FOR PULLBACK",
        title="OPPORTUNITY",
    )
    assert message.startswith("🚀 OPPORTUNITY")
    assert "🚀 OHM" not in message


def test_movement_alert_title_has_no_ohm_branding():
    message = format_price_movement_message(
        {
            "stage": "READY",
            "symbol": "KASUSD",
            "readiness_score": 70,
            "expected_move_low_pct": 10,
            "expected_move_high_pct": 25,
            "reasons": ["1h momentum is positive"],
        }
    )
    assert message.startswith("🚀 MOVEMENT WATCH")
    assert "OHM MOVEMENT WATCH" not in message


def test_known_user_facing_alert_modules_do_not_reintroduce_ohm_prefix():
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "app/services/compact_alerts.py",
        root / "app/services/price_movement_notifier.py",
        root / "app/services/telegram_notifier.py",
        root / "app/services/chief_alert_notifier.py",
        root / "app/jobs/scan_movers.py",
        root / "app/services/movement_discovery_v2.py",
        root / "app/services/telegram_market_insights.py",
        root / "app/services/telegram_command_center.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for prefix in FORBIDDEN_USER_ALERT_PREFIXES:
        assert prefix not in combined
