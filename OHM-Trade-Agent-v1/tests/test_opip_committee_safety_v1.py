"""Architectural safety proof for the Intelligence Committee plane.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These are structural checks, not documentary claims. They prove that the
committee plane:

* cannot import an exchange, order, registry, notification, or execution module;
* cannot import a model vendor SDK at all (provider access is injected);
* reads no wall clock in its pure, deterministic modules;
* writes only inside its own data directory;
* is not imported by any runtime root, so it cannot sit in a trading path;
* declares non-authority explicitly, and its prompt asserts the same.

If a future change gives the committee a path to trading authority, these tests
fail rather than the change passing review silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
COMMITTEE_ROOT = APP_ROOT / "opip" / "committee"
COMMITTEE_IMPORT_ROOT = "app.opip.committee"

#: The exact runtime roots that must never reach into the committee plane.
RUNTIME_ROOTS = (
    APP_ROOT / "services",
    APP_ROOT / "jobs",
    APP_ROOT / "api",
    APP_ROOT / "opip" / "discovery",
    APP_ROOT / "opip" / "decision",
    APP_ROOT / "opip" / "risk",
)

#: Import prefixes the committee plane may never use.
FORBIDDEN_IMPORT_PREFIXES = (
    "app.exchanges",
    "app.scanner",
    "app.api",
    "app.jobs",
    "app.notifications",
    "app.opip.decision",
    "app.opip.risk",
    "app.opip.data_platform",
    "app.opip.canonical",
    "openai",
    "anthropic",
    "google.generativeai",
    "google.generativeai",
    "cohere",
    "mistralai",
    "litellm",
)

#: Privileged modules that carry order, lifecycle, or notification authority.
FORBIDDEN_MODULE_FRAGMENTS = (
    "kraken_private",
    "order_intent_registry",
    "active_trade_registry",
    "pending_setup_registry",
    "paper_trade_registry",
    "trade_monitor",
    "paper_trade_engine",
    "paper_trade_control",
    "recommendation_gate",
    "telegram",
    "trade_cli",
    "risk",
)

#: Text tokens that would indicate execution or notification capability.
FORBIDDEN_TOKENS = (
    "AddOrder",
    "CancelOrder",
    "/private/",
    "register_trade",
    "confirm_entry",
    "send_trade_plan",
    "publish_qualified_long",
    "enroll_paper_opportunity",
    "place_order",
    "create_order",
    "submit_order",
    "cancel_order",
    "modify_order",
)

#: ML/framework dependencies the repository deliberately keeps out of app/opip.
FORBIDDEN_ML_FRAGMENTS = (
    "xgboost",
    "lightgbm",
    "sklearn",
    "scikit",
    "torch",
    "tensorflow",
)

#: Modules whose behaviour must be a pure function of their inputs.
PURE_MODULES = (
    "contracts.py",
    "evidence.py",
    "opinion.py",
    "outbound.py",
    "serialization.py",
    "ledger.py",
)

#: The single infrastructure helper the store may use for cross-process locking.
ALLOWED_SERVICE_IMPORTS = ("app.services.registry_io",)


def _committee_files() -> list[Path]:
    return sorted(COMMITTEE_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_committee_package_exists_and_is_not_empty():
    assert _committee_files(), "committee package must exist"


def test_committee_never_imports_a_forbidden_plane():
    offenders: list[str] = []
    for path in _committee_files():
        for name in _imported_modules(path):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                if name == prefix or name.startswith(prefix + "."):
                    offenders.append(f"{path.name}:{name}")
    assert offenders == [], f"committee must not import: {offenders}"


def test_committee_uses_no_service_module_except_locking():
    offenders: list[str] = []
    for path in _committee_files():
        for name in _imported_modules(path):
            if not name.startswith("app.services"):
                continue
            if not any(
                name == allowed or name.startswith(allowed + ".")
                for allowed in ALLOWED_SERVICE_IMPORTS
            ):
                offenders.append(f"{path.name}:{name}")
    assert offenders == [], f"unexpected service imports: {offenders}"


def test_committee_never_imports_a_privileged_module():
    offenders: list[str] = []
    for path in _committee_files():
        for name in _imported_modules(path):
            if name.startswith(("app.opip.committee", "app.opip.decision_intelligence")):
                continue
            lowered = name.lower()
            for fragment in FORBIDDEN_MODULE_FRAGMENTS:
                if fragment in lowered:
                    offenders.append(f"{path.name}:{name}")
    assert offenders == [], f"privileged module reached: {offenders}"


def test_committee_source_contains_no_execution_or_notification_token():
    offenders: list[str] = []
    for path in _committee_files():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == [], f"execution token present: {offenders}"


def test_committee_introduces_no_ml_dependency():
    for path in _committee_files():
        lowered = path.read_text(encoding="utf-8").lower()
        for fragment in FORBIDDEN_ML_FRAGMENTS:
            assert fragment not in lowered, f"{path.name} references {fragment}"


def test_no_runtime_root_reaches_into_the_committee_plane():
    """The committee is not wired into any production trading path."""
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for name in _imported_modules(path):
                if name == COMMITTEE_IMPORT_ROOT or name.startswith(
                    COMMITTEE_IMPORT_ROOT + "."
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert offenders == [], f"runtime roots must not import the committee: {offenders}"


def test_no_application_module_outside_the_package_imports_the_committee():
    """Nothing in app/ consumes the plane yet, so it cannot alter behaviour."""
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if COMMITTEE_ROOT in path.parents:
            continue
        for name in _imported_modules(path):
            if name == COMMITTEE_IMPORT_ROOT or name.startswith(
                COMMITTEE_IMPORT_ROOT + "."
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert offenders == []


def test_pure_committee_modules_read_no_wall_clock():
    for name in PURE_MODULES:
        source = (COMMITTEE_ROOT / name).read_text(encoding="utf-8")
        assert "datetime.now(" not in source, name
        assert "time.time(" not in source, name
        assert "utcnow(" not in source, name


def test_committee_declares_non_authority():
    from app.opip import committee

    assert committee.AUTHORITATIVE is False
    assert committee.CAN_PLACE_ORDERS is False
    assert committee.MEASUREMENT_ONLY is True


def test_committee_contracts_expose_no_authority_field():
    from app.opip.committee import contracts

    for record in (
        contracts.CommitteeCase,
        contracts.ProviderCallOutcome,
        contracts.CommitteeCaseOutcome,
        contracts.StructuredOpinion,
        contracts.CommitteePolicy,
    ):
        fields = record.__dataclass_fields__
        for token in ("authority", "execution", "admission", "ranking", "sizing"):
            assert not any(token in field for field in fields), (record, token)


def test_committee_never_constructs_a_wire_request_outside_the_runtime():
    """Screening cannot be bypassed: only the runtime builds a wire request."""
    builders: list[str] = []
    for path in _committee_files():
        source = path.read_text(encoding="utf-8")
        if "ProviderWireRequest(" in source:
            builders.append(path.name)
    assert builders == ["runtime.py"], builders


def test_the_runtime_screens_the_payload_before_it_is_wired():
    source = (COMMITTEE_ROOT / "runtime.py").read_text(encoding="utf-8")
    assert "screen_model_bound_view(" in source
    assert "model_bound_view()" in source


def test_provider_adapters_never_receive_the_case_or_snapshot():
    """A seat cannot receive anything but its own screened wire request."""
    source = (COMMITTEE_ROOT / "providers.py").read_text(encoding="utf-8")
    assert "model_bound_view" not in source
    assert "EvidenceSnapshot" not in source
    assert "CommitteeCase" not in source


def test_committee_writes_only_inside_its_own_data_directory():
    from app.opip.committee.store import COMMITTEE_DIR

    assert COMMITTEE_DIR == Path("/app/data/opip/committee")
    for path in _committee_files():
        text = path.read_text(encoding="utf-8")
        for literal in ("/app/data/", "/var/lib/"):
            if literal in text and path.name != "store.py":
                pytest.fail(f"{path.name} declares a data path literal")


def test_committee_ships_dark_and_only_shadow_is_permitted():
    from types import SimpleNamespace

    from app.core.config import Settings

    from app.opip.committee.settings import (
        committee_shadow_enabled,
        resolve_committee_cost_ceiling,
        resolve_committee_mode,
    )

    def _ns(**kwargs):
        return SimpleNamespace(**kwargs)

    assert Settings.model_fields["opip_committee_mode"].default == "off"
    assert Settings.model_fields["opip_committee_max_estimated_cost_microunits"].default == 0

    assert resolve_committee_mode(None) in {"off", "shadow"}
    assert resolve_committee_mode(_ns(opip_committee_mode="off")) == "off"
    assert resolve_committee_mode(_ns(opip_committee_mode="shadow")) == "shadow"
    # Malformed or unknown configuration resolves to off, never to enabled.
    assert resolve_committee_mode(_ns(opip_committee_mode="active")) == "off"
    assert resolve_committee_mode(_ns(opip_committee_mode="")) == "off"
    assert committee_shadow_enabled(_ns(opip_committee_mode="active")) is False
    assert committee_shadow_enabled(_ns(opip_committee_mode="shadow")) is True

    assert resolve_committee_cost_ceiling(_ns(opip_committee_max_estimated_cost_microunits=0)) is None
    assert resolve_committee_cost_ceiling(_ns(opip_committee_max_estimated_cost_microunits=500)) == 500
    assert resolve_committee_cost_ceiling(_ns(opip_committee_max_estimated_cost_microunits="bad")) is None


def test_committee_prompt_asserts_non_authority():
    from app.opip.committee.runtime import COMMITTEE_SYSTEM_PROMPT

    lowered = COMMITTEE_SYSTEM_PROMPT.lower()
    assert "no authority" in lowered
    assert "cannot place" in lowered
    for token in ("place_order", "submit order", "execute the trade"):
        assert token not in lowered
