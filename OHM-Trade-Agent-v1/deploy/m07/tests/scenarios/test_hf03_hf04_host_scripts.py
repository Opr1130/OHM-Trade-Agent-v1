"""HF-03 / HF-04: executable safety contracts for the host-side scripts.

Shell scripts cannot be executed here (no V2 host), so these tests assert the
properties that make them safe, and syntax-check them with bash -n when bash is
available. They are intentionally structural: a passing test does not prove the
scripts run on a host, only that the safety rules are encoded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

R1 = Path(__file__).resolve().parents[2]
BOOTSTRAP = R1 / "scripts" / "v2" / "bootstrap-v2.sh"
ROLLBACK = R1 / "scripts" / "v2" / "rollback-v2.sh"


def _bash() -> str | None:
    candidate = shutil.which("bash")
    if candidate:
        return candidate
    fallback = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
    return str(fallback) if fallback.is_file() else None


@pytest.fixture(scope="module")
def bootstrap_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rollback_text() -> str:
    return ROLLBACK.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", [BOOTSTRAP, ROLLBACK], ids=["bootstrap", "rollback"])
def test_shell_syntax(script):
    bash = _bash()
    if not bash:
        pytest.skip("bash not available for a syntax check")
    env = dict(os.environ)
    env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, env=env)
    assert completed.returncode == 0, completed.stderr


def test_bootstrap_never_clones_and_never_masks_setup(bootstrap_text):
    assert "git clone" not in bootstrap_text, "bootstrap must reuse the installed release"
    masked = [line.strip() for line in bootstrap_text.splitlines()
              if "|| true" in line and not line.strip().startswith("#")]
    # Only read-only/idempotence probes may mask a non-zero exit. Everything that
    # mutates state must fail closed (R2: D10-class assertion, no `or True` escape).
    allowed = ("rev-parse", "image inspect", "grep -c", "grep -cx")
    unexpected = [line for line in masked if not any(token in line for token in allowed)]
    assert not unexpected, unexpected


def test_bootstrap_validates_identity_and_never_touches_tracked_nginx(bootstrap_text):
    assert "169.254.169.254/metadata/v1/id" in bootstrap_text
    assert 'DROPLET_ID="610307364"' in bootstrap_text
    # R3: the certified nginx config is used as-is; nothing is generated or installed over it.
    assert "$APP_ROOT/deploy/nginx/dashboard-sidecar.conf" in bootstrap_text
    assert "refusing to synthesise one" in bootstrap_text
    assert "nginx-v2.conf" not in bootstrap_text
    assert "nginx-mount.override.yml" not in bootstrap_text


def test_bootstrap_excludes_paper_p1_and_unapproved_scheduler(bootstrap_text):
    assert "ohm-freqtrade-paper" in bootstrap_text  # only as a refusal guard
    assert "retire-p1" not in bootstrap_text
    assert "activate-scheduler" in bootstrap_text
    assert '"$LIVE_ACCEPTANCE"' in bootstrap_text, "scheduler needs live acceptance evidence"
    assert "https_ok" in bootstrap_text and "telegram_ok" in bootstrap_text
    assert "reconcile-scheduler.sh" in bootstrap_text


def test_bootstrap_separates_every_receipt(bootstrap_text):
    for receipt in ("BOOTSTRAP_PREPARED", "BOOTSTRAP_AUTHORIZED", "BOOTSTRAP_EXECUTED",
                    "WRITER_ACTIVATED", "SCHEDULER_ACTIVATED"):
        assert receipt in bootstrap_text
    assert "blocking_incidents" in bootstrap_text
    assert "bind_authorization" in bootstrap_text, "authorisation must be bound to the prepared digest"
    assert "fresh_host_only" in bootstrap_text


def test_bootstrap_evaluates_preflight_independently(bootstrap_text):
    assert "preflight_protection_health" in bootstrap_text
    assert "pf_rc" in bootstrap_text, "the preflight exit code must be captured, not discarded"
    assert "set +e" in bootstrap_text and "set -e" in bootstrap_text


def test_rollback_is_durable_and_reads_the_database(rollback_text):
    assert 'DURABLE="/var/lib/opip-rollback"' in rollback_text
    assert "640 root:root" in rollback_text
    assert "LIVE_DB=" in rollback_text and "db_facts" in rollback_text
    assert "live_at_prepare" in rollback_text
    assert "depends_on_transient_controller_override" in rollback_text


def test_rollback_separates_kinds_and_guards_newer_data(rollback_text):
    assert "safe-baseline|code|data" in rollback_text
    assert "data rollback is not performed here" in rollback_text
    assert "requires --previous-image" in rollback_text
    assert "opip_rollback_guard.py" in rollback_text
    assert "live canonical history advanced since prepare" in rollback_text
    assert "WAL-safe canonical snapshot failed verification" in rollback_text


def test_readiness_uses_strict_known_host_verification():
    """HF-05: the SSH helper must never auto-accept an unknown host key."""
    common = (R1 / "scripts" / "lib" / "OpipR1.Common.ps1").read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=yes" in common
    assert "accept-new" not in common
    readiness = (R1 / "scripts" / "Invoke-V2Readiness.ps1").read_text(encoding="utf-8")
    assert "PreBootstrap" in readiness and "PostConfiguration" in readiness
    assert "sudo -n" in readiness
    assert "Invoke-R1SshProbe" in readiness


def test_rollback_preserves_and_validates_before_switching(rollback_text):
    preserve_index = rollback_text.index("opip_rollback_guard.py\" snapshot")
    stop_index = rollback_text.index("stop services")
    assert preserve_index < stop_index, "canonical must be snapshot before services stop"
    assert "sha256" in rollback_text
    assert "cp -a \"$APP_ROOT/data/opip/canonical\"" not in rollback_text
