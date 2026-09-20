"""Immutable Paper-v2 execution-model economics.

A fill's cost depends on the fee rate and slippage assumption, and those must be
immutable for a given execution lineage. Reading them from mutable runtime
settings at fill-construction time broke that: an attempt could commit, the
process could restart, an operator could change a setting, and the same
deterministic fill identity would then be rebuilt with different economics - a
canonical payload conflict, or worse, silently different costs.

Economics are therefore resolved from the immutable economic-model version the
execution already carries, not from settings:

    opip-paper-economics-v2  ->  fee 0.4%, slippage 10 bps

``registry_paper_economics()`` fails closed on an unknown version rather than
substituting a default, so a version that has no frozen definition can never
silently acquire one.

Relationship to runtime settings
--------------------------------

``paper_trade_fee_rate`` and ``paper_trade_slippage_bps`` still exist and are still
validated at load time (see :func:`assert_runtime_economics_match_model`), so an
operator who configures a different fee learns immediately that this model version
does not describe it. What they no longer do is *reinterpret a historical
execution*: the producer resolves economics by version, so a settings change cannot
alter an already-committed attempt's fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.paper_execution import PAPER_ECONOMIC_MODEL_VERSION


@dataclass(frozen=True)
class PaperExecutionEconomics:
    """One frozen set of execution-cost assumptions."""

    economic_model_version: str
    fee_rate: float
    slippage_bps: float

    def cost_components(self, quantity: float, price: float) -> dict[str, float]:
        """Cost components for one fill. Deterministic in its inputs alone."""
        notional = float(quantity) * float(price)
        return {
            "fee_cost": notional * self.fee_rate,
            "spread_cost": 0.0,
            "slippage_cost": notional * (self.slippage_bps / 10_000.0),
            "other_supported_cost": 0.0,
        }


#: The frozen economic registry. Adding a version here is the *only* way to change
#: execution-cost assumptions, so an existing version can never drift, and an
#: existing trade stays bound to the coefficients it was executed under.
_PAPER_ECONOMICS: Mapping[str, PaperExecutionEconomics] = MappingProxyType(
    {
        PAPER_ECONOMIC_MODEL_VERSION: PaperExecutionEconomics(
            economic_model_version=PAPER_ECONOMIC_MODEL_VERSION,
            # The approved B/C-2 contract: 0.4% fee, 10 bps slippage.
            fee_rate=0.004,
            slippage_bps=10.0,
        )
    }
)


def paper_economics_for_version(version: str) -> PaperExecutionEconomics:
    """Resolve the frozen economics for a version, failing closed when unknown.

    An unrecognised version must never be treated as "use the current settings":
    that would reintroduce exactly the mutable-economics defect this exists to
    remove, and would let a historical execution be reinterpreted.
    """
    key = str(version or "").strip()
    try:
        return _PAPER_ECONOMICS[key]
    except KeyError as exc:
        raise ValueError(
            f"no frozen execution economics for economic_model_version {key!r}"
        ) from exc


def assert_runtime_economics_match_model(settings: Any) -> None:
    """Fail closed when configured economics disagree with the frozen model.

    Called where settings are the intended input (a fresh execution's assumptions),
    so a misconfiguration surfaces immediately instead of being discovered as a
    canonical conflict later. It never rewrites history.
    """
    frozen = paper_economics_for_version(PAPER_ECONOMIC_MODEL_VERSION)
    configured_fee = float(getattr(settings, "paper_trade_fee_rate", frozen.fee_rate))
    configured_slippage = float(
        getattr(settings, "paper_trade_slippage_bps", frozen.slippage_bps)
    )
    if abs(configured_fee - frozen.fee_rate) > 1e-12:
        raise ValueError(
            "configured paper_trade_fee_rate does not match the frozen economic "
            f"model {PAPER_ECONOMIC_MODEL_VERSION}"
        )
    if abs(configured_slippage - frozen.slippage_bps) > 1e-12:
        raise ValueError(
            "configured paper_trade_slippage_bps does not match the frozen "
            f"economic model {PAPER_ECONOMIC_MODEL_VERSION}"
        )


__all__ = [
    "PaperExecutionEconomics",
    "assert_runtime_economics_match_model",
    "paper_economics_for_version",
]
