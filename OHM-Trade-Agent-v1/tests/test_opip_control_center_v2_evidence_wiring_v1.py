"""ATDD — Control Center V2 evidence wiring: answer from evidence, or say what is missing.

ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1. The November 8 production review
found three different failures rendered as one: an operator could not tell a
missing projection from a missing interaction, a read that ran out of time from
an empty evidence set, or a reference document from an activation. These tests
pin the distinctions, and pin the bounded cost of the two readers that sit in
the authenticated request path.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.analytics import direct
from app.opip.config import staging as config_staging
from app.opip.decision import store

INCREMENT = "ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1"
APP_ROOT = Path(__file__).resolve().parents[1]
UI = APP_ROOT / "control-center-v2"


def _ledger_settings(path: Path) -> SimpleNamespace:
    """Only the reader's own setting is needed, and it is never written to."""
    return SimpleNamespace(opip_ai_router_usage_path=str(path))


def _usage(status: str, *, latency: int = 1_000, error_code: str | None = None) -> dict:
    return {
        "timestamp_utc": "2026-11-08T09:00:00+00:00",
        "provider": "openai",
        "model": "gpt-5",
        "route_tier": "premium",
        "candidate_count": 3,
        "latency_ms": latency,
        "status": status,
        "error_code": error_code,
        "advisory_only": True,
        "funded_trade_authority_changed": False,
    }


def _write_jsonl(path: Path, rows: list[dict], *, trailing: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(body + trailing)


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_chief_evidence_is_read_from_the_router_ledger(tmp_path):
    """AC-001: recorded provider invocations are evidence, not a missing source."""
    path = tmp_path / "opip_ai_router_usage.jsonl"
    _write_jsonl(
        path,
        [
            _usage("OK", latency=900),
            _usage("FAILED", latency=1_500, error_code="EMPTY_RESPONSE"),
            _usage("FAILED", latency=2_100, error_code="PROVIDER_TIMEOUT"),
        ],
    )

    payload = direct.ai(_ledger_settings(path))

    assert payload["status"] == "MEASURED"
    assert payload["reason_code"] is None
    assert payload["source"] == "CANONICAL_DIRECT"
    assert payload["verification"] == "UNPROJECTED"
    summary = payload["summary"]
    assert summary["inferences"] == 3
    assert summary["succeeded"] == 1
    assert summary["failed"] == 2
    assert summary["failure_codes"] == {"EMPTY_RESPONSE": 1, "PROVIDER_TIMEOUT": 1}
    assert summary["statuses"] == {"OK": 1, "FAILED": 2}
    assert summary["latency_ms_p50"] == 1_500.0
    assert summary["models"] == [{"provider": "openai", "model": "gpt-5", "total": 3}]
    assert summary["funded_trade_authority_changed"] is False
    assert payload["records_read"] == 3
    assert len(payload["page"]["rows"]) == 3
    assert payload["page"]["rows"][0]["verdict"] == "FAILED"


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_ai_absence_states_are_distinguished_and_never_claim_success(tmp_path):
    """AC-001: absent ledger, empty ledger and recorded invocations are three states."""
    absent = direct.ai(_ledger_settings(tmp_path / "missing.jsonl"))
    assert absent["status"] == "NO_EVIDENCE"
    assert absent["reason_code"] == "NO_AI_USAGE_LEDGER"

    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("", encoding="utf-8")
    empty = direct.ai(_ledger_settings(empty_path))
    assert empty["status"] == "NO_EVIDENCE"
    assert empty["reason_code"] == "NO_AI_INVOCATION_RECORDED"

    for payload in (absent, empty):
        # No rate is invented from an absence, and no zero stands in for unknown.
        assert payload["summary"]["inferences"] == 0
        assert payload["summary"]["succeeded"] == 0
        assert payload["summary"]["win_rate"] is None
        assert payload["summary"]["false_signal_rate"] is None
        assert payload["summary"]["missed_opportunity_rate"] is None
        assert payload["summary"]["matured_outcomes"] is None
        assert payload["summary"]["latency_ms_p50"] is None
        assert payload["summary"]["metrics_reason"] == "NO_MATURED_OUTCOME_EVIDENCE"
        assert payload["page"]["rows"] == []


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_a_truncated_ledger_tail_does_not_hide_the_readable_rows(tmp_path):
    """AC-001: a partially written row is dropped, not fatal, and not silent."""
    path = tmp_path / "opip_ai_router_usage.jsonl"
    _write_jsonl(path, [_usage("OK")], trailing=b'{"timestamp_utc":"2026-11-08T09:01:00')

    payload = direct.ai(_ledger_settings(path))

    assert payload["status"] == "MEASURED"
    assert payload["summary"]["inferences"] == 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_data_coverage_does_not_assert_a_zero_quarantine(tmp_path):
    """AC-003: projection-only facts are unknown without the projection, not zero."""
    settings = SimpleNamespace(
        opip_analytics_scan_state_dir=str(tmp_path / "scan"),
        opip_analytics_scoring_comparison_path=str(tmp_path / "cmp.jsonl"),
    )

    payload = direct.data_coverage(settings)

    assert payload["quarantine_backlog"] is None
    assert payload["aging"] is None
    assert payload["schema_version"] is None
    assert payload["projection_reason_code"] == "ANALYTICS_PROJECTION_NOT_READABLE"
    assert payload["source"] == "CANONICAL_DIRECT"


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_scan_summary_read_is_bounded_and_still_returns_the_newest_row(tmp_path, monkeypatch):
    """AC-004: the zero-trade read model reads a bounded tail, not the whole stream."""
    path = tmp_path / "scan_summaries.jsonl"
    rows = [
        {"scan_id": f"SCAN:{index}", "decision_at_utc": "2026-11-08T09:00:00+00:00"}
        for index in range(400)
    ]
    _write_jsonl(path, rows)

    seen: list[int] = []
    original = store.read_tail_lines

    def spy(target, max_lines, **kwargs):
        seen.append(int(max_lines))
        return original(target, max_lines, **kwargs)

    monkeypatch.setattr(store, "read_tail_lines", spy)

    assert store.read_latest_scan_summary(path=path)["scan_id"] == "SCAN:399"
    assert seen == [store.READ_TAIL_SUMMARY_LINES]
    assert store.READ_TAIL_SUMMARY_LINES < len(rows)


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_funnel_rows_are_read_from_a_bounded_tail_for_the_requested_scan(tmp_path, monkeypatch):
    """AC-004: the explanation still returns the recorded rows of its own scan."""
    path = tmp_path / "funnel_events.jsonl"
    _write_jsonl(
        path,
        [
            {"scan_id": "SCAN:OLD", "symbol": "BTC/USD", "stage": "ADMITTED"},
            {"scan_id": "SCAN:NEW", "symbol": "BTC/USD", "stage": "ADMITTED"},
            {"scan_id": "SCAN:NEW", "symbol": "BTC/USD", "stage": "POLICY_REJECTED"},
        ],
    )

    seen: list[int] = []
    original = store.read_tail_lines

    def spy(target, max_lines, **kwargs):
        seen.append(int(max_lines))
        return original(target, max_lines, **kwargs)

    monkeypatch.setattr(store, "read_tail_lines", spy)

    rows = store.read_funnel_events_for_scan("SCAN:NEW", path=path)

    assert [row["stage"] for row in rows] == ["ADMITTED", "POLICY_REJECTED"]
    assert seen == [store.READ_TAIL_FUNNEL_LINES]
    assert store.read_funnel_events_for_scan("", path=path) == []


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_a_truncated_funnel_tail_row_is_skipped_rather_than_fatal(tmp_path):
    """AC-004: fail-soft behaviour is preserved by the bounded reader."""
    path = tmp_path / "funnel_events.jsonl"
    _write_jsonl(
        path,
        [{"scan_id": "SCAN:NEW", "symbol": "ETH/USD", "stage": "ADMITTED"}],
        trailing=b'{"scan_id":"SCAN:NEW","symbol":',
    )

    rows = store.read_funnel_events_for_scan("SCAN:NEW", path=path)

    assert [row["symbol"] for row in rows] == ["ETH/USD"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_scoring_authority_is_reconciled_without_activating_anything():
    """AC-005: the reference document and the deployed selector are reported apart."""
    drift = config_staging.scoring_authority_reconciliation(
        {
            "scoring.production_declared": {
                "value": "v1",
                "source": "app/core/strategy_policy.json",
            },
            "scoring.shadow_declared": {"value": "v1"},
            "scoring.target_production_declared": {"value": "v2"},
            "v2.production_selector": {
                "value": "v2",
                "source": "ENV:OPIP_PRODUCTION_SCORING_VERSION",
            },
        }
    )
    assert drift["state"] == "REFERENCE_DRIFT"
    assert drift["declared"] == "v1"
    assert drift["selector"] == "v2"
    assert drift["mutates_runtime"] is False
    assert "production=v1" in drift["detail"]

    match = config_staging.scoring_authority_reconciliation(
        {
            "scoring.production_declared": {"value": "v2"},
            "v2.production_selector": {"value": "v2"},
        }
    )
    assert match["state"] == "MATCH"
    assert match["mutates_runtime"] is False

    unverified = config_staging.scoring_authority_reconciliation(
        {"scoring.production_declared": {"value": "v1"}, "v2.production_selector": None}
    )
    # An unset selector is not read as agreement.
    assert unverified["state"] == "UNVERIFIED"
    assert unverified["mutates_runtime"] is False


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_config_overview_reports_the_runtime_selector_and_keeps_activation_closed(
    tmp_path, monkeypatch
):
    """AC-005: the console can see the drift, and still cannot act on it."""
    from app.core.config import Settings

    monkeypatch.setenv("OPIP_PRODUCTION_SCORING_VERSION", "v2")
    settings = Settings(
        webhook_secret="test-operator-credential",  # gitleaks:allow - test fixture
        app_env="test",
        opip_analytics_db_path=str(tmp_path / "opip_analytics.db"),
        opip_analytics_scan_state_dir=str(tmp_path / "scan"),
        opip_analytics_scoring_comparison_path=str(tmp_path / "cmp.jsonl"),
    )

    payload = config_staging.overview(None, settings)

    reconciliation = payload["authority_reconciliation"]
    assert reconciliation["selector"] == "v2"
    assert reconciliation["declared"] == "v1"
    assert reconciliation["state"] == "REFERENCE_DRIFT"
    assert reconciliation["mutates_runtime"] is False
    assert payload["activation"]["console_can_activate"] is False
    assert payload["effective"]["values"]["v2.production_selector"]["value"] == "v2"


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_the_console_classifies_a_timed_out_read_instead_of_leaking_an_abort():
    """AC-002: the browser's abort text is never presented as a system state."""
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "export function readErrorState" in api
    assert "READ_TIMEOUT_CODE" in api
    assert "READ TIMEOUT after" in api
    # The timeout is classified by our own budget, and a caller-cancelled read
    # keeps its AbortError identity so a superseded read is never reported.
    assert "timedOut" in api and "failure.name='TimeoutError'" in api
    assert "readErrorState(result.reason)" in view
    assert "readErrorState(err)" in view


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_the_console_derives_f11_and_qualification_instead_of_hardcoding_them():
    """AC-006: a displayed safety state comes from recorded evidence."""
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "f11Display(records.health?.runtime?.protection)" in view
    # The Overview KPI must not carry a hard-coded safety label of its own.
    assert "['F11 verification','NOT ATTESTED']" not in view
    assert "'Qualified signals', 'UNAVAILABLE'" not in api
    assert "isNumber(kpi.qualified)" in api




