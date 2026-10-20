"""R5: owner-authorized fresh canonical genesis for a brand-new V2 history.

Behavioural proof for ``ATDD-M07-R5-fresh-canonical-genesis-v1``. Every test
runs against the certified application checkout (``OPIP_CERTIFIED_SRC``) and
never contacts a host, a container or the internet; the droplet identity service
is replaced by a loopback stub.

Publishing a canonical store requires provable POSIX directory durability, so
the successful paths are Linux-only and fail closed elsewhere by design. Every
refusal path runs on any platform, which is what makes the safety claims
portable.
"""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import opip_genesis
import opip_rollback_guard

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
DROPLET_ID = "610307364"
LINUX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="publishing a canonical store requires POSIX directory durability",
)
WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="asserts the Windows fail-closed durability verdict",
)

_env_cert = os.environ.get("OPIP_CERTIFIED_SRC")
CERT_SRC = (Path(_env_cert) / "OHM-Trade-Agent-v1") if _env_cert else Path(__file__).resolve().parents[4]


class _MetadataHandler(BaseHTTPRequestHandler):
    identity = DROPLET_ID

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        if self.path != "/metadata/v1/id":
            self.send_error(404)
            return
        body = self.identity.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:  # keep CI output readable
        return


@pytest.fixture
def imds():
    """Loopback stand-in for the droplet metadata identity service."""
    servers: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def start(identity: str = DROPLET_ID) -> str:
        handler = type("_Handler", (_MetadataHandler,), {"identity": identity})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}/metadata/v1/id"

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    live_db = (
        tmp_path / "v2" / "data" / "opip" / "canonical" / opip_genesis.CANONICAL_DB_NAME
    )
    receipt = tmp_path / "state" / "canonical-fresh-genesis-receipt.json"
    return live_db, receipt


def _genesis(
    live_db: Path,
    receipt: Path | None,
    imds_url: str,
    **overrides: object,
) -> dict:
    kwargs: dict = {
        "app_root": CERT_SRC,
        "live_db": live_db,
        "receipt_out": receipt,
        "imds_url": imds_url,
        "authorize": True,
    }
    kwargs.update(overrides)
    return opip_genesis.fresh_genesis(**kwargs)


def _checks(result: dict) -> dict[str, dict]:
    return {check["id"]: check for check in result["checks"]}


def _failure_ids(result: dict) -> list[str]:
    return [check["id"] for check in result["checks"] if check["status"] == "FAIL"]


def _seed(db: Path, events: int = 2) -> None:
    """Create a store that holds canonical history, through the certified writer."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION
    from app.opip.canonical.writer import CanonicalWriter

    db.parent.mkdir(parents=True, exist_ok=True)
    writer = CanonicalWriter(db)
    try:
        for index in range(events):
            writer.submit(
                WriterIntent(
                    schema_version=SCHEMA_VERSION,
                    priority="NORMAL",
                    idempotency_key=f"r5:seed:{index}",
                    event_type="alert_governor.transition.recorded",
                    payload={
                        "identity": f"EARLY_MOVER:R5-{index}",
                        "transition_key": f"READY:r5:{index}",
                        "message_id": 5000 + index,
                        "created_new": True,
                        "scan_id": "r5-scan",
                    },
                    ops_handoff={
                        "operation": "RECORD",
                        "identity": f"EARLY_MOVER:R5-{index}",
                        "transition_key": f"READY:r5:{index}",
                        "message_id": 5000 + index,
                        "created_new": True,
                        "reservation_token": f"r5tok{index}",
                        "state_family": "early_watch",
                    },
                )
            )
        writer.checkpoint_wal()
    finally:
        writer.close()


@LINUX_ONLY
def test_fresh_genesis_publishes_an_empty_certified_store(imds, layout) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "PASS", result["checks"]
    assert result["completed"] is True
    assert result["canonical_initialized"] is True
    assert Path(result["receipt"]) == receipt

    facts = opip_genesis.store_facts(live_db)
    assert facts["history_epoch"] == 1
    assert facts["next_local_sequence"] == 1
    assert facts["event_count"] == 0
    assert facts["integrity_check"] == "ok"
    assert facts["foreign_key_violations"] == 0
    assert facts["sidecars"] == []

    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["host"] == DROPLET_ID
    assert document["host_identity_proven"] is True
    assert document["certified_sha"] == CERTIFIED_SHA
    assert document["destination_db"] == str(live_db.resolve())
    assert document["destination_db_sha256"] == facts["db_sha256"]
    assert document["initial_epoch"] == {"history_epoch": 1, "next_local_sequence": 1}
    assert document["historical_events"] == 0
    assert document["events_table_rows"] == 0
    assert document["history_imported"] is False
    assert document["schema"]["fingerprint"] == facts["schema_fingerprint"]
    assert document["schema"]["sidecars"] == []
    provenance = document["generation_provenance"]
    assert provenance["schema_initializer"] == (
        "app.opip.canonical.schema.initialize_schema"
    )
    assert provenance["schema_initializer_via"].startswith(
        "app.opip.canonical.writer.CanonicalWriter"
    )
    assert provenance["history_imported"] is False
    assert provenance["v1_archives_touched"] is False
    assert provenance["historical_sources_read"] == []
    assert document["receipt_sha256"] == opip_genesis.receipt_digest(document)

    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert verified["verdict"] == "PASS", verified["problems"]

    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "PASS", gate["problems"]
    assert gate["receipt_kind"] == opip_genesis.GENESIS_OPERATION


@LINUX_ONLY
def test_a_repeated_invocation_refuses_and_changes_nothing(imds, layout) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    store_digest = opip_genesis.sha256_file(live_db)
    receipt_digest = opip_genesis.sha256_file(receipt)
    evidence_before = sorted(p.name for p in receipt.parent.iterdir())

    repeated = _genesis(live_db, receipt, imds())
    assert repeated["verdict"] == "REFUSED"
    assert "RECEIPT_PRESENT" in _failure_ids(repeated)
    assert opip_genesis.sha256_file(live_db) == store_digest
    assert opip_genesis.sha256_file(receipt) == receipt_digest
    assert sorted(p.name for p in receipt.parent.iterdir()) == evidence_before



def test_an_existing_nonempty_canonical_history_is_never_overwritten(
    imds, layout
) -> None:
    live_db, receipt = layout
    _seed(live_db, events=3)
    before = opip_genesis.sha256_file(live_db)
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "EXISTING_CANONICAL_STORE" in _failure_ids(result)
    assert opip_genesis.sha256_file(live_db) == before
    assert not receipt.exists()


def test_an_existing_empty_file_is_never_adopted(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    live_db.write_bytes(b"")
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "EXISTING_CANONICAL_STORE" in _failure_ids(result)
    assert live_db.read_bytes() == b""


def test_a_corrupted_database_is_refused_and_left_untouched(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    live_db.write_bytes(b"not a sqlite database at all")
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "EXISTING_CANONICAL_STORE" in _failure_ids(result)
    assert live_db.read_bytes() == b"not a sqlite database at all"


def test_conflicting_sqlite_sidecars_are_refused(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    wal = Path(f"{live_db}-wal")
    wal.write_bytes(b"stale wal frames")
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "SQLITE_SIDECARS" in _failure_ids(result)
    assert not live_db.exists()
    assert wal.read_bytes() == b"stale wal frames"


def test_an_unknown_database_file_blocks_a_fresh_genesis(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    foreign = live_db.parent / "opip_canonical_v1_export.sqlite3"
    foreign.write_bytes(b"SQLite format 3\x00 v1 canonical history")
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "UNKNOWN_DATABASE_FILE" in _failure_ids(result)
    assert foreign.read_bytes().startswith(b"SQLite format 3")
    assert not live_db.exists()


def test_a_receipt_inside_the_store_directory_is_refused(imds, layout) -> None:
    live_db, _receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    inside = live_db.parent / "canonical-fresh-genesis-receipt.json"
    result = _genesis(live_db, inside, imds())
    assert result["verdict"] == "REFUSED"
    assert "RECEIPT_LOCATION" in _failure_ids(result)
    assert not live_db.exists()
    assert not inside.exists()


def test_a_competing_store_lock_refuses_the_genesis(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    from app.opip.canonical.schema import CanonicalStoreLock

    holder = CanonicalStoreLock(live_db)
    holder.acquire()
    try:
        result = _genesis(live_db, receipt, imds())
    finally:
        holder.release()
    assert result["verdict"] == "REFUSED"
    assert "EXISTING_WRITER" in _failure_ids(result)
    assert not live_db.exists()


def test_exactly_one_canonical_writer_can_own_the_store(layout) -> None:
    from app.opip.canonical.schema import CanonicalStoreBusyError
    from app.opip.canonical.writer import CanonicalWriter

    live_db, _receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    first = CanonicalWriter(live_db)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(live_db)
    finally:
        first.close()


def test_a_writer_socket_refuses_the_genesis(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    socket_path = live_db.parent / "writer.sock"
    socket_path.write_text("", encoding="utf-8")
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "EXISTING_WRITER" in _failure_ids(result)
    assert not live_db.exists()



def test_owner_authorization_is_required(imds, layout) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds(), authorize=False)
    assert result["verdict"] == "REFUSED"
    assert "AUTHORIZATION" in _failure_ids(result)
    assert not live_db.exists()
    assert not receipt.exists()


def test_an_incorrect_certified_sha_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds(), certified_sha="0" * 40)
    assert result["verdict"] == "REFUSED"
    assert "CERTIFIED_SHA" in _failure_ids(result)
    assert not live_db.exists()


def test_an_incorrect_host_identity_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds("589794133"))
    assert result["verdict"] == "REFUSED"
    assert "HOST_IDENTITY" in _failure_ids(result)
    assert "host" not in result
    assert not live_db.exists()


def test_an_unreachable_identity_service_refuses_instead_of_guessing(
    layout,
) -> None:
    live_db, receipt = layout
    result = _genesis(
        live_db, receipt, "http://127.0.0.1:9/metadata/v1/id"
    )
    assert result["verdict"] == "REFUSED"
    assert "HOST_IDENTITY" in _failure_ids(result)
    assert not live_db.exists()


def test_a_destination_outside_the_certified_layout_is_refused(imds, tmp_path) -> None:
    wrong = tmp_path / "somewhere" / opip_genesis.CANONICAL_DB_NAME
    receipt = tmp_path / "state" / "receipt.json"
    result = _genesis(wrong, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "DESTINATION_LAYOUT" in _failure_ids(result)
    assert not wrong.exists()


def test_a_mismatched_destination_confirmation_is_refused(imds, layout, tmp_path) -> None:
    live_db, receipt = layout
    result = _genesis(
        live_db,
        receipt,
        imds(),
        confirm_destination=str(tmp_path / "another" / "canonical" / "db.sqlite3"),
    )
    assert result["verdict"] == "REFUSED"
    assert "DESTINATION_CONFIRMED" in _failure_ids(result)
    assert not live_db.exists()


def test_confirmed_destination_is_accepted(imds, layout) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds(), confirm_destination=str(live_db))
    # Reaches the platform gate at least; on POSIX this is a full PASS.
    assert "DESTINATION_CONFIRMED" not in _failure_ids(result)


@WINDOWS_ONLY
def test_publishing_fails_closed_when_directory_durability_is_unprovable(
    imds, layout
) -> None:
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "PLATFORM_BLOCKED"
    assert "PLATFORM_DURABILITY" in _failure_ids(result)
    assert not live_db.exists()
    assert not receipt.exists()



@LINUX_ONLY
def test_a_tampered_receipt_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["host"] = "589794133"
    receipt.write_text(json.dumps(document), encoding="utf-8")
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert verified["verdict"] == "REFUSED"
    assert any("self-digest" in problem for problem in verified["problems"])
    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "REFUSED"


@LINUX_ONLY
def test_a_forged_but_self_consistent_receipt_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["destination_db_sha256"] = "0" * 64
    document["receipt_sha256"] = opip_genesis.receipt_digest(document)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert verified["verdict"] == "REFUSED"
    assert any("do not match" in problem for problem in verified["problems"])


@LINUX_ONLY
def test_a_stale_receipt_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC, now=later
    )
    assert verified["verdict"] == "REFUSED"
    assert any("stale" in problem for problem in verified["problems"])
    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC, max_age_hours=0.0
    )
    assert gate["verdict"] == "REFUSED"


@LINUX_ONLY
def test_a_future_dated_receipt_is_refused(imds, layout) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC, now=earlier
    )
    assert verified["verdict"] == "REFUSED"
    assert any("future" in problem for problem in verified["problems"])


@LINUX_ONLY
def test_a_receipt_for_another_destination_is_refused(imds, layout, tmp_path) -> None:
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    other = tmp_path / "v2" / "data" / "opip" / "canonical" / "other.sqlite3"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(live_db.read_bytes())
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=other, app_root=CERT_SRC
    )
    assert verified["verdict"] == "REFUSED"
    assert any("destination" in problem for problem in verified["problems"])



@LINUX_ONLY
def test_a_store_written_after_genesis_can_no_longer_activate(imds, layout) -> None:
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION
    from app.opip.canonical.writer import CanonicalWriter

    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    writer = CanonicalWriter(live_db)
    try:
        writer.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="NORMAL",
                idempotency_key="r5:post-genesis:0",
                event_type="alert_governor.transition.recorded",
                payload={
                    "identity": "EARLY_MOVER:R5-POST",
                    "transition_key": "READY:r5:post",
                    "message_id": 6000,
                    "created_new": True,
                    "scan_id": "r5-post",
                },
                ops_handoff={
                    "operation": "RECORD",
                    "identity": "EARLY_MOVER:R5-POST",
                    "transition_key": "READY:r5:post",
                    "message_id": 6000,
                    "created_new": True,
                    "reservation_token": "r5posttok",
                    "state_family": "early_watch",
                },
            )
        )
        writer.checkpoint_wal()
    finally:
        writer.close()
    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "REFUSED"
    assert gate["problems"]


@LINUX_ONLY
def test_a_failed_initialization_publishes_nothing_and_a_retry_succeeds(
    imds, layout, monkeypatch
) -> None:
    live_db, receipt = layout
    real_load = opip_genesis.load_certified
    stack = real_load(CERT_SRC)

    class _Exploding:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated certified schema initialization failure")

    monkeypatch.setattr(
        opip_genesis,
        "load_certified",
        lambda _root: replace(stack, CanonicalWriter=_Exploding),
    )
    failed = _genesis(live_db, receipt, imds())
    assert failed["verdict"] == "FAIL"
    assert "CERTIFIED_INITIALIZATION" in _failure_ids(failed)
    assert not live_db.exists()
    assert not receipt.exists()
    leftovers = [p.name for p in live_db.parent.iterdir()]
    assert [
        name for name in leftovers if name.startswith(opip_genesis.STAGING_MARKER)
    ] == []
    assert list(receipt.parent.glob("*.failed.json")), (
        "a failed genesis must leave diagnostic evidence beside the receipt path"
    )

    monkeypatch.setattr(opip_genesis, "load_certified", real_load)
    retried = _genesis(live_db, receipt, imds())
    assert retried["verdict"] == "PASS", retried["checks"]


@LINUX_ONLY
def test_interrupted_genesis_artifacts_block_retry_until_removed(imds, layout) -> None:
    live_db, receipt = layout
    live_db.parent.mkdir(parents=True, exist_ok=True)
    staged = live_db.parent / (
        f"{opip_genesis.STAGING_MARKER}4242.deadbeefdeadbeef.sqlite3"
    )
    staged.write_bytes(b"SQLite format 3\x00partial staging artifact")
    blocked = _genesis(live_db, receipt, imds())
    assert blocked["verdict"] == "REFUSED"
    assert "INTERRUPTED_GENESIS_ARTIFACT" in _failure_ids(blocked)
    assert staged.exists(), "genesis must never delete an existing database artifact"

    staged.unlink()
    retried = _genesis(live_db, receipt, imds())
    assert retried["verdict"] == "PASS", retried["checks"]



@LINUX_ONLY
def test_v1_history_learning_and_calibration_cannot_enter_the_new_store(
    imds, layout, tmp_path
) -> None:
    live_db, receipt = layout
    archive = tmp_path / "v1-archive"
    archive.mkdir()
    v1_db = archive / opip_genesis.CANONICAL_DB_NAME
    _seed(v1_db, events=2)
    learning = archive / "learning-evidence.jsonl"
    learning.write_text('{"outcome": "v1"}\n', encoding="utf-8")
    calibration = archive / "calibration.json"
    calibration.write_text('{"version": "v1"}\n', encoding="utf-8")
    # V1 material parked beside the V2 data root, outside the canonical directory.
    parked = tmp_path / "v2" / "data" / "v1-archive" / opip_genesis.CANONICAL_DB_NAME
    parked.parent.mkdir(parents=True)
    parked.write_bytes(v1_db.read_bytes())
    sources = (v1_db, learning, calibration, parked)
    before = {path: opip_genesis.sha256_file(path) for path in sources}

    result = _genesis(live_db, receipt, imds())
    assert result["verdict"] == "PASS", result["checks"]
    after = {path: opip_genesis.sha256_file(path) for path in sources}
    assert after == before, "genesis must not read, move or rewrite V1 evidence"

    facts = opip_genesis.store_facts(live_db)
    assert facts["event_count"] == 0
    assert facts["history_epoch"] == 1
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["generation_provenance"]["historical_sources_read"] == []
    assert document["generation_provenance"]["v1_archives_touched"] is False
    assert document["history_imported"] is False


def test_the_genesis_surface_cannot_name_a_historical_source() -> None:
    parameters = set(inspect.signature(opip_genesis.fresh_genesis).parameters)
    assert parameters == {
        "app_root",
        "live_db",
        "receipt_out",
        "certified_sha",
        "expected_host",
        "imds_url",
        "confirm_destination",
        "authorize",
    }
    source = Path(opip_genesis.__file__).read_text(encoding="utf-8")
    for forbidden in ("--bundle", "--restore", "--import", "--source-db", "--learning"):
        assert forbidden not in source, forbidden


def test_writer_gate_also_accepts_a_valid_native_recovery_receipt(tmp_path) -> None:
    db = tmp_path / "v2" / "data" / "opip" / "canonical" / opip_genesis.CANONICAL_DB_NAME
    _seed(db, events=1)
    facts = opip_genesis.store_facts(db)
    receipt = tmp_path / "canonical-recovery-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "receipt_schema": 1,
                "operation": opip_genesis.NATIVE_RECOVERY_OPERATION,
                "completed": True,
                "verdict": "PASS",
                "restored_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": DROPLET_ID,
                "destination_db": str(db.resolve()),
                "source_release_sha": CERTIFIED_SHA,
                "integrity_check": "ok",
                "foreign_key_violations": 0,
                "restored": {
                    "history_epoch": facts["history_epoch"],
                    "next_local_sequence": facts["next_local_sequence"],
                    "event_count": facts["event_count"],
                    "integrity_check": "ok",
                },
            }
        ),
        encoding="utf-8",
    )
    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "PASS", gate["problems"]
    assert gate["receipt_kind"] == opip_genesis.NATIVE_RECOVERY_OPERATION



def test_an_unsupported_receipt_operation_is_refused(tmp_path) -> None:
    db = tmp_path / "v2" / "data" / "opip" / "canonical" / opip_genesis.CANONICAL_DB_NAME
    _seed(db, events=1)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "receipt_schema": 1,
                "operation": "canonical-file-copy",
                "completed": True,
                "verdict": "PASS",
                "restored_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": DROPLET_ID,
                "destination_db": str(db.resolve()),
            }
        ),
        encoding="utf-8",
    )
    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "REFUSED"
    assert any("not a supported writer-gate receipt" in p for p in gate["problems"])


def test_a_self_declared_genesis_pass_cannot_admit_a_store_with_history(
    tmp_path,
) -> None:
    """The strongest forgery: every claim self-consistent, the store not empty."""
    db = tmp_path / "v2" / "data" / "opip" / "canonical" / opip_genesis.CANONICAL_DB_NAME
    _seed(db, events=2)
    facts = opip_genesis.store_facts(db)
    document = {
        "receipt_schema": opip_genesis.GENESIS_RECEIPT_SCHEMA,
        "operation": opip_genesis.GENESIS_OPERATION,
        "completed": True,
        "verdict": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": DROPLET_ID,
        "host_identity_proven": True,
        "certified_sha": CERTIFIED_SHA,
        "destination_db": str(db.resolve()),
        "destination_db_sha256": facts["db_sha256"],
        "schema": {
            "schema_version": facts["schema_version"],
            "fingerprint": facts["schema_fingerprint"],
            "sidecars": [],
        },
        "initial_epoch": {"history_epoch": 1, "next_local_sequence": 1},
        "historical_events": 0,
        "events_table_rows": 0,
        "history_imported": False,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "certified_modules": opip_genesis.certified_module_digests(CERT_SRC),
    }
    document["receipt_sha256"] = opip_genesis.receipt_digest(document)
    receipt = tmp_path / "forged-genesis-receipt.json"
    receipt.write_text(json.dumps(document), encoding="utf-8")

    gate = opip_rollback_guard.writer_gate(
        receipt_path=receipt, db=db, app_root=CERT_SRC
    )
    assert gate["verdict"] == "REFUSED"
    assert any("event_count" in problem for problem in gate["problems"])


def test_a_genesis_receipt_without_the_certified_source_is_refused(tmp_path) -> None:
    db = tmp_path / "v2" / "data" / "opip" / "canonical" / opip_genesis.CANONICAL_DB_NAME
    _seed(db, events=1)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "receipt_schema": opip_genesis.GENESIS_RECEIPT_SCHEMA,
                "operation": opip_genesis.GENESIS_OPERATION,
                "completed": True,
                "verdict": "PASS",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": DROPLET_ID,
                "host_identity_proven": True,
                "certified_sha": CERTIFIED_SHA,
                "destination_db": str(db.resolve()),
            }
        ),
        encoding="utf-8",
    )
    gate = opip_rollback_guard.writer_gate(receipt_path=receipt, db=db)
    assert gate["verdict"] == "REFUSED"
    assert any("--app-root" in problem for problem in gate["problems"])

