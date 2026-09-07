"""Point-in-time prior-observation and persistence evidence for Issue #223.

Reuses the bounded full-universe ``history_by_symbol`` ring buffer already
maintained by :mod:`app.services.full_market_observation` and the qualifying-
scan persistence counter from :mod:`app.services.signal_features`. Nothing
here synthesises persistence: a first sighting yields zero prior
observations and no persistence count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.opip.early.shadow_observer import OBSERVATION_STATE_FILE, load_observation_history
from app.services.signal_features import (
    FeatureDerivationConfig,
    ObservationSnapshot,
    derive_symbol_features,
    snapshot_from_mapping,
)

OBSERVATION_CONTEXT_VERSION = "opip-early-observation-context-v1"

#: Fiat and stable quotes that may be stripped to recover a base-asset alias.
#: Longest first so ``ETHUSDT`` becomes ``ETH``, not ``ETHU``. Crypto quotes
#: such as ``BTC`` / ``ETH`` are excluded: ``ETHBTC`` is a distinct instrument.
_FIAT_STABLE_QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP")


@dataclass(frozen=True)
class ObservationContext:
    """Per-base-asset prior-observation and persistence evidence."""

    version: str
    prior_observation_counts: Mapping[str, int]
    persistence_scans: Mapping[str, int]
    history_symbols: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "history_symbols": int(self.history_symbols),
            "prior_observation_counts": dict(self.prior_observation_counts),
            "persistence_scans": dict(self.persistence_scans),
            "synthesised": False,
            "trade_authority_changed": False,
            "production_selection_changed": False,
        }


def _snapshots_for(rows: Sequence[Mapping[str, Any]]) -> list[ObservationSnapshot]:
    snapshots: list[ObservationSnapshot] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        snapshot = snapshot_from_mapping(row, observed_at=row.get("observed_at") or row.get("recorded_at"))
        if snapshot is not None:
            snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item.observed_at)
    return snapshots


def _base_asset_key(symbol: str) -> str:
    """Uppercase a history or pair key. Quote stripping is not done here.

    Full-market history is keyed by the display pair. Base-asset aliases are
    derived only from the fiat/stable quote set in
    :func:`build_observation_context`. Crypto quote suffixes are never
    stripped, so ``ETHBTC`` cannot contaminate persistence for ``ETH``.
    """
    return str(symbol or "").strip().upper()


def _aliases_for_history_key(symbol: str) -> set[str]:
    aliases = {symbol}
    for quote in _FIAT_STABLE_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            aliases.add(symbol[: -len(quote)])
            break
    return aliases


def build_observation_context(
    *,
    state_path: Path | None = None,
    history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    config: FeatureDerivationConfig | None = None,
) -> ObservationContext:
    """Derive prior-observation counts and persistence from existing history.

    Persistence uses the same qualifying-scan definition Signal Quality v1
    already tests. Prior observation count is simply how many earlier scans
    exist for the symbol; it is never invented.
    """
    resolved = history if history is not None else load_observation_history(state_path or OBSERVATION_STATE_FILE)
    derivation = config or FeatureDerivationConfig()
    priors: dict[str, int] = {}
    persistence: dict[str, int] = {}

    for key, rows in resolved.items():
        symbol = _base_asset_key(key)
        snapshots = _snapshots_for(rows)
        aliases = _aliases_for_history_key(symbol)
        prior_count = max(0, len(snapshots) - 1)
        features = derive_symbol_features(snapshots, config=derivation)
        persistence_count = (
            int(features.consecutive_qualifying_scans) if features.valid else 0
        )
        for alias in aliases:
            priors[alias] = max(priors.get(alias, 0), prior_count)
            if features.valid:
                persistence[alias] = max(persistence.get(alias, 0), persistence_count)

    return ObservationContext(
        version=OBSERVATION_CONTEXT_VERSION,
        prior_observation_counts=priors,
        persistence_scans=persistence,
        history_symbols=len(resolved),
    )
