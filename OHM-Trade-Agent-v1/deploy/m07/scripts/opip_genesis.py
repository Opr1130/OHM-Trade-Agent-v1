"""M07 R5: owner-authorized fresh canonical genesis for a brand-new V2 history.

Design rules:
  * a fresh genesis creates a NEW, empty canonical store. It never copies,
    imports, merges, migrates or "cleans" historical V1 data, learning evidence
    or calibration state, and it has no option that can name such a source;
  * schema initialization is delegated to the CERTIFIED writer
    (``app.opip.canonical.writer.CanonicalWriter`` ->
    ``app.opip.canonical.schema.initialize_schema``), so this toolkit adds no
    competing database writer and no second DDL;
  * the published store is claimed with a no-replace primitive (``os.link``), so
    an existing database, sidecar pair or unknown file can never be overwritten,
    adopted or silently discarded;
  * the genesis receipt is *evidence*, not authority: writer activation
    re-derives every recorded fact from the live store and from the certified
    source bytes before accepting it;
  * the two operator statements that make a genesis *intentional* -- the exact
    destination (``--confirm-destination``) and the authoritative evidence path
    (``--receipt-out``) -- are preconditions, not post-hoc checks. Missing either
    one is refused before the canonical directory, the store or any evidence file
    is created, so a mistyped runtime root can never produce an orphaned store
    and evidence-free history;
  * host identity is read only from the pinned droplet metadata endpoint. The
    endpoint is not operator-selectable on the command line, and an in-process
    override is accepted only for a literal loopback test stub, so an untrusted
    or self-declared metadata URL can never substitute the production identity.

Verdicts mirror the certified recovery engine so the existing host scripts and
operator habits keep working: PASS(0), FAIL(1), PLATFORM_BLOCKED(2),
INSUFFICIENT(2), REFUSED(3).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import ipaddress
import json
import os
import platform
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: The certified application SHA. V2 genesis must never be produced by any
#: other build; ``--certified-sha`` is compared against this constant.
CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
#: The V2 droplet identity the bootstrap already pins.
DROPLET_ID = "610307364"
#: The only metadata path that carries a host identity on the V2 droplet.
METADATA_PATH = "/metadata/v1/id"
#: The single production identity endpoint. It is deliberately a constant rather
#: than a command-line option: an operator (or anything that can edit a command
#: line) must never be able to point the identity read at a service it controls.
DEFAULT_IMDS_URL = f"http://169.254.169.254{METADATA_PATH}"

GENESIS_OPERATION = "canonical-fresh-genesis"
NATIVE_RECOVERY_OPERATION = "canonical-native-restore"
GENESIS_RECEIPT_SCHEMA = 1

CANONICAL_DB_NAME = "opip_canonical_v1.sqlite3"
#: Parent directory names in path order: ``<root>/data/opip/canonical/<db>``.
CANONICAL_PARENT_NAMES = ("data", "opip", "canonical")
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
STAGING_MARKER = ".opip-genesis."
DURABILITY_PROBE_PREFIX = ".opip-genesis.probe."
INITIAL_EPOCH = 1
INITIAL_SEQUENCE = 1
DEFAULT_MAX_AGE_HOURS = 24.0
CLOCK_SKEW = timedelta(minutes=5)

#: Certified modules whose bytes initialized the store. Their digests are part
#: of the receipt, so a receipt is bound to the exact certified code that
#: produced the database.
CERTIFIED_MODULE_FILES = (
    "app/opip/canonical/schema.py",
    "app/opip/canonical/writer.py",
    "app/opip/canonical/paths.py",
)

_VERDICT_EXIT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "PLATFORM_BLOCKED": 2,
    "INSUFFICIENT": 2,
    "REFUSED": 3,
}


class GenesisRefused(RuntimeError):
    """An authoritative precondition is not satisfied; nothing may be created."""


class GenesisProvenanceError(RuntimeError):
    """Certified source bytes needed for genesis provenance are unavailable."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def receipt_digest(record: dict) -> str:
    """SHA-256 over the receipt body, excluding the digest field itself."""
    body = {key: value for key, value in record.items() if key != "receipt_sha256"}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()

def certified_module_digests(app_root: Path) -> dict[str, str]:
    """Digest the certified modules that own schema initialization."""
    root = Path(app_root)
    digests: dict[str, str] = {}
    for relative in CERTIFIED_MODULE_FILES:
        path = root / relative
        if not path.is_file():
            raise GenesisProvenanceError(
                f"certified source is incomplete: {path} is missing"
            )
        digests[relative] = sha256_file(path)
    return digests


def host_identity(
    imds_url: str = DEFAULT_IMDS_URL, *, timeout: float = 5.0
) -> tuple[str | None, str]:
    """Read the droplet identity from metadata. Returns ``(identity, detail)``.

    ``identity`` is ``None`` when the endpoint is untrusted, when the metadata
    service cannot be read, or when it returns something that is not a plausible
    droplet id; callers must then refuse. No fallback (hostname,
    ``platform.node()``, environment) is ever used as an identity substitute,
    because those are self-declared.
    """
    trusted, trust_detail = imds_endpoint_trust(imds_url)
    if not trusted:
        return None, trust_detail
    try:
        with urllib.request.urlopen(imds_url, timeout=timeout) as response:  # noqa: S310
            raw = response.read(256).decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"metadata identity unavailable at {imds_url}: {exc}"
    if not raw or len(raw) > 64 or not all(ch.isalnum() or ch in "-._" for ch in raw):
        return None, f"metadata identity is not a plausible host id: {raw[:64]!r}"
    return raw, f"metadata identity read from {imds_url}"


def imds_endpoint_trust(url: str) -> tuple[bool, str]:
    """Classify a metadata URL as an accepted host-identity source.

    Returns ``(trusted, detail)``. A URL is accepted only when it is the pinned
    droplet metadata endpoint, or a literal loopback address carrying the same
    metadata path, which is what the isolated test stub uses. Everything else --
    a host name, a public or private network address, any other path, any other
    scheme -- is a self-declared identity substitute and is refused before a
    network read or a filesystem mutation. Only the pinned endpoint yields
    production identity authority; a loopback stub is recorded as such and never
    counts as proof about the production droplet.
    """
    raw = str(url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        return False, f"metadata endpoint is not a URL: {raw!r} ({exc})"
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.path != METADATA_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return False, (
            "metadata endpoint must be the pinned plain-HTTP metadata URL "
            f"{DEFAULT_IMDS_URL}; observed {raw!r}"
        )
    if raw == DEFAULT_IMDS_URL:
        return True, f"pinned droplet metadata endpoint {DEFAULT_IMDS_URL}"
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return False, (
            "metadata endpoint host is not a literal IP address, so it is a "
            f"self-declared identity source: {raw!r}"
        )
    if address.is_loopback:
        return True, (
            "loopback metadata stub "
            f"{raw} (test evidence only; no production identity authority)"
        )
    return False, (
        "metadata endpoint is neither the pinned droplet metadata service nor a "
        f"loopback test stub: {raw!r}"
    )


def schema_fingerprint(connection: sqlite3.Connection) -> str:
    """Digest the store's schema objects, proving which DDL produced it."""
    rows = connection.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
        "ORDER BY type, name"
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def sqlite_sidecars(db_path: Path) -> tuple[Path, ...]:
    target = Path(db_path)
    return tuple(
        side
        for side in (Path(f"{target}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES)
        if side.exists()
    )


def store_facts(db_path: Path) -> dict:
    """Independent facts about a canonical store, derived from the file itself."""
    db = Path(db_path)
    facts: dict = {
        "destination_db": str(db.resolve()),
        "db_sha256": sha256_file(db) if db.is_file() else None,
        "sidecars": [str(side) for side in sqlite_sidecars(db)],
        "history_epoch": None,
        "next_local_sequence": None,
        "event_count": None,
        "schema_version": None,
        "schema_fingerprint": None,
        "integrity_check": None,
        "foreign_key_violations": None,
    }
    if not db.is_file():
        return facts
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT schema_version, history_epoch, next_local_sequence "
            "FROM meta WHERE id = 1"
        ).fetchone()
        if row is not None:
            facts["schema_version"] = int(row[0])
            facts["history_epoch"] = int(row[1])
            facts["next_local_sequence"] = int(row[2])
        facts["event_count"] = int(
            connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        )
        facts["schema_fingerprint"] = schema_fingerprint(connection)
        facts["integrity_check"] = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        facts["foreign_key_violations"] = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
    finally:
        connection.close()
    return facts


@dataclass(frozen=True)
class CertifiedStack:
    """The certified primitives genesis reuses instead of re-implementing."""

    CanonicalStoreLock: type
    CanonicalWriter: type
    CanonicalStoreBusyError: type
    CanonicalDurabilityError: type
    CanonicalDbValidationError: type
    initialize_schema: object
    canonical_store_path: object
    normalize_to_rollback_journal: object
    assert_rollback_journal_backup: object
    assert_sidecar_free: object
    validate_canonical_sqlite: object
    fsync_file_required: object
    fsync_directory_required: object
    schema_version: int


def load_certified(app_root: Path) -> CertifiedStack:
    """Import the certified canonical primitives from ``app_root``."""
    root = str(Path(app_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from app.opip.canonical.backup import (
            assert_rollback_journal_backup,
            assert_sidecar_free,
            normalize_to_rollback_journal,
        )
        from app.opip.canonical.paths import SCHEMA_VERSION
        from app.opip.canonical.schema import (
            CanonicalDbValidationError,
            CanonicalDurabilityError,
            CanonicalStoreBusyError,
            CanonicalStoreLock,
            canonical_store_path,
            fsync_directory_required,
            fsync_file_required,
            initialize_schema,
            validate_canonical_sqlite,
        )
        from app.opip.canonical.writer import CanonicalWriter
    except Exception as exc:  # noqa: BLE001 - fail closed with the reason
        raise GenesisProvenanceError(
            f"cannot import the certified canonical modules from {app_root}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return CertifiedStack(
        CanonicalStoreLock=CanonicalStoreLock,
        CanonicalWriter=CanonicalWriter,
        CanonicalStoreBusyError=CanonicalStoreBusyError,
        CanonicalDurabilityError=CanonicalDurabilityError,
        CanonicalDbValidationError=CanonicalDbValidationError,
        initialize_schema=initialize_schema,
        canonical_store_path=canonical_store_path,
        normalize_to_rollback_journal=normalize_to_rollback_journal,
        assert_rollback_journal_backup=assert_rollback_journal_backup,
        assert_sidecar_free=assert_sidecar_free,
        validate_canonical_sqlite=validate_canonical_sqlite,
        fsync_file_required=fsync_file_required,
        fsync_directory_required=fsync_directory_required,
        schema_version=int(SCHEMA_VERSION),
    )


def canonical_layout_ok(db_path: Path) -> tuple[bool, str]:
    """Require the certified canonical layout: ``.../data/opip/canonical/<db>``."""
    db = Path(db_path)
    if len(db.parent.parts) < len(CANONICAL_PARENT_NAMES):
        return False, (
            "destination is not the certified canonical layout "
            f"(expected data/opip/canonical/{CANONICAL_DB_NAME}); observed {db}"
        )
    observed = (db.name,) + db.parent.parts[-len(CANONICAL_PARENT_NAMES):]
    expected = (CANONICAL_DB_NAME,) + CANONICAL_PARENT_NAMES
    if observed != expected:
        return False, (
            "destination is not the certified canonical layout "
            f"(expected data/opip/canonical/{CANONICAL_DB_NAME}); observed {db}"
        )
    return True, f"destination layout is the certified canonical layout: {db}"




def _check(checks: list[dict], check_id: str, ok: bool, detail: str) -> bool:
    checks.append({"id": check_id, "status": "PASS" if ok else "FAIL", "detail": detail})
    return ok


def _write_json_create_only(path: Path, record: dict) -> None:
    """Publish a receipt without ever replacing an existing one.

    ``os.link`` is the no-replace claim: it fails when the path already exists,
    so evidence can never be silently overwritten by a later run.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = dict(record)
    body["receipt_sha256"] = receipt_digest(record)
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    staged = target.with_name(f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:16]}")
    try:
        with staged.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(str(staged), str(target))
    finally:
        with contextlib.suppress(OSError):
            if staged.exists():
                staged.unlink()


def _failure_evidence_path(receipt_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return receipt_path.with_name(
        f"{receipt_path.name}.{stamp}.{uuid.uuid4().hex[:8]}.failed.json"
    )


def _cleanup_staging(staged: Path) -> None:
    """Remove only this invocation's private staging artifacts.

    Scoped to the ``.opip-genesis.`` marker so this helper can never be pointed
    at a real canonical file or at an operator's evidence.
    """
    if STAGING_MARKER not in Path(staged).name:
        return
    for suffix in ("",) + SQLITE_SIDECAR_SUFFIXES + (".writer.lock",):
        candidate = Path(f"{staged}{suffix}")
        with contextlib.suppress(OSError):
            if candidate.exists():
                candidate.unlink()


def _prove_platform_durability(stack: CertifiedStack, directory: Path) -> None:
    """Require provable file *and* namespace durability before creating a store.

    Runs before any canonical artifact exists, so a platform that cannot prove
    directory durability (Windows, by the certified contract) refuses with the
    store untouched instead of publishing a file it cannot honestly report as
    durable.
    """
    probe = (
        Path(directory)
        / f"{DURABILITY_PROBE_PREFIX}{os.getpid()}.{uuid.uuid4().hex[:16]}"
    )
    try:
        probe.write_bytes(b"")
        stack.fsync_file_required(probe)
        stack.fsync_directory_required(directory)
    finally:
        with contextlib.suppress(OSError):
            if probe.exists():
                probe.unlink()



def fresh_genesis(
    *,
    app_root: Path,
    live_db: Path,
    receipt_out: Path | None = None,
    certified_sha: str = CERTIFIED_SHA,
    expected_host: str = DROPLET_ID,
    imds_url: str = DEFAULT_IMDS_URL,
    confirm_destination: str | None = None,
    authorize: bool = False,
) -> dict:
    """Create a brand-new empty canonical store with a verifiable receipt.

    Every precondition is evaluated *before* anything is created: authorization,
    the authoritative receipt path, the certified SHA, a trusted metadata
    endpoint, the metadata host identity, the certified destination layout, the
    operator's explicit destination confirmation, the receipt location and the
    absence of any existing store, sidecar, writer or unknown file. A refusal
    leaves the canonical directory, the store and the authoritative receipt
    untouched, and publishes its own separate diagnostic evidence beside (never
    on top of) the authoritative receipt path.

    ``imds_url`` exists for the isolated test stub only. It must be the pinned
    droplet endpoint or a literal loopback address; the command line does not
    expose it, so production identity cannot be self-declared.
    """
    app_root = Path(app_root)
    live_db = Path(live_db)
    canonical_dir = live_db.parent
    receipt_path = Path(receipt_out) if receipt_out else None
    started_at = utc_now()
    result: dict = {
        "receipt_schema": GENESIS_RECEIPT_SCHEMA,
        "operation": GENESIS_OPERATION,
        "started_at_utc": started_at,
        "app_root": str(app_root),
        "destination_db": str(live_db),
        "canonical_initialized": False,
        "history_imported": False,
        "checks": [],
        "verdict": "FAIL",
    }
    checks: list[dict] = result["checks"]

    def finish(verdict: str) -> dict:
        result["verdict"] = verdict
        result["completed"] = verdict == "PASS"
        result["finished_at_utc"] = utc_now()
        # ``generated_at_utc`` is the receipt timestamp the writer gate ages.
        result["generated_at_utc"] = result["finished_at_utc"]
        if receipt_path is None:
            # Unreachable in a normal run: RECEIPT_REQUIRED refuses first. Kept
            # as a defensive stop so a future edit can never publish a PASS
            # without an authoritative receipt path.
            result["receipt_error"] = (
                "authoritative receipt path is required (--receipt-out)"
            )
            if verdict == "PASS":
                result["verdict"] = "FAIL"
                result["completed"] = False
            return result
        target = (
            receipt_path if verdict == "PASS" else _failure_evidence_path(receipt_path)
        )
        # Record the path before hashing so the file and the returned document
        # carry the same self-digest.
        result["receipt"] = str(target)
        try:
            _write_json_create_only(target, result)
        except Exception as exc:  # noqa: BLE001 - evidence failure must not mask the verdict
            result["receipt_error"] = f"{type(exc).__name__}: {exc}"
            if verdict == "PASS":
                # Without its receipt the store is evidence-free: never report PASS.
                result["verdict"] = "FAIL"
                result["completed"] = False
        return result

    def fail(check_id: str, detail: str, verdict: str = "REFUSED") -> dict:
        _check(checks, check_id, False, detail)
        return finish(verdict)

    if not _check(
        checks,
        "AUTHORIZATION",
        authorize is True,
        "owner authorization recorded (--authorize)"
        if authorize is True
        else "owner authorization is required (--authorize); genesis starts a new "
        "canonical history and is never implicit",
    ):
        return finish("REFUSED")

    # The authoritative evidence path is a precondition, not a post-hoc check:
    # a genesis that cannot publish its receipt must not create a store, because
    # a published store without evidence is an orphan the writer gate can never
    # admit and a retry can never replace.
    if not _check(
        checks,
        "RECEIPT_REQUIRED",
        receipt_path is not None,
        f"an authoritative receipt path is required (--receipt-out): genesis never "
        f"creates a store at {live_db} that it cannot produce evidence for",
    ):
        return finish("REFUSED")

    if not _check(
        checks,
        "CERTIFIED_SHA",
        certified_sha == CERTIFIED_SHA,
        f"certified_sha={certified_sha} must equal the certified application SHA "
        f"{CERTIFIED_SHA}",
    ):
        return finish("REFUSED")

    # Which service may answer for this host is decided before it is contacted.
    endpoint_trusted, endpoint_detail = imds_endpoint_trust(imds_url)
    if not _check(
        checks,
        "HOST_IDENTITY_ENDPOINT",
        endpoint_trusted,
        f"{endpoint_detail}; production identity is read only from "
        f"{DEFAULT_IMDS_URL}",
    ):
        return finish("REFUSED")

    observed_host, host_detail = host_identity(imds_url)
    if not _check(
        checks,
        "HOST_IDENTITY",
        observed_host is not None and observed_host == expected_host,
        f"observed={observed_host} expected={expected_host}; {host_detail}",
    ):
        return finish("REFUSED")
    result["host"] = observed_host
    result["host_identity_source"] = imds_url
    result["host_identity_endpoint"] = endpoint_detail
    result["host_identity_production_endpoint"] = imds_url == DEFAULT_IMDS_URL
    result["host_identity_proven"] = True
    result["hostname"] = platform.node()

    layout_ok, layout_detail = canonical_layout_ok(live_db)
    if not _check(checks, "DESTINATION_LAYOUT", layout_ok, layout_detail):
        return finish("REFUSED")

    # A certified *shape* is not an operator confirmation. Without an explicit
    # confirmation of this exact path, a mistyped runtime root or mount would
    # silently initialize the wrong canonical store.
    if confirm_destination is None:
        if not _check(
            checks,
            "DESTINATION_CONFIRMED",
            False,
            "an explicit destination confirmation is required "
            f"(--confirm-destination {live_db}); genesis never creates a store at "
            "a path the operator did not name",
        ):
            return finish("REFUSED")
    else:
        confirmed = Path(confirm_destination).expanduser().resolve()
        if not _check(
            checks,
            "DESTINATION_CONFIRMED",
            confirmed == live_db.expanduser().resolve(),
            f"confirmed destination {confirmed} != target {live_db.resolve()}",
        ):
            return finish("REFUSED")

    resolved_receipt = receipt_path.expanduser().resolve()
    inside_store = resolved_receipt.parent == canonical_dir.expanduser().resolve()
    if not _check(
        checks,
        "RECEIPT_LOCATION",
        not inside_store and resolved_receipt != live_db.expanduser().resolve(),
        f"receipt {resolved_receipt} must live outside the canonical store "
        "directory",
    ):
        return finish("REFUSED")
    if not _check(
        checks,
        "RECEIPT_PRESENT",
        not receipt_path.exists(),
        f"a receipt already exists at {receipt_path}; genesis never overwrites "
        "evidence",
    ):
        return finish("REFUSED")

    if not _check(
        checks,
        "EXISTING_CANONICAL_STORE",
        not live_db.exists() and not live_db.is_symlink(),
        f"refusing to touch an existing canonical destination: {live_db}",
    ):
        return finish("REFUSED")

    sidecars = sqlite_sidecars(live_db)
    if not _check(
        checks,
        "SQLITE_SIDECARS",
        not sidecars,
        "conflicting SQLite sidecars exist for the destination: "
        f"{[str(side) for side in sidecars]}",
    ):
        return finish("REFUSED")

    if canonical_dir.exists() and not canonical_dir.is_dir():
        return fail(
            "CANONICAL_DIRECTORY",
            f"canonical directory is not a directory: {canonical_dir}",
        )

    # A live writer is refused before any file listing, so the reported reason is
    # the writer rather than an incidental unknown file.
    writer_socket = canonical_dir / "writer.sock"
    if not _check(
        checks,
        "EXISTING_WRITER",
        not writer_socket.exists(),
        f"a canonical writer socket is present: {writer_socket}",
    ):
        return finish("REFUSED")

    allowed_entries = {f"{live_db.name}.writer.lock", writer_socket.name}
    entries = (
        sorted(path.name for path in canonical_dir.iterdir())
        if canonical_dir.is_dir()
        else []
    )
    unknown = [name for name in entries if name not in allowed_entries]
    interrupted = [name for name in unknown if name.startswith(STAGING_MARKER)]
    if not _check(
        checks,
        "INTERRUPTED_GENESIS_ARTIFACT",
        not interrupted,
        f"an interrupted genesis left store artifacts behind: {interrupted}; inspect "
        "and remove them explicitly before retrying (genesis never adopts or "
        "deletes an existing database)",
    ):
        return finish("REFUSED")
    if not _check(
        checks,
        "UNKNOWN_DATABASE_FILE",
        not unknown,
        "the canonical directory holds files this operation did not create and does "
        f"not recognise: {unknown}; a fresh genesis requires an empty canonical "
        "directory",
    ):
        return finish("REFUSED")


    try:
        stack = load_certified(app_root)
    except GenesisProvenanceError as exc:
        return fail("CERTIFIED_MODULES", str(exc), verdict="INSUFFICIENT")

    result["generation_provenance"] = {
        "toolkit": "deploy/m07/scripts/opip_genesis.py",
        "toolkit_sha256": sha256_file(Path(__file__).resolve()),
        "toolkit_path": str(Path(__file__).resolve()),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "schema_initializer": "app.opip.canonical.schema.initialize_schema",
        "schema_initializer_via": (
            "app.opip.canonical.writer.CanonicalWriter (certified single writer)"
        ),
        "publish_primitive": "os.link (no-replace claim)",
        "durability": {
            "file": "app.opip.canonical.schema.fsync_file_required",
            "directory": "app.opip.canonical.schema.fsync_directory_required",
        },
        "previous_canonical_state": "NO_CANONICAL_STORE_AT_DESTINATION",
        "historical_sources_read": [],
        "v1_archives_touched": False,
        "history_imported": False,
    }
    try:
        result["certified_modules"] = certified_module_digests(app_root)
    except GenesisProvenanceError as exc:
        return fail("CERTIFIED_MODULES", str(exc), verdict="INSUFFICIENT")

    canonical_dir.mkdir(parents=True, exist_ok=True)
    staged = canonical_dir / (
        f"{STAGING_MARKER}{os.getpid()}.{uuid.uuid4().hex[:16]}.sqlite3"
    )
    if staged.resolve() == live_db.resolve():
        return fail("STAGING_PATH", "genesis staging path collides with the destination")
    try:
        store_lock = stack.CanonicalStoreLock(live_db)
        try:
            store_lock.acquire()
        except stack.CanonicalStoreBusyError as exc:
            return fail("EXISTING_WRITER", f"canonical store is already owned: {exc}")
        try:
            _check(checks, "STORE_OWNERSHIP", True, "exclusive store ownership proven")
            # Durability is proven only after ownership, so a competing writer is
            # always reported as such rather than as a platform limitation.
            try:
                _prove_platform_durability(stack, canonical_dir)
            except stack.CanonicalDurabilityError as exc:
                return fail("PLATFORM_DURABILITY", str(exc), verdict="PLATFORM_BLOCKED")
            _check(
                checks,
                "PLATFORM_DURABILITY",
                True,
                "file and directory durability proven on this filesystem",
            )
            # The certified single writer initializes the schema; this toolkit
            # never executes DDL of its own.
            writer = stack.CanonicalWriter(staged)

            try:
                writer.checkpoint_wal()
            finally:
                writer.close()
            stack.normalize_to_rollback_journal(staged)
            stack.assert_rollback_journal_backup(staged)
            stack.assert_sidecar_free(
                staged, field_name="staged genesis store", error_type=GenesisRefused
            )
            stack.validate_canonical_sqlite(staged)
            staged_facts = store_facts(staged)
            if staged_facts["history_epoch"] != INITIAL_EPOCH or int(
                staged_facts["next_local_sequence"] or 0
            ) != INITIAL_SEQUENCE:
                return fail(
                    "STAGED_INITIAL_COORDINATES",
                    "certified schema initialization did not produce the genesis "
                    f"coordinates: epoch={staged_facts['history_epoch']} "
                    f"sequence={staged_facts['next_local_sequence']}",
                )
            if int(staged_facts["event_count"] if staged_facts["event_count"] is not None else -1) != 0:
                return fail(
                    "STAGED_EVENT_COUNT",
                    f"staged store is not empty: events={staged_facts['event_count']}",
                )
            stack.fsync_file_required(staged)
            if live_db.exists() or live_db.is_symlink():
                return fail(
                    "EXISTING_CANONICAL_STORE",
                    f"destination appeared during genesis: {live_db}",
                )
            try:
                os.link(str(staged), str(live_db))
            except FileExistsError:
                return fail(
                    "EXISTING_CANONICAL_STORE",
                    f"destination was claimed by another process: {live_db}",
                )
            except OSError as exc:
                return fail(
                    "NO_REPLACE_PUBLISH",
                    "no-replace publication is unavailable for this destination; "
                    f"refusing to fall back to a replace-capable call: {exc}",
                )
        finally:
            store_lock.release()
    except Exception as exc:  # noqa: BLE001 - a certified failure must be evidence
        # Any certified initialization/publication failure becomes a recorded
        # FAIL with no published store, never an unlabelled traceback.
        return fail(
            "CERTIFIED_INITIALIZATION",
            f"{type(exc).__name__}: {exc}",
            verdict="FAIL",
        )
    finally:
        _cleanup_staging(staged)



    try:
        stack.fsync_directory_required(canonical_dir)
    except stack.CanonicalDurabilityError as exc:
        return fail("PUBLISH_DURABLE", str(exc), verdict="PLATFORM_BLOCKED")
    _check(
        checks,
        "PUBLISH_DURABLE",
        True,
        "published store and its directory entry are durable",
    )

    installed = store_facts(live_db)
    result.update(
        {
            "canonical_initialized": True,
            "certified_sha": certified_sha,
            "destination_db": str(live_db.resolve()),
            "destination_db_sha256": installed["db_sha256"],
            "schema": {
                "schema_version": installed["schema_version"],
                "fingerprint": installed["schema_fingerprint"],
                "journal_mode": "delete",
                "sidecars": installed["sidecars"],
            },
            "initial_epoch": {
                "history_epoch": installed["history_epoch"],
                "next_local_sequence": installed["next_local_sequence"],
            },
            "historical_events": 0,
            "events_table_rows": installed["event_count"],
            "integrity_check": installed["integrity_check"],
            "foreign_key_violations": installed["foreign_key_violations"],
        }
    )

    problems: list[str] = []
    if installed["sidecars"]:
        problems.append(f"published store has sidecars: {installed['sidecars']}")
    if installed["schema_version"] != stack.schema_version:
        problems.append(
            f"schema_version={installed['schema_version']} != certified "
            f"{stack.schema_version}"
        )
    if installed["history_epoch"] != INITIAL_EPOCH:
        problems.append(
            f"history_epoch={installed['history_epoch']} != {INITIAL_EPOCH}"
        )
    if installed["next_local_sequence"] != INITIAL_SEQUENCE:
        problems.append(
            f"next_local_sequence={installed['next_local_sequence']} != "
            f"{INITIAL_SEQUENCE}"
        )
    if installed["event_count"] != 0:
        problems.append(f"event_count={installed['event_count']} != 0")
    if installed["integrity_check"] != "ok":
        problems.append(f"integrity_check={installed['integrity_check']}")
    if installed["foreign_key_violations"] != 0:
        problems.append(f"foreign_key_violations={installed['foreign_key_violations']}")
    if not installed["schema_fingerprint"]:
        problems.append("schema fingerprint unavailable")
    if problems:
        return fail("INSTALLED_STORE_VERIFICATION", "; ".join(problems), verdict="FAIL")
    _check(
        checks,
        "INSTALLED_STORE_VERIFICATION",
        True,
        "installed store is an empty certified-schema genesis store "
        f"(epoch={INITIAL_EPOCH}, sequence={INITIAL_SEQUENCE}, events=0, integrity=ok, "
        "foreign_keys=0)",
    )
    return finish("PASS")



def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _receipt_age_problems(
    receipt: dict, max_age_hours: float, now: datetime | None
) -> list[str]:
    """Refuse stale or future-dated receipts; a receipt is evidence with an age."""
    problems: list[str] = []
    try:
        generated = _parse_timestamp(str(receipt.get("generated_at_utc", "")))
    except ValueError:
        return ["receipt has no usable generated_at_utc"]
    reference = now or datetime.now(timezone.utc)
    if generated - reference > CLOCK_SKEW:
        problems.append(
            f"receipt is dated in the future ({generated.isoformat()} > "
            f"{reference.isoformat()})"
        )
    age_hours = (reference - generated).total_seconds() / 3600.0
    if age_hours > max_age_hours:
        problems.append(
            f"receipt is stale ({age_hours:.1f}h > {max_age_hours}h); regenerate it "
            "from the current empty store before activating the writer"
        )
    return problems


def verify_genesis_receipt(
    *,
    receipt_path: Path,
    db: Path,
    app_root: Path,
    expected_host: str = DROPLET_ID,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    expected_certified_sha: str = CERTIFIED_SHA,
    now: datetime | None = None,
) -> dict:
    """Independently re-verify a fresh-genesis receipt against the live store.

    Nothing the receipt *claims* is trusted on its own: destination bytes,
    schema, coordinates, integrity, foreign keys, certified source digests, host
    and age are all re-derived here. A fabricated or tampered receipt cannot
    pass, because it cannot make the live store match.
    """
    receipt_path, db, app_root = Path(receipt_path), Path(db), Path(app_root)
    problems: list[str] = []
    facts = store_facts(db)
    result: dict = {
        "verdict": "REFUSED",
        "receipt_kind": GENESIS_OPERATION,
        "receipt": str(receipt_path),
        "problems": problems,
        "live": facts,
    }
    if not receipt_path.is_file():
        problems.append(f"fresh-genesis receipt missing: {receipt_path}")
        return result
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(
            f"fresh-genesis receipt unreadable: {type(exc).__name__}: {exc}"
        )
        return result
    if not isinstance(receipt, dict):
        problems.append("fresh-genesis receipt is not a JSON object")
        return result


    if receipt.get("receipt_schema") != GENESIS_RECEIPT_SCHEMA:
        problems.append(f"receipt_schema={receipt.get('receipt_schema')}")
    if receipt.get("operation") != GENESIS_OPERATION:
        problems.append(
            f"operation={receipt.get('operation')} is not {GENESIS_OPERATION}"
        )
    if receipt.get("completed") is not True or receipt.get("verdict") != "PASS":
        problems.append(
            "receipt is not a completed PASS "
            f"(completed={receipt.get('completed')}, verdict={receipt.get('verdict')})"
        )
    recorded_digest = str(receipt.get("receipt_sha256") or "")
    if not recorded_digest or recorded_digest != receipt_digest(receipt):
        problems.append(
            "receipt self-digest does not match its body (tampered or truncated "
            "receipt)"
        )
    if receipt.get("certified_sha") != expected_certified_sha:
        problems.append(
            f"receipt certified_sha={receipt.get('certified_sha')} != "
            f"{expected_certified_sha}"
        )
    if receipt.get("host_identity_proven") is not True:
        problems.append("receipt does not record a proven host identity")
    if str(receipt.get("host") or "") != expected_host:
        problems.append(
            f"receipt host={receipt.get('host')} != expected {expected_host}"
        )
    problems.extend(_receipt_age_problems(receipt, max_age_hours, now))

    recorded_dest = str(receipt.get("destination_db") or "")
    if not recorded_dest:
        problems.append("receipt does not record a destination database")
    elif Path(recorded_dest).expanduser().resolve() != db.expanduser().resolve():
        problems.append(f"receipt destination {recorded_dest} != live db {db.resolve()}")

    if receipt.get("history_imported") is not False:
        problems.append(
            f"receipt history_imported={receipt.get('history_imported')}; a fresh "
            "genesis must declare that no history was imported"
        )
    for key in ("historical_events", "events_table_rows"):
        value = receipt.get(key)
        if not isinstance(value, int) or value != 0:
            problems.append(f"receipt {key}={value}")
    if receipt.get("integrity_check") != "ok":
        problems.append(f"receipt integrity_check={receipt.get('integrity_check')}")
    foreign_keys = receipt.get("foreign_key_violations")
    if not isinstance(foreign_keys, int) or foreign_keys != 0:
        problems.append(f"receipt foreign_key_violations={foreign_keys}")

    initial = receipt.get("initial_epoch") or {}
    if initial.get("history_epoch") != INITIAL_EPOCH:
        problems.append(f"receipt initial history_epoch={initial.get('history_epoch')}")
    if initial.get("next_local_sequence") != INITIAL_SEQUENCE:
        problems.append(
            f"receipt initial next_local_sequence={initial.get('next_local_sequence')}"
        )
    schema = receipt.get("schema") or {}
    if not schema.get("schema_version") or not schema.get("fingerprint"):
        problems.append("receipt does not record the initialized schema identity")
    if schema.get("sidecars"):
        problems.append(f"receipt records sidecars: {schema.get('sidecars')}")

    if not db.is_file():
        problems.append(f"live database missing: {db}")
        return result
    if facts["sidecars"]:
        problems.append(f"live store has SQLite sidecars: {facts['sidecars']}")
    if facts["db_sha256"] != receipt.get("destination_db_sha256"):
        problems.append(
            "live database bytes do not match the generated genesis store "
            f"(live={facts['db_sha256']} "
            f"receipt={receipt.get('destination_db_sha256')}); the store has been "
            "written to or replaced since genesis"
        )
    if facts["schema_version"] != schema.get("schema_version"):
        problems.append(
            f"live schema_version={facts['schema_version']} != "
            f"receipt={schema.get('schema_version')}"
        )
    if facts["schema_fingerprint"] != schema.get("fingerprint"):
        problems.append("live schema fingerprint does not match the receipt")
    if facts["history_epoch"] != INITIAL_EPOCH:
        problems.append(f"live history_epoch={facts['history_epoch']}")
    if facts["next_local_sequence"] != INITIAL_SEQUENCE:
        problems.append(f"live next_local_sequence={facts['next_local_sequence']}")
    if facts["event_count"] != 0:
        problems.append(f"live event_count={facts['event_count']}")
    if facts["integrity_check"] != "ok":
        problems.append(f"live integrity_check={facts['integrity_check']}")
    if facts["foreign_key_violations"] != 0:
        problems.append(f"live foreign_key_violations={facts['foreign_key_violations']}")

    try:
        observed_modules = certified_module_digests(app_root)
    except GenesisProvenanceError as exc:
        problems.append(str(exc))
    else:
        recorded_modules = receipt.get("certified_modules") or {}
        for relative, digest in observed_modules.items():
            if recorded_modules.get(relative) != digest:
                problems.append(
                    "certified source module does not match the receipt provenance: "
                    f"{relative}"
                )

    result["verdict"] = "PASS" if not problems else "REFUSED"
    return result



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M07 R5 fresh canonical genesis")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fresh = sub.add_parser(
        "fresh-genesis",
        description=(
            "Create a new empty canonical store. Requires --authorize, "
            "--confirm-destination and --receipt-out; host identity is read only "
            "from the pinned droplet metadata endpoint."
        ),
    )
    p_fresh.add_argument("--app-root", required=True)
    p_fresh.add_argument("--live-db", required=True)
    p_fresh.add_argument(
        "--receipt-out",
        help="authoritative receipt path (required; genesis refuses without it)",
    )
    p_fresh.add_argument(
        "--confirm-destination",
        help=(
            "the exact --live-db path the operator intends to initialize "
            "(required; genesis refuses without it)"
        ),
    )
    p_fresh.add_argument("--certified-sha", default=CERTIFIED_SHA)
    p_fresh.add_argument("--expected-host", default=DROPLET_ID)
    # Deliberately no --imds-url: the identity endpoint is a constant, so the
    # production path cannot be pointed at a service the operator controls.
    p_fresh.add_argument("--authorize", action="store_true")
    p_fresh.add_argument("--out")

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--receipt", required=True)
    p_verify.add_argument("--db", required=True)
    p_verify.add_argument("--app-root", required=True)
    p_verify.add_argument("--expected-host", default=DROPLET_ID)
    p_verify.add_argument("--certified-sha", default=CERTIFIED_SHA)
    p_verify.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    p_verify.add_argument("--out")

    args = parser.parse_args(argv)
    out = Path(args.out) if getattr(args, "out", None) else None

    if args.command == "fresh-genesis":
        result = fresh_genesis(
            app_root=Path(args.app_root),
            live_db=Path(args.live_db),
            receipt_out=Path(args.receipt_out) if args.receipt_out else None,
            certified_sha=args.certified_sha,
            expected_host=args.expected_host,
            confirm_destination=args.confirm_destination,
            authorize=args.authorize,
        )
    else:
        result = verify_genesis_receipt(
            receipt_path=Path(args.receipt),
            db=Path(args.db),
            app_root=Path(args.app_root),
            expected_host=args.expected_host,
            max_age_hours=args.max_age_hours,
            expected_certified_sha=args.certified_sha,
        )

    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return _VERDICT_EXIT_CODES.get(str(result.get("verdict")), 1)


if __name__ == "__main__":
    raise SystemExit(main())

