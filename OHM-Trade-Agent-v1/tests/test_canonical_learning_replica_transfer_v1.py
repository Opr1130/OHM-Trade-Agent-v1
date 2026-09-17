"""Transfer-plane contracts: exporter, forced reader, learning sync.

The exporter, reader and sync scripts only run as root on Linux, so the
executable assertions here are Linux-marked and run on CI. The contract
assertions run everywhere and are deliberately *scoped* rather than
whole-file substring searches, because a whole-file search cannot distinguish a
real behaviour from a comment or an unrelated branch.

Coverage is split by what each assertion can actually prove:

  CONTRACT VERIFIED   - the script's declarative shape is pinned here.
  RUNTIME VERIFIED    - requires executing the scripts on Linux with Docker,
                        and is marked as such.

The distinction matters for the read-only mount and the write attempt in
particular: neither is claimed as runtime-verified by this file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REMOTE = Path("deploy/remote")
LEARNING = Path("deploy/learning")
EXPORTER = REMOTE / "export-opip-learning-evidence.sh"
READER = REMOTE / "opip-learning-read-export.sh"
SYNC = LEARNING / "opip-learning-sync.sh"
RUNNER = LEARNING / "opip-learning-job.sh"

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="deployment scripts execute as root on Linux only"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(text: str, name: str) -> str:
    """Extract one shell function body by brace balance.

    Scanning starts at depth 1 so an inner ``|| { ... }`` block does not
    terminate the extraction early.
    """
    start = text.index(f"{name}() {{")
    index = start + len(f"{name}() {{")
    depth = 1
    for position in range(index, len(text)):
        char = text[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : position + 1]
    raise AssertionError(f"unbalanced function body for {name}")


# ===========================================================================
# Exporter
# ===========================================================================


def test_exporter_uses_python_for_snapshot_logic():
    """SQLite backup/manifest logic must not be reimplemented in shell."""
    text = _text(EXPORTER)
    assert "app.opip.learning.canonical_replica export" in text
    assert '"$PYTHON_BIN"' in text
    assert 'PYTHONPATH="$APP_ROOT"' in text
    # No raw SQLite copying anywhere in the exporter.
    for forbidden in ("cp -- \"$DATA_ROOT/opip/canonical", "tar -c .* sqlite3", "sqlite3 "):
        assert not re.search(forbidden, text), f"exporter must not {forbidden}"


def test_exporter_declares_python_bin_and_preflights_exact_interpreter():
    text = _text(EXPORTER)
    assert 'PYTHON_BIN="${OPIP_PYTHON_BIN:-/usr/bin/python3}"' in text
    assert '[[ -x "$PYTHON_BIN" ]]' in text
    assert "missing executable O'Pip Python interpreter" in text


def test_exporter_requires_valid_deployed_sha_for_canonical_provenance():
    """An unknown release must not mint canonical replica provenance."""
    text = _text(EXPORTER)
    assert 'if [[ -z "$production_deployed_sha" ]]; then' in text
    # The marker is only populated in the else branch, which runs only with a
    # valid SHA.
    sha_gate = text.index('if [[ -z "$production_deployed_sha" ]]; then')
    marker = text.index("canonical_learning_replica_version=1")
    assert marker > sha_gate
    # No git HEAD fallback for the release SHA.
    assert "git rev-parse" not in text


def test_exporter_publishes_content_addressed_bundle_without_deleting_committed_one():
    text = _text(EXPORTER)
    body = text[text.index("REPLICA_EXPORT_NAME=") : text.index("manifest_tmp=")]
    assert 'REPLICA_PUBLISH_NAME="${REPLICA_EXPORT_NAME}.${replica_sha}"' in body
    assert 'mv -- "$REPLICA_STAGING" "$REPLICA_PUBLISH_DIR"' in body
    assert 'rm -rf -- "$REPLICA_STAGING"' in body
    # The currently committed directory must not be removed before manifest
    # commit. Old content-addressed directories are pruned only afterwards.
    assert 'rm -rf -- "$REPLICA_PUBLISH_DIR"' not in body
    assert "canonical_learning_replica_dir=${REPLICA_PUBLISH_NAME}" in body


def test_exporter_failure_does_not_publish_a_v1_marker():
    """A failed canonical export must not certify a generation."""
    text = _text(EXPORTER)
    body = text[text.index("REPLICA_EXPORT_NAME=") : text.index("manifest_tmp=")]
    assert "replica_rc=$?" in body
    assert "canonical replica export FAILED" in body
    # The failure path exits before the manifest is written.
    assert body.index("replica_rc != 0") < body.index("REPLICA_MARKER_LINES=\"canonical")


def test_exporter_tree_helpers_are_defined_before_first_use():
    """bash would fail at runtime if the helpers were defined after use."""
    text = _text(EXPORTER)
    assert text.index("tree_bytes() {") < text.index("replica_bytes=\"$(tree_bytes")
    assert text.index("tree_sha256() {") < text.index("replica_sha=\"$(tree_sha256")


def test_exporter_marker_binds_version_directory_bytes_and_digest():
    text = _text(EXPORTER)
    for field in (
        "canonical_learning_replica_version=1",
        "canonical_learning_replica_dir=${REPLICA_PUBLISH_NAME}",
        "canonical_learning_replica_bytes=${replica_bytes}",
        "canonical_learning_replica_sha256=${replica_sha}",
    ):
        assert field in text
    # Conditional inclusion keeps legacy readers seeing the same schema.
    assert 'if [[ -n "$REPLICA_MARKER_LINES" ]]; then' in text
    assert 'printf \'%s\\n\' "$REPLICA_MARKER_LINES"' in text


def test_exporter_publishes_manifest_last_then_prunes_old_replica_dirs():
    text = _text(EXPORTER)
    manifest_publish = text.index('mv -f -- "$manifest_tmp" "$EXPORT_ROOT/manifest.env"')
    replica_publish = text.index('mv -- "$REPLICA_STAGING" "$REPLICA_PUBLISH_DIR"')
    prune = text.index('old_replica; do')
    assert replica_publish < manifest_publish < prune


def test_exporter_retains_exclusive_publish_lock():
    text = _text(EXPORTER)
    assert "flock -x 8" in text
    # Lock acquired before any publication work.
    assert text.index("flock -x 8") < text.index("copy_locked_jsonl ")


# ===========================================================================
# Forced reader
# ===========================================================================


def test_reader_takes_shared_lock_before_inspecting_the_marker():
    """Marker and bundle must come from one committed generation."""
    text = _text(READER)
    assert text.index("flock -s 8") < text.index("REPLICA_MARKER=")
    assert text.index("flock -s 8") < text.index("CANONICAL_REPLICA_DIR=")


def test_reader_parses_replica_version_and_directory_from_committed_manifest_only():
    text = _text(READER)
    assert 'awk -F= \'$1 == "canonical_learning_replica_version"' in text
    assert 'awk -F= \'$1 == "canonical_learning_replica_dir"' in text
    assert '"$EXPORT_ROOT/manifest.env"' in text
    assert "$EXPORT_ROOT/$CANONICAL_REPLICA_DIR" in text


def test_reader_legacy_mode_does_not_require_the_canonical_directory():
    text = _text(READER)
    # The canonical requirement lives inside the marker branch only.
    marker_branch = text[text.index('if [[ -n "$REPLICA_MARKER" ]]') : text.index("for name in")]
    assert "exit 66" in marker_branch
    # The unconditional availability loop excludes the canonical directory.
    loop = text[text.index("for name in") : text.index("exec tar -C")]
    assert "CANONICAL_REPLICA_DIR" not in loop


def test_reader_marker_one_requires_path_safe_content_addressed_bundle():
    text = _text(READER)
    branch = text[text.index('if [[ -n "$REPLICA_MARKER" ]]') : text.index("for name in")]
    assert 'if [[ "$REPLICA_MARKER" != "1" ]]' in branch
    assert '^canonical_learning_replica\\.[0-9a-f]{64}$' in branch
    assert "invalid canonical replica directory marker" in branch
    assert "export unavailable: $CANONICAL_REPLICA_DIR" in branch
    assert "exit 66" in branch


def test_reader_tar_members_include_manifest_and_append_replica_conditionally():
    text = _text(READER)
    assert "TAR_MEMBERS=(" in text
    assert "manifest.env" in text
    assert 'TAR_MEMBERS+=("$CANONICAL_REPLICA_DIR")' in text
    assert 'exec tar -C "$EXPORT_ROOT" -cf - "${TAR_MEMBERS[@]}"' in text


def test_reader_does_not_bump_the_ssh_protocol():
    """The replica contract belongs in manifest.env, not the command string."""
    text = _text(READER)
    assert "opip-export-v2" in text
    assert "opip-export-v3" not in text
    assert "canonical_learning_replica_version" not in text.split("elif [[ \"$ORIGINAL\"")[1]


# ===========================================================================
# Learning sync
# ===========================================================================


def test_sync_replica_root_is_overridable_and_separate_from_data_root():
    text = _text(SYNC)
    assert (
        'CANONICAL_REPLICA_ROOT="${OPIP_LEARNING_CANONICAL_REPLICA_ROOT:-/var/lib/opip-learning/canonical-replica}"'
        in text
    )
    assert 'DATA_ROOT="/var/lib/opip-learning/data"' in text


def test_sync_resolves_aliases_before_rejecting_replica_root_under_data_root():
    text = _text(SYNC)
    assert "realpath" in text.split("for cmd in", 1)[1].split("; do", 1)[0]
    assert 'DATA_ROOT_RESOLVED="$(realpath -m -- "$DATA_ROOT")"' in text
    assert (
        'CANONICAL_REPLICA_ROOT_RESOLVED="$(realpath -m -- "$CANONICAL_REPLICA_ROOT")"'
        in text
    )
    assert 'case "$CANONICAL_REPLICA_ROOT_RESOLVED/" in' in text
    assert '"$DATA_ROOT_RESOLVED"/*)' in text
    assert "must not resolve beneath the data root" in text
    assert 'CANONICAL_REPLICA_ROOT="$CANONICAL_REPLICA_ROOT_RESOLVED"' in text


def test_sync_requires_the_learning_image_and_does_not_use_host_python():
    """The app package is guaranteed in the image, not on the host."""
    text = _text(SYNC)
    assert ': "${OPIP_LEARNING_IMAGE:?OPIP_LEARNING_IMAGE is required}"' in text
    # The only Python invocation is inside the helper container.
    assert 'python -m app.opip.learning.canonical_replica "$@"' in text
    for line in text.splitlines():
        if "python3 -m app" in line or "python -m app" in line:
            assert "$OPIP_LEARNING_IMAGE" in text[: text.index(line)] or "docker run" in line


def test_sync_requires_docker_and_realpath_and_preflights_them():
    text = _text(SYNC)
    preflight = text.split("for cmd in", 1)[1].split("; do", 1)[0]
    assert "docker" in preflight
    assert "realpath" in preflight


def test_sync_helper_container_uses_the_hardened_posture():
    body = _function_body(_text(SYNC), "replica_helper")
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        "--pids-limit 128",
        "PYTHONDONTWRITEBYTECODE=1",
    ):
        assert flag in body
    # Unrelated mounts must not leak in.
    assert "/app/data" not in body


def test_sync_installer_mounts_store_read_write_but_input_read_only():
    """Only the trusted installer writes; the input stays immutable."""
    body = _function_body(_text(SYNC), "replica_helper")
    assert '-v "$2:$2:ro"' in body
    assert '-v "$2:$2:rw"' in body
    assert "--input-ro" in body
    assert "--store-rw" in body


def test_sync_marker_admission_binds_dynamic_directory_and_covers_all_cases():
    text = _text(SYNC)
    block = text[text.index("CANONICAL_REPLICA_REQUIRED=0") : text.index("validate_artifact() {")]
    assert 'CANONICAL_REPLICA_REQUIRED=1' in block
    assert "manifest_value canonical_learning_replica_dir" in block
    assert '^canonical_learning_replica\\.[0-9a-f]{64}$' in block
    # marker empty + CURRENT must fail closed.
    assert 'if [[ "$release_status" == "CURRENT" ]]; then' in block
    assert "export declares no canonical replica" in block
    # Unsupported marker rejected.
    assert "unsupported canonical_learning_replica_version" in block
    # Canonical admission requires a valid production SHA.
    assert "canonical replica requires a valid production_deployed_sha" in block


def test_sync_marker_is_not_used_to_infer_worker_generation():
    """Worker generation comes from the deployed SHA, not the export marker."""
    text = _text(SYNC)
    block = text[text.index("CANONICAL_REPLICA_REQUIRED=0") : text.index("validate_artifact() {")]
    # The decision is driven by release_status, which is derived from SHAs.
    assert "release_status" in block
    assert "OPIP_DEPLOYED_SHA" not in block or "release compatibility" in block.lower()


def test_sync_validates_outer_transport_against_directory_bytes_and_digest():
    body = _function_body(_text(SYNC), "validate_canonical_replica_outer")
    assert "manifest_value canonical_learning_replica_bytes" in body
    assert "manifest_value canonical_learning_replica_sha256" in body
    assert 'canonical_learning_replica.$expected_sha' in body
    assert 'tree_bytes "$bundle"' in body
    assert 'tree_sha256 "$bundle"' in body
    assert "exit 65" in body
    assert "exit 66" in body


def test_sync_delegates_inner_provenance_to_python():
    """Shell must not reproduce evidence-validation rules."""
    inner = _function_body(_text(SYNC), "validate_canonical_replica_inner")
    assert "replica_helper" in inner
    assert "verify" in inner
    assert "--release-sha" in inner
    # No shell-side hashing of the SQLite artifact.
    assert "sqlite" not in inner.lower()


def test_sync_install_delegates_to_python_installer():
    body = _function_body(_text(SYNC), "install_canonical_replica")
    assert "replica_helper" in body
    assert "install" in body
    assert "--host-root" in body
    assert "--staging" in body


def test_sync_validates_before_any_publication():
    text = _text(SYNC)
    validate_at = text.index("validate_canonical_replica_outer\n  validate_canonical_replica_inner")
    first_publish = text.index('rm -f -- \\\n  "$DATA_ROOT/p1_shadow_outbox.jsonl"')
    assert validate_at < first_publish


def test_sync_activates_the_replica_after_publishing_the_data_manifest():
    """Plane lock is held throughout, so this ordering cannot expose a gap."""
    text = _text(SYNC)
    manifest_at = text.index('mv -f -- "$INCOMING/manifest.env" "$DATA_ROOT/manifest.env"')
    install_at = text.index("  install_canonical_replica\nfi")
    last_sync_at = text.index("last_sync_at_utc=%s")
    assert manifest_at < install_at < last_sync_at


def test_sync_cleans_only_attempt_scoped_incoming_data():
    text = _text(SYNC)
    assert 'rm -rf -- "${INCOMING:?}/"*' in text
    # The replica store is never broadly deleted.
    assert 'rm -rf -- "$CANONICAL_REPLICA_ROOT' not in text
    assert 'rm -rf -- "$CANONICAL_REPLICA_ROOT/"*' not in text


def test_sync_preserves_release_drift_semantics():
    text = _text(SYNC)
    assert 'release_status="RELEASE_DRIFT"' in text
    assert "sync allowed; compute blocked" in text


# ===========================================================================
# Runner (cross-check against the sync installer boundary)
# ===========================================================================


def test_runner_never_mounts_the_replica_writable():
    """Learning jobs are read-only consumers; only sync may write."""
    text = _text(RUNNER)
    assert "REPLICA_MOUNT_ARGS" in text
    assert ":ro" in text
    # No writable canonical mount in the job runner.
    assert "canonical-replica:rw" not in text
    assert "$CANONICAL_REPLICA_ROOT:/app/canonical-replica\"" not in text


# ===========================================================================
# Executable wiring (Linux CI only)
# ===========================================================================


@POSIX_ONLY
def test_shell_scripts_are_syntactically_valid():
    import subprocess

    for script in (EXPORTER, READER, SYNC, RUNNER):
        subprocess.run(["bash", "-n", str(script)], check=True)


@POSIX_ONLY
def test_sync_exposes_test_root_overrides():
    """Temporary roots must be reachable without /var/lib access."""
    text = _text(SYNC)
    assert "OPIP_LEARNING_CANONICAL_REPLICA_ROOT" in text
