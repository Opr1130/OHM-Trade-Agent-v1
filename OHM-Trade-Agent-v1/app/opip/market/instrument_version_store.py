"""Durable InstrumentVersion reconstruction from canonical evidence (PR3).

Instrument-version history is additive event traffic on the existing generic
events table — no physical schema bump. Callers hydrate an
``InstrumentVersionRegistry`` from committed payloads before refresh, then
commit any newly minted version before dependent observations.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.opip.contracts.events import MARKET_INSTRUMENT_VERSION_RECORDED
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.serialization import iso_z
from app.opip.contracts.temporal import require_utc
from app.opip.identity.contract import (
    IdentityProvenance,
    IdentityResolutionStatus,
    InstrumentClass,
)
from app.opip.market.instruments import InstrumentVersionRegistry


def instrument_version_from_payload(payload: Mapping[str, Any]) -> InstrumentVersion:
    """Rebuild one InstrumentVersion from a committed canonical payload."""
    return InstrumentVersion(
        venue=str(payload["venue"]),
        base_asset=str(payload["base_asset"]),
        quote_currency=str(payload["quote_currency"]),
        venue_instrument_id=str(payload["venue_instrument_id"]),
        version=int(payload["version"]),
        reference_data_version=str(payload["reference_data_version"]),
        observed_at_utc=require_utc(
            datetime.fromisoformat(
                str(payload["observed_at_utc"]).replace("Z", "+00:00")
            ),
            field_name="observed_at_utc",
        ),
        instrument_class=InstrumentClass(str(payload.get("instrument_class") or "SPOT")),
        resolution_status=IdentityResolutionStatus(
            str(payload.get("resolution_status") or "EXACT")
        ),
        provenance=IdentityProvenance(
            str(payload.get("provenance") or "KRAKEN_METADATA")
        ),
        price_decimals=(
            int(payload["price_decimals"])
            if payload.get("price_decimals") is not None
            else None
        ),
        tick_size=(
            float(payload["tick_size"]) if payload.get("tick_size") is not None else None
        ),
        min_order_size=(
            float(payload["min_order_size"])
            if payload.get("min_order_size") is not None
            else None
        ),
        attributes={
            str(key): str(value)
            for key, value in dict(payload.get("attributes") or {}).items()
        },
    )


def reconstruct_instrument_version_registry(
    payloads: Sequence[Mapping[str, Any]],
    *,
    reference_data_version: str | None = None,
) -> InstrumentVersionRegistry:
    """Latest committed version wins per instrument_key (monotonic versions).

    Same version with a different reference fingerprint is integrity corruption
    and fails closed. Same version with the same fingerprint is idempotent.
    """
    latest: dict[str, InstrumentVersion] = {}
    for payload in payloads:
        version = instrument_version_from_payload(payload)
        existing = latest.get(version.instrument_key)
        if existing is None:
            latest[version.instrument_key] = version
            continue
        if version.version < existing.version:
            continue
        if version.version > existing.version:
            latest[version.instrument_key] = version
            continue
        if version.reference_fingerprint() != existing.reference_fingerprint():
            raise ValueError(
                "instrument version conflict: same version with different "
                f"reference fingerprint for {version.instrument_key}"
            )
        # Identical version + fingerprint: idempotent keep.
        latest[version.instrument_key] = version
    ref = reference_data_version
    if ref is None and latest:
        ref = next(iter(latest.values())).reference_data_version
    return InstrumentVersionRegistry(
        reference_data_version=ref or "opip-evidence-identity-v1", known=latest
    )


def load_instrument_version_payloads(db_path: Path) -> list[dict[str, Any]]:
    """Read committed instrument-version records from the canonical SQLite WAL."""
    from app.opip.canonical.schema import connect

    target = Path(db_path)
    if not target.exists():
        return []
    conn = connect(target, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (MARKET_INSTRUMENT_VERSION_RECORDED,),
        ).fetchall()
    finally:
        conn.close()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def hydrate_instrument_version_registry(
    db_path: Path | None = None,
    *,
    reference_data_version: str | None = None,
) -> InstrumentVersionRegistry:
    """Rebuild the registry from durable evidence (empty when DB is absent)."""
    if db_path is None:
        from app.opip.canonical.paths import db_path as default_db_path

        db_path = default_db_path()
    payloads = load_instrument_version_payloads(Path(db_path))
    return reconstruct_instrument_version_registry(
        payloads, reference_data_version=reference_data_version
    )


def instrument_version_record_payload(version: InstrumentVersion) -> dict[str, Any]:
    """Canonical payload; includes fingerprint for independent verification."""
    payload = version.to_dict()
    payload["record_type"] = "InstrumentVersion"
    payload["observed_at_utc"] = iso_z(
        version.observed_at_utc, field_name="observed_at_utc"
    )
    return payload


__all__ = [
    "hydrate_instrument_version_registry",
    "instrument_version_from_payload",
    "instrument_version_record_payload",
    "load_instrument_version_payloads",
    "reconstruct_instrument_version_registry",
]
