"""Current runtime posture guard (MUTABLE; replaced by later activation increments).

This module is deliberately NOT the R4-B1 freeze. It asserts the *current*
repository-controlled production posture, which an owner-authorized activation
increment is expected to change. The owner-authorized Release Pipeline v1
increment (ATDD-RELEASE-PIPELINE-v1) selects the EVIDENCE_SHADOW release profile,
which sets the production posture to canonical writer `shadow`, Feature Bus
`shadow`, target spine `shadow`, and Paper-v2 unset.

The immutable freeze contract `ATDD-R4-B1-contract-freeze` records the B1
completion invariant - Feature Bus `off` and Paper-v2 unset *at B1 completion* -
as contract text, and its acceptance module does not read mutable repository
configuration. This owner-authorized activation replaces THIS guard; the freeze
stays valid.

Every value below is a literal in the core service `environment` block, so a
stale `.env` can neither activate, widen, nor silently disable capture. The
assertions are exact (parsed YAML, not substring matching) so a mode cannot pass
by accident.
"""

from __future__ import annotations

from pathlib import Path

import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = APP_ROOT / "docker-compose.yml"
CORE_SERVICE = "ohm-trade-agent"


def _core_environment() -> dict[str, object]:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = document["services"][CORE_SERVICE]["environment"]
    assert isinstance(environment, dict), "core service environment must be a mapping"
    return environment


def test_feature_bus_is_owner_activated_to_shadow_in_the_core_service():
    """The EVIDENCE_SHADOW release profile pins the Feature Bus to `shadow` as a repo-controlled literal."""
    assert _core_environment()["OPIP_FEATURE_BUS_MODE"] == "shadow"


def test_canonical_writer_is_shadow_in_the_core_service():
    """The canonical shadow writer remains activated on the core service."""
    assert _core_environment()["OPIP_CANONICAL_WRITER_MODE"] == "shadow"


def test_target_spine_is_shadow_and_non_authoritative_in_the_core_service():
    """The EVIDENCE_SHADOW profile composes the F3-F7 target spine as a non-authoritative shadow path."""
    assert _core_environment()["OPIP_TARGET_SPINE_MODE"] == "shadow"


def test_paper_v2_mode_is_unset_in_the_core_service():
    """Paper-v2 activation is an owner decision, so the core service must not set it."""
    assert "OPIP_PAPER_V2_MODE" not in _core_environment()
