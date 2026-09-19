"""Process-instance identity for canonical evidence provenance.

Emitter provenance needs one honest answer to "which process instance produced
this record?". O'Pip has no deployment-provided process identity, so this module
mints the smallest truthful one: a single ``PROC:`` identifier per Python
process, stable for that process's lifetime.

Scope and limits, deliberately narrow:

* It is **emitter provenance only**. It never enters a semantic identity, an
  idempotency key, or any decision fact, so a restart cannot change what a record
  means - only which process emitted it. The canonical writer already strips
  ``process_instance_id`` when comparing same-key payloads for that reason.
* A new process (including a restart) receives a new identifier, because it
  genuinely is a different process instance.
* It is not a trade, order, or decision identity and must never be used as one.

The identifier is a UUID4 minted once per process. Randomness is acceptable and
intended here precisely because this value is provenance rather than semantic
identity; using a clock or a counter would imply an ordering that does not exist.
"""

from __future__ import annotations

import uuid

#: Domain prefix distinguishing a process-instance identifier from snapshot,
#: episode, instrument or trade identities.
PROCESS_INSTANCE_PREFIX = "PROC"

_process_instance_id: str | None = None


def process_instance_id() -> str:
    """Return this process's ``PROC:`` identifier, minting it on first use.

    The value is computed once and reused for the process lifetime, so every
    record a process emits shares one instance identity. A separate process - or
    the same code after a restart - mints its own.
    """
    global _process_instance_id
    if _process_instance_id is None:
        _process_instance_id = (
            f"{PROCESS_INSTANCE_PREFIX}:{uuid.uuid4()}"
        )
    return _process_instance_id


__all__ = ["PROCESS_INSTANCE_PREFIX", "process_instance_id"]
