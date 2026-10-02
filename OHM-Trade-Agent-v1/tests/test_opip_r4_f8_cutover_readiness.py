"""R4-F8 paper-v2 cutover readiness and independent protection.

These tests prove the R4-A increment: bounded, fail-closed cutover-readiness
evidence, the aggregate readiness verdict that distinguishes READINESS from
ACTIVATION, Paper-v2 protection that no longer depends on opportunity discovery,
the SHORT-authority and pending-mandate gaps being visible, and that no authority
is activated by this increment.

No test here activates Paper v2, wires F7 as the admission authority, enables the
Feature Bus or the Committee, or touches a funded order path.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs import run_cycle
from app.services import paper_v2_cutover_readiness as readiness
from app.services.paper_v2_cutover_readiness import (
    CanonicalWriterEvidence,
    CutoverEvidence,
    DirectionCoverage,
    ModeEvidence,
    PendingMandate,
    ProtectionEvidence,
    RollbackEvidence,
    SelectorHandoff,
)

APP_ROOT = Path(__file__).resolve().parents[1]
INCREMENT = "ATDD-R4-F8-paper-v2-cutover-readiness"

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _healthy_evidence(**overrides) -> CutoverEvidence:
    """A fully healthy technical evidence bundle, with overridable dimensions."""
    defaults = dict(
        mode=ModeEvidence(status=readiness.EVIDENCE_READY, mode="off"),
        drain=readiness.LegacyDrainStatus(
            status=readiness.DRAIN_READY, reason="legacy drained"
        ),
        protection=ProtectionEvidence(
            status=readiness.EVIDENCE_READY, independent_of_discovery=True
        ),
        universe=readiness.observe_universe_gate_evidence(),
        direction=DirectionCoverage(long_covered=True, short_covered=True),
        pending=PendingMandate(
            status=readiness.PAPER_V2_PENDING_MANDATE,
            immediate_only=True,
            requires_pending_lifecycle=False,
        ),
        selector=SelectorHandoff(
            status=readiness.EVIDENCE_READY,
            fields_compatible=True,
            admission_source_active=False,
        ),
        canonical_writer=CanonicalWriterEvidence(status=readiness.EVIDENCE_READY),
        rollback=RollbackEvidence(status=readiness.EVIDENCE_READY),
    )
    defaults.update(overrides)
    return CutoverEvidence(**defaults)


def _install_drain_sources(
    monkeypatch,
    *,
    freqtrade_status="OK",
    freqtrade_open=0,
    outstanding=0,
    v1_pending=0,
    v1_open=0,
):
    monkeypatch.setattr(
        "app.services.freqtrade_result_ingest.freqtrade_dry_run_status",
        lambda **kwargs: {
            "status": freqtrade_status,
            "open_trades": freqtrade_open,
        },
    )
    monkeypatch.setattr(
        "app.services.freqtrade_signal_bridge.outstanding_admitted_signals",
        lambda **kwargs: [{}] * outstanding,
    )
    monkeypatch.setattr(
        "app.services.paper_trade_control.paper_trade_enabled", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "app.services.paper_trade_registry.account_summary",
        lambda equity, **kwargs: SimpleNamespace(
            pending_entries=v1_pending,
            open_positions=v1_open,
            reserved_capital=0.0,
        ),
    )


class _CycleRecorder:
    def __init__(self) -> None:
        self.order: list[str] = []

    def record(self, name: str):
        def _inner(*args, **kwargs):
            self.order.append(name)
            return None

        return _inner


def _drive_cycle(monkeypatch, *, mode="SEARCH", scan=None, operator_raises=False, settings=None):
    """Drive the real ``_run_cycle_once`` with only outer seams stubbed."""
    recorder = _CycleRecorder()
    settings = settings or SimpleNamespace(tradingview_v2_enabled=False)
    decision = SimpleNamespace(
        override_mode=mode,
        effective_mode=mode,
        occupied_slots=0,
        active_trades=0,
        live_order_intents=0,
        pending_setups=0,
        quiet_hours=False,
        reason="test",
    )

    monkeypatch.setattr(
        run_cycle,
        "reconcile_kraken_account",
        lambda: SimpleNamespace(
            status="OK",
            mode="observe",
            active_checked=0,
            order_intents_checked=0,
            open_orders_seen=0,
            fills_seen=0,
            would_close=(),
            closed=(),
            would_fill=(),
            filled=(),
            reason=None,
        ),
    )
    if operator_raises:
        def _boom():
            raise RuntimeError("operator state offline")

        monkeypatch.setattr(run_cycle, "get_operator_decision", _boom)
    else:
        monkeypatch.setattr(run_cycle, "get_operator_decision", lambda: decision)
    monkeypatch.setattr(run_cycle, "_notify_monitor_degraded", lambda **kw: None)
    monkeypatch.setattr(
        run_cycle, "_close_operator_state_incident_if_open", lambda: None
    )
    monkeypatch.setattr(run_cycle, "get_settings", lambda: settings)
    monkeypatch.setattr(run_cycle, "search_due", lambda _decision: True)
    monkeypatch.setattr(run_cycle, "mark_search_started", lambda: None)
    monkeypatch.setattr(run_cycle, "mark_search_finished", lambda *a, **k: None)
    monkeypatch.setattr(run_cycle, "recover_interrupted_search", lambda: False)
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: None)
    monkeypatch.setattr(run_cycle, "_run_early_watch_if_due", lambda **kw: None)
    monkeypatch.setattr(run_cycle, "_run_paper_monitor_fail_open", lambda: None)
    monkeypatch.setattr(run_cycle, "_run_event_intelligence_fail_open", lambda **kw: None)
    monkeypatch.setattr(run_cycle, "_run_external_order_review_fail_open", lambda: None)
    monkeypatch.setattr(run_cycle, "_run_learning_fail_open", lambda: None)
    monkeypatch.setattr(
        run_cycle, "_run_qualified_alert_retry_fail_open", lambda **kw: None
    )
    monkeypatch.setattr(
        run_cycle, "_run_entry_watch_recheck_fail_open", lambda: False
    )

    if scan is None:

        def scan(_job):
            return None

    monkeypatch.setattr(run_cycle, "run_scan_with_telemetry", scan)

    monkeypatch.setattr(
        run_cycle, "monitor_active_main", recorder.record("monitor_active")
    )
    monkeypatch.setattr(
        run_cycle,
        "run_scheduled_paper_v2_protection_sweep",
        lambda settings, *, requested: recorder.record("paper_v2_protection")(
            settings, requested=requested
        ),
    )
    return recorder


# ===========================================================================
# AC-001 — bounded live evidence
# ===========================================================================


def test_ac_001_default_is_not_live_evidence(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: an unreadable mode is unavailable, never the default."""
    monkeypatch.setattr(readiness, "_process_settings", lambda: None)
    evidence = readiness.observe_mode_evidence(None)
    assert evidence.status == readiness.EVIDENCE_UNAVAILABLE
    assert evidence.mode is None
    assert evidence.reason_code == readiness.REASON_MODE_UNAVAILABLE

    monkeypatch.setattr(
        readiness,
        "_process_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("no config")),
    )
    evidence = readiness.observe_mode_evidence(None)
    assert evidence.status == readiness.EVIDENCE_UNAVAILABLE
    assert evidence.mode is None
    assert evidence.reason_code == readiness.REASON_MODE_UNAVAILABLE


def test_ac_001_missing_state_is_unavailable():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: a settings object without the mode field is not evidence."""
    evidence = readiness.observe_mode_evidence(SimpleNamespace())
    assert evidence.status == readiness.EVIDENCE_UNAVAILABLE
    assert evidence.mode is None


@pytest.mark.parametrize("raw", ["Active", "ACTIVE", " active ", "", "unexpected", 1, True, None])
def test_ac_001_malformed_state_fails_closed(raw):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: a near-miss mode fails closed, never activating."""
    evidence = readiness.observe_mode_evidence(SimpleNamespace(opip_paper_v2_mode=raw))
    assert evidence.status == readiness.EVIDENCE_UNAVAILABLE
    assert evidence.reason_code == readiness.REASON_MODE_UNAVAILABLE


def test_ac_001_repository_default_is_not_live_mode_evidence():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: a settings field default is not observed live evidence."""
    from app.core.config import Settings

    defaulted = Settings(_env_file=None, webhook_secret="x" * 20)
    assert "opip_paper_v2_mode" not in defaulted.model_fields_set
    assert (
        readiness.observe_mode_evidence(defaulted).status
        == readiness.EVIDENCE_UNAVAILABLE
    )

    explicit = Settings(
        _env_file=None, webhook_secret="x" * 20, opip_paper_v2_mode="off"
    )
    evidence = readiness.observe_mode_evidence(explicit)
    assert evidence.status == readiness.EVIDENCE_READY
    assert evidence.mode == "off"


def test_ac_001_report_contains_no_environment_or_secrets(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: the report never dumps the environment or a secret."""
    sentinel = "SENTINEL_SECRET_VALUE_1234567890"
    monkeypatch.setenv("OPIP_TEST_SECRET_SENTINEL", sentinel)
    report = readiness.evaluate_cutover_readiness(_healthy_evidence())
    dumped = json.dumps(report.to_dict(), sort_keys=True)
    assert sentinel not in dumped
    assert "OPIP_TEST_SECRET_SENTINEL" not in dumped
    # Only the typed facts are reported, not arbitrary configuration.
    assert set(report.to_dict()) == {
        "overall",
        "reason_codes",
        "activation_prerequisites",
        "evidence",
    }


def test_ac_001_report_bounds_drain_reason_text(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-001: raw drain exception text is never serialized."""
    sentinel = "malformed_net_pnl_987654321"
    monkeypatch.setattr(
        "app.services.freqtrade_result_ingest.freqtrade_dry_run_status",
        lambda **kwargs: (_ for _ in ()).throw(ValueError(sentinel)),
    )
    drain = readiness.evaluate_legacy_drain(starting_equity=10_000.0)
    # The evaluator's own reason still carries the detail for the scan's print.
    assert sentinel in drain.reason
    report = readiness.evaluate_cutover_readiness(_healthy_evidence(drain=drain))
    dumped = json.dumps(report.to_dict(), sort_keys=True)
    assert sentinel not in dumped
    assert (
        report.to_dict()["evidence"]["drain"]["reason_code"]
        == "LEGACY_DRAIN_UNAVAILABLE"
    )


# ===========================================================================
# AC-002 — legacy drain
# ===========================================================================


def test_ac_002_drain_ready_when_legacy_empty(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-002: zero legacy exposure resolves to READY."""
    _install_drain_sources(monkeypatch)
    status = readiness.evaluate_legacy_drain(starting_equity=10_000.0)
    assert status.status == readiness.DRAIN_READY
    assert status.ready is True


@pytest.mark.parametrize(
    ("freqtrade_open", "outstanding", "v1_pending", "v1_open"),
    [
        (1, 0, 0, 0),
        (0, 1, 0, 0),
        (0, 0, 1, 0),
        (0, 0, 0, 1),
    ],
)
def test_ac_002_drain_draining_while_any_obligation_remains(
    monkeypatch, freqtrade_open, outstanding, v1_pending, v1_open
):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-002: every legacy obligation state resolves to DRAINING."""
    _install_drain_sources(
        monkeypatch,
        freqtrade_open=freqtrade_open,
        outstanding=outstanding,
        v1_pending=v1_pending,
        v1_open=v1_open,
    )
    status = readiness.evaluate_legacy_drain(starting_equity=10_000.0)
    assert status.status == readiness.DRAIN_DRAINING
    assert status.ready is False


def test_ac_002_drain_unavailable_when_unreadable(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-002: an unreadable legacy source is UNAVAILABLE, never drained."""
    monkeypatch.setattr(
        "app.services.freqtrade_result_ingest.freqtrade_dry_run_status",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("legacy store offline")),
    )
    status = readiness.evaluate_legacy_drain(starting_equity=10_000.0)
    assert status.status == readiness.DRAIN_UNAVAILABLE
    assert status.ready is False


# ===========================================================================
# AC-003 — protection independence from discovery
# ===========================================================================


def test_ac_003_cycle_runs_protection_before_discovery(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: the cycle protects before discovery."""
    recorder = _drive_cycle(monkeypatch, mode="SEARCH")
    run_cycle._run_cycle_once()
    assert "monitor_active" in recorder.order
    assert "paper_v2_protection" in recorder.order
    assert recorder.order.index("monitor_active") < recorder.order.index(
        "paper_v2_protection"
    )


def test_ac_003_protection_runs_when_discovery_is_skipped(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: MAINTENANCE skips discovery but still protects."""
    recorder = _drive_cycle(monkeypatch, mode="MAINTENANCE")
    run_cycle._run_cycle_once()
    assert "paper_v2_protection" in recorder.order


def test_ac_003_protection_runs_when_discovery_throws(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: a throwing scanner does not stop protection."""

    def _explode(_job):
        raise RuntimeError("scanner exploded")

    recorder = _drive_cycle(monkeypatch, mode="SEARCH", scan=_explode)
    with pytest.raises(RuntimeError, match="scanner exploded"):
        run_cycle._run_cycle_once()
    assert "paper_v2_protection" in recorder.order


def test_ac_003_protection_runs_when_operator_state_is_unreadable(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: degraded mode still protects existing exposure."""
    recorder = _drive_cycle(monkeypatch, mode="SEARCH", operator_raises=True)
    run_cycle._run_cycle_once()
    assert "paper_v2_protection" in recorder.order


def test_ac_003_protection_runs_when_active_monitor_throws(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: a raising active monitor still lets paper protection advance."""
    recorder = _drive_cycle(monkeypatch, mode="MAINTENANCE")
    monkeypatch.setattr(
        run_cycle,
        "monitor_active_main",
        lambda: (_ for _ in ()).throw(RuntimeError("monitor exploded")),
    )
    # The monitor's own error still propagates; protection runs first regardless.
    with pytest.raises(RuntimeError, match="monitor exploded"):
        run_cycle._run_cycle_once()
    assert "paper_v2_protection" in recorder.order


def test_ac_003_cycle_protection_is_fail_open(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-003: a protection failure never aborts the cycle."""
    monkeypatch.setattr(run_cycle, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        run_cycle,
        "run_scheduled_paper_v2_protection_sweep",
        lambda settings, *, requested: (_ for _ in ()).throw(RuntimeError("canonical offline")),
    )
    run_cycle._run_paper_v2_protection_fail_open()  # must not raise


# ===========================================================================
# AC-004 — protection ordering and idempotency
# ===========================================================================


def test_ac_004_scan_protection_precedes_admission():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-004: in the scan, protection precedes admission routing."""
    source = (APP_ROOT / "app" / "jobs" / "scan_opportunities.py").read_text("utf-8")
    tree = ast.parse(source)
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    protection_lines = [
        node.lineno
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_paper_v2_protection_sweep"
    ]
    routing_lines = [
        node.lineno
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_route_paper_v2_opportunities"
    ]
    assert protection_lines and routing_lines
    assert max(protection_lines) < min(routing_lines)


def test_ac_004_protection_sweep_is_idempotent_and_read_only():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-004: a repeated sweep is byte-identical and writes nothing."""
    from app.opip.canonical.models import PaperV2ProtectionWork
    from app.services.paper_v2_protection_runtime import run_protection_sweep

    class _ReadOnlyClient:
        def __init__(self):
            self.reads = 0
            self.writes = 0

        def get_paper_v2_protection_work(self):
            self.reads += 1
            return PaperV2ProtectionWork(status="OK", items=())

        def __getattr__(self, name):  # any mutation attempt is a failure
            if name.startswith("_") or name.startswith("get_"):
                raise AttributeError(name)

            def _unexpected(*args, **kwargs):
                self.writes += 1
                raise AssertionError(f"unexpected write via {name}")

            return _unexpected

    client = _ReadOnlyClient()
    first = run_protection_sweep(client, kraken_client=object(), settings=SimpleNamespace())
    second = run_protection_sweep(client, kraken_client=object(), settings=SimpleNamespace())
    assert first == second
    assert client.reads == 2
    assert client.writes == 0
    assert first.new_admissions_allowed is True


# ===========================================================================
# AC-005 — universe metadata gate
# ===========================================================================


def test_ac_005_universe_metadata_gate_fails_closed():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-005: absent universe metadata is reported, not re-requested."""
    unobserved = readiness.observe_universe_gate_evidence(None)
    assert unobserved.enforced_fail_closed is True
    assert unobserved.status == readiness.EVIDENCE_READY

    empty = readiness.observe_universe_gate_evidence(())
    assert empty.status == readiness.EVIDENCE_UNAVAILABLE
    assert empty.reason_code == readiness.REASON_UNIVERSE_NOT_OBSERVED

    observed = readiness.observe_universe_gate_evidence((object(),))
    assert observed.status == readiness.EVIDENCE_READY
    assert observed.observed_asset_count == 1

    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(universe=empty)
    )
    assert readiness.REASON_UNIVERSE_NOT_OBSERVED in report.reason_codes


# ===========================================================================
# AC-006 — SHORT authority gap
# ===========================================================================


def test_ac_006_direction_coverage_reflects_router_authority():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-006: direction coverage is derived from the target route's supported directions. At R4-F8 the route was long-only; R4-B2 supplied genuine SHORT authority (owner mandate), so both directions now report covered with no gap."""
    coverage = readiness.observe_direction_coverage()
    assert coverage.long_covered is True
    assert coverage.short_covered is True
    assert coverage.reason_code is None

    report = readiness.evaluate_cutover_readiness(_healthy_evidence(direction=coverage))
    assert readiness.REASON_SHORT_AUTHORITY_MISSING not in report.reason_codes
    assert report.overall == readiness.READINESS_READY


def test_ac_006_long_coverage_gap_is_reported(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-006: a missing LONG coverage is named, never reported as no gap."""
    monkeypatch.setattr(
        "app.services.paper_v2_scan_router.SUPPORTED_DIRECTIONS", frozenset({"SHORT"})
    )
    coverage = readiness.observe_direction_coverage()
    assert coverage.long_covered is False
    assert coverage.reason_code == readiness.REASON_LONG_AUTHORITY_MISSING
    report = readiness.evaluate_cutover_readiness(_healthy_evidence(direction=coverage))
    assert readiness.REASON_LONG_AUTHORITY_MISSING in report.reason_codes


# ===========================================================================
# AC-007 — pending-entry mandate / WAIT
# ===========================================================================


def test_ac_007_pending_mandate_is_documented_immediate_only():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-007: the current paper mandate needs no pending lifecycle."""
    mandate = readiness.observe_pending_mandate()
    assert mandate.status == readiness.PAPER_V2_PENDING_MANDATE
    assert mandate.immediate_only is True
    assert mandate.requires_pending_lifecycle is False


def test_ac_007_unsupported_pending_mandate_fails_closed():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-007: an unknown pending requirement blocks readiness."""
    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(
            pending=PendingMandate(
                status="UNKNOWN",
                immediate_only=False,
                requires_pending_lifecycle=True,
            )
        )
    )
    assert readiness.REASON_PENDING_MANDATE_UNKNOWN in report.reason_codes


# ===========================================================================
# AC-008 — selector handoff contract
# ===========================================================================


def test_ac_008_selector_handoff_fields_compatible():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-008: the frozen F7 records expose the handoff fields."""
    handoff = readiness.observe_selector_handoff()
    assert handoff.status == readiness.EVIDENCE_READY
    assert handoff.fields_compatible is True
    assert handoff.admission_source_active is False
    # The R4-B wiring bridge is named and is deliberately not implemented here.
    assert readiness.REQUIRED_SELECTOR_CANDIDATE_BRIDGE == "PCAND_TO_OPIPC"


def test_ac_008_f7_is_not_the_admission_authority():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-008: neither run_cycle nor the scan imports F7."""
    for relative in ("app/jobs/run_cycle.py", "app/jobs/scan_opportunities.py"):
        source = (APP_ROOT / relative).read_text("utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name.startswith(("app.opip.portfolio_selector", "app.opip.portfolio_comparator"))
            for name in imported
        ), relative


# ===========================================================================
# AC-009 — isolation
# ===========================================================================


def _read_source(relative: str) -> str:
    return (APP_ROOT / relative).read_text("utf-8")


def test_ac_009_new_code_activates_no_authority():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-009: the new code activates no mode, bus, committee or order path."""
    for relative in (
        "app/services/paper_v2_cutover_readiness.py",
        "app/jobs/report_paper_v2_cutover_readiness.py",
    ):
        source = _read_source(relative)
        tree = ast.parse(source)
        # No assignment to the activation switch, and no environment mutation.
        targets: set[str] = set()
        subscript_writes = 0
        setenv_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        targets.add(target.id)
                    elif isinstance(target, ast.Attribute):
                        targets.add(target.attr)
                    elif isinstance(target, ast.Subscript):
                        subscript_writes += 1
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "setenv":
                    setenv_calls += 1
        assert "opip_paper_v2_mode" not in targets
        assert subscript_writes == 0
        assert setenv_calls == 0
        # No Committee, Feature Bus, or exchange-client import.
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            assert not name.startswith("app.opip.committee")
            assert not name.startswith("app.opip.features")
            assert not name.startswith("app.exchanges")


def test_ac_009_no_funded_or_exchange_credentials_added():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-009: no funded order API or exchange credential is introduced."""
    source = _read_source("app/services/paper_v2_cutover_readiness.py")
    for forbidden in (
        "place_order",
        "cancel_order",
        "modify_order",
        "KRAKEN_API_KEY",
        "KRAKEN_API_SECRET",
        "private_balance",
    ):
        assert forbidden not in source, forbidden


# ===========================================================================
# AC-010 — rollback
# ===========================================================================


def test_ac_010_rollback_path_remains():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-010: the activation default stays off and rollback is available."""
    from app.core.config import Settings

    assert Settings.model_fields["opip_paper_v2_mode"].default == "off"
    assert readiness.observe_rollback_evidence().status == readiness.EVIDENCE_READY
    # An unprovable default is not a rollback.
    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(
            rollback=RollbackEvidence(
                status=readiness.EVIDENCE_UNAVAILABLE,
                reason_code=readiness.REASON_ROLLBACK_UNAVAILABLE,
            )
        )
    )
    assert readiness.REASON_ROLLBACK_UNAVAILABLE in report.reason_codes


# ===========================================================================
# AC-011 — aggregate verdict
# ===========================================================================


def test_ac_011_verdict_ready_when_all_technical_gates_pass():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-011: healthy technical evidence resolves to READY."""
    report = readiness.evaluate_cutover_readiness(_healthy_evidence())
    assert report.overall == readiness.READINESS_READY
    assert report.reason_codes == ()
    assert report.ready is True


def test_ac_011_verdict_reflects_direction_coverage(monkeypatch):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-011: complete direction coverage permits READY, and a route missing a direction is NOT_READY with the named reason."""
    # The target route now supports LONG and SHORT: coverage complete, READY.
    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(direction=readiness.observe_direction_coverage())
    )
    assert report.overall == readiness.READINESS_READY
    assert report.reason_codes == ()

    # A route missing LONG is NOT_READY with the named missing-authority reason.
    monkeypatch.setattr(
        "app.services.paper_v2_scan_router.SUPPORTED_DIRECTIONS", frozenset({"SHORT"})
    )
    blocked = readiness.evaluate_cutover_readiness(
        _healthy_evidence(direction=readiness.observe_direction_coverage())
    )
    assert blocked.overall == readiness.READINESS_NOT_READY
    assert readiness.REASON_LONG_AUTHORITY_MISSING in blocked.reason_codes


def test_ac_011_unreadable_evidence_fails_closed():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-011: unreadable facts become UNAVAILABLE, never favorable."""
    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(
            mode=ModeEvidence(
                status=readiness.EVIDENCE_UNAVAILABLE,
                reason_code=readiness.REASON_MODE_UNAVAILABLE,
            ),
            drain=readiness.LegacyDrainStatus(
                status=readiness.DRAIN_UNAVAILABLE, reason="unreadable"
            ),
            protection=ProtectionEvidence(
                status=readiness.EVIDENCE_UNAVAILABLE,
                independent_of_discovery=False,
                reason_code=readiness.REASON_PROTECTION_UNAVAILABLE,
            ),
            selector=SelectorHandoff(
                status=readiness.EVIDENCE_UNAVAILABLE,
                fields_compatible=False,
                admission_source_active=False,
                reason_code=readiness.REASON_SELECTOR_HANDOFF_UNAVAILABLE,
            ),
            canonical_writer=CanonicalWriterEvidence(
                status=readiness.EVIDENCE_UNAVAILABLE,
                reason_code=readiness.REASON_CANONICAL_WRITER_UNAVAILABLE,
            ),
        )
    )
    assert report.overall == readiness.READINESS_NOT_READY
    for expected in (
        readiness.REASON_MODE_UNAVAILABLE,
        readiness.REASON_LEGACY_DRAIN_UNAVAILABLE,
        readiness.REASON_PROTECTION_UNAVAILABLE,
        readiness.REASON_PROTECTION_NOT_INDEPENDENT,
        readiness.REASON_CANONICAL_WRITER_UNAVAILABLE,
        readiness.REASON_SELECTOR_HANDOFF_UNAVAILABLE,
    ):
        assert expected in report.reason_codes, expected


def test_ac_011_unavailable_equity_fails_closed():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-011: an unprovable starting equity blocks the drain rather than defaulting."""
    for equity in (None, 0.0, -5.0, "not-a-number"):
        kwargs = {"opip_paper_v2_mode": "off"}
        if equity is not None:
            kwargs["paper_trade_starting_equity"] = equity
        report = readiness.cutover_readiness_report(SimpleNamespace(**kwargs), client=None)
        assert report.evidence.drain.status == readiness.DRAIN_UNAVAILABLE, equity
        assert readiness.REASON_LEGACY_DRAIN_UNAVAILABLE in report.reason_codes


# ===========================================================================
# AC-012 — READINESS is not ACTIVATION
# ===========================================================================


def test_ac_012_mode_inactive_is_a_prerequisite_not_a_blocker():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-012: an inactive mode is an owner prerequisite, not a technical gap."""
    report = readiness.evaluate_cutover_readiness(
        _healthy_evidence(mode=ModeEvidence(status=readiness.EVIDENCE_READY, mode="off"))
    )
    assert "PAPER_V2_MODE_NOT_ACTIVE" in report.activation_prerequisites
    assert "SELECTOR_ADMISSION_NOT_ACTIVE" in report.activation_prerequisites
    assert readiness.REASON_MODE_UNAVAILABLE not in report.reason_codes


def test_ac_012_readiness_never_activates():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-012: computing readiness writes no activation switch."""
    source = _read_source("app/services/paper_v2_cutover_readiness.py")
    for mutation in ("setenv", "environ[", "os.environ"):
        assert mutation not in source, mutation
    report = readiness.cutover_readiness_report(
        SimpleNamespace(
            opip_paper_v2_mode="off",
            paper_trade_starting_equity=10_000.0,
        ),
        client=None,
    )
    assert report.overall in (readiness.READINESS_READY, readiness.READINESS_NOT_READY)
    # Readiness never reports a granted authority; it reports evidence only.
    assert report.overall != "ACTIVATED"


def test_ac_012_baseline_triage_recorded():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-012: the R4 baseline triage is recorded as durable evidence."""
    triage = APP_ROOT / "docs" / "atdd" / "evidence" / "R4-F8-BASELINE-TRIAGE.md"
    assert triage.is_file()
    text = triage.read_text("utf-8")
    for required in (
        "environment/platform-only",
        "production-relevant",
        "51 failed",
        "SHORT_AUTHORITY_MISSING",
    ):
        assert required in text, required


# ===========================================================================
# AC-013 — the readiness report job is read-only
# ===========================================================================


def test_ac_013_readiness_job_is_read_only(capsys):
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-013: the job prints one machine-readable report."""
    from app.jobs import report_paper_v2_cutover_readiness as job

    job.main()
    out = capsys.readouterr().out
    assert "CUTOVER READINESS" in out
    assert "Readiness:" in out
    assert "Reason codes:" in out


# ===========================================================================
# AC-014 — protection-readiness safety
# ===========================================================================


class _ProtectionClient:
    def __init__(self, items):
        self._items = tuple(items)

    def get_paper_v2_protection_work(self):
        from app.opip.canonical.models import PaperV2ProtectionWork

        return PaperV2ProtectionWork(status="OK", items=self._items)


def test_ac_014_unsafe_exposure_blocks_protection_readiness():
    """ATDD-R4-F8-paper-v2-cutover-readiness/AC-014: unplanned open exposure is unsafe and blocks readiness, read-only."""
    from app.opip.canonical.models import PaperV2ProtectionWorkItem

    unsafe = PaperV2ProtectionWorkItem(
        paper_trade_id="PTV2:unsafe", remaining_quantity=5.0, protection_plan=None
    )
    evidence = readiness.observe_protection_evidence(_ProtectionClient([unsafe]))
    assert evidence.status == readiness.EVIDENCE_BLOCKED
    assert evidence.reason_code == readiness.REASON_PROTECTION_UNSAFE

    report = readiness.evaluate_cutover_readiness(_healthy_evidence(protection=evidence))
    assert readiness.REASON_PROTECTION_UNSAFE in report.reason_codes

    # A flat or terminal exposure is healthy.
    safe = PaperV2ProtectionWorkItem(
        paper_trade_id="PTV2:safe", remaining_quantity=0.0, final_verified=True
    )
    assert (
        readiness.observe_protection_evidence(_ProtectionClient([safe])).status
        == readiness.EVIDENCE_READY
    )
