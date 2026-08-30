"""Provider-neutral shadow evidence adapter contract.

Foundation v1 deliberately defines only an interface. Concrete model-library
dependencies arrive in a later build after point-in-time data, labels, dataset
construction, and validation gates are proven.
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
