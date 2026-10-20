"""R4: remote POSIX paths and argument quoting survive a Windows operator host.

``Invoke-V2Bootstrap.ps1`` only *prints* the authorised remote sequence; these
tests prove it prints POSIX paths with correct quoting and never executes SSH.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

R1 = Path(__file__).resolve().parents[2]
SCRIPTS = R1 / "scripts"
COMMON = SCRIPTS / "lib" / "OpipR1.Common.ps1"
BOOTSTRAP = SCRIPTS / "Invoke-V2Bootstrap.ps1"
CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"

PWSh = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(PWSh is None, reason="PowerShell is not available")

_HARNESS = r'''
param([Parameter(Mandatory)][string]$Lib)
. $Lib
$results = [ordered]@{}
$results['join'] = Join-OpipPosixPath -Root '/opt/opip-m07-toolkit' -Child 'scripts/v2/bootstrap-v2.sh'
$results['join_backslash'] = Join-OpipPosixPath -Root '/opt/opip-m07-toolkit' -Child 'scripts\v2\bootstrap-v2.sh'
$results['join_trailing'] = Join-OpipPosixPath -Root '/opt/opip-m07-toolkit/' -Child '/scripts/v2/bootstrap-v2.sh'
$results['quote_plain'] = ConvertTo-OpipPosixArgument '/opt/opip-m07-toolkit/scripts/x.sh'
$results['quote_space'] = ConvertTo-OpipPosixArgument '/opt/opip m07/scripts/x.sh'
$results['quote_single'] = ConvertTo-OpipPosixArgument "/opt/it's here/x.sh"
$results['join_quote'] = Join-OpipPosixPath -Root "/opt/opip's toolkit" -Child 'scripts/v2/x.sh'
try { $null = Join-OpipPosixPath -Root 'relative/path' -Child 'x'; $results['relative_refused'] = 'NO' } catch { $results['relative_refused'] = 'YES' }
try { $null = ConvertTo-OpipPosixArgument "a`nb"; $results['newline_refused'] = 'NO' } catch { $results['newline_refused'] = 'YES' }
try { $null = Join-OpipPosixPath -Root '/opt/x' -Child '../escape'; $results['dotdot_refused'] = 'NO' } catch { $results['dotdot_refused'] = 'YES' }
$results | ConvertTo-Json -Compress
'''


def _harness(tmp_path: Path, executable: str | None = None) -> dict:
    harness = tmp_path / "harness.ps1"
    harness.write_text(_HARNESS, encoding="utf-8")
    args = [executable or PWSh, "-NoProfile"]
    if os.name == "nt":
        # The harness is a test artifact; the host policy may forbid -File.
        args += ["-ExecutionPolicy", "Bypass"]
    args += ["-File", str(harness), "-Lib", str(COMMON)]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip())


def test_posix_join_never_emits_windows_separators(tmp_path):
    result = _harness(tmp_path)
    assert result["join"] == "/opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh"
    assert "\\" not in result["join"]
    assert result["join_backslash"] == "/opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh"
    assert result["join_trailing"] == "/opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh"


def test_posix_quoting_round_trips_spaces_and_quotes(tmp_path):
    result = _harness(tmp_path)
    assert shlex.split(result["quote_plain"]) == ["/opt/opip-m07-toolkit/scripts/x.sh"]
    assert shlex.split(result["quote_space"]) == ["/opt/opip m07/scripts/x.sh"]
    assert shlex.split(result["quote_single"]) == ["/opt/it's here/x.sh"]


def test_posix_join_preserves_embedded_quotes_and_nested_paths(tmp_path):
    result = _harness(tmp_path)
    assert result["join_quote"] == "/opt/opip's toolkit/scripts/v2/x.sh"
    assert "\\" not in result["join_quote"]
    assert shlex.split(result["quote_single"]) == ["/opt/it's here/x.sh"]
    assert result["dotdot_refused"] == "YES"


def test_posix_helpers_refuse_escape_hatches(tmp_path):
    result = _harness(tmp_path)
    assert result["relative_refused"] == "YES"
    assert result["newline_refused"] == "YES"
    assert result["dotdot_refused"] == "YES"



def test_helpers_behave_the_same_in_windows_powershell_51(tmp_path):
    legacy = shutil.which("powershell")
    if not legacy or Path(legacy).resolve() == Path(PWSh).resolve():
        pytest.skip("Windows PowerShell 5.1 is not available")
    assert _harness(tmp_path, legacy) == _harness(tmp_path)


def test_bootstrap_wrapper_has_no_remote_execution_path():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "Join-OpipPosixPath" in text
    assert "Join-Path $ToolkitRoot" not in text
    for forbidden in ("Invoke-R1SshProbe", "& ssh", "Start-Process", "ssh.exe"):
        assert forbidden not in text, forbidden
    assert "never opens a remote shell" in text


def _run_wrapper(tmp_path: Path, toolkit_root: str) -> tuple[subprocess.CompletedProcess, dict, Path]:
    m07 = tmp_path / "m07"
    shutil.copytree(SCRIPTS, m07 / "scripts")
    marker = tmp_path / "ssh-was-called"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if os.name == "nt":
        (bin_dir / "ssh.cmd").write_text(
            f'@echo off\r\necho called > "{marker}"\r\nexit 99\r\n', encoding="ascii"
        )
    else:
        stub = bin_dir / "ssh"
        stub.write_text(f'#!/bin/sh\necho called > "{marker}"\nexit 99\n', encoding="utf-8")
        stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [PWSh, "-NoProfile", "-File", str(m07 / "scripts" / "Invoke-V2Bootstrap.ps1"),
         "-Phase", "Authorize", "-Authorize", "-ConfirmSha", CERTIFIED_SHA,
         "-ConfirmHost", "610307364", "-ToolkitRoot", toolkit_root],
        capture_output=True, text=True, timeout=180, env=env,
    )
    receipt_path = m07 / "evidence" / "receipts" / "bootstrap-Authorize.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    return completed, receipt, marker


def test_wrapper_prints_shell_safe_paths_with_embedded_quotes(tmp_path):
    completed, receipt, marker = _run_wrapper(tmp_path, "/opt/opip's m07 toolkit")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    steps = "\n".join(receipt["notes"])
    assert "\\opt" not in steps
    command = receipt["notes"][1]
    tokens = shlex.split(command)
    assert tokens[:3] == [
        "sudo", "-n", "/opt/opip's m07 toolkit/scripts/v2/bootstrap-v2.sh"
    ]
    assert tokens[-2:] == ["--toolkit-root", "/opt/opip's m07 toolkit"]
    assert not marker.exists(), "the wrapper must never open a remote shell"

