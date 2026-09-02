"""R1 regression coverage for the ranking-enabled action-gate path."""

import importlib.util
from pathlib import Path

from app.jobs import scan_opportunities


def _load_profit_ranking_test_module():
    path = Path(__file__).with_name("test_profit_ranking.py")
    spec = importlib.util.spec_from_file_location("r1_profit_ranking_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ranking_enabled_still_applies_action_gate(monkeypatch):
    helpers = _load_profit_ranking_test_module()
    events, _, sent = helpers._configure_pipeline(
        monkeypatch,
        [{"symbol": "RANKEDGATEDUSD", "move": 7.0}],
    )
    settings = scan_opportunities.get_settings()
    settings.opip_global_capital_ranking_enabled = True

    scan_opportunities.main()

    assert "action_gate" in events
    # The real ranking-enabled capacity gate may reject this synthetic candidate
    # for unavailable liquidity capacity. The invariant under test is that the
    # mandatory gate is invoked rather than bypassed.
    assert sent == []



def test_ranking_disabled_still_applies_action_gate(monkeypatch):
    helpers = _load_profit_ranking_test_module()
    events, _, sent = helpers._configure_pipeline(
        monkeypatch,
        [{"symbol": "UNRANKEDGATEDUSD", "move": 7.0}],
    )
    settings = scan_opportunities.get_settings()
    settings.opip_global_capital_ranking_enabled = False

    scan_opportunities.main()

    assert "action_gate" in events
