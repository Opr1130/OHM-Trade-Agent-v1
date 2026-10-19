"""R3 controller-parity contracts.

These tests bind the M07 host scripts to the **certified deployment controller**
(`deploy/remote/ohm-deploy`) instead of to an owner-invented overlay. They fail
if anyone reintroduces a second Compose source of truth.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

R1 = Path(__file__).resolve().parents[2]
BOOTSTRAP = R1 / "scripts" / "v2" / "bootstrap-v2.sh"
ROLLBACK = R1 / "scripts" / "v2" / "rollback-v2.sh"


@pytest.fixture(scope="module")
def bootstrap_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rollback_text() -> str:
    return ROLLBACK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def controller_text() -> str:
    from conftest import CERT_SRC
    path = CERT_SRC / "deploy" / "remote" / "ohm-deploy"
    if not path.is_file():
        pytest.skip(f"certified controller not present at {path}")
    return path.read_text(encoding="utf-8")


def _overlay_sets(text: str) -> list[str]:
    """Lines that layer `-f` files onto the CORE Compose project.

    The paper stack (`-f "$PAPER_COMPOSE"`) is a separate project and is excluded.
    """
    return [line.strip() for line in text.splitlines()
            if re.search(r"docker compose\s+-f|compose\s+-f", line)
            and "PAPER_COMPOSE" not in line
            and not line.strip().startswith("#")]


def _first_compose_file(line: str) -> str | None:
    match = re.search(r"-f\s+(\"[^\"]+\"|'[^']+'|\S+)", line)
    return match.group(1).strip("\"'") if match else None


def test_controller_overlay_sets_always_start_from_the_base_file(controller_text):
    """The certified controller keeps docker-compose.yml as the first -f file."""
    overlay = _overlay_sets(controller_text)
    assert overlay, "controller must have at least one core overlay path to compare against"
    for line in overlay:
        assert _first_compose_file(line) == "docker-compose.yml", line


def test_bootstrap_matches_the_controller_invocation(bootstrap_text, controller_text):
    assert '( cd "$APP_ROOT" && docker compose "$@" )' in bootstrap_text
    assert 'docker compose -f' not in bootstrap_text
    assert 'cd "$APP_ROOT"' in controller_text  # same working directory


def test_rollback_overlay_pattern_matches_the_controller(rollback_text, controller_text):
    ours = _overlay_sets(rollback_text)
    assert ours, "safe-baseline/code rollback need an overlay"
    for line in ours:
        assert _first_compose_file(line) == "docker-compose.yml", line
        assert any(token in line for token in
                   ("$OVERLAY", "SAFE_BASELINE_OVERRIDE", "code.override.yml")), line
    # The controller's own safe-baseline overlay uses the same base-first pattern.
    theirs = [line for line in _overlay_sets(controller_text) if "SAFE_BASELINE_OVERRIDE" in line]
    assert theirs, "controller safe-baseline overlay not found"
    assert all("-f docker-compose.yml" in line for line in theirs)


def test_no_host_script_depends_on_staged_overlay_inputs(bootstrap_text, rollback_text):
    for text in (bootstrap_text, rollback_text):
        assert "/tmp/opip-v2" not in text
        assert "STAGE_IN" not in text


def test_tools_resolve_through_the_integrity_verified_toolkit(bootstrap_text, rollback_text):
    for text in (bootstrap_text, rollback_text):
        assert 'TOOLKIT_ROOT="/opt/opip-m07-toolkit"' in text
        assert "TOOLKIT-MANIFEST.sha256" in text
        assert "sha256sum -c" in text
        assert 'TOOLS="$TOOLKIT_ROOT/scripts"' in text
    # A future release that ships the helpers is still accepted, but the
    # certified c196dd67 release is never assumed to contain them.
    assert 'TOOLS="$SRC/deploy/m07/scripts"' in bootstrap_text
    assert 'TOOLS="$APP_ROOT/deploy/m07/scripts"' in rollback_text
    assert 'TOOLS="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/deploy/m07/scripts"' not in rollback_text


def test_config_digest_covers_only_the_certified_configuration(bootstrap_text):
    start = bootstrap_text.index("config_digest_value() {")
    body = bootstrap_text[start:bootstrap_text.index("\n}\n", start)]
    assert "$ENV_FILE" in body
    assert "docker-compose.yml" in body
    assert "nginx-v2.conf" not in body


CERT_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
REPO = R1.parents[2]
HELPERS = ("opip_receipts.py", "opip_rollback_guard.py", "opip_recovery.py", "opip_bundle.py")


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _run_bytes(cmd, **kwargs):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


@pytest.mark.skipif(os.name == "nt", reason="POSIX: executes the real bash entrypoint")
def test_bootstrap_locates_helpers_outside_the_certified_tree(tmp_path):
    """A clean c196dd67 extraction has no deploy/m07; helpers are provisioned separately."""
    for tool in ("git", "tar", "rsync", "bash"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for this regression")
    probe = _run(["git", "-C", str(REPO), "cat-file", "-e", f"{CERT_SHA}^{{commit}}"])
    if probe.returncode != 0:
        pytest.skip("certified commit is absent in this checkout (shallow clone)")

    archive = _run_bytes(["git", "-C", str(REPO), "archive", "--format=tar", CERT_SHA,
                          "OHM-Trade-Agent-v1"])
    assert archive.returncode == 0, archive.stderr.decode()
    clean = tmp_path / "release"
    clean.mkdir()
    extract = _run_bytes(["tar", "-x", "-C", str(clean)], input=archive.stdout)
    assert extract.returncode == 0, extract.stderr.decode()

    certified = clean / "OHM-Trade-Agent-v1"
    assert (certified / "docker-compose.yml").is_file()
    assert not (certified / "deploy" / "m07").exists(), "c196dd67 must not contain the toolkit"

    toolkit = tmp_path / "toolkit"
    provisioned = _run(["bash", str(BOOTSTRAP), "--mode", "provision-toolkit",
                        "--from", str(R1), "--to", str(toolkit)])
    assert provisioned.returncode == 0, provisioned.stderr
    assert (toolkit / "TOOLKIT-MANIFEST.sha256").is_file()

    check = _run(["bash", str(BOOTSTRAP), "--mode", "check-toolkit",
                  "--toolkit-root", str(toolkit), "--release-root", str(clean)])
    assert check.returncode == 0, check.stderr
    fields = dict(line.split("=", 1) for line in check.stdout.splitlines() if "=" in line)
    assert fields["TOOLKIT"] == str(toolkit / "scripts")
    assert fields["TOOLKIT_SOURCE"] == "separate"
    for helper in HELPERS:
        assert (toolkit / "scripts" / helper).is_file(), helper

    victim = toolkit / "scripts" / "opip_receipts.py"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    tampered = _run(["bash", str(BOOTSTRAP), "--mode", "check-toolkit",
                     "--toolkit-root", str(toolkit), "--release-root", str(clean)])
    assert tampered.returncode != 0
    assert "integrity" in (tampered.stderr + tampered.stdout).lower()

    shipped = certified / "deploy" / "m07"
    shutil.copytree(R1, shipped, ignore=shutil.ignore_patterns("__pycache__", "evidence"))
    fallback = _run(["bash", str(BOOTSTRAP), "--mode", "check-toolkit",
                     "--toolkit-root", str(tmp_path / "absent"), "--release-root", str(clean)])
    assert fallback.returncode == 0, fallback.stderr
    assert f"TOOLKIT={shipped / 'scripts'}" in fallback.stdout
    assert "TOOLKIT_SOURCE=release" in fallback.stdout


def test_operator_guidance_contains_no_obsolete_overlay_or_nginx_staging():
    runbook = (R1 / "06-SINGLE-CUTOVER-RUNBOOK.md").read_text(encoding="utf-8")
    assert "single Compose file" in runbook
    for obsolete in ("nginx-mount.override.yml", "docker-compose.v2.yml", "/tmp/opip-v2"):
        assert obsolete not in runbook, obsolete

    bootstrap_wrapper = (R1 / "scripts" / "Invoke-V2Bootstrap.ps1").read_text(encoding="utf-8")
    for obsolete in ("docker-compose.v2.yml", "nginx-v2.conf", "/tmp/opip-v2", "ActivateScheduler"):
        assert obsolete not in bootstrap_wrapper, obsolete

    readiness = (R1 / "scripts" / "Invoke-V2Readiness.ps1").read_text(encoding="utf-8")
    assert "docker-compose.v2.yml" not in readiness
    rollback_wrapper = (R1 / "scripts" / "Invoke-V2Rollback.ps1").read_text(encoding="utf-8")
    assert "/tmp/opip-v2" not in rollback_wrapper
    dashboard = (R1 / "scripts" / "Invoke-V2DashboardValidation.ps1").read_text(encoding="utf-8")
    assert "drafts\\nginx-v2.conf" not in dashboard
    assert "dashboard-sidecar.conf" in dashboard

    for historical in ("03-REVISED-DEPLOYMENT-PROCEDURE.md", "10-HF-DEFECT-EVIDENCE.md",
                       "11-HF-CUTOVER-PROCEDURE.md"):
        text = (R1 / historical).read_text(encoding="utf-8")
        assert "HISTORICAL" in text.splitlines()[2], historical


def _provision(destination, source=None):
    return _run(["bash", str(BOOTSTRAP), "--mode", "provision-toolkit",
                 "--from", str(source or R1), "--to", str(destination)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX: executes the real bash entrypoint")
def test_provisioning_refuses_unsafe_destinations_before_any_mutation(tmp_path):
    for tool in ("bash", "rsync", "realpath", "mktemp"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for this regression")

    # Root and protected destinations are refused.
    probe = Path("/etc") / "opip-m07-toolkit-do-not-create"
    for destination in ("/", "/etc", "/opt", str(probe)):
        result = _provision(destination)
        assert result.returncode != 0, destination
        assert not probe.exists()

    # Unexpected existing contents survive untouched.
    unexpected = tmp_path / "unexpected"
    unexpected.mkdir()
    sentinel = unexpected / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    before = sorted(p.name for p in unexpected.iterdir())
    result = _provision(unexpected)
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in unexpected.iterdir()) == before
    assert not (unexpected / "TOOLKIT-MANIFEST.sha256").exists()

    # A symlink destination and a symlink ancestor are refused.
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    result = _provision(link)
    assert result.returncode != 0
    assert link.is_symlink()
    assert (real / "keep.txt").read_text(encoding="utf-8") == "keep"

    ancestor_target = tmp_path / "ancestor-target"
    ancestor_target.mkdir()
    ancestor = tmp_path / "ancestor"
    ancestor.symlink_to(ancestor_target, target_is_directory=True)
    result = _provision(ancestor / "toolkit")
    assert result.returncode != 0
    assert not (ancestor_target / "toolkit").exists()

    # Source/destination overlap in both directions.
    source = tmp_path / "source"
    shutil.copytree(R1, source, ignore=shutil.ignore_patterns("__pycache__", "evidence"))
    result = _provision(source / "nested", source)
    assert result.returncode != 0
    assert not (source / "nested").exists()
    assert (source / "scripts" / "v2" / "bootstrap-v2.sh").is_file()

    parent = tmp_path / "parent"
    parent.mkdir()
    nested_source = parent / "toolkit-source"
    shutil.copytree(R1, nested_source, ignore=shutil.ignore_patterns("__pycache__", "evidence"))
    result = _provision(parent, nested_source)
    assert result.returncode != 0
    assert (nested_source / "scripts" / "v2" / "bootstrap-v2.sh").is_file()

    # Non-root may not provision outside the canonical path or a temporary root.
    outside = Path.home() / ".opip-m07-toolkit-should-refuse"
    result = _provision(outside)
    assert result.returncode != 0
    assert not outside.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX: executes the real bash entrypoint")
def test_provisioning_replaces_only_a_verified_toolkit(tmp_path):
    for tool in ("bash", "rsync", "realpath", "mktemp"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for this regression")

    toolkit = tmp_path / "toolkit"
    assert _provision(toolkit).returncode == 0
    assert _provision(toolkit).returncode == 0  # verified tree may be replaced
    manifest = toolkit / "TOOLKIT-MANIFEST.sha256"
    assert manifest.is_file()
    original_manifest = manifest.read_bytes()

    # A tampered tree is refused and left exactly as it was.
    helper = toolkit / "scripts" / "opip_receipts.py"
    tampered = helper.read_text(encoding="utf-8") + "\n# tampered\n"
    helper.write_text(tampered, encoding="utf-8")
    result = _provision(toolkit)
    assert result.returncode != 0
    assert helper.read_text(encoding="utf-8") == tampered
    assert manifest.read_bytes() == original_manifest

    # No destructive synchronisation is used by the provisioning path.
    text = BOOTSTRAP.read_text(encoding="utf-8")
    start = text.index("mode_provision_toolkit() {")
    block = text[start:text.index("\n}\n", start)]
    assert "--delete" not in block
    assert "mktemp -d" in block
    assert 'mv -- "$staging" "$dest"' in block
    assert 'CANONICAL_TOOLKIT_ROOT="/opt/opip-m07-toolkit"' in text

