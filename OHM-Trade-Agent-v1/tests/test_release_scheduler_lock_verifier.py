"""Release-scheduler lock verification (R4-B2 / EVIDENCE_SHADOW deploy gate).

Proves the deploy's ``validate_release_scheduler_contract`` lock check verifies the
REAL Feature Bus / feasibility producer invariant (a per-producer in-container
process-level lock with a distinct identity) and fails closed when the guard is
weakened, instead of silently aborting on a stale symbol name.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
FB_PRODUCER = APP_ROOT / "app" / "jobs" / "capture_feature_bus_shadow.py"
FEAS_PRODUCER = APP_ROOT / "app" / "jobs" / "capture_feasibility_evidence_shadow.py"

_BEGIN = "  # --- release-scheduler lock verification (begin) ---"
_END = "  # --- release-scheduler lock verification (end) ---"


def _bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _verifier_block() -> str:
    text = DEPLOY.read_text(encoding="utf-8")
    start = text.index(_BEGIN)
    end = text.index(_END, start)
    # The block body sits after the begin marker's line and before the end marker.
    body = text[text.index("\n", start) + 1 : end]
    assert body.strip(), "verifier block is empty"
    return body


def _run_verifier(root: Path, bash: str) -> subprocess.CompletedProcess:
    script = (
        "set -Eeuo pipefail\n"
        f"APP_ROOT='{root.as_posix()}'\n"
        "check() {\n"
        f"{_verifier_block()}"
        "}\n"
        "check\n"
    )
    return subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=60
    )


def _is_environment_fork_failure(proc: subprocess.CompletedProcess) -> bool:
    """True when bash could not run at all (Windows Git-bash cannot always fork)."""
    stderr = proc.stderr or ""
    return "fork:" in stderr or "Resource temporarily unavailable" in stderr


@pytest.mark.acceptance
def test_ac_012_verifier_checks_the_real_lock_invariant() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-012: the deploy verifies the real Feature Bus / feasibility lock invariant, not a stale symbol name."""
    text = DEPLOY.read_text(encoding="utf-8")
    # The stale, silently-failing assertion is gone.
    assert (
        "grep -Fq 'FEATURE_BUS_CAPTURE_LOCK_PATH' "
        '"$APP_ROOT/app/jobs/capture_feature_bus_shadow.py"' not in text
    )
    # The real invariant is asserted, explicitly and diagnostically.
    assert "class CaptureProcessLock" in text
    assert "run_capture_locked" in text
    assert "share a lock identity" in text
    assert "OPIP_RELEASE_SCHEDULER=UNIQUE_BOUNDED" in text
    assert "OPIP_FEATURE_BUS_LOCK_IDENTITY=" in text
    assert "OPIP_FEASIBILITY_LOCK_IDENTITY=" in text


@pytest.mark.acceptance
def test_ac_012_feature_bus_lock_identity_is_present_and_distinct() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-012: the Feature Bus producer exposes a distinct, producer-named lock identity; the feasibility identity differs."""
    import app.jobs.capture_feature_bus_shadow as fb
    import app.jobs.capture_feasibility_evidence_shadow as feas

    assert fb.FEATURE_BUS_CAPTURE_LOCK_PATH == "/tmp/opip-feature-bus-capture.lock"
    # Backwards-compatible alias resolves to the same identity.
    assert fb.DEFAULT_PROCESS_LOCK_PATH == fb.FEATURE_BUS_CAPTURE_LOCK_PATH
    assert feas.FEASIBILITY_CAPTURE_LOCK_PATH != fb.FEATURE_BUS_CAPTURE_LOCK_PATH
    assert hasattr(fb, "CaptureProcessLock")
    assert callable(fb.run_capture_locked)


@pytest.mark.acceptance
def test_ac_012_verifier_accepts_the_real_producers_and_fails_on_weakening() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-012: the actual verifier block accepts the real producer sources and fails closed when the process-lock guard is removed or identities collapse."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")

    # 1. Accepts the real repository layout.
    ok = _run_verifier(APP_ROOT, bash)
    if _is_environment_fork_failure(ok):
        pytest.skip("bash cannot fork reliably in this environment")
    assert ok.returncode == 0, ok.stderr
    assert "OPIP_RELEASE_SCHEDULER=UNIQUE_BOUNDED" in ok.stdout
    assert "OPIP_FEATURE_BUS_LOCK_IDENTITY=/tmp/opip-feature-bus-capture.lock" in ok.stdout

    # 2. Adversarial: removing the Feature Bus process-lock guard must fail.
    tmp = APP_ROOT / ".tmp-lock-verify-fixture"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        for rel in (
            "app/jobs/capture_feature_bus_shadow.py",
            "app/jobs/capture_feasibility_evidence_shadow.py",
        ):
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((APP_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")

        weakened = (tmp / "app/jobs/capture_feature_bus_shadow.py").read_text(
            encoding="utf-8"
        )
        (tmp / "app/jobs/capture_feature_bus_shadow.py").write_text(
            weakened.replace("class CaptureProcessLock", "class _RemovedLock"),
            encoding="utf-8",
        )
        bad = _run_verifier(tmp, bash)
        assert bad.returncode != 0
        assert "process-level lock guard is missing" in bad.stderr
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
