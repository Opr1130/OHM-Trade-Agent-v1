"""OPIP-LEARN-FRESH-001 / Slice 0: TESTS FIRST, source-contract readiness.

These tests are intentionally allowed to be RED until every evidence source
needed for the prospective V2 cohort is proven. No collector/learning activation,
market/network calls, production writes, or test changes to safety gates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.decision.funnel import QualificationFunnel
from app.opip.decision.summary import build_scan_summary
from app.opip.discovery.admission import (
    build_callback_evaluation,
    finalize_broad_search_evaluations,
)
from app.scanner.models import MarketSnapshot

APP_ROOT = Path(__file__).resolve().parents[1]
EXPORTER = APP_ROOT / "deploy/remote/export-opip-learning-evidence.sh"
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def _exported_jsonl_paths() -> set[str]:
    script = EXPORTER.read_text(encoding="utf-8")
    return set(re.findall(r'copy_locked_jsonl "\$DATA_ROOT/([^"]+)"', script))


def _exported_json_state_paths_with_locks() -> dict[str, str]:
    """Planned copy-only JSON state routes, separate from JSONL evidence.

    Each route must name the writer's actual registry lock explicitly. This
    avoids inventing a .<file>.lock path from the JSON filename and avoids
    introducing an independently-written JSONL twin.
    """
    script = EXPORTER.read_text(encoding="utf-8")
    pattern = (
        r'^\s*copy_locked_json_state\s+"\$DATA_ROOT/([^"]+\.json)"'
        r'\s+"state_snapshots/[^"]+\.json"'
        r'\s+"\$DATA_ROOT/(\.[^"]+\.lock)"'
    )
    return dict(re.findall(pattern, script, flags=re.MULTILINE))


def _screening_row() -> dict:
    snapshot = MarketSnapshot(
        symbol="XRPUSD",
        primary_pair="XXRPZUSD",
        underlying_asset="XRP",
        last_price=1.08231,
        ema20=1.08,
        ema50=1.07,
        ema200=1.06,
        rsi=61.0,
        macd_line=0.01,
        macd_signal=0.005,
        macd_histogram=0.005,
        atr=0.02,
        atr_pct=1.85,
        volume_ratio=2.0,
        technical_score=78,
        trend="bullish",
        ticker_last=1.08231,
    )
    provisional = build_callback_evaluation(
        snapshot, 78, 74, "LONG",
        observed_at=NOW, scan_id="SCAN:S0:001", universe_count=1,
        min_score=75,
    )
    assert provisional is not None
    rows = finalize_broad_search_evaluations(
        [provisional], selected=[], observed_at=NOW, universe_count=1,
        min_score=75,
    )
    assert len(rows) == 1
    return rows[0]


@pytest.mark.acceptance
def test_s0_001_qualification_sources_are_exported_without_reactivating_p1():
    """AT-001/005, CT-001/002, EC-091: approved source inventory exists."""
    paths = _exported_jsonl_paths()
    required = {
        "opip/qualification/screening_evaluations.jsonl",
        "opip/qualification/funnel_events.jsonl",
        "opip/qualification/scan_summaries.jsonl",
        "candidate_trace.jsonl",
        "intelligence_learning/events.jsonl",
        "telegram_delivery_events.jsonl",
    }
    assert required <= paths
    assert "p1_shadow_outbox.jsonl" not in paths


@pytest.mark.acceptance
def test_s0_002_screening_row_contains_finalized_rejection_identity():
    """AT-001, AT-005, EC-102: inspect a real source producer contract."""
    row = _screening_row()
    assert row["scan_id"] == "SCAN:S0:001"
    assert row["scanner_type"] == "BROAD_SEARCH"
    assert row["venue_instrument_id"]
    assert row["observed_at"]
    assert row["metadata"]["finalization_status"] == "FINALIZED"
    assert row["outcome"] in {"COARSE_RANK_LIMIT", "BELOW_THRESHOLD"}


@pytest.mark.acceptance
def test_s0_003_funnel_and_summary_preserve_rejections_and_ai_stage():
    """AT-001/003, CT-002/003: source-level, no paid AI invocation."""
    funnel = QualificationFunnel(scan_id="SCAN:S0:002", decision_at=NOW)
    funnel.register(symbol="XRPUSD", direction="SHORT")
    funnel.ai_stage.invocation_status = "BUDGET_BLOCKED"
    funnel.ai_stage.model = "read-only-source-model"
    events = funnel.funnel_events()
    assert len(events) == 1
    assert events[0]["candidate_id"]
    assert events[0]["scan_id"] == "SCAN:S0:002"
    assert events[0]["gate_results"]
    summary = build_scan_summary(funnel)
    assert summary["record_type"] == "OPIP_SCAN_SUMMARY"
    assert summary["ai_stage"]["invocation_status"] == "BUDGET_BLOCKED"
    assert summary["ai_stage"]["model"] == "read-only-source-model"
    assert summary["funnel"]["entered"] >= 1


@pytest.mark.acceptance
def test_s0_004_delivery_ledger_is_a_distinct_exported_source():
    """AT-004: actual delivery state must not be guessed from a decision."""
    from app.services.telegram_delivery import EVENT_FILE
    from app.services.tradevisor_inbox import INBOX_FILE

    assert EVENT_FILE.name == "telegram_delivery_events.jsonl"
    assert EVENT_FILE.name in _exported_jsonl_paths()
    assert INBOX_FILE.name == "tradevisor_v1_inbox.json"
    assert INBOX_FILE.name != EVENT_FILE.name


@pytest.mark.acceptance
def test_s0_005_tradevisor_frozen_decisions_have_verified_transfer_coverage():
    """RED until frozen Tradevisor inbox records have a verified transfer route.

    Telegram delivery records alone cannot reconstruct rejected/unnotified
    Tradevisor decisions or the frozen decision_record.
    """
    from app.services.tradevisor_inbox import INBOX_FILE, LOCK_FILE

    routes = _exported_json_state_paths_with_locks()
    assert routes.get(INBOX_FILE.name) == LOCK_FILE.name, (
        "S0 GAP: Tradevisor JSON frozen decisions lack a copy-only snapshot "
        "route under their existing registry lock; JSONL conversion is forbidden"
    )
    assert "state_snapshots/manifest.json" in EXPORTER.read_text(encoding="utf-8"), (
        "S0 GAP: a per-generation snapshot integrity/lineage manifest is missing"
    )


@pytest.mark.acceptance
def test_s0_006_manual_advisory_outcome_source_has_verified_transfer_coverage():
    """RED until recommendation/observed-outcome source is available remotely."""
    from app.services.trade_outcome_registry import OUTCOME_FILE, registry_lock_file

    routes = _exported_json_state_paths_with_locks()
    assert routes.get(OUTCOME_FILE.name) == registry_lock_file().name, (
        "S0 GAP: trade-outcome JSON registry lacks a copy-only snapshot route "
        "under its existing registry lock; Telegram is not trade-outcome proof"
    )
    assert "state_snapshots/manifest.json" in EXPORTER.read_text(encoding="utf-8"), (
        "S0 GAP: a per-generation snapshot integrity/lineage manifest is missing"
    )


@pytest.mark.acceptance
def test_s0_007_v2_screening_row_proves_production_scorer_identity():
    """RED until the persisted Stage-0 row proves V2 rather than legacy V1.

    A legacy strategy_version label or a high score is not sufficient proof.
    """
    row = _screening_row()
    evidence = row.get("metadata") or {}
    value = row.get("production_scoring_version") or evidence.get("production_scoring_version")
    assert value == "v2", (
        "S0 GAP: finalized screening row lacks explicit production_scoring_version=v2; "
        "cannot classify V2 source population from legacy version labels alone"
    )


@pytest.mark.acceptance
def test_s0_008_existing_export_has_retired_p1_and_source_lineage_guards():
    """BR-005/010: never reinstate the historical multi-hundred-MB P1 outbox."""
    source = EXPORTER.read_text(encoding="utf-8")
    assert 'rm -f -- "$EXPORT_ROOT/p1_shadow_outbox.jsonl"' in source
    assert "production_deployed_sha" in source
    assert "manifest.env" in source
