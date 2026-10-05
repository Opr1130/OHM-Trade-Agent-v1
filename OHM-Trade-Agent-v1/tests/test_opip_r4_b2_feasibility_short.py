"""R4-B2 Slice 3A: SHORT / BTNL feasibility-evidence integration (AC-022).

Proves the SHORT route is genuinely implemented: margin discovery uses Kraken's
Bitnomial execution venue, the BTNL margin book (never the spot book) supplies the
SHORT execution evidence, the BTNL ``:BTNL`` provenance F5 requires is carried, and a
SHORT request can never be satisfied by LONG/spot evidence. Deterministic fixtures.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import app.jobs.capture_feasibility_evidence_shadow as producer  # noqa: E402
import app.opip.feasibility as feasibility  # noqa: E402
from app.exchanges.kraken import (  # noqa: E402
    BookLevel,
    Candle,
    PreTradeBook,
)
from app.opip.contracts.feasibility import FeasibilityCheckStatus  # noqa: E402
from app.scanner.execution_validation import (  # noqa: E402
    INVALID,
    UNAVAILABLE,
    VALID,
)

pytestmark = pytest.mark.acceptance

T = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class _Snapshot:
    def __init__(self, *, cutoff: datetime = T):
        self.snapshot_id = "SNAP:short:1"
        self.instrument_version_id = "INSTR:kraken:SOL:USD:1"
        self.venue_instrument_id = "SOLUSD"
        self.evaluation_cutoff = cutoff
        self.evaluated_at_utc = cutoff
        self.availability = SimpleNamespace(
            source_at_utc=cutoff - timedelta(seconds=60), visible_at_utc=cutoff
        )


def _minute_candles(*, end_epoch: datetime, count: int = 6) -> list[Candle]:
    return [
        Candle(
            timestamp=int(end_epoch.timestamp()) - (count - 1 - i) * 60,
            open=100.0, high=101.0, low=99.0, close=100.0,
            vwap=100.0, volume=10.0, trade_count=1,
        )
        for i in range(count)
    ]


class _ShortClient:
    """Kraken double: BTNL venue discovery + BTNL PreTrade/PostTrade + spot OHLC."""

    def __init__(self, *, eligible=True, book=None, raise_btln=False):
        self._eligible = eligible
        self._book = book
        self._raise_btln = raise_btln
        self.pretrade_symbols: list[str] = []
        self.posttrade_symbols: list[str] = []

    def get_ohlc(self, pair, interval=60, since=None):
        return _minute_candles(end_epoch=T)

    def get_ticker(self, pair):
        return {"last": 100.0}

    def get_asset_pairs(self, execution_venue=None):
        if execution_venue != producer.BITNOMIAL_EXECUTION_VENUE:
            return {}
        if not self._eligible:
            return {}  # pair absent from the margin venue
        return {
            "SOLUSD": {
                "altname": "SOLUSD",
                "wsname": "SOL/USD",
                "leverage_sell": [2, 3],
            }
        }

    def get_pre_trade(self, symbol):
        self.pretrade_symbols.append(symbol)
        if self._raise_btln:
            raise RuntimeError("BTNL PreTrade unavailable")
        return self._book or PreTradeBook(
            symbol=symbol,
            bids=[BookLevel(price=99.9, quantity=10.0)],
            asks=[BookLevel(price=100.1, quantity=10.0)],
        )

    def get_post_trade(self, symbol, count=100):
        self.posttrade_symbols.append(symbol)
        return []


# ---------------------------------------------------------------------------
# AC-022
# ---------------------------------------------------------------------------


def test_ac_022_margin_discovery_uses_bitnomial_venue_and_marks_btnl():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: SHORT margin discovery uses Kraken's Bitnomial venue and resolves a :BTNL venue symbol with a genuine leverage tier."""
    snapshot = _Snapshot()
    margin = producer.discover_short_margin(snapshot, client=_ShortClient())
    assert margin["margin_validation_status"] == "ELIGIBLE"
    assert margin["margin_eligible"] is True
    assert ":BTNL" in margin["margin_venue_symbol"].upper()
    assert margin["margin_max_leverage"] == 3.0  # min(account ceiling 3, venue max 3)


def test_ac_022_leverage_is_bounded_by_account_ceiling():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: the SHORT leverage tier is bounded by the account ceiling, never the raw venue maximum."""
    snapshot = _Snapshot()

    class _HighLeverageClient(_ShortClient):
        def get_asset_pairs(self, execution_venue=None):
            if execution_venue != producer.BITNOMIAL_EXECUTION_VENUE:
                return {}
            return {
                "SOLUSD": {
                    "altname": "SOLUSD",
                    "wsname": "SOL/USD",
                    "leverage_sell": [10],
                }
            }

    # Venue advertises 10x; the default account ceiling (3x) bounds the effective tier.
    bounded = producer.discover_short_margin(snapshot, client=_HighLeverageClient())
    assert bounded["margin_max_leverage"] == 3.0
    # An explicit tighter ceiling is honored.
    tighter = producer.discover_short_margin(
        snapshot, client=_HighLeverageClient(), account_leverage_ceiling=2.0
    )
    assert tighter["margin_max_leverage"] == 2.0


def test_ac_022_pair_absent_from_venue_is_ineligible_not_fabricated():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: a pair absent from the margin venue is INELIGIBLE (present negative evidence), never fabricated eligible."""
    snapshot = _Snapshot()
    margin = producer.discover_short_margin(snapshot, client=_ShortClient(eligible=False))
    assert margin["margin_validation_status"] == "INELIGIBLE"
    assert margin["margin_eligible"] is False
    assert margin["margin_venue_symbol"] is None


def test_ac_022_execution_uses_btnl_book_not_spot():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: SHORT execution evidence is built from the BTNL margin book symbol, never the spot symbol."""
    snapshot = _Snapshot()
    client = _ShortClient()
    evidence = producer.build_short_feasibility_evidence(
        snapshot, client=client, notional_usd=500.0, acquisition_instant=T,
        interval_minutes=1, interval_seconds=60,
    )
    assert evidence.direction == "SHORT"
    assert evidence.margin_venue_symbol and ":BTNL" in evidence.margin_venue_symbol
    assert evidence.execution_validation.status == VALID
    # The BTNL book was fetched with the :BTNL margin venue symbol, not the spot pair.
    assert client.pretrade_symbols and all(":BTNL" in s for s in client.pretrade_symbols)
    assert "SOLUSD" not in client.pretrade_symbols


def test_ac_022_ineligible_pair_yields_unavailable_execution_not_spot_as_btnl():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: an ineligible pair yields an explicitly UNAVAILABLE SHORT execution record; the spot book is never used as BTNL."""
    snapshot = _Snapshot()
    client = _ShortClient(eligible=False)
    evidence = producer.build_short_feasibility_evidence(
        snapshot, client=client, notional_usd=500.0, acquisition_instant=T,
        interval_minutes=1, interval_seconds=60,
    )
    assert evidence.execution_validation.status == UNAVAILABLE
    # No BTNL book was fetched at all (spot evidence never substituted).
    assert client.pretrade_symbols == []


def test_ac_022_btnl_book_failure_is_unavailable_present_evidence():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: a BTNL book failure yields an explicitly UNAVAILABLE (present) execution record, and F5 abstains rather than vetoing on absence."""
    snapshot = _Snapshot()
    client = _ShortClient(raise_btln=True)
    evidence = producer.build_short_feasibility_evidence(
        snapshot, client=client, notional_usd=500.0, acquisition_instant=T,
        interval_minutes=1, interval_seconds=60,
    )
    assert evidence.execution_validation.status == UNAVAILABLE
    # A present UNAVAILABLE execution record is missing evidence -> INSUFFICIENT_EVIDENCE.
    check = feasibility._execution_check(evidence, T)
    assert check.status is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE


def test_ac_022_short_execution_carries_btnl_provenance_for_f5():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: the SHORT evidence carries the :BTNL provenance F5 requires, so the BTNL book is trusted as the margin book."""
    snapshot = _Snapshot()
    evidence = producer.build_short_feasibility_evidence(
        snapshot, client=_ShortClient(), notional_usd=500.0, acquisition_instant=T,
        interval_minutes=1, interval_seconds=60,
    )
    assert feasibility._has_btnl_venue_provenance(evidence) is True


def test_ac_022_invalid_status_sort_order_constant_is_importable():
    """ATDD-R4-B2-controlled-paper-activation/AC-022: the negative execution status token used above is the real scanner token (no producer-side re-definition)."""
    assert INVALID == "INVALID"
    assert VALID == "VALID"
    assert UNAVAILABLE == "UNAVAILABLE"


def test_ac_022_point_in_time_provenance_round_trips_epoch_invariance():
    """ATDD-R4-B2-controlled-paper-activation/AC-022 and ATDD-RELEASE-PIPELINE-v1/AC-017: the durable point-in-time provenance token PERSISTS ``epoch_invariant``, so serialize -> parse reproduces the exact semantics that were published instead of silently downgrading epoch-invariant venue metadata to a post-epoch market observation; an absent or falsified flag makes the same input fail PIT validation."""
    epoch = T
    post_epoch = T + timedelta(seconds=30)

    market = producer.PointInTimeInput(
        name="freshness_1m_anchor",
        kind=producer.PIT_KIND_MARKET,
        event_cutoff=epoch,
        epoch_invariant=False,
    )
    venue = producer.PointInTimeInput(
        name="margin_venue_discovery",
        kind=producer.PIT_KIND_VENUE_METADATA,
        event_cutoff=post_epoch,
        epoch_invariant=True,
    )
    refs = (market.provenance_ref(epoch), venue.provenance_ref(epoch))
    assert "epoch_invariant=False" in refs[0]
    assert "epoch_invariant=True" in refs[1]
    # The published token's own pit_valid is what the reader must reproduce.
    assert "pit_valid=True" in refs[0]
    assert "pit_valid=True" in refs[1]

    parsed = {entry.name: entry for entry in producer.point_in_time_inputs(refs)}
    assert parsed["freshness_1m_anchor"].epoch_invariant is False
    assert parsed["freshness_1m_anchor"].event_cutoff == epoch
    assert parsed["margin_venue_discovery"].epoch_invariant is True
    assert parsed["margin_venue_discovery"].event_cutoff == post_epoch
    # pit_valid recomputed from parsed provenance matches the published semantics
    # on BOTH inputs, so the durable audit cannot contradict itself.
    assert parsed["freshness_1m_anchor"].pit_valid(epoch) is True
    assert parsed["margin_venue_discovery"].pit_valid(epoch) is True
    producer.assert_point_in_time_support(parsed.values(), evaluation_time=epoch)

    # Invariance is NEVER inferred from the input's name on read: dropping the
    # persisted token makes the same post-epoch venue metadata PIT-INVALID.
    stripped = ":".join(
        token
        for token in refs[1].split(":")
        if not token.startswith("epoch_invariant=")
    )
    (degraded,) = producer.point_in_time_inputs([stripped])
    assert degraded.name == "margin_venue_discovery"
    assert degraded.epoch_invariant is False
    assert degraded.pit_valid(epoch) is False
    with pytest.raises(producer.FeasibilityPointInTimeError):
        producer.assert_point_in_time_support([degraded], evaluation_time=epoch)


def test_ac_022_short_margin_venue_provenance_survives_the_durable_audit():
    """ATDD-R4-B2-controlled-paper-activation/AC-022 and ATDD-RELEASE-PIPELINE-v1/AC-017: a SHORT record whose margin-venue capability was acquired AFTER the evaluation epoch serializes an audit that still supports the record when read back, so the durable provenance never flags valid epoch-invariant venue metadata as a point-in-time violation."""
    snapshot = _Snapshot(cutoff=T)
    acquisition_instant = T + timedelta(seconds=30)
    evidence = producer.build_short_feasibility_evidence(
        snapshot,
        client=_ShortClient(),
        notional_usd=500.0,
        acquisition_instant=acquisition_instant,
        interval_minutes=1,
        interval_seconds=60,
    )
    assert evidence.evaluation_time == T

    parsed = {
        entry.name: entry
        for entry in producer.point_in_time_inputs(evidence.source_evidence_refs)
    }
    margin = parsed["margin_venue_discovery"]
    assert margin.kind == producer.PIT_KIND_VENUE_METADATA
    assert margin.event_cutoff == acquisition_instant > evidence.evaluation_time
    assert margin.epoch_invariant is True
    assert margin.pit_valid(evidence.evaluation_time) is True

    # Every admitted input still supports the record after a serialize -> parse
    # round trip: the audit contradicts nothing it published.
    producer.assert_point_in_time_support(
        parsed.values(), evaluation_time=evidence.evaluation_time
    )
    assert all(
        entry.pit_valid(evidence.evaluation_time) for entry in parsed.values()
    )


def test_ac_022_producer_dispatches_short_direction(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-022: the producer's injectable builder receives the requested SHORT direction and records genuine SHORT evidence."""
    producer._save_cursor(cursor_path, (0, 0))
    snapshot = _Snapshot()
    built = []

    def _builder(snap, direction):
        built.append(direction)
        return producer.build_short_feasibility_evidence(
            snap, client=_ShortClient(), notional_usd=500.0, acquisition_instant=T,
            interval_minutes=1, interval_seconds=60,
        )

    submitted = []
    summary = producer.capture_feasibility_evidence_shadow(
        settings=SimpleNamespace(
            opip_feature_bus_mode="shadow",
            opip_canonical_writer_mode="shadow",
            opip_feasibility_capture_limit=8,
            opip_feasibility_capture_budget_seconds=45,
            opip_feasibility_capture_notional_usd=500.0,
        ),
        reader=_FakeReader([snapshot]),
        evidence_builder=_builder,
        direction_for=lambda snap: "SHORT",
        submit_payload=lambda payload: submitted.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert built == ["SHORT"]
    assert summary.recorded == 1
    assert submitted[0]["evidence"]["evidence"]["direction"] == "SHORT"

    # A SHORT request routed to a LONG-only builder is rejected (never recorded).
    producer._save_cursor(cursor_path, (0, 0))  # reset so the second pass re-reads
    summary_2 = producer.capture_feasibility_evidence_shadow(
        settings=SimpleNamespace(
            opip_feature_bus_mode="shadow",
            opip_canonical_writer_mode="shadow",
            opip_feasibility_capture_limit=8,
            opip_feasibility_capture_budget_seconds=45,
            opip_feasibility_capture_notional_usd=500.0,
        ),
        reader=_FakeReader([snapshot]),
        evidence_builder=lambda snap, d: producer.build_long_feasibility_evidence(
            snap, client=_ShortClient(), notional_usd=500.0, acquisition_instant=T,
            interval_minutes=1, interval_seconds=60,
        ),
        direction_for=lambda snap: "SHORT",
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary_2.rejected == 1
    assert summary_2.recorded == 0


class _FakeReader:
    """Per-record fake (mirrors the committed reader seam)."""

    def __init__(self, snapshots):
        from app.opip.features.committed_snapshot_reader import CommittedSnapshotRecord

        self._records = tuple(
            CommittedSnapshotRecord(
                event_id=f"EV:{i + 1}",
                history_epoch=0,
                local_sequence=i + 1,
                snapshot=s,
                rejected=False,
            )
            for i, s in enumerate(snapshots)
        )

    def head_cursor(self):
        return (0, len(self._records))

    def read_records(self, *, after=None, limit=200):
        start = 0 if after is None else int(after[1])
        window = self._records[start : start + limit]
        tail = (0, start + len(window)) if window else after
        return window, tail

    def close(self):
        pass


@pytest.fixture
def cursor_path(tmp_path):
    return tmp_path / "fev_short_cursor.json"
