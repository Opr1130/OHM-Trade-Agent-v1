"""Market ingestion for the O'Pip feature bus (PR3 slices B and C).

Layering inside this package:

* ``instruments`` — instrument version identity, pure given reference data.
* ``observations`` — pure normalization of venue rows into observations.
* ``aggregates`` — pure one-minute grid alignment, gap and coverage semantics.
* ``source`` — the ``MarketObservationSource`` interface and the Kraken pilot,
  the only module here permitted to touch an exchange client.
"""

from __future__ import annotations

__all__: list[str] = []
