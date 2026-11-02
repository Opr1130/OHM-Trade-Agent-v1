"""Config-driven directional technical score V2, with no production authority.

This is a pure, opt-in evaluator. Existing V1 selectors and trading gates do
not import this module. Missing evidence is unscorable, never bullish evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CONFIG = Path(__file__).with_name("technical_score_v2.json")
REQUIRED_WEIGHTS = (
    "higher_timeframe_adx",
    "ema_stack_extension",
    "relative_volume",
    "rsi",
    "macd_acceleration",
    "volatility_percentile",
)
REQUIRED_PARAMETERS = (
    "adx_min",
    "rvol_zero",
    "rvol_full",
    "clv_confirmation",
    "rsi_lower_zero",
    "rsi_lower_full",
    "rsi_upper_full",
    "rsi_upper_zero",
    "extension_full_atr",
    "extension_zero_atr",
    "atr_rank_low_zero",
    "atr_rank_low_full",
    "atr_rank_high_full",
    "atr_rank_high_zero",
)


@dataclass(frozen=True)
class V2Config:
    version: str
    mode: str
    threshold: float
    weights: Mapping[str, float]
    parameters: Mapping[str, float]


@dataclass(frozen=True)
class V2Features:
    """Point-in-time features not fully present in legacy MarketSnapshot."""
    higher_timeframe_ema50_rising: bool | None
    adx: float | None
    candle_close_location: float | None
    macd_histogram_rising: bool | None
    atr_percentile: float | None
    higher_timeframe_ema50_falling: bool | None = None
    macd_histogram_falling: bool | None = None


@dataclass(frozen=True)
class V2Score:
    version: str
    mode: str
    score: float | None
    threshold: float
    components: Mapping[str, float]
    missing: tuple[str, ...]

    @property
    def status(self) -> str:
        return "READY" if not self.missing else "INSUFFICIENT_FEATURES"

    @property
    def threshold_met(self) -> bool:
        return self.score is not None and self.score >= self.threshold


def _clamp(x: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, x))


def _ramp(value: float, lower: float, upper: float) -> float:
    return _clamp((value - lower) / (upper - lower))


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"V2_CONFIG_INVALID_{name.upper()}")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError(f"V2_CONFIG_NONFINITE_{name.upper()}")
    return f


def load_v2_config(path: Path | None = None) -> V2Config:
    """Read a single version-controlled configuration; reject unsafe drift.

    A configuration file is not permission to change live trading authority.
    """
    raw = json.loads((path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"version", "mode", "threshold", "weights", "parameters"}:
        raise ValueError("V2_CONFIG_SCHEMA")
    if raw["version"] != "TECHNICAL_SCORE_V2_1" or raw["mode"] != "shadow":
        raise ValueError("V2_CONFIG_AUTHORITY")
    threshold = _number(raw["threshold"], "threshold")
    if not 0 <= threshold <= 100:
        raise ValueError("V2_CONFIG_THRESHOLD")
    weights = raw["weights"]
    params = raw["parameters"]
    if not isinstance(weights, dict) or set(weights) != set(REQUIRED_WEIGHTS):
        raise ValueError("V2_CONFIG_WEIGHTS")
    if not isinstance(params, dict) or set(params) != set(REQUIRED_PARAMETERS):
        raise ValueError("V2_CONFIG_PARAMETERS")
    weights = {name: _number(value, name) for name, value in weights.items()}
    params = {name: _number(value, name) for name, value in params.items()}
    if any(weight < 0 or weight > 100 for weight in weights.values()) or not math.isclose(sum(weights.values()), 100, abs_tol=1e-8):
        raise ValueError("V2_CONFIG_WEIGHT_TOTAL")
    for names in (
        ("rvol_zero", "rvol_full"),
        ("rsi_lower_zero", "rsi_lower_full", "rsi_upper_full", "rsi_upper_zero"),
        ("extension_full_atr", "extension_zero_atr"),
        ("atr_rank_low_zero", "atr_rank_low_full", "atr_rank_high_full", "atr_rank_high_zero"),
    ):
        if any(params[left] >= params[right] for left, right in zip(names, names[1:])):
            raise ValueError("V2_CONFIG_RANGES")
    if not 0 < params["clv_confirmation"] <= 1 or not 0 <= params["adx_min"] <= 100:
        raise ValueError("V2_CONFIG_BOUNDS")
    if params["atr_rank_low_zero"] < 0 or params["atr_rank_high_zero"] > 100:
        raise ValueError("V2_CONFIG_ATR_RANK")
    return V2Config(raw["version"], raw["mode"], threshold, weights, params)


def _score_directional_v2(snapshot: Any, features: V2Features, config: V2Config, direction: str) -> V2Score:
    """Evaluate supplied direction using point-in-time features, with no I/O.

    Does not choose candidates, create orders, send Telegram, or call AI.
    """
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("INVALID_V2_DIRECTION")
    is_long = direction == "LONG"
    inputs: dict[str, Any] = {
        "higher_timeframe_ema50_aligned": (features.higher_timeframe_ema50_rising if is_long else features.higher_timeframe_ema50_falling),
        "adx": features.adx,
        "candle_close_location": features.candle_close_location,
        "macd_histogram_accelerating": (features.macd_histogram_rising if is_long else features.macd_histogram_falling),
        "atr_percentile": features.atr_percentile,
        "last_price": getattr(snapshot, "last_price", None),
        "ema20": getattr(snapshot, "ema20", None),
        "ema50": getattr(snapshot, "ema50", None),
        "ema200": getattr(snapshot, "ema200", None),
        "atr": getattr(snapshot, "atr", None),
        "rsi": getattr(snapshot, "rsi", None),
        "volume_ratio": getattr(snapshot, "volume_ratio", None),
        "macd_line": getattr(snapshot, "macd_line", None),
        "macd_signal": getattr(snapshot, "macd_signal", None),
    }
    missing: list[str] = []
    for name, value in inputs.items():
        if value is None:
            missing.append(name)
        elif isinstance(value, bool):
            if name not in ("higher_timeframe_ema50_aligned", "macd_histogram_accelerating"):
                missing.append(name)
        elif not isinstance(value, (float, int)) or not math.isfinite(float(value)):
            missing.append(name)
    if missing:
        return V2Score(config.version, config.mode, None, config.threshold, {}, tuple(dict.fromkeys(missing)))
    if (
        inputs["atr"] <= 0
        or inputs["last_price"] <= 0
        or not 0 <= features.candle_close_location <= 1
        or not 0 <= features.atr_percentile <= 100
        or not 0 <= features.adx <= 100
    ):
        return V2Score(config.version, config.mode, None, config.threshold, {}, ("INVALID_FEATURE_VALUE",))

    p, w = config.parameters, config.weights
    price = float(inputs["last_price"])
    ema20, ema50, ema200 = float(inputs["ema20"]), float(inputs["ema50"]), float(inputs["ema200"])
    extension_atr = ((price - ema20) if is_long else (ema20 - price)) / float(inputs["atr"])
    ext_factor = 1 - _ramp(extension_atr, p["extension_full_atr"], p["extension_zero_atr"])
    htf = (0.5 if inputs["higher_timeframe_ema50_aligned"] else 0) + (0.5 if features.adx > p["adx_min"] else 0)
    ema_alignment = (
        (0.2 if (price > ema20 if is_long else price < ema20) else 0)
        + (0.4 if (ema20 > ema50 if is_long else ema20 < ema50) else 0)
        + (0.4 if (ema50 > ema200 if is_long else ema50 < ema200) else 0)
    )
    volume = _ramp(float(inputs["volume_ratio"]), p["rvol_zero"], p["rvol_full"])
    directional_close_location = (float(features.candle_close_location) if is_long else 1 - float(features.candle_close_location))
    close_factor = _clamp(directional_close_location / p["clv_confirmation"])
    rsi = float(inputs["rsi"]) if is_long else 100 - float(inputs["rsi"])
    if rsi < p["rsi_lower_full"]:
        rsi_factor = _ramp(rsi, p["rsi_lower_zero"], p["rsi_lower_full"])
    elif rsi <= p["rsi_upper_full"]:
        rsi_factor = 1.0
    else:
        rsi_factor = 1 - _ramp(rsi, p["rsi_upper_full"], p["rsi_upper_zero"])
    macd_confirmed = (inputs["macd_line"] > inputs["macd_signal"] if is_long else inputs["macd_line"] < inputs["macd_signal"])
    macd = (0.5 + (0.5 if inputs["macd_histogram_accelerating"] else 0)) if macd_confirmed else 0
    atr_rank = float(features.atr_percentile)
    if atr_rank < p["atr_rank_low_full"]:
        atr_factor = _ramp(atr_rank, p["atr_rank_low_zero"], p["atr_rank_low_full"])
    elif atr_rank <= p["atr_rank_high_full"]:
        atr_factor = 1.0
    else:
        atr_factor = 1 - _ramp(atr_rank, p["atr_rank_high_full"], p["atr_rank_high_zero"])
    components = {
        "higher_timeframe_adx": round(w["higher_timeframe_adx"] * htf, 3),
        "ema_stack_extension": round(w["ema_stack_extension"] * ema_alignment * ext_factor, 3),
        "relative_volume": round(w["relative_volume"] * volume * close_factor, 3),
        "rsi": round(w["rsi"] * rsi_factor, 3),
        "macd_acceleration": round(w["macd_acceleration"] * macd, 3),
        "volatility_percentile": round(w["volatility_percentile"] * atr_factor, 3),
    }
    score = round(_clamp(sum(components.values()) / 100) * 100, 2)
    return V2Score(config.version, config.mode, score, config.threshold, components, ())


def score_long_v2(snapshot: Any, features: V2Features, config: V2Config) -> V2Score:
    """Pure LONG scoring; no qualification or trading permission."""
    return _score_directional_v2(snapshot, features, config, "LONG")


def score_short_v2(snapshot: Any, features: V2Features, config: V2Config) -> V2Score:
    """Pure bearish scoring with mirrored RSI and price/EMA requirements."""
    return _score_directional_v2(snapshot, features, config, "SHORT")
