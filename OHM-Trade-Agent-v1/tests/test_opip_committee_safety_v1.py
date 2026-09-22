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
    "evaluation.py",
    "metrics.py",
    "opinion.py",
    "outbound.py",
    "pricing.py",
    "prospective.py",
    "serialization.py",
    "settings.py",
    "ledger.py",
    "attribution.py",
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
    """No record may carry a field that grants, rather than denies, authority."""
    from app.opip.committee import contracts, evaluation

    # The only authority-adjacent field permitted is an explicit denial flag.
    denial_flags = {"trade_authority_changed", "measurement_only"}
    for record in (
        contracts.CommitteeCase,
        contracts.ProviderCallOutcome,
        contracts.CommitteeCaseOutcome,
        contracts.StructuredOpinion,
        contracts.CommitteePolicy,
        evaluation.EvaluationReport,
        evaluation.ArmEvaluation,
    ):
        fields = record.__dataclass_fields__
        for field in fields:
            if "authority" in field:
                assert field in denial_flags, (record, field)
        for token in ("admission", "ranking", "sizing", "promote", "winner", "champion"):
            assert not any(token in field for field in fields), (record, token)

    flags = evaluation.EvaluationReport.__dataclass_fields__
    assert flags["trade_authority_changed"].default is False
    assert flags["measurement_only"].default is True


def test_bake_off_can_never_promote_or_rank_for_action():
    from datetime import datetime, timezone

    from app.opip.committee.contracts import CaseType, EvaluationPhase
    from app.opip.committee.evaluation import EvaluationReport

    fields = EvaluationReport.__dataclass_fields__
    assert fields["automatic_promotion"].default is False
    assert fields["trade_authority_changed"].default is False
    assert fields["measurement_only"].default is True

    # The contract itself refuses anything that would read as promotion.
    empty_arms: tuple = ()
    generated_at = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        EvaluationReport(
            experiment_id="x",
            phase=EvaluationPhase.RETROSPECTIVE,
            case_type=CaseType.MARKET_OPPORTUNITY,
            generated_at=generated_at,
            case_count=1,
            minimum_samples=1,
            arms=empty_arms,
            provenance=None,  # type: ignore[arg-type]
        )


def test_pricing_is_configuration_and_not_a_hardcoded_vendor_table():
    """Cost must come from configuration, never from a table in source."""
    from app.opip.committee.pricing import PriceBook

    # No built-in prices: an unconfigured price book is empty, not defaulted.
    assert PriceBook().is_empty
    assert len(PriceBook.from_env({})) == 0

    tree = ast.parse((COMMITTEE_ROOT / "pricing.py").read_text(encoding="utf-8"))
    allowed_module_constants = {
        "COMMITTEE_PRICES_ENV",
        "TOKENS_PER_PRICING_UNIT",
        "MICROUNITS_PER_UNIT",
        "COST_UNKNOWN",
        "__all__",
    }
    unexpected: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id not in allowed_module_constants:
                        unexpected.append(target.id)
    assert unexpected == [], f"pricing must not define extra constants: {unexpected}"


def test_evaluation_module_names_contain_no_promotion_surface():
    """No function or attribute may read as promoting, ranking, or choosing."""
    forbidden = ("promote", "winner", "best_", "champion", "rank")
    offenders: list[str] = []
    for name in ("evaluation.py", "attribution.py"):
        tree = ast.parse((COMMITTEE_ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                if any(token in lowered for token in forbidden):
                    offenders.append(f"{name}:{node.name}")
            elif isinstance(node, ast.Attribute):
                lowered = node.attr.lower()
                if any(token in lowered for token in ("promote", "winner", "champion")):
                    offenders.append(f"{name}:{node.attr}")
    assert offenders == [], offenders


def test_attribution_carries_an_explicit_non_promotion_disposition():
    from app.opip.committee.attribution import AttributionReport

    fields = AttributionReport.__dataclass_fields__
    assert fields["automatic_promotion"].default is False
    assert fields["trade_authority_changed"].default is False
    assert fields["advisory_only"].default is True
    assert fields["measurement_only"].default is True


def test_committee_reports_are_never_wired_to_a_production_consumer():
    """Nothing outside the plane may read committee reports yet."""
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if COMMITTEE_ROOT in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if "COMMITTEE-ATTRIBUTION" in text or "COMMITTEE-EVALUATION" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], offenders


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


def test_prospective_plane_is_deterministic_and_separated():
    """The prospective protocol takes every timestamp as an argument."""
    source = (COMMITTEE_ROOT / "prospective.py").read_text(encoding="utf-8")
    for token in ("datetime.now(", "utcnow(", "time.time("):
        assert token not in source, token
    # Phase separation is structural: the module records prospective evidence
    # and refuses to label a retrospective observation as prospective.
    assert "HindsightLeakageError" in source
    assert "EvaluationPhase.PROSPECTIVE" in source


def test_no_committee_module_schedules_or_calls_itself():
    """The plane is inert: nothing self-schedules or starts a background task."""
    for path in _committee_files():
        source = path.read_text(encoding="utf-8")
        for token in (
            "asyncio.create_task",
            "threading.Thread",
            "subprocess",
            "os.system",
            "crontab",
            "APScheduler",
        ):
            assert token not in source, (path.name, token)


def test_committee_never_evaluates_or_executes_model_text():
    """Model output is data: it is never evaled, executed, or shelled."""
    for path in _committee_files():
        source = path.read_text(encoding="utf-8")
        for token in ("eval(", "exec(", "os.popen", "yaml.load", "pickle.load"):
            assert token not in source, (path.name, token)


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
