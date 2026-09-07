"""Feature flags for Issue #223 early detection.

Every flag defaults dark and follows the existing
``opip_funnel_telemetry_enabled`` environment convention so no ``Settings``
contract changes and no compose changes are required to merge safely.

Two flags gate production behaviour and are independently revertible:

``OPIP_EARLY_VALIDATION_PARITY_ENABLED``
    activates the repaired Early Watch validation (real ticker context, so the
    existing mandatory bad-print and staleness rejections stop no-opping) and
    requires a ``QUALIFIED`` evidence grade before a card is alert-eligible.
    Strictly tightening: it can only remove candidates, never add them.

``OPIP_EARLY_SELECTOR_PROMOTED``
    would make the reserved-cohort selector authoritative for production
    Stage-0 selection. It must stay off until every gate in
    :mod:`app.opip.early.promotion` is satisfied by real evidence.

The remaining flags gate shadow evidence writes only.
"""

from __future__ import annotations

import os
from typing import Mapping

#: Production: repaired Early Watch validation + fail-closed qualification.
VALIDATION_PARITY_FLAG = "OPIP_EARLY_VALIDATION_PARITY_ENABLED"
#: Production: reserved-cohort selector becomes authoritative. Keep dark.
SELECTOR_PROMOTED_FLAG = "OPIP_EARLY_SELECTOR_PROMOTED"
#: Shadow: persist reserved-cohort challenger decisions for later A/B.
SELECTOR_SHADOW_FLAG = "OPIP_EARLY_SELECTOR_SHADOW_ENABLED"
#: Shadow: persist the fine-timeframe lead-time experiment.
TIMEFRAME_SHADOW_FLAG = "OPIP_EARLY_TIMEFRAME_SHADOW_ENABLED"
#: Shadow: persist point-in-time milestone ledger rows.
TIMING_LEDGER_FLAG = "OPIP_EARLY_TIMING_LEDGER_ENABLED"

_TRUE = frozenset({"1", "true", "yes", "on"})


def _flag(name: str, environ: Mapping[str, str] | None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "false")).strip().lower() in _TRUE


def early_validation_parity_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether repaired Early Watch validation gates operator promotion.

    Enabling this can only reduce the candidate set. It never widens
    discovery, never loosens a threshold, and never grants trade authority.
    """
    return _flag(VALIDATION_PARITY_FLAG, environ)


def early_selector_promoted(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the reserved-cohort selector is authoritative in production.

    Must remain false until :func:`app.opip.early.promotion.evaluate_promotion`
    reports ``PROMOTION_ELIGIBLE`` against real captured evidence.
    """
    return _flag(SELECTOR_PROMOTED_FLAG, environ)


def early_selector_shadow_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether shadow selector decisions are persisted. Measurement only."""
    return _flag(SELECTOR_SHADOW_FLAG, environ)


def early_timeframe_shadow_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the fine-timeframe experiment is persisted. Measurement only."""
    return _flag(TIMEFRAME_SHADOW_FLAG, environ)


def early_timing_ledger_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether milestone ledger rows are persisted. Measurement only."""
    return _flag(TIMING_LEDGER_FLAG, environ)


def flag_state(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Report every Issue #223 flag for diagnostics and PR evidence."""
    return {
        VALIDATION_PARITY_FLAG: early_validation_parity_enabled(environ),
        SELECTOR_PROMOTED_FLAG: early_selector_promoted(environ),
        SELECTOR_SHADOW_FLAG: early_selector_shadow_enabled(environ),
        TIMEFRAME_SHADOW_FLAG: early_timeframe_shadow_enabled(environ),
        TIMING_LEDGER_FLAG: early_timing_ledger_enabled(environ),
    }
