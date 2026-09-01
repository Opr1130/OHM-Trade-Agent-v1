"""Stable vocabulary for evidence-time instrument identity."""

from __future__ import annotations

from enum import Enum


IDENTITY_REFERENCE_DATA_VERSION = "opip-evidence-identity-v1"


class InstrumentClass(str, Enum):
    SPOT = "SPOT"
    BONDING = "BONDING"
    STAKED = "STAKED"
    DERIVATIVE = "DERIVATIVE"
    LEVERAGED_TOKEN = "LEVERAGED_TOKEN"
    UNKNOWN = "UNKNOWN"


class IdentityResolutionStatus(str, Enum):
    EXACT = "EXACT"
    REFERENCE_ALIAS = "REFERENCE_ALIAS"
    CLASSIFIED_UNRESOLVED = "CLASSIFIED_UNRESOLVED"
    UNKNOWN = "UNKNOWN"


class IdentityProvenance(str, Enum):
    KRAKEN_METADATA = "KRAKEN_METADATA"
    STATIC_REFERENCE = "STATIC_REFERENCE"
    RAW_ONLY = "RAW_ONLY"
