"""Frozen semantic taxonomy for O'Pip alert families (Alert v2).

Alert v2 separates notification planes that must never be conflated:

* ``SYSTEM``             -- operational/health incidents (family ``SYSTEM_HEALTH``)
* ``TRADE_PROTECTION``   -- stop / target / emergency / position warnings
* ``SIGNAL_OPPORTUNITY`` -- signal, movement and qualification alerts
* ``EXTERNAL_OPERATOR``  -- external Kraken order review
* ``COMMAND``            -- interactive command responses

The separation is deliberate and load-bearing:

* an operational failure must never be counted as a signal or opportunity event;
* a signal or opportunity must never be rendered as a system failure;
* a system/attention budget must never suppress a protection event.

This module is pure data plus pure functions. It performs no I/O, owns no alert
authority, and never sends anything. Existing emitters keep their existing
delivery and governance paths; they only gain a single source of truth for
"which plane does this alert belong to".

Legacy family names are preserved as-is. ``MONITOR_DEGRADED`` predates Alert v2
and remains mapped so historical rows stay classifiable, but new operational
incidents are emitted under ``SYSTEM_HEALTH``.
"""

from __future__ import annotations

from enum import Enum


class AlertClass(str, Enum):
    """The notification plane an alert belongs to."""

    SYSTEM = "SYSTEM"
    TRADE_PROTECTION = "TRADE_PROTECTION"
    SIGNAL_OPPORTUNITY = "SIGNAL_OPPORTUNITY"
    EXTERNAL_OPERATOR = "EXTERNAL_OPERATOR"
    COMMAND = "COMMAND"


#: Canonical alert family for every operational/system incident.
SYSTEM_HEALTH_FAMILY = "SYSTEM_HEALTH"

#: Legacy family that previously carried monitoring degradation. It is retained
#: only for classification of historical delivery rows; new operational
#: incidents use :data:`SYSTEM_HEALTH_FAMILY`.
LEGACY_MONITOR_DEGRADED_FAMILY = "MONITOR_DEGRADED"

#: Legacy user-facing branding that must never appear in an automatic Alert v2
#: title. Internal identifiers, filenames and historical evidence are out of
#: scope: only the rendered, user-facing alert surface is constrained.
LEGACY_USER_FACING_BRANDING = (
    "OHM AI",
    "OHM RISK",
    "OHM MONITORING",
    "OHM CHIEF",
    "OHM ENTRY",
    "OHM SETUP",
    "OHM DO NOT CHASE",
    "OHM ORDERS",
    "OHM POSITIONS",
    "OHM MARKET",
    "OHM PAPER",
)

_FAMILY_CLASS: dict[str, AlertClass] = {
    SYSTEM_HEALTH_FAMILY: AlertClass.SYSTEM,
    LEGACY_MONITOR_DEGRADED_FAMILY: AlertClass.SYSTEM,
    "ACTIVE_TRADE": AlertClass.TRADE_PROTECTION,
    "EMERGENCY_RISK": AlertClass.TRADE_PROTECTION,
    "OPIP_EVENT_RISK": AlertClass.TRADE_PROTECTION,
    "EARLY_MOVER": AlertClass.SIGNAL_OPPORTUNITY,
    "BROAD_WATCH": AlertClass.SIGNAL_OPPORTUNITY,
    "QUALIFIED_OPPORTUNITY": AlertClass.SIGNAL_OPPORTUNITY,
    "PRICE_MOVEMENT": AlertClass.SIGNAL_OPPORTUNITY,
    "PENDING_SETUP": AlertClass.SIGNAL_OPPORTUNITY,
    "TRADING_SIGNAL": AlertClass.SIGNAL_OPPORTUNITY,
    "EXTERNAL_ORDER_REVIEW": AlertClass.EXTERNAL_OPERATOR,
    "COMMAND_WATCH": AlertClass.COMMAND,
}


def _families_for(alert_class: AlertClass) -> frozenset[str]:
    return frozenset(
        family for family, value in _FAMILY_CLASS.items() if value is alert_class
    )


#: Families that may contribute to signal/opportunity statistics.
SIGNAL_OPPORTUNITY_FAMILIES = _families_for(AlertClass.SIGNAL_OPPORTUNITY)
#: Operational/system families.
SYSTEM_FAMILIES = _families_for(AlertClass.SYSTEM)
#: Lifecycle-critical trade/protection families.
TRADE_PROTECTION_FAMILIES = _families_for(AlertClass.TRADE_PROTECTION)
#: External/operator families.
EXTERNAL_OPERATOR_FAMILIES = _families_for(AlertClass.EXTERNAL_OPERATOR)
#: Interactive command families.
COMMAND_FAMILIES = _families_for(AlertClass.COMMAND)


def normalize_family(alert_family: str | None) -> str:
    return str(alert_family or "").strip().upper()


def alert_class_for_family(alert_family: str | None) -> AlertClass:
    """Return the notification plane for a family.

    Unknown families default to :attr:`AlertClass.COMMAND`, which is the only
    plane that participates in neither signal/opportunity statistics nor
    trade/protection authority nor system incident governance.
    """

    return _FAMILY_CLASS.get(normalize_family(alert_family), AlertClass.COMMAND)


def is_signal_opportunity_family(alert_family: str | None) -> bool:
    return alert_class_for_family(alert_family) is AlertClass.SIGNAL_OPPORTUNITY


def is_system_family(alert_family: str | None) -> bool:
    return alert_class_for_family(alert_family) is AlertClass.SYSTEM


def is_trade_protection_family(alert_family: str | None) -> bool:
    return alert_class_for_family(alert_family) is AlertClass.TRADE_PROTECTION


def is_external_operator_family(alert_family: str | None) -> bool:
    return alert_class_for_family(alert_family) is AlertClass.EXTERNAL_OPERATOR


def contains_legacy_user_facing_branding(text: str | None) -> bool:
    """Return whether rendered user-facing text still carries legacy branding."""

    haystack = str(text or "")
    return any(label in haystack for label in LEGACY_USER_FACING_BRANDING)
