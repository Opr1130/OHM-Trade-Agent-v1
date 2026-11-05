"""ATDD — Control Center V2 production acceptance repair.

ATDD-OPIP-CONTROL-CENTER-V2-AC01. Two production defects reported after release
``08af854636b0a4714e25fd564a3cdd3024420b6d``:

* the approved brand asset never reached the edge image, because the Vite build
  stage did not receive ``control-center-v2/public``;
* the operator console showed little or no data, because the analytical projection
  is not populated (its maintenance worker is not scheduled) and nothing read the
  canonical evidence that does exist.

These tests prove the minimal repair: the asset is shipped, and an explicitly
labelled read-through answers with real evidence when the projection cannot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.opip.analytics import direct

INCREMENT = "ATDD-OPIP-CONTROL-CENTER-V2-AC01"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
APP_ROOT = Path(__file__).resolve().parents[1]
UI = APP_ROOT / "control-center-v2"
EDGE_DOCKERFILE = APP_ROOT / "deploy" / "control-center" / "Dockerfile.edge"
LOGO = UI / "public" / "assets" / "opip-control-center-logo-transparent.png"


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "webhook_secret": "test-operator-credential",  # gitleaks:allow - test fixture
        "app_env": "test",
        "opip_analytics_db_path": str(tmp_path / "absent_analytics.db"),
        "opip_analytics_scan_state_dir": str(tmp_path / "scan"),
        "opip_analytics_scoring_comparison_path": str(tmp_path / "cmp.jsonl"),
    }
    values.update(overrides)
    return Settings(**values)


def _tree(settings: Settings) -> dict[str, object]:
    """A realistic canonical tree: attempt ledger, comparison ledger, one batch."""
    root = Path(settings.opip_analytics_scan_state_dir)
    staging = root / "staging" / "SCAN-20261104T120000Z"
    staging.mkdir(parents=True, exist_ok=True)
    rows = [
        {"symbol": "BTC/USD", "technical_score": 84, "trade_direction": "LONG",
         "movement_timeframe": "1H", "v2_long_score": 91.5, "v2_short_score": 12.0,
         "v2_feature_status": "READY", "v2_profile_version": "TECHNICAL_SCORE_V2_1",
         "v2_profile_digest": "a" * 64,
         "v2_long_components": {"rsi": 15.0, "relative_volume": 18.0}},
        {"symbol": "ETH/USD", "technical_score": 71, "trade_direction": "SHORT",
         "v2_long_score": None, "v2_short_score": None,
         "v2_feature_status": "INSUFFICIENT_FEATURES"},
    ]
    snapshots = staging / "snapshots.jsonl"
    snapshots.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    digest = hashlib.sha256(snapshots.read_bytes()).hexdigest()
    (staging / "manifest.json").write_text(
        json.dumps({
            "attempt_id": "SCAN:20261104T120000Z",
            "started_at": "2026-11-04T12:00:00+00:00",
            "snapshots": {"file": "snapshots.jsonl", "rows": len(rows), "sha256": digest},
        }),
        encoding="utf-8",
    )
    (staging / "worker_status.json").write_text(
        json.dumps({
            "attempt_id": "SCAN:20261104T120000Z",
            "state": "COMPLETED",
            "finished_at": "2026-11-04T12:02:00+00:00",
            "scoring": {"production_scoring_version": "v2", "v2_evaluation_enabled": True,
                        "v2_fields_in_snapshot_schema": True,
                        "v2_status_counts": {"READY": 1, "INSUFFICIENT_FEATURES": 1}},
        }),
        encoding="utf-8",
    )
    (root / "scan_attempts.jsonl").write_text(
        json.dumps({"seq": 1, "event": "CREATE", "attempt_id": "SCAN:20261104T120000Z",
                    "state": "RESERVED", "at": "2026-11-04T12:00:00+00:00"}) + "\n"
        + json.dumps({"seq": 2, "event": "TERMINALIZE", "attempt_id": "SCAN:20261104T120000Z",
                      "state": "TERMINAL", "outcome": "COMPLETED",
                      "at": "2026-11-04T12:02:30+00:00"}) + "\n",
        encoding="utf-8",
    )
    Path(settings.opip_analytics_scoring_comparison_path).write_text(
        json.dumps({
            "schema_version": 1, "scan_id": "SCAN:20261104T120000Z",
            "observed_at_utc": "2026-11-04T12:02:00+00:00",
            "model_authority": "V2_PRODUCTION_V1_OBSERVATION",
            "v1_threshold": 80, "v2_threshold": 75, "scanned": 2, "v2_ready": 1,
            "v1_top": [{"symbol": "BTC/USD", "direction": "LONG", "score": 84}],
            "v2_top": [{"symbol": "BTC/USD", "direction": "LONG", "score": 91.5}],
            "v1_only": [], "v2_only": [], "outcome_status": "NOT_MATURED",
        }) + "\n",
        encoding="utf-8",
    )
    return {"root": root, "staging": staging, "snapshots": snapshots}


def _snapshot_of_tree(root: Path) -> dict[str, tuple[int, float]]:
    state: dict[str, tuple[int, float]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            state[str(path)] = (stat.st_size, stat.st_mtime)
    return state


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_edge_image_ships_the_approved_public_asset():
    """ATDD-OPIP-CONTROL-CENTER-V2-AC01/AC-001: public/ reaches the Vite build stage."""
    dockerfile = EDGE_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY control-center-v2/public ./public" in dockerfile
    # The copy must happen before the build, or Vite has nothing to emit.
    assert dockerfile.index("COPY control-center-v2/public ./public") < dockerfile.index(
        "RUN npm run build"
    )
    assert "COPY control-center-v2/src ./src" in dockerfile
    # The approved artwork itself is present and is the reviewed file.
    assert LOGO.is_file()
    raw = LOGO.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert hashlib.sha256(raw).hexdigest() == (
        "08db86b072d04ab9d966820d429309570084461c85af27a2b96bc46c86ec6595"
    )
    # The served URL is built from the dashboard base, not from the site root.
    fmt = (UI / "src" / "format.mjs").read_text(encoding="utf-8")
    assert "import.meta.env.BASE_URL" in fmt


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_read_through_answers_with_real_evidence_when_the_projection_is_absent(tmp_path):
    """ATDD-OPIP-CONTROL-CENTER-V2-AC01/AC-002: bounded canonical reads, clearly labelled."""
    settings = _settings(tmp_path)
    _tree(settings)
    assert not Path(settings.opip_analytics_db_path).exists()

    signals = direct.signals(settings, limit=10)
    assert signals["status"] == "MEASURED"
    assert signals["source"] == "CANONICAL_DIRECT"
    assert signals["verification"] == "UNPROJECTED"
    assert {row["symbol"] for row in signals["rows"]} == {"BTC/USD", "ETH/USD"}
    by_symbol = {row["symbol"]: row for row in signals["rows"]}
    # A recorded score is classified; an uncomputed score stays null, never zero.
    assert by_symbol["BTC/USD"]["divergence"] in {"BOTH", "V1_ONLY", "V2_ONLY", "NEITHER"}
    assert by_symbol["ETH/USD"]["v2_long_score"] is None
    assert by_symbol["ETH/USD"]["v2_best_score"] is None
    assert by_symbol["ETH/USD"]["divergence"] == "INSUFFICIENT_FEATURES"
    # A direct read is explicitly unverified - it is not a reconciled KPI.
    assert all(row["is_verified"] == 0 for row in signals["rows"])

    health = direct.health_block(settings)
    assert health["kpi"]["status"] == "MEASURED"
    assert health["kpi"]["symbols_scanned"] == 2
    assert health["kpi"]["v2_directional"] == 1
    assert health["kpi"]["qualified"] is None
    assert health["kpi"]["qualification_reason"] == "NO_QUALIFICATION_EVENT_SOURCE"
    assert health["reconciliation"]["state"] == "UNVERIFIED"
    assert health["comparison"]["scan_id"] == "SCAN:20261104T120000Z"

    funnel = direct.funnel(settings)
    assert funnel["status"] == "MEASURED"
    attempt = funnel["attempts"][0]
    assert attempt["attempt_id"] == "SCAN:20261104T120000Z"
    assert attempt["acquisition_outcome"] == "COMPLETED"
    assert attempt["snapshots"] == 2
    assert attempt["v2_status"]["READY"] == 1
    assert attempt["qualified"] is None
    assert attempt["ai_review"] is None

    coverage = direct.data_coverage(settings)
    present = {item["source_id"] for item in coverage["sources"] if item["exists"]}
    assert "scan_attempt_ledger" in present
    assert "scoring_comparison_ledger" in present
    # The direct layer does not claim a projection state; the API wrapper stamps it.
    assert "projection" not in coverage

    assert direct.ai(settings)["status"] == "NO_EVIDENCE"



@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_api_serves_canonical_evidence_when_the_projection_is_missing(tmp_path, monkeypatch):
    """ATDD-OPIP-CONTROL-CENTER-V2-AC01/AC-002: the route falls back and says so."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import control_center as control_center_api

    settings = _settings(tmp_path)
    _tree(settings)
    monkeypatch.setattr(control_center_api, "get_settings", lambda: settings)
    monkeypatch.setattr(control_center_api, "_require_secret", lambda value: None)
    app = FastAPI()
    app.include_router(control_center_api.router)
    client = TestClient(app)

    health = client.get("/api/opip/control-center/health").json()
    assert health["projection"]["status"] == "MEASURED"
    assert health["projection"]["source"] == "CANONICAL_DIRECT"
    assert health["projection"]["projection_fallback"] is True
    assert health["projection"]["kpi"]["symbols_scanned"] == 2
    assert health["runtime"]["trading_authority"]["funded_orders"] is False

    signals = client.get("/api/opip/control-center/signals?limit=5").json()
    assert signals["status"] == "MEASURED"
    assert signals["source"] == "CANONICAL_DIRECT"
    assert signals["projection"]["status"] == "UNAVAILABLE"
    assert len(signals["rows"]) == 2

    filtered = client.get(
        "/api/opip/control-center/signals?v2_status=INSUFFICIENT_FEATURES"
    ).json()
    assert [row["symbol"] for row in filtered["rows"]] == ["ETH/USD"]

    scanner = client.get("/api/opip/control-center/scanner").json()
    assert scanner["status"] == "MEASURED"
    assert scanner["attempts"][0]["attempt_id"] == "SCAN:20261104T120000Z"

    data = client.get("/api/opip/control-center/data").json()
    assert data["status"] == "MEASURED"
    assert data["projection"]["status"] == "UNAVAILABLE"
    assert data["sources_present"] >= 2

    forensics = client.get(
        "/api/opip/control-center/forensics"
        "?attempt_id=SCAN:20261104T120000Z&symbol=BTC%2FUSD"
    ).json()
    assert forensics["status"] == "MEASURED"
    assert forensics["signal"]["symbol"] == "BTC/USD"
    assert forensics["source"] == "CANONICAL_DIRECT"

    ai = client.get("/api/opip/control-center/ai").json()
    assert ai["status"] == "NO_EVIDENCE"
    assert ai["reason_code"] == "NO_AI_INFERENCE_SOURCE"


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_zero_buy_surface_explains_from_recorded_qualification_evidence():
    """ATDD-OPIP-CONTROL-CENTER-V2-AC01/AC-003: explanation is recorded, never invented."""
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    # The explanation is assembled from the recorded payload only.
    assert "export function zeroBuySummary" in api
    assert "dominantGate" in api and "deepestStage" in api
    assert "export function provenanceLabel" in api
    assert "CANONICAL (DIRECT)" in api
    # The console renders that explanation instead of a raw dump.
    assert "<ZeroBuyCard zero={records.zero}/>" in view
    assert "Why zero BUY signals" in view
    assert "<ProvenanceNote payload={records.health?.projection}/>" in view
    assert "<AuthPanel/>" in view
    # No recommendation, score or alert is synthesised anywhere in the new layer.
    module = (APP_ROOT / "app" / "opip" / "analytics" / "direct.py").read_text(
        encoding="utf-8"
    )
    assert "NO_QUALIFICATION_EVENT_SOURCE" in module
    assert "NO_PROMOTION_EVENT_SOURCE" in module
    for forbidden in ('"BUY"', "'BUY'", "BUY_SIGNAL", "recommendation", "alert_level"):
        assert forbidden not in module


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_read_through_is_read_only_and_preserves_authority(tmp_path):
    """ATDD-OPIP-CONTROL-CENTER-V2-AC01/AC-004: no writes, no projection, no authority."""
    settings = _settings(tmp_path)
    paths = _tree(settings)
    root = paths["root"]
    before = _snapshot_of_tree(root)  # type: ignore[arg-type]

    direct.comparisons(settings)
    direct.attempt_events(settings)
    direct.signals(settings)
    direct.funnel(settings)
    direct.data_coverage(settings)
    direct.health_block(settings)
    direct.ai(settings)
    direct.forensics(settings, attempt_id="SCAN:20261104T120000Z", symbol="BTC/USD")

    # Canonical evidence is byte- and mtime-identical after every read.
    assert _snapshot_of_tree(root) == before  # type: ignore[arg-type]
    # No projection is created or mutated by a read path.
    assert not Path(settings.opip_analytics_db_path).exists()

    module = (APP_ROOT / "app" / "opip" / "analytics" / "direct.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("conn.execute", "write_transaction", "connect_writer", "os.replace", "unlink"):
        assert forbidden not in module

