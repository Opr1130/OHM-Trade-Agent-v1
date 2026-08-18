from types import SimpleNamespace

from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_position_verification import KrakenPositionVerifier


def _trade(**overrides):
    values = {
        "symbol": "WLDUSD",
        "entry_price": 0.3458,
        "stop_price": 0.3384,
        "target_1": 0.36,
        "target_2": 0.38,
        "risk_level": "medium",
        "trade_id": "OHM-WLD",
        "capital": 100.0,
    }
    values.update(overrides)
    return ActiveTrade(**values)


class Client:
    enabled = True

    def __init__(self, balances=None, positions=None):
        self.balances = balances or {}
        self.positions = positions or {}

    def assert_read_only(self):
        return SimpleNamespace(name="ohm-read-only")

    def get_balance(self):
        return self.balances

    def get_open_positions(self):
        return self.positions


def test_zero_spot_balance_is_absent():
    verifier = KrakenPositionVerifier(Client(balances={"WLD": 0.0, "ZUSD": 500.0}))
    verifier.refresh()

    result = verifier.verify(_trade())

    assert result.status == "ABSENT"
    assert result.observed_quantity == 0.0


def test_meaningful_spot_balance_is_verified():
    verifier = KrakenPositionVerifier(Client(balances={"WLD": 200.0}))
    verifier.refresh()

    result = verifier.verify(_trade())

    assert result.status == "VERIFIED"
    assert result.observed_quantity == 200.0


def test_residual_dust_below_one_percent_of_expected_quantity_is_absent():
    verifier = KrakenPositionVerifier(Client(balances={"WLD": 1.0}))
    verifier.refresh()

    result = verifier.verify(_trade(capital=100.0, entry_price=0.3458))

    assert result.status == "ABSENT"


def test_open_short_position_is_verified():
    positions = {
        "P1": {
            "pair": "WLDUSD",
            "type": "sell",
            "vol": "100",
            "vol_closed": "25",
        }
    }
    verifier = KrakenPositionVerifier(Client(positions=positions))
    verifier.refresh()

    result = verifier.verify(_trade(direction="SHORT"))

    assert result.status == "VERIFIED"
    assert result.observed_quantity == 75.0


def test_missing_short_position_is_absent():
    verifier = KrakenPositionVerifier(Client())
    verifier.refresh()

    result = verifier.verify(_trade(direction="SHORT"))

    assert result.status == "ABSENT"


def test_missing_credentials_are_unavailable_and_never_verified():
    client = Client()
    client.enabled = False
    verifier = KrakenPositionVerifier(client)
    verifier.refresh()

    result = verifier.verify(_trade())

    assert result.status == "UNAVAILABLE"
    assert result.verified is False
