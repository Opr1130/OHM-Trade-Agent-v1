"""Learning runner contract: the replica must be mounted read-only.

These are static contract tests over the runner script rather than a running
container, so they pin the *shape* of the Docker invocation. They deliberately
scope to the ``docker run`` argument region instead of substring-matching the
whole file, because a whole-file search would not distinguish a real mount from
a comment or from an unrelated variable assignment.

Runtime filesystem enforcement is a separate claim and is NOT asserted here.
``CONTRACT VERIFIED`` and ``RUNTIME FILESYSTEM VERIFIED`` are different states;
the write attempt belongs to deployment validation on a host with Docker.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

RUNNER = Path("deploy/learning/opip-learning-job.sh")


def _runner_text() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _docker_run_block() -> str:
    """Extract just the ``docker run`` argument region."""
    text = _runner_text()
    start = text.index("docker run --rm")
    # The invocation ends at the image variable that terminates the arguments.
    end = text.index('"$OPIP_LEARNING_IMAGE"', start)
    return text[start:end]


def _mount_args(block: str) -> list[str]:
    return re.findall(r'-v\s+"([^"]+)"', block)


# ---------------------------------------------------------------------------
# The read-only replica bind
# ---------------------------------------------------------------------------


def test_replica_bind_is_present_and_read_only():
    """The read-only bind is built as an array and expanded into docker run.

    ``:ro`` appears in the array assignment rather than inline in the invocation,
    so assert both halves: the array element is read-only, and the array is
    actually expanded into the argument list.
    """
    text = _runner_text()
    assert (
        'REPLICA_MOUNT_ARGS=(-v "$REPLICA_GENERATION:$CANONICAL_REPLICA_CONTAINER_ROOT:ro")'
        in text
    )
    assert '"${REPLICA_MOUNT_ARGS[@]}"' in _docker_run_block()


def test_empty_replica_mount_array_is_used_when_absent():
    """An absent replica must not silently mount an empty directory.

    The array is empty (not a bind to a nonexistent path), so a container can
    never be handed read access to an unproven location.
    """
    text = _runner_text()
    assert "REPLICA_MOUNT_ARGS=()" in text


def test_replica_bind_source_is_the_resolved_generation():
    """The source must be one immutable generation, not the parent repository."""
    block = _docker_run_block()
    assert '"$REPLICA_GENERATION:$CANONICAL_REPLICA_CONTAINER_ROOT:ro"' in _runner_text()
    # The writable parent must never be mounted as the replica source.
    assert '"-v "$CANONICAL_REPLICA_ROOT:' not in block
    assert "$CANONICAL_REPLICA_ROOT:$CANONICAL_REPLICA_CONTAINER_ROOT" not in block


def test_no_writable_mount_exposes_the_replica_root():
    """A writable alias would defeat the read-only bind entirely."""
    for mount in _mount_args(_docker_run_block()):
        if "canonical-replica" in mount or "CANONICAL_REPLICA" in mount:
            assert mount.endswith(":ro"), f"replica mount must be read-only: {mount}"


def test_data_root_remains_writable_and_separate():
    """Learning outputs still need the writable data root, and it must not
    contain the replica."""
    mounts = _mount_args(_docker_run_block())
    assert '"$DATA_ROOT:/app/data"' in _runner_text()
    assert any(m.startswith("$DATA_ROOT:/app/data") for m in mounts)
    # The two roots are independent host paths.
    text = _runner_text()
    assert "/var/lib/opip-learning/data" in text
    assert "/var/lib/opip-learning/canonical-replica" in text


def test_replica_root_is_outside_the_writable_data_root():
    text = _runner_text()
    data_root = re.search(r'DATA_ROOT="\$\{OPIP_LEARNING_DATA_ROOT:-([^}]+)\}"', text)
    replica_root = re.search(
        r'CANONICAL_REPLICA_ROOT="\$\{OPIP_CANONICAL_REPLICA_ROOT_HOST:-([^}]+)\}"', text
    )
    assert data_root and replica_root
    assert not replica_root.group(1).startswith(data_root.group(1)), (
        "the replica root must not live under the writable learning data root"
    )


# ---------------------------------------------------------------------------
# Environment and isolation flags
# ---------------------------------------------------------------------------


def test_replica_root_environment_is_passed():
    block = _docker_run_block()
    assert 'OPIP_CANONICAL_REPLICA_ROOT="$CANONICAL_REPLICA_CONTAINER_ROOT"' in block
    assert 'OPIP_CANONICAL_DIR="$CANONICAL_REPLICA_CONTAINER_ROOT/opip/canonical"' in block


def test_isolation_flags_are_preserved():
    block = _docker_run_block()
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
    ):
        assert flag in block


# ---------------------------------------------------------------------------
# Resolution and fail-closed behaviour
# ---------------------------------------------------------------------------


def test_pointer_resolution_rejects_traversal_and_absolute_paths():
    text = _runner_text()
    # A strict identifier pattern is what rejects traversal.
    assert r"^[A-Za-z0-9][A-Za-z0-9._-]*$" in text
    assert "current" in text


def test_resolution_requires_an_installed_generation_with_manifest():
    text = _runner_text()
    resolver = text[text.index("resolve_canonical_replica_generation()") :]
    resolver = resolver[: resolver.index("REPLICA_GENERATION=")]
    assert '-d "$resolved"' in resolver
    assert "replica_manifest.json" in resolver


def test_required_jobs_fail_closed_when_no_replica_is_installed():
    text = _runner_text()
    assert 'REQUIRE_CANONICAL_REPLICA_JOBS=" readiness outcomes "' in text
    assert "requires a verified canonical replica" in text
    # Missing replica must be a hard stop, never a silent empty mount.
    assert "exit 78" in text


def test_readiness_job_is_dispatched_to_the_readiness_module():
    text = _runner_text()
    assert "readiness)" in text
    assert "app.jobs.run_opip_ml_data_readiness" in text
