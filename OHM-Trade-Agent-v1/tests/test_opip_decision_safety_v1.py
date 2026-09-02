"""Structural safety invariants for the O'Pip Decision Engine (Build 1).

Build 1 adds observation. These tests exist to prove it added nothing else:
the shadow engine cannot change a production admission, cannot reach the
exchange, cannot escalate a permission, cannot create a counterfactual or
futures position, and no model influences any trading decision.
"""

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

from app.jobs import scan_opportunities
from app.opip.decision import (
    comparison,
    engine,
    explanations,
    funnel,
    gates,
    identity,
    models,
    observer,
    store,
    summary,
    thresholds,
    versioning,
)
from app.opip.decision.engine import OPipDecisionEngine
from app.opip.decision.models import DecisionOutcome
from app.opip.decision.observer import NullScanObserver, OPipScanObserver

from tests.test_profit_ranking import _configure_pipeline


OPIP_PACKAGE = Path("app/opip")
OPIP_MODULES = (
    comparison,
    engine,
    explanations,
    funnel,
    gates,
    identity,
    models,
    observer,
    store,
    summary,
    thresholds,
    versioning,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


#: Identity fields derived from the scan's wall-clock decision timestamp. Two
#: separate runs legitimately differ here, so they are excluded when comparing
#: instrumented and uninstrumented production output.
_TIME_DERIVED_ALERT_KEYS = (
    "signal_id",
    "journey_id",
    "_lineage_episode_id",
)


def _stable_alerts(alerts) -> list[dict]:
    return [
        {
            key: value
            for key, value in alert.items()
            if key not in _TIME_DERIVED_ALERT_KEYS
        }
        for alert in alerts
    ]


def _opip_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(OPIP_PACKAGE.rglob("*.py"))
    )


# ------------------------------------------------------- decision isolation --


def test_shadow_evaluation_cannot_change_the_production_decision(monkeypatch):
    """Running the funnel must produce byte-identical production output."""
    specifications = [
        {"symbol": "AAAUSD", "move": 9.0, "confidence": 95},
        {"symbol": "BBBUSD", "move": 7.0, "confidence": 90},
        {"symbol": "CCCUSD", "move": 5.0, "target_pass": False},
    ]
    events, ranked_symbols, sent = _configure_pipeline(monkeypatch, specifications)
    scan_opportunities.main()
    instrumented = (list(events), list(ranked_symbols), _stable_alerts(sent))

    monkeypatch.undo()
    events2, ranked2, sent2 = _configure_pipeline(monkeypatch, specifications)
    monkeypatch.setattr(
        scan_opportunities,
        "build_scan_observer",
        lambda **kwargs: NullScanObserver(),
    )
    scan_opportunities.main()
    uninstrumented = (list(events2), list(ranked2), _stable_alerts(sent2))

    assert instrumented[0] == uninstrumented[0]
    assert instrumented[1] == uninstrumented[1]
    assert instrumented[2] == uninstrumented[2]
    assert instrumented[1], "harness must actually rank something"
    assert instrumented[2], "harness must actually send something"


def test_a_divergent_shadow_verdict_does_not_reach_production(monkeypatch):
    """Even a shadow engine that qualifies everything changes no alert."""
    specifications = [{"symbol": "CCCUSD", "move": 5.0, "target_pass": False}]
    _events, ranked_symbols, sent = _configure_pipeline(monkeypatch, specifications)

    class _AlwaysQualified:
        def __init__(self, **kwargs):
            pass

        def evaluate(self, evidence):
            from app.opip.decision.models import AdmissionDecision

            return AdmissionDecision(
                candidate_id="forced", episode_id=None, asset="CCC", pair="CCCUSD",
                market_type="SPOT", direction="LONG", decided_at=NOW.isoformat(),
                decision=DecisionOutcome.QUALIFIED,
            )

    monkeypatch.setattr(observer, "OPipDecisionEngine", _AlwaysQualified)
    scan_opportunities.main()
    assert ranked_symbols == []
    assert sent == []


def test_observer_does_not_mutate_the_candidate_snapshots():
    from tests.test_opip_decision_engine_v1 import execution, snapshot

    candidate = snapshot()
    candidate.execution_validation = execution()
    before = {
        field: getattr(candidate, field)
        for field in (
            "symbol", "last_price", "technical_score", "trade_direction",
            "execution_validation", "price_movement_signal",
        )
    }
    scan_observer = OPipScanObserver(
        snapshots=[candidate], decision_at=NOW, account_equity=10_000.0,
        telemetry_enabled=False,
    )
    scan_observer.register_candidates([candidate])
    scan_observer.record_margin([candidate])
    scan_observer.record_execution([candidate])
    scan_observer.record_cross_market([candidate])
    scan_observer.record_reference([candidate])
    scan_observer.finalize(scan_context={}, print_summary=False)

    after = {field: getattr(candidate, field) for field in before}
    assert before == after


# ------------------------------------------------------- exchange isolation --


def test_opip_modules_never_reference_private_exchange_or_order_endpoints():
    source = _opip_source()
    forbidden = (
        "kraken_private",
        "KrakenPrivateClient",
        "AddOrder",
        "CancelOrder",
        "/private/",
        "order_intent_registry",
        "register_trade",
        "confirm_entry",
    )
    for name in forbidden:
        assert name not in source, f"{name} must not appear in app/opip"


def test_opip_modules_import_no_exchange_client_module():
    """No O'Pip module may import an exchange transport at all."""
    banned_prefixes = ("app.exchanges",)
    for path in sorted(OPIP_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(banned_prefixes), (
                    f"{path} imports exchange module {name}"
                )


def test_decision_engine_exposes_no_order_placement_surface():
    """No callable on the engine may look like an execution operation."""
    callables = [
        name
        for name in dir(OPipDecisionEngine)
        if not name.startswith("_")
        and callable(getattr(OPipDecisionEngine, name, None))
    ]
    forbidden_fragments = (
        "order", "buy", "sell", "trade", "execute", "submit", "cancel",
    )
    for name in callables:
        assert not any(fragment in name.lower() for fragment in forbidden_fragments), name
    assert OPipDecisionEngine.AUTHORITATIVE is False
    assert OPipDecisionEngine.CAN_PLACE_ORDERS is False


def test_shadow_gate_evaluation_issues_no_exchange_request(monkeypatch):
    """Constructing a Kraken client anywhere under evaluation is a failure."""
    import app.exchanges.kraken as kraken

    def _explode(*args, **kwargs):
        raise AssertionError("shadow evaluation must not build an exchange client")

    monkeypatch.setattr(kraken, "KrakenClient", _explode)

    from tests.test_opip_decision_engine_v1 import execution, snapshot

    candidate = snapshot(trade_direction="SHORT", margin_validation_status="ELIGIBLE")
    candidate.execution_validation = execution()
    decision = OPipDecisionEngine(
        account_equity=10_000.0, decision_at=NOW
    ).evaluate(engine.CandidateEvidence(snapshot=candidate))
    assert decision.decision in {
        DecisionOutcome.REJECTED,
        DecisionOutcome.QUALIFIED,
        DecisionOutcome.OPERATIONAL_FAILURE,
    }


# ---------------------------------------------------- permission invariance --


def test_enabling_telemetry_changes_no_authority_or_safety_state(tmp_path, monkeypatch):
    from app.services import paper_trade_control

    control = tmp_path / "control.json"
    monkeypatch.setattr(paper_trade_control, "CONTROL_FILE", control)
    monkeypatch.setattr(
        paper_trade_control, "LOCK_FILE", tmp_path / ".paper_control.lock"
    )
    before = paper_trade_control.paper_trade_enabled()

    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "true")
    assert store.opip_funnel_telemetry_enabled() is True

    from tests.test_opip_decision_engine_v1 import execution, snapshot

    candidate = snapshot()
    candidate.execution_validation = execution()
    scan_observer = OPipScanObserver(
        snapshots=[candidate], decision_at=NOW, account_equity=10_000.0,
        telemetry_enabled=False,
    )
    scan_observer.register_candidates([candidate])
    scan_observer.finalize(scan_context={}, print_summary=False)

    assert paper_trade_control.paper_trade_enabled() is before
    assert not control.exists()


def test_telemetry_flag_is_measurement_only_and_dark_by_default(monkeypatch):
    monkeypatch.delenv("OPIP_FUNNEL_TELEMETRY_ENABLED", raising=False)
    assert store.opip_funnel_telemetry_enabled() is False
    for value in ("true", "1", "on", "yes"):
        monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", value)
        assert store.opip_funnel_telemetry_enabled() is True
    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "maybe")
    assert store.opip_funnel_telemetry_enabled() is False


def test_opip_writes_only_inside_its_own_qualification_directory():
    assert store.QUALIFICATION_DIR == Path("/app/data/opip/qualification")
    for path in (
        store.FUNNEL_EVENTS_FILE,
        store.SCAN_SUMMARIES_FILE,
        store.DEAD_LETTER_FILE,
    ):
        assert store.QUALIFICATION_DIR in path.parents


# ------------------------------------------------- counterfactual isolation --


def test_build_1_never_assigns_the_counterfactual_outcome():
    """Eligibility is recorded; a counterfactual decision is never produced."""
    source = _opip_source()
    tree_hits = [
        node
        for path in sorted(OPIP_PACKAGE.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
        and node.attr == "COUNTERFACTUAL_ELIGIBLE"
    ]
    # The enum member is declared for a future build and may be referenced in
    # tests, but no O'Pip module may assign it as a terminal outcome.
    assert tree_hits == []
    assert "COUNTERFACTUAL_ELIGIBLE" in source  # the enum member still exists


def test_opip_creates_no_counterfactual_or_paper_trade():
    source = _opip_source()
    forbidden = (
        "enroll_paper_opportunity",
        "publish_qualified_long",
        "counterfactual_gate_audit",
        "record_paper_admission(",
        "set_paper_trade_enabled",
    )
    for name in forbidden:
        assert name not in source


def test_paper_stage_records_eligibility_only():
    from tests.test_opip_decision_engine_v1 import execution, snapshot
    from types import SimpleNamespace

    candidate = snapshot()
    candidate.execution_validation = execution()
    scan_observer = OPipScanObserver(
        snapshots=[candidate], decision_at=NOW, account_equity=10_000.0,
        telemetry_enabled=False,
    )
    scan_observer.register_candidates([candidate])
    ranked = [
        SimpleNamespace(
            rank=1,
            profit_ranking=SimpleNamespace(total_score=80.0),
            opportunity=SimpleNamespace(snapshot=candidate, alert={}),
        )
    ]
    eligible = scan_observer.record_paper_admission_eligibility(
        ranked, paper_enabled=False
    )
    assert eligible == 0
    eligible_on = scan_observer.record_paper_admission_eligibility(
        ranked, paper_enabled=True
    )
    assert eligible_on == 1


# -------------------------------------------------------- futures isolation --


def test_opip_modules_reference_no_futures_surface():
    source = _opip_source().lower()
    for name in ("futures", "perpetual", "derivatives_position", "leverage_order"):
        assert name not in source


# ------------------------------------------------------------- ML isolation --


def test_no_ml_dependency_is_introduced():
    source = _opip_source().lower()
    for name in ("xgboost", "lightgbm", "shap", "sklearn", "scikit", "torch", "tensorflow"):
        assert name not in source
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    for name in ("xgboost", "lightgbm", "shap", "scikit-learn", "torch", "tensorflow"):
        assert name not in requirements


def test_model_version_is_declared_but_unset():
    stamp = versioning.version_stamp()
    assert stamp["model_version"] is None
    assert stamp["feature_schema_version"] is None


def test_gate_policy_mirrors_production_thresholds_without_redefining_them():
    """The mirror must alias production constants, never restate a number."""
    import app.services.recommendation_gate as recommendation_gate
    import app.services.economic_quality_gate as economic_quality_gate
    import app.services.target_attainability as target_attainability

    assert thresholds.AI_MIN_CONFIDENCE is recommendation_gate.MIN_CONFIDENCE
    assert (
        thresholds.PRODUCTION_MAX_CAPITAL_FRACTION
        is economic_quality_gate.PRODUCTION_MAX_CAPITAL_FRACTION
    )
    assert (
        thresholds.TARGET_MIN_QUALIFYING_SCORE
        is target_attainability.MIN_QUALIFYING_SCORE
    )


def test_production_thresholds_are_unchanged_by_this_build():
    """Pin the exact live values so a threshold edit cannot ride along."""
    import app.services.recommendation_gate as recommendation_gate
    import app.services.economic_quality_gate as economic_quality_gate
    import app.services.target_attainability as target_attainability
    import app.scanner.candidates as candidates_module

    assert recommendation_gate.MIN_CONFIDENCE == 85
    assert recommendation_gate.ALLOWED_RISK_LEVELS == {"low", "medium"}
    assert economic_quality_gate.PRODUCTION_MAX_CAPITAL_FRACTION == 0.20
    assert target_attainability.MIN_QUALIFYING_SCORE == 65
    assert candidates_module.MIN_TECHNICAL_SCORE == 80


def test_scan_instrumentation_is_observation_only():
    """No O'Pip call site in the scan may feed a value back into the flow."""
    source = inspect.getsource(scan_opportunities.main)
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("opip."):
            continue
        assert "=" not in stripped.split("(")[0], stripped
    # The single permitted assignment is the eligibility counter, which feeds
    # only the summary it is reported in.
    assert "paper_admission_eligible = opip.record_paper_admission_eligibility(" in source
    assert source.count("= opip.") == 1



def test_confidence_evidence_cannot_reach_alert_paper_or_order_code():
    """The below-85 evidence tag must stay inside the observer/read side."""
    source = inspect.getsource(gates.evaluate_recommendation_gate_item)
    package_source = _opip_source()
    assert "AI_CONFIDENCE_COUNTERFACTUAL" in source
    forbidden = (
        "send_trade_plan",
        "send_telegram",
        "publish_qualified_long",
        "enroll_paper_opportunity",
        "record_paper_admission(",
        "place_order",
        "create_order",
        "submit_order",
    )
    for name in forbidden:
        assert name not in source
    # The O'Pip decision package remains an observer/read-side package: it may
    # describe paper eligibility, but it must not invoke production admission
    # or exchange-order functions.
    for name in forbidden:
        assert name not in package_source
