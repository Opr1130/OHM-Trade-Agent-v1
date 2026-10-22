"""Durable recovery intent and terminal receipts.

A recovery binding must exist before the environment references it, and a
pending binding must never look like a successful release. This module writes a
PREPARED intent with ``O_EXCL`` and strict fsync, then a separate CERTIFIED or
BLOCKED terminal record. Historical records are never edited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.fs_durability import fsync_dir, fsync_file

KIND = "OPIP_LEARNING_RECOVERY_BINDING"
SCHEMA = 1
PREPARED = "PREPARED"
IN_PROGRESS = "IN_PROGRESS"
BLOCKED = "BLOCKED"
CERTIFIED = "CERTIFIED"
TERMINAL = {BLOCKED, CERTIFIED}


class RecoveryReceiptError(RuntimeError):
    """A recovery receipt could not be written truthfully."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RecoveryReceiptError(f"unreadable recovery receipt {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecoveryReceiptError(f"recovery receipt is not an object: {path}")
    return payload


def _exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        fsync_file(path)
        fsync_dir(path.parent)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_intent(
    path: Path,
    *,
    transaction_id: str,
    production_sha: str,
    tooling_sha: str,
    expected_image: str,
    expected_image_id: str,
    manifest_sha: str,
    binding_report_sha256: str = "",
) -> dict[str, Any]:
    path = Path(path)
    payload = {
        "receipt": KIND,
        "schema": SCHEMA,
        "status": PREPARED,
        "created_at_utc": _utc_now(),
        "transaction_id": transaction_id,
        "production_sha": production_sha,
        "tooling_sha": tooling_sha,
        "expected_image": expected_image,
        "expected_image_id": expected_image_id,
        "manifest_sha": manifest_sha,
        "binding_report_sha256": binding_report_sha256,
        "success": False,
        "terminal_receipt": None,
    }
    try:
        _exclusive(path, payload)
    except FileExistsError:
        existing = _read(path)
        if existing.get("transaction_id") != transaction_id:
            raise RecoveryReceiptError(
                f"recovery intent already belongs to another transaction: {path}"
            )
    return _read(path)


def terminal_path(intent_path: Path, status: str) -> Path:
    if status not in TERMINAL:
        raise RecoveryReceiptError(f"invalid terminal status: {status}")
    intent_path = Path(intent_path)
    return intent_path.with_name(
        f"{intent_path.stem}.{status.lower()}{intent_path.suffix}"
    )


def write_terminal(
    intent_path: Path,
    *,
    status: str,
    detail: str,
    failure_receipt: str = "",
) -> Path:
    intent_path = Path(intent_path)
    intent = _read(intent_path)
    if intent.get("receipt") != KIND or intent.get("status") not in {
        PREPARED,
        IN_PROGRESS,
    }:
        raise RecoveryReceiptError(f"not a pending recovery intent: {intent_path}")
    output = terminal_path(intent_path, status)
    payload = {
        "receipt": KIND,
        "schema": SCHEMA,
        "status": status,
        "recorded_at_utc": _utc_now(),
        "transaction_id": intent.get("transaction_id"),
        "production_sha": intent.get("production_sha"),
        "tooling_sha": intent.get("tooling_sha"),
        "expected_image": intent.get("expected_image"),
        "manifest_sha": intent.get("manifest_sha"),
        "success": status == CERTIFIED,
        "detail": detail,
        "failure_receipt": failure_receipt or None,
        "intent_sha256": _sha256(intent_path),
        "historical_evidence_modified": False,
    }
    _exclusive(output, payload)
    return output


def materialize_blocked(binding_path: Path, failure_path: Path) -> Path:
    """Create a truthful BLOCKED receipt at a previously missing binding path.

    Used only to repair a pending binding left by a failed recovery. It records
    existing failure evidence and never asserts success.
    """
    binding_path = Path(binding_path)
    failure_path = Path(failure_path)
    failure = _read(failure_path)
    payload = {
        "receipt": KIND,
        "schema": SCHEMA,
        "status": BLOCKED,
        "recorded_at_utc": _utc_now(),
        "transaction_id": str(failure.get("transaction_dir") or ""),
        "production_sha": failure.get("production_sha"),
        "tooling_sha": failure.get("tooling_sha"),
        "success": False,
        "detail": "recovery failed before a final binding receipt was written",
        "failure_receipt": str(failure_path),
        "failure_sha256": _sha256(failure_path),
        "historical_evidence_modified": False,
    }
    if binding_path.exists():
        existing = _read(binding_path)
        if (
            existing.get("status") == BLOCKED
            and existing.get("failure_sha256") == payload["failure_sha256"]
        ):
            return binding_path
        raise RecoveryReceiptError(f"binding path already exists: {binding_path}")
    _exclusive(binding_path, payload)
    return binding_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recovery binding receipts")
    sub = parser.add_subparsers(dest="command", required=True)

    intent = sub.add_parser("write-intent")
    intent.add_argument("--path", required=True)
    intent.add_argument("--transaction-id", required=True)
    intent.add_argument("--production-sha", required=True)
    intent.add_argument("--tooling-sha", required=True)
    intent.add_argument("--expected-image", required=True)
    intent.add_argument("--expected-image-id", required=True)
    intent.add_argument("--manifest-sha", required=True)
    intent.add_argument("--binding-report-sha256", default="")

    terminal = sub.add_parser("write-terminal")
    terminal.add_argument("--intent", required=True)
    terminal.add_argument("--status", required=True, choices=sorted(TERMINAL))
    terminal.add_argument("--detail", required=True)
    terminal.add_argument("--failure-receipt", default="")

    block = sub.add_parser("materialize-blocked")
    block.add_argument("--binding", required=True)
    block.add_argument("--failure", required=True)

    args = parser.parse_args(argv)
    if args.command == "write-intent":
        result = write_intent(
            Path(args.path),
            transaction_id=args.transaction_id,
            production_sha=args.production_sha,
            tooling_sha=args.tooling_sha,
            expected_image=args.expected_image,
            expected_image_id=args.expected_image_id,
            manifest_sha=args.manifest_sha,
            binding_report_sha256=args.binding_report_sha256,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "write-terminal":
        path = write_terminal(
            Path(args.intent),
            status=args.status,
            detail=args.detail,
            failure_receipt=args.failure_receipt,
        )
        print(json.dumps({"terminal": str(path)}, indent=2, sort_keys=True))
        return 0
    path = materialize_blocked(Path(args.binding), Path(args.failure))
    print(json.dumps({"blocked": str(path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

