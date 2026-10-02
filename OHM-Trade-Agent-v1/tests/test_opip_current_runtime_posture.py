"""Current runtime posture guard (MUTABLE; replaced by later activation increments).

This module is deliberately NOT the R4-B1 freeze. It asserts the *current*
repository-controlled production posture, which an owner-authorized activation
increment (for example R4-B2 Paper-v2 activation, or a Feature Bus activation)
is expected to change.

The immutable freeze contract `ATDD-R4-B1-contract-freeze` records the B1
completion invariant - Feature Bus `off` and Paper-v2 unset *at B1 completion* -
as contract text, and its acceptance module does not read mutable repository
configuration. When an activation increment changes a mode, it replaces or
updates THIS guard as part of that increment; the freeze stays valid.

The assertions below are exact (parsed YAML, not substring matching) so a mode
cannot pass by accident.
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


def test_feature_bus_is_pinned_off_in_the_core_service():
    """The core service ships the Feature Bus explicitly off, not by default."""
    assert _core_environment()["OPIP_FEATURE_BUS_MODE"] == "off"


def test_paper_v2_mode_is_unset_in_the_core_service():
    """Paper-v2 activation is an owner decision, so the core service must not set it."""
    assert "OPIP_PAPER_V2_MODE" not in _core_environment()
