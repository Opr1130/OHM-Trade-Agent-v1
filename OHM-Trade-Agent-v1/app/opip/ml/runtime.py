"""Provider-neutral shadow evidence adapter contract.

Foundation v1 deliberately defines only an interface. Concrete model-library
dependencies arrive in a later build after point-in-time data, labels, dataset
construction, and validation gates are proven.
"""
from __future__ import annotations

from typing import Protocol

from app.opip.ml.contracts import FeatureSnapshot, ModelEvidence


class ModelAdapter(Protocol):
    """Evidence-only scoring boundary with no trading side effects."""

    @property
    def model_id(self) -> str:
        ...

    @property
    def model_family(self) -> str:
        ...

    @property
    def model_version(self) -> str:
        ...

    def score(self, snapshot: FeatureSnapshot) -> ModelEvidence:
        """Score one immutable snapshot and return immutable typed evidence."""
        ...
