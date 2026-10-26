"""ATDD: release receipt + artifact identity contract (O'Pip deploy artifact mode).

These are executable acceptance tests for the *decision* logic that replaces the
Git-derived release identity on hosts without `.git`. They use no Docker, no Git
and no network, so they run anywhere Python runs.

Scope note: this module tests receipt/identity/eligibility semantics. The deploy
transaction itself (quiescence barrier, scheduler restore, health gates) is
unchanged and is covered by the existing controller contract tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Module-level mark: tests/atdd_scope.py only indexes tests that carry a pytest
# mark (module-level pytestmark or a decorator), so this module declares one.
# Acceptance marks are applied per traced test (see the AC docstrings), so only
# the tests cited by the scope contract are indexed by tests/atdd_scope.py.
MODULE_PATH = ROOT / "deploy" / "release" / "release_artifact.py"

_spec = importlib.util.spec_from_file_location("opip_release_artifact", MODULE_PATH)
ra = importlib.util.module_from_spec(_spec)
# dataclasses resolves annotations through sys.modules[cls.__module__]; register
# the module before executing it or @dataclass raises AttributeError.
sys.modules["opip_release_artifact"] = ra
_spec.loader.exec_module(ra)

APP_SHA = "a26d664972075b2a220a466215cf71e389decb86"
WRITER_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
APP_IMAGE_ID = "sha256:" + "a" * 64
WRITER_IMAGE_ID = "sha256:" + "b" * 64
REG_APP = "registry.example/opip/app@sha256:" + "c" * 64
REG_WRITER = "registry.example/opip/writer@sha256:" + "d" * 64


def artifact(service: str, ref: str, image_id: str, label: str,
             kind: str = "registry", reference: str | None = None):
    return ra.ServiceArtifact(
        service=service,
        image_ref=ref,
        image_id=image_id,
        revision_label=label,
        provenance_kind=kind,
        provenance_reference=reference if reference is not None else ref,
    )


def receipt(**overrides):
    base = dict(
        sequence=7,
        release_id=APP_SHA,
        authorized_target_sha=APP_SHA,
        stage="VERIFIED",
        source_control_version=ra.SCHEMA_ID,
        app=artifact("ohm-trade-agent", REG_APP, APP_IMAGE_ID, APP_SHA),
        writer=artifact("opip-canonical-writer", REG_WRITER, WRITER_IMAGE_ID, WRITER_SHA),
        pair_id=f"pair-{APP_SHA[:12]}-{WRITER_SHA[:12]}",
        runtime={"app_container_id": "c0ffee", "writer_container_id": "beef"},
        last_verified_good=None,
        health_evidence={"core_ok": True, "writer_ok": True, "profile": "EVIDENCE_SHADOW"},
    )
    base.update(overrides)
    return ra.Receipt(**base)


# --- immutability of references -------------------------------------------
@pytest.mark.parametrize("ref", [
    REG_APP,
    APP_IMAGE_ID,
])
def test_digest_pinned_references_are_accepted(ref):
    assert ra._is_digest_pinned(ref) is True


@pytest.mark.parametrize("ref", [
    "ohm-trade-agent:latest",
    "ohm-trade-agent:a26d6649",
    "registry.example/opip/app:v1",
    "ohm-trade-agent",
])
def test_floating_tags_are_rejected(ref):
    assert ra._is_digest_pinned(ref) is False


def test_service_artifact_rejects_floating_tag():
    with pytest.raises(ra.ArtifactError, match="not digest-pinned"):
        artifact("ohm-trade-agent", "ohm-trade-agent:latest", APP_IMAGE_ID, APP_SHA).validate()


def test_service_artifact_rejects_bad_image_id():
    with pytest.raises(ra.ArtifactError, match="image_id"):
        artifact("ohm-trade-agent", REG_APP, "aa565b64", APP_SHA).validate()


def test_service_artifact_rejects_non_sha_revision_label():
    with pytest.raises(ra.ArtifactError, match="revision_label"):
        artifact("ohm-trade-agent", REG_APP, APP_IMAGE_ID, "a26d6649").validate()


def test_registry_provenance_must_be_digest_pinned():
    with pytest.raises(ra.ArtifactError, match="registry provenance"):
        artifact("ohm-trade-agent", REG_APP, APP_IMAGE_ID, APP_SHA,
                 kind="registry", reference="registry.example/opip/app:latest").validate()


def test_unknown_provenance_kind_rejected():
    with pytest.raises(ra.ArtifactError, match="provenance kind"):
        artifact("ohm-trade-agent", REG_APP, APP_IMAGE_ID, APP_SHA,
                 kind="sneaky", reference="x").validate()


# --- two-image reconciliation ---------------------------------------------
def test_receipt_requires_both_services():
    r = receipt()
    r.validate()
    d = r.as_dict()
    assert d["app"]["service"] == "ohm-trade-agent"
    assert d["writer"]["service"] == "opip-canonical-writer"


def test_receipt_rejects_missing_writer():
    with pytest.raises(ra.ArtifactError):
        receipt(writer=artifact("", "", "", "")).validate()


def test_receipt_rejects_swapped_service_identity():
    with pytest.raises(ra.ArtifactError, match="app service"):
        receipt(app=artifact("opip-canonical-writer", REG_APP, APP_IMAGE_ID, APP_SHA)).validate()


def test_receipt_rejects_bad_target_sha():
    with pytest.raises(ra.ArtifactError, match="authorized_target_sha"):
        receipt(authorized_target_sha="fde262e").validate()


def test_receipt_rejects_unknown_stage():
    with pytest.raises(ra.ArtifactError, match="invalid stage"):
        receipt(stage="DONE").validate()


def test_receipt_rejects_empty_pair_id():
    with pytest.raises(ra.ArtifactError, match="pair_id"):
        receipt(pair_id="").validate()


def test_receipt_rejects_zero_sequence():
    with pytest.raises(ra.ArtifactError, match="sequence"):
        receipt(sequence=0).validate()


# --- rollback eligibility (fail closed) -----------------------------------
@pytest.mark.acceptance
def test_registry_verified_pair_is_rollback_eligible():
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-001: a registry-attestable VERIFIED pair is rollback-eligible."""
    verdict = receipt().rollback_eligibility()
    assert verdict["eligible"] is True
    assert verdict["reason"] == "OK"


def test_local_store_only_pair_is_blocked_storage():
    r = receipt(
        app=artifact("ohm-trade-agent", APP_IMAGE_ID, APP_IMAGE_ID, APP_SHA,
                     kind="local_store", reference=APP_IMAGE_ID),
        writer=artifact("opip-canonical-writer", WRITER_IMAGE_ID, WRITER_IMAGE_ID,
                        WRITER_SHA, kind="local_store", reference=WRITER_IMAGE_ID),
    )
    verdict = r.rollback_eligibility()
    assert verdict["eligible"] is False
    assert verdict["reason"] == "BLOCKED_STORAGE"
    assert "failure domain" in verdict["detail"]


def test_mixed_provenance_is_blocked_storage():
    r = receipt(writer=artifact("opip-canonical-writer", WRITER_IMAGE_ID, WRITER_IMAGE_ID,
                                WRITER_SHA, kind="local_store", reference=WRITER_IMAGE_ID))
    assert r.rollback_eligibility()["reason"] == "BLOCKED_STORAGE"


def test_unverified_stage_is_not_eligible():
    verdict = receipt(stage="APPLIED").rollback_eligibility()
    assert verdict["eligible"] is False
    assert verdict["reason"] == "STAGE_NOT_VERIFIED:APPLIED"


def test_missing_health_is_not_eligible():
    verdict = receipt(health_evidence={"core_ok": True}).rollback_eligibility()
    assert verdict["eligible"] is False
    assert verdict["reason"] == "HEALTH_UNPROVEN"


def test_failed_health_is_not_eligible():
    verdict = receipt(health_evidence={"core_ok": True, "writer_ok": False}
                      ).rollback_eligibility()
    assert verdict["reason"] == "HEALTH_UNPROVEN"


# --- atomic receipt I/O ----------------------------------------------------
def test_atomic_write_produces_valid_receipt(tmp_path):
    target = tmp_path / "receipts" / "current.json"
    ra.write_receipt_atomic(str(target), receipt())
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["schema"] == ra.SCHEMA_ID
    assert data["rollback_eligibility"]["eligible"] is True
    leftovers = [p.name for p in target.parent.iterdir() if p.name.startswith(".receipt-")]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_atomic_write_mode_is_restrictive(tmp_path):
    target = tmp_path / "current.json"
    ra.write_receipt_atomic(str(target), receipt())
    if os.name == "posix":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o640, oct(mode)


def test_atomic_write_replaces_without_partial_state(tmp_path):
    target = tmp_path / "current.json"
    ra.write_receipt_atomic(str(target), receipt(sequence=1))
    ra.write_receipt_atomic(str(target), receipt(sequence=2))
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["sequence"] == 2


def test_atomic_write_refuses_invalid_receipt(tmp_path):
    target = tmp_path / "current.json"
    with pytest.raises(ra.ArtifactError):
        ra.write_receipt_atomic(str(target), receipt(stage="NOPE"))
    assert not target.exists(), "invalid receipt must not create state"


# --- corrupt / absent / truncated state ------------------------------------
def test_missing_receipt_fails_closed(tmp_path):
    with pytest.raises(ra.ArtifactError, match="missing"):
        ra.load_receipt_dict(str(tmp_path / "absent.json"))


def test_empty_receipt_fails_closed(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ra.ArtifactError, match="empty"):
        ra.load_receipt_dict(str(p))


def test_truncated_receipt_fails_closed(tmp_path):
    p = tmp_path / "trunc.json"
    p.write_text('{"schema": "opip-release-receipt-v1", "sequence": 3, "app": {', encoding="utf-8")
    with pytest.raises(ra.ArtifactError, match="not valid JSON"):
        ra.load_receipt_dict(str(p))


def test_wrong_schema_fails_closed(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"schema": "opip-release-receipt-v0"}), encoding="utf-8")
    with pytest.raises(ra.ArtifactError, match="schema"):
        ra.load_receipt_dict(str(p))


def test_roundtrip_preserves_identity(tmp_path):
    target = tmp_path / "current.json"
    original = receipt()
    ra.write_receipt_atomic(str(target), original)
    restored = ra.receipt_from_dict(ra.load_receipt_dict(str(target)))
    assert restored.app.image_id == original.app.image_id
    assert restored.writer.image_ref == original.writer.image_ref
    assert restored.pair_id == original.pair_id
    assert restored.release_id == original.release_id


# --- monotonic sequence ----------------------------------------------------
def test_first_sequence_is_one():
    assert ra.next_sequence(None) == 1


def test_sequence_increments():
    assert ra.next_sequence({"sequence": 41}) == 42


def test_sequence_rejects_corrupt_previous():
    with pytest.raises(ra.ArtifactError):
        ra.next_sequence({"sequence": "many"})


# --- stage machine ---------------------------------------------------------
@pytest.mark.parametrize("current,target", [
    ("PREPARED", "APPLIED"),
    ("APPLIED", "VERIFIED"),
    ("VERIFIED", "ROLLED_BACK"),
    ("APPLIED", "ROLLBACK_UNPROVEN"),
])
def test_legal_stage_transitions(tmp_path, current, target):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": ra.SCHEMA_ID, "stage": current}), encoding="utf-8")
    assert ra.main(["stage-transition", "--path", str(p), "--to", target]) == 0


@pytest.mark.parametrize("current,target", [
    ("VERIFIED", "APPLIED"),
    ("ROLLED_BACK", "VERIFIED"),
    ("PREPARED", "VERIFIED"),
])
def test_illegal_stage_transitions_are_refused(tmp_path, current, target):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": ra.SCHEMA_ID, "stage": current}), encoding="utf-8")
    assert ra.main(["stage-transition", "--path", str(p), "--to", target]) == 2


# --- CLI contract used by the shell controller -----------------------------
def test_cli_validate_emits_contract(tmp_path, capsys):
    p = tmp_path / "r.json"
    ra.write_receipt_atomic(str(p), receipt())
    assert ra.main(["validate", "--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "RECEIPT_VALID=1" in out
    assert f"APP_IMAGE_ID={APP_IMAGE_ID}" in out
    assert f"WRITER_IMAGE_ID={WRITER_IMAGE_ID}" in out
    assert "ROLLBACK_ELIGIBLE=1" in out


def test_cli_blocked_storage_is_nonzero(tmp_path, capsys):
    p = tmp_path / "r.json"
    ra.write_receipt_atomic(str(p), receipt(
        app=artifact("ohm-trade-agent", APP_IMAGE_ID, APP_IMAGE_ID, APP_SHA,
                     kind="local_store", reference=APP_IMAGE_ID)))
    assert ra.main(["rollback-eligibility", "--path", str(p)]) == 3
    out = capsys.readouterr().out
    assert "ROLLBACK_ELIGIBLE=0" in out
    assert "BLOCKED_STORAGE" in out


def test_cli_bad_receipt_returns_two(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text("{not json", encoding="utf-8")
    assert ra.main(["validate", "--path", str(p)]) == 2
    assert "ARTIFACT_ERROR" in capsys.readouterr().err


# --- controller / compose static contracts --------------------------------
CONTROLLER = ROOT / "deploy" / "remote" / "ohm-deploy"
BOOTSTRAP = ROOT / "deploy" / "remote" / "bootstrap-deploy-controller-only.sh"
COMPOSE = ROOT / "docker-compose.yml"
ARTIFACT_OVERLAY = ROOT / "docker-compose.artifact.yml"


def test_controller_git_dependency_is_still_present_and_documented():
    """Guard the premise: the shipped controller derives identity from .git.

    If a future change removes this, the artifact-mode migration must be
    re-reviewed rather than silently assumed.
    """
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "stat -c '%U' \"$REPO_ROOT/.git\"" in text
    assert "rev-parse" in text
    assert "reset --hard" in text
    assert "build --build-arg" in text


def _yaml_body(path: Path) -> str:
    """Overlay text without comment lines, so prose cannot satisfy a check."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.acceptance
def test_artifact_overlay_pins_images_and_forbids_pull():
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-004: the overlay pins digests and forbids implicit pulls."""
    body = _yaml_body(ARTIFACT_OVERLAY)
    assert "image: ${OPIP_APP_IMAGE:" in body
    assert "image: ${OPIP_WRITER_IMAGE:" in body
    assert len(re.findall(r"(?m)^\s*pull_policy:\s*never\s*$", body)) == 2


def test_artifact_overlay_is_additive_only():
    """The overlay must not weaken base compose for the build-on-host path."""
    base = COMPOSE.read_text(encoding="utf-8")
    assert "build: ." in base
    body = _yaml_body(ARTIFACT_OVERLAY)
    for forbidden in ("build:", "privileged:", "network_mode:", "cap_add:",
                      "devices:", "volumes:"):
        assert forbidden not in body, f"overlay must not set {forbidden}"


# --- D9: release bundle of host-executed inputs -----------------------------
def make_bundle(tmp_path, overrides=None, drop=None, mutate=None):
    """Build a real bundle from the repo's own host scripts."""
    out = tmp_path / "bundle"
    out.mkdir()
    manifest = ra.build_bundle(
        str(ROOT), APP_SHA, str(out),
        REG_APP, REG_WRITER, APP_SHA, WRITER_SHA)
    if drop:
        (out / drop).unlink()
    if mutate:
        p = out / mutate
        p.write_bytes(p.read_bytes() + b"\n# tampered\n")
    if overrides:
        mp = out / "release-bundle.json"
        data = json.loads(mp.read_text(encoding="utf-8"))
        data.update(overrides)
        mp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out


def test_bundle_builds_with_every_allowlisted_host_script(tmp_path):
    out = make_bundle(tmp_path)
    data = json.loads((out / "release-bundle.json").read_text(encoding="utf-8"))
    assert data["schema"] == ra.BUNDLE_SCHEMA
    assert set(data["files"]) == set(ra.BUNDLE_ALLOWLIST)
    for rel in ra.BUNDLE_ALLOWLIST:
        assert (out / rel).is_file(), rel


# --- D9: on-host Compose inputs must match the release bundle --------------
def test_onhost_config_matching_bundle_verifies(tmp_path):
    out = make_bundle(tmp_path)
    checked = ra.verify_onhost_config(str(out), str(ROOT))
    assert set(checked) == set(ra.VERIFIED_CONFIG_ALLOWLIST)


@pytest.mark.acceptance
def test_onhost_config_drift_is_refused(tmp_path):
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-003: drifted on-host Compose inputs are refused."""
    out = make_bundle(tmp_path)
    root = tmp_path / "apptree"
    root.mkdir()
    for rel in ra.VERIFIED_CONFIG_ALLOWLIST:
        (root / rel).write_text("# drifted\n", encoding="utf-8")
    with pytest.raises(ra.ArtifactError, match="differs from the release bundle"):
        ra.verify_onhost_config(str(out), str(root))


def test_missing_compose_file_in_bundle_is_refused(tmp_path):
    out = make_bundle(tmp_path, drop="docker-compose.artifact.yml")
    with pytest.raises(ra.ArtifactError):
        ra.verify_bundle(str(out))


# --- controller wiring contracts (static, exact) ---------------------------
@pytest.mark.acceptance
def test_ghcr_publication_is_protected_and_private():
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-006: publication is owner-gated, job-scoped and never on PR code."""
    wf = (ROOT.parent / ".github" / "workflows" / "release-artifact-publish.yml")
    text = wf.read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    # owner-gated triggers only: no pull_request, no pull_request_target
    assert "pull_request" not in body
    assert "workflow_dispatch" in body
    # least privilege: packages write is job-scoped, contents read at top level
    assert "packages: write" in body
    assert body.index("permissions:") < body.index("packages: write")
    # private visibility enforced at package creation
    assert "ghcr.io" in body
    assert "visibility" in body or "private" in body.lower()


@pytest.mark.acceptance
def test_bootstrap_installs_release_helper_without_enabling_artifact_mode():
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-005: tooling install is version-coupled and does not enable artifact mode."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "release_artifact.py" in text
    assert "OPIP_RELEASE_TOOL_DST" in text
    assert "sha256" in text.lower()
    # installing tooling must never switch the deploy mode on
    assert "OPIP_DEPLOY_ARTIFACT_MODE=required" not in text
    assert "export OPIP_DEPLOY_ARTIFACT_MODE" not in text
    assert 'echo "OPIP_DEPLOY_ARTIFACT_MODE=off"' in text


def test_controller_cannot_silently_use_git_without_metadata():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "if [[ -z \"$REPO_OWNER\" ]]" in text
    assert "artifact mode is off" in text  # refuse exit 67 when .git absent
    assert "GIT=(false)" in text           # tripwire in artifact mode


def test_controller_admission_is_git_free_in_artifact_mode():
    text = CONTROLLER.read_text(encoding="utf-8")
    guard = text.index("artifact_preflight || {")
    legacy_fetch = text.index('"${GIT[@]}" fetch --prune origin main')
    assert guard < legacy_fetch
    # the fetch must sit inside the non-artifact branch
    between = text[guard:legacy_fetch]
    assert "else" in between


@pytest.mark.acceptance
def test_controller_rollback_has_no_rebuild_in_artifact_mode():
    """ATDD-RELEASE-ARTIFACT-MODE-v1/AC-002: rollback must never rebuild in artifact mode."""
    text = CONTROLLER.read_text(encoding="utf-8")
    idx = text.index("artifact_apply_pair \"$ROLLBACK_APP_REF\"")
    branch_end = text.index("elif docker compose -f docker-compose.yml", idx)
    artifact_branch = text[idx:branch_end]
    assert "build" not in artifact_branch.replace("--no-build", "")
    assert "--no-build" in text


def test_controller_forward_apply_avoids_build_in_artifact_mode():
    text = CONTROLLER.read_text(encoding="utf-8")
    idx = text.index('artifact_apply_pair "$ARTIFACT_APP_REF"')
    branch = text[idx:text.index("\nelse\n", idx)]
    assert "artifact_apply_pair" in branch
    assert "compose build" not in branch
    assert "--no-build" in text


def test_controller_rollback_never_writes_empty_last_good():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert 'printf \'%s\\n\' "$ROLLBACK_RELEASE_ID" > "$LAST_GOOD_FILE"' in text
    assert 'printf \'%s\\n\' "$TARGET_SHA" > "$LAST_GOOD_FILE"' in text  # forward unchanged


def test_bundle_verifies_and_exposes_two_image_refs(tmp_path):
    out = make_bundle(tmp_path)
    info = ra.verify_bundle(str(out))
    assert info["app_ref"] == REG_APP
    assert info["writer_ref"] == REG_WRITER
    assert info["app_revision"] != info["writer_revision"]


def test_bundle_rejects_missing_script(tmp_path):
    out = make_bundle(tmp_path, drop="deploy/remote/reconcile-scheduler.sh")
    with pytest.raises(ra.ArtifactError):
        ra.verify_bundle(str(out))


def test_bundle_rejects_tampered_script(tmp_path):
    out = make_bundle(tmp_path, mutate="deploy/remote/reconcile-scheduler.sh")
    with pytest.raises(ra.ArtifactError, match="checksum mismatch"):
        ra.verify_bundle(str(out))


def test_bundle_rejects_wrong_schema(tmp_path):
    out = make_bundle(tmp_path, overrides={"schema": "opip-release-bundle-v0"})
    with pytest.raises(ra.ArtifactError, match="schema"):
        ra.verify_bundle(str(out))


def test_bundle_rejects_floating_image_ref(tmp_path):
    out = make_bundle(tmp_path, overrides={
        "images": {"app": {"ref": "ghcr.io/o/app:latest", "revision": APP_SHA},
                   "writer": {"ref": REG_WRITER, "revision": WRITER_SHA}}})
    with pytest.raises(ra.ArtifactError, match="digest-pinned"):
        ra.verify_bundle(str(out))


def test_bundle_rejects_path_traversal_entry(tmp_path):
    out = make_bundle(tmp_path)
    mp = out / "release-bundle.json"
    data = json.loads(mp.read_text(encoding="utf-8"))
    data["files"]["../../etc/passwd"] = "a" * 64
    mp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with pytest.raises(ra.ArtifactError, match="safe relative path"):
        ra.verify_bundle(str(out))


def test_host_script_resolves_inside_bundle_only(tmp_path):
    out = make_bundle(tmp_path)
    resolved = ra.resolve_host_script(
        str(out), "deploy/remote/reconcile-scheduler.sh")
    assert resolved.startswith(str(out.resolve()))
    assert "app/deploy/remote" not in resolved.replace(str(out), "")


def test_host_script_refuses_non_allowlisted_name(tmp_path):
    out = make_bundle(tmp_path)
    with pytest.raises(ra.ArtifactError, match="allowlisted"):
        ra.resolve_host_script(str(out), "deploy/remote/evil.sh")


def test_host_script_detects_post_verification_tamper(tmp_path):
    out = make_bundle(tmp_path)
    ra.verify_bundle(str(out))
    p = out / "deploy/remote/reconcile-scheduler.sh"
    p.write_bytes(p.read_bytes() + b"\n# late tamper\n")
    with pytest.raises(ra.ArtifactError, match="checksum mismatch"):
        ra.resolve_host_script(str(out), "deploy/remote/reconcile-scheduler.sh")


# --- registry digest vs local config id ------------------------------------
DIGEST = "sha256:" + "e" * 64
CONFIG = "sha256:" + "f" * 64


def test_registry_digest_accepts_matching_repo_digest():
    v = ra.validate_registry_digest(DIGEST, f"ghcr.io/o/app@{DIGEST}", CONFIG)
    assert v["registry_identity"] is True


def test_registry_digest_rejects_config_id_used_as_digest():
    with pytest.raises(ra.ArtifactError, match="config id"):
        ra.validate_registry_digest(CONFIG, f"ghcr.io/o/app@{CONFIG}", CONFIG)


def test_registry_digest_rejects_mismatched_repo_digest():
    with pytest.raises(ra.ArtifactError, match="does not match"):
        ra.validate_registry_digest(DIGEST, f"ghcr.io/o/app@{CONFIG}", CONFIG)
