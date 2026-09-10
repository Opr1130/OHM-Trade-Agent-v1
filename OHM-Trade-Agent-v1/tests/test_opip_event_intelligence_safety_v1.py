from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.opip.events.observer import capture_external_event_intelligence


ROOT = Path(__file__).resolve().parents[1]
EVENT_PACKAGE = ROOT / "app" / "opip" / "events"


FORBIDDEN_IMPORT_PREFIXES = (
    "app.exchanges",
    "app.services.chief_alert_notifier",
    "app.services.price_movement_notifier",
    "app.services.order_intent_registry",
    "app.services.active_trade_registry",
    "app.services.paper_trade_registry",
    "app.services.pending_setup_registry",
    "app.services.recommendation_gate",
    "app.opip.decision",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def test_event_package_has_no_trading_authority_imports():
    for path in EVENT_PACKAGE.rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith(FORBIDDEN_IMPORT_PREFIXES), (
                f"{path.name} imports trading-authority module {module}"
            )


def test_event_package_has_no_ai_provider_dependency():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EVENT_PACKAGE.rglob("*.py")
    ).casefold()
    for forbidden in (
        "openai(",
        "anthropic",
        "gemini",
        "google.generativeai",
    ):
        assert forbidden not in text


def test_event_store_is_dark_by_default():
    settings = Settings(webhook_secret="123456789012")
    assert settings.opip_event_store_enabled is False
    assert settings.opip_event_ingest_interval_seconds == 300
    assert settings.opip_event_provider_timeout_seconds == 5.0
    assert settings.opip_event_mapping_lookups_per_capture == 1


def test_disabled_event_capture_performs_no_provider_work(tmp_path):
    class ExplodingClient:
        def get_posts(self, symbols):
            raise AssertionError("disabled event foundation must not call provider")
        def get_events(self, slugs, from_time, to_time):
            raise AssertionError("disabled event foundation must not call provider")

    result = capture_external_event_intelligence(
        settings=SimpleNamespace(opip_event_store_enabled=False),
        capture_started_at=datetime.now(timezone.utc),
        state_path=tmp_path / "state.json",
        identity_registry_path=tmp_path / "identity.json",
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        cryptopanic_client=ExplodingClient(),
        coinmarketcal_client=ExplodingClient(),
    )
    assert not result.enabled
    assert not result.ran
    assert result.telemetry["events_persisted"] == 0


def test_event_foundation_files_do_not_reference_order_or_notification_actions():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EVENT_PACKAGE.rglob("*.py")
    )
    forbidden_tokens = (
        "place_order",
        "cancel_order",
        "send_trade_plan",
        "send_price_movement_update",
        "qualified_alerts(",
        "publish_qualified_long(",
    )
    for token in forbidden_tokens:
        assert token not in text


def test_event_capture_runs_after_real_protection_and_critical_discovery():
    source = (ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    cycle_source = source[source.index("def _run_cycle_once()") : source.index("def main()")]
    active = cycle_source.index("monitor_active_main()")
    broad = cycle_source.index("_run_broad_discovery_if_due(", active)
    retry = cycle_source.index("_run_qualified_alert_retry_fail_open(", broad)
    recheck = cycle_source.index("_run_entry_watch_recheck_fail_open()", retry)
    pending = cycle_source.index("monitor_pending_main()", recheck)
    early = cycle_source.index("_run_early_watch_if_due(", pending)
    paper = cycle_source.index("_run_paper_monitor_fail_open()", early)
    event = cycle_source.index("_run_event_intelligence_fail_open(settings=settings)", paper)
    assert active < broad < retry < recheck < pending < early < paper < event


def test_current_opportunity_scanner_does_not_consume_event_store():
    source = (ROOT / "app" / "jobs" / "scan_opportunities.py").read_text(
        encoding="utf-8"
    )
    assert "app.opip.events.storage" not in source
    assert "get_visible_events(" not in source
    assert "EventStore(" not in source


def test_existing_news_and_catalyst_modules_do_not_depend_on_event_store():
    for relative in (
        "app/scanner/news_context.py",
        "app/scanner/scheduled_catalysts.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "app.opip.events" not in source
        assert "get_visible_events(" not in source


def test_production_compose_enables_event_intelligence_shadow_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    core = compose.split("  ohm-trade-agent:", 1)[1]
    assert 'OPIP_EVENT_STORE_ENABLED: "true"' in core
    settings = Settings(webhook_secret="123456789012")
    assert settings.opip_event_store_enabled is False
