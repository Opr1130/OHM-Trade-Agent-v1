from __future__ import annotations

from types import SimpleNamespace

from app.services import telegram_command_center as commands
from app.services import telegram_market_insights as insights


class FakePairsClient:
    def __init__(self, pairs):
        self.pairs = pairs

    def get_asset_pairs(self):
        return self.pairs


def _details(base, quote, altname, wsname):
    return {
        "base": base,
        "quote": quote,
        "altname": altname,
        "wsname": wsname,
        "status": "online",
    }


def _settings():
    return SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id=None,
        telegram_command_rate_limit_per_minute=12,
    )


def test_bare_asset_ending_usd_prefers_exact_base_match():
    client = FakePairsClient(
        {
            "BUSDUSD": _details("BUSD", "ZUSD", "BUSDUSD", "BUSD/USD"),
            "BUSD": _details("B", "ZUSD", "BUSD", "B/USD"),
        }
    )
    resolved = insights.resolve_market("BUSD", client)
    assert resolved.base_asset == "BUSD"
    assert resolved.display_pair == "BUSDUSD"


def test_explicit_pair_still_resolves_base_plus_quote():
    client = FakePairsClient(
        {
            "BTCUSD": _details("XXBT", "ZUSD", "XBTUSD", "XBT/USD"),
        }
    )
    resolved = insights.resolve_market("BTC/USD", client)
    assert resolved.base_asset == "BTC"
    assert resolved.quote_asset == "USD"


def test_plain_unwatch_asset_ending_usd_is_not_suffix_stripped(monkeypatch):
    removed = []
    sent = []
    monkeypatch.setattr(commands, "_delete_watch", lambda symbol: removed.append(symbol) or True)
    monkeypatch.setattr(commands, "_send", lambda settings, text: sent.append(text) or True)

    commands._handle_unwatch(_settings(), "BUSD")

    assert removed == ["BUSD"]
    assert "BUSD removed" in sent[0]
