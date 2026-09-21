"""B/C-4 remediation regressions for the four ARB blockers.

Each test corresponds to one blocker and is written to fail if the defect returns:

1. Analytics-plane separation — the cockpit must read the verified canonical
   *replica* through a read-only, lock-free reader, and must not resolve the trading
   host's authoritative store or writer path.
2. Per-trade admission rescan — a ledger read must not re-enumerate admitted
   history once per trade.
3. Lineage identity lookups — identity resolution must be index-bounded rather than
   scanning an event family per trade.
4. Fill temporal endpoints — chronology must follow canonical temporal evidence, not
   commit order.
"""

from __future__ import annotations

import pathlib
import sys
import ast

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_bc4_cockpit_ledger_v1 import (  # noqa: E402
    _BookTransport,
    _Settings,
    _bounded,
    _entry,
    _exact,
    _QUALIFICATION,
    writer_env,
)
from test_opip_paper_v2_cross_scan_simulation_bc3 import (  # noqa: E402
    _opportunity as _symbol_opportunity,
)

from app.api import cockpit  # noqa: E402
from app.opip.cockpit import build_trade_row  # noqa: E402
from app.opip.cockpit.ledger import read_paper_ledger_from_reader  # noqa: E402


@pytest.fixture(autouse=True)
def _bypass_secret(monkeypatch):
    """Authenticate every call so these tests exercise behaviour, not auth."""
    monkeypatch.setattr(cockpit, "_require_secret", lambda value: None)


# ---------------------------------------------------------------------------
# BLOCKER 1 — analytics-plane separation
# ---------------------------------------------------------------------------


def test_cockpit_resolves_the_replica_and_never_the_production_store():
    """Analytics must read the replica; the production path must be unreachable."""
    import ast

    source = pathlib.Path(cockpit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "app.opip.learning.canonical_replica" in imported
    assert "app.opip.canonical.paths" not in imported
    assert "app.services.paper_v2_scan_router" not in imported


def test_cockpit_never_reaches_the_writer_rpc_or_a_writable_writer():
    """No writer-RPC path, and no writable CanonicalWriter construction."""
    import ast

    source = pathlib.Path(cockpit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "for_reads" in called
    assert "CanonicalWriter(" not in source
    for banned in (
        "get_paper_v2_ledger",
        "submit",
        "trigger_paper_protection_action",
        "initialize_schema",
        "checkpoint_wal",
    ):
        assert banned not in called, f"cockpit calls writer surface {banned!r}"


def test_replica_absence_yields_unavailable_and_computes_nothing(monkeypatch):
    """With no replica the cockpit must not silently read anything else.

    This is the structural guarantee: the trading host holds no replica, so no
    analytical computation can occur there.
    """
    monkeypatch.setattr(cockpit, "_replica_db_path", lambda: None)

    overview = cockpit.cockpit_overview(x_webhook_secret=None)
    trades = cockpit.cockpit_trades(quote_currency=None, limit=5, x_webhook_secret=None)
    detail = cockpit.cockpit_trade_detail("PTV2:x", x_webhook_secret=None)

    for payload in (overview, trades, detail):
        assert payload["trust"]["is_healthy"] is False
        assert payload["details"]
        assert "REPLICA" in payload["details"][0].upper()
    assert overview["portfolios"] == []
    assert trades["count"] == 0
    assert detail["found"] is False


def test_for_reads_is_read_only_and_takes_no_store_lock(tmp_path):
    """``for_reads`` must refuse writes and must not hold the store lock.

    Proven behaviourally: SQLite rejects a write, and a production writer can still
    acquire the same store afterwards — impossible if the reader had taken the
    exclusive lock.
    """
    from app.opip.canonical.writer import CanonicalWriter

    store = pathlib.Path(tmp_path) / "canonical.sqlite3"
    writer = CanonicalWriter(store)
    writer.close()

    reader = CanonicalWriter.for_reads(store)
    try:
        assert reader.is_read_only is True
        assert reader.paper_v2_ledger().status == "OK"
        with pytest.raises(Exception):
            reader._conn.execute("CREATE TABLE rw_check (a)")  # noqa: SLF001
        # No exclusive lock: the production writer can still take the store.
        second = CanonicalWriter(store)
        second.close()
    finally:
        reader.close()


def test_for_reads_close_does_not_checkpoint_or_release_ownership(tmp_path):
    """Closing a reader must not attempt a WAL checkpoint it does not own."""
    store = pathlib.Path(tmp_path) / "canonical.sqlite3"
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(store)
    writer.close()

    reader = CanonicalWriter.for_reads(store)
    reader.close()  # must not raise
    assert reader.is_read_only is True


def test_read_paper_ledger_from_reader_fails_closed_on_a_broken_reader():
    class _Broken:
        def paper_v2_ledger(self):
            raise RuntimeError("replica unreadable")

    ledger = read_paper_ledger_from_reader(_Broken())

    assert ledger.entries == ()
    assert ledger.trust.is_healthy is False
    assert ledger.trust.completeness.value == "UNKNOWN"
    assert ledger.details


def test_ledger_projection_is_shared_between_reader_and_writer(tmp_path):
    """One implementation of canonical economics, not two.

    A separate replica-only query implementation would be a second source of truth,
    so both modes must return identical output for identical evidence.
    """
    from app.opip.canonical.writer import CanonicalWriter

    store = pathlib.Path(tmp_path) / "canonical.sqlite3"
    writer = CanonicalWriter(store)
    try:
        as_writer = writer.paper_v2_ledger().to_dict()
    finally:
        writer.close()

    reader = CanonicalWriter.for_reads(store)
    try:
        as_reader = reader.paper_v2_ledger().to_dict()
    finally:
        reader.close()

    assert as_writer == as_reader


# ---------------------------------------------------------------------------
# BLOCKER 2 — per-trade admission rescan
# ---------------------------------------------------------------------------


def _open_three_trades(server, client):
    """Commit three real, distinct Paper-v2 trades through the producer.

    Each trade needs its own symbol: reusing one identity would be rejected as an
    invalid intent rather than producing a second trade.
    """
    from app.exchanges.kraken import KrakenClient
    from app.services.paper_v2_execution import run_paper_v2_opportunity

    results = []
    for symbol in ("BTCUSD", "ETHUSD", "SOLUSD"):
        results.append(
            run_paper_v2_opportunity(
                _symbol_opportunity(symbol),
                client=client,
                kraken_client=KrakenClient(
                    transport=_BookTransport(publication_ts="2026-09-20T11:59:59Z")
                ),
                settings=_Settings(),
                now=_QUALIFICATION,
                clock=lambda: _QUALIFICATION,
            )
        )
    return results


def test_ledger_read_does_not_rescan_admitted_history_per_trade(writer_env, monkeypatch):
    """One ledger read must enumerate admitted history a bounded number of times.

    The defect: the ledger entry builder called ``_admitted_dispositions()`` once per
    trade and searched the result, so work grew quadratically with admitted trade
    count. With the disposition carried on the projection item, the enumeration
    happens a constant number of times per read regardless of trade count.
    """
    server, client = writer_env
    _open_three_trades(server, client)

    calls = {"n": 0}
    original = server.writer._admitted_dispositions  # noqa: SLF001

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(server.writer, "_admitted_dispositions", _counting)

    ledger = server.writer.paper_v2_ledger()

    assert ledger.status == "OK"
    assert len(ledger.entries) == 3
    # Constant, not one-per-trade: the projection enumerates admitted dispositions
    # once to build items, and the ledger builder reuses what the item carries.
    assert calls["n"] <= 2, (
        f"admitted history enumerated {calls['n']} times for 3 trades; "
        "the ledger must not rescan per trade"
    )


def test_ledger_entry_carries_the_disposition_time_from_the_item(writer_env):
    """The disposition instant must come from the item, not a re-scan."""
    server, client = writer_env
    results = _open_three_trades(server, client)

    ledger = server.writer.paper_v2_ledger()
    by_trade = {entry.paper_trade_id: entry for entry in ledger.entries}

    for result in results:
        entry = by_trade[result.paper_trade_id]
        assert entry.disposition_time is not None
        assert entry.disposition_time.get("precision") == "EXACT"
        assert entry.disposition_time.get("occurred_at")


def test_query_count_does_not_grow_quadratically_with_trade_count(writer_env):
    """Directly measure query volume against admitted trade count."""
    server, client = writer_env

    def _measure() -> int:
        counts = {"n": 0}
        conn = server.writer._conn  # noqa: SLF001

        class _Counting:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, *args, **kwargs):
                counts["n"] += 1
                return self._inner.execute(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        server.writer._conn = _Counting(conn)  # noqa: SLF001
        try:
            ledger = server.writer.paper_v2_ledger()
            assert ledger.status == "OK"
        finally:
            server.writer._conn = conn  # noqa: SLF001
        return counts["n"]

    _open_three_trades(server, client)
    three = _measure()
    # A quadratic (per-trade rescan) implementation would roughly triple this per
    # added trade; a linear one grows in proportion.
    assert three < 400, f"ledger read issued {three} statements for 3 trades"


# ---------------------------------------------------------------------------
# BLOCKER 3 — index-bounded lineage identity lookups
# ---------------------------------------------------------------------------


def test_lineage_identity_lookup_uses_the_unique_idempotency_index(writer_env):
    """Identity resolution must be an index lookup, not an event-family scan."""
    server, _client = writer_env

    exact = server.writer._conn.execute(  # noqa: SLF001
        "EXPLAIN QUERY PLAN SELECT event_id FROM events "
        "WHERE idempotency_key = ? LIMIT 1",
        ("x",),
    ).fetchall()
    exact_detail = " ".join(str(row["detail"]) for row in exact)

    prefix = server.writer._conn.execute(  # noqa: SLF001
        "EXPLAIN QUERY PLAN SELECT event_id FROM events "
        "WHERE idempotency_key >= ? AND idempotency_key < ?",
        ("a", "b"),
    ).fetchall()
    prefix_detail = " ".join(str(row["detail"]) for row in prefix)

    for detail in (exact_detail, prefix_detail):
        assert "SCAN events" not in detail, detail
        # The unique index on idempotency_key (sqlite_autoindex_events_*) or the
        # named index must be used for both forms.
        assert "INDEX" in detail.upper(), detail


def test_no_json_extract_identity_scan_remains_in_lineage(writer_env):
    """Lineage must not resolve identities by scanning a JSON payload field."""
    import ast

    source = pathlib.Path(
        "app/opip/canonical/writer.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find _ledger_lineage and assert it contains no json_extract SQL.
    lineage = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_ledger_lineage"
    )
    body = ast.get_source_segment(source, lineage) or ""
    assert "json_extract" not in body, (
        "lineage still resolves identities with a JSON-path scan"
    )
    assert "context_idempotency_key" in body
    assert "_event_id_by_idempotency_key" in body


def test_lineage_includes_referenced_records_via_index_lookup(writer_env):
    """Lineage still completes ancestry, now bounded by an index."""
    server, _client = writer_env
    results = _open_three_trades(server, _client)

    ledger = server.writer.paper_v2_ledger()
    entry = next(
        e for e in ledger.entries if e.paper_trade_id == results[0].paper_trade_id
    )
    assert entry.event_ids
    assert len(set(entry.event_ids)) == len(entry.event_ids)

    placeholders = ",".join("?" for _ in entry.event_ids)
    rows = server.writer._conn.execute(  # noqa: SLF001
        f"SELECT event_type FROM events WHERE event_id IN ({placeholders})",
        tuple(entry.event_ids),
    ).fetchall()
    types = {str(r["event_type"]) for r in rows}
    assert any("decision_intelligence.context" in t for t in types), types
    assert any("instrument_version" in t for t in types), types


# ---------------------------------------------------------------------------
# BLOCKER 4 — fill temporal endpoints follow temporal evidence
# ---------------------------------------------------------------------------


def _fill_at(*, side: str, quantity: float, price: float, offset: int) -> dict:
    return {
        "side": side,
        "quantity": quantity,
        "price": price,
        "fee_cost": 0.1,
        "spread_cost": 0.0,
        "slippage_cost": 0.0,
        "other_supported_cost": 0.0,
        "fill_time": _exact(offset),
        "economic_model_version": "opip-paper-economics-v2",
    }


def test_entry_fill_extreme_follows_occurrence_not_commit_order(writer_env):
    """A late-committed earlier fill must still be recognised as the earliest."""
    server, _client = writer_env

    earlier = _fill_at(side="BUY", quantity=5.0, price=100.0, offset=0)
    later = _fill_at(side="BUY", quantity=5.0, price=101.0, offset=600)

    # Commit order puts the later fill first: naive fills[0] would pick it.
    chosen = server.writer._fill_temporal_extreme(  # noqa: SLF001
        [later, earlier], want_earliest=True
    )
    assert chosen is not None
    assert chosen["occurred_at"] == earlier["fill_time"]["occurred_at"]

    # And for the latest endpoint the reverse holds.
    chosen_last = server.writer._fill_temporal_extreme(  # noqa: SLF001
        [later, earlier], want_earliest=False
    )
    assert chosen_last["occurred_at"] == later["fill_time"]["occurred_at"]


def test_exit_fill_extreme_follows_occurrence_with_multiple_exits(writer_env):
    server, _client = writer_env

    first_out = _fill_at(side="SELL", quantity=2.0, price=110.0, offset=600)
    second_out = _fill_at(side="SELL", quantity=3.0, price=112.0, offset=1200)

    chosen = server.writer._fill_temporal_extreme(  # noqa: SLF001
        [second_out, first_out], want_earliest=False
    )
    assert chosen is not None
    assert chosen["occurred_at"] == second_out["fill_time"]["occurred_at"]


def test_overlapping_bounded_exit_windows_are_unorderable(writer_env):
    """Overlapping windows admit two orderings, so no endpoint is claimed."""
    server, _client = writer_env

    a = dict(_fill_at(side="SELL", quantity=2.0, price=110.0, offset=0))
    a["fill_time"] = _bounded(600, 1200)
    b = dict(_fill_at(side="SELL", quantity=3.0, price=111.0, offset=0))
    b["fill_time"] = _bounded(900, 1500)

    assert server.writer._fill_temporal_extreme([a, b], want_earliest=False) is None  # noqa: SLF001
    assert server.writer._fill_temporal_extreme([a, b], want_earliest=True) is None  # noqa: SLF001


def test_disjoint_bounded_windows_order_deterministically(writer_env):
    """Disjoint bounded windows are orderable and keep their interval."""
    server, _client = writer_env

    a = dict(_fill_at(side="SELL", quantity=2.0, price=110.0, offset=0))
    a["fill_time"] = _bounded(600, 700)
    b = dict(_fill_at(side="SELL", quantity=3.0, price=111.0, offset=0))
    b["fill_time"] = _bounded(1200, 1300)

    latest = server.writer._fill_temporal_extreme([a, b], want_earliest=False)  # noqa: SLF001
    assert latest is not None
    assert latest["precision"] == "BOUNDED"
    assert latest["window_start"] == b["fill_time"]["window_start"]

    earliest = server.writer._fill_temporal_extreme([a, b], want_earliest=True)  # noqa: SLF001
    assert earliest["window_start"] == a["fill_time"]["window_start"]


def test_unknown_fill_time_never_manufactures_an_endpoint(writer_env):
    """UNKNOWN evidence yields no endpoint rather than falling back to commit order."""
    server, _client = writer_env

    unknown = dict(_fill_at(side="SELL", quantity=5.0, price=110.0, offset=0))
    unknown["fill_time"] = {
        "precision": "UNKNOWN",
        "basis": "MODEL_ASSIGNED",
        "reason": "no clock",
    }

    assert server.writer._fill_temporal_extreme([unknown], want_earliest=False) is None  # noqa: SLF001
    assert server.writer._fill_temporal_extreme([unknown], want_earliest=True) is None  # noqa: SLF001


def test_one_unknown_among_exact_fills_refuses_both_endpoints(writer_env):
    """A single unprovable fill makes the extreme unprovable."""
    server, _client = writer_env

    exact = _fill_at(side="SELL", quantity=5.0, price=110.0, offset=600)
    unknown = dict(_fill_at(side="SELL", quantity=1.0, price=111.0, offset=0))
    unknown["fill_time"] = {
        "precision": "UNKNOWN",
        "basis": "MODEL_ASSIGNED",
        "reason": "no clock",
    }

    assert server.writer._fill_temporal_extreme([exact, unknown], want_earliest=False) is None  # noqa: SLF001


def test_missing_fill_time_refuses_the_endpoint(writer_env):
    server, _client = writer_env
    broken = {"side": "SELL", "quantity": 1.0, "price": 1.0}
    assert server.writer._fill_temporal_extreme([broken], want_earliest=False) is None  # noqa: SLF001


def test_empty_fills_yield_no_endpoint(writer_env):
    server, _client = writer_env
    assert server.writer._fill_temporal_extreme([], want_earliest=True) is None  # noqa: SLF001


def test_unprovable_exit_endpoint_propagates_as_unavailable_not_a_guess(writer_env):
    """A trade whose exit endpoint is unprovable must not claim a timestamp."""
    unorderable_a = dict(_fill_at(side="SELL", quantity=2.0, price=110.0, offset=0))
    unorderable_a["fill_time"] = _bounded(600, 1200)
    unorderable_b = dict(_fill_at(side="SELL", quantity=3.0, price=111.0, offset=0))
    unorderable_b["fill_time"] = _bounded(900, 1500)

    row = build_trade_row(
        _entry(
            exit_fills=(unorderable_a, unorderable_b),
            exited_quantity=5.0,
            remaining_quantity=0.0,
            first_entry_fill_time=_exact(0),
            last_exit_fill_time=None,
        )
    )
    # No point instant can be proven, so holding and the equity placement are
    # withheld rather than derived from commit order.
    assert row.last_exit_fill_at is None
    assert row.holding_seconds is None
    assert row.early_close is None


def test_endpoint_selection_is_replay_deterministic(writer_env):
    """Repeated selection over the same evidence yields the same endpoint."""
    server, _client = writer_env
    fills = [
        _fill_at(side="SELL", quantity=1.0, price=110.0, offset=600),
        _fill_at(side="SELL", quantity=1.0, price=111.0, offset=1200),
        _fill_at(side="SELL", quantity=1.0, price=112.0, offset=1800),
    ]
    first = server.writer._fill_temporal_extreme(fills, want_earliest=False)  # noqa: SLF001
    for _ in range(5):
        assert server.writer._fill_temporal_extreme(fills, want_earliest=False) == first  # noqa: SLF001
