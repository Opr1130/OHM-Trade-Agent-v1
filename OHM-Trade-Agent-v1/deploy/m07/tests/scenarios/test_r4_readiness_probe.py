"""R4 L-01: the readiness probe must not lose a real release to a 0700 parent.

These tests execute the real ``Invoke-V2Readiness.ps1`` entry point with a
scripted ``ssh`` on PATH. No host, key or network is involved: every remote
command is answered by the stub, so exit codes and refusal behaviour are
exercised end to end.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

R1 = Path(__file__).resolve().parents[2]
SCRIPTS = R1 / "scripts"
READINESS = SCRIPTS / "Invoke-V2Readiness.ps1"
COMMON = SCRIPTS / "lib" / "OpipR1.Common.ps1"

PWSh = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(PWSh is None, reason="PowerShell is not available")

_STUB = r'''
import json, os, sys

arguments = sys.argv[1:]
command = arguments[-1] if arguments else ""
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": command}) + "\n")


def emit(text, code):
    sys.stdout.write(text)
    sys.stdout.flush()
    raise SystemExit(code)


if "metadata/v1/id" in command:
    emit(os.environ.get("STUB_IDENTITY", "610307364"), int(os.environ.get("STUB_IDENTITY_RC", "0")))
if "p=" in command and "echo PRESENT" in command:
    emit(os.environ.get("STUB_SOURCE", "PRESENT"), int(os.environ.get("STUB_SOURCE_RC", "0")))
if "docker image inspect" in command:
    emit(os.environ.get("STUB_IMAGE", json.dumps({"org.opencontainers.image.revision": "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"})),
         int(os.environ.get("STUB_IMAGE_RC", "0")))
if "docker ps -a" in command:
    emit(os.environ.get("STUB_CONTAINERS", "0"), 0)
if "df -Pk" in command:
    emit(os.environ.get("STUB_STORAGE", "90000000"), 0)
if "backups" in command:
    emit(os.environ.get("STUB_BACKUPS", "0"), 0)
emit("", 0)
'''


def _install_stub(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "ssh_stub.py").write_text(_STUB, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "ssh.cmd").write_text(
            '@echo off\r\npython "%~dp0ssh_stub.py" %*\r\n', encoding="ascii"
        )
    else:
        target = bin_dir / "ssh"
        target.write_text(
            '#!/bin/sh\nexec python3 "$(dirname "$0")/ssh_stub.py" "$@"\n',
            encoding="utf-8",
        )
        target.chmod(0o755)


def _run(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess, dict, list[str]]:
    m07 = tmp_path / "m07"
    shutil.copytree(SCRIPTS, m07 / "scripts")
    bin_dir = tmp_path / "bin"
    _install_stub(bin_dir)
    log = tmp_path / "stub.log"
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["STUB_LOG"] = str(log)
    env.update(overrides)
    completed = subprocess.run(
        [PWSh, "-NoProfile", "-File", str(m07 / "scripts" / "Invoke-V2Readiness.ps1"),
         "-Phase", "PreBootstrap", "-Execute", "-Authorized"],
        capture_output=True, text=True, timeout=180, env=env,
    )
    receipt_path = m07 / "evidence" / "receipts" / "readiness-PreBootstrap.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    commands = [
        json.loads(line)["command"]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if log.is_file() else []
    return completed, receipt, commands


def _check(receipt: dict, check_id: str) -> dict:
    for item in receipt.get("checks", []):
        if item.get("id") == check_id:
            return item
    return {}


def test_l01_a_root_only_release_cannot_fail_readiness(tmp_path):
    """L-01: a valid 0700 release is PRESENT through the approved sudo read."""
    completed, receipt, commands = _run(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert receipt["status"] == "PASS"
    assert _check(receipt, "SOURCE_RELEASE_PRESENT")["status"] == "PASS"
    assert _check(receipt, "REMOTE_IDENTITY")["status"] == "PASS"
    source = next(command for command in commands if "p=" in command and "echo PRESENT" in command)
    assert source.startswith("sudo -n bash -c '")
    assert "test -d" in source or "[ ! -d" in source
    assert "[ ! -r" in source and "[ ! -x" in source


def test_l01_missing_release_directory_is_a_failure_not_a_pass(tmp_path):
    completed, receipt, _ = _run(tmp_path, STUB_SOURCE="MISSING", STUB_SOURCE_RC="4")
    assert completed.returncode == 1
    assert receipt["status"] == "FAIL"
    check = _check(receipt, "SOURCE_RELEASE_PRESENT")
    assert check["status"] == "FAIL"
    assert "absent" in check["detail"]


def test_l01_sudo_refusal_is_insufficient_and_never_pass(tmp_path):
    completed, receipt, _ = _run(
        tmp_path, STUB_SOURCE="sudo: a password is required", STUB_SOURCE_RC="1"
    )
    assert completed.returncode == 1
    assert receipt["status"] == "FAIL"
    check = _check(receipt, "SOURCE_RELEASE_PRESENT")
    assert check["status"] == "INSUFFICIENT"


def test_l01_wrong_host_refuses_and_propagates_the_exit_code(tmp_path):
    completed, receipt, _ = _run(tmp_path, STUB_IDENTITY="589794133")
    assert completed.returncode == 1
    assert _check(receipt, "REMOTE_IDENTITY")["status"] == "FAIL"


def test_l01_a_failed_identity_probe_is_insufficient(tmp_path):
    completed, receipt, _ = _run(tmp_path, STUB_IDENTITY_RC="2")
    assert completed.returncode == 1
    assert _check(receipt, "REMOTE_IDENTITY")["status"] == "INSUFFICIENT"


def test_l01_other_successful_checks_are_unchanged(tmp_path):
    completed, receipt, _ = _run(tmp_path)
    assert completed.returncode == 0
    assert _check(receipt, "CERTIFIED_IMAGE")["status"] == "PASS"
    assert _check(receipt, "CONTAINER_STATE")["status"] == "PASS"
    assert _check(receipt, "STORAGE")["status"] == "PASS"
    assert _check(receipt, "BACKUP_SEED_INFO")["status"] == "SKIP"


def test_l01_the_probe_keeps_strict_host_verification():
    common = COMMON.read_text(encoding="utf-8")
    readiness = READINESS.read_text(encoding="utf-8")
    assert "-o BatchMode=yes" in common
    assert "-o StrictHostKeyChecking=yes" in common
    assert "StrictHostKeyChecking=no" not in common + readiness
    assert "sudo -n bash -c" in readiness
    # The unprivileged probe that produced the false negative must be gone.
    assert "\"test -d $releases/$sha/OHM-Trade-Agent-v1 && echo present\"" not in readiness

