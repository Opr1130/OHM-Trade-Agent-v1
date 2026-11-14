"""ATDD-OPIP-POSTGRES-PHASE1-V1: PostgreSQL Phase 1 inventory, schema, store API and fail-closed gate.

Two layers:

* Pure tests (always run): migration discipline, storage-inventory governance, configuration
  defaults, the fail-closed admission gate with injected failures, and the F11-independence import
  boundary.
* Database tests (run when ``OPIP_TEST_POSTGRES_DSN`` names an admin connection to a DISPOSABLE
  server, e.g. ``host=127.0.0.1 port=55432 user=postgres dbname=postgres``): every migration is
  applied to a fresh database and idempotency, ordering, atomicity, concurrency, authority
  rollback, least privilege and preflight are proven against real PostgreSQL. They never touch
  any production database.
"""

from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.data_platform import business_store as bs
from app.opip.data_platform import migrations as mig
from app.opip.data_platform import storage_inventory as inv
from app.opip.data_platform.streams import STREAM_SPECS

pytestmark = pytest.mark.acceptance

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_DIR = APP_ROOT / "app" / "opip" / "data_platform" / "migrations"
NOW = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)

#: Deployed migrations are checksum-bound (``apply_migrations`` refuses a changed file).
PINNED_CHECKSUMS = {
    "001_initial.sql": "4155e19df6b750cbed6f2f9c5dafb115b8186ce83a5a7200e8483ddce7468ff6",
    "002_read_models.sql": "bc4db83880e32c932dc74862ad0dd3f4378344f74da8b570ef2cab66bb751089",
    "003_roles.sql": "19c2a1a4a25b6a977e0f5b92fec2e1b308dcacd443e54f00e6060dc6b03da945",
    "004_opportunity_accountability.sql": "7330a192e7ba695965a09b3d60fbf7ff69be57591f5f16eee297a69e0d62f310",
    "005_dashboard_freshness.sql": "9b67d2e57d9382e1a6bfe299d46fcef9a1e3d2e753dc7d77000cd97aadb4cfe4",
    "006_freshness_threshold_parity.sql": "6b265d5345b4f4649bf28b0016cb842e3d243502c7d5d13db4625a0ab3a73694",
}


# =============================================================================================
# Migration discipline
# =============================================================================================
def test_ac_001_a_migration_007_is_additive_and_earlier_migrations_are_untouched():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-001: 007 only adds; 001-006 checksums are unchanged."""
    for name, expected in PINNED_CHECKSUMS.items():
        assert hashlib.sha256((MIGRATION_DIR / name).read_bytes()).hexdigest() == expected, name
    versions = [item.version for item in mig.discover_migrations()]
    assert versions == list(range(1, 8))
    sql = (MIGRATION_DIR / "007_business_store.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", sql)
    assert not re.search(r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|VIEW)|TRUNCATE|DELETE\s+FROM)\b", code, re.I)
    assert not re.search(r"ALTER\s+TABLE", code, re.I)
    for statement in re.findall(r"CREATE\s+TABLE\s+(?!IF NOT EXISTS)\S+", code, re.I):
        pytest.fail(f"non-idempotent CREATE TABLE: {statement}")
    assert "INSERT INTO ops.cutover_authority (singleton) VALUES (true) ON CONFLICT DO NOTHING" in code
    assert not re.search(r"UPDATE\s+ops\.cutover_authority\s+SET\s+authority\s*=\s*'POSTGRES'", code, re.I)


# =============================================================================================
# Storage inventory governance
# =============================================================================================
def _literal_data_paths() -> set[str]:
    found: set[str] = set()
    pattern = re.compile(r"[\"'](/app/data/[A-Za-z0-9_./-]+)[\"']")
    for path in (APP_ROOT / "app").rglob("*.py"):
        found.update(m.group(1)[len("/app/data/"):] for m in pattern.finditer(path.read_text(encoding="utf-8")))
    return found


def test_ac_002_a_every_storage_path_in_app_is_classified():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-002: a new /app/data path cannot appear unclassified."""
    unclassified = sorted(p for p in _literal_data_paths() | {str(s.relative_path) for s in STREAM_SPECS}
                          if inv.covering_entry(p) is None)
    assert unclassified == []


def test_ac_002_b_inventory_targets_and_streams_match_the_checked_in_schema():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-002: every claimed PostgreSQL target exists in a migration."""
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.sql")))
    relations = set(re.findall(
        r"CREATE\s+(?:OR REPLACE\s+)?(?:MATERIALIZED\s+)?(?:TABLE|VIEW)\s+(?:IF NOT EXISTS\s+)?([a-z_]+\.[a-z_]+)", sql, re.I
    ))
    for entry in inv.INVENTORY:
        if entry.pg_target:
            assert entry.pg_target in relations, (entry.path, entry.pg_target)
    registered = {str(spec.relative_path) for spec in STREAM_SPECS if not spec.retired}
    for entry in inv.INVENTORY:
        if entry.basis == "source" and entry.write == "SHIPPED_FROM_FILE":
            assert entry.path in registered, f"{entry.path} claims shipping but is not a registered stream"
    # Safety-critical state is never assigned a PostgreSQL target or write path.
    for entry in inv.INVENTORY:
        if entry.kind == inv.SAFETY_CRITICAL:
            assert entry.pg_target is None and entry.write == "NONE"
            assert inv.cutover_readiness(entry) == "EXCLUDED_BY_DESIGN"
    # The V1/V2 comparison file is an acknowledged gap, not a silently shipped stream.
    assert str(next(e for e in inv.INVENTORY if "scoring_comparison" in e.path).path) not in registered


def test_ac_002_c_rendered_matrix_is_in_sync_with_the_module():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-002: the published matrix is generated, not hand-written."""
    doc = (APP_ROOT / "docs" / "engineering" / "OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md").read_text(encoding="utf-8")
    assert doc == inv.render_markdown()
    assert [row["domain"] for row in inv.domain_matrix()] == list(inv.DOMAINS)


# =============================================================================================
# Configuration, fail-closed admission gate, failure injection
# =============================================================================================
def test_ac_003_a_disabled_by_default_and_unknown_values_never_enable_postgres():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-003: the default is FILES and typos never enable PostgreSQL."""
    assert bs.BusinessStoreConfig.from_env({}).authority == bs.FILES
    assert bs.BusinessStoreConfig.from_env({"OPIP_BUSINESS_STORE_AUTHORITY": " postgres "}).authority == bs.POSTGRES
    typo = bs.BusinessStoreConfig.from_env({"OPIP_BUSINESS_STORE_AUTHORITY": "postgress"})
    assert typo.authority == bs.FILES and "treated as FILES" in typo.config_error


class _Boom(Exception):
    pass


class _BrokenConnection:
    calls = 0

    def __enter__(self):
        type(self).calls += 1
        raise _Boom("connection refused")

    def __exit__(self, *exc):  # pragma: no cover
        return False


class _FakeStore:
    def __init__(self, authority="POSTGRES", epoch=3, raises=False, store_epoch=3):
        self._answer = (authority, epoch)
        self._raises = raises
        self.authority_epoch = store_epoch
        self.calls = 0

    def read_authority(self):
        self.calls += 1
        if self._raises:
            raise bs.StoreUnavailable("down")
        return self._answer


def _pg_config(epoch=3):
    return bs.BusinessStoreConfig(authority=bs.POSTGRES, database_url="x", expected_epoch=epoch)


def test_ac_003_b_files_authority_needs_no_database_and_cannot_fail():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-003: with FILES authority the gate makes no database call."""
    store = _FakeStore(raises=True)
    decision = bs.new_admissions_allowed(bs.BusinessStoreConfig(), store)
    assert decision == bs.AdmissionDecision(True, "FILES_AUTHORITATIVE") and store.calls == 0
    assert bs.new_admissions_allowed(bs.BusinessStoreConfig(), None).allowed is True


@pytest.mark.parametrize(
    "store, config, reason",
    [
        (None, _pg_config(), "POSTGRES_STORE_NOT_CONFIGURED"),
        (_FakeStore(), _pg_config(epoch=0), "POSTGRES_STORE_NOT_CONFIGURED"),
        (_FakeStore(raises=True), _pg_config(), "POSTGRES_UNAVAILABLE"),
        (_FakeStore(authority="FILES"), _pg_config(), "AUTHORITY_NOT_POSTGRES"),
        (_FakeStore(epoch=4), _pg_config(), "AUTHORITY_EPOCH_MISMATCH"),
        (_FakeStore(store_epoch=2), _pg_config(), "AUTHORITY_EPOCH_MISMATCH"),
    ],
)
def test_ac_003_c_postgres_authority_fails_closed_unless_proven(store, config, reason):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-003: no proof of an authoritative reachable database, no admission."""
    decision = bs.new_admissions_allowed(config, store)
    assert (decision.allowed, decision.reason) == (False, reason)
    assert bs.new_admissions_allowed(_pg_config(), _FakeStore()) == bs.AdmissionDecision(True, "POSTGRES_AUTHORITATIVE")


def test_ac_003_d_driver_failures_map_to_unavailable_and_state_errors_are_typed():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-003: a broken link is StoreUnavailable; SQLSTATEs are typed."""
    _BrokenConnection.calls = 0
    store = bs.BusinessStore(_BrokenConnection, authority_epoch=1)
    with pytest.raises(bs.StoreUnavailable):
        store.read_authority()
    with pytest.raises(bs.StoreUnavailable):
        store.record_ai_call({"observed_at": NOW, "provider": "p", "model": "m", "call_id": "1",
                              "status": "SUCCESS", "total_tokens": 1})
    assert bs.new_admissions_allowed(_pg_config(1), store) == bs.AdmissionDecision(False, "POSTGRES_UNAVAILABLE")

    class _State(Exception):
        def __init__(self, sqlstate):
            super().__init__(sqlstate)
            self.sqlstate = sqlstate

    assert isinstance(bs._classify(_State("P0002")), bs.AuthorityMismatch)
    assert isinstance(bs._classify(_State("P0003")), bs.AuthorityMismatch)
    assert isinstance(bs._classify(_State("P0004")), bs.IdempotencyConflict)
    assert isinstance(bs._classify(_State("P0005")), bs.SequenceConflict)
    assert isinstance(bs._classify(_Boom("x")), bs.StoreUnavailable)

    class DataError(Exception):  # stands in for psycopg.DataError (matched by name up the MRO)
        pass

    class IntegrityError(Exception):
        pass

    class _NumericValueOutOfRange(DataError):  # a driver subclass, not named in any list
        pass

    class _UniqueViolation(IntegrityError):
        pass

    for rejected in ("22003", "22P02", "23505", "23514", "23502"):
        assert isinstance(bs._classify(_State(rejected)), bs.StoreRejected), rejected
    assert isinstance(bs._classify(_NumericValueOutOfRange("x")), bs.StoreRejected)
    assert isinstance(bs._classify(_UniqueViolation("x")), bs.StoreRejected)
    for unavailable in ("08006", "57P01", "53300", "42501", "XX000"):
        assert isinstance(bs._classify(_State(unavailable)), bs.StoreUnavailable), unavailable


def test_ac_003_e_payloads_are_canonical_and_reject_non_finite_values():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-003: deterministic hashing; NaN and non-JSON values are refused."""
    assert bs.canonical_json({"b": 1, "a": [1, 2]}) == bs.canonical_json({"a": [1, 2], "b": 1})
    with pytest.raises(ValueError):
        bs.canonical_json({"x": float("nan")})
    with pytest.raises(TypeError):
        bs.canonical_json({"x": object()})
    store = bs.BusinessStore(lambda: (_ for _ in ()).throw(AssertionError("no connection for bad input")), authority_epoch=1)
    with pytest.raises(ValueError):
        store.record_ai_call({"observed_at": datetime(2026, 1, 1), "provider": "p", "model": "m",
                              "call_id": "1", "status": "SUCCESS", "total_tokens": 1})


# =============================================================================================
# F11 / protective-exit independence of PostgreSQL
# =============================================================================================
def test_ac_004_a_nothing_outside_the_data_platform_imports_the_business_store():
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-004: F11, exits and emergency alerts cannot depend on PostgreSQL."""
    importers: list[str] = []
    for path in (APP_ROOT / "app").rglob("*.py"):
        if "data_platform" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            if any(name.endswith("business_store") or name.endswith("data_platform.business_store") for name in names):
                importers.append(str(path.relative_to(APP_ROOT)))
    assert importers == [], "the PostgreSQL store must not be imported by runtime paths before an approved cutover"
    safety = [p for p in (APP_ROOT / "app").rglob("*.py")
              if re.search(r"protection|emergency|f11|liquidat|exit", p.name, re.I)]
    assert safety, "expected safety-critical modules to exist"
    for path in safety:
        assert "business_store" not in path.read_text(encoding="utf-8"), path


# =============================================================================================
# Real-PostgreSQL tests (disposable server only)
# =============================================================================================
ADMIN_DSN = os.environ.get("OPIP_TEST_POSTGRES_DSN")
needs_pg = pytest.mark.skipif(not ADMIN_DSN, reason="set OPIP_TEST_POSTGRES_DSN to a disposable PostgreSQL server")


def _dsn_for(dbname: str) -> str:
    return re.sub(r"dbname=\S+", "", ADMIN_DSN) + f" dbname={dbname}"


@pytest.fixture()
def pg():
    import psycopg

    dbname = "opip_t_" + uuid.uuid4().hex[:12]
    admin = psycopg.connect(ADMIN_DSN, autocommit=True)
    admin.execute(f'CREATE DATABASE "{dbname}"')
    dsn = _dsn_for(dbname)
    setup = psycopg.connect(dsn)
    mig.apply_migrations(setup)
    setup.close()

    class Handle:
        pass

    handle = Handle()
    handle.dsn = dsn
    handle.connect = lambda: psycopg.connect(dsn, autocommit=True)
    handle.store = lambda epoch=2: bs.BusinessStore(handle.connect, authority_epoch=epoch)

    def sql(statement, params=None, fetch=False):
        with handle.connect() as connection:
            cursor = connection.execute(statement, params)
            return cursor.fetchall() if fetch else None

    handle.sql = sql
    handle.flip = lambda to, epoch, reason="test": sql(
        "SELECT ops.set_cutover_authority(%s, %s, 'pytest', %s)", (to, epoch, reason), fetch=True)[0][0]
    yield handle
    admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    admin.close()


def _delivery(i=0, **over):
    row = {"alert_id": "TGALERT:A1", "alert_family": "QUALIFIED_OPPORTUNITY", "event_type": "NEW_TRADE",
           "status": "SUPPRESSED", "delivered": False, "attempted_at": (NOW + timedelta(minutes=i)).isoformat(),
           "signal_id": "S1", "journey_id": "J1", "trade_id": "T1", "symbol": "SOL", "attempt": 0,
           "retry_count": 0, "suppression_reason": "ACTIONABLE_GATE:POSITION_CAPACITY_INSUFFICIENT",
           "evidence": {"order_size_usd": 750.0}}
    row.update(over)
    return row


@needs_pg
def test_ac_005_a_migrations_apply_once_and_authority_starts_as_files(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-005: 001-007 apply on a fresh database; re-running is a no-op."""
    import psycopg

    with psycopg.connect(pg.dsn) as connection:
        assert mig.apply_migrations(connection) == []
    assert pg.sql("SELECT authority, epoch FROM ops.cutover_authority", fetch=True) == [("FILES", 1)]
    assert pg.sql("SELECT max(version) FROM ops.schema_version", fetch=True) == [(7,)]


@needs_pg
def test_ac_005_b_database_refuses_business_writes_while_files_is_authoritative(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-005: no application cooperation is needed to prevent split-brain."""
    store = pg.store(epoch=1)
    with pytest.raises(bs.AuthorityMismatch):
        store.record_delivery_event(_delivery())
    assert pg.sql("SELECT count(*) FROM ops.business_event", fetch=True) == [(0,)]
    assert pg.sql("SELECT count(*) FROM ops.aggregate_head", fetch=True) == [(0,)]


@needs_pg
def test_ac_005_c_idempotent_ordered_atomic_append(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-005: replay is a no-op, conflicts are typed, sequence is gap-free."""
    epoch = pg.flip("POSTGRES", 1)
    store = pg.store(epoch)
    first = store.record_delivery_event(_delivery(0))
    again = store.record_delivery_event(_delivery(0))
    second = store.record_delivery_event(_delivery(1))
    assert (first.status, first.aggregate_seq) == ("INSERTED", 1)
    assert (again.status, again.aggregate_seq) == ("DUPLICATE", 1)
    assert (second.status, second.aggregate_seq) == ("INSERTED", 2)
    assert pg.sql("SELECT count(*) FROM signal.delivery_event", fetch=True) == [(2,)]
    assert pg.sql("SELECT aggregate_seq FROM ops.business_event ORDER BY aggregate_seq", fetch=True) == [(1,), (2,)]
    with pytest.raises(bs.IdempotencyConflict):
        store.record_delivery_event(_delivery(0, suppression_reason="DIFFERENT"))
    with pytest.raises(bs.SequenceConflict):
        store.record_delivery_event(_delivery(2), expected_prev_seq=1)
    ok = store.record_delivery_event(_delivery(2), expected_prev_seq=2)
    assert ok.aggregate_seq == 3
    row = pg.sql("SELECT evidence, suppression_reason FROM signal.delivery_event ORDER BY attempted_at LIMIT 1", fetch=True)[0]
    assert row[0] == {"order_size_usd": 750.0}


@needs_pg
def test_ac_005_d_a_rejected_projection_rolls_back_the_ledger_event(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-005: ledger event and typed row commit together or not at all."""
    store = pg.store(pg.flip("POSTGRES", 1))
    bad = {"observed_at": NOW, "provider": "p", "model": "m", "call_id": "c1", "status": "FAILURE", "total_tokens": 5}
    with pytest.raises(bs.StoreRejected):
        store.record_ai_call(bad)  # FAILURE without error_type violates the CHECK
    assert pg.sql("SELECT count(*) FROM ops.business_event", fetch=True) == [(0,)]
    assert pg.sql("SELECT last_seq FROM ops.aggregate_head", fetch=True) == []
    good = store.record_ai_call({**bad, "error_type": "TRUNCATED_OUTPUT"})
    assert good.inserted


@needs_pg
def test_ac_005_e_concurrent_writers_keep_the_sequence_gap_free_and_dedupe_replays(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-005: 8 writers x 25 events on one aggregate; replays race safely."""
    store = pg.store(pg.flip("POSTGRES", 1))
    errors: list[BaseException] = []

    def worker(w: int) -> None:
        try:
            for i in range(25):
                store.record_delivery_event(_delivery(w * 100 + i))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert pg.sql("SELECT array_agg(aggregate_seq ORDER BY aggregate_seq) FROM ops.business_event", fetch=True)[0][0] == list(range(1, 201))
    assert pg.sql("SELECT last_seq FROM ops.aggregate_head", fetch=True) == [(200,)]

    results: list[str] = []
    same = _delivery(9999)

    def replay() -> None:
        results.append(store.record_delivery_event(same).status)

    racers = [threading.Thread(target=replay) for _ in range(8)]
    [t.start() for t in racers]
    [t.join() for t in racers]
    assert sorted(results) == ["DUPLICATE"] * 7 + ["INSERTED"]
    assert pg.sql("SELECT count(*) FROM signal.delivery_event", fetch=True) == [(201,)]


@needs_pg
def test_ac_006_a_authority_rollback_stops_stale_writers_and_is_audited(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-006: rollback to FILES and any epoch change refuse old writers."""
    import psycopg

    epoch1 = pg.flip("POSTGRES", 1)
    writer = pg.store(epoch1)
    assert writer.record_delivery_event(_delivery(0)).inserted
    rolled_back = pg.flip("FILES", epoch1, "rollback drill")
    with pytest.raises(bs.AuthorityMismatch):
        writer.record_delivery_event(_delivery(1))
    reenabled = pg.flip("POSTGRES", rolled_back, "re-enable drill")
    with pytest.raises(bs.AuthorityMismatch):
        writer.record_delivery_event(_delivery(1))  # stale epoch is still refused after re-enable
    assert pg.store(reenabled).record_delivery_event(_delivery(1)).inserted
    transitions = pg.sql("SELECT from_authority, to_authority, from_epoch, to_epoch FROM ops.cutover_transition ORDER BY transition_id", fetch=True)
    assert transitions == [("FILES", "POSTGRES", 1, 2), ("POSTGRES", "FILES", 2, 3), ("FILES", "POSTGRES", 3, 4)]
    with pytest.raises(psycopg.Error):
        pg.flip("FILES", 1)  # compare-and-set on a stale epoch
    with pytest.raises(psycopg.Error):
        pg.sql("UPDATE ops.cutover_authority SET epoch = 1")  # epochs never decrease
    with pytest.raises(psycopg.Error):
        pg.sql("DELETE FROM ops.cutover_authority")
    assert bs.new_admissions_allowed(_pg_config(reenabled), pg.store(reenabled)).allowed is True
    assert bs.new_admissions_allowed(_pg_config(reenabled), pg.store(epoch1)).allowed is False


@needs_pg
def test_ac_006_b_application_writer_role_is_least_privilege(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-006: the app role appends but cannot flip authority or edit history."""
    import psycopg

    epoch = pg.flip("POSTGRES", 1)
    with psycopg.connect(pg.dsn, autocommit=True) as connection:
        connection.execute("SET ROLE opip_app_writer")
        row = connection.execute(
            "SELECT status FROM ops.append_business_event('k1','agg','1','E',now(),'{}'::jsonb,%s,%s)",
            (bs.canonical_json({})[1], epoch),
        ).fetchone()
        assert row[0] == "INSERTED"
        for denied in (
            "UPDATE ops.cutover_authority SET reason = 'x'",
            "UPDATE ops.business_event SET event_type = 'X'",
            "DELETE FROM ops.business_event",
            "SELECT ops.set_cutover_authority('FILES', 2, 'a', 'b')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(denied)
        for bypass in (  # the function is the only way into the ledger and its head
            "INSERT INTO ops.business_event (idempotency_key, aggregate_type, aggregate_id, aggregate_seq,"
            " event_type, occurred_at, payload, payload_sha256, authority_epoch)"
            f" VALUES ('direct','agg','1',9,'E',now(),'{{}}'::jsonb,repeat('0',64),{epoch})",
            "UPDATE ops.aggregate_head SET last_seq = 99",
            "INSERT INTO ops.aggregate_head (aggregate_type, aggregate_id, last_seq) VALUES ('x','y',0)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(bypass)


@needs_pg
def test_ac_006_c_typed_projection_inserts_obey_authority_and_epoch(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-006: a restricted role cannot write a typed row after rollback."""
    import psycopg

    epoch = pg.flip("POSTGRES", 1)
    digest = bs.canonical_json({})[1]
    with psycopg.connect(pg.dsn, autocommit=True) as connection:
        connection.execute("SET ROLE opip_app_writer")
        assert connection.execute(
            "SELECT status FROM ops.append_business_event('ev1','ai','1','AI_CALL',now(),'{}'::jsonb,%s,%s)",
            (digest, epoch),
        ).fetchone()[0] == "INSERTED"
        connection.execute("RESET ROLE")
    rolled_back = pg.flip("FILES", epoch, "rollback drill")
    insert_ai_call = (
        "INSERT INTO signal.ai_call (idempotency_key, observed_at, provider, model, status, total_tokens)"
        " VALUES ('ev1', now(), 'p', 'm', 'SUCCESS', 1)"
    )
    with psycopg.connect(pg.dsn, autocommit=True) as connection:
        connection.execute("SET ROLE opip_app_writer")
        with pytest.raises(psycopg.Error) as refused:
            connection.execute(insert_ai_call)
        assert refused.value.sqlstate == "P0002"
    reenabled = pg.flip("POSTGRES", rolled_back, "re-enable drill")
    assert reenabled != epoch
    with psycopg.connect(pg.dsn, autocommit=True) as connection:
        connection.execute("SET ROLE opip_app_writer")
        with pytest.raises(psycopg.Error) as stale:
            connection.execute(insert_ai_call)  # ledger event belongs to the old epoch
        assert stale.value.sqlstate == "P0003"
    assert pg.sql("SELECT count(*) FROM signal.ai_call", fetch=True) == [(0,)]


@needs_pg
def test_ac_007_a_outcomes_never_turn_missing_evidence_into_a_verdict(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-007: INSUFFICIENT_EVIDENCE carries no verdict or return."""
    store = pg.store(pg.flip("POSTGRES", 1))
    base = {"signal_id": "S1", "horizon_seconds": 3600, "evaluated_at": NOW, "evidence": {"reason": "no price path"}}
    ok = store.record_signal_outcome({**base, "outcome_class": "INSUFFICIENT_EVIDENCE", "verdict": "INSUFFICIENT_EVIDENCE"})
    assert ok.inserted
    with pytest.raises(bs.StoreRejected):
        store.record_signal_outcome({**base, "horizon_seconds": 14400, "outcome_class": "INSUFFICIENT_EVIDENCE",
                                    "verdict": "INSUFFICIENT_EVIDENCE", "gross_return_pct": 1.0})
    with pytest.raises(bs.StoreRejected):
        store.record_signal_outcome({**base, "horizon_seconds": 14400, "outcome_class": "HYPOTHETICAL",
                                    "verdict": "INSUFFICIENT_EVIDENCE"})
    with pytest.raises(bs.StoreRejected):
        store.record_signal_outcome({**base, "horizon_seconds": 14400, "outcome_class": "INSUFFICIENT_EVIDENCE",
                                    "verdict": "TARGET_FIRST"})
    assert store.record_signal_outcome({**base, "horizon_seconds": 14400, "outcome_class": "HYPOTHETICAL",
                                        "verdict": "TARGET_FIRST", "gross_return_pct": 2.0, "net_return_pct": 1.0,
                                        "assumed_fee_pct": 0.52, "assumed_slippage_pct": 0.5}).inserted
    assert pg.sql("SELECT count(*) FROM learning.signal_outcome", fetch=True) == [(2,)]


@needs_pg
def test_ac_008_a_database_outage_fails_closed_quickly_and_writes_nothing(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-008: an unreachable database means no admission and no partial write."""
    import psycopg

    epoch = pg.flip("POSTGRES", 1)
    dead_dsn = re.sub(r"port=\d+", "port=1", pg.dsn)
    dead = bs.BusinessStore(lambda: psycopg.connect(dead_dsn, connect_timeout=1, autocommit=True), authority_epoch=epoch)
    with pytest.raises(bs.StoreUnavailable):
        dead.record_delivery_event(_delivery())
    assert bs.new_admissions_allowed(_pg_config(epoch), dead) == bs.AdmissionDecision(False, "POSTGRES_UNAVAILABLE")
    assert pg.sql("SELECT count(*) FROM ops.business_event", fetch=True) == [(0,)]


@needs_pg
def test_ac_009_a_preflight_reports_readiness_and_detects_checksum_drift(pg):
    """ATDD-OPIP-POSTGRES-PHASE1-V1/AC-009: preflight is read-only, honest about host-only checks and detects drift."""
    import psycopg

    with psycopg.connect(pg.dsn) as connection:
        report = {item["check"]: item for item in bs.preflight(connection)}
    assert {name: report[name]["status"] for name in ("migrations", "authority_row", "app_writer_least_privilege", "connection_headroom")} == {
        "migrations": "PASS", "authority_row": "PASS", "app_writer_least_privilege": "PASS", "connection_headroom": "PASS"}
    assert report["host_disk_and_memory"]["status"] == "UNVERIFIED"
    assert report["backup_restore_drill"]["status"] == "UNVERIFIED"
    pg.sql("UPDATE ops.schema_version SET sha256 = repeat('0', 64) WHERE version = 7")
    with psycopg.connect(pg.dsn) as connection:
        drifted = {item["check"]: item for item in bs.preflight(connection)}
    assert drifted["migrations"]["status"] == "FAIL" and "7" in drifted["migrations"]["detail"]
