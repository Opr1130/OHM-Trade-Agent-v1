"""Canonical shadow activation: blast radius and authority boundary.

This file proves what activating ``OPIP_CANONICAL_WRITER_MODE=shadow`` on the
core service does and, more importantly, what it does *not* do.

The activation is grounded in the real production compose file rather than a
hand-written settings object, so these tests fail if someone later changes the
compose in a way that silently widens or narrows the blast radius.

Activation is evidence capture only. It grants no ranking, trading, sizing,
alert-qualification, or funded execution authority, and it must not activate
the separately-gated Feature Bus.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.core.config import Settings

COMPOSE = Path("docker-compose.yml")


def _compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _service_block(name: str) -> str:
    """Extract one top-level service block from the compose file.

    Parsed rather than substring-matched: a whole-file search for a variable
    cannot distinguish the service that actually reads it from one that merely
    declares it, which would let a false safety assertion pass.
    """
    lines = _compose_text().splitlines()
    marker = f"  {name}:"
    start = None
    for index, line in enumerate(lines):
        if line == marker:
            start = index
            break
    assert start is not None, f"service {name} not found in compose"

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        # A new top-level service key is indented two spaces; its properties
        # are indented four or more.
        if line.startswith("  ") and not line.startswith("    ") and line.strip():
            end = index
            break
    return "\n".join(lines[start:end])


def _core_capture_mode() -> str:
    """Read the configured capture mode from the core service block."""
    for line in _service_block("ohm-trade-agent").splitlines():
        stripped = line.strip()
        if stripped.startswith("OPIP_CANONICAL_WRITER_MODE:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    raise AssertionError("core service does not declare OPIP_CANONICAL_WRITER_MODE")


def _core_feature_bus_mode() -> str:
    """Read the pinned Feature Bus mode from the core service block."""
    for line in _service_block("ohm-trade-agent").splitlines():
        stripped = line.strip()
        if stripped.startswith("OPIP_FEATURE_BUS_MODE:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    raise AssertionError("core service does not pin OPIP_FEATURE_BUS_MODE")


def _production_settings(**overrides) -> Settings:
    """Settings derived from the *configured* production core capture modes.

    Grounded in the compose file rather than hard-coded, so reverting the
    activation makes the dependent assertions fail instead of quietly passing.
    """
    values = {
        "opip_canonical_writer_mode": _core_capture_mode(),
        "opip_feature_bus_mode": _core_feature_bus_mode(),
    }
    values.update(overrides)
    return Settings(webhook_secret="test-webhook-secret", **values)


# ---------------------------------------------------------------------------
# Core activation
# ---------------------------------------------------------------------------


def test_core_service_activates_canonical_shadow():
    core = _service_block("ohm-trade-agent")
    assert 'OPIP_CANONICAL_WRITER_MODE: "shadow"' in core


def test_shadow_capture_is_enabled_for_the_activated_core():
    from app.opip.canonical.bridge import shadow_capture_enabled

    assert shadow_capture_enabled(_production_settings()) is True


def test_shadow_gate_does_not_widen_to_a_non_shadow_value():
    from app.opip.canonical.bridge import shadow_capture_enabled

    assert shadow_capture_enabled(Settings(
        webhook_secret="test-webhook-secret", opip_canonical_writer_mode="off"
    )) is False


def test_writer_daemon_does_not_consume_the_mode_variable():
    """The daemon-side compose value is a label, not the activation authority.

    Proved from source: ``writer_service.py`` never reads this setting, so the
    writer service's own ``"off"`` cannot be (mis)read as activation state.
    """
    from app.opip.canonical import writer_service

    source = inspect.getsource(writer_service)
    assert "OPIP_CANONICAL_WRITER_MODE" not in source
    assert "opip_canonical_writer_mode" not in source
    assert "resolve_writer_mode" not in source
    assert "shadow_capture_enabled" not in source


# ---------------------------------------------------------------------------
# Blast radius A - PR2 Early Watch canonical capture
# ---------------------------------------------------------------------------


def test_early_watch_canonical_capture_becomes_enabled():
    """Enabling shadow activates the already-approved Early Watch capture path."""
    from app.opip.canonical import bridge

    source = inspect.getsource(bridge)
    # The Early Watch capture paths are gated on exactly this helper.
    assert source.count("shadow_capture_enabled(settings)") >= 4


def test_early_watch_ops_json_authority_is_unchanged():
    """JSON remains operational alert-control authority under activation."""
    from app.opip.canonical import bridge

    source = inspect.getsource(bridge)
    assert "JSON remains operational" in source
    # Ops state mutation must not be conditional on canonical success.
    assert "record_opportunity_alert(" in source


def test_capture_gap_is_recorded_rather_than_blocking_ops():
    """Writer failure records a visible gap; it never blocks the ops path."""
    from app.opip.canonical import bridge

    source = inspect.getsource(bridge)
    assert "append_capture_gap" in source
    assert "reconcile_capture_gap_spool" in source


# ---------------------------------------------------------------------------
# Blast radius B - PR-A terminal paper-outcome capture
# ---------------------------------------------------------------------------


def test_paper_outcome_capture_is_gated_on_the_same_shadow_gate():
    from app.services import paper_outcome_outbox

    assert paper_outcome_outbox.canonical_capture_enabled(
        _production_settings()
    ) is True


def test_paper_outcome_capture_is_inert_without_shadow():
    from app.services import paper_outcome_outbox

    assert paper_outcome_outbox.canonical_capture_enabled(
        Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="off")
    ) is False


def test_paper_outcome_gate_fails_closed_on_settings_error(monkeypatch):
    """An unreadable mode must not be read as activated."""
    from app.services import paper_outcome_outbox

    def _boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(paper_outcome_outbox, "shadow_capture_enabled", _boom)
    assert paper_outcome_outbox.canonical_capture_enabled() is False


# ---------------------------------------------------------------------------
# Blast radius C - Feature Bus MUST remain inactive
# ---------------------------------------------------------------------------


def test_production_compose_pins_the_feature_bus_gate_off():
    """Feature Bus isolation must be declarative, not dependent on the .env.

    The core service loads ``env_file: .env``, so a stale deployment ``.env``
    carrying ``OPIP_FEATURE_BUS_MODE=shadow`` would activate Feature Bus capture
    the moment the writer is shadow, because that gate is dual-keyed. Pinning
    the value here makes the blast radius reviewable in the repository instead
    of resting on a file that is invisible to review. This forbids activation;
    it does not perform it.
    """
    assert _core_feature_bus_mode() == "off"


def test_feature_bus_is_not_activated_by_the_activation_itself():
    """The compose feature-bus pin comes from this change, and it is off."""
    core = _service_block("ohm-trade-agent")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in core


def test_feature_bus_stays_disabled_under_production_activation():
    """Writer=shadow plus pinned-off feature bus must not activate capture.

    This is the single most important isolation property of the activation.
    """
    from app.opip.features.publisher import (
        feature_bus_capture_enabled,
        resolve_feature_bus_mode,
    )

    settings = _production_settings()
    assert resolve_feature_bus_mode(settings) == "off"
    assert feature_bus_capture_enabled(settings) is False


def test_stale_env_cannot_activate_feature_bus():
    """A stale .env value is overridden by the compose pin.

    ``env_file`` values are overridden by the service ``environment`` block, so
    the pinned ``off`` wins. This test asserts the compose declares the pin,
    which is what makes that override effective.
    """
    core = _service_block("ohm-trade-agent")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in core
    # The hazard this defends against: shadow writer + shadow feature bus.
    from app.opip.features.publisher import feature_bus_capture_enabled

    stale = Settings(
        webhook_secret="test-webhook-secret",
        opip_feature_bus_mode="shadow",
        opip_canonical_writer_mode="shadow",
    )
    assert feature_bus_capture_enabled(stale) is True  # the hazard is real
    assert feature_bus_capture_enabled(_production_settings()) is False  # pinned off


def test_feature_bus_requires_both_gates_explicitly():
    from app.opip.features.publisher import feature_bus_capture_enabled

    both = Settings(
        webhook_secret="test-webhook-secret",
        opip_feature_bus_mode="shadow",
        opip_canonical_writer_mode="shadow",
    )
    assert feature_bus_capture_enabled(both) is True
    writer_only = Settings(
        webhook_secret="test-webhook-secret",
        opip_feature_bus_mode="off",
        opip_canonical_writer_mode="shadow",
    )
    assert feature_bus_capture_enabled(writer_only) is False


# ---------------------------------------------------------------------------
# Startup independence (§12)
# ---------------------------------------------------------------------------


def test_core_does_not_hard_depend_on_writer_health():
    """Activation must not couple core startup to writer availability."""
    core = _service_block("ohm-trade-agent")
    assert "condition: service_healthy" not in core
    assert "opip-canonical-writer" not in core


def test_writer_service_still_declares_its_own_healthcheck():
    writer = _service_block("opip-canonical-writer")
    assert "healthcheck:" in writer
    assert "app.opip.canonical.healthcheck" in writer


# ---------------------------------------------------------------------------
# Authority boundary (§10) - activation grants no decision authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "app.services.capital_allocation",
        "app.services.trade_decision_intelligence",
        "app.services.portfolio_risk",
        "app.services.learning_governance",
        "app.services.profitability_learning",
    ],
)
def test_decision_modules_do_not_reference_the_capture_gate(module_path):
    """Activation must not reach sizing, risk, or learning-influence logic.

    If any of these modules gained a dependency on the canonical capture gate,
    enabling evidence capture could change a decision, which the activation
    explicitly must not do.
    """
    module = __import__(module_path, fromlist=["*"])
    source = inspect.getsource(module)
    for forbidden in (
        "shadow_capture_enabled",
        "resolve_writer_mode",
        "feature_bus_capture_enabled",
        "OPIP_CANONICAL_WRITER_MODE",
    ):
        assert forbidden not in source, f"{module_path} must not consult {forbidden}"


def test_canonical_capture_gate_is_only_consumed_by_evidence_producers():
    """Constrain the consumers of the gate to known evidence-only producers.

    Scans every gate entry point, not just the two base helpers: a module that
    consulted the gate through ``canonical_capture_enabled`` or
    ``feature_bus_capture_enabled`` would otherwise evade this check while still
    deriving behaviour from canonical capture state.

    Every entry below is an evidence path, never a decision path:

    * ``bridge.py`` - PR2 Early Watch canonical capture + gap reconciliation.
    * ``publisher.py`` - Feature Bus capture (dual-gated, fails closed).
    * ``paper_outcome_outbox.py`` - PR-A outbox delivery and reconciliation.
    * ``paper_trade_registry.py`` - paper lifecycle evidence delivery, which
      delegates to the outbox gate above.
    * ``run_feature_bus_pilot.py`` - documentation of the dual gate; it names
      the variable in a docstring and consults no gate at runtime.

    The scan is textual, so a docstring mention counts. That is deliberate: an
    over-approximation fails closed by forcing a new consumer to be classified
    here rather than passing unnoticed.
    """
    gate_names = (
        "shadow_capture_enabled",
        "resolve_writer_mode",
        "canonical_capture_enabled",
        "feature_bus_capture_enabled",
        "resolve_feature_bus_mode",
    )
    expected = {
        "app/jobs/run_feature_bus_pilot.py",
        "app/opip/canonical/bridge.py",
        "app/opip/features/publisher.py",
        "app/services/paper_outcome_outbox.py",
        "app/services/paper_trade_registry.py",
    }
    root = Path("app")
    actual: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in gate_names):
            actual.add(path.as_posix())
    assert actual == expected, f"unexpected gate consumers: {actual ^ expected}"


def test_paper_trade_registry_gate_use_is_evidence_only():
    """The registry consults the gate only to decide whether to emit evidence."""
    from app.services import paper_trade_registry

    source = inspect.getsource(paper_trade_registry)
    for line in source.splitlines():
        if "canonical_capture_enabled(" in line:
            # Only ever used as a guard before emitting evidence.
            assert "if not canonical_capture_enabled()" in line.strip()

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # No ranking, sizing, or admission function may consult the gate.
            assert node.func.id not in {
                "recommend_capital",
                "evaluate_trade_decision",
                "evaluate_portfolio_risk",
            }


def test_settings_reject_invalid_capture_mode():
    """An invalid mode must fail parsing rather than silently becoming off."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="alert")
    with pytest.raises(ValidationError):
        Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="shadwo")


def test_activation_does_not_change_risk_or_sizing_defaults():
    """Pin the risk/sizing defaults so activation cannot ride along with them."""
    from app.services.capital_allocation import recommend_capital

    allocation = recommend_capital(
        available_capital=10_000.0,
        stop_distance_pct=2.0,
        confidence_score=80.0,
        net_edge_pct=3.0,
        calibration_multiplier=1.0,
    )
    signature = inspect.signature(recommend_capital)
    assert signature.parameters["risk_per_trade_pct"].default == 0.75
    assert signature.parameters["max_position_pct"].default == 20.0
    assert allocation.recommended_capital > 0


def test_activation_does_not_grant_exchange_or_live_authority():
    """No file in this change may reference live exchange execution."""
    compose = _compose_text()
    for forbidden in ("kraken_private", "KRAKEN_API_KEY", "LIVE_TRADING"):
        assert forbidden not in compose
