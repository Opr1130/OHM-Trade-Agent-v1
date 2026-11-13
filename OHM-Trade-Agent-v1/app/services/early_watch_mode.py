"""Early Watch notification mode - an explicit release configuration decision.

Why a mode and not a behaviour change
-------------------------------------
Automatic ``EARLY_MOVER`` alerts are the one Early Watch output that reaches a
human without an operator asking. Making them quieter (or louder) changes what
the owner sees in production, so it must be an explicit, named release decision -
never a side effect of an unrelated merge, and never something a measurement
*script* decides on its own.

This module therefore resolves exactly two modes:

``active`` (default)
    Today's behaviour, unchanged. Automatic ``EARLY_MOVER`` notifications are
    delivered exactly as before.

``evidence_only``
    Early Watch keeps detecting, scoring and persisting its observations - the
    rejected and suppressed evidence is preserved, not discarded - but the
    *automatic* ``EARLY_MOVER`` delivery is suppressed. It calls the notification
    boundary and records that it decided not to send.

What evidence_only deliberately does NOT touch:

* **critical safety alerts** (position/protection/emergency paths),
* **qualified entry notifications** (``chief_alert_notifier`` / qualified alert
  outbox),
* **subscribed ``COMMAND_WATCH`` notifications** (an operator explicitly asked to
  watch a market; that is an on-request service, not Early Watch noise).

Those are separate alert families with their own authorities. This module only
governs the automatic Early Watch family.

Unknown values fail closed to ``active``: a typo must never silently silence
alerts, and it must never widen them either.
"""

from __future__ import annotations

import os
from typing import Mapping

MODE_ENV = "OPIP_EARLY_WATCH_MODE"

MODE_ACTIVE = "active"
MODE_EVIDENCE_ONLY = "evidence_only"

ALLOWED_MODES = (MODE_ACTIVE, MODE_EVIDENCE_ONLY)

#: Emitted whenever an automatic Early Watch delivery is withheld, so a
#: suppressed observation is still auditable rather than silently dropped.
SUPPRESSED_PREFIX = "OPIP_EARLY_WATCH_SUPPRESSED="


def resolve_early_watch_mode(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the configured Early Watch mode, failing closed to ``active``."""
    source = os.environ if environ is None else environ
    raw = str(source.get(MODE_ENV, "") or "").strip().lower()
    if raw == MODE_EVIDENCE_ONLY:
        return MODE_EVIDENCE_ONLY
    return MODE_ACTIVE


def automatic_notifications_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Whether automatic ``EARLY_MOVER`` delivery is permitted."""
    return resolve_early_watch_mode(environ) == MODE_ACTIVE


def suppression_evidence_line(
    identity: str,
    *,
    mode: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """The durable marker for one withheld automatic Early Watch notification."""
    resolved = mode if mode in ALLOWED_MODES else resolve_early_watch_mode(environ)
    return f"{SUPPRESSED_PREFIX}{identity} mode={resolved}"
