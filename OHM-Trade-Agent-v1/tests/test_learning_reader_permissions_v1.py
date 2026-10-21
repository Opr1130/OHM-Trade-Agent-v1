import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "remote" / "opip-learning-reader-permissions.sh"
CONFIGURE = ROOT / "deploy" / "remote" / "configure-opip-learning-reader.sh"

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX ownership/mode contract"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.acceptance
def test_pre_existing_nested_export_dirs_become_reader_traversable(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-001: contract criterion."""
    """ATDD-LEARNING-READER-v1: a late reader account can traverse old exports."""
    import grp

    group = grp.getgrgid(os.getgid()).gr_name
    export_root = tmp_path / "export"
    export_root.mkdir(mode=0o700)

    nested = export_root / "intelligence_learning"
    nested.mkdir(mode=0o700)
    events = nested / "events.jsonl"
    events.write_text("{}\n")
    events.chmod(0o600)

    qualification = export_root / "opip" / "qualification"
    qualification.mkdir(parents=True, mode=0o700)
    screening = qualification / "screening_evaluations.jsonl"
    screening.write_text("{}\n")
    screening.chmod(0o600)

    publish_lock = export_root / ".publish.lock"
    publish_lock.write_text("")
    publish_lock.chmod(0o600)

    staging = export_root / ".intelligence_learning"
    staging.mkdir(mode=0o700)

    result = subprocess.run(
        ["bash", str(SCRIPT), str(export_root), group],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    assert _mode(export_root) == 0o750
    assert _mode(nested) == 0o750
    assert _mode(qualification) == 0o750
    assert _mode(events) == 0o640
    assert _mode(screening) == 0o640
    assert _mode(publish_lock) == 0o640
    # Hidden staging remains exporter-owned and is not published evidence.
    assert _mode(staging) == 0o700


@pytest.mark.acceptance
def test_permission_normalization_requires_explicit_arguments(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-001: contract criterion."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 64
    assert "usage:" in result.stderr


@pytest.mark.acceptance
def test_reader_configuration_contract_invokes_permission_normalization():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-001: contract criterion."""
    text = CONFIGURE.read_text(encoding="utf-8")
    assert "opip-learning-reader-permissions.sh" in text
    assert '"$EXPORT_ROOT" "$USER_NAME"' in text


@pytest.mark.acceptance
def test_ci_syntax_gate_covers_reader_scripts():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-004: contract criterion."""
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "pytest.yml").read_text(
        encoding="utf-8"
    )
    assert "bash -n deploy/remote/configure-opip-learning-reader.sh" in workflow
    assert "bash -n deploy/remote/opip-learning-reader-permissions.sh" in workflow

