import json
from pathlib import Path

import pytest

from app.services.learning_recovery_receipt import (
    BLOCKED,
    CERTIFIED,
    PREPARED,
    RecoveryReceiptError,
    materialize_blocked,
    write_intent,
    write_terminal,
)

ROOT = Path(__file__).resolve().parents[1]


def _intent(tmp_path: Path, transaction: str = "tx-1") -> Path:
    path = tmp_path / "binding.intent.json"
    write_intent(
        path,
        transaction_id=transaction,
        production_sha="a" * 40,
        tooling_sha="b" * 40,
        expected_image="opip-learning:recovery",
        expected_image_id="sha256:" + "c" * 64,
        manifest_sha="a" * 40,
    )
    return path


@pytest.mark.acceptance
def test_intent_is_prepared_before_terminal_and_is_not_edited(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-016: durable binding order."""
    path = _intent(tmp_path)
    before = path.read_bytes()
    intent = json.loads(before)
    assert intent["status"] == PREPARED
    assert intent["success"] is False
    terminal = write_terminal(
        path, status=CERTIFIED, detail="recovery complete"
    )
    record = json.loads(terminal.read_text(encoding="utf-8"))
    assert record["status"] == CERTIFIED
    assert record["success"] is True
    assert record["intent_sha256"]
    assert path.read_bytes() == before


@pytest.mark.acceptance
def test_blocked_terminal_never_asserts_success(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-016: truthful failure binding."""
    path = _intent(tmp_path)
    terminal = write_terminal(
        path, status=BLOCKED, detail="outcomes failed", failure_receipt="failure.json"
    )
    record = json.loads(terminal.read_text(encoding="utf-8"))
    assert record["status"] == BLOCKED
    assert record["success"] is False
    assert record["failure_receipt"] == "failure.json"


@pytest.mark.acceptance
def test_duplicate_intent_is_idempotent_but_foreign_transaction_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-016: O_EXCL intent identity."""
    path = _intent(tmp_path)
    assert _intent(tmp_path).read_bytes() == path.read_bytes()
    with pytest.raises(RecoveryReceiptError):
        write_intent(
            path,
            transaction_id="tx-2",
            production_sha="a" * 40,
            tooling_sha="b" * 40,
            expected_image="opip-learning:recovery",
            expected_image_id="sha256:" + "c" * 64,
            manifest_sha="a" * 40,
        )


@pytest.mark.acceptance
def test_missing_binding_is_materialized_as_blocked_from_failure(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-016: prior failed binding repair."""
    failure = tmp_path / "failure.json"
    failure.write_text(
        json.dumps(
            {
                "transaction_dir": "/var/lib/opip-learning/state/recovery-transactions/x",
                "production_sha": "a" * 40,
                "tooling_sha": "b" * 40,
            }
        ),
        encoding="utf-8",
    )
    binding = tmp_path / "missing-binding.json"
    assert not binding.exists()
    result = materialize_blocked(binding, failure)
    record = json.loads(result.read_text(encoding="utf-8"))
    assert record["status"] == BLOCKED
    assert record["success"] is False
    assert record["failure_sha256"]
    assert materialize_blocked(binding, failure) == binding


@pytest.mark.acceptance
def test_recovery_script_creates_intent_before_binding_reference():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-016: no future binding path."""
    script = (
        ROOT / "deploy/learning/run-recovery-learning-deploy.sh"
    ).read_text(encoding="utf-8")
    intent = script.index("learning_recovery_receipt write-intent")
    env_write = script.index('-v receipt="$RECEIPT_INTENT"')
    assert intent < env_write
    assert "learning_recovery_receipt write-terminal" in script
    assert "--status BLOCKED" in script


@pytest.mark.acceptance
def test_sync_preserves_before_replacing_and_marks_after_commit():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: preservation boundary order."""
    script = (
        ROOT / "deploy/learning/opip-learning-sync.sh"
    ).read_text(encoding="utf-8")
    preserve = script.index("derived_state_helper retire")
    replace = script.index('mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"')
    mark = script.index("derived_state_helper mark-lineage")
    assert preserve < replace < mark
    assert "refusing to reuse unbound legacy derived state" in script
