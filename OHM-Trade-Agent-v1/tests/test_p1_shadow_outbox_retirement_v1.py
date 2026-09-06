from pathlib import Path
import subprocess

from app.opip.data_platform.streams import STREAM_SPECS, resolve_streams


ROOT = Path(__file__).resolve().parents[1]


def test_p1_outbox_is_retired_end_to_end():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    exporter = (ROOT / "deploy/remote/export-opip-learning-evidence.sh").read_text(encoding="utf-8")
    reader = (ROOT / "deploy/remote/opip-learning-read-export.sh").read_text(encoding="utf-8")
    sync = (ROOT / "deploy/learning/opip-learning-sync.sh").read_text(encoding="utf-8")
    runner = (ROOT / "deploy/learning/opip-learning-job.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    retire = (ROOT / "deploy/remote/retire-p1-shadow-outbox.sh").read_text(encoding="utf-8")

    assert 'P1_SHADOW_OUTBOX_ENABLED: "false"' in compose
    assert 'copy_locked_jsonl "$DATA_ROOT/p1_shadow_outbox.jsonl"' not in exporter
    assert 'rm -f -- "$EXPORT_ROOT/p1_shadow_outbox.jsonl"' in exporter
    assert "p1_shadow_outbox_retired=1" in exporter
    assert "schema_version=4" in exporter
    assert "p1_shadow_outbox.jsonl" not in reader
    assert '"$DATA_ROOT/p1_shadow_outbox.jsonl"' in sync
    assert '[[ "$schema" == "4" ]]' in sync
    assert '[[ "$retired" == "1" ]]' in sync
    assert "P1_SHADOW_OUTBOX_ENABLED=true" not in runner
    assert 'write_disposition "CONSUMED_EMPTY"' in runner

    p1 = next(spec for spec in STREAM_SPECS if spec.name == "p1_shadow_outbox")
    assert p1.retired is True
    resolved = {spec.name for spec, _ in resolve_streams(Path("/tmp/opip-data"))}
    assert "p1_shadow_outbox" not in resolved

    # Irreversible cleanup must happen only after last-good is authoritative
    # and rollback handling has been disabled.
    last_good = deploy.rfind('> "$LAST_GOOD_FILE"')
    trap_off = deploy.rfind("trap - ERR")
    cleanup = deploy.rfind('bash "$P1_RETIRE_SCRIPT"')
    assert last_good < cleanup
    assert trap_off < cleanup
    assert 'bash "$LEARNING_EXPORTER"' in deploy[cleanup:]

    assert 'status=RETIRED_OWNER_DISCARDED' in retire
    assert 'historical_backfill_required=false' in retire
    assert 'trade_authority_changed=false' in retire
    assert 'docker compose exec -T ohm-trade-agent' in retire
    assert 'rm -f -- "$OUTBOX" "$CHECKPOINT" "$DEAD_LETTER"' in retire


def test_retirement_shells_parse():
    for rel in (
        "deploy/remote/retire-p1-shadow-outbox.sh",
        "deploy/remote/export-opip-learning-evidence.sh",
        "deploy/remote/opip-learning-read-export.sh",
        "deploy/learning/opip-learning-sync.sh",
        "deploy/learning/opip-learning-job.sh",
        "deploy/remote/ohm-deploy",
    ):
        subprocess.run(["bash", "-n", str(ROOT / rel)], check=True)
