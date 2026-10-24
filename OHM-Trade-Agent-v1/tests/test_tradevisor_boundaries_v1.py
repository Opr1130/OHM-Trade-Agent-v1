"""ATDD: Tradevisor boundary and regression checks.

Covers ATDD-35 (no execution path), ATDD-43 (F11 protection independence),
ATDD-46 (Wave 8.2 route unchanged), ATDD-47 (native alerts unchanged),
ATDD-49 (existing kernels remain authoritative), ATDD-50 (release profiles),
ATDD-52 (no default activation).

These are structural assertions on purpose: they fail if a future change
quietly routes Tradevisor evidence into an order, a selector, or a second
canonical writer.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.jobs import run_cycle
from app.services import notification_policy, release_profiles
from app.services import tradevisor_inbox as inbox

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def source(relative: str) -> str:
    return (APP / relative).read_text(encoding="utf-8")


TRADEVISOR_MODULES = (
    "api/tradevisor.py",
    "services/tradevisor_ingress.py",
    "services/tradevisor_inbox.py",
    "services/tradevisor_market.py",
    "services/tradevisor_evaluation.py",
    "services/tradevisor_notifier.py",
    "services/tradevisor_pipeline.py",
    "services/tradevisor_learning.py",
    "jobs/drain_tradevisor_inbox.py",
    "jobs/report_tradevisor_learning.py",
    "models/tradevisor.py",
)

#: Names that would mean Tradevisor gained an execution or admission path.
FORBIDDEN_CAPABILITIES = (
    "AddOrder",
    "CancelOrder",
    "register_order_intent",
    "confirm_entry",
    "place_order",
    "submit_order",
    "paper_v2_execution",
    "select_candidates",
    "select_directional_candidates",
    "canonical_writer",
    "writer_service",
)


def test_atdd35_no_tradevisor_module_can_place_or_confirm_an_order():
    for module in TRADEVISOR_MODULES:
        text = source(module)
        for capability in FORBIDDEN_CAPABILITIES:
            assert capability not in text, f"{module} references {capability}"


def test_atdd35_webhook_ack_path_performs_no_downstream_io():
    """The ack path may reuse the shared bounded-body reader and nothing else.

    ``app.api.routes`` is imported solely for ``_read_bounded_body`` (an
    accepted, reviewed coupling to the existing Wave 8.2 boundary helper). No
    market, AI, Telegram or evaluation module may be reachable from the route.
    """
    text = source("api/tradevisor.py")
    for forbidden in (
        "telegram_delivery",
        "market_scanner",
        "tradevisor_market",
        "tradevisor_pipeline",
        "tradevisor_evaluation",
        "tradevisor_notifier",
        "openai",
        "KrakenClient",
    ):
        assert forbidden not in text, f"webhook ack path imports {forbidden}"

    tree = ast.parse(text)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    allowed = {
        "__future__",
        "json",
        "time",
        "collections",
        "datetime",
        "fastapi",
        "app.api.routes",  # _read_bounded_body only
        "app.core.config",
        "app.services.secret_auth",
        "app.services.tradevisor_inbox",
        "app.services.tradevisor_ingress",
    }
    assert imported <= allowed, imported - allowed


def test_atdd35_market_evidence_is_public_data_only():
    text = source("services/tradevisor_market.py")
    for forbidden in ("AddOrder", "Balance", "CancelOrder", "api_key", "API-Sign"):
        assert forbidden not in text


def test_atdd49_geometry_and_capital_owners_remain_authoritative():
    text = source("services/tradevisor_evaluation.py")
    assert "from app.services.entry_exit_advisor import" in text
    assert "from app.services.target_attainability import" in text
    assert "from app.services.economic_quality_gate import" in text
    assert "from app.services.capital_allocation import" in text
    # No local geometry formula: the R4 kernel stays the single owner.
    for forbidden in ("stop_distance =", "target_1 =", "risk_per_unit ="):
        assert forbidden not in text


def test_atdd47_tradevisor_reuses_the_existing_notification_policy_class():
    assert "ACTIONABLE_TRADE" in notification_policy.CRITICAL_EVENTS
    text = source("services/tradevisor_notifier.py")
    assert "ACTIONABLE_TRADE" in text
    # A new critical class would perturb existing attention budgeting.
    assert "CRITICAL_EVENTS" not in text


def test_atdd46_wave_8_2_route_and_schema_are_untouched():
    routes = source("api/routes.py")
    assert '@router.post("/webhooks/tradingview/v2"' in routes
    assert "TradingViewSignalV2" in routes
    tradevisor = source("api/tradevisor.py")
    # Isolation: no shared schema, inbox, or verification value with Wave 8.2.
    for forbidden in (
        "TradingViewSignalV2",
        "tradingview_inbox",
        "TRADINGVIEW_VERIFICATION_HEADER",
        "tradingview_webhook_token",
        "tradingview_internal_verification_value",
    ):
        assert forbidden not in tradevisor


def test_atdd50_release_profiles_cannot_activate_tradevisor():
    baseline = release_profiles.resolve_release_profile("SAFE_BASELINE")
    modes = release_profiles.render_profile_environment("SAFE_BASELINE")
    for key in (
        "OPIP_FEATURE_BUS_MODE",
        "OPIP_CANONICAL_WRITER_MODE",
        "OPIP_TARGET_SPINE_MODE",
        "OPIP_PAPER_V2_MODE",
        "OPIP_COMMITTEE_MODE",
    ):
        assert key in modes
    assert baseline["allowed_modes"]["OPIP_PAPER_V2_MODE"] == "off"
    # Tradevisor is deliberately absent from the release-profile contract: it
    # cannot be activated by choosing a profile.
    assert not any("TRADEVISOR" in key.upper() for key in modes)
    verdict, problems = release_profiles.validate_profile_contract(
        "SAFE_BASELINE", requested_modes={"OPIP_FEATURE_BUS_MODE": "active"}
    )
    assert verdict is False
    assert any("OPIP_FEATURE_BUS_MODE" in problem for problem in problems)


def test_atdd52_no_committed_environment_enables_the_ingress():
    defaulted = Settings(_env_file=None, webhook_secret="x" * 12)
    assert defaulted.opip_tradevisor_enabled is False
    for name in ("docker-compose.yml", "docker-compose.paper.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "OPIP_TRADEVISOR_ENABLED" not in text


def test_atdd43_tradevisor_phase_runs_after_protection():
    text = source("jobs/run_cycle.py")
    protection = text.index('_cycle_phase("ACTIVE_POSITION_PROTECTION")')
    advisory = text.index('_cycle_phase("TRADEVISOR_ADVISORY")')
    assert protection < advisory
    # The *call site* must follow the protection phase, not merely exist.
    assert text.index("_run_tradevisor_fail_open()", advisory) > advisory


def test_atdd43_phase_is_inert_when_disabled(monkeypatch):
    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: SimpleNamespace(opip_tradevisor_enabled=False),
    )

    def explode(*_args, **_kwargs):
        raise AssertionError("drain must not run while the ingress is dark")

    monkeypatch.setattr(inbox, "process_queued_events", explode)
    run_cycle._run_tradevisor_fail_open()  # must return without touching the inbox


def test_atdd43_phase_failure_never_aborts_the_cycle(monkeypatch):
    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: SimpleNamespace(opip_tradevisor_enabled=True),
    )
    monkeypatch.setattr(inbox, "pending_count", lambda: 1)

    def boom(**_kwargs):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(inbox, "process_queued_events", boom)
    run_cycle._run_tradevisor_fail_open()  # must swallow and report


def test_atdd47_drain_job_declares_no_authority(capsys, monkeypatch):
    from app.jobs import drain_tradevisor_inbox as job

    monkeypatch.setattr(
        job, "get_settings", lambda: SimpleNamespace(opip_tradevisor_enabled=False)
    )
    job.main()
    printed = capsys.readouterr().out
    assert "Trade authority changed: False" in printed


@pytest.mark.parametrize("module", TRADEVISOR_MODULES)
def test_every_tradevisor_module_parses_and_imports_no_execution_surface(module):
    tree = ast.parse(source(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in (
        "ccxt",
        "app.services.order_intent_registry",
        "app.services.kraken_reconciliation",
    ):
        assert banned not in imported
