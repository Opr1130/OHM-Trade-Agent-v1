"""PR-A: canonical terminal paper-outcome contract and writer admission.

Two layers, deliberately separate:

* Contract tests exercise identity, quote currency, economics and lineage with
  no writer involved.
* Canonical tests seed a real isolated store through the real ``CanonicalWriter``
  and assert admission, priority, stream routing, idempotency and the economic
  immutability guarantee.

The immutability matrix is the load-bearing part. Economic truth must take part
in same-key conflict detection, so a resubmission with changed economics is a
conflict rather than a duplicate. That property comes from the writer routing
this event type through the unstripped ``_idempotency_payload_json`` fall-through;
these tests exist to stop a future change from routing it into a volatile-strip
branch.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.writer import ACCEPTED_EVENT_TYPES, CanonicalWriter
from app.opip.contracts.paper_outcome import (
    CANCELLED,
    CLOSED,
    ENGINE_FREQTRADE_DRY_RUN,
    ENGINE_OHM_PAPER_SIM,
    LINEAGE_COMPLETE,
    LINEAGE_INCOMPLETE,
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_STREAM,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    UNRESOLVED,
    build_terminal_outcome_payload,
    resolve_quote_currency,
    terminal_outcome_id,
    terminal_outcome_idempotency_key,
    validate_terminal_outcome_payload,
)

ENTER = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20


def _payload(**overrides) -> dict:
    values = {
        "engine": ENGINE_OHM_PAPER_SIM,
        "paper_trade_id": PAPER_ID,
        "episode_id": "EP:test-1",
        "cohort_id": "COH:test-1",
        "strategy_version": "wave9-v1",
        "exchange": "KRAKEN",
        "native_symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "quote_currency": "USD",
        "terminal_status": CLOSED,
        "exit_reason": "STOP",
        "exit_price": 98.0,
        "entry_timestamp": ENTER,
        "exit_timestamp": EXIT,
        "capital_committed": 1000.0,
        "gross_pnl": -20.0,
        "fees_paid": 4.0,
        "net_pnl": -24.0,
        "net_pnl_pct": -2.4,
        "final_revision": 7,
        "terminal_event_id": "PTE:" + "b" * 24,
        "intended_entry_low": 100.0,
        "intended_entry_high": 101.0,
        "intended_entry_limit": 100.5,
        "simulated_entry_price": 100.6,
        "quantity_initial": 9.94,
        "executed_notional": 1000.0,
        "candidate_id": "CAND:test-1",
        "decision_context_id": "DI-CONTEXT:" + "c" * 32,
    }
    values.update(overrides)
    return build_terminal_outcome_payload(**values)


@pytest.fixture
def canonical_store(tmp_path, monkeypatch):
    """An isolated canonical store, created through the real writer."""
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _submit(writer: CanonicalWriter, payload: dict, *, priority: str = PAPER_OUTCOME_PRIORITY,
            key: str | None = None, ops_handoff=None):
    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority=priority,  # type: ignore[arg-type]
        idempotency_key=key or terminal_outcome_idempotency_key(payload["outcome_id"]),
        event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        payload=payload,
        ops_handoff=ops_handoff,
    )
    return writer.submit(intent)


def _events(db: Path) -> list:
    import sqlite3

    connection = sqlite3.connect(str(db))
    try:
        return list(
            connection.execute(
                "SELECT event_type, payload_json FROM events ORDER BY local_sequence"
            )
        )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Contract: identity
# ---------------------------------------------------------------------------


def test_outcome_identity_is_deterministic():
    first = terminal_outcome_id(
        engine=ENGINE_OHM_PAPER_SIM,
        paper_trade_id=PAPER_ID,
        terminal_status=CLOSED,
        exit_reason="STOP",
    )
    second = terminal_outcome_id(
        engine=ENGINE_OHM_PAPER_SIM,
        paper_trade_id=PAPER_ID,
        terminal_status=CLOSED,
        exit_reason="STOP",
    )
    assert first == second
    assert first.startswith("PAPER-OUTCOME:")


def test_outcome_identity_separates_execution_engines():
    """Two engines simulating one opportunity must never alias onto one outcome."""
    shadow = terminal_outcome_id(
        engine=ENGINE_OHM_PAPER_SIM,
        paper_trade_id=PAPER_ID,
        terminal_status=CLOSED,
        exit_reason="STOP",
    )
    freqtrade = terminal_outcome_id(
        engine=ENGINE_FREQTRADE_DRY_RUN,
        paper_trade_id=PAPER_ID,
        terminal_status=CLOSED,
        exit_reason="STOP",
    )
    assert shadow != freqtrade


def test_outcome_identity_is_stable_across_revision_change():
    """A retry carrying a later revision is the same logical outcome."""
    payload = _payload()
    later = _payload(final_revision=99)
    assert payload["outcome_id"] == later["outcome_id"]


def test_changed_exit_reason_is_a_different_identity_not_a_correction():
    stopped = _payload(exit_reason="STOP")
    timed_out = _payload(exit_reason="TIME_EXIT")
    assert stopped["outcome_id"] != timed_out["outcome_id"]


# ---------------------------------------------------------------------------
# Contract: quote currency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("BTCUSD", "USD"), ("BTCUSDT", "USDT"), ("SOLUSDT", "USDT"), ("ETHUSD", "USD")],
)
def test_quote_currency_is_resolved_longest_suffix_first(symbol, expected):
    assert resolve_quote_currency(symbol) == expected


def test_unknown_quote_currency_fails_closed():
    assert resolve_quote_currency("BTCGBP") is None
    assert resolve_quote_currency("BTCUSD", quote_currency="GBP") is None


def test_payload_rejects_unknown_quote_currency():
    with pytest.raises(ValueError):
        _payload(native_symbol="BTCGBP", quote_currency="GBP")


def test_payload_rejects_unknown_direction():
    """Direction-scoped learning buckets need a real side."""
    with pytest.raises(ValueError, match="unsupported direction"):
        _payload(direction="SIDEWAYS")


def test_payload_requires_direction():
    payload = _payload()
    stripped = {key: value for key, value in payload.items() if key != "direction"}
    with pytest.raises(ValueError, match="missing keys: direction"):
        validate_terminal_outcome_payload(stripped)


def test_usdt_pair_keeps_usdt_quote_currency():
    payload = _payload(native_symbol="BTCUSDT", quote_currency="USDT")
    assert payload["quote_currency"] == "USDT"


# ---------------------------------------------------------------------------
# Contract: economics
# ---------------------------------------------------------------------------


def test_closed_economics_must_be_internally_consistent():
    with pytest.raises(ValueError, match="net_pnl must equal"):
        _payload(gross_pnl=-20.0, fees_paid=4.0, net_pnl=-999.0)


def test_unresolved_outcome_must_not_assert_economics():
    """Unknown is not break-even: zeros would be a false economic claim."""
    with pytest.raises(ValueError, match="must not assert economics"):
        _payload(
            terminal_status=UNRESOLVED,
            exit_reason="UNRESOLVED",
            gross_pnl=0.0,
            fees_paid=0.0,
            net_pnl=0.0,
            net_pnl_pct=0.0,
        )


def test_unresolved_outcome_accepts_absent_economics():
    payload = _payload(
        terminal_status=UNRESOLVED,
        exit_reason="UNRESOLVED",
        gross_pnl=None,
        fees_paid=None,
        net_pnl=None,
        net_pnl_pct=None,
        capital_committed=None,
        exit_price=None,
    )
    assert payload["terminal_status"] == UNRESOLVED
    assert payload["net_pnl"] is None


def test_cancelled_outcome_must_be_zero_not_negative():
    with pytest.raises(ValueError, match="zero realised economics"):
        _payload(
            terminal_status=CANCELLED,
            exit_reason="PENDING_TTL_EXPIRED",
            gross_pnl=-5.0,
            fees_paid=0.0,
            net_pnl=-5.0,
            net_pnl_pct=-0.5,
        )


def test_cancelled_outcome_keeps_committed_capital():
    """A cancelled setup realised nothing, but capital was still committed."""
    payload = _payload(
        terminal_status=CANCELLED,
        exit_reason="PENDING_TTL_EXPIRED",
        gross_pnl=0.0,
        fees_paid=0.0,
        net_pnl=0.0,
        net_pnl_pct=0.0,
    )
    assert payload["net_pnl"] == 0.0
    assert payload["capital_committed"] == 1000.0


def test_non_finite_economics_are_rejected():
    with pytest.raises(ValueError):
        _payload(gross_pnl=float("nan"), net_pnl=float("nan"))


# ---------------------------------------------------------------------------
# Contract: lineage and chronology
# ---------------------------------------------------------------------------


def test_complete_lineage_is_reported_when_all_slots_present():
    payload = _payload()
    assert payload["lineage_completeness"] == LINEAGE_COMPLETE
    assert payload["lineage_missing"] == []


def test_missing_decision_context_is_named_not_fabricated():
    payload = _payload(decision_context_id=None, candidate_id=None)
    assert payload["lineage_completeness"] == LINEAGE_INCOMPLETE
    assert payload["lineage_missing"] == ["candidate_id", "decision_context_id"]
    assert payload["decision_context_id"] is None


def test_incomplete_lineage_must_name_what_is_missing():
    payload = _payload(decision_context_id=None)
    with pytest.raises(ValueError, match="must name the missing fields"):
        validate_terminal_outcome_payload({**payload, "lineage_completeness": LINEAGE_INCOMPLETE, "lineage_missing": []})


def test_exit_cannot_precede_entry():
    with pytest.raises(ValueError, match="exit_timestamp cannot precede"):
        _payload(entry_timestamp=EXIT, exit_timestamp=ENTER)


def test_strategy_version_is_never_absent():
    """An uncaptured strategy version is UNKNOWN, never the deployed version."""
    payload = _payload(strategy_version=None)
    assert payload["strategy_version"] == "UNKNOWN"


def test_revision_must_be_a_positive_integer():
    with pytest.raises(ValueError):
        _payload(final_revision=0)


# ---------------------------------------------------------------------------
# Canonical admission
# ---------------------------------------------------------------------------


def test_paper_outcome_event_type_is_admitted():
    assert PAPER_OUTCOME_TERMINAL_RECORDED in ACCEPTED_EVENT_TYPES


def test_valid_outcome_is_committed(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        ack = _submit(writer, _payload())
        assert ack.status == "OK", ack.detail
        assert ack.event_id
    finally:
        writer.close()


def test_outcome_lands_in_its_own_stream(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        assert _submit(writer, _payload()).status == "OK"
        rows = _events(canonical_store)
        assert len(rows) == 1
        assert rows[0][0] == PAPER_OUTCOME_TERMINAL_RECORDED
        # Stream isolation: paper outcomes must not consume the early-watch
        # watermark or rewind the alert governor's progress.
        import sqlite3

        connection = sqlite3.connect(str(canonical_store))
        try:
            streams = {
                row[0] for row in connection.execute("SELECT stream FROM watermarks")
            }
        finally:
            connection.close()
        assert PAPER_OUTCOME_STREAM in streams
    finally:
        writer.close()


def test_outcome_requires_low_priority(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        ack = _submit(writer, _payload(), priority="NORMAL")
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
    finally:
        writer.close()


def test_outcome_must_not_carry_an_ops_handoff(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        ack = _submit(
            writer,
            _payload(),
            ops_handoff={"operation": "RECORD", "state_family": "early_watch"},
        )
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
    finally:
        writer.close()


def test_malformed_outcome_is_rejected_not_committed(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        bad = _payload()
        bad["net_pnl"] = -999.0
        ack = _submit(writer, bad)
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
        assert _events(canonical_store) == []
    finally:
        writer.close()


def test_outcome_does_not_require_an_ops_handoff_row(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        assert _submit(writer, _payload()).status == "OK"
        assert writer.list_pending_handoffs() == []
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# Idempotency and economic immutability
# ---------------------------------------------------------------------------


def test_identical_resubmission_is_a_duplicate(canonical_store):
    writer = CanonicalWriter(canonical_store)
    try:
        first = _submit(writer, _payload())
        again = _submit(writer, _payload())
        assert first.status == "OK"
        assert again.status == "DUPLICATE_OK"
        assert again.event_id == first.event_id
        assert len(_events(canonical_store)) == 1
    finally:
        writer.close()


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"gross_pnl": -10.0, "net_pnl": -14.0}, id="gross_and_net_pnl"),
        pytest.param({"fees_paid": 9.0, "net_pnl": -29.0}, id="fees"),
        pytest.param({"quantity_initial": 4.2}, id="quantity"),
        pytest.param({"simulated_entry_price": 111.1}, id="entry_price"),
        pytest.param({"exit_price": 77.7}, id="exit_price"),
        pytest.param({"strategy_version": "wave9-v2"}, id="strategy_version"),
        pytest.param({"native_symbol": "BTCUSDT", "quote_currency": "USDT"}, id="quote_currency"),
        pytest.param({"capital_committed": 500.0}, id="capital_committed"),
        pytest.param({"terminal_event_id": "PTE:" + "d" * 24}, id="terminal_event_id"),
    ],
)
def test_changed_economics_on_the_same_identity_is_a_conflict(canonical_store, mutation):
    """Same identity + different economics must never be accepted as a duplicate."""
    writer = CanonicalWriter(canonical_store)
    try:
        assert _submit(writer, _payload()).status == "OK"
        ack = _submit(writer, _payload(**mutation))
        assert ack.status == "REJECTED"
        assert ack.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        assert len(_events(canonical_store)) == 1
    finally:
        writer.close()


def test_revision_change_is_a_conflict_not_a_silent_duplicate(canonical_store):
    """A moved lifecycle cursor means the underlying evidence changed.

    ``final_revision`` is recorded provenance and deliberately takes part in
    conflict detection: this event type is never routed through volatile
    stripping. A retry must resubmit the identical intent - which is exactly
    what the producer's spool preserves - so a differing revision fails closed
    instead of being silently accepted as a duplicate.
    """
    writer = CanonicalWriter(canonical_store)
    try:
        assert _submit(writer, _payload(final_revision=7)).status == "OK"
        ack = _submit(writer, _payload(final_revision=8))
        assert ack.status == "REJECTED"
        assert ack.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        assert len(_events(canonical_store)) == 1
    finally:
        writer.close()


def test_exact_intent_retry_is_a_duplicate(canonical_store):
    """The acknowledgement-loss path: resubmitting the same intent is safe."""
    writer = CanonicalWriter(canonical_store)
    try:
        intent_payload = _payload(final_revision=7)
        first = _submit(writer, intent_payload)
        retry = _submit(writer, dict(intent_payload))
        assert first.status == "OK"
        assert retry.status == "DUPLICATE_OK"
        assert retry.event_id == first.event_id
        assert len(_events(canonical_store)) == 1
    finally:
        writer.close()


def test_correction_is_append_only_and_preserves_history(canonical_store):
    """A correction adds evidence; it never rewrites the committed record."""
    writer = CanonicalWriter(canonical_store)
    try:
        original = _payload()
        assert _submit(writer, original).status == "OK"

        # A correction is a NEW identity (sequence 1) that names what it
        # supersedes, so retries of the original stay idempotent.
        corrected = _payload(
            gross_pnl=-10.0,
            net_pnl=-14.0,
            correction_seq=1,
            supersedes_id=original["outcome_id"],
            supersession_reason="fee model corrected after review",
        )
        assert corrected["outcome_id"] != original["outcome_id"]
        ack = _submit(writer, corrected)
        assert ack.status == "OK", ack.detail

        rows = _events(canonical_store)
        assert len(rows) == 2
        # The superseded record is still present: history is never erased.
        assert original["outcome_id"] in rows[0][1]
        assert original["outcome_id"] in rows[1][1]
    finally:
        writer.close()


def test_correction_resolves_to_the_effective_record():
    """Linkage reads the effective outcome, not the superseded history."""
    from app.opip.learning.linkage import resolve_effective_outcomes

    original = _payload()
    corrected = _payload(
        gross_pnl=-10.0,
        net_pnl=-14.0,
        correction_seq=1,
        supersedes_id=original["outcome_id"],
    )
    effective = resolve_effective_outcomes([original, corrected])
    assert [row["outcome_id"] for row in effective] == [corrected["outcome_id"]]


def test_sequence_zero_cannot_supersede():
    with pytest.raises(ValueError, match="cannot supersede"):
        _payload(supersedes_id="PAPER-OUTCOME:" + "e" * 32)


def test_a_correction_must_name_what_it_supersedes():
    with pytest.raises(ValueError, match="must name the outcome it supersedes"):
        _payload(correction_seq=1)

    payload = _payload()
    stripped = {k: v for k, v in payload.items() if k != "correction_seq"}
    with pytest.raises(ValueError, match="missing keys: correction_seq"):
        validate_terminal_outcome_payload(stripped)


# ---------------------------------------------------------------------------
# Architectural boundary
# ---------------------------------------------------------------------------


def test_paper_modules_never_instantiate_the_canonical_writer():
    """Direct store ownership is the daemon's job, not paper trading's.

    Detected via AST rather than a substring check, because the permitted
    ``CanonicalWriterClient`` contains the forbidden name as a substring.
    """
    from app.services import (
        paper_outcome_outbox,
        paper_trade_control,
        paper_trade_engine,
        paper_trade_models,
        paper_trade_monitor,
        paper_trade_registry,
        paper_trade_simulation,
    )

    offenders: list[str] = []
    for module in (
        paper_outcome_outbox,
        paper_trade_control,
        paper_trade_engine,
        paper_trade_models,
        paper_trade_monitor,
        paper_trade_registry,
        paper_trade_simulation,
    ):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "CanonicalWriter":
                    offenders.append(f"{module.__name__}:{node.lineno}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "CanonicalWriter":
                        offenders.append(f"{module.__name__}: imports CanonicalWriter")
                    # A module-import of the writer module itself is equally a
                    # boundary violation.
                    if alias.name == "app.opip.canonical.writer":
                        offenders.append(f"{module.__name__}: imports canonical.writer")
    assert offenders == [], f"paper modules must not own the canonical store: {offenders}"


def test_paper_outcome_reader_never_owns_the_store():
    """The canonical reader is read-only and must not construct a writer."""
    from app.opip.learning import paper_outcome_reader

    tree = ast.parse(inspect.getsource(paper_outcome_reader))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "CanonicalWriter"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                assert alias.name != "CanonicalWriter"
