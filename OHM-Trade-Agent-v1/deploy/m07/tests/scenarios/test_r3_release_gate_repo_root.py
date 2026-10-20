"""R3 release-gate authority contract.

``mode_execute`` runs the EVIDENCE_SHADOW architecture gate inside a one-shot
``compose run`` container. The certified image ships only ``app/``, so inside
that container ``_resolve_repo_root()`` cannot see ``docker-compose.yml`` and the
gate fails closed with ``FileNotFoundError``, aborting the install *after* the
core service has already started and leaving no ``BOOTSTRAP_EXECUTED`` receipt.

The gate container must therefore be handed exactly the tracked production
Compose file, read-only, and be pointed at it with ``--repo-root``. These tests
fail if that binding is removed, and they prove the Compose-backed gate still
passes the certified posture and still refuses missing or non-conforming
posture evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

R1 = Path(__file__).resolve().parents[2]
BOOTSTRAP = R1 / "scripts" / "v2" / "bootstrap-v2.sh"

#: The only file the gate may read as posture authority, and the flag that
#: points the gate at the read-only mount.
RELEASE_GATE_MOUNT = '-v "$APP_ROOT/docker-compose.yml:/repo/docker-compose.yml:ro"'
RELEASE_GATE_REPO_ROOT = "--repo-root /repo"

CERTIFIED_ENV = {
    "OPIP_RELEASE_PROFILE": "EVIDENCE_SHADOW",
    "OPIP_FEATURE_BUS_MODE": "shadow",
    "OPIP_CANONICAL_WRITER_MODE": "shadow",
    "OPIP_TARGET_SPINE_MODE": "shadow",
    "OPIP_PAPER_V2_MODE": "off",
    "OPIP_COMMITTEE_MODE": "off",
    "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "1000.0",
}


def _release_gate_stanza() -> str:
    """The full `run "release gate" ...` invocation, continuations joined."""
    lines = BOOTSTRAP.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if 'run "release gate"' not in line:
            continue
        block = [line]
        while block[-1].rstrip().endswith("\\"):
            index += 1
            block.append(lines[index])
        return "\n".join(block)
    return ""


def _write_compose(root: Path, env: dict[str, str]) -> Path:
    lines = ["services:", "  ohm-trade-agent:", "    environment:"]
    lines.extend(f'      {key}: "{value}"' for key, value in env.items())
    path = root / "docker-compose.yml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def gate():
    from conftest import CERT_SRC

    if not (CERT_SRC / "app" / "services" / "release_profiles.py").is_file():
        pytest.skip(f"certified app tree not present at {CERT_SRC}")
    from app.services.release_profiles import evaluate_architecture_gate

    return evaluate_architecture_gate


def test_release_gate_receives_the_certified_compose_authority() -> None:
    """The gate container must see docker-compose.yml and be pointed at it."""
    stanza = _release_gate_stanza()
    assert stanza, 'no `run "release gate"` invocation in bootstrap-v2.sh'
    assert RELEASE_GATE_MOUNT in stanza, (
        "release gate must mount the tracked docker-compose.yml read-only at /repo"
    )
    assert RELEASE_GATE_REPO_ROOT in stanza, (
        "release gate must pass --repo-root /repo so it can resolve the posture"
    )


def test_release_gate_layers_no_overlay_on_the_core_stack() -> None:
    """AC-001: the mount must not become a second Compose source of truth."""
    stanza = _release_gate_stanza()
    assert stanza, 'no `run "release gate"` invocation in bootstrap-v2.sh'
    assert " -f " not in stanza and "--file" not in stanza, (
        "release gate must not layer a Compose overlay onto the core stack"
    )
    assert stanza.count(" -v ") == 1, (
        "release gate must bind exactly one read-only path into the gate container"
    )
    assert RELEASE_GATE_MOUNT in stanza, (
        "the single bind must be the certified docker-compose.yml"
    )


def test_compose_backed_gate_passes_for_the_certified_posture(gate, tmp_path) -> None:
    _write_compose(tmp_path, CERTIFIED_ENV)
    verdict = gate("EVIDENCE_SHADOW", repo_root=str(tmp_path))
    assert verdict["status"] == "PASS", verdict
    assert verdict["checks"]["FUNDED_AUTHORITY_ABSENT"] is True
    assert verdict["checks"]["PAPER_V2_REMAINS_OFF"] is True
    assert verdict["checks"]["CURRENT_RUNTIME_POSTURE_CONSISTENT"] is True


def test_compose_backed_gate_refuses_a_missing_authority(gate, tmp_path) -> None:
    """No Compose file means no provable posture: the gate must refuse."""
    with pytest.raises(FileNotFoundError):
        gate("EVIDENCE_SHADOW", repo_root=str(tmp_path))


def test_compose_backed_gate_refuses_a_non_conforming_posture(gate, tmp_path) -> None:
    """A stale or activated paper mode is posture evidence the gate must reject."""
    _write_compose(tmp_path, {**CERTIFIED_ENV, "OPIP_PAPER_V2_MODE": "active"})
    verdict = gate("EVIDENCE_SHADOW", repo_root=str(tmp_path))
    assert verdict["status"] == "FAIL"
    assert verdict["checks"]["PAPER_V2_REMAINS_OFF"] is False
    assert verdict["checks"]["CURRENT_RUNTIME_POSTURE_CONSISTENT"] is False
