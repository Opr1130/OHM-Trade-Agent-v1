"""Explicit, fail-closed paper-registry genesis.

Why this boundary exists
------------------------

``paper_trading/state.json`` is simultaneously:

* the paper lifecycle registry, and
* a canonical learning-replica **completeness companion** - without it the
  replica cannot prove outbox completeness and therefore cannot certify a
  complete canonical paper-outcome population.

Two locally-correct semantics met without a migration step between them:

* ``app.services.registry_io.load_json`` returns ``{}`` when a registry file does
  not exist, so the trade path has always read a missing registry as "no
  lifecycle state yet"; and
* the canonical replica contract treats a missing lifecycle state as
  "completeness cannot be proven" and fails closed.

Neither is wrong. The defect was the missing genesis boundary: an installation
that legitimately has zero paper lifecycle activity had no way to declare an
*initialized empty* registry, so the canonical replica export failed closed
forever (``CANONICAL_REPLICA_PAPER_STATE_MISSING``).

What this module does and does not do
-------------------------------------

It creates the canonical empty registry **only** when virgin state is provable,
and it records durable initialization provenance so that a later disappearance of
the registry is never mistaken for virginity.

It deliberately does **not**:

* weaken or bypass the replica contract (``require_paper_state`` stays True);
* synthesize lifecycle completeness, or invent paper activity;
* reinterpret a missing registry as an empty one;
* reconstruct, repair or delete any lifecycle evidence;
* quarantine or rewrite an existing registry (a corrupt registry is reported and
  refused, never replaced).

A refusal is an operator-visible integrity incident, not a retryable condition.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sys
from typing import Any

from app.opip.canonical.gap_spool import GapSpoolError, load_gap_spool
from app.services.paper_trade_registry import (
    EVIDENCE_GAP_SPOOL_FILE,
    EVENT_FILE,
    STATE_FILE,
    registry_state_lock,
    write_empty_registry_locked,
)
from app.services.registry_io import registry_lock, save_json_atomic

logger = logging.getLogger(__name__)

#: Durable initialization marker. It lives in host operational state (alongside
#: ``last-good-sha``) rather than in the lifecycle data root: the marker must
#: outlive the registry it protects, and it must never be part of the evidence a
#: reader could be handed.
DEFAULT_MARKER_PATH = Path("/var/lib/ohm-deploy/paper-registry-initialized-v1")

GENESIS_MARKER_KIND = "paper_registry_initialized_v1"
GENESIS_MARKER_VERSION = 1

#: Outcomes.
GENESIS_INITIALIZED = "INITIALIZED"
GENESIS_ALREADY_INITIALIZED = "ALREADY_INITIALIZED"
GENESIS_ADOPTED_EXISTING = "ADOPTED_EXISTING"
GENESIS_REFUSED = "REFUSED"

#: Stable refusal / status reasons.
REASON_STATE_PRESENT_VALID = "PAPER_REGISTRY_STATE_PRESENT_VALID"
REASON_INITIALIZED_NOW = "PAPER_REGISTRY_GENESIS_INITIALIZED"
REASON_EVIDENCE_EXISTS = "PAPER_REGISTRY_GENESIS_REFUSED_EVIDENCE_EXISTS"
REASON_STATE_LOST = "PAPER_REGISTRY_STATE_LOST_OR_UNPROVABLE"
REASON_STATE_CORRUPT = "PAPER_REGISTRY_GENESIS_REFUSED_STATE_CORRUPT"
REASON_NOT_PROVABLE = "PAPER_REGISTRY_GENESIS_REFUSED_NOT_PROVABLE"

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

#: Bases recorded in the marker, so provenance distinguishes a first-ever
#: initialization from adopting a registry that already existed.
BASIS_GENESIS = "GENESIS"
BASIS_ADOPTED = "ADOPTED_EXISTING_STATE"


class PaperRegistryStateCorruptError(RuntimeError):
    """The existing registry cannot be validated, so genesis must not touch it."""


@dataclass(frozen=True)
class GenesisOutcome:
    """One genesis attempt's result, in a form the deploy can report."""

    status: str
    reason: str
    detail: str
    marker_path: Path
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.status == GENESIS_REFUSED


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    """Reject a naive datetime rather than localizing it.

    Mirrors the replica contract: host-local interpretation of a naive stamp
    would silently shift recorded provenance.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    moment = _require_utc(value, field_name="timestamp")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_registry_payload(path: Path) -> dict[str, Any]:
    """Structurally validate an existing registry **without mutating it**.

    Deliberately does not use ``registry_io.load_json``: that helper quarantines a
    malformed registry by renaming it, and genesis must never move an operator's
    evidence. A corrupt registry is reported and refused instead.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PaperRegistryStateCorruptError(f"registry unreadable: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PaperRegistryStateCorruptError(f"registry is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PaperRegistryStateCorruptError("registry must be a JSON object")
    rows = payload.get("lifecycles")
    if rows is None:
        raise PaperRegistryStateCorruptError("registry has no lifecycles object")
    if not isinstance(rows, dict):
        raise PaperRegistryStateCorruptError("registry lifecycles must be an object")
    return payload


def _count_paper_events(event_file: Path) -> tuple[int, str | None]:
    """Count durable paper lifecycle events, or report why it is unprovable.

    Any non-empty line that cannot be parsed is treated as unprovable rather than
    as "no events": a partially written line is itself evidence that something
    happened, and this proof exists precisely to avoid a false virgin claim.
    """
    if not event_file.exists():
        return 0, None
    try:
        text = event_file.read_text(encoding="utf-8")
    except OSError as exc:
        return 0, f"paper event stream unreadable: {exc}"
    count = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            return 0, f"paper event stream has an unparseable row: {exc}"
        if not isinstance(row, dict):
            return 0, "paper event stream has a non-object row"
        count += 1
    return count, None


def _unresolved_gap_count(gap_spool_file: Path) -> tuple[int, str | None]:
    """Count unresolved paper evidence gaps, or report why it is unprovable."""
    try:
        spool = load_gap_spool(gap_spool_file)
    except GapSpoolError as exc:
        return 0, f"paper evidence gap spool is corrupt: {exc}"
    except Exception as exc:  # noqa: BLE001 - any read failure is unprovable
        return 0, f"paper evidence gap spool unreadable: {exc}"
    unresolved = spool.get("unresolved")
    if not isinstance(unresolved, list):
        return 0, "paper evidence gap spool has no unresolved list"
    return len(unresolved), None


def _canonical_outcome_count(canonical_db: Path | None) -> tuple[int, bool, str | None]:
    """Count canonical terminal paper outcomes, or report why it is unprovable.

    A missing canonical store is **not** treated as "no outcomes". The store is
    the authoritative economic evidence plane and is initialized by the writer
    service; its absence is an anomaly, and treating it as empty is exactly the
    "a file that disappeared must never silently become empty" failure. So
    absence is unprovable, not virgin.
    """
    from app.opip.canonical.paths import db_path as canonical_db_path
    from app.opip.learning.paper_outcome_reader import (
        PaperOutcomeIntegrityError,
        PaperOutcomeSourceUnavailableError,
        read_canonical_paper_outcomes,
    )

    target = Path(canonical_db) if canonical_db is not None else canonical_db_path()
    if not target.exists():
        return 0, False, f"canonical outcome store is absent at {target}"
    try:
        read = read_canonical_paper_outcomes(target)
    except PaperOutcomeSourceUnavailableError as exc:
        return 0, False, f"canonical outcome store unavailable: {exc}"
    except PaperOutcomeIntegrityError as exc:
        return 0, False, f"canonical outcome store unreadable: {exc}"
    return len(read.outcomes), bool(read.stream_present), None


def _write_marker(
    marker_path: Path,
    *,
    basis: str,
    recorded_at: datetime,
    release_sha: str | None,
) -> None:
    """Durably record that the paper registry has been initialized.

    Written with the repository's existing atomic durable write primitive (the
    same one every registry uses), and written *after* the registry itself so a
    crash between the two leaves the recoverable ordering: registry present,
    marker missing - which genesis resolves by adopting the existing registry,
    never by refusing as "lost".
    """
    payload: dict[str, Any] = {
        "kind": GENESIS_MARKER_KIND,
        "schema_version": GENESIS_MARKER_VERSION,
        "paper_only": True,
        "basis": basis,
        "recorded_at_utc": _iso_z(recorded_at),
        "release_sha": release_sha if (release_sha and _SHA_PATTERN.match(release_sha)) else None,
    }
    save_json_atomic(Path(marker_path), payload)


@dataclass(frozen=True)
class _VirginProof:
    """Outcome of the virgin-state proof across every durable paper evidence source."""

    virgin: bool
    reason: str | None
    detail: str
    facts: dict[str, Any]


def _prove_virgin(
    *,
    event_file: Path,
    gap_spool_file: Path,
    canonical_db: Path | None,
) -> _VirginProof:
    """Prove that no durable O'Pip paper lifecycle evidence exists.

    Checked twice by the caller - once before taking the state lock as a fast
    path, and again *inside* the lock immediately before creating the registry.
    The second check is the authoritative one: evidence committed after the
    pre-check must still block creation, or an empty registry could be written
    over real activity and then falsely certify completeness.
    """
    events, events_error = _count_paper_events(event_file)
    gaps, gaps_error = _unresolved_gap_count(gap_spool_file)
    outcomes, stream_present, canonical_error = _canonical_outcome_count(canonical_db)
    facts: dict[str, Any] = {
        "paper_event_rows": events,
        "paper_event_error": events_error,
        "unresolved_gap_rows": gaps,
        "gap_spool_error": gaps_error,
        "canonical_terminal_outcomes": outcomes,
        "canonical_stream_present": stream_present,
        "canonical_error": canonical_error,
    }

    unprovable = [
        reason
        for reason in (events_error, gaps_error, canonical_error)
        if reason is not None
    ]
    if unprovable:
        return _VirginProof(
            virgin=False,
            reason=REASON_NOT_PROVABLE,
            detail="; ".join(unprovable),
            facts=facts,
        )

    if events or gaps or outcomes or stream_present:
        return _VirginProof(
            virgin=False,
            reason=REASON_EVIDENCE_EXISTS,
            detail=(
                f"durable paper evidence exists (events={events}, "
                f"unresolved_gaps={gaps}, canonical_outcomes={outcomes}, "
                f"canonical_stream={stream_present}); refusing to create an empty registry"
            ),
            facts=facts,
        )

    return _VirginProof(
        virgin=True,
        reason=None,
        detail="no durable paper lifecycle evidence exists",
        facts=facts,
    )


def _adopt_existing_registry_if_valid(
    *,
    state_file: Path,
    marker_path: Path,
    marker_present: bool,
    stamp: datetime,
    release_sha: str | None,
    facts: dict[str, Any],
) -> GenesisOutcome | None:
    """Adopt an existing valid registry, refusing a corrupt one.

    Returns ``None`` when no registry exists, so the caller continues to genesis.
    A corrupt registry is never rewritten, replaced or quarantined - it is reported
    and refused.

    Called both before taking the state lock (fast path) and again inside it, so
    one implementation covers sequential adoption, concurrent adoption and the
    corrupt case identically.
    """
    if not state_file.exists():
        return None
    try:
        _validate_registry_payload(state_file)
    except PaperRegistryStateCorruptError as exc:
        logger.critical(
            "paper registry genesis refused: existing registry is invalid (%s)", exc
        )
        return GenesisOutcome(
            status=GENESIS_REFUSED,
            reason=REASON_STATE_CORRUPT,
            detail=str(exc),
            marker_path=marker_path,
            facts={**facts, "state_present": True, "state_valid": False},
        )
    # A valid registry is never rewritten or reformatted. Adoption only records
    # durable provenance, so a later disappearance cannot be read as virginity.
    basis = BASIS_GENESIS if marker_present else BASIS_ADOPTED
    _write_marker(marker_path, basis=basis, recorded_at=stamp, release_sha=release_sha)
    return GenesisOutcome(
        status=GENESIS_ALREADY_INITIALIZED if marker_present else GENESIS_ADOPTED_EXISTING,
        reason=REASON_STATE_PRESENT_VALID,
        detail="paper registry already present and valid",
        marker_path=marker_path,
        facts={**facts, "state_present": True, "state_valid": True, "basis": basis},
    )


def ensure_paper_registry_initialized(
    *,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
    gap_spool_file: Path = EVIDENCE_GAP_SPOOL_FILE,
    canonical_db: Path | None = None,
    marker_path: Path = DEFAULT_MARKER_PATH,
    release_sha: str | None = None,
    now: datetime | None = None,
) -> GenesisOutcome:
    """Ensure the paper registry exists, or refuse with a stable reason.

    Idempotent: an existing valid registry is never rewritten, and repeated
    genesis after success is a no-op. Serially safe: the create decision is made
    under the same state lock lifecycle writes use, so concurrent genesis attempts
    cannot produce conflicting state.
    """
    stamp = _require_utc(now or datetime.now(timezone.utc), field_name="now")
    state_file = Path(state_file)
    marker_path = Path(marker_path)
    marker_present = marker_path.exists()

    facts: dict[str, Any] = {
        "state_file": str(state_file),
        "marker_path": str(marker_path),
        "marker_present": marker_present,
        # The marker is a boolean fact. Whether a *file* exists is not the same
        # as whether a *name* resolves, so both are recorded.
        "marker_exists": marker_present,
    }

    adopted = _adopt_existing_registry_if_valid(
        state_file=state_file,
        marker_path=marker_path,
        marker_present=marker_present,
        stamp=stamp,
        release_sha=release_sha,
        facts=facts,
    )
    if adopted is not None:
        return adopted

    # State is absent. A marker proves it was initialized at some point, so its
    # absence now is potential evidence loss - never virginity.
    if marker_present:
        logger.critical(
            "paper registry genesis refused: initialization marker present but "
            "registry is missing (%s); refusing to recreate an empty registry",
            state_file,
        )
        return GenesisOutcome(
            status=GENESIS_REFUSED,
            reason=REASON_STATE_LOST,
            detail=(
                "initialization marker exists but the paper registry is missing; "
                "treating this as potential evidence loss"
            ),
            marker_path=marker_path,
            facts={**facts, "state_present": False, "state_valid": False},
        )

    # No state and no marker: virgin state must be *proven*, never assumed.
    # This first pass is a fast path; the authoritative proof runs again inside
    # the state lock, immediately before anything is created.
    proof = _prove_virgin(
        event_file=Path(event_file),
        gap_spool_file=Path(gap_spool_file),
        canonical_db=canonical_db,
    )
    facts.update(proof.facts)
    facts["state_present"] = False
    facts["state_valid"] = None
    if not proof.virgin:
        logger.critical("paper registry genesis refused: %s", proof.detail)
        return GenesisOutcome(
            status=GENESIS_REFUSED,
            reason=proof.reason or REASON_NOT_PROVABLE,
            detail=proof.detail,
            marker_path=marker_path,
            facts=facts,
        )

    # Provably virgin. Create under the shared state lock and re-check inside it,
    # so two concurrent attempts cannot both decide to create.
    #
    # The evidence proof is repeated here deliberately. Every paper lifecycle
    # writer persists state.json under this same lock *before* emitting its event
    # or canonical outcome, so holding the lock already excludes the dangerous
    # interleaving; re-proving inside the lock makes that invariant explicit and
    # also covers any evidence writer that does not touch state.json (for example
    # a gap appended by a recovery path). Without it, evidence committed between
    # the pre-check and the write would be silently overwritten by an empty
    # registry that then certifies completeness.
    with registry_lock(registry_state_lock(state_file)):
        adopted = _adopt_existing_registry_if_valid(
            state_file=state_file,
            marker_path=marker_path,
            marker_present=marker_present,
            stamp=stamp,
            release_sha=release_sha,
            facts=facts,
        )
        if adopted is not None:
            # Another attempt won the race; its own marker write is equivalent.
            return adopted

        recheck = _prove_virgin(
            event_file=Path(event_file),
            gap_spool_file=Path(gap_spool_file),
            canonical_db=canonical_db,
        )
        if not recheck.virgin:
            logger.critical(
                "paper registry genesis refused under lock: %s", recheck.detail
            )
            return GenesisOutcome(
                status=GENESIS_REFUSED,
                reason=recheck.reason or REASON_NOT_PROVABLE,
                detail=recheck.detail,
                marker_path=marker_path,
                facts={**facts, **recheck.facts, "state_present": False},
            )

        write_empty_registry_locked(state_file)

    _write_marker(
        marker_path, basis=BASIS_GENESIS, recorded_at=stamp, release_sha=release_sha
    )
    logger.info("paper registry genesis: created empty registry at %s", state_file)
    return GenesisOutcome(
        status=GENESIS_INITIALIZED,
        reason=REASON_INITIALIZED_NOW,
        detail="empty paper registry created from provable virgin state",
        marker_path=marker_path,
        facts={**facts, "state_present": True, "state_valid": True, "basis": BASIS_GENESIS},
    )


def _cmd_genesis(args: argparse.Namespace) -> int:
    outcome = ensure_paper_registry_initialized(
        state_file=Path(args.state_file),
        event_file=Path(args.event_file),
        gap_spool_file=Path(args.gap_spool),
        canonical_db=Path(args.canonical_db) if args.canonical_db else None,
        marker_path=Path(args.marker),
        release_sha=args.release_sha or None,
    )
    # Machine-readable status on stdout so the deploy reports exactly what
    # happened, and so a refusal can never be mistaken for a successful genesis.
    print(f"OPIP_PAPER_REGISTRY_GENESIS={outcome.status}")
    print(f"OPIP_PAPER_REGISTRY_GENESIS_REASON={outcome.reason}")
    print(f"OPIP_PAPER_REGISTRY_GENESIS_MARKER={outcome.marker_path}")
    print(json.dumps(outcome.facts, sort_keys=True))
    if outcome.refused:
        print(f"O'Pip paper registry genesis: REFUSED ({outcome.reason})", file=sys.stderr)
        print(f"  {outcome.detail}", file=sys.stderr)
        return 4
    print(f"O'Pip paper registry genesis: {outcome.status} ({outcome.reason})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="paper_registry_genesis",
        description="Explicit, fail-closed paper registry genesis.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    genesis = sub.add_parser("ensure", help="ensure the paper registry exists")
    genesis.add_argument("--state-file", default=str(STATE_FILE))
    genesis.add_argument("--event-file", default=str(EVENT_FILE))
    genesis.add_argument("--gap-spool", default=str(EVIDENCE_GAP_SPOOL_FILE))
    genesis.add_argument("--canonical-db", default="")
    genesis.add_argument("--marker", default=str(DEFAULT_MARKER_PATH))
    genesis.add_argument("--release-sha", default="")
    genesis.set_defaults(func=_cmd_genesis)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASIS_ADOPTED",
    "BASIS_GENESIS",
    "DEFAULT_MARKER_PATH",
    "GENESIS_ADOPTED_EXISTING",
    "GENESIS_ALREADY_INITIALIZED",
    "GENESIS_INITIALIZED",
    "GENESIS_MARKER_KIND",
    "GENESIS_MARKER_VERSION",
    "GENESIS_REFUSED",
    "GenesisOutcome",
    "PaperRegistryStateCorruptError",
    "REASON_EVIDENCE_EXISTS",
    "REASON_INITIALIZED_NOW",
    "REASON_NOT_PROVABLE",
    "REASON_STATE_CORRUPT",
    "REASON_STATE_LOST",
    "REASON_STATE_PRESENT_VALID",
    "ensure_paper_registry_initialized",
    "main",
]
