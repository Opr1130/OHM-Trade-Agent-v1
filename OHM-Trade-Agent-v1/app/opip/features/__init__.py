"""Shared feature computation for the O'Pip feature bus (PR3 slices D-G).

One engine, one feature definition. Existing production math is reused through
``indicators`` rather than reimplemented, existing ML snapshot evidence is
adapted through ``ml_bridge`` rather than duplicated, and existing production
feature paths are compared through ``parity`` rather than assumed equivalent.

Nothing in this package places orders, sends alerts, ranks candidates, or
mutates production policy.
"""

from __future__ import annotations

__all__: list[str] = []
