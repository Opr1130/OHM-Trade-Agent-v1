"""Committee enablement gates.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The committee ships dark. ``off`` is the default and ``shadow`` is the only
value that permits committee work; both are research-only. Nothing here can
enable a trading path, and an unreadable or malformed configuration resolves to
``off`` rather than to an enabled committee.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMMITTEE_MODE_OFF = "off"
COMMITTEE_MODE_SHADOW = "shadow"
COMMITTEE_MODES = frozenset({COMMITTEE_MODE_OFF, COMMITTEE_MODE_SHADOW})


@dataclass(frozen=True)
class CommitteeShadowSettings:
    """Explicit, injected enablement for callers and tests.

    The execution API refuses to run unless the committee is enabled, so a caller
    that intends committee work states that intent here rather than depending on
    ambient process settings. Mirrors the Feature Bus shadow-settings helper.
    """

    opip_committee_mode: str = COMMITTEE_MODE_SHADOW
    opip_committee_max_estimated_cost_microunits: int = 0


def resolve_committee_mode(settings: Any | None = None) -> str:
    """Resolve the committee mode, failing closed to ``off``."""
    if settings is not None:
        mode = str(getattr(settings, "opip_committee_mode", COMMITTEE_MODE_OFF) or COMMITTEE_MODE_OFF)
    else:
        try:
            from app.core.config import get_settings

            mode = str(get_settings().opip_committee_mode or COMMITTEE_MODE_OFF)
        except Exception:
            mode = COMMITTEE_MODE_OFF
    normalized = mode.strip().lower()
    return normalized if normalized in COMMITTEE_MODES else COMMITTEE_MODE_OFF


def committee_shadow_enabled(settings: Any | None = None) -> bool:
    """Whether committee work is permitted at all. Research-only when true."""
    return resolve_committee_mode(settings) == COMMITTEE_MODE_SHADOW


def resolve_committee_cost_ceiling(settings: Any | None = None) -> int | None:
    """Declared cost ceiling in microunits, or ``None`` when none is declared."""
    raw: Any = None
    if settings is not None:
        raw = getattr(settings, "opip_committee_max_estimated_cost_microunits", 0)
    else:
        try:
            from app.core.config import get_settings

            raw = get_settings().opip_committee_max_estimated_cost_microunits
        except Exception:
            raw = 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return None if value <= 0 else value


__all__ = [
    "COMMITTEE_MODE_OFF",
    "COMMITTEE_MODE_SHADOW",
    "COMMITTEE_MODES",
    "CommitteeShadowSettings",
    "committee_shadow_enabled",
    "resolve_committee_cost_ceiling",
    "resolve_committee_mode",
]
