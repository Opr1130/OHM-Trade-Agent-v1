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

Why the sentinel lives on ``sys``
---------------------------------

"One identity per process lifetime" has to survive ``importlib.reload`` of this
module: a reload re-executes the module body and rebinds its globals, so a module
global is not process-scoped at all - it is module-object scoped, and a reload
would hand the same process a second identity. The state is therefore kept on the
``sys`` module, which is not reloaded, as a single ``(pid, identifier)`` sentinel.

Keying on the PID is what makes the value honestly per-process:

* a reload sees the same PID and returns the same identifier;
* a genuine restart is a new process with a new PID and mints its own;
* a forked child inherits the sentinel but not the PID, so it mints its own
  rather than inheriting and reusing its parent's identity.

The identifier is a UUID4 minted once. Randomness is acceptable and intended
precisely because this value is provenance rather than semantic identity; a clock
or counter would imply an ordering that does not exist.
"""

from __future__ import annotations

import os
import sys
import uuid

#: Domain prefix distinguishing a process-instance identifier from snapshot,
#: episode, instrument or trade identities.
PROCESS_INSTANCE_PREFIX = "PROC"

_SENTINEL_ATTRIBUTE = "_opip_process_instance_identity"


def process_instance_id() -> str:
    """Return this process's ``PROC:`` identifier, minting it on first use.

    The value is computed once per (process, PID) and reused for that process
    lifetime, so every record a process emits shares one instance identity. A
    separate process - or the same code after a restart - mints its own.
    """
    pid = os.getpid()
    sentinel = getattr(sys, _SENTINEL_ATTRIBUTE, None)
    if isinstance(sentinel, tuple) and len(sentinel) == 2 and sentinel[0] == pid:
        return sentinel[1]

    # ``dict.setdefault`` is atomic under the GIL, so two threads racing on first
    # use still receive one identifier: the loser gets the winner's value rather
    # than a second one. A plain check-then-set would not guarantee that.
    registry = getattr(sys, _SENTINEL_ATTRIBUTE, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(sys, _SENTINEL_ATTRIBUTE, registry)
    return registry.setdefault(pid, f"{PROCESS_INSTANCE_PREFIX}:{uuid.uuid4()}")


__all__ = ["PROCESS_INSTANCE_PREFIX", "process_instance_id"]
