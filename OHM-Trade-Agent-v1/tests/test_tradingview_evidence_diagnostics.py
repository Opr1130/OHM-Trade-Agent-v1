from datetime import datetime, timedelta, timezone

from app.services.tradingview_evidence_diagnostics import analyze_tradingview_registry


NOW = datetime(2026, 8, 19, 4, 40, tzinfo=timezone.utc)


def _row(symbol, direction, age_seconds, *, status="PROCESSED", disposition="QUALIFIED_FOR_NATIVE_CYCLE"):
    return {
        "status": status,
        "final_disposition": disposition,
        "received_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "payload": {"symbol": symbol, "direction": direction},
    }


def test_diagnostics_separate_recent_and_stale_qualified_evidence():
    registry = {
        "events": {
            "a": _row("KRAKEN:BTCUSD", "LONG", 120),
            "b": _row("KRAKEN:LINKUSD", "LONG", 1200),
            "c": _row("KRAKEN:ETHUSD", "SHORT", 60, disposition="REJECTED"),
        }
    }
    report = analyze_tradingview_registry(registry, now=NOW, freshness_seconds=900)
    assert report["processed_qualified_total"] == 2
    assert report["recent_qualified"] == 1
    assert report["stale_or_future_qualified"] == 1
    assert report["qualified_pairs"] == ["BTCUSD", "LINKUSD"]
    assert report["qualified_directions"]["BTCUSD"] == ["LONG"]
    assert report["recent_evidence"][0]["pair"] == "BTCUSD"


def test_diagnostics_normalize_xbt_alias_to_btc():
    registry = {"events": {"a": _row("KRAKEN:XBTUSD", "LONG", 100)}}
    report = analyze_tradingview_registry(registry, now=NOW, freshness_seconds=900)
    assert report["qualified_pairs"] == ["BTCUSD"]
