"""ATDD: quote SQLite lineage sidecar identifiers without bypassing state isolation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.learning_derived_state import (
    LineageIdentity,
    StateLineageError,
    bind_state_connection,
    write_lineage,
)
from tests.atdd_scope import (
    SCOPE_CHANGE_REQUIRED,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
)

pytestmark = pytest.mark.acceptance
APP_ROOT = Path(__file__).resolve().parents[1]
IDENTITY = LineageIdentity(
    "LINEAGE-B-V2-GENESIS",
    "a26d664972075b2a220a466215cf71e389decb86",
    "gen-bd1613ddd16a449b",
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_lineage(root, IDENTITY)
    return root


def test_populated_quoted_table_cannot_masquerade_as_empty_sidecar(tmp_path: Path) -> None:
    """ATDD-SQLITE-LINEAGE-IDENTIFIER-M01/AC-001: crafted schema name cannot hide unbound legacy rows."""
    root = _root(tmp_path)
    conn = sqlite3.connect(root / "legacy.sqlite3")
    # Without identifier escaping this name makes the count query run against
    # the EMPTY table "state" with WHERE 1=0, hiding the populated real table.
    malicious_name = 'state" WHERE 1=0 --'
    quoted = '"' + malicious_name.replace('"', '""') + '"'
    try:
        conn.execute("CREATE TABLE state (value TEXT)")
        conn.execute(f"CREATE TABLE {quoted} (value TEXT)")
        conn.execute(f"INSERT INTO {quoted} (value) VALUES (?)", ("legacy-row",))
        conn.commit()

        with pytest.raises(StateLineageError, match="STATE_LINEAGE_UNBOUND"):
            bind_state_connection(conn, root, "learning-shadow")

        assert conn.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM metadata WHERE key='lineage_id'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_empty_quoted_unicode_table_binds_to_current_lineage(tmp_path: Path) -> None:
    """ATDD-SQLITE-LINEAGE-IDENTIFIER-M01/AC-002: exact quoted table count preserves fresh-sidecar binding."""
    root = _root(tmp_path)
    conn = sqlite3.connect(root / "fresh.sqlite3")
    schema_name = 'feature" snapshot ✅'
    quoted = '"' + schema_name.replace('"', '""') + '"'
    try:
        conn.execute(f"CREATE TABLE {quoted} (value TEXT)")
        conn.commit()
        bound = bind_state_connection(conn, root, "learning-shadow")
        assert bound == IDENTITY
        assert conn.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0] == 0
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='lineage_id'"
        ).fetchone()
        assert row == (IDENTITY.lineage_id,)
    finally:
        conn.close()


def test_sqlite_lineage_security_scope_rejects_mixed_authority_edits() -> None:
    """ATDD-SQLITE-LINEAGE-IDENTIFIER-M01/AC-003: maintenance scope is narrow and fail closed."""
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(APP_ROOT / "tests")
    increment = "ATDD-SCANNER-RELIABILITY-M01"
    path = "OHM-Trade-Agent-v1/app/services/learning_derived_state.py"
    allowed = check_contracts(
        contracts, acceptance, (path,), active_increment=increment
    )
    assert allowed.passed, allowed.reasons
    blocked = check_contracts(
        contracts,
        acceptance,
        (path, "OHM-Trade-Agent-v1/app/opip/canonical/writer.py"),
        active_increment=increment,
    )
    assert blocked.status == SCOPE_CHANGE_REQUIRED
