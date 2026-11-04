"""Round-2 focused regressions: V2 evidence round-trip and fail-closed promotion.

Every assertion here exists because production ran for eleven comparison
records with a shortlist of zero: the isolated worker serialised snapshots that
carried no V2 evidence, the core ran with V2 production authority and selected
nothing, and the result was indistinguishable from "the market produced no
opportunities". They pin the two halves of the repair for ATDD-SCANNER-V2-EVIDENCE-M01:

* a worker reports which of the two causes it hit (image too old, or attempt
  without the scoring authority), and
* a V2-authority core refuses acquisition evidence that carries no V2 scoring,
  with an explicit durable reason, instead of quietly selecting nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import scan_attempt as sa
from app.services import scan_manifest as sm
from app.services import scan_result_promotion as promo

INCREMENT = "ATDD-SCANNER-V2-EVIDENCE-M01"
APP_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _real_now():
    """Manifests are written with a real clock; promotion must match it."""
    return datetime.now(timezone.utc)


def _settings(tmp_path: Path, **overrides):
    base = {
        "opip_scan_state_dir": str(tmp_path / "scan"),
        "opip_scan_execution_mode": "isolated",
        "opip_scan_deadline_seconds": 300,
        "signal_quality_scan_interval_seconds": 600,
        "opip_production_scoring_version": "v2",
        "opip_score_v2_evaluation_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(**overrides):
    from app.scanner.models import MarketSnapshot

    base = dict(
        symbol="BTCUSD",
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
        technical_score=85,
        trend="UP",
        v2_feature_status="READY",
        v2_long_score=88.0,
        v2_short_score=12.0,
        v2_profile_digest="digest-1",
        v2_long_components={"trend": 1.0},
        v2_short_components={"trend": 0.0},
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def _publish(settings, attempt_id: str, *, snapshots):
    from app.services.scan_supervisor import attempt_staging_dir

    directory = attempt_staging_dir(settings, attempt_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "snapshots.jsonl").write_bytes(
        b"".join(
            json.dumps(asdict(row), sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
            for row in snapshots
        )
    )
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id=attempt_id, deadline_seconds=300, now=NOW)
    attempt = store.terminalize(attempt_id, sa.OUTCOME_COMPLETED, None, now=NOW)
    sm.write_manifest(
        directory,
        {
            "attempt_id": attempt_id,
            "fence": attempt.fence,
            "coverage": {
                "requested": len(snapshots),
                "analyzed": len(snapshots),
                "failed": 0,
                "skipped": 0,
            },
            "snapshots": {
                "file": "snapshots.jsonl",
                "rows": len(snapshots),
                "sha256": sm.sha256_path(directory / "snapshots.jsonl"),
            },
        },
        complete=True,
        row_count=len(snapshots),
    )
    return attempt


def _promote(settings, snapshots):
    attempt = _publish(settings, "SCAN:V2", snapshots=snapshots)
    return promo.load_promoted(settings=settings, attempt=attempt, now=_real_now())


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_v2_authority_rejects_a_population_with_no_v2_evidence(tmp_path: Path):
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-002: No silent V1 fall-back.

    A population whose every row is ``NOT_COMPUTED`` is an acquisition pipeline
    defect, not a quiet market. It must be rejected with its own reason code so
    the recorded disposition says exactly that.
    """
    settings = _settings(tmp_path)
    blank = dict(
        v2_feature_status="NOT_COMPUTED",
        v2_long_score=None,
        v2_short_score=None,
        v2_long_components=None,
        v2_short_components=None,
        v2_profile_digest=None,
    )
    snapshots = [_snapshot(symbol="BTCUSD", **blank), _snapshot(symbol="ETHUSD", **blank)]

    with pytest.raises(promo.PromotionRejected) as excinfo:
        _promote(settings, snapshots)

    assert excinfo.value.reason == promo.REASON_V2_EVIDENCE_ABSENT


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_v2_authority_rejects_a_population_that_errored_everywhere(tmp_path: Path):
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-002: V2 was attempted and raised for every symbol: also a defect, not a market."""
    settings = _settings(tmp_path)
    blank = dict(
        v2_long_score=None,
        v2_short_score=None,
        v2_long_components=None,
        v2_short_components=None,
        v2_profile_digest=None,
    )
    snapshots = [
        _snapshot(symbol="BTCUSD", v2_feature_status="ERROR:FileNotFoundError", **blank),
        _snapshot(symbol="ETHUSD", v2_feature_status="ERROR:KeyError", **blank),
    ]

    with pytest.raises(promo.PromotionRejected) as excinfo:
        _promote(settings, snapshots)

    assert excinfo.value.reason == promo.REASON_V2_EVIDENCE_ERROR


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_v2_authority_accepts_attempted_but_insufficient_evidence(tmp_path: Path):
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-003: Insufficient inputs are a real market outcome, not a pipeline defect.

    The population is consumable, the selector still refuses every row, and no
    candidate is invented to hide it.
    """
    from app.scanner.directional_candidates import select_directional_candidates

    settings = _settings(tmp_path)
    snapshots = [
        _snapshot(symbol="BTCUSD", v2_feature_status="INSUFFICIENT_FEATURES",
                  v2_long_score=None, v2_short_score=None, v2_long_components=None,
                  v2_short_components=None, v2_profile_digest=None),
        _snapshot(symbol="ETHUSD", v2_long_score=40.0, v2_short_score=20.0),
    ]

    promoted = _promote(settings, snapshots)

    assert len(promoted.snapshots) == 2
    assert select_directional_candidates(list(promoted.snapshots), scoring_version="v2") == []


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_v2_authority_promotes_a_genuine_v2_score(tmp_path: Path):
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-003: A round-tripped V2 score reaches production selection unchanged."""
    from app.scanner.directional_candidates import select_directional_candidates

    settings = _settings(tmp_path)
    promoted = _promote(settings, [_snapshot(symbol="BTCUSD", v2_long_score=88.0)])

    assert promoted.snapshots[0].v2_feature_status == "READY"
    assert promoted.snapshots[0].v2_long_score == 88.0
    assert promoted.snapshots[0].v2_long_components == {"trend": 1.0}
    selected = select_directional_candidates(list(promoted.snapshots), scoring_version="v2")
    assert [item.symbol for item in selected] == ["BTCUSD"]
    assert selected[0].technical_score == 88


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_v1_authority_is_unchanged_by_the_v2_gate(tmp_path: Path):
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-002: With V1 as production authority the V2 gate must not interfere."""
    settings = _settings(tmp_path, opip_production_scoring_version="v1")
    snapshots = [
        _snapshot(symbol="BTCUSD", v2_feature_status="NOT_COMPUTED",
                  v2_long_score=None, v2_short_score=None, v2_long_components=None,
                  v2_short_components=None, v2_profile_digest=None),
    ]

    promoted = _promote(settings, snapshots)

    assert len(promoted.snapshots) == 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_worker_status_records_which_cause_it_hit():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-004: The image / configuration distinction is recorded with the evidence."""
    from app.jobs import scan_worker

    settings = SimpleNamespace(
        opip_production_scoring_version="v2", opip_score_v2_evaluation_enabled=False
    )
    evidence = scan_worker._v2_evidence(
        settings,
        [
            _snapshot(symbol="BTCUSD"),
            _snapshot(symbol="ETHUSD", v2_feature_status="INSUFFICIENT_FEATURES"),
            _snapshot(symbol="SOLUSD", v2_feature_status="ERROR:ValueError"),
        ],
    )

    assert evidence["production_scoring_version"] == "v2"
    assert evidence["v2_evaluation_enabled"] is False
    assert evidence["v2_fields_in_snapshot_schema"] is True
    assert evidence["v2_status_counts"] == {
        "ERROR": 1,
        "INSUFFICIENT_FEATURES": 1,
        "READY": 1,
    }


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_unconfigured_worker_reports_not_computed_for_every_row():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-004: The exact production signature: settings absent, nothing computed."""
    from app.jobs import scan_worker

    settings = SimpleNamespace()
    blank = dict(
        v2_feature_status="NOT_COMPUTED",
        v2_long_score=None,
        v2_short_score=None,
        v2_long_components=None,
        v2_short_components=None,
        v2_profile_digest=None,
    )
    evidence = scan_worker._v2_evidence(
        settings, [_snapshot(**blank), _snapshot(symbol="ETHUSD", **blank)]
    )

    assert evidence["v2_status_counts"] == {"NOT_COMPUTED": 2}
    assert evidence["production_scoring_version"] == ""
    assert evidence["v2_evaluation_enabled"] is False


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_scanner_service_carries_the_core_scoring_authority():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-004: The worker must be told the same scoring authority the core selects with.

    Container environment is resolved at `docker compose run`, not baked into the
    image, so this is the half of the defect a rebuild alone cannot fix.
    """
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("opip-scan-worker:", 1)[1].split("\nnetworks:", 1)[0]

    assert 'OPIP_PRODUCTION_SCORING_VERSION: "v2"' in block
    # Isolation is unchanged: no env_file, no funded credential.
    assert "env_file" not in block
    assert "KRAKEN" not in block
    assert "read_only: true" in block
    assert "cap_drop:" in block


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_release_controller_builds_proves_and_restores_the_scanner_image():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-001: The governed release owns the scanner image, not a manual build."""
    controller = (APP_ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")

    # Built from the same approved SHA as the core ...
    assert 'SCANNER_SERVICE="opip-scan-worker"' in controller
    assert 'opip-irr-shadow "${scanner_build_targets[@]}"' in controller
    # ... proven before anything is recreated ...
    assert "verify_scanner_image" in controller
    assert "OPIP_SCANNER_IMAGE_ID=" in controller
    assert "OPIP_SCANNER_IMAGE_REVISION=" in controller
    assert "OPIP_SCANNER_IMAGE_SCHEMA=" in controller
    assert "OPIP_SCANNER_IMAGE_ACCEPTANCE=BLOCKED" in controller
    # ... and restored with the release during rollback.
    assert 'scanner_rollback_targets=("$SCANNER_SERVICE")' in controller
    assert "OPIP_ROLLBACK_SCANNER=DECLARED_BY_RESTORED_RELEASE" in controller


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_release_controller_refuses_an_incompatible_scanner_image():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-001: An image that is not this release, or cannot emit V2, blocks acceptance."""
    controller = (APP_ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")

    assert 'SCANNER_IMAGE_PROBE="SCHEMA_V2_READY"' in controller
    assert "V2_EVIDENCE_FIELDS_ABSENT" in controller
    assert "is not the approved SHA $TARGET_SHA" in controller
    assert "isolated scanner image is not proven V2-capable" in controller



# --- Round 3: profile-aware scanner discovery + fail-closed V2 releases ------


def _compose_service_block(service: str) -> str:
    """The named service block exactly as `docker compose config` renders it.

    Compose normalises service keys to two-space indent, which is the shape the
    controller's predicate parses, so the checked-in file is representative.
    """
    lines = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    block: list[str] = []
    inside = False
    for line in lines:
        if line.rstrip() == f"  {service}:":
            inside = True
            continue
        if inside and line.strip() and not line.startswith("   "):
            break
        if inside:
            block.append(line)
    return "\n".join(block)


V2_PREDICATE_ERE = r'OPIP_PRODUCTION_SCORING_VERSION:[[:space:]]*"?v2"?'
#: Same rule as the shell/grep ERE above, expressed for Python's `re`.
V2_PREDICATE = r'OPIP_PRODUCTION_SCORING_VERSION:\s*"?v2"?'


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_scanner_detection_activates_the_scanner_profile():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-001: discovery must see a profile-gated service.

    `docker compose config --services` lists only services whose profiles are
    active, so a scanner gated behind `profiles: [scanner]` was invisible and
    every release reported NOT_DECLARED - which is exactly what production
    printed at 03:17:59 despite the service being declared.
    """
    controller = (APP_ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    scanner_block = _compose_service_block("opip-scan-worker")

    # The service really is profile-gated, which is why the flag is required.
    assert "profiles:" in scanner_block and "- scanner" in scanner_block
    # Forward discovery and rollback discovery both activate the profile.
    assert (
        "docker compose --profile scanner -f docker-compose.yml config --services"
        in controller
    )
    assert (
        'docker compose --profile scanner -f docker-compose.yml -f "$SAFE_BASELINE_OVERRIDE"'
        in controller
    )
    # No un-profiled forward discovery survives.
    assert "docker compose -f docker-compose.yml config --services" not in controller


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_a_v2_release_without_a_declared_scanner_fails_closed():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-001: an unprovable scanner blocks a V2 release."""
    import re

    controller = (APP_ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    core_block = _compose_service_block("ohm-trade-agent")
    v1_block = core_block.replace(
        'OPIP_PRODUCTION_SCORING_VERSION: "v2"', 'OPIP_PRODUCTION_SCORING_VERSION: "v1"'
    )

    # The predicate reads the core service's scoring authority, not a guess.
    assert "release_requires_scanner()" in controller
    assert V2_PREDICATE_ERE in controller
    # The dependency is real: this release's core selects with V2.
    assert re.search(V2_PREDICATE, core_block), "core must select V2"
    # A V1 core does not trip it.
    assert not re.search(V2_PREDICATE, v1_block), "a V1 copy must not require the scanner"

    # An undeclared scanner on a V2 release refuses release acceptance.
    assert "elif release_requires_scanner; then" in controller
    assert "OPIP_SCANNER_IMAGE=UNDECLARED_FOR_V2_RELEASE" in controller
    assert "OPIP_SCANNER_IMAGE_ACCEPTANCE=BLOCKED" in controller
    assert (
        "the core selects with V2 production authority but this release declares no"
        in controller
    )


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_rollback_still_tolerates_a_release_without_the_scanner_service():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-001: older releases keep a safe rollback."""
    controller = (APP_ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    rollback = controller.split("  if docker compose --profile scanner", 1)[1]
    body = rollback.split("  else\n", 1)[0]

    # A restored release that predates the service is recorded, never fatal.
    assert "scanner_rollback_targets=()" in controller
    assert "OPIP_ROLLBACK_SCANNER=ABSENT_FROM_RESTORED_RELEASE" in controller
    assert "OPIP_ROLLBACK_SCANNER=DECLARED_BY_RESTORED_RELEASE" in controller
    # The tolerated branch performs no refusal.
    assert "UNDECLARED_FOR_V2_RELEASE" not in body
    assert "false" not in body
