"""ATDD — scan coverage recovery after a legitimate candidate exclusion.

ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1.

Production defect (P1, baseline ``fed71e3c``): a single legitimately ineligible
candidate made every acquisition attempt PARTIAL. ``build_kraken_asset_universe``
already ranks every eligible underlying asset and then keeps only the first
``limit``; ``scan_market`` evaluated exactly those and had no deterministic
replacement. One newly listed pair with fewer than ``MIN_CANDLES_REQUIRED``
hourly candles (production: ``KORUSD``, 39/200 candles) therefore produced
``analyzed=199``, ``skipped=1``, an incomplete manifest and a
``PARTIAL_SCAN_NOT_ACTIONABLE`` promotion rejection for as long as the pair
stayed ranked inside the top 200 (up to ~8.3 days per listing).

The correction is bounded, deterministic replacement from the ranked pool the
universe builder has already acquired, plus truthful coverage/exclusion evidence
so an exclusion can never be invisible again.

These tests use synthetic Kraken data only: fixed ranking fixtures, a
deterministic provider stub and no network access.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import config as app_config
from app.scanner import market_scanner
from app.scanner.models import MarketSnapshot
from app.scanner.universe import UniverseAsset, UniverseBuildResult
from app.services import scan_attempt as sa
from app.services import scan_manifest as sm
from app.services import scan_result_promotion as promo
from app.services.scan_supervisor import attempt_staging_dir

INCREMENT = "ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1"
APP_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 3, 56, tzinfo=timezone.utc)
TARGET = 200


def _asset(base: str, rank: int) -> UniverseAsset:
    notional = 1_000_000.0 - rank
    return UniverseAsset(
        base_asset=base,
        usd_pair=f"{base}USD",
        primary_pair=f"{base}USD",
        primary_quote_currency="USD",
        primary_kraken_symbol=f"{base}/USD",
        usd_24h_notional_usd=notional,
        combined_24h_notional_usd=notional,
        liquidity_rank=rank,
    )


def _universe(selected: list[UniverseAsset], ranked: list[UniverseAsset] | None = None):
    pool = list(ranked if ranked is not None else selected)
    return UniverseBuildResult(
        assets=list(selected),
        eligible_usd_markets=len(pool),
        eligible_usdt_markets=0,
        unique_underlying_assets=len(pool),
        selected_liquid_assets=len(selected),
        usdt_usd_rate=1.0,
        warnings=[],
        ticker_batch_requests=1,
        ranked_assets=pool,
    )


def _snapshot(symbol: str, universe_asset: UniverseAsset | None = None) -> MarketSnapshot:
    base = (
        universe_asset.base_asset
        if universe_asset is not None
        else symbol.removesuffix("USD")
    )
    return MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=80,
        trend="UP",
        underlying_asset=base,
        primary_pair=symbol,
        primary_quote_currency="USD",
        kraken_public_symbol=f"{base}/USD",
        v2_feature_status="READY",
        v2_long_score=70.0,
        v2_short_score=20.0,
    )


def _install(monkeypatch, universe, *, replacement: int = 25, outcomes=None):
    """Stub only the I/O boundaries: ranking, provider and settings."""
    calls: list[str] = []
    table = dict(outcomes or {})
    monkeypatch.setattr(
        app_config,
        "get_settings",
        lambda: SimpleNamespace(
            opip_scan_max_workers=4,
            opip_scan_batch_assets=50,
            opip_scan_replacement_candidates=replacement,
        ),
    )
    monkeypatch.setattr(market_scanner, "KrakenClient", lambda: object())
    monkeypatch.setattr(
        market_scanner, "build_kraken_asset_universe", lambda client, limit: universe
    )

    def fake_analyze(symbol, universe_asset=None):
        calls.append(symbol)
        status, reason = table.get(symbol, ("ok", None))
        if status != "ok":
            return status, None, reason
        return "ok", _snapshot(symbol, universe_asset), None

    monkeypatch.setattr(market_scanner, "analyze_symbol", fake_analyze)
    return calls


def _ranked_fixture(*, skipped: tuple[str, ...] = (), pool_extra: int = 0):
    """200 selected candidates (ranks 1..200) plus optional replacement candidates."""
    bases = [f"A{index:03d}" for index in range(1, TARGET + 1)]
    for name in skipped:
        bases[int(name[1:]) - 1] = name
    selected = [_asset(name, index) for index, name in enumerate(bases, start=1)]
    ranked = list(selected)
    for offset in range(1, pool_extra + 1):
        ranked.append(_asset(f"R{offset:03d}", TARGET + offset))
    return selected, ranked


# --- AC-001: an all-valid universe needs no replacement --------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_complete_acquisition_needs_no_replacement(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-001 200 valid candidates produce 200 snapshots and zero replacements."""
    selected, ranked = _ranked_fixture()
    calls = _install(monkeypatch, _universe(selected, ranked))

    result = market_scanner.scan_market(limit=TARGET)

    assert result.requested == TARGET
    assert result.analyzed == TARGET
    assert len(result.snapshots) == TARGET
    assert result.attempted == TARGET
    assert result.replacement_attempts == 0
    assert result.exclusions == []
    assert len(calls) == TARGET


# --- AC-002: the production regression ------------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_short_history_candidate_is_replaced_and_scan_completes(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-002 the exact production signature is repaired, not accepted.

    Before the correction this scan cannot reach 200 valid snapshots even though
    a valid rank-201 candidate exists.
    """
    selected, ranked = _ranked_fixture(skipped=("A049",), pool_extra=1)
    selected[48] = _asset("KOR", 49)
    ranked[48] = selected[48]
    calls = _install(
        monkeypatch,
        _universe(selected, ranked),
        outcomes={"KORUSD": ("skip", "KORUSD: insufficient history (39 candles)")},
    )

    result = market_scanner.scan_market(limit=TARGET)

    assert result.requested == TARGET
    assert result.analyzed == TARGET, "one ineligible candidate must not cap the scan at 199"
    assert len(result.snapshots) == TARGET
    assert len({item.underlying_asset for item in result.snapshots}) == TARGET
    assert result.skipped == 1
    assert result.failed == 0
    assert result.attempted == TARGET + 1
    assert result.replacement_attempts == 1
    assert result.remaining_candidate_capacity == 0
    assert calls.count("KORUSD") == 1
    assert calls.count("R001USD") == 1
    assert [item["pair"] for item in result.exclusions] == ["KORUSD"]
    assert result.exclusions[0]["status"] == "skip"
    assert result.exclusions[0]["liquidity_rank"] == 49
    assert "insufficient history (39 candles)" in result.exclusions[0]["reason"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_worker_persists_a_complete_recovered_attempt(tmp_path: Path, monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-002 the worker seam reports a recovered attempt as complete."""
    from app.jobs import scan_worker

    settings = _settings(
        tmp_path,
        opip_production_scoring_version="v2",
        opip_score_v2_evaluation_enabled=True,
    )
    monkeypatch.setattr(scan_worker, "get_settings", lambda: settings)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:W", deadline_seconds=300, now=NOW)

    selected, ranked = _ranked_fixture(skipped=("A049",), pool_extra=1)
    _install(
        monkeypatch,
        _universe(selected, ranked),
        outcomes={"A049USD": ("skip", "A049USD: insufficient history (39 candles)")},
    )
    result = market_scanner.scan_market(limit=TARGET)
    monkeypatch.setattr(market_scanner, "scan_market", lambda limit: result)

    assert scan_worker.run(attempt_id="SCAN:W") == scan_worker.EXIT_OK

    directory = attempt_staging_dir(settings, "SCAN:W")
    status = json.loads((directory / scan_worker.STATUS_NAME).read_text(encoding="utf-8"))
    manifest = sm.read_manifest(directory)

    assert status["state"] == "COMPLETED"
    assert status["reason_code"] is None
    assert status["coverage"]["analyzed"] == status["coverage"]["requested"] == TARGET
    assert status["coverage"]["attempted"] == TARGET + 1
    assert status["exclusions"]["records"][0]["pair"] == "A049USD"
    assert status["universe"]["unique_underlying_assets"] == TARGET + 1
    assert manifest["complete"] is True
    assert sm.is_complete(manifest) is True


# --- AC-003: repeated invalid replacements --------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_repeated_invalid_replacements_are_recorded_and_order_preserved(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-003 two exclusions, then the next ranked candidate completes the scan."""
    selected, ranked = _ranked_fixture(skipped=("A049",), pool_extra=2)
    calls = _install(
        monkeypatch,
        _universe(selected, ranked),
        outcomes={
            "A049USD": ("skip", "A049USD: insufficient history (12 candles)"),
            "R001USD": ("skip", "R001USD: insufficient history (198 candles)"),
        },
    )

    result = market_scanner.scan_market(limit=TARGET)

    assert result.analyzed == TARGET
    assert result.skipped == 2
    assert result.replacement_attempts == 2
    assert [item["pair"] for item in result.exclusions] == ["A049USD", "R001USD"]
    assert calls.index("R001USD") < calls.index("R002USD")


# --- AC-004: exhaustion stays fail-closed ---------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_exhausted_candidate_pool_stays_incomplete(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-004 no replacement available means an honest partial acquisition."""
    from app.jobs import scan_worker

    selected, _ = _ranked_fixture(skipped=("A049",))
    _install(
        monkeypatch,
        _universe(selected, list(selected)),
        outcomes={"A049USD": ("skip", "A049USD: insufficient history (39 candles)")},
    )

    result = market_scanner.scan_market(limit=TARGET)

    assert result.analyzed == TARGET - 1
    assert result.replacement_attempts == 0
    assert result.remaining_candidate_capacity == 0

    coverage = scan_worker._coverage(result, TARGET)
    assert coverage["analyzed"] == TARGET - 1
    assert coverage["skipped"] == 1
    assert sm.coverage_is_complete(coverage) is False


# --- AC-005: data-quality rejections are first class -----------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_data_quality_rejection_is_recorded_and_replaced(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-005 a rejected candidate never contributes a snapshot and is replaced."""
    selected, ranked = _ranked_fixture(pool_extra=1)
    selected[4] = _asset("BAD", 5)
    ranked[4] = selected[4]
    _install(
        monkeypatch,
        _universe(selected, ranked),
        outcomes={"BADUSD": ("data_reject", "BADUSD: invalid OHLC close")},
    )

    result = market_scanner.scan_market(limit=TARGET)

    assert result.analyzed == TARGET
    assert result.data_quality_rejected == 1
    assert result.data_quality_rejections == ["BADUSD: invalid OHLC close"]
    assert result.failed == 0
    assert "BADUSD" not in {item.symbol for item in result.snapshots}
    assert [item["status"] for item in result.exclusions] == ["data_reject"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_real_provider_gate_classifies_short_history_as_a_skip(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-005 the 200-candle eligibility gate itself is unchanged and still skips."""
    from app.exchanges.kraken import Candle

    candles = [
        Candle(
            timestamp=1_760_000_000 + index * 3600,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            vwap=100.0,
            volume=10.0,
            trade_count=5,
        )
        for index in range(39)
    ]

    class Client:
        def get_ohlc(self, symbol, interval=60, since=None):
            return list(candles)

    monkeypatch.setattr(market_scanner, "KrakenClient", Client)

    status, snapshot, message = market_scanner.analyze_symbol("KORUSD")

    assert status == "skip"
    assert snapshot is None
    assert message == "KORUSD: insufficient history (39 candles)"


# --- AC-006: coverage and exclusion evidence ------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_coverage_evidence_keeps_totals_and_recovered_exclusions(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-006 201 attempted, 200 valid, one recorded exclusion — all visible."""
    from app.jobs import scan_worker

    selected, ranked = _ranked_fixture(skipped=("A049",), pool_extra=1)
    _install(
        monkeypatch,
        _universe(selected, ranked),
        outcomes={"A049USD": ("skip", "A049USD: insufficient history (39 candles)")},
    )
    result = market_scanner.scan_market(limit=TARGET)

    coverage = scan_worker._coverage(result, TARGET)
    assert coverage["schema_version"] == sm.COVERAGE_SCHEMA_VERSION
    assert coverage["requested"] == TARGET
    assert coverage["analyzed"] == TARGET
    assert coverage["attempted"] == TARGET + 1
    assert coverage["skipped"] == 1
    assert coverage["unique_assets"] == TARGET
    assert coverage["data_quality_rejected"] == 0
    assert sm.coverage_is_complete(coverage) is True

    exclusions = scan_worker._exclusions(result)
    assert exclusions["total"] == 1 and exclusions["truncated"] is False
    assert exclusions["records"][0]["pair"] == "A049USD"
    assert "insufficient history" in exclusions["records"][0]["reason"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_exclusion_evidence_is_bounded_and_reports_truncation():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-006 a pathological attempt cannot grow the persisted evidence without bound."""
    from app.jobs import scan_worker

    result = SimpleNamespace(
        exclusions=[
            {
                "pair": f"S{index:03d}USD",
                "base_asset": f"S{index:03d}",
                "liquidity_rank": index,
                "status": "skip",
                "reason": "S" * 400,
                "stage": "initial",
            }
            for index in range(1, 41)
        ]
    )
    evidence = scan_worker._exclusions(result)

    assert evidence["total"] == 40
    assert evidence["recorded"] == scan_worker.EXCLUSIONS_RECORDED_LIMIT
    assert evidence["truncated"] is True
    assert len(evidence["records"][0]["reason"]) <= scan_worker.EXCLUSION_REASON_MAX_CHARS


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_qualification_adapter_reports_coverage_gaps_not_observations():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-006 downstream counters describe gaps, so a recovered scan reads COMPLETED."""
    from app.services.operations_analytics import derive_scan_outcome

    coverage = {
        "schema_version": sm.COVERAGE_SCHEMA_VERSION,
        "requested": 200,
        "analyzed": 200,
        "failed": 0,
        "skipped": 1,
        "data_quality_rejected": 0,
        "attempted": 201,
        "unique_assets": 200,
    }
    promoted = SimpleNamespace(coverage=coverage, snapshots=[_snapshot("BTCUSD")])

    result = promo.to_scan_result(promoted)

    assert (result.requested, result.analyzed, result.skipped, result.failed) == (200, 200, 0, 0)
    assert derive_scan_outcome(result) == "COMPLETED"

    legacy = SimpleNamespace(
        coverage={"requested": 200, "analyzed": 199, "failed": 0, "skipped": 1},
        snapshots=[],
    )
    legacy_result = promo.to_scan_result(legacy)
    assert (
        legacy_result.requested,
        legacy_result.analyzed,
        legacy_result.skipped,
    ) == (200, 199, 1)
    assert derive_scan_outcome(legacy_result) == "PARTIAL"


# --- AC-007: manifest completeness contract -------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-007")
def test_legacy_manifest_keeps_its_original_interpretation():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-007 historical schema-1 coverage is never reinterpreted."""
    legacy_complete = {"requested": 200, "analyzed": 200, "failed": 0, "skipped": 0}
    legacy_partial = {"requested": 200, "analyzed": 199, "failed": 0, "skipped": 1}

    assert sm.coverage_is_complete(legacy_complete) is True
    assert sm.coverage_is_complete(legacy_partial) is False


@pytest.mark.acceptance(f"{INCREMENT}/AC-007")
def test_recovered_coverage_is_complete_but_inconsistent_coverage_is_not():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-007 recovered completeness is earned, and bad accounting fails closed."""
    recovered = {
        "schema_version": sm.COVERAGE_SCHEMA_VERSION,
        "requested": 200,
        "analyzed": 200,
        "failed": 0,
        "skipped": 1,
        "data_quality_rejected": 0,
        "attempted": 201,
        "unique_assets": 200,
    }
    assert sm.coverage_is_complete(recovered) is True

    # A shortfall is never complete, whatever the other counters claim.
    assert sm.coverage_is_complete({**recovered, "analyzed": 199, "attempted": 200}) is False
    # Accounting that does not reconcile fails closed.
    assert sm.coverage_is_complete({**recovered, "attempted": 200}) is False
    # Duplicate underlying assets can never satisfy the target.
    assert sm.coverage_is_complete({**recovered, "unique_assets": 199}) is False
    # A missing counter is not a zero.
    broken = dict(recovered)
    del broken["attempted"]
    assert sm.coverage_is_complete(broken) is False
    # Non-integer counters are refused rather than coerced.
    assert sm.coverage_is_complete({**recovered, "attempted": "201"}) is False

    assert sm.is_complete({"complete": True, "coverage": recovered}) is True
    assert sm.is_complete({"complete": False, "coverage": recovered}) is False


# --- AC-008: promotion integrity is unchanged -----------------------------


def _settings(tmp_path: Path, **overrides):
    values = {
        "opip_scan_state_dir": str(tmp_path / "scan"),
        "opip_scan_execution_mode": "isolated",
        "opip_scan_deadline_seconds": 300,
        "signal_quality_scan_interval_seconds": 600,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _publish_worker_evidence(settings, attempt_id: str, snapshots, coverage) -> Path:
    """Write exactly what the worker writes: snapshots, manifest and sentinel."""
    directory = attempt_staging_dir(settings, attempt_id)
    directory.mkdir(parents=True, exist_ok=True)
    rows = b"".join(
        json.dumps(
            {
                "symbol": item.symbol,
                "last_price": item.last_price,
                "ema20": item.ema20,
                "ema50": item.ema50,
                "ema200": item.ema200,
                "rsi": item.rsi,
                "macd_line": item.macd_line,
                "macd_signal": item.macd_signal,
                "macd_histogram": item.macd_histogram,
                "atr": item.atr,
                "atr_pct": item.atr_pct,
                "volume_ratio": item.volume_ratio,
                "technical_score": item.technical_score,
                "trend": item.trend,
                "underlying_asset": item.underlying_asset,
                "primary_pair": item.primary_pair,
                "v2_feature_status": item.v2_feature_status,
                "v2_long_score": item.v2_long_score,
                "v2_short_score": item.v2_short_score,
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
        for item in snapshots
    )
    snapshots_path = directory / "snapshots.jsonl"
    snapshots_path.write_bytes(rows)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    attempt = store.get(attempt_id)
    sm.write_manifest(
        directory,
        {
            "attempt_id": attempt_id,
            "fence": attempt.fence if attempt else 1,
            "coverage": coverage,
            "snapshots": {
                "file": "snapshots.jsonl",
                "rows": len(snapshots),
                "sha256": sm.sha256_path(snapshots_path),
            },
        },
        complete=sm.coverage_is_complete(coverage),
        row_count=len(snapshots),
    )
    return directory


@pytest.mark.acceptance(f"{INCREMENT}/AC-008")
def test_recovered_manifest_is_consumable_and_corruption_still_fails(tmp_path: Path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-008 a genuinely recovered scan is promotable; a corrupted one never is."""
    settings = _settings(tmp_path, opip_production_scoring_version="v2")
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:R", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:R", sa.OUTCOME_COMPLETED, None, now=NOW)

    snapshots = [_snapshot("BTCUSD"), _snapshot("ETHUSD")]
    coverage = {
        "schema_version": sm.COVERAGE_SCHEMA_VERSION,
        "requested": 2,
        "analyzed": 2,
        "failed": 0,
        "skipped": 1,
        "data_quality_rejected": 0,
        "attempted": 3,
        "unique_assets": 2,
    }
    directory = _publish_worker_evidence(settings, "SCAN:R", snapshots, coverage)

    promoted = promo.load_promoted(
        settings=settings, attempt=attempt, now=datetime.now(timezone.utc)
    )
    assert promoted.complete is True
    assert len(promoted.snapshots) == 2

    (directory / "snapshots.jsonl").write_bytes(b'{"symbol":"BTCUSD"}\n')
    with pytest.raises(promo.PromotionRejected):
        promo.load_promoted(
            settings=settings, attempt=attempt, now=datetime.now(timezone.utc)
        )


@pytest.mark.acceptance(f"{INCREMENT}/AC-008")
def test_partial_attempt_is_still_not_actionable(tmp_path: Path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-008 the fail-closed promotion policy is untouched."""
    from app.services import scan_promotion_runner as runner

    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:P", deadline_seconds=300, now=NOW)
    store.terminalize("SCAN:P", sa.OUTCOME_PARTIAL, "COVERAGE_INCOMPLETE", now=NOW)

    called: list[object] = []
    outcome = runner.run_pending_promotion(
        settings, now=NOW, qualify=called.append, hold_lock=False
    )

    assert outcome["status"] == runner.STATUS_REJECTED
    assert outcome["reason_code"] == "PARTIAL_SCAN_NOT_ACTIONABLE"
    assert called == []


# --- AC-009 / AC-010: Control Center read view ----------------------------


def _analytics_settings(tmp_path: Path):
    from app.core.config import Settings

    return Settings(
        webhook_secret="test-operator-credential",  # gitleaks:allow - test fixture
        app_env="test",
        opip_analytics_db_path=str(tmp_path / "absent_analytics.db"),
        opip_analytics_scan_state_dir=str(tmp_path / "scan"),
        opip_analytics_scoring_comparison_path=str(tmp_path / "cmp.jsonl"),
    )


def _canonical_tree(settings, *, with_evidence: bool) -> None:
    root = Path(settings.opip_analytics_scan_state_dir)
    if not with_evidence:
        return
    staging = root / "staging" / "SCAN-20261106T035615Z"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "snapshots.jsonl").write_text(
        json.dumps(
            {"symbol": "BTC/USD", "technical_score": 80, "v2_feature_status": "READY"}
        )
        + "\n",
        encoding="utf-8",
    )
    digest = sm.sha256_path(staging / "snapshots.jsonl")
    (staging / "manifest.json").write_text(
        json.dumps({"attempt_id": "SCAN:20261106T035615Z", "snapshots": {"sha256": digest}}),
        encoding="utf-8",
    )
    (staging / "worker_status.json").write_text(
        json.dumps(
            {
                "attempt_id": "SCAN:20261106T035615Z",
                "state": "COMPLETED",
                "finished_at": "2026-11-06T03:58:00+00:00",
                "coverage": {
                    "schema_version": sm.COVERAGE_SCHEMA_VERSION,
                    "requested": 200,
                    "analyzed": 200,
                    "failed": 0,
                    "skipped": 1,
                    "data_quality_rejected": 0,
                    "attempted": 201,
                    "unique_assets": 200,
                },
                "exclusions": {
                    "recorded": 1,
                    "total": 1,
                    "truncated": False,
                    "records": [
                        {
                            "pair": "KORUSD",
                            "base_asset": "KOR",
                            "liquidity_rank": 49,
                            "status": "skip",
                            "reason": "KORUSD: insufficient history (39 candles)",
                            "stage": "initial",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "scan_attempts.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "event": "TERMINALIZE",
                "attempt_id": "SCAN:20261106T035615Z",
                "state": "TERMINAL",
                "outcome": "COMPLETED",
                "at": "2026-11-06T03:58:30+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.acceptance(f"{INCREMENT}/AC-009")
def test_control_center_scanner_view_exposes_coverage_and_exclusions(tmp_path: Path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-009 the operator reads coverage, the excluded pair and its reason."""
    from app.opip.analytics import direct

    settings = _analytics_settings(tmp_path)
    _canonical_tree(settings, with_evidence=True)

    payload = direct.funnel(settings, limit=5)

    assert payload["status"] == "MEASURED"
    attempt = payload["attempts"][0]
    worker = attempt["worker_status"]
    assert worker["coverage"]["analyzed"] == worker["coverage"]["requested"] == 200
    assert worker["coverage"]["attempted"] == 201
    assert worker["exclusions"]["records"][0]["pair"] == "KORUSD"
    assert "insufficient history" in worker["exclusions"]["records"][0]["reason"]
    # Promotion and acquisition stay separate dimensions.
    assert attempt["acquisition_outcome"] == "COMPLETED"
    assert attempt["promotion_reason"] == "NO_PROMOTION_EVENT_SOURCE"


@pytest.mark.acceptance(f"{INCREMENT}/AC-010")
def test_missing_scanner_evidence_is_never_reported_as_healthy(tmp_path: Path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-010 with no recorded evidence the view says so instead of guessing."""
    from app.opip.analytics import direct

    settings = _analytics_settings(tmp_path)
    _canonical_tree(settings, with_evidence=False)

    payload = direct.funnel(settings, limit=5)

    assert payload["status"] == "NO_EVIDENCE"
    assert payload["attempts"] == []


# --- AC-011: acquisition authority is unchanged ---------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-011")
def test_scanner_acquisition_keeps_no_notification_ai_or_execution_authority():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-011 the correction adds no authority to the isolated acquisition path."""
    forbidden = (
        "scan_opportunities",
        "chief_alert_notifier",
        "qualified_alert",
        "telegram",
        "order_intent",
        "kraken_private",
        "AddOrder",
        "operator_control",
        "set_override_mode",
    )
    for relative in (
        "app/jobs/scan_worker.py",
        "app/scanner/market_scanner.py",
        "app/scanner/universe.py",
    ):
        source = (APP_ROOT / relative).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{relative} must not reference {token}"

    # The 200-candle history rule is a legitimate eligibility gate and is unchanged.
    assert market_scanner.MIN_CANDLES_REQUIRED == 200


# --- AC-012: bounded replacement ------------------------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-012")
@pytest.mark.parametrize("budget", [0, 1, 25, 100])
def test_replacement_attempts_are_bounded(monkeypatch, budget):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-012 a pathological pool cannot cause unbounded acquisition work."""
    selected, ranked = _ranked_fixture(skipped=("A049",), pool_extra=40)
    outcomes = {"A049USD": ("skip", "A049USD: insufficient history (39 candles)")}
    for offset in range(1, 41):
        outcomes[f"R{offset:03d}USD"] = (
            "skip",
            f"R{offset:03d}USD: insufficient history (9 candles)",
        )
    calls = _install(
        monkeypatch, _universe(selected, ranked), replacement=budget, outcomes=outcomes
    )

    result = market_scanner.scan_market(limit=TARGET)

    expected = min(budget, len(ranked) - TARGET)
    assert result.replacement_attempts == expected
    assert result.skipped == 1 + expected
    assert result.attempted == TARGET + expected
    assert len(calls) == result.attempted
    assert result.analyzed == TARGET - 1
