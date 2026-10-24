"""Strict, credential-free Tradevisor V2 ingress evidence.

Tradevisor is *candidate evidence only*. This model has no field — and no
downstream consumer — that can place, modify, confirm or cancel an order, set
risk approval, or change portfolio/canonical state. Those capabilities are not
representable here, so a malicious or buggy sender cannot smuggle them in
through the wire contract.

The wire contract is an owner-authored TradingView alert template
(``schema_version = tradevisor-ingress-v1``), not a vendor-native format.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SUPPORTED_SCHEMA_VERSION = "tradevisor-ingress-v1"


class TradevisorIngressV1(BaseModel):
    """One Tradevisor V2 BUY/SELL alert message, strictly validated.

    ``extra="forbid"`` is the primary injection control: risk approval, order
    permission, entry authorization, AI authority or any other unexpected key
    is rejected outright instead of being silently ignored.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )

    schema_version: Literal["tradevisor-ingress-v1"]
    source: Literal["TRADEVISOR"]
    indicator: Literal["TRADEVISOR_V2"]
    signal: Literal["BUY", "SELL"]
    symbol: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9._/-]+$")
    exchange: str = Field(min_length=3, max_length=24, pattern=r"^[A-Za-z0-9._-]+$")
    # TradingView {{interval}} resolves to a minute count ("60") for 1H. A
    # human alias such as "1H" is also accepted and normalized centrally.
    timeframe: str = Field(min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    signal_price: float = Field(gt=0)
    bar_opened_at: datetime
    triggered_at: datetime
    alert_config_version: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    #: Operator/vendor-declared version of the EXTERNAL indicator script.
    #:
    #: Optional so every already-configured TradingView alert keeps working.
    #: It is provenance only: an alert body cannot prove what the external
    #: Pine script contains, so this value is recorded as an unverified claim
    #: and never as a Certified source revision. It is deliberately excluded
    #: from the replay identity (see ``dedup_id_for``).
    indicator_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9._+-]+$",
    )
