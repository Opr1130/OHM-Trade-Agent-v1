from types import SimpleNamespace

from app.services import external_order_review as review


class FakePrivateClient:
    enabled = True

    def assert_read_only(self):
        return SimpleNamespace(name="ohm-read-only")

    def get_open_orders(self):
        return {
            "ORDER-OLD-1": {
                "descr": {"pair": "CSPRUSD", "type": "sell", "price": "0.003"},
                "vol": "10000",
                "opentm": 1786377600.0,
            },
            "ORDER-OLD-2": {
                "descr": {"pair": "ADAUSD", "type": "sell", "price": "1.25"},
                "vol": "200",
                "opentm": 1786377601.0,
            },
        }


class FakePublicClient:
    def get_ticker(self, pair):
        return {"last": 0.0022 if pair == "CSPRUSD" else 0.75}


def test_external_orders_send_once_and_remain_unmanaged(monkeypatch, tmp_path):
    monkeypatch.setattr(review, "REVIEW_FILE", tmp_path / "external_order_reviews.json")
    monkeypatch.setattr(review, "LOCK_FILE", tmp_path / ".external_order_reviews.lock")
    monkeypatch.setattr(review, "get_live_order_intents", lambda: [])
    monkeypatch.setattr(
        review,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        ),
    )
    messages = []
    monkeypatch.setattr(
        review,
        "send_tracked_telegram",
        lambda **kwargs: messages.append(kwargs["message"]) or SimpleNamespace(delivered=True, message_id=81),
    )

    first = review.review_external_open_orders(FakePrivateClient(), FakePublicClient())
    second = review.review_external_open_orders(FakePrivateClient(), FakePublicClient())

    assert first.status == "OK"
    assert first.unmatched_orders_seen == 2
    assert first.new_reviews == 2
    assert first.notifications_sent == 2
    assert second.new_reviews == 0
    assert second.notifications_sent == 0
    assert len(messages) == 2
    assert "EXTERNAL / UNMANAGED" in messages[0]
    assert "one-time review only" in messages[0]


def test_ohm_linked_order_is_not_reviewed_as_external(monkeypatch, tmp_path):
    monkeypatch.setattr(review, "REVIEW_FILE", tmp_path / "external_order_reviews.json")
    monkeypatch.setattr(review, "LOCK_FILE", tmp_path / ".external_order_reviews.lock")
    intent = SimpleNamespace(symbol="CSPRUSD", direction="SHORT", limit_price=0.003)
    monkeypatch.setattr(review, "get_live_order_intents", lambda: [intent])
    monkeypatch.setattr(
        review,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        ),
    )
    sent = []
    monkeypatch.setattr(
        review,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(1) or SimpleNamespace(delivered=True, message_id=82),
    )

    result = review.review_external_open_orders(FakePrivateClient(), FakePublicClient())

    assert result.open_orders_seen == 2
    assert result.unmatched_orders_seen == 1
    assert result.notifications_sent == 1
    assert len(sent) == 1
