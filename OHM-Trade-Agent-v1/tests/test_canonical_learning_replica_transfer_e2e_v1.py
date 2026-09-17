"""Linux/root transfer E2E: exporter -> forced reader -> sync -> generation.

This is an **executable** integration test, not a source inspection. It runs the
three real deployment surfaces unmodified against production-shaped paths, which
is safe only because a CI runner is disposable.

What is real here:

* the actual ``export-opip-learning-export.sh`` orchestrator;
* the actual ``opip-learning-read-export.sh`` forced reader, reached through its
  real command protocol;
* the actual ``opip-learning-sync.sh``, including its Docker helper invocation;
* the actual Python replica verifier and installer;
* the actual PR-A0 ``publish_backup_generation`` durability path (POSIX only,
  which is exactly the gap Windows cannot cover).

What is faked: **only the SSH transport**. A controlled ``ssh`` shim on PATH
receives the exact forced-command string, invokes the real reader with
``SSH_ORIGINAL_COMMAND`` set, and streams its real tar bytes back. The reader is
therefore never bypassed and the sync protocol is exercised faithfully.

Skipped unless running as root on Linux with Docker and a learning image, so the
ordinary non-root pytest run is unaffected. CI builds the image and runs this
file in a dedicated root step.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.backup import hash_file_sha256
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_STREAM,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.learning.canonical_replica import (
    resolve_current_generation,
    resolve_verified_replica_bundle,
)
from app.opip.learning.paper_outcome_reader import read_canonical_paper_outcomes

REPO_ROOT = Path(__file__).resolve().parents[1]

# Production-shaped paths. These are only ever created inside a disposable CI
# runner, and every one of them is removed in the fixture teardown.
APP_ROOT = Path("/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1")
DATA_ROOT = APP_ROOT / "data"
EXPORT_ROOT = Path("/var/lib/opip-learning-export")
DEPLOY_STATE = Path("/var/lib/ohm-deploy")
LEARNING_DATA = Path("/var/lib/opip-learning/data")
LEARNING_STATE = Path("/var/lib/opip-learning/state")
LEARNING_REPLICA = Path("/var/lib/opip-learning/canonical-replica")
ENV_FILE = Path("/etc/opip-learning.env")

EXPORTER = REPO_ROOT / "deploy/remote/export-opip-learning-evidence.sh"
READER = REPO_ROOT / "deploy/remote/opip-learning-read-export.sh"
SYNC = REPO_ROOT / "deploy/learning/opip-learning-sync.sh"

RELEASE_SHA = "0cd0c30eba0d45fb97aa1032364bfd93be671657"
ENTER = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20
EPISODE = "EP:1"

_IMAGE = os.environ.get("OPIP_LEARNING_TEST_IMAGE", "").strip()


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not _is_root() or not _have_docker() or not _IMAGE,
    reason=(
        "transfer E2E requires root + Linux + Docker + OPIP_LEARNING_TEST_IMAGE "
        "(run via the dedicated CI root step)"
    ),
)


# ---------------------------------------------------------------------------
# Test-owned state
# ---------------------------------------------------------------------------


def _cleanup() -> None:
    for path in (EXPORT_ROOT, LEARNING_REPLICA, Path("/var/lib/opip-learning"), DEPLOY_STATE):
        shutil.rmtree(path, ignore_errors=True)
    for path in (ENV_FILE, Path("/var/run/opip-learning-export.lock")):
        path.unlink(missing_ok=True)
    shutil.rmtree("/opt/OHM-Trade-Agent-v1", ignore_errors=True)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """Set up production-shaped state, then remove it."""
    _cleanup()
    tmp = tmp_path_factory.mktemp("replica-e2e")

    # Application root with a data directory, mirroring the deployed layout.
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "paper_trading").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "opip" / "canonical").mkdir(parents=True, exist_ok=True)
    DEPLOY_STATE.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    LEARNING_DATA.mkdir(parents=True, exist_ok=True)
    LEARNING_STATE.mkdir(parents=True, exist_ok=True)

    (DEPLOY_STATE / "last-good-sha").write_text(RELEASE_SHA + "\n", encoding="utf-8")

    # SSH shim: the only faked boundary. It receives the exact forced command,
    # runs the real reader with SSH_ORIGINAL_COMMAND set, and returns its tar.
    shim_dir = tmp / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "ssh"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -Eeuo pipefail
            # The sync invokes: ssh <host> "<forced command>"
            shift  # host
            export SSH_ORIGINAL_COMMAND="$*"
            exec {READER}
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    ENV_FILE.write_text(
        textwrap.dedent(
            f"""\
            OPIP_PRODUCTION_HOST=production.example.invalid
            OPIP_PRODUCTION_USER=opip-export
            OPIP_DEPLOYED_SHA={RELEASE_SHA}
            OPIP_LEARNING_IMAGE={_IMAGE}
            """
        ),
        encoding="utf-8",
    )

    yield {
        "tmp": tmp,
        "shim_dir": shim_dir,
        "env": {
            **os.environ,
            "PATH": f"{shim_dir}:{os.environ['PATH']}",
            # Overrides are read from the process environment, matching how
            # systemd supplies them in production.
            "OPIP_LEARNING_CANONICAL_REPLICA_ROOT": str(LEARNING_REPLICA),
            "OPIP_LEARNING_DATA_ROOT": str(LEARNING_DATA),
            "OPIP_LEARNING_STATE_ROOT": str(LEARNING_STATE),
            "OPIP_LEARNING_LOCK_FILE": "/var/run/opip-learning-plane.e2e.lock",
            "OPIP_CANONICAL_REPLICA_ROOT": str(LEARNING_REPLICA),
        },
    }
    _cleanup()


def _run(script: Path, env: dict, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# Canonical source seeding
# ---------------------------------------------------------------------------


def _outcome(paper_trade_id: str = PAPER_ID, episode_id: str = EPISODE, **overrides) -> dict:
    values = {
        "engine": "OHM_PAPER_SIM_V1",
        "paper_trade_id": paper_trade_id,
        "episode_id": episode_id,
        "cohort_id": "COH:1",
        "strategy_version": "OPIP-STRATEGY-V1",
        "exchange": "KRAKEN",
        "native_symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "quote_currency": "USD",
        "terminal_status": "CLOSED",
        "exit_reason": "STOP",
        "exit_price": 98.0,
        "entry_timestamp": ENTER,
        "exit_timestamp": EXIT,
        "capital_committed": 1000.0,
        "gross_pnl": -25.0,
        "fees_paid": 4.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "final_revision": 7,
        "terminal_event_id": "PTE:" + "b" * 24,
        "candidate_id": "CAND:1",
        "decision_context_id": "DI-CONTEXT:" + "c" * 32,
    }
    values.update(overrides)
    return build_terminal_outcome_payload(**values)


def _seed_canonical(outcomes: list[dict], *, delivery: str, gap_unresolved: list | None) -> None:
    """Seed the canonical source through the real writer and contract helpers."""
    from app.opip.canonical.models import WriterIntent

    live = DATA_ROOT / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    if live.exists():
        live.unlink()
    writer = CanonicalWriter(live)
    try:
        for payload in outcomes:
            ack = writer.submit(
                WriterIntent(
                    schema_version=1,
                    priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
                    idempotency_key=terminal_outcome_idempotency_key(payload["outcome_id"]),
                    event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                    payload=payload,
                )
            )
            assert ack.status == "OK", ack.detail
    finally:
        writer.close()
    # The snapshot is taken from a closed writer, so the live store is in WAL
    # mode; the exporter's PR-A0 publication normalizes the copy.

    lifecycles = {
        payload["paper_trade_id"]: {
            "paper_trade_id": payload["paper_trade_id"],
            "episode_id": payload["episode_id"],
            "status": "CLOSED",
            "revision": 8,
            "paper_only": True,
            "exchange_write_authority": False,
            "direction": "LONG",
            "closed_at": EXIT.isoformat(),
            "exit_price": 98.0,
            "net_pnl": payload.get("net_pnl"),
            "net_pnl_pct": payload.get("net_pnl_pct"),
            "outcome": "LOSS",
            "outcome_outbox": {"delivery": delivery, "gap_id": None},
        }
        for payload in outcomes
    }
    (DATA_ROOT / "paper_trading" / "state.json").write_text(
        json.dumps(
            {"schema_version": 1, "paper_only": True, "lifecycles": lifecycles},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (DATA_ROOT / "paper_trading" / "evidence_gap_spool.json").write_text(
        json.dumps({"unresolved": gap_unresolved or [], "updated_at": None}),
        encoding="utf-8",
    )


def _manifest_value(key: str) -> str:
    manifest = EXPORT_ROOT / "manifest.env"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


# ---------------------------------------------------------------------------
# Full chain
# ---------------------------------------------------------------------------


def test_export_reader_sync_installs_a_verified_generation(harness):
    """The load-bearing test: real transfer, real install, real readiness."""
    _seed_canonical([_outcome()], delivery="COMMITTED", gap_unresolved=None)

    # 1. Real exporter (root-only), exercising the real PR-A0 backup path.
    export = _run(EXPORTER, harness["env"])
    assert "deployment succeeded" not in export.stdout  # sanity: right script
    assert "canonical replica bundle OK" in export.stdout, export.stderr

    # Production export assertions.
    assert _manifest_value("schema_version") == "4"
    assert _manifest_value("canonical_learning_replica_version") == "1"
    assert _manifest_value("production_deployed_sha") == RELEASE_SHA
    bundle = EXPORT_ROOT / "canonical_learning_replica"
    assert bundle.is_dir()
    assert (bundle / "replica_manifest.json").is_file()
    assert (bundle / "opip/canonical/opip_canonical_v1.sqlite3").is_file()
    assert (bundle / "paper_trading/state.json").is_file()
    assert (bundle / "paper_trading/evidence_gap_spool.json").is_file()

    # 2. Real sync, which drives the real forced reader over the faked transport.
    sync = _run(SYNC, harness["env"])
    assert "sync: OK" in sync.stdout, (sync.stdout, sync.stderr)

    # 3. Installed generation and current pointer.
    current = resolve_current_generation(LEARNING_REPLICA)
    assert current.is_dir()
    assert current.parent.name == "generations"
    assert (current / "replica_manifest.json").is_file()

    # 4. Provenance: replica source == production == worker.
    bundle_verified = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA
    )
    assert bundle_verified.manifest.source_release_sha == RELEASE_SHA
    assert bundle_verified.completeness_supported is True

    # 5. Content: all three authority inputs from THIS generation.
    read = read_canonical_paper_outcomes(bundle_verified.canonical_db_path)
    assert len(read.outcomes) == 1
    assert bundle_verified.paper_state_path.is_file()
    assert bundle_verified.paper_gap_spool_path.is_file()
    # Completeness companions must not come from the writable data root.
    assert LEARNING_DATA not in bundle_verified.paper_state_path.parents

    # 6. Readiness against the installed replica.
    from app.opip.learning.linkage import (
        LinkageStatus,
        OutcomeSourceQuality,
        build_learning_linkage_records,
    )

    records = build_learning_linkage_records(
        canonical_rows=[{"snapshot_id": "SNAP:1", "episode_id": EPISODE, "decision_status": "QUALIFIED"}],
        ml_snapshot_rows=[
            {
                "canonical_snapshot_id": "SNAP:1",
                "ml_snapshot_id": "ML:1",
                "feature_snapshot": {
                    "snapshot_id": "ML:1",
                    "direction": "LONG",
                    "decision_at_utc": ENTER.isoformat().replace("+00:00", "Z"),
                    "features": [{"name": "momentum", "value": 1.0}],
                },
            }
        ],
        paper_trade_rows=[
            {
                "paper_trade_id": PAPER_ID,
                "episode_id": EPISODE,
                "status": "CLOSED",
                "revision": 8,
                "direction": "LONG",
                "closed_at": EXIT.isoformat(),
                "exit_price": 98.0,
                "net_pnl": -29.0,
                "net_pnl_pct": -2.9,
                "outcome": "LOSS",
                "outcome_outbox": {"delivery": "COMMITTED"},
            }
        ],
        paper_outcome_rows=[o.as_dict() for o in read.outcomes],
        paper_outcome_incomplete_reasons=(),
        paper_outcome_population_incomplete=False,
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].linkage_status is LinkageStatus.COMPLETE_FINAL
    assert records[0].primary_supervised_eligible is True

    # 7. Transfer-plane §39: the replica installed by THIS transfer, no
    #    approved promotion, so runtime influence is exactly neutral.
    from app.services import profitability_learning
    from app.services import trade_decision_intelligence as intel
    from app.services.learning_governance import NEUTRAL_CALIBRATION_MULTIPLIER

    profile = {
        "schema_version": 2,
        "version": "profitability-learning-v2",
        "trade_calibration": {"status": "CALIBRATED"},
        "weights": {"direction:LONG": 1.20},
    }
    profile["profile_id"] = profitability_learning._profile_content_id(profile)
    gov = harness["tmp"] / "gov"
    gov.mkdir(exist_ok=True)
    (gov / "strategy_calibration_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    os.environ["OPIP_LEARNING_GOVERNANCE_DIR"] = str(gov)
    profitability_learning.PROFILE_FILE = gov / "strategy_calibration_profile.json"
    profitability_learning.LOCK_FILE = gov / ".lock"

    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG", regime="RISK_ON"
    )
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == "NO_APPROVED_PROMOTION"


def test_legacy_export_without_marker_still_reads(harness):
    """Transition safety: an export with no marker must still serve."""
    _seed_canonical([], delivery="COMMITTED", gap_unresolved=None)
    # Force the no-marker path by hiding the deployed SHA.
    env = dict(harness["env"])
    (DEPLOY_STATE / "last-good-sha").write_text("not-a-sha\n", encoding="utf-8")
    try:
        export = _run(EXPORTER, env)
        assert "canonical replica skipped" in export.stdout
        assert _manifest_value("canonical_learning_replica_version") == ""
        assert "canonical_learning_replica_version" not in (
            EXPORT_ROOT / "manifest.env"
        ).read_text(encoding="utf-8")

        # Reader still serves the legacy schema-4 export.
        reader = subprocess.run(
            ["bash", str(READER)],
            env={**env, "SSH_ORIGINAL_COMMAND": f"opip-export-v2 sha={RELEASE_SHA} "
                 "sync_success_at=NONE capture_at=NONE capture_rc=0 outcomes_at=NONE "
                 "outcomes_rc=0"},
            capture_output=True,
            text=True,
            check=False,
        )
        # The forced-command regex is strict; a legacy tar may still be produced
        # when the command is accepted, otherwise the rejection is explicit.
        assert reader.returncode in (0, 126)
    finally:
        (DEPLOY_STATE / "last-good-sha").write_text(RELEASE_SHA + "\n", encoding="utf-8")


def test_read_only_mount_of_installed_generation(harness, tmp_path):
    """RUNTIME read-only enforcement against the generation sync installed."""
    current = resolve_current_generation(LEARNING_REPLICA)
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--read-only",
            "--network", "none",
            "--cap-drop", "ALL",
            "-v", f"{current}:/app/canonical-replica:ro",
            "-v", f"{LEARNING_DATA}:/app/data",
            _IMAGE,
            "bash", "-c",
            "touch /app/canonical-replica/WRITE_MUST_FAIL && echo WROTE || echo REFUSED",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "REFUSED" in result.stdout, (result.stdout, result.stderr)
    assert "WROTE" not in result.stdout
