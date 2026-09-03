from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.opip.data_platform.archive_lifecycle import assess_segment, discover_segments


ROOT = Path(__file__).resolve().parents[1]


def test_analytics_compose_adds_private_grafana_with_persistence_and_bounded_logs():
    compose = (ROOT / "deploy" / "analytics" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "opip-grafana:" in compose
    assert "grafana/grafana:11.2.2" in compose
    assert "${OPIP_GRAFANA_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "/var/lib/opip-data-platform/grafana:/var/lib/grafana" in compose
    assert "GF_AUTH_ANONYMOUS_ENABLED: \"false\"" in compose
    assert "OPIP_GRAFANA_DB_USER: ${OPIP_GRAFANA_DB_USER:-opip_dashboard}" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "max-size: 10m" in compose and "max-file: \"5\"" in compose


def test_grafana_provisioning_is_code_driven_and_read_only_datasource():
    datasource = (
        ROOT
        / "deploy"
        / "grafana"
        / "provisioning"
        / "datasources"
        / "opip-postgres.yml"
    ).read_text(encoding="utf-8")
    dashboards_provider = (
        ROOT
        / "deploy"
        / "grafana"
        / "provisioning"
        / "dashboards"
        / "opip-dashboards.yml"
    ).read_text(encoding="utf-8")

    assert "uid: opip-analytics-postgres" in datasource
    assert "user: ${OPIP_GRAFANA_DB_USER:-opip_dashboard}" in datasource
    assert "password: ${OPIP_GRAFANA_DB_PASSWORD}" in datasource
    assert "timescaledb: false" in datasource
    assert "folder: O'Pip Intelligence" in dashboards_provider


def test_intelligence_cockpit_dashboard_contains_required_sections_and_variables():
    payload = json.loads(
        (
            ROOT
            / "deploy"
            / "grafana"
            / "dashboards"
            / "opip-intelligence-cockpit-v1.json"
        ).read_text(encoding="utf-8")
    )
    titles = {panel["title"] for panel in payload["panels"]}
    for required in (
        "Freshness Status",
        "System Pulse (freshness and backlog)",
        "Qualification Funnel (24h)",
        "Current Chokes by Outcome (7d)",
        "Opportunity Accountability Trend",
        "Paper Trading Performance (30d)",
        "Signal Quality Evidence (recent 200)",
        "Learning Throughput (30d)",
        "Failure Eradication Signals (30d)",
        "Latency (decision trend + current ingest lag)",
        "Per-Asset / Pair Drilldown",
    ):
        assert required in titles

    variable_names = {item["name"] for item in payload["templating"]["list"]}
    for variable in (
        "symbol",
        "strategy",
        "scanner",
        "direction",
        "gate_reason",
        "model_version",
        "config_version",
    ):
        assert variable in variable_names

    freshness_sql = next(
        panel["targets"][0]["rawSql"]
        for panel in payload["panels"]
        if panel["title"] == "Freshness Status"
    )
    assert "shipper_lag_seconds" in freshness_sql
    assert "db_lag_seconds" in freshness_sql
    assert "mv_age_seconds" in freshness_sql


def test_archive_lifecycle_is_fail_closed_for_cold_segment_until_all_evidence_exists(tmp_path):
    segment = tmp_path / "screening-20250101.jsonl.gz"
    segment.write_bytes(b"payload")
    old = 1700000000
    segment.touch()
    # force COLD age
    import os

    os.utime(segment, (old, old))

    checksum = hashlib.sha256(segment.read_bytes()).hexdigest()
    segment.with_suffix(segment.suffix + ".sha256").write_text(f"{checksum}  {segment.name}\n", encoding="utf-8")
    (tmp_path / "manifest.env").write_text(f"segment={segment.name}\n", encoding="utf-8")

    partial = assess_segment(segment)
    assert partial.tier == "COLD"
    assert partial.cleanup_eligible is False
    assert "segment_not_finalized" in partial.blockers
    assert "archive_not_verified" in partial.blockers
    assert "offhost_backup_not_verified" in partial.blockers

    segment.with_suffix(segment.suffix + ".finalized").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".archive.verified").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".offhost.verified").write_text("ok\n", encoding="utf-8")

    complete = assess_segment(segment)
    assert complete.cleanup_eligible is True
    assert complete.blockers == []


def test_archive_lifecycle_manifest_requires_exact_segment_name(tmp_path):
    segment = tmp_path / "screening-20250101.jsonl.gz"
    segment.write_bytes(b"payload")
    import os

    old = 1700000000
    os.utime(segment, (old, old))
    checksum = hashlib.sha256(segment.read_bytes()).hexdigest()
    segment.with_suffix(segment.suffix + ".sha256").write_text(
        f"{checksum}  {segment.name}\n",
        encoding="utf-8",
    )
    segment.with_suffix(segment.suffix + ".finalized").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".archive.verified").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".offhost.verified").write_text("ok\n", encoding="utf-8")
    (tmp_path / "manifest.env").write_text(
        "segment=screening-20250101.jsonl.gz.extra\n",
        encoding="utf-8",
    )

    assessed = assess_segment(segment)
    assert assessed.manifest_recorded is False
    assert "manifest_not_updated" in assessed.blockers


def test_archive_lifecycle_hot_and_warm_tiers_keep_expected_compression_rules(tmp_path):
    hot = tmp_path / "hot-segment.jsonl"
    hot.write_bytes(b"hot")
    warm = tmp_path / "warm-segment.jsonl"
    warm.write_bytes(b"warm")

    import os
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    os.utime(hot, (now.timestamp(), now.timestamp()))
    warm_age = now.timestamp() - 20 * 86400
    os.utime(warm, (warm_age, warm_age))

    hot_assessed = assess_segment(hot, require_offhost=False)
    assert hot_assessed.tier == "HOT"
    assert hot_assessed.blockers == []

    warm_assessed = assess_segment(warm, require_offhost=False)
    assert warm_assessed.tier == "WARM"
    assert "segment_not_finalized" in warm_assessed.blockers
    assert "checksum_missing_or_mismatch" in warm_assessed.blockers
    assert "manifest_not_updated" in warm_assessed.blockers
    assert "archive_not_verified" in warm_assessed.blockers
    assert "warm_cold_segment_must_be_compressed" in warm_assessed.blockers


def test_archive_lifecycle_discovery_ignores_sidecar_markers(tmp_path):
    segment = tmp_path / "segment-1.jsonl.gz"
    segment.write_bytes(b"payload")
    (tmp_path / "segment-1.jsonl.gz.sha256").write_text("x", encoding="utf-8")
    (tmp_path / "segment-1.jsonl.gz.finalized").write_text("x", encoding="utf-8")
    (tmp_path / "segment-1.jsonl.gz.archive.verified").write_text("x", encoding="utf-8")
    (tmp_path / "segment-1.jsonl.gz.offhost.verified").write_text("x", encoding="utf-8")
    assert discover_segments(tmp_path) == [segment]
