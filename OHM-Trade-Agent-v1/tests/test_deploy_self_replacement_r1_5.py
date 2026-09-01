from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "remote" / "ohm-deploy"
RECONCILE = ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"


def _extract_shell_function(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}$",
        text,
    )
    assert match, f"{name} function not found"
    return match.group(0)


def test_deploy_and_reconcile_shell_parse():
    subprocess.run(["bash", "-n", str(DEPLOY)], check=True)
    subprocess.run(["bash", "-n", str(RECONCILE)], check=True)


def test_restore_helper_replaces_inode_instead_of_truncating(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_text("new release\n", encoding="utf-8")
    target.write_text("old running release\n", encoding="utf-8")
    before_inode = target.stat().st_ino

    helper = _extract_shell_function(
        DEPLOY.read_text(encoding="utf-8"),
        "replace_file_atomically",
    )
    subprocess.run(
        [
            "bash",
            "-c",
            helper + '\nreplace_file_atomically "$1" "$2"',
            "test",
            str(source),
            str(target),
        ],
        check=True,
    )

    assert target.read_text(encoding="utf-8") == "new release\n"
    assert target.stat().st_ino != before_inode


def test_scheduler_executable_refresh_uses_atomic_staging():
    text = RECONCILE.read_text(encoding="utf-8")
    assert "install_executable_atomically" in text
    assert 'install -o root -g root -m 0755 "$DEPLOY_SCRIPT_SRC" "$DEPLOY_SCRIPT_DST"' not in text
