"""R3 controller-parity contracts.

These tests bind the M07 host scripts to the **certified deployment controller**
(`deploy/remote/ohm-deploy`) instead of to an owner-invented overlay. They fail
if anyone reintroduces a second Compose source of truth.
"""

from __future__ import annotations

import re
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


def test_tools_resolve_inside_the_shipped_toolkit(bootstrap_text, rollback_text):
    assert 'TOOLS="$SRC/deploy/m07/scripts"' in bootstrap_text
    assert 'TOOLS="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/deploy/m07/scripts"' in rollback_text


def test_config_digest_covers_only_the_certified_configuration(bootstrap_text):
    start = bootstrap_text.index("config_digest_value() {")
    body = bootstrap_text[start:bootstrap_text.index("\n}\n", start)]
    assert "$ENV_FILE" in body
    assert "docker-compose.yml" in body
    assert "nginx-v2.conf" not in body
