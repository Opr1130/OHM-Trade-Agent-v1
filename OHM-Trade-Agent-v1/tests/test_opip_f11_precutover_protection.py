"""F11 pre-cutover protection: acceptance guards.

Proves the F11 read-only protection-health decision: exactly one mutating
protection authority per domain, zero silent holdings (including geometry and
materiality), explicit fail-closed uncertainty and incident health, a read-only
authority-free report with a single JSON document on stdout, and a genuinely
non-mutating read seam.

The evaluator is pure, so most guards drive it with constructed evidence.
"""

from __future__ import annotations

import ast
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import protection_health as ph
from app.services import system_incidents
from app.services.registry_io import (
    RegistryCorruptionError,
    RegistryIOError,
    read_json_without_quarantine,
)

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
ATDD = APP_ROOT / "docs" / "atdd"
SCOPE_CONTRACTS = ATDD / "scope-contracts"
CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-F11-precutover-protection.md"
HEALTH_MODULE = APP_ROOT / "app" / "services" / "protection_health.py"
REPORT_JOB = APP_ROOT / "app" / "jobs" / "report_protection_health.py"
RUN_CYCLE = APP_ROOT / "app" / "jobs" / "run_cycle.py"
SCAN = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"
REGISTRY_IO = APP_ROOT / "app" / "services" / "registry_io.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "kraken_private",
    "exchanges",
    "committee",
    "writer",
    "canonical",
    "order",
    "kraken_reconciliation",
    "active_trade_monitor",
)

#: The F11 evaluator may import only this application surface (read-only, pure).
HEALTH_ALLOWED_APP_IMPORTS = ("app.core.config",)

FORBIDDEN_CALL_TOKENS = (
    ".submit(",
    "close_trade",
    "update_trade_remaining_quantity",
    "mark_order_filled",
    "bind_exchange_order_txid",
    "admit_paper_opportunity",
    "trigger_paper_protection_action",
    "WriterIntent",
    "os.replace",
    "save_json",
)

#: Registry/order mutators for the live (Kraken/operator) exposure domain.
LIVE_MUTATORS = frozenset(
    {
        "close_trade",
        "update_trade_remaining_quantity",
        "mark_order_filled",
        "bind_exchange_order_txid",
    }
)
#: Canonical mutators for the target (Paper-v2) exposure domain.
TARGET_MUTATORS = frozenset({"admit_paper_opportunity", "trigger_paper_protection_action"})

DECLARED_LIVE_MUTATOR_CALLERS = frozenset(
    {
        "app/api/routes.py",
        "app/services/kraken_reconciliation.py",
        "app/services/trade_cli.py",
    }
)
DECLARED_TARGET_MUTATOR_CALLERS = frozenset(
    {
        "app/opip/canonical/server.py",
        "app/services/paper_v2_execution.py",
        "app/services/paper_v2_protection_runtime.py",
    }
)


def _pointer() -> str:
    return (ATDD / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _exposure(
    status: str,
    symbol: str = "SOLUSD",
    *,
    entry: float | None = 100.0,
    stop: float | None = 90.0,
    direction: str = "LONG",
):
    if entry is None and stop is None:
        trade = None
    else:
        trade = SimpleNamespace(
            entry_price=entry, stop_price=stop, direction=direction
        )
    return SimpleNamespace(
        status=status, symbol=symbol, trade=trade, observed_quantity=1.0
    )


def _healthy_kwargs(**overrides):
    kwargs = {"coverage_complete": True, "incidents_healthy": True}
    kwargs.update(overrides)
    return kwargs


def _app_imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _mutating_exposure_callers(tokens: frozenset[str]) -> set[str]:
    callers: set[str] = set()
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name in tokens:
                callers.add(path.relative_to(APP_ROOT).as_posix())
                break
    return callers


# ---------------------------------------------------------------------------
# AC-001
# ---------------------------------------------------------------------------


def test_ac_001_contract_and_pointer_are_consistent():
    """ATDD-R4-F11-precutover-protection/AC-001: the contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT.is_file(), CONTRACT
    assert "INCREMENT:\nATDD-R4-F11-precutover-protection" in _contract_text()
    pointer = _pointer()
    assert (SCOPE_CONTRACTS / f"{pointer}.md").is_file(), pointer


# ---------------------------------------------------------------------------
# AC-002 census
# ---------------------------------------------------------------------------


def test_ac_002_one_mutating_authority_and_read_only_evaluator():
    """ATDD-R4-F11-precutover-protection/AC-002: the mutating exposure callers are exactly the declared authorities, and the F11 evaluator is read-only and not among them."""
    for token in FORBIDDEN_CALL_TOKENS:
        assert token not in HEALTH_MODULE.read_text(encoding="utf-8"), token
    for name in _app_imports(HEALTH_MODULE):
        lowered = name.lower()
        for forbidden in FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in lowered, (name, forbidden)
        if name.startswith("app."):
            assert name.startswith(HEALTH_ALLOWED_APP_IMPORTS), name

    live = _mutating_exposure_callers(LIVE_MUTATORS)
    target = _mutating_exposure_callers(TARGET_MUTATORS)
    assert live == set(DECLARED_LIVE_MUTATOR_CALLERS), live
    assert target == set(DECLARED_TARGET_MUTATOR_CALLERS), target
    assert not (
        {"app/services/protection_health.py", "app/jobs/report_protection_health.py"}
        & (live | target)
    )


# ---------------------------------------------------------------------------
# AC-003 silent holdings and coverage
# ---------------------------------------------------------------------------


def test_ac_003_silent_holding_is_unsafe_and_suspends():
    """ATDD-R4-F11-precutover-protection/AC-003: a verified managed holding whose protection plan is missing, non-finite or non-positive is a silent holding, reported UNSAFE with admissions suspended."""
    health = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", stop=None),), **_healthy_kwargs()
    )
    assert health.state == ph.STATE_UNSAFE
    assert health.admissions_suspended is True
    assert health.silent_holdings == ("SOLUSD",)
    assert ph.REASON_SILENT_HOLDING in health.reason_codes

    protected = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),), **_healthy_kwargs()
    )
    assert protected.state == ph.STATE_HEALTHY
    assert protected.silent_holdings == ()

    absent = ph.evaluate_protection_health(
        (_exposure("ABSENT", entry=None, stop=None),), **_healthy_kwargs()
    )
    assert absent.state == ph.STATE_HEALTHY
    assert absent.silent_holdings == ()


@pytest.mark.parametrize("stop", [0, -1.0, float("nan"), float("inf")])
def test_ac_003_non_finite_or_non_positive_stop_is_a_silent_holding(stop):
    """ATDD-R4-F11-precutover-protection/AC-003: a protection plan that is non-finite or non-positive is not a protection plan, so the holding is silent and admissions are suspended."""
    health = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", stop=stop),), **_healthy_kwargs()
    )
    assert health.state == ph.STATE_UNSAFE
    assert health.silent_holdings == ("SOLUSD",)


def test_ac_003_missing_coverage_fails_closed():
    """ATDD-R4-F11-precutover-protection/AC-003: coverage is proven only when explicitly True; a defaulted or zero-argument call is never HEALTHY."""
    defaulted = ph.evaluate_protection_health((_exposure("VERIFIED_MANAGED"),))
    assert defaulted.state == ph.STATE_UNAVAILABLE
    assert defaulted.admissions_suspended is True
    assert defaulted.coverage_complete is False
    assert ph.REASON_COVERAGE_INCOMPLETE in defaulted.reason_codes

    zero_arg = ph.evaluate_protection_health()
    assert zero_arg.state == ph.STATE_UNAVAILABLE
    assert zero_arg.admissions_suspended is True


# ---------------------------------------------------------------------------
# AC-004 uncertainty
# ---------------------------------------------------------------------------


def test_ac_004_uncertainty_fails_closed():
    """ATDD-R4-F11-precutover-protection/AC-004: unmanaged, unreadable/degraded and incomplete-coverage evidence each withhold admissions, and only a fully proven set is HEALTHY."""
    unmanaged = ph.evaluate_protection_health(
        (_exposure("VERIFIED_UNMANAGED", entry=None, stop=None),), **_healthy_kwargs()
    )
    assert unmanaged.state == ph.STATE_UNSAFE
    assert unmanaged.unmanaged_exposures == ("SOLUSD",)
    assert ph.REASON_UNMANAGED_EXPOSURE in unmanaged.reason_codes

    degraded = ph.evaluate_protection_health(
        (_exposure("DEGRADED"),), **_healthy_kwargs()
    )
    assert degraded.state == ph.STATE_UNAVAILABLE
    assert degraded.uncertain_exposures == ("SOLUSD",)
    assert ph.REASON_EXPOSURE_UNCERTAIN in degraded.reason_codes

    unknown = ph.evaluate_protection_health(
        (_exposure("UNKNOWN"),), **_healthy_kwargs()
    )
    assert unknown.state == ph.STATE_UNAVAILABLE

    incomplete = ph.evaluate_protection_health(
        (), coverage_complete=False, incidents_healthy=True
    )
    assert incomplete.state == ph.STATE_UNAVAILABLE
    assert ph.REASON_COVERAGE_INCOMPLETE in incomplete.reason_codes

    target_unhealthy = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),),
        **_healthy_kwargs(target_protection_allows_admissions=False),
    )
    assert target_unhealthy.state == ph.STATE_UNSAFE
    assert ph.REASON_TARGET_PROTECTION_UNHEALTHY in target_unhealthy.reason_codes

    proven = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=True,
        target_protection_allows_admissions=None,
    )
    assert proven.state == ph.STATE_HEALTHY
    assert proven.reason_codes == (ph.REASON_HEALTHY,)


# ---------------------------------------------------------------------------
# AC-005 report is read-only, machine-readable, and authority-free
# ---------------------------------------------------------------------------


def test_ac_005_report_is_read_only_and_authority_free(monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-005: the report prints exactly one machine-readable decision, mutates nothing, and fails closed when exposure or incidents are unproven."""
    import app.jobs.report_protection_health as report

    resolution = SimpleNamespace(
        exposures=(_exposure("VERIFIED_MANAGED"),), coverage_complete=True
    )
    monkeypatch.setattr(
        report, "_read_only_resolver", lambda: SimpleNamespace(resolve=lambda: resolution)
    )
    monkeypatch.setattr(
        report,
        "read_incident_projection",
        lambda: report.IncidentProjection(health=True, open_incidents=()),
    )
    payload = report.build_report()
    assert payload["state"] == ph.STATE_HEALTHY
    assert "resolution_reason" not in payload
    assert json.loads(json.dumps(payload))["state"] == ph.STATE_HEALTHY

    # Unresolvable exposure fails closed.
    def _boom():
        raise RuntimeError("kraken down")

    monkeypatch.setattr(
        report, "_read_only_resolver", lambda: SimpleNamespace(resolve=_boom)
    )
    failed = report.build_report()
    assert failed["state"] == ph.STATE_UNAVAILABLE
    assert failed["admissions_suspended"] is True

    # Unproven incident health withholds admissions even when the exposure is fine.
    monkeypatch.setattr(
        report, "_read_only_resolver", lambda: SimpleNamespace(resolve=lambda: resolution)
    )
    monkeypatch.setattr(
        report,
        "read_incident_projection",
        lambda: report.IncidentProjection(health=None, open_incidents=None),
    )
    unproven = report.build_report()
    assert unproven["state"] == ph.STATE_UNAVAILABLE
    assert ph.REASON_INCIDENTS_UNPROVEN in unproven["reason_codes"]


def test_report_exposes_incomplete_resolution_reason_without_changing_gate(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-022: incomplete exposure coverage preserves resolver diagnostics without changing the fail-closed decision."""
    import app.jobs.report_protection_health as report

    reason = "active trade registry unavailable: read failed"
    resolution = SimpleNamespace(
        exposures=(_exposure("VERIFIED_MANAGED"),),
        coverage_complete=False,
        reason=reason,
    )
    monkeypatch.setattr(
        report, "_read_only_resolver", lambda: SimpleNamespace(resolve=lambda: resolution)
    )
    monkeypatch.setattr(
        report,
        "read_incident_projection",
        lambda: report.IncidentProjection(health=True, open_incidents=()),
    )

    payload = report.build_report()

    assert payload["resolution_reason"] == reason
    assert payload["state"] == ph.STATE_UNAVAILABLE
    assert payload["admissions_suspended"] is True
    assert payload["coverage_complete"] is False
    assert ph.REASON_COVERAGE_INCOMPLETE in payload["reason_codes"]


# ---------------------------------------------------------------------------
# AC-006 ordering / no new authority
# ---------------------------------------------------------------------------


def test_ac_006_protection_ordering_and_no_new_authority():
    """ATDD-R4-F11-precutover-protection/AC-006: protection precedes discovery/admission, the Kraken-first path stays live, and this increment activates no mode and adds no scheduler."""
    run_cycle = RUN_CYCLE.read_text(encoding="utf-8")
    scan = SCAN.read_text(encoding="utf-8")
    protection_phase = run_cycle.index(
        "monitor_active_main()\n    finally:\n        _run_paper_v2_protection_fail_open()"
    )
    first_discovery = run_cycle.index(
        "_run_broad_discovery_if_due(\n            decision=decision"
    )
    assert protection_phase < first_discovery
    assert scan.index("protection_sweep = _run_paper_v2_protection_sweep(") < scan.index(
        "protection_sweep.new_admissions_allowed"
    ) < scan.index("paper_v2_summary = _route_paper_v2_opportunities(")
    assert "_run_protection_health" not in run_cycle


# ---------------------------------------------------------------------------
# AC-007 genuinely non-mutating read seam
# ---------------------------------------------------------------------------


def test_ac_007_read_json_without_quarantine_does_not_move_corrupt_file(tmp_path):
    """ATDD-R4-F11-precutover-protection/AC-007: the observer's read seam raises on a corrupt registry without quarantining (moving) it."""
    corrupt = tmp_path / "registry.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(RegistryCorruptionError) as excinfo:
        read_json_without_quarantine(corrupt)
    # No quarantine path was recorded, and the source file still exists.
    assert excinfo.value.quarantine_path is None
    assert corrupt.exists()
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_ac_007_active_trade_loader_is_non_mutating(tmp_path, monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-007: the read-only active-trade loader fails closed on a corrupt registry or a non-object row and moves nothing."""
    import app.services.active_trade_registry as registry

    corrupt = tmp_path / "active_trades.json"
    corrupt.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(registry, "TRADE_FILE", corrupt)
    with pytest.raises(RegistryCorruptionError):
        registry.read_active_trades_without_mutation()
    assert corrupt.exists()
    assert list(tmp_path.glob("*.corrupt-*")) == []

    # A non-object row is corruption, not "no holdings": it must fail closed so a
    # malformed entry can never be dropped and reported HEALTHY.
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"SOLUSD": "garbage"}', encoding="utf-8")
    monkeypatch.setattr(registry, "TRADE_FILE", malformed)
    with pytest.raises(RegistryIOError):
        registry.read_active_trades_without_mutation()
    assert malformed.exists()


# ---------------------------------------------------------------------------
# AC-008 materiality: no economically positive holding is hidden
# ---------------------------------------------------------------------------


def test_ac_008_observer_disables_the_materiality_floor(monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-008: a positive unmanaged holding below the resolver's default materiality floor is surfaced by the observer's zero floor, so zero-silent-holdings is not violated."""
    import app.services.kraken_exposure_resolver as ker

    class _Private:
        enabled = True

        def assert_read_only(self):
            return SimpleNamespace(name="ro")

        def get_balance(self):
            return {"SOL": 0.01}

        def get_open_positions(self):
            return {}

    monkeypatch.setattr(ker, "_pair_catalog", lambda client: {"SOL": "SOLUSD"})
    monkeypatch.setattr(
        ker,
        "_ticker_notionals",
        lambda client, *, quantities, pairs_by_asset: {"SOL": 5.0},
    )
    monkeypatch.setattr(ker, "_minimum_unmanaged_notional_usd", lambda: 25.0)

    def _resolver(floor):
        return ker.KrakenExposureResolver(
            private_client=_Private(),
            public_client=object(),
            trade_loader=lambda: [],
            minimum_unmanaged_notional_usd=floor,
        )

    # Default floor (25 USD): the 5 USD positive holding is suppressed.
    default_view = _resolver(None).resolve()
    assert all(
        not (e.symbol == "SOLUSD" and e.status == "VERIFIED_UNMANAGED")
        for e in default_view.exposures
    )

    # Observer floor (0): the same holding is surfaced, so it cannot hide.
    observed = _resolver(0.0).resolve()
    assert any(
        e.symbol == "SOLUSD" and e.status == "VERIFIED_UNMANAGED"
        for e in observed.exposures
    )

    health = ph.evaluate_protection_health(
        observed.exposures, **_healthy_kwargs()
    )
    assert health.state == ph.STATE_UNSAFE
    assert "SOLUSD" in health.unmanaged_exposures


@pytest.mark.parametrize(
    "override,expected",
    [
        (0.0, 0.0),
        (-5.0, 0.0),
        (float("nan"), 0.0),
        (10**400, 0.0),
        # An override can only LOWER the floor: 1000 is clamped to the default.
        (1000.0, 25.0),
    ],
)
def test_ac_008_materiality_override_can_only_lower(override, expected):
    """ATDD-R4-F11-precutover-protection/AC-008: a non-finite, negative or oversized override resolves to zero without raising, and a positive override can only lower the floor (clamped to the default), never hide more exposure."""
    import app.services.kraken_exposure_resolver as ker

    resolver = object.__new__(ker.KrakenExposureResolver)
    resolver.minimum_unmanaged_notional_usd = override
    value = resolver._resolve_minimum_notional()
    assert value == expected, (override, value, expected)


def test_ac_008_materiality_override_cannot_widen_behavior(monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-008: an override above the default does not suppress more exposure than the default."""
    import app.services.kraken_exposure_resolver as ker

    monkeypatch.setattr(ker, "_minimum_unmanaged_notional_usd", lambda: 25.0)
    resolver = object.__new__(ker.KrakenExposureResolver)
    resolver.minimum_unmanaged_notional_usd = 1000.0
    assert resolver._resolve_minimum_notional() == 25.0


# ---------------------------------------------------------------------------
# AC-009 LONG/SHORT protection geometry
# ---------------------------------------------------------------------------


def test_ac_009_long_stop_must_be_below_entry():
    """ATDD-R4-F11-precutover-protection/AC-009: a LONG protection plan is proven only when a positive stop sits below the entry; a same-side or above-entry stop is not protection."""
    ok = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=90.0, direction="LONG"),),
        **_healthy_kwargs(),
    )
    assert ok.state == ph.STATE_HEALTHY

    wrong_side = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=110.0, direction="LONG"),),
        **_healthy_kwargs(),
    )
    assert wrong_side.state == ph.STATE_UNSAFE
    assert wrong_side.geometry_invalid_exposures == ("SOLUSD",)
    assert wrong_side.silent_holdings == ()
    assert ph.REASON_GEOMETRY_INVALID in wrong_side.reason_codes

    equal = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=100.0, direction="LONG"),),
        **_healthy_kwargs(),
    )
    assert equal.state == ph.STATE_UNSAFE


def test_ac_009_short_stop_must_be_above_entry():
    """ATDD-R4-F11-precutover-protection/AC-009: a SHORT protection plan is proven only when a positive stop sits above the entry; a below-entry stop is not protection."""
    ok = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=110.0, direction="SHORT"),),
        **_healthy_kwargs(),
    )
    assert ok.state == ph.STATE_HEALTHY

    wrong_side = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=90.0, direction="SHORT"),),
        **_healthy_kwargs(),
    )
    assert wrong_side.state == ph.STATE_UNSAFE
    assert ph.REASON_GEOMETRY_INVALID in wrong_side.reason_codes

    unknown_direction = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=90.0, direction="SIDEWAYS"),),
        **_healthy_kwargs(),
    )
    assert unknown_direction.state == ph.STATE_UNAVAILABLE
    assert unknown_direction.uncertain_exposures == ("SOLUSD",)


# ---------------------------------------------------------------------------
# AC-010 overflow fail-closed and single-document stdout
# ---------------------------------------------------------------------------


def test_ac_010_oversized_int_fails_closed():
    """ATDD-R4-F11-precutover-protection/AC-010: an oversized integer stop fails closed without raising OverflowError."""
    huge = 10**400
    assert ph._positive_finite(huge) is False
    health = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=100.0, stop=huge),), **_healthy_kwargs()
    )
    assert health.state == ph.STATE_UNSAFE
    assert health.silent_holdings == ("SOLUSD",)


def test_ac_010_cli_stdout_is_one_json_document(monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-010: the CLI writes exactly one valid JSON document to stdout; banners go to stderr."""
    import app.jobs.report_protection_health as report

    resolution = SimpleNamespace(
        exposures=(_exposure("VERIFIED_MANAGED"),), coverage_complete=True
    )
    monkeypatch.setattr(
        report, "_read_only_resolver", lambda: SimpleNamespace(resolve=lambda: resolution)
    )
    monkeypatch.setattr(
        report,
        "read_incident_projection",
        lambda: report.IncidentProjection(health=True, open_incidents=()),
    )

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        report.main()

    parsed = json.loads(out.getvalue())  # exactly one document, valid JSON
    assert parsed["state"] == ph.STATE_HEALTHY
    assert "READ ONLY" not in out.getvalue()
    assert "READ ONLY" in err.getvalue()


# ---------------------------------------------------------------------------
# AC-011 incident health and explicit ownership (no latch)
# ---------------------------------------------------------------------------


def test_ac_011_incident_health_required_for_healthy():
    """ATDD-R4-F11-precutover-protection/AC-011: protection-incident health must be proven before HEALTHY; an open incident or unproven health withholds admissions."""
    open_incident = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=False,
    )
    assert open_incident.state == ph.STATE_UNAVAILABLE
    assert open_incident.admissions_suspended is True
    assert ph.REASON_INCIDENTS_UNHEALTHY in open_incident.reason_codes

    unproven = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=None,
    )
    assert unproven.state == ph.STATE_UNAVAILABLE
    assert ph.REASON_INCIDENTS_UNPROVEN in unproven.reason_codes

    proven = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=True,
    )
    assert proven.state == ph.STATE_HEALTHY


def test_ac_011_corrupt_incident_row_is_unproven(monkeypatch):
    """ATDD-R4-F11-precutover-protection/AC-011: a structurally corrupt protection-incident record is unproven (None), never reported healthy."""
    import app.jobs.report_protection_health as report

    healthy_doc = {"incidents": {"KRAKEN:RATE_LIMIT": {"scope": "KRAKEN:RATE_LIMIT", "state": "OPEN"}}}
    corrupt_doc = {"incidents": {"KRAKEN:HELD_ASSET_PRICING": "garbage"}}
    recovered_doc = {
        "incidents": {
            "KRAKEN:PUBLIC_CONNECTIVITY": {
                "scope": "KRAKEN:PUBLIC_CONNECTIVITY",
                "state": system_incidents.STATE_RECOVERED,
            }
        }
    }

    monkeypatch.setattr(
        report, "read_json_without_quarantine", lambda path: corrupt_doc
    )
    assert report.protection_incidents_healthy() is None

    monkeypatch.setattr(
        report, "read_json_without_quarantine", lambda path: healthy_doc
    )
    assert report.protection_incidents_healthy() is False

    monkeypatch.setattr(
        report, "read_json_without_quarantine", lambda path: recovered_doc
    )
    assert report.protection_incidents_healthy() is True


def test_ac_009_managed_without_valid_entry_is_silent_not_geometry():
    """ATDD-R4-F11-precutover-protection/AC-009: a managed holding with a positive stop but an invalid entry has no provable plan, so it is silent rather than geometry-invalid."""
    health = ph.evaluate_protection_health(
        (_exposure("VERIFIED_MANAGED", entry=0.0, stop=90.0, direction="LONG"),),
        **_healthy_kwargs(),
    )
    assert health.state == ph.STATE_UNSAFE
    assert health.silent_holdings == ("SOLUSD",)
    assert health.geometry_invalid_exposures == ()


def test_ac_011_no_suspension_latch_or_write_surface():
    """ATDD-R4-F11-precutover-protection/AC-011: F11 owns no suspension latch or human-resume decision and writes nothing; the contract states the owning authority explicitly."""
    for module in (HEALTH_MODULE, REPORT_JOB):
        source = module.read_text(encoding="utf-8")
        for token in ("os.replace", "save_json", "open(", "write_text", "write("):
            assert token not in source, (module.name, token)

    text = _contract_text()
    assert "owns NO suspension latch" in text
    assert "incident lifecycle" in text
    assert "new_admissions_allowed" in text
