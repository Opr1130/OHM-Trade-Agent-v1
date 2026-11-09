"""O'Pip runtime-identity authority must fail closed.

The defect under test: ``app/services/target_attainability.py`` reimplemented the
"is this a conservative runtime?" question locally by reading ``Settings.app_env``,
whose model default is ``"development"``. The canonical authority
``app.core.runtime_environment.conservative_runtime()`` treats an absent value as
``"unknown"`` and answers conservatively. The duplicate therefore answered the
opposite of the canonical authority whenever ``APP_ENV`` was absent - which is
exactly the posture a deployment gets when nobody sets it - and the LONG
execution-evidence gate could reject nothing.

Only the environment variable is manipulated here. The gate, the canonical
authority, the Compose file and the CI workflow are the real artefacts.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import config as config_module
from app.core import runtime_environment
from app.scanner.models import MarketSnapshot
from app.services import target_attainability
from app.services.entry_exit_advisor import build_entry_exit_plan

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = APP_ROOT / "docker-compose.yml"
PYTEST_WORKFLOW = APP_ROOT.parent / ".github" / "workflows" / "pytest.yml"

#: Identities that are deliberately NOT production-like. Everything else - including
#: absent, blank, unrecognised and mixed-case - must be treated as conservative.
NON_PRODUCTION = ("development", "dev", "test", "testing")

#: Identities that must never become permissive.
CONSERVATIVE_IDENTITIES = (
    "",
    "unknown",
    "staging",
    "prod",
    "production",
    "PRODUCTION",
    "Production",
    "qa",
    "rehearsal",
    "  ",
    "live",
)


@pytest.fixture(autouse=True)
def _isolate_runtime_identity(tmp_path, monkeypatch):
    """Keep each case free of a developer .env and of the cached Settings object."""
    monkeypatch.chdir(tmp_path)
    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


def _execution(**overrides) -> SimpleNamespace:
    values = {
        "status": "VALID",
        "spread_bps": 5.0,
        "estimated_visible_round_trip_market_drag_pct": 0.2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(execution) -> SimpleNamespace:
    """The gate reads only ``execution_validation`` from its argument."""
    return SimpleNamespace(execution_validation=execution)


def _long_market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="SOLUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=97.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.2,
        technical_score=85,
        trend="bullish",
        underlying_asset="SOL",
        primary_pair="SOLUSD",
        primary_quote_currency="USD",
        ticker_last=100.0,
    )


# ------------------------------------------------------------ AC-001 authority ---


@pytest.mark.parametrize("identity", CONSERVATIVE_IDENTITIES + NON_PRODUCTION)
def test_ac_001_target_attainability_returns_the_canonical_answer(
    identity, monkeypatch
):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-001: one runtime authority."""
    monkeypatch.setenv("APP_ENV", identity)
    config_module.get_settings.cache_clear()
    assert target_attainability._conservative_runtime() is (
        runtime_environment.conservative_runtime()
    )


def test_ac_001_target_attainability_returns_the_canonical_answer_when_unset(
    monkeypatch,
):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-001: absent identity."""
    monkeypatch.delenv("APP_ENV", raising=False)
    config_module.get_settings.cache_clear()
    assert target_attainability._conservative_runtime() is (
        runtime_environment.conservative_runtime()
    ) is True


# ------------------------------------------------------- AC-002 absent identity ---


def test_ac_002_absent_identity_rejects_missing_execution_evidence(monkeypatch):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-002: unset APP_ENV."""
    monkeypatch.delenv("APP_ENV", raising=False)
    config_module.get_settings.cache_clear()

    reasons = target_attainability._long_execution_rejections(_snapshot(None))

    assert "LONG execution evidence is unavailable" in reasons


def test_ac_002_absent_identity_fails_long_qualification(monkeypatch):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-002: the gate is wired in."""
    monkeypatch.delenv("APP_ENV", raising=False)
    config_module.get_settings.cache_clear()

    market = _long_market()
    result = target_attainability.evaluate_target_attainability(
        build_entry_exit_plan(market, "medium"), market
    )

    assert "LONG execution evidence is unavailable" in result.rejection_reasons
    assert result.qualified is False


# ------------------------------------------------------ AC-003 unknown identity ---


@pytest.mark.parametrize("identity", CONSERVATIVE_IDENTITIES)
def test_ac_003_unknown_identity_is_never_permissive(identity, monkeypatch):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-003: unrecognised identity."""
    monkeypatch.setenv("APP_ENV", identity)
    config_module.get_settings.cache_clear()

    assert target_attainability._conservative_runtime() is True
    assert target_attainability._long_execution_rejections(_snapshot(None)) != []


# ---------------------------------------------------- AC-004 production identity ---


@pytest.mark.parametrize("identity", ["production", "PRODUCTION", "Production"])
def test_ac_004_production_rejects_each_missing_evidence_kind(identity, monkeypatch):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-004: production identity."""
    monkeypatch.setenv("APP_ENV", identity)
    config_module.get_settings.cache_clear()

    unavailable = target_attainability._long_execution_rejections(
        _snapshot(_execution(status="UNAVAILABLE", spread_bps=None))
    )
    no_spread = target_attainability._long_execution_rejections(
        _snapshot(_execution(spread_bps=None))
    )
    no_drag = target_attainability._long_execution_rejections(
        _snapshot(_execution(estimated_visible_round_trip_market_drag_pct=None))
    )

    assert any("validation status" in reason for reason in unavailable)
    assert "LONG spread evidence is unavailable" in no_spread
    assert "LONG round-trip drag evidence is unavailable" in no_drag


# ------------------------------------------------- AC-005 non-production identity ---


@pytest.mark.parametrize("identity", NON_PRODUCTION + ("Test", "DEVELOPMENT"))
def test_ac_005_non_production_keeps_documented_developer_behaviour(
    identity, monkeypatch
):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-005: explicit non-production."""
    monkeypatch.setenv("APP_ENV", identity)
    config_module.get_settings.cache_clear()

    assert target_attainability._conservative_runtime() is False
    assert target_attainability._long_execution_rejections(_snapshot(None)) == []


def test_ac_005_bad_evidence_still_rejects_in_non_production(monkeypatch):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-005: bad evidence is universal."""
    monkeypatch.setenv("APP_ENV", "test")
    config_module.get_settings.cache_clear()

    spread = target_attainability._long_execution_rejections(
        _snapshot(_execution(spread_bps=target_attainability.MAX_LONG_SPREAD_BPS + 1.0))
    )
    drag = target_attainability._long_execution_rejections(
        _snapshot(
            _execution(
                estimated_visible_round_trip_market_drag_pct=(
                    target_attainability.MAX_LONG_ROUND_TRIP_DRAG_PCT + 0.5
                )
            )
        )
    )

    assert any("exceeds quality limit" in reason for reason in spread)
    assert any("exceeds quality limit" in reason for reason in drag)


# ------------------------------------------------------- Compose parsing helpers ---


def _service_block(name: str) -> list[str]:
    lines = COMPOSE.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line == f"  {name}:"), None
    )
    assert start is not None, f"service {name} is not declared"
    block: list[str] = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith("    "):
            if line.startswith("  ") or not line.startswith(" "):
                break
        block.append(line)
    return block


def _core_environment_literals() -> dict[str, str]:
    """Return the literal key/value pins of the core service environment block."""
    block = _service_block("ohm-trade-agent")
    start = next(
        (index for index, line in enumerate(block) if line.strip() == "environment:"),
        None,
    )
    assert start is not None, "core service has no environment block"
    literals: dict[str, str] = {}
    for line in block[start + 1:]:
        if not line.strip():
            continue
        if not line.startswith("      "):
            break
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if separator:
            literals[key.strip()] = value.strip().strip('"').strip("'")
    return literals


# ------------------------------------------------ AC-006 production identity pin ---


def test_ac_006_core_service_pins_app_env_as_a_literal():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-006: Compose literal."""
    literals = _core_environment_literals()

    assert literals.get("APP_ENV") == "production"
    # The pin is only authoritative because it sits in the block that overrides
    # env_file; without env_file the claim of precedence would be vacuous.
    assert ".env.core" in "\n".join(_service_block("ohm-trade-agent"))


# ------------------------------------------------- AC-007 literal wins in CI ---


def test_ac_007_ci_proves_the_literal_beats_a_stale_env_core():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-007: precedence is proven."""
    workflow = PYTEST_WORKFLOW.read_text(encoding="utf-8")

    # The validation step writes a stale conflicting value...
    assert "APP_ENV=development" in workflow
    # ...and then asserts the rendered core value, which must be the literal.
    assert '"APP_ENV":"production"' in workflow.replace(" ", "")


# ------------------------------------------------------- AC-008 hermetic suite ---


def test_ac_008_fixture_module_supplies_the_boot_field_and_test_identity():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-008: no ambient dependency."""
    conftest = (APP_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert 'setdefault("WEBHOOK_SECRET"' in conftest
    assert 'setdefault("APP_ENV"' in conftest
    # The defaults are active for this very test, which is what makes the suite
    # independent of the shell that launched it.
    assert os.environ.get("WEBHOOK_SECRET")
    assert os.environ.get("APP_ENV") in NON_PRODUCTION


def test_ac_008_non_ascii_repository_html_is_read_with_explicit_utf8():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-008: Windows codec defect."""
    target = APP_ROOT / "app" / "api" / "strategy_admin.html"
    assert any(byte > 127 for byte in target.read_bytes()), (
        "the guard below is only meaningful while this file is not pure ASCII"
    )

    reader = (APP_ROOT / "tests" / "test_advisory_policy_v2.py").read_text(
        encoding="utf-8"
    )
    assert "read_text()" not in reader
    assert 'encoding="utf-8"' in reader


# -------------------------------------------------- AC-009 authority unchanged ---


def test_ac_009_no_release_posture_or_economic_default_is_widened():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-009: no authority widening."""
    from app.services import economic_quality_gate as gate

    literals = _core_environment_literals()
    assert literals["OPIP_RELEASE_PROFILE"] == "EVIDENCE_SHADOW"
    assert literals["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert literals["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert literals["OPIP_PAPER_V2_MODE"] == "off"
    assert literals["OPIP_COMMITTEE_MODE"] == "off"
    assert not any(key.upper().startswith("KRAKEN_") for key in literals)

    assert gate.MIN_NET_PROFIT == 75.0
    assert gate.MIN_REWARD_TO_RISK == 2.5
    assert gate.PRODUCTION_MAX_CAPITAL_FRACTION == 0.20

    risk_field = config_module.Settings.model_fields["risk_per_trade_pct"]
    assert risk_field.default == 0.35


def test_ac_009_production_identity_closes_the_unauthenticated_legacy_ingress(
    monkeypatch,
):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-009: FIX-003 blast radius.

    Pinning the production identity has a second, deliberate consequence:
    ``/webhooks/tradingview`` was previously reachable in production because the
    absent APP_ENV made the guard read the deployment as a development one and
    grant "development compatibility" without any secret. Under the pinned
    identity that route is refused unless the operator explicitly enables it and
    supplies a separate, long secret. This test pins that posture so the
    consequence cannot drift silently in either direction.
    """
    from app.services.legacy_tradingview_guard import (
        evaluate_legacy_tradingview_request,
    )

    monkeypatch.delenv("TRADINGVIEW_V1_ENABLED", raising=False)
    monkeypatch.delenv("TRADINGVIEW_V1_WEBHOOK_SECRET", raising=False)

    denied = evaluate_legacy_tradingview_request(
        app_env="production", supplied_legacy_secret=None
    )
    assert denied.allowed is False
    assert denied.status_code == 404

    dev = evaluate_legacy_tradingview_request(
        app_env="development", supplied_legacy_secret=None
    )
    assert dev.allowed is True

    # The documented escape hatch still works, and only with its own secret.
    monkeypatch.setenv("TRADINGVIEW_V1_ENABLED", "true")
    assert (
        evaluate_legacy_tradingview_request(
            app_env="production", supplied_legacy_secret="anything"
        ).status_code
        == 503
    )
    monkeypatch.setenv("TRADINGVIEW_V1_WEBHOOK_SECRET", "v" * 40)
    allowed = evaluate_legacy_tradingview_request(
        app_env="production", supplied_legacy_secret="v" * 40
    )
    assert allowed.allowed is True
    assert allowed.inject_operator_secret is True


def test_ac_009_local_learning_stays_remote_only_under_the_production_identity(
    monkeypatch,
):
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-009: production identity."""
    from app.services import learning_scheduler

    monkeypatch.setenv("APP_ENV", "production")
    assert learning_scheduler.conservative_runtime() is True


# ------------------------------------------------------------ AC-010 governance ---


def test_ac_010_contract_declares_the_active_increment():
    """ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1/AC-010: scope is declared."""
    pointer = (APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT").read_text(
        encoding="utf-8"
    ).strip()
    assert pointer == "ATDD-OPIP-RUNTIME-AUTHORITY-FAILCLOSED-v1"

    contract = (
        APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{pointer}.md"
    ).read_text(encoding="utf-8")
    assert contract.startswith(f"INCREMENT:\n{pointer}")
    for section in (
        "OWNER-APPROVED INTENT:",
        "ARCHITECTURE REFERENCES:",
        "APPROVED ACCEPTANCE CRITERIA:",
        "EXPLICITLY OUT OF SCOPE:",
        "FROZEN BOUNDARIES:",
        "ACCEPTANCE TEST TRACEABILITY:",
        "IMPLEMENTATION MAP:",
        "DEFERRED DISCOVERIES:",
        "UNAPPROVED SCOPE CHANGES:",
    ):
        assert section in contract
    assert contract.rstrip().endswith("NONE")


