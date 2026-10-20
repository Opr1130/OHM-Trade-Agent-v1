"""R2 receipt binding for the three-phase bootstrap.

Corrects D06: a stale PREPARED receipt or an old AUTHORIZATION could be reused.
Each receipt carries a content digest and an expiry; AUTHORIZED embeds the
PREPARED digest; EXECUTE re-validates both, re-reads host/SHA/image/config,
then marks the authorization consumed so it cannot be replayed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
RECEIPT_SCHEMA = 2
DEFAULT_TTL_MINUTES = 60


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _digest(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k not in {"receipt_digest", "consumed_at_utc"}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write(path: Path, payload: dict) -> dict:
    payload = dict(payload)
    payload.pop("consumed_at_utc", None)
    payload["receipt_digest"] = _digest(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_prepared(
    *,
    path: Path,
    host: str,
    sha: str,
    image_id: str,
    config_digest: str,
    preflight_digest: str,
    preflight: dict,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> dict:
    now = _utc()
    return _write(Path(path), {
        "receipt": "BOOTSTRAP_PREPARED",
        "receipt_schema": RECEIPT_SCHEMA,
        "prepared_at_utc": _iso(now),
        "expires_at_utc": _iso(now + timedelta(minutes=ttl_minutes)),
        "host": str(host),
        "certified_sha": str(sha),
        "image_id": str(image_id),
        "config_digest": str(config_digest),
        "preflight_digest": str(preflight_digest),
        "preflight": preflight,
        "fresh_host_only": bool(preflight.get("fresh_host_only")),
        "blocking_incidents": list(preflight.get("blocking_incidents") or []),
        "ready": bool(preflight.get("ready")),
        "nonce": secrets.token_hex(16),
    })


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
def bind_authorization(
    *,
    prepared_path: Path,
    path: Path,
    confirm_sha: str,
    confirm_host: str,
    acknowledge_fresh_host: bool,
    owner: str = "owner",
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> dict:
    prepared = read(prepared_path)
    if prepared.get("receipt_digest") != _digest(prepared):
        raise SystemExit("refusing authorization: prepared receipt digest is invalid (tampered or stale)")
    if _parse(prepared["expires_at_utc"]) <= _utc():
        raise SystemExit("refusing authorization: prepared receipt expired; re-run prepare")
    if list(prepared.get("blocking_incidents") or []):
        raise SystemExit(
            f"refusing authorization: {len(prepared['blocking_incidents'])} blocking protection incident(s)"
        )
    if prepared.get("ready") is not True:
        if not acknowledge_fresh_host:
            raise SystemExit("preflight is blocked; --acknowledge-fresh-host is required")
        if prepared.get("fresh_host_only") is not True:
            raise SystemExit("preflight block is not a fresh-host condition; refusing")
    if confirm_sha != prepared.get("certified_sha"):
        raise SystemExit("refusing authorization: confirmed SHA does not match the prepared receipt")
    if str(confirm_host) != str(prepared.get("host")):
        raise SystemExit("refusing authorization: confirmed host does not match the prepared receipt")
    now = _utc()
    return _write(Path(path), {
        "receipt": "BOOTSTRAP_AUTHORIZED",
        "receipt_schema": RECEIPT_SCHEMA,
        "authorized_at_utc": _iso(now),
        "expires_at_utc": _iso(now + timedelta(minutes=ttl_minutes)),
        "host": prepared["host"],
        "certified_sha": prepared["certified_sha"],
        "image_id": prepared["image_id"],
        "config_digest": prepared["config_digest"],
        "prepared_digest": prepared["receipt_digest"],
        "preflight_digest": prepared["preflight_digest"],
        "fresh_host_acknowledged": bool(acknowledge_fresh_host),
        "owner": str(owner),
        "token": secrets.token_hex(16),
    })


def verify_for_execute(*, prepared_path: Path, authorized_path: Path, observed: dict) -> dict:
    """Re-validate everything immediately before mutation."""
    problems: list[str] = []
    prepared = read(prepared_path)
    authorized = read(authorized_path)
    if prepared.get("receipt_digest") != _digest(prepared):
        problems.append("prepared digest invalid")
    if authorized.get("receipt_digest") != _digest(authorized):
        problems.append("authorization digest invalid")
    if authorized.get("consumed_at_utc"):
        problems.append("authorization already consumed")
    if authorized.get("prepared_digest") != prepared.get("receipt_digest"):
        problems.append("authorization is not bound to this prepared receipt")
    for receipt, label in ((prepared, "prepared"), (authorized, "authorized")):
        if _parse(receipt.get("expires_at_utc", "")) <= _utc():
            problems.append(f"{label} receipt expired")
    for key in ("host", "certified_sha", "image_id", "config_digest"):
        if authorized.get(key) != prepared.get(key):
            problems.append(f"{key} differs between receipts")
        if observed.get(key) is not None and str(observed[key]) != str(prepared.get(key)):
            problems.append(f"{key} drifted on the host: observed={observed[key]} receipt={prepared.get(key)}")
    return {"verdict": "PASS" if not problems else "FAIL", "problems": problems}


def consume(path: Path) -> dict:
    payload = read(path)
    payload["consumed_at_utc"] = _iso(_utc())
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload

