from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_stream_failure_does_not_fail_core_scheduler_reconciliation():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")

    assert 'if bash "$STREAM_RECONCILE"; then' in source
    assert "shadow evidence unavailable or incomplete" in source
    assert "production core unaffected" in source


def test_stream_worker_activation_gate_remains_strict():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-stream-worker.sh"
    ).read_text(encoding="utf-8")

    assert "app.opip.streaming.activation_check" in source
    assert "O'Pip stream worker activation check failed" in source
    assert "exit 1" in source
