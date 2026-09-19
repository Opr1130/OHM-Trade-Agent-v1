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
``sys`` module, which is not reloaded, as a single ``(pid -> identifier)``
registry guarded by a lock that lives there too.

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
import threading
import uuid

#: Domain prefix distinguishing a process-instance identifier from snapshot,
#: episode, instrument or trade identities.
PROCESS_INSTANCE_PREFIX = "PROC"

_HOLDER_ATTRIBUTE = "_opip_process_instance_holder"


def _new_holder() -> dict:
    """A fresh process-scoped holder: one shared lock plus the PID registry."""
    return {"lock": threading.Lock(), "identities": {}}


def _process_holder() -> dict:
    """Return the one process-scoped holder, publishing it atomically.

    ``sys.__dict__.setdefault`` is atomic under the GIL, so concurrent first callers
    all receive the *same* holder even though several may construct a candidate. A
    check-then-``setattr`` pattern did not guarantee that: two threads could each
    publish their own holder, and each would then own a different lock and a
    different registry, so one process could mint two identities.

    Keeping the holder on ``sys`` rather than in this module is what makes it survive
    ``importlib.reload``: a module global is module-object scoped, so a reload would
    hand the same process a second identity.
    """
    holder = sys.__dict__.get(_HOLDER_ATTRIBUTE)
    if isinstance(holder, dict):
        return holder
    return sys.__dict__.setdefault(_HOLDER_ATTRIBUTE, _new_holder())


def process_instance_id() -> str:
    """Return this process's ``PROC:`` identifier, minting it on first use.

    The value is computed once per (process, PID) and reused for that process
    lifetime, so every record a process emits shares one instance identity. A
    separate process - or the same code after a restart - mints its own.

    Both the holder publication and first mint are synchronized, so concurrent first
    callers in one process cannot observe two different identifiers.
    """
    pid = os.getpid()
    holder = _process_holder()
    registry = holder["identities"]
    existing = registry.get(pid)
    if existing is not None:
        return existing
    with holder["lock"]:
        # Re-check under the lock: another thread may have minted while we waited.
        existing = registry.get(pid)
        if existing is not None:
            return existing
        minted = f"{PROCESS_INSTANCE_PREFIX}:{uuid.uuid4()}"
        registry[pid] = minted
        return minted


__all__ = ["PROCESS_INSTANCE_PREFIX", "process_instance_id"]
