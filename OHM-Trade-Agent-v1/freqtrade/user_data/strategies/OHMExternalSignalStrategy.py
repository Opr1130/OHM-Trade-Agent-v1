from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from pandas import DataFrame

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


BRIDGE_DIR = Path("/freqtrade/bridge")
SIGNALS_FILE = BRIDGE_DIR / "signals.json"
CONTROL_FILE = BRIDGE_DIR / "control.json"


class OHMExternalSignalStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False
    process_only_new_candles = False
    startup_candle_count = 1

    minimal_roi = {"0": 10.0}
    stoploss = -0.50
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False

    position_adjustment_enable = True
    max_entry_position_adjustment = 0

    order_types = {
        "entry": "limit",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "limit",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}
    unfilledtimeout = {"entry": 1440, "exit": 10, "unit": "minutes"}

    _control: dict[str, Any] = {}
    _signals_by_id: dict[str, dict[str, Any]] = {}
    _signals_by_pair: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _reload_bridge(self) -> None:
        self._control = self._read_json(CONTROL_FILE)
        payload = self._read_json(SIGNALS_FILE)
        by_id: dict[str, dict[str, Any]] = {}
        by_pair: dict[str, list[dict[str, Any]]] = {}
        for row in payload.get("signals", []):
            if not isinstance(row, dict):
                continue
            signal_id = str(row.get("signal_id") or "")
            pair = str(row.get("pair") or "")
            if not signal_id or not pair or str(row.get("direction") or "").upper() != "LONG":
                continue
            by_id[signal_id] = row
            by_pair.setdefault(pair, []).append(row)
        for rows in by_pair.values():
            rows.sort(key=lambda item: str(item.get("decision_at") or ""), reverse=True)
        self._signals_by_id = by_id
        self._signals_by_pair = by_pair

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        self._reload_bridge()
        try:
            Path("/tmp/ohm_freqtrade_heartbeat").write_text(
                current_time.astimezone(timezone.utc).isoformat(),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _enabled(self) -> bool:
        return bool(self._control.get("enabled")) and (
            str(self._control.get("authoritative_engine") or "FREQTRADE_DRY_RUN")
            == "FREQTRADE_DRY_RUN"
        )

    def _signal_by_id(self, signal_id: str | None) -> dict[str, Any] | None:
        if not signal_id:
            return None
        return self._signals_by_id.get(str(signal_id))

    def _active_signal(self, pair: str, current_time: datetime) -> dict[str, Any] | None:
        if not self._enabled():
            return None
        current = current_time.astimezone(timezone.utc)
        for row in self._signals_by_pair.get(pair, []):
            decision = self._parse_time(row.get("decision_at"))
            expiry = self._parse_time(row.get("expires_at"))
            if decision is None or expiry is None:
                continue
            if decision <= current <= expiry:
                return row
        return None

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None
        if dataframe.empty:
            return dataframe
        now = datetime.now(timezone.utc)
        signal = self._active_signal(str(metadata.get("pair") or ""), now)
        if signal is None:
            return dataframe
        dataframe.loc[dataframe.index[-1], "enter_long"] = 1
        dataframe.loc[dataframe.index[-1], "enter_tag"] = str(signal["signal_id"])
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        if side != "long" or not self._enabled():
            return False
        signal = self._signal_by_id(entry_tag)
        if signal is None or signal.get("pair") != pair:
            return False
        expiry = self._parse_time(signal.get("expires_at"))
        if expiry is None or current_time.astimezone(timezone.utc) > expiry:
            return False
        try:
            stop = float(signal["stop_price"])
            chase = float(signal["chase_limit"])
            requested = float(rate)
        except (KeyError, TypeError, ValueError):
            return False
        if not (stop < requested <= chase):
            return False

        # A signal is one-shot even across bot restarts.
        for prior in Trade.get_trades_proxy(pair=pair):
            if str(getattr(prior, "enter_tag", "") or "") == str(entry_tag):
                return False
        return True

    def custom_entry_price(
        self,
        pair: str,
        trade: Trade | None,
        current_time: datetime,
        proposed_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        signal = self._signal_by_id(entry_tag)
        if signal is None or side != "long":
            return proposed_rate
        try:
            price = float(signal["entry_price"])
            stop = float(signal["stop_price"])
            chase = float(signal["chase_limit"])
        except (KeyError, TypeError, ValueError):
            return proposed_rate
        if stop < price <= chase:
            return price
        return proposed_rate

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        signal = self._signal_by_id(entry_tag)
        if signal is None or side != "long":
            return 0.0
        try:
            requested = float(signal["stake_amount"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        if requested <= 0:
            return 0.0
        return min(requested, float(max_stake))

    def check_entry_timeout(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        signal = self._signal_by_id(getattr(trade, "enter_tag", None))
        if signal is None:
            return True
        expiry = self._parse_time(signal.get("expires_at"))
        return expiry is None or current_time.astimezone(timezone.utc) > expiry

    def order_filled(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: datetime,
        **kwargs,
    ) -> None:
        if order.ft_order_side == trade.entry_side:
            signal = self._signal_by_id(getattr(trade, "enter_tag", None))
            if signal is not None:
                for key in (
                    "signal_id",
                    "episode_id",
                    "cohort_id",
                    "journey_id",
                    "stop_price",
                    "target_1",
                    "target_2",
                    "max_hold_hours",
                ):
                    trade.set_custom_data(key=f"ohm_{key}", value=signal.get(key))
                trade.set_custom_data(key="ohm_tp1_done", value=False)
        elif (
            order.ft_order_side == trade.exit_side
            and str(getattr(order, "ft_order_tag", "") or "") == "ohm_tp1"
        ):
            trade.set_custom_data(key="ohm_tp1_done", value=True)

    def _trade_value(self, trade: Trade, key: str, default: Any = None) -> Any:
        value = trade.get_custom_data(key=f"ohm_{key}", default=None)
        if value is not None:
            return value
        signal = self._signal_by_id(getattr(trade, "enter_tag", None))
        return signal.get(key, default) if signal else default

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        try:
            stop = float(self._trade_value(trade, "stop_price"))
        except (TypeError, ValueError):
            return None
        if stop <= 0 or stop >= current_rate:
            return None
        return stoploss_from_absolute(
            stop,
            current_rate=current_rate,
            is_short=False,
            leverage=float(getattr(trade, "leverage", 1.0) or 1.0),
        )

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        if bool(trade.get_custom_data(key="ohm_tp1_done", default=False)):
            return None
        try:
            target_1 = float(self._trade_value(trade, "target_1"))
        except (TypeError, ValueError):
            return None
        if current_rate >= target_1 and trade.stake_amount > 0:
            return -(float(trade.stake_amount) * 0.5), "ohm_tp1"
        return None

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        try:
            target_2 = float(self._trade_value(trade, "target_2"))
        except (TypeError, ValueError):
            target_2 = 0.0
        if target_2 > 0 and current_rate >= target_2:
            return "ohm_tp2"

        try:
            max_hold_hours = int(self._trade_value(trade, "max_hold_hours", 24))
        except (TypeError, ValueError):
            max_hold_hours = 24
        opened = getattr(trade, "date_entry_fill_utc", None) or trade.open_date_utc
        if opened and current_time.astimezone(timezone.utc) >= opened + timedelta(
            hours=max(1, max_hold_hours)
        ):
            return "ohm_time_exit"
        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return 1.0
