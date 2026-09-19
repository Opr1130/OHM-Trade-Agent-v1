"""B/C-3 Paper v2 activation gate.

Paper v2 is **inactive** unless an operator explicitly selects it. This module is
the single place that answers "may the Paper v2 paper-execution path run?", so
every producer and scheduler seam resolves the same answer the same way.

The gate is fail-closed by construction:

* only the exact mode ``active`` enables the path;
* every other value - including a value this module does not recognise, a missing
  field, an empty string, or a non-string - resolves to ``off``;
* a malformed environment value fails ``Settings`` parsing outright, so a typo
  cannot silently activate execution.

Activation grants **paper-only** authority. The Paper v2 path writes canonical
paper evidence through the frozen B/C-1/B/C-2 writer APIs. It does not create
funded or live orders, does not reach the exchange client, does not widen
credentials, and does not change Kraken authority. This module deliberately
imports nothing but the settings accessor and ``typing``, so those surfaces are
not reachable through the activation gate.
"""

from __future__ import annotations

from typing import Any

PAPER_V2_MODE_OFF = "off"
PAPER_V2_MODE_ACTIVE = "active"

#: The only values this gate recognises. Anything else is treated as inactive.
PAPER_V2_MODES = frozenset({PAPER_V2_MODE_OFF, PAPER_V2_MODE_ACTIVE})


def resolve_paper_v2_mode(settings: Any | None = None) -> str:
    """Resolve the Paper v2 activation mode, always fail-closed.

    Mirrors the existing writer/feature-bus mode resolvers: accepts a Settings
    object, any object exposing the field, or no argument at all (in which case
    the process settings are consulted).

    Only the exact canonical value activates. The comparison is deliberately not
    normalized: stripping or lower-casing would let a malformed host value such as
    ``"Active"``, ``"ACTIVE"`` or ``" active "`` enable a live execution path, which
    contradicts the documented contract. Any other value, any missing field and
    any non-string resolves to ``off``.
    """
    if settings is not None:
        raw = getattr(settings, "opip_paper_v2_mode", PAPER_V2_MODE_OFF)
    else:
        try:
            from app.core.config import get_settings

            raw = get_settings().opip_paper_v2_mode
        except Exception:
            # An unreadable configuration is not a reason to activate execution.
            raw = PAPER_V2_MODE_OFF
    if isinstance(raw, str) and raw in PAPER_V2_MODES:
        return raw
    return PAPER_V2_MODE_OFF


def paper_v2_active(settings: Any | None = None) -> bool:
    """Whether the Paper v2 paper-execution path may run at all."""
    return resolve_paper_v2_mode(settings) == PAPER_V2_MODE_ACTIVE


__all__ = [
    "PAPER_V2_MODE_ACTIVE",
    "PAPER_V2_MODE_OFF",
    "PAPER_V2_MODES",
    "paper_v2_active",
    "resolve_paper_v2_mode",
]
