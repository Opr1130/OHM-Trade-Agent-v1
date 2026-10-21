import os
from pathlib import Path

import pytest

from app.services.learning_recovery_transaction import (
    KEEP_CONFIG_BLOCKED,
    REFUSE_TIMERS_ENABLED,
    ROLLBACK_CONFIG,
    failure_action,
    generation_committed,
    restore,
    snapshot,
    verify_snapshot,
    write_receipt,
)


def _tx(tmp_path: Path) -> Path:
    return tmp_path / "transaction"


@pytest.mark.acceptance
def test_snapshot_restore_round_trip_and_missing_file_removal(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-012: contract criterion."""
    env = tmp_path / "etc" / "opip-learning.env"
    env.parent.mkdir(parents=True)
    env.write_text("OPIP_DEPLOYED_SHA=old\n")
    env.chmod(0o600)
    script = tmp_path / "usr" / "opip-learning-sync"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\nold\n")
    script.chmod(0o755)
    new_unit = tmp_path / "etc" / "systemd" / "opip-learning-sync.service"

    manifest = snapshot([env, script, new_unit], _tx(tmp_path))
    assert verify_snapshot(manifest) == []

    env.write_text("OPIP_DEPLOYED_SHA=new\n")
    script.write_text("#!/bin/bash\nnew\n")
    new_unit.parent.mkdir(parents=True)
    new_unit.write_text("new unit\n")

    actions = restore(manifest)
    assert env.read_text() == "OPIP_DEPLOYED_SHA=old\n"
    assert script.read_text() == "#!/bin/bash\nold\n"
    assert not new_unit.exists()
    if os.name == "posix":
        assert os.stat(script).st_mode & 0o777 == 0o755
    assert any(action["action"] == "removed_new_file" for action in actions)

    # Idempotent retry.
    restore(manifest)
    assert env.read_text() == "OPIP_DEPLOYED_SHA=old\n"
    assert not new_unit.exists()


@pytest.mark.acceptance
def test_tampered_backup_refuses_restore(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-012: contract criterion."""
    env = tmp_path / "env"
    env.write_text("original\n")
    manifest = snapshot([env], _tx(tmp_path))
    env.write_text("mutated\n")
    backup = Path(manifest["files"][0]["backup"])
    backup.write_text("tampered\n")

    with pytest.raises(ValueError, match="backup verification failed"):
        restore(manifest)
    assert env.read_text() == "mutated\n"


@pytest.mark.parametrize(
    "committed,timers,expected",
    [
        (False, False, ROLLBACK_CONFIG),
        (True, False, KEEP_CONFIG_BLOCKED),
        (False, True, REFUSE_TIMERS_ENABLED),
        (True, True, REFUSE_TIMERS_ENABLED),
    ],
)
@pytest.mark.acceptance
def test_failure_policy_distinguishes_config_from_committed_evidence(
    committed, timers, expected
):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-012: contract criterion."""
    assert (
        failure_action(
            stage="capture", evidence_committed=committed, timers_enabled=timers
        )
        == expected
    )


@pytest.mark.acceptance
def test_generation_commit_boundary():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-012: contract criterion."""
    assert generation_committed("gen-a", "gen-b") is True
    assert generation_committed("gen-a", "gen-a") is False
    assert generation_committed("gen-a", "") is False


@pytest.mark.acceptance
def test_receipt_is_append_only(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-012: contract criterion."""
    tx = _tx(tmp_path)
    path = write_receipt(tx, {"status": "PREPARED"}, name="prepared.json")
    assert path.read_text()
    with pytest.raises(FileExistsError):
        write_receipt(tx, {"status": "DUPLICATE"}, name="prepared.json")
