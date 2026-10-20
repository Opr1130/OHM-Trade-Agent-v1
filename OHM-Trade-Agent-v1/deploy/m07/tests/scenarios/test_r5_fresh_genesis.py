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
import shutil
import sqlite3
import subprocess
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

#: The host bootstrap whose install routine is exercised below. It lives in the
#: toolkit, not in the certified application tree.
M07_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = M07_ROOT / "scripts" / "v2" / "bootstrap-v2.sh"


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
        "confirm_destination": str(live_db),
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
def test_a_repeated_invocation_preserves_the_store_and_receipt_and_adds_only_evidence(
    imds, layout
) -> None:
    """A repeat may add diagnostic evidence; it may never change canonical state.

    The contract is deliberately narrow: the canonical database and the
    authoritative receipt are immutable across a repeat, while *each* refusal is
    allowed to publish its own separate ``*.failed.json`` beside the receipt. This
    test names that contract exactly, so "changes nothing" can never be read as a
    promise the implementation does not make.
    """
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    store_digest = opip_genesis.sha256_file(live_db)
    receipt_digest = opip_genesis.sha256_file(receipt)
    evidence_before = sorted(p.name for p in receipt.parent.iterdir())

    repeated = _genesis(live_db, receipt, imds())
    assert repeated["verdict"] == "REFUSED"
    assert "RECEIPT_PRESENT" in _failure_ids(repeated)
    # The canonical database and the authoritative receipt are byte-identical.
    assert opip_genesis.sha256_file(live_db) == store_digest
    assert opip_genesis.sha256_file(receipt) == receipt_digest
    # The authoritative receipt still independently verifies after the repeat.
    verified = opip_genesis.verify_genesis_receipt(
        receipt_path=receipt, db=live_db, app_root=CERT_SRC
    )
    assert verified["verdict"] == "PASS", verified["problems"]
    # Evidence is never overwritten: a refusal only adds its own diagnostic file.
    added = [
        name for name in sorted(p.name for p in receipt.parent.iterdir())
        if name not in evidence_before
    ]
    assert len(added) == 1, added
    assert added[0].startswith(receipt.name) and added[0].endswith(".failed.json")
    diagnostic = json.loads((receipt.parent / added[0]).read_text(encoding="utf-8"))
    assert diagnostic["verdict"] == "REFUSED"
    assert diagnostic["completed"] is False
    assert diagnostic["receipt"] == str(receipt.parent / added[0])
    assert diagnostic["destination_db"] == str(live_db)


@LINUX_ONLY
def test_each_repeated_invocation_adds_its_own_evidence_and_nothing_else(
    imds, layout
) -> None:
    """Repeats accumulate *separate* diagnostics; canonical bytes never move."""
    live_db, receipt = layout
    assert _genesis(live_db, receipt, imds())["verdict"] == "PASS"
    store_digest = opip_genesis.sha256_file(live_db)
    receipt_digest = opip_genesis.sha256_file(receipt)
    baseline = sorted(p.name for p in receipt.parent.iterdir())

    for attempt in range(2):
        repeat = _genesis(live_db, receipt, imds())
        assert repeat["verdict"] == "REFUSED"
        assert "RECEIPT_PRESENT" in _failure_ids(repeat)
        added = [
            name for name in sorted(p.name for p in receipt.parent.iterdir())
            if name not in baseline
        ]
        # One new diagnostic per attempt, and none of them is the receipt itself.
        assert len(added) == attempt + 1, added
        assert receipt.name not in added
        assert opip_genesis.sha256_file(live_db) == store_digest
        assert opip_genesis.sha256_file(receipt) == receipt_digest




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


def test_a_missing_receipt_path_is_refused_before_any_store_is_created(
    imds, layout
) -> None:
    """AC-002: no authoritative receipt path means no genesis at all.

    A store without its receipt is an orphan the writer gate can never admit and a
    retry can never replace, so the receipt path is a precondition: it is refused
    before the canonical directory, the database or any evidence file exists.
    """
    live_db, receipt = layout
    result = _genesis(live_db, None, imds())
    assert result["verdict"] == "REFUSED"
    assert result["completed"] is False
    assert "RECEIPT_REQUIRED" in _failure_ids(result)
    detail = _checks(result)["RECEIPT_REQUIRED"]["detail"]
    assert "--receipt-out" in detail
    # Nothing at all was created -- including the evidence directory, because
    # there is no authoritative receipt path to publish evidence beside.
    assert not live_db.exists()
    assert not live_db.parent.exists()
    assert not receipt.exists()
    assert not receipt.parent.exists()
    assert list(live_db.parents[3].rglob("*.sqlite3*")) == []
    assert result.get("receipt") is None
    assert result.get("receipt_error")


def test_a_missing_receipt_path_never_publishes_a_store_even_when_authorized(
    imds, layout
) -> None:
    """AC-002: authorization alone never buys a store without evidence."""
    live_db, receipt = layout
    result = _genesis(live_db, None, imds(), authorize=True)
    assert result["verdict"] == "REFUSED"
    assert _checks(result)["AUTHORIZATION"]["status"] == "PASS"
    assert not live_db.exists()
    assert not live_db.parent.exists()
    assert not receipt.parent.exists()


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


def test_an_untrusted_metadata_endpoint_cannot_substitute_the_host_identity(
    layout,
) -> None:
    """AC-002: only the pinned endpoint (or a loopback stub) may name this host."""
    live_db, receipt = layout
    untrusted = "http://203.0.113.10/metadata/v1/id"
    trusted, detail = opip_genesis.imds_endpoint_trust(untrusted)
    assert trusted is False
    assert "neither the pinned droplet metadata service" in detail

    # Refused before the endpoint is contacted, so no network is required here.
    result = _genesis(live_db, receipt, untrusted)
    assert result["verdict"] == "REFUSED"
    assert "HOST_IDENTITY_ENDPOINT" in _failure_ids(result)
    assert "HOST_IDENTITY" not in _failure_ids(result)
    assert "host" not in result
    assert not live_db.exists()
    assert not live_db.parent.exists()


@pytest.mark.parametrize(
    "candidate",
    [
        "http://localhost/metadata/v1/id",  # a resolvable name is self-declared
        "https://169.254.169.254/metadata/v1/id",  # the pinned service is plain HTTP
        "http://169.254.169.254/metadata/v1/../v1/id?x=1",  # query string
        "http://169.254.169.254/",  # not the identity path
        "http://user:pass@203.0.113.10/metadata/v1/id",  # embedded credentials
        "http://0.0.0.0/metadata/v1/id",  # not loopback
        "file:///etc/hostname",  # not an HTTP metadata service
        "",
    ],
)
def test_only_the_pinned_endpoint_or_a_loopback_stub_may_name_the_host(
    candidate: str,
) -> None:
    """AC-002: the endpoint allowlist is exact, not a prefix or scheme match."""
    trusted, detail = opip_genesis.imds_endpoint_trust(candidate)
    assert trusted is False, (candidate, detail)
    assert detail


def test_the_pinned_and_loopback_endpoints_remain_accepted() -> None:
    """AC-002: production identity and the isolated test stub both still work."""
    assert opip_genesis.imds_endpoint_trust(opip_genesis.DEFAULT_IMDS_URL)[0] is True
    assert opip_genesis.imds_endpoint_trust(
        "http://127.0.0.1:8080/metadata/v1/id"
    )[0] is True
    assert opip_genesis.imds_endpoint_trust("http://[::1]:8080/metadata/v1/id")[0] is True
    loopback_trusted, loopback_detail = opip_genesis.imds_endpoint_trust(
        "http://127.0.0.1:8080/metadata/v1/id"
    )
    assert loopback_trusted is True
    assert "no production identity authority" in loopback_detail


def test_the_production_cli_cannot_select_a_metadata_endpoint(layout) -> None:
    """AC-002: the identity endpoint is a constant, not an operator option."""
    live_db, receipt = layout
    with pytest.raises(SystemExit) as excinfo:
        opip_genesis.main(
            [
                "fresh-genesis",
                "--app-root",
                str(CERT_SRC),
                "--live-db",
                str(live_db),
                "--receipt-out",
                str(receipt),
                "--confirm-destination",
                str(live_db),
                "--authorize",
                "--imds-url",
                "http://203.0.113.10/metadata/v1/id",
            ]
        )
    # argparse rejects the unknown option before a single check runs.
    assert excinfo.value.code == 2
    assert not live_db.exists()
    assert not receipt.exists()


def test_a_destination_outside_the_certified_layout_is_refused(imds, tmp_path) -> None:
    wrong = tmp_path / "somewhere" / opip_genesis.CANONICAL_DB_NAME
    receipt = tmp_path / "state" / "receipt.json"
    result = _genesis(wrong, receipt, imds())
    assert result["verdict"] == "REFUSED"
    assert "DESTINATION_LAYOUT" in _failure_ids(result)
    assert not wrong.exists()


def test_a_missing_destination_confirmation_is_refused_before_any_mutation(
    imds, layout
) -> None:
    """AC-002: a certified *shape* is not an operator confirmation.

    A path that merely ends in ``data/opip/canonical/opip_canonical_v1.sqlite3``
    must never be initialized without the operator naming that exact destination,
    otherwise a wrong runtime root or a mount typo silently creates the wrong
    canonical store.
    """
    live_db, receipt = layout
    result = _genesis(live_db, receipt, imds(), confirm_destination=None)
    assert result["verdict"] == "REFUSED"
    assert "DESTINATION_CONFIRMED" in _failure_ids(result)
    detail = _checks(result)["DESTINATION_CONFIRMED"]["detail"]
    assert "an explicit destination confirmation is required" in detail
    assert "--confirm-destination" in detail
    # No mutation: the canonical directory, the store and the receipt are absent.
    assert not live_db.exists()
    assert not live_db.parent.exists()
    assert not receipt.exists()
    assert list(live_db.parents[3].rglob("*.sqlite3*")) == []


def test_a_missing_destination_confirmation_refuses_even_at_the_certified_path(
    imds, layout
) -> None:
    """AC-002: the certified layout alone never authorizes a genesis."""
    live_db, receipt = layout
    assert opip_genesis.canonical_layout_ok(live_db)[0] is True
    result = _genesis(live_db, receipt, imds(), confirm_destination=None)
    assert result["verdict"] == "REFUSED"
    assert _checks(result)["DESTINATION_LAYOUT"]["status"] == "PASS"
    assert "DESTINATION_CONFIRMED" in _failure_ids(result)
    assert not live_db.exists()


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



# --- bootstrap install_release: `.env`/`data` are runtime state ------------------
# `prepare` requires the owner-provided `.env` at APP_ROOT and creates `data`, so
# `execute` must be able to install into a root holding both, while still refusing
# a root that holds anything unrelated. The host script is exercised directly: the
# real `install_release` function is extracted and driven with a stubbed manifest.

_INSTALL_RELEASE_STUBS = """#!/usr/bin/env bash
set -Eeuo pipefail
log() { printf 'LOG %s\\n' "$*" >&2; }
die() { printf 'REFUSED: %s\\n' "$*" >&2; exit 3; }
run() { local _desc="$1"; shift; "$@"; }
manifest_verify() { [[ "${STUB_MANIFEST_OK:-0}" == "1" ]]; }
"""


def _bootstrap_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def _install_release_body(text: str) -> str:
    start = text.index("install_release() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _usable_bash() -> str | None:
    """A bash that actually executes; a broken shim must not produce fake signal."""
    candidate = shutil.which("bash")
    if not candidate:
        fallback = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
        candidate = str(fallback) if fallback.is_file() else None
    if not candidate:
        return None
    probe = subprocess.run([candidate, "-c", "exit 0"], capture_output=True)
    return candidate if probe.returncode == 0 else None


def _write_install_harness(tmp_path, text: str) -> Path:
    script = tmp_path / "install_harness.sh"
    script.write_text(
        _INSTALL_RELEASE_STUBS + _install_release_body(text) + "install_release\n",
        encoding="utf-8",
        newline="\n",
    )
    return script


def _run_install_release(tmp_path, *, manifest_ok: bool):
    bash = _usable_bash()
    if not bash:
        pytest.skip("no working bash to exercise install_release")
    if not shutil.which("rsync"):
        pytest.skip("rsync not available to exercise install_release")

    src = tmp_path / "release_src"
    (src / "app").mkdir(parents=True)
    (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (src / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = tmp_path / "release-manifest.sha256"
    manifest.write_text("stub\n", encoding="utf-8")

    script = _write_install_harness(tmp_path, _bootstrap_text())
    env = dict(os.environ)
    env.update(
        PATH=str(Path(bash).parent) + os.pathsep + env.get("PATH", ""),
        SRC=str(src),
        APP_ROOT=str(tmp_path / "app_root"),
        MANIFEST=str(manifest),
        SHA=CERTIFIED_SHA,
        STUB_MANIFEST_OK="1" if manifest_ok else "0",
    )
    completed = subprocess.run([bash, str(script)], capture_output=True, text=True, env=env)
    return completed, tmp_path / "app_root"


def test_install_release_treats_env_and_data_as_runtime_state() -> None:
    """The pre-install classification must ignore exactly `data` and `.env`."""
    body = _install_release_body(_bootstrap_text())
    assert "local foreign" in body
    assert "! -name data ! -name .env" in body
    # The retired form classified `.env` as foreign content.
    assert "! -name data -print -quit 2>/dev/null)" not in body
    # Both entries must also survive the install copy.
    assert "--exclude 'data'" in body
    assert "--exclude '.env'" in body




def test_install_release_installs_into_a_root_holding_only_env_and_data(
    tmp_path,
) -> None:
    """Positive: prepare's `.env` + `data` must not deadlock execute."""
    app_root = tmp_path / "app_root"
    app_root.mkdir()
    (app_root / ".env").write_text("OWNER_PROVIDED=value\n", encoding="utf-8")
    (app_root / "data").mkdir()
    (app_root / "data" / "runtime-state.txt").write_text("keep\n", encoding="utf-8")

    completed, root = _run_install_release(tmp_path, manifest_ok=True)

    assert completed.returncode == 0, completed.stderr
    assert "non-certified content" not in completed.stderr
    assert (root / "docker-compose.yml").is_file()
    assert (root / "app" / "main.py").is_file()
    assert (root / ".env").read_text(encoding="utf-8") == "OWNER_PROVIDED=value\n"
    assert (root / "data" / "runtime-state.txt").read_text(encoding="utf-8") == "keep\n"


def test_install_release_refuses_unrelated_content(tmp_path) -> None:
    """Negative: any other top-level entry still refuses on the manifest mismatch."""
    app_root = tmp_path / "app_root"
    app_root.mkdir()
    (app_root / ".env").write_text("OWNER_PROVIDED=value\n", encoding="utf-8")
    (app_root / "data").mkdir()
    (app_root / "stray-foreign.txt").write_text("unknown\n", encoding="utf-8")

    completed, root = _run_install_release(tmp_path, manifest_ok=False)

    assert completed.returncode == 3, completed.stdout
    assert "non-certified content" in completed.stderr
    assert (root / "stray-foreign.txt").exists(), "a refusal must not delete content"
    assert (root / ".env").exists()
    assert not (root / "docker-compose.yml").exists(), "a refusal must install nothing"


def test_install_release_refuses_mismatched_release_content(tmp_path) -> None:
    """Negative: a drifted release tree is refused, never silently reinstalled."""
    app_root = tmp_path / "app_root"
    app_root.mkdir()
    (app_root / "docker-compose.yml").write_text(
        "services: {drifted: true}\n", encoding="utf-8"
    )

    completed, root = _run_install_release(tmp_path, manifest_ok=False)

    assert completed.returncode == 3, completed.stdout
    assert "non-certified content" in completed.stderr
    assert (root / "docker-compose.yml").read_text(encoding="utf-8").startswith(
        "services: {drifted"
    ), "the drifted tree must be left untouched"


def test_install_release_is_idempotent_when_already_installed(tmp_path) -> None:
    """Positive: a verified existing install returns early without reinstalling."""
    app_root = tmp_path / "app_root"
    app_root.mkdir()
    (app_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app_root / ".env").write_text("OWNER_PROVIDED=value\n", encoding="utf-8")

    completed, _root = _run_install_release(tmp_path, manifest_ok=True)

    assert completed.returncode == 0, completed.stderr
    assert "already installed and verified" in completed.stderr


def test_install_release_still_refuses_a_symlinked_runtime_root(tmp_path) -> None:
    """Regression: the single-root contract is unchanged by this fix."""
    target = tmp_path / "real_root"
    target.mkdir()
    link = tmp_path / "linked_root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this platform")

    bash = _usable_bash()
    if not bash:
        pytest.skip("no working bash to exercise install_release")
    script = _write_install_harness(tmp_path, _bootstrap_text())
    env = dict(os.environ)
    env.update(
        PATH=str(Path(bash).parent) + os.pathsep + env.get("PATH", ""),
        SRC=str(tmp_path),
        APP_ROOT=str(link),
        MANIFEST=str(tmp_path / "m.sha256"),
        SHA=CERTIFIED_SHA,
        STUB_MANIFEST_OK="1",
    )
    completed = subprocess.run([bash, str(script)], capture_output=True, text=True, env=env)
    assert completed.returncode == 3, completed.stdout
    assert "single-root contract" in completed.stderr


# --- M07 protection-preflight contract (extraction + incident observation) --------
# The certified preflight emits verdict.ready/reason plus evidence_shadow.* and
# strict_f11.reason_codes; M07 must consume exactly those fields, refuse malformed
# or contradictory evidence, and observe the REAL runtime incident store.

_PREFLIGHT_BEGIN = "# --- M07-PREFLIGHT-CONTRACT-BEGIN ---"
_PREFLIGHT_END = "# --- M07-PREFLIGHT-CONTRACT-END ---"


def _bootstrap_source() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def _preflight_contract_source() -> str:
    text = _bootstrap_source()
    assert _PREFLIGHT_BEGIN in text, "preflight contract block marker missing"
    assert _PREFLIGHT_END in text, "preflight contract block terminator missing"
    start = text.index(_PREFLIGHT_BEGIN) + len(_PREFLIGHT_BEGIN)
    return text[start : text.index(_PREFLIGHT_END)]


def _preflight_document(
    *,
    ready: bool,
    shadow_ready: bool,
    state: str,
    codes: list,
    incidents: list,
    sha: str = CERTIFIED_SHA,
    profile: str = "EVIDENCE_SHADOW",
) -> dict:
    return {
        "schema_version": 1,
        "candidate_sha": sha,
        "release_profile": profile,
        "read_only": True,
        "strict_f11": {
            "state": "HEALTHY" if ready else "UNAVAILABLE",
            "reason_codes": [] if ready else ["EXPOSURE_COVERAGE_INCOMPLETE"],
        },
        "evidence_shadow": {
            "ready": shadow_ready,
            "state": state,
            "blocking_reason_codes": list(codes),
            "blocking_incidents": list(incidents),
        },
        "verdict": {"ready": ready, "reason": "test verdict"},
    }


def _run_preflight_contract(tmp_path, document, rc, store):
    """Run the embedded contract block against one synthetic preflight document."""
    script = tmp_path / "contract.py"
    script.write_text(_preflight_contract_source(), encoding="utf-8", newline="\n")
    receipt = tmp_path / "protection-preflight.json"
    store_path = tmp_path / "system_incidents.json"
    if document is not None:
        receipt.write_text(json.dumps(document), encoding="utf-8")
    if store is not None:
        if isinstance(store, str):
            store_path.write_text(store, encoding="utf-8")
        else:
            store_path.write_text(json.dumps(store), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script), str(receipt), str(rc), CERTIFIED_SHA, str(store_path)],
        capture_output=True,
        text=True,
    )
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = None
    return completed, payload


def test_preflight_observes_runtime_data_not_release_data() -> None:
    """The preflight must mount $APP_ROOT/data, never the immutable release tree."""
    text = _bootstrap_source()
    start = text.index("preflight_evaluate() {")
    body = text[start : text.index("\n}\n", start)]
    assert '-v "$runtime_data:/app/data:ro"' in body
    assert '-v "$SRC/data:' not in body, "release data must never be mounted as runtime evidence"
    assert '-v "$SRC/app:/app/app:ro"' in body, "candidate code still comes from the release"
    assert '-L "$runtime_data"' in body
    assert '-L "$incident_store"' in body
    assert "INCIDENT_STATE_FILE" in text


def test_preflight_contract_extracts_a_ready_verdict(tmp_path) -> None:
    """READY is accepted only with a valid, non-active host incident store."""
    document = _preflight_document(
        ready=True, shadow_ready=True, state="READY", codes=[], incidents=[]
    )
    completed, payload = _run_preflight_contract(
        tmp_path, document, 0, {"incidents": {}, "schema_version": 2}
    )
    assert completed.returncode == 0, completed.stdout
    assert payload["ready"] is True
    assert payload["evidence_shadow_state"] == "READY"
    assert payload["incident_store"]["state"] == "VALID_EMPTY"
    assert payload["fresh_host_only"] is False


@pytest.mark.parametrize(
    "store",
    [
        pytest.param(None, id="absent-store"),
        pytest.param("not-json{", id="malformed-json"),
        pytest.param({"incidents": {}}, id="missing-schema"),
        pytest.param({"incidents": {}, "schema_version": 99}, id="unsupported-schema"),
        pytest.param({"incidents": {"SYSTEM_HEALTH:x": "not-a-dict"}}, id="non-dict-row"),
        pytest.param(
            {"incidents": {"SYSTEM_HEALTH:x": {"state": "OPEN"}}, "schema_version": 2},
            id="active-incident",
        ),
    ],
)
def test_preflight_ready_refuses_absent_or_contradictory_host_evidence(
    tmp_path, store
) -> None:
    """A READY verdict may not coexist with missing, corrupt or active incident state."""
    document = _preflight_document(
        ready=True, shadow_ready=True, state="READY", codes=[], incidents=[]
    )
    completed, _payload = _run_preflight_contract(tmp_path, document, 0, store)
    assert completed.returncode == 4, completed.stdout
    assert "contract_error" in completed.stdout


def test_preflight_ready_accepts_recovered_only_store(tmp_path) -> None:
    """Recovered rows are closed per the certified projection, so READY stays valid."""
    store = {
        "incidents": {"SYSTEM_HEALTH:kraken_read_only": {"state": "RECOVERED"}},
        "schema_version": 2,
    }
    document = _preflight_document(
        ready=True, shadow_ready=True, state="READY", codes=[], incidents=[]
    )
    completed, payload = _run_preflight_contract(tmp_path, document, 0, store)
    assert completed.returncode == 0, completed.stdout
    assert payload["incident_store"]["state"] == "VALID_RECOVERED_ONLY"
    assert payload["incident_store"]["recovered_records"] == 1
    assert payload["incident_store"]["active_records"] == 0



def test_preflight_contract_extracts_blocked_verdict_and_real_reason_codes(tmp_path) -> None:
    codes = ["COVERAGE_INCOMPLETE", "INCIDENT_UNREADABLE"]
    document = _preflight_document(
        ready=False, shadow_ready=False, state="BLOCKED", codes=codes, incidents=[]
    )
    completed, payload = _run_preflight_contract(tmp_path, document, 76, None)
    assert completed.returncode == 0, completed.stdout
    assert payload["ready"] is False
    assert payload["blocking_reason_codes"] == codes
    assert payload["strict_f11_reason_codes"] == ["EXPOSURE_COVERAGE_INCOMPLETE"]
    assert payload["incident_store"]["state"] == "ABSENT"
    assert payload["fresh_host_only"] is False



@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("verdict"), id="missing-verdict"),
        pytest.param(lambda d: d["verdict"].update(ready="true"), id="ready-not-bool"),
        pytest.param(lambda d: d.update(candidate_sha="0" * 40), id="sha-mismatch"),
        pytest.param(lambda d: d.update(release_profile="SAFE_BASELINE"), id="profile-mismatch"),
        pytest.param(lambda d: d.pop("evidence_shadow"), id="missing-evidence-shadow"),
        pytest.param(lambda d: d["strict_f11"].pop("reason_codes"), id="missing-strict-codes"),
        pytest.param(lambda d: d.update(read_only=False), id="not-read-only"),
        pytest.param(
            lambda d: d["evidence_shadow"].update(blocking_reason_codes="COVERAGE_INCOMPLETE"),
            id="codes-not-a-list",
        ),
    ],
)
def test_preflight_contract_refuses_malformed_evidence(tmp_path, mutate) -> None:
    document = _preflight_document(
        ready=False, shadow_ready=False, state="BLOCKED",
        codes=["COVERAGE_INCOMPLETE"], incidents=[],
    )
    mutate(document)
    completed, _payload = _run_preflight_contract(tmp_path, document, 76, None)
    assert completed.returncode == 4, completed.stdout
    assert "contract_error" in completed.stdout


@pytest.mark.parametrize(
    "document_kwargs,rc",
    [
        pytest.param(dict(ready=True, shadow_ready=False, state="BLOCKED",
                          codes=["COVERAGE_INCOMPLETE"], incidents=[]), 0,
                     id="ready-but-shadow-blocked"),
        pytest.param(dict(ready=True, shadow_ready=True, state="READY",
                          codes=[], incidents=[]), 76,
                     id="ready-with-blocked-exit-code"),
        pytest.param(dict(ready=False, shadow_ready=False, state="BLOCKED",
                          codes=["COVERAGE_INCOMPLETE"], incidents=[]), 0,
                     id="blocked-with-ready-exit-code"),
        pytest.param(dict(ready=True, shadow_ready=True, state="BLOCKED",
                          codes=[], incidents=[]), 0,
                     id="ready-with-blocked-state"),
    ],
)
def test_preflight_contract_refuses_contradictions(tmp_path, document_kwargs, rc) -> None:
    document = _preflight_document(**document_kwargs)
    completed, _payload = _run_preflight_contract(tmp_path, document, rc, None)
    assert completed.returncode == 4, completed.stdout



@pytest.mark.parametrize(
    "store,expected",
    [
        pytest.param(None, "ABSENT", id="absent"),
        pytest.param({"incidents": {}, "schema_version": 2}, "VALID_EMPTY", id="valid-empty"),
        pytest.param(
            {"incidents": {"SYSTEM_HEALTH:a": {"state": "RECOVERED"}},
             "schema_version": 2},
            "VALID_RECOVERED_ONLY", id="recovered-only"),
        pytest.param(
            {"incidents": {"SYSTEM_HEALTH:a": {"state": "RECOVERED"},
                           "SYSTEM_HEALTH:b": {"state": "OPEN"}},
             "schema_version": 2},
            "VALID_OPEN_INCIDENTS", id="mixed-open-and-recovered"),
        pytest.param(
            {"incidents": {"SYSTEM_HEALTH:a": {"state": "OPEN"}}, "schema_version": 1},
            "VALID_OPEN_INCIDENTS", id="schema-1-readable"),
        pytest.param("not-json{", "MALFORMED", id="malformed-json"),
        pytest.param({"incidents": {}}, "MALFORMED", id="missing-schema"),
        pytest.param({"incidents": {}, "schema_version": 99}, "MALFORMED", id="unsupported-schema"),
        pytest.param({"incidents": {}, "schema_version": "2"}, "MALFORMED", id="schema-not-int"),
        pytest.param({"incidents": {}, "schema_version": True}, "MALFORMED", id="schema-bool"),
        pytest.param({"schema_version": 2}, "MALFORMED", id="missing-incidents-map"),
        pytest.param({"incidents": [], "schema_version": 2}, "MALFORMED", id="incidents-not-a-map"),
        pytest.param({"incidents": {"SYSTEM_HEALTH:a": "oops"}, "schema_version": 2},
                     "MALFORMED", id="non-dict-row"),
        pytest.param('{"incidents": {}, "schema_version": 2, "x": NaN}',
                     "MALFORMED", id="nonfinite-json"),
        pytest.param([], "MALFORMED", id="not-an-object"),
    ],
)
def test_preflight_contract_classifies_incident_state(tmp_path, store, expected) -> None:

    document = _preflight_document(
        ready=False, shadow_ready=False, state="BLOCKED",
        codes=["COVERAGE_INCOMPLETE"], incidents=[],
    )
    completed, payload = _run_preflight_contract(tmp_path, document, 76, store)
    assert completed.returncode == 0, completed.stdout
    assert payload["incident_store"]["state"] == expected


@pytest.mark.parametrize(
    "store",
    [
        pytest.param(None, id="absent-store"),
        pytest.param({"incidents": {}, "schema_version": 2}, id="valid-empty-store"),
        pytest.param({"incidents": {"SYSTEM_HEALTH:x": {"state": "OPEN"}}, "schema_version": 2},
                     id="active-store"),
        pytest.param({"incidents": {"SYSTEM_HEALTH:x": {"state": "RECOVERED"}},
                      "schema_version": 2}, id="recovered-only-store"),
        pytest.param("not-json{", id="malformed-store"),
    ],
)
def test_preflight_never_acknowledges_unknown_exposure(tmp_path, store) -> None:
    """No acknowledgement path may be offered for a real certified block."""
    document = _preflight_document(
        ready=False, shadow_ready=False, state="BLOCKED",
        codes=["COVERAGE_INCOMPLETE", "INCIDENT_UNREADABLE"], incidents=[],
    )
    completed, payload = _run_preflight_contract(tmp_path, document, 76, store)
    assert completed.returncode == 0, completed.stdout
    assert payload["fresh_host_only"] is False


def test_preflight_contract_output_carries_no_secret_material(tmp_path) -> None:
    """The evaluated summary is bounded evidence: codes, state, digest, sizes only."""
    document = _preflight_document(
        ready=False, shadow_ready=False, state="BLOCKED",
        codes=["COVERAGE_INCOMPLETE"], incidents=[],
    )
    completed, payload = _run_preflight_contract(
        tmp_path, document, 76, {"incidents": {}, "schema_version": 2}
    )
    assert completed.returncode == 0, completed.stdout
    assert set(payload) == {
        "ready", "reason", "reasons", "blocking_reason_codes", "blocking_incidents",
        "evidence_shadow_state", "strict_f11_reason_codes", "incident_store",
        "fresh_host_only",
    }
    assert set(payload["incident_store"]) == {
        "state", "sha256", "bytes", "active_records", "recovered_records",
    }
    assert len(payload["incident_store"]["sha256"]) == 64


def test_preflight_records_store_digest_for_drift_detection() -> None:
    """prepare records the digest; authorize and execute must re-verify it."""
    text = _bootstrap_source()

    def body(name: str) -> str:
        start = text.index(f"{name}() {{")
        return text[start : text.index("\n}\n", start)]

    assert "assert_incident_store_unchanged" in body("mode_authorize")
    assert "assert_incident_store_unchanged" in body("mode_execute")
    assert "incident store drifted since prepare" in text
    assert "incident_store_digest" in body("assert_incident_store_unchanged")



