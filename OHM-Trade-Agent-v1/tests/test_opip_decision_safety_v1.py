"""Structural safety invariants for the O'Pip Decision Engine (Build 1).

Build 1 adds observation. These tests exist to prove it added nothing else:
the shadow engine cannot change a production admission, cannot reach the
exchange, cannot escalate a permission, cannot create a counterfactual or
futures position, and no model influences any trading decision.
"""

import ast
import inspect
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest

try:  # `tomllib` ships with Python 3.11+; the scan fails closed without it.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - unsupported interpreter
    tomllib = None

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

from tests.ml_dependency_policy import (  # noqa: E402
    RELEVANT_MANIFESTS,
    manifest_file_violations,
    package_import_violations,
)
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
    """No model package is declared or imported by the decision package.

    The prohibition is unchanged; the detection delegates to the shared policy
    (`tests.ml_dependency_policy`). Static imports and the supported
    dynamic-import paths are read from the module AST, and the declared
    manifests are parsed as requirements, so an ML dependency or a declared ML
    distribution is still caught while ordinary prose that merely contains
    `shap` is not. Anything the policy cannot prove fails closed.
    """
    assert package_import_violations(OPIP_PACKAGE) == ()
    for name in RELEVANT_MANIFESTS:
        assert manifest_file_violations(Path(name)) == ()


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


# --------------------------------------- ML dependency scan (evidence-based) --
#
# Increment `ATDD-DECISION-SAFETY-SCAN-v1` replaces the raw substring scan with
# evidence: declared distribution names and parsed imports. The authority for
# what each assertion below means is
# `docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v1.md`.

SCAN_APP_ROOT = Path(__file__).resolve().parents[1]
SCAN_INCREMENT = "ATDD-DECISION-SAFETY-SCAN-v1"
SCAN_CONTRACTS = SCAN_APP_ROOT / "docs" / "atdd" / "scope-contracts"
SCAN_POINTER = SCAN_APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
SCAN_CHANGED_SURFACES = (
    "OHM-Trade-Agent-v1/docs/MODULE2_INTELLIGENCE_COMMITTEE.md",
    "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v1.md",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-B-horizon-intelligence.md",
    "OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py",
)

#: The prohibited model distributions. Declaring or importing any of them means a
#: model could influence a trading decision. `sklearn` is kept beside its
#: distribution name `scikit-learn` because it is both a valid import root and a
#: valid (legacy) requirement spelling.
PROHIBITED_ML_DISTRIBUTIONS = frozenset(
    {
        "lightgbm",
        "scikit-learn",
        "shap",
        "sklearn",
        "tensorflow",
        "torch",
        "xgboost",
    }
)

#: Distribution names are compared under PEP 503 normalization, so `Scikit_Learn`,
#: `scikit-learn` and `scikit.learn` are one name.
_DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalized_distribution(name: str) -> str:
    """The PEP 503 form of a distribution name."""

    return re.sub(r"[-_.]+", "-", name.strip().lower()).strip("-")


def _requirement_name(line: str) -> str | None:
    """The distribution a requirement line declares, or `None` if it declares none.

    Comments, blank lines and pip options (`-r`, `-e`, `--hash`, ...) declare no
    distribution of their own and return `None`.
    """

    stripped = line.split("#", 1)[0].strip()
    if not stripped or stripped.startswith("-"):
        return None
    match = _DISTRIBUTION_NAME.match(stripped)
    return _normalized_distribution(match.group(0)) if match else None


def _read_text_relative(name: str) -> str:
    """The default reader: dependency declarations live at the app root."""

    return Path(name).read_text(encoding="utf-8")


def _declared_in_pyproject(text: str) -> frozenset[str]:
    """Distributions declared by PEP 621 `project` dependencies."""

    if tomllib is None:  # pragma: no cover - fail closed, never pass silently
        raise AssertionError("pyproject dependency parsing requires Python 3.11+")
    project = tomllib.loads(text).get("project") or {}
    declared: list[str] = list(project.get("dependencies") or ())
    for extra in (project.get("optional-dependencies") or {}).values():
        declared.extend(extra or ())
    names = {_requirement_name(str(item)) for item in declared}
    return frozenset(name for name in names if name)


class UnresolvableDeclarationError(RuntimeError):
    """A dependency declaration could not be read to a proof.

    The scan fails closed: an include that is missing, unreadable or remote means
    the declared dependency set cannot be proven clean, so the scan refuses
    instead of reporting nothing. Refusing is the only safe answer for a guard
    whose entire purpose is to prove that no model dependency is declared.
    """


#: A remote include (`-r https://...`) cannot be read by a static scan.
_REMOTE_INCLUDE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _required_by(line: str) -> str | None:
    """The file a `-r` / `--requirement` line includes, or `None` otherwise."""

    stripped = line.split("#", 1)[0].strip()
    if not stripped.startswith(("-r ", "--requirement ", "-r=", "--requirement=")):
        return None
    return re.split(r"[= ]", stripped, maxsplit=1)[1].strip().strip("\"'")


def _declared_in(
    path: str,
    kind: str,
    read: Callable[[str], str],
    _seen: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Every distribution one declaration file names, includes followed.

    Failure rules (`ATDD-DECISION-SAFETY-SCAN-v2/AC-001`): a declaration file or
    include that cannot be read, and a remote include, fail closed with
    `UnresolvableDeclarationError`. A circular include is resolvable - the
    revisit is skipped - so it terminates and still reports what it contains.
    """

    if path in _seen:
        return frozenset()
    try:
        text = read(path)
    except (OSError, KeyError) as exc:  # missing or unreadable: fail closed
        raise UnresolvableDeclarationError(
            f"declaration {path!r} could not be read: {exc}"
        ) from exc
    if kind == "pyproject":
        return _declared_in_pyproject(text)
    found: set[str] = set()
    for line in text.splitlines():
        target = _required_by(line)
        if target is not None:
            if _REMOTE_INCLUDE.match(target):
                raise UnresolvableDeclarationError(
                    f"declaration {path!r} includes the remote requirement "
                    f"{target!r}, which this scan cannot read"
                )
            found |= _declared_in(target, "requirements", read, _seen | {path})
            continue
        name = _requirement_name(line)
        if name:
            found.add(name)
    return frozenset(found)


def _declared_prohibited(
    sources: Sequence[tuple[str, str]],
    *,
    read: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    """Prohibited distributions declared by `sources`.

    `sources` is a sequence of `(path, kind)` where `kind` is `"requirements"` or
    `"pyproject"`. `read` resolves an included requirement file, so `-r` includes
    are followed instead of being skipped (the default reads from the app root).
    """

    reader = read if read is not None else _read_text_relative
    found: set[str] = set()
    for path, kind in sources:
        found |= _declared_in(path, kind, reader)
    return tuple(sorted(found & PROHIBITED_ML_DISTRIBUTIONS))


def _dependency_declarations() -> tuple[tuple[str, str], ...]:
    """Every dependency declaration this scan reads, discovered from the app root.

    Discovery globs the app root, so a new requirement file is covered without
    editing this module - the scan widens with the repository instead of relying
    on a hand-kept list.
    """

    declarations = [
        (path.name, "requirements") for path in sorted(Path().glob("requirements*.txt"))
    ]
    if Path("pyproject.toml").is_file():
        declarations.append(("pyproject.toml", "pyproject"))
    return tuple(declarations)


def _root_module(name: str) -> str:
    return name.split(".", 1)[0].lower()


def _prohibited_imports_in_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Prohibited import roots found in already-read module sources.

    The scan reads `ast.Import` / `ast.ImportFrom` nodes, so plain, aliased,
    from-import and submodule forms are all covered without matching prose.
    Relative imports (level > 0) carry no distribution and are ignored.
    """

    found: set[str] = set()
    for source in sources:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(_root_module(alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.add(_root_module(node.module))
    return tuple(sorted(found & PROHIBITED_ML_DISTRIBUTIONS))


def _prohibited_imports_in_paths(paths: Sequence[Path]) -> tuple[str, ...]:
    """Prohibited import roots in the given module files."""

    return _prohibited_imports_in_sources(
        [path.read_text(encoding="utf-8") for path in paths]
    )


#: The dynamic-import call names this scan evaluates. A prohibited module reached
#: through one of them is a real import even though no `Import` node names it.
_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module", "find_spec"})


def _called_name(node: ast.Call) -> str | None:
    """The called function's own name, however it is qualified."""

    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dynamic_import_calls(source: str) -> tuple[tuple[str, str | None], ...]:
    """Every dynamic-import call in `source` as `(call name, literal target)`.

    The target is `None` when the first argument is not a string literal - a
    variable, an f-string, a concatenation, or no argument at all. That case is
    reported by `_unprovable_dynamic_imports_in_sources`, which the scan asserts
    is empty, so an unprovable call is refused rather than skipped.
    """

    calls: list[tuple[str, str | None]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in _DYNAMIC_IMPORT_CALLS:
            continue
        target: str | None = None
        if node.args and isinstance(node.args[0], ast.Constant):
            first = node.args[0].value
            if isinstance(first, str):
                target = first
        calls.append((name, target))
    return tuple(sorted(calls))


def _prohibited_dynamic_imports_in_sources(
    sources: Sequence[str],
) -> tuple[str, ...]:
    """Prohibited import roots reached by a literal dynamic-import call."""

    found: set[str] = set()
    for source in sources:
        for _name, target in _dynamic_import_calls(source):
            if target:
                found.add(_root_module(target))
    return tuple(sorted(found & PROHIBITED_ML_DISTRIBUTIONS))


def _prohibited_dynamic_imports_in_paths(paths: Sequence[Path]) -> tuple[str, ...]:
    """Prohibited dynamic imports in the given module files."""

    return _prohibited_dynamic_imports_in_sources(
        [path.read_text(encoding="utf-8") for path in paths]
    )


def _unprovable_dynamic_imports_in_sources(
    sources: Sequence[str],
) -> tuple[str, ...]:
    """Dynamic-import calls whose target cannot be proven from the source.

    This is the fail-closed signal (`AC-003`): the scan asserts it is empty, so a
    call the scan cannot resolve is refused instead of assumed harmless.
    """

    found: set[str] = set()
    for source in sources:
        for name, target in _dynamic_import_calls(source):
            if target is None:
                found.add(name)
    return tuple(sorted(found))


def _unprovable_dynamic_imports_in_paths(paths: Sequence[Path]) -> tuple[str, ...]:
    """Unprovable dynamic imports in the given module files."""

    return _unprovable_dynamic_imports_in_sources(
        [path.read_text(encoding="utf-8") for path in paths]
    )


@pytest.mark.acceptance
def test_ac_001_a_real_ml_dependency_is_rejected_in_every_declared_and_imported_form() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v1/AC-001: real violations are still refused.

    GIVEN declared dependency forms (plain, pinned, extra, specifier, included)
    and imported forms (plain, aliased, from-import, submodule)
    WHEN the evidence scan reads each declaration and each import
    THEN every prohibited distribution is reported under its normalized name, so
    a real model dependency can never pass this increment by being written in an
    unusual but valid form and the detection is never replaced by an exclusion
    """
    declared = {
        "requirements.txt": (
            "# ordinary prose: one payload shape serves both halves\n"
            "shap==0.45.0\n"
            "xgboost\n"
            "lightgbm[extra]>=4.0\n"
            "scikit_learn~=1.4\n"
            "-r included.txt\n"
        ),
        "included.txt": "torch==2.4.0\nnumpy==2.0.0\n",
        "pyproject.toml": (
            "[project]\n"
            'name = "sample"\n'
            'dependencies = ["pandas", "tensorflow==2.16.1"]\n'
            "[project.optional-dependencies]\n"
            'dev = ["pytest", "scikit-learn"]\n'
        ),
    }
    sources = (
        ("requirements.txt", "requirements"),
        ("pyproject.toml", "pyproject"),
    )
    assert _declared_prohibited(sources, read=declared.__getitem__) == (
        "lightgbm",
        "scikit-learn",
        "shap",
        "tensorflow",
        "torch",
        "xgboost",
    )
    assert _prohibited_imports_in_sources(["import shap\n"]) == ("shap",)
    assert _prohibited_imports_in_sources(["import shap as explainer\n"]) == ("shap",)
    assert _prohibited_imports_in_sources(["from shap import KernelExplainer\n"]) == (
        "shap",
    )
    assert _prohibited_imports_in_sources(["import torch.nn as nn\n"]) == ("torch",)
    assert _prohibited_imports_in_sources(["from xgboost.sklearn import XGB\n"]) == (
        "xgboost",
    )
    assert _prohibited_imports_in_sources(["from .shap import local_helper\n"]) == ()
    assert _normalized_distribution("SciKit_Learn") == "scikit-learn"
    assert _requirement_name("SciKit_Learn[extra]>=1.0") == "scikit-learn"
    assert _requirement_name("--index-url https://example.invalid") is None


@pytest.mark.acceptance
def test_ac_002_harmless_prose_identifiers_and_strings_are_accepted() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v1/AC-002: prose is not a dependency.

    GIVEN the ordinary English and identifiers a substring scan rejected -
    `shape`, `shaped`, `authority-shaped`, `_timing_shape(`, a docstring and
    string literals - together with unrelated real dependencies
    WHEN the evidence scan reads that source and the real decision package
    THEN nothing is reported, because only declared distributions and parsed
    imports count as evidence
    """
    prose = (
        '"""A payload shape serves both halves; an authority-shaped record."""\n'
        "def _timing_shape(moves: dict[str, float]) -> tuple[bool, str | None]:\n"
        '    """The hold-shaped plan asked what a rotation would do."""\n'
        '    note = "borrow the shape of a decided one"\n'
        "    labels = {'shape': 'unchanged', 'reshaped': True}\n"
        "    return True, note + str(labels)\n"
    )
    assert _prohibited_imports_in_sources([prose]) == ()
    assert _declared_prohibited((), read=lambda _name: "") == ()
    assert _prohibited_imports_in_paths(sorted(OPIP_PACKAGE.rglob("*.py"))) == ()
    declared = {
        "requirements.txt": "numpy==2.0.0\npandas>=2.0\npytest==8.4.2\n",
        "pyproject.toml": '[project]\ndependencies = ["pandas"]\n',
    }
    assert (
        _declared_prohibited(
            (("requirements.txt", "requirements"), ("pyproject.toml", "pyproject")),
            read=declared.__getitem__,
        )
        == ()
    )


@pytest.mark.acceptance
def test_ac_003_every_module_and_declaration_is_still_examined(tmp_path: Path) -> None:
    """ATDD-DECISION-SAFETY-SCAN-v1/AC-003: no file-level escape from the scan.

    GIVEN the real decision package and its discovered dependency declarations,
    plus a synthetic module and an included requirement file that each *do*
    declare a prohibited form
    WHEN the scan enumerates and inspects them
    THEN the enumeration covers every `app/opip/**/*.py` file and every
    discovered `requirements*.txt` and `pyproject.toml`, an included requirement
    file is followed rather than skipped, and a violating file is detected
    wherever it lives - so no blanket exclusion or allowlist replaces detection
    """
    modules = sorted(OPIP_PACKAGE.rglob("*.py"))
    assert modules, "the decision package must contain modules to scan"
    declarations = _dependency_declarations()
    kinds = {kind for _path, kind in declarations}
    assert kinds == {"requirements", "pyproject"}
    declared_names = {
        Path(path).name for path, kind in declarations if kind == "requirements"
    }
    assert declared_names == {path.name for path in Path().glob("requirements*.txt")}
    violating = tmp_path / "violating.py"
    violating.write_text("from shap import KernelExplainer\n", encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text('"""authority-shaped prose only."""\n', encoding="utf-8")
    assert _prohibited_imports_in_paths([violating]) == ("shap",)
    assert _prohibited_imports_in_paths([clean]) == ()
    assert _prohibited_imports_in_paths([clean, violating]) == ("shap",)
    hidden = {"requirements.txt": "-r hidden.txt\n", "hidden.txt": "shap==0.45.0\n"}
    assert _declared_prohibited(
        (("requirements.txt", "requirements"),), read=hidden.__getitem__
    ) == ("shap",)


@pytest.mark.acceptance
def test_ac_004_the_scan_contract_authorizes_every_changed_surface() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v1/AC-004: the change is contract-authorized.

    GIVEN this increment's contract, the acceptance index and the changed files
    WHEN the real scope gate evaluates them
    THEN the movable pointer resolves to this declared increment, every changed
    surface is mapped to an AC of this contract, the gate passes with no
    traceability gap and no unapproved criterion, and this module pins no global
    pointer
    """
    from tests.atdd_scope import (  # noqa: PLC0415 - local import keeps the header stable
        PASS,
        check_scope,
        load_acceptance_index,
        load_scope_contracts,
        read_increment_pointer,
    )
    from tests.test_atdd_scope_control import global_pointer_pin_violations

    contracts = load_scope_contracts(SCAN_CONTRACTS)
    assert read_increment_pointer(SCAN_POINTER) in {
        item.increment for item in contracts
    }
    ours = [item for item in contracts if item.increment == SCAN_INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    allowed = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    for surface in SCAN_CHANGED_SURFACES:
        assert surface in allowed, surface
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    result = check_scope(
        contract,
        load_acceptance_index(SCAN_APP_ROOT / "tests"),
        SCAN_CHANGED_SURFACES,
    )
    assert result.status == PASS, result.reasons
    assert global_pointer_pin_violations(
        Path(__file__).read_text(encoding="utf-8")
    ) == []


# --------- v2: fail-closed declarations and literal dynamic-import detection ------


SCAN_V2_INCREMENT = "ATDD-DECISION-SAFETY-SCAN-v2"
SCAN_V2_CHANGED_SURFACES = (
    "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-DECISION-SAFETY-SCAN-v2.md",
    "OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py",
)


@pytest.mark.acceptance
def test_ac_001_an_unresolvable_dependency_include_fails_closed() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v2/AC-001: an include that cannot be read is refused.

    GIVEN requirement declarations that include another file by `-r` or
    `--requirement`, covering a missing target, an unreadable target and a
    remote target, together with a resolvable include and a circular include
    WHEN the scan resolves those declarations
    THEN the missing, unreadable and remote includes fail closed with an error
    naming the unresolvable target instead of reporting nothing, the resolvable
    include is still followed and reports its prohibited dependency, and the
    circular include terminates while still reporting the prohibited dependency
    it contains
    """

    missing = {"requirements.txt": "-r missing.txt\n"}
    with pytest.raises(UnresolvableDeclarationError, match="missing.txt"):
        _declared_prohibited(
            (("requirements.txt", "requirements"),), read=missing.__getitem__
        )
    remote = {"requirements.txt": "--requirement https://example.invalid/req.txt\n"}
    with pytest.raises(UnresolvableDeclarationError, match="example.invalid"):
        _declared_prohibited(
            (("requirements.txt", "requirements"),), read=remote.__getitem__
        )

    def _denied(_name: str) -> str:
        raise PermissionError("permission denied")

    with pytest.raises(UnresolvableDeclarationError, match="permission denied"):
        _declared_prohibited((("requirements.txt", "requirements"),), read=_denied)

    resolvable = {
        "requirements.txt": "-r included.txt\n",
        "included.txt": "shap==0.45.0\n",
    }
    assert _declared_prohibited(
        (("requirements.txt", "requirements"),), read=resolvable.__getitem__
    ) == ("shap",)

    circular = {
        "requirements.txt": "-r second.txt\n",
        "second.txt": "-r requirements.txt\ntorch==2.4.0\n",
    }
    assert _declared_prohibited(
        (("requirements.txt", "requirements"),), read=circular.__getitem__
    ) == ("torch",)


@pytest.mark.acceptance
def test_ac_002_a_prohibited_module_reached_by_a_literal_dynamic_import_is_reported() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v2/AC-002: literal dynamic imports are evidence.

    GIVEN source that reaches a module through `importlib.import_module`,
    `__import__` and `importlib.util.find_spec` with string-literal targets,
    including aliased spellings and a submodule literal
    WHEN the scan parses that source
    THEN every prohibited distribution is reported under its normalized name,
    and a benign literal target is accepted
    """

    assert _prohibited_dynamic_imports_in_sources(
        ['import importlib\nimportlib.import_module("shap")\n']
    ) == ("shap",)
    assert _prohibited_dynamic_imports_in_sources(['__import__("torch")\n']) == (
        "torch",
    )
    assert _prohibited_dynamic_imports_in_sources(
        ['from importlib import util\nutil.find_spec("sklearn")\n']
    ) == ("sklearn",)
    assert _prohibited_dynamic_imports_in_sources(
        ['import importlib as il\nil.import_module("xgboost.train")\n']
    ) == ("xgboost",)
    assert _prohibited_dynamic_imports_in_sources(
        ['import importlib\nimportlib.import_module("json")\n']
    ) == ()
    assert _prohibited_dynamic_imports_in_sources(
        ['import importlib\nimportlib.import_module(name)\n']
    ) == ()


@pytest.mark.acceptance
def test_ac_003_an_unprovable_dynamic_import_fails_closed(tmp_path: Path) -> None:
    """ATDD-DECISION-SAFETY-SCAN-v2/AC-003: an unprovable dynamic import is refused.

    GIVEN dynamic-import calls whose target cannot be proven from the source - a
    variable, an f-string, a concatenation and a call with no argument - plus a
    literal call and the real decision package
    WHEN the scan evaluates that source
    THEN every unprovable call is reported by name so the caller refuses it
    instead of skipping it, the literal call is not reported as unprovable, and
    the real package contains no unprovable and no prohibited dynamic import
    """

    assert _unprovable_dynamic_imports_in_sources(
        ["import importlib\nimportlib.import_module(target)\n"]
    ) == ("import_module",)
    assert _unprovable_dynamic_imports_in_sources(['__import__(f"{name}.sub")\n']) == (
        "__import__",
    )
    assert _unprovable_dynamic_imports_in_sources(
        ['import importlib\nimportlib.import_module(prefix + ".train")\n']
    ) == ("import_module",)
    assert _unprovable_dynamic_imports_in_sources(
        ["import importlib\nimportlib.import_module()\n"]
    ) == ("import_module",)
    assert _unprovable_dynamic_imports_in_sources(
        ['import importlib\nimportlib.import_module("json")\n']
    ) == ()

    modules = sorted(OPIP_PACKAGE.rglob("*.py"))
    assert modules, "the decision package must contain modules to scan"
    assert _unprovable_dynamic_imports_in_paths(modules) == ()
    assert _prohibited_dynamic_imports_in_paths(modules) == ()

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib\nimportlib.import_module(variable_target)\n",
        encoding="utf-8",
    )
    assert _unprovable_dynamic_imports_in_paths([probe]) == ("import_module",)


@pytest.mark.acceptance
def test_ac_004_prose_and_lookalike_calls_are_still_accepted() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v2/AC-004: no new false positives.

    GIVEN ordinary English, a lookalike call name and the accepted v1 controls -
    the real decision package, the real declarations and every static import form
    WHEN the widened scan reads them
    THEN the prose source is accepted by all three scans, the real package and
    the real declarations stay clean, and each static form is still refused
    """

    prose = (
        '"""A payload shape serves both halves; an authority-shaped record."""\n'
        "def _timing_shape(moves: dict[str, float]):\n"
        '    """reshape the plan note."""\n'
        '    note = "borrow the shape of a decided one"\n'
        "    return _timing_shape_like(note)\n"
    )
    assert _prohibited_imports_in_sources([prose]) == ()
    assert _prohibited_dynamic_imports_in_sources([prose]) == ()
    assert _unprovable_dynamic_imports_in_sources([prose]) == ()

    assert _prohibited_imports_in_sources(["import shap\n"]) == ("shap",)
    assert _prohibited_imports_in_sources(["import shap as explainer\n"]) == ("shap",)
    assert _prohibited_imports_in_sources(["from shap import KernelExplainer\n"]) == (
        "shap",
    )
    assert _prohibited_imports_in_sources(["import torch.nn as nn\n"]) == ("torch",)
    assert _prohibited_imports_in_sources(["from xgboost.sklearn import XGB\n"]) == (
        "xgboost",
    )
    assert _normalized_distribution("SciKit_Learn") == "scikit-learn"
    assert _declared_prohibited(_dependency_declarations()) == ()
    assert _prohibited_imports_in_paths(sorted(OPIP_PACKAGE.rglob("*.py"))) == ()


@pytest.mark.acceptance
def test_ac_005_the_v2_contract_authorizes_every_changed_surface() -> None:
    """ATDD-DECISION-SAFETY-SCAN-v2/AC-005: the widening is contract-authorized.

    GIVEN this increment's contract, the acceptance index and the changed files
    WHEN the real scope gate evaluates them
    THEN the movable pointer resolves to a declared increment, this increment's
    contract maps every changed surface to a criterion of its own, the gate
    passes with no traceability gap and no unapproved criterion, the changed
    surfaces touch no runtime path, and this module pins no global pointer
    """

    from tests.atdd_scope import (  # noqa: PLC0415 - local import keeps the header stable
        PASS,
        check_scope,
        load_acceptance_index,
        load_scope_contracts,
        read_increment_pointer,
    )
    from tests.test_atdd_scope_control import global_pointer_pin_violations

    contracts = load_scope_contracts(SCAN_CONTRACTS)
    assert read_increment_pointer(SCAN_POINTER) in {
        item.increment for item in contracts
    }
    ours = [item for item in contracts if item.increment == SCAN_V2_INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    allowed = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    for surface in SCAN_V2_CHANGED_SURFACES:
        assert surface in allowed, surface
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert not [surface for surface in SCAN_V2_CHANGED_SURFACES if "/app/" in surface]
    result = check_scope(
        contract,
        load_acceptance_index(SCAN_APP_ROOT / "tests"),
        SCAN_V2_CHANGED_SURFACES,
    )
    assert result.status == PASS, result.reasons
    assert global_pointer_pin_violations(
        Path(__file__).read_text(encoding="utf-8")
    ) == []

