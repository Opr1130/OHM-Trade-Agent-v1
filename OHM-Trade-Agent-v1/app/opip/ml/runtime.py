"""Provider-neutral shadow model adapter contract.

No concrete XGBoost/LightGBM dependency is imported in foundation v1. Model
families plug into this interface later after datasets and validation are ready.
"""
from __future__ import annotations

from typing import Mapping, Protocol


class ModelAdapter(Protocol):
    @property
    def model_id(self) -> str:
        ...

    @property
    def model_family(self) -> str:
        ...

    def score(self, features: Mapping[str, object]) -> Mapping[str, float | None]:
        """Return evidence-only scores; no side effects or trading authority."""
        ...
