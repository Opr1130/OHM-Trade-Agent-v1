"""Deterministic reconnect/backoff policy for O'Pip BUILD 4.2."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class BackoffPolicy:
    minimum_seconds: float = 0.5
    maximum_seconds: float = 30.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.20

    def __post_init__(self) -> None:
        values = (
            self.minimum_seconds,
            self.maximum_seconds,
            self.multiplier,
            self.jitter_ratio,
        )
        if not all(math.isfinite(float(item)) for item in values):
            raise ValueError("backoff values must be finite")
        if self.minimum_seconds <= 0:
            raise ValueError("minimum_seconds must be positive")
        if self.maximum_seconds < self.minimum_seconds:
            raise ValueError("maximum_seconds must be >= minimum_seconds")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be in [0, 1]")

    def delay_for(self, attempt: int, *, jitter_unit: float = 0.0) -> float:
        """Return bounded delay for zero-based retry attempt.

        jitter_unit is injected in [-1, 1] so tests never depend on
        randomness. Runtime callers may map a random sample into that range.
        """
        if int(attempt) < 0:
            raise ValueError("attempt cannot be negative")
        if not math.isfinite(float(jitter_unit)) or not -1 <= jitter_unit <= 1:
            raise ValueError("jitter_unit must be finite in [-1, 1]")
        retry_attempt = int(attempt)
        if self.multiplier == 1 or self.maximum_seconds == self.minimum_seconds:
            base = self.minimum_seconds
        else:
            log_multiplier = math.log(self.multiplier)
            saturation_attempt = max(
                0,
                math.ceil(
                    (math.log(self.maximum_seconds) - math.log(self.minimum_seconds))
                    / log_multiplier
                ),
            )
            effective_attempt = min(retry_attempt, saturation_attempt)
            if effective_attempt >= saturation_attempt:
                base = self.maximum_seconds
            else:
                base = min(
                    self.maximum_seconds,
                    math.exp(
                        math.log(self.minimum_seconds)
                        + effective_attempt * log_multiplier
                    ),
                )
        jitter = base * self.jitter_ratio * float(jitter_unit)
        return max(0.0, min(self.maximum_seconds, base + jitter))
