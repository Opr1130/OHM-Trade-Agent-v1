from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path

from app.opip.data_platform.archive_lifecycle import assess_segment


ROOT = Path(__file__).resolve().parents[1]


def _make_cold(path: Path) -> datetime:
    now = datetime.now(timezone.utc)
    old = now.timestamp() - 100 * 86400
    os.utime(path, (old, old))
    return now


def _write_complete_sidecars(segment: Path) -> None:
    checksum = hashlib.sha256(segment.read_bytes()).hexdigest()
    segment.with_suffix(segment.suffix + ".sha256").write_text(
        f"{checksum}  {segment.name}\n", encoding="utf-8"
    )
    segment.with_suffix(segment.suffix + ".finalized").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".archive.verified").write_text("ok\n", encoding="utf-8")
    segment.with_suffix(segment.suffix + ".offhost.verified").write_text("ok\n", encoding="utf-8")
    (segment.parent / "manifest.env").write_text(
        f"segment={segment.name}\n", encoding="utf-8"
    )


def test_empty_compressed_cold_segment_is_never_cleanup_eligible(tmp_path: Path):
    segment = tmp_path / "empty.jsonl.gz"
    segment.write_bytes(b"")
    now = _make_cold(segment)
    _write_complete_sidecars(segment)

    assessed = assess_segment(segment, now=now)

    assert assessed.tier == "COLD"
    assert assessed.compression == "invalid"
    assert assessed.checksum_verified is True
    assert assessed.cleanup_eligible is False
    assert "warm_cold_segment_must_be_compressed" in assessed.blockers


def test_invalid_utf8_evidence_fails_closed_without_aborting(tmp_path: Path):
    segment = tmp_path / "segment.jsonl.gz"
    segment.write_bytes(gzip.compress(b'{"ok":true}\n'))
    now = _make_cold(segment)
    _write_complete_sidecars(segment)

    segment.with_suffix(segment.suffix + ".sha256").write_bytes(b"\xff\xfe")
    (tmp_path / "manifest.env").write_bytes(b"\xff\xfe")

    assessed = assess_segment(segment, now=now)

    assert assessed.checksum_verified is False
    assert assessed.manifest_recorded is False
    assert assessed.cleanup_eligible is False
    assert "checksum_missing_or_mismatch" in assessed.blockers
    assert "manifest_not_updated" in assessed.blockers


def test_segment_read_oserror_becomes_checksum_failure(tmp_path: Path, monkeypatch):
    segment = tmp_path / "segment.jsonl.gz"
    segment.write_bytes(gzip.compress(b'{"ok":true}\n'))
    now = _make_cold(segment)
    _write_complete_sidecars(segment)

    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == segment and "b" in mode:
            raise OSError("simulated evidence read failure")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assessed = assess_segment(segment, now=now)

    assert assessed.compression == "gzip"
    assert assessed.checksum_verified is False
    assert assessed.cleanup_eligible is False
    assert "checksum_missing_or_mismatch" in assessed.blockers


def test_analytics_clients_require_verified_tls_and_mount_ca():
    env_example = (ROOT / "deploy" / "analytics" / "env.example").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "analytics" / "docker-compose.yml").read_text(encoding="utf-8")
    bootstrap = (
        ROOT / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")

    tls_query = "sslmode=verify-full&sslrootcert=/etc/opip-data-platform/tls/postgres-ca.crt"
    assert 'OPIP_ANALYTICS_ADMIN_DATABASE_URL="postgresql://' in env_example
    assert 'OPIP_ANALYTICS_DATABASE_URL="postgresql://' in env_example
    assert env_example.count(tls_query) == 2
    assert "# Keep the DSNs double-quoted because this file is sourced by the bootstrap" in env_example

    ca_mount = (
        "/etc/opip-data-platform/tls/postgres-ca.crt:"
        "/etc/opip-data-platform/tls/postgres-ca.crt:ro"
    )
    shipper_block = compose.split("  opip-shipper:\n", 1)[1].split("\n  opip-data-admin:\n", 1)[0]
    admin_block = compose.split("  opip-data-admin:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert ca_mount in shipper_block
    assert ca_mount in admin_block

    assert "hostssl all all 172.29.0.0/24 scram-sha-256" in bootstrap
    assert "host all all 172.29.0.0/24 scram-sha-256" not in bootstrap
    assert "hostssl all opip_dashboard $OPIP_PRODUCTION_PRIVATE_CIDR scram-sha-256" in bootstrap
    assert "require_analytics_verify_full_dsn OPIP_ANALYTICS_ADMIN_DATABASE_URL" in bootstrap
    assert "require_analytics_verify_full_dsn OPIP_ANALYTICS_DATABASE_URL" in bootstrap


def test_deployment_markers_select_coherent_latest_rows():
    dashboard = json.loads(
        (
            ROOT
            / "deploy"
            / "grafana"
            / "dashboards"
            / "opip-intelligence-cockpit-v1.json"
        ).read_text(encoding="utf-8")
    )
    panel = next(panel for panel in dashboard["panels"] if panel["id"] == 3)
    sql = panel["targets"][0]["rawSql"]

    assert "WITH latest_marker AS" in sql
    assert "ORDER BY observed_at DESC, ingested_at DESC" in sql
    assert "observed_at > now() - interval '30 days'" in sql
    assert "latest_schema AS" in sql
    assert "ORDER BY version DESC" in sql
    assert "schema_row.applied_at AS schema_applied_at" in sql
    assert "marker.observed_at" in sql
    assert "max(nullif(payload->>" not in sql
    assert "max(sha256)" not in sql
    assert "SELECT now() AS observed_at" not in sql
