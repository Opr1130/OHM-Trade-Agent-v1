"""R4-B1 dormant target-spine wiring: behavioral acceptance guards.

Proves the off-by-default gate, the unified-cycle hook (after protection, only
when enabled, fail-open), the inert-no-source path, reachability through the real
R4-B0 spine when explicitly test-enabled, and that no new authority is created.

The reachability guard drives the *real* production spine via the R4-B0
composition harness, so it proves the runner executes a genuine composition
rather than a stub.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.opip.contracts.portfolio import PortfolioStatus
from app.opip.forecast import TrustedForecastModelRegistry
from app.services import target_spine_cycle as tsc

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
RUN_CYCLE = APP_ROOT / "app" / "jobs" / "run_cycle.py"
SCAN = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"
TARGET_SPINE_MODULE = APP_ROOT / "app" / "services" / "target_spine_cycle.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "kraken_private",
    "exchanges",
    "committee",
    "writer",
    "canonical",
    "storage",
    "paper_v2_execution",
    "portfolio_paper_handoff",
)

#: The target-spine module may import only this application surface. Any other
#: `app.` import fails closed, so a future write routed through an unlisted
#: import cannot evade a fixed token deny-list.
ALLOWED_APP_IMPORT_PREFIXES = ("app.core.config",)

FORBIDDEN_CALL_TOKENS = (
    ".submit(",
    "admit_paper_opportunity",
    "trigger_paper_protection_action",
    "WriterIntent",
    "insert_event",
)


class _Stub:
    def __init__(self, mode: str) -> None:
        self.opip_target_spine_mode = mode


def _settings_kwargs(**overrides):
    return {"_env_file": None, "webhook_secret": "x" * 20, **overrides}


def test_ac_007_gate_defaults_and_fails_closed():
    """ATDD-R4-B1-runtime-integration-dormant/AC-007: the target-spine gate defaults to off, accepts off and shadow, rejects an invalid value, and resolves fail-closed."""
    assert Settings(**_settings_kwargs()).opip_target_spine_mode == "off"
    assert (
        Settings(**_settings_kwargs(opip_target_spine_mode="shadow")).opip_target_spine_mode
        == "shadow"
    )
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(opip_target_spine_mode="active"))

    # Resolve fails closed for an unknown or missing value.
    assert tsc.resolve_target_spine_mode(_Stub("bogus")) == "off"
    assert tsc.resolve_target_spine_mode(_Stub("")) == "off"
    assert tsc.resolve_target_spine_mode(_Stub("shadow")) == "shadow"
    assert tsc.target_spine_enabled(_Stub("off")) is False
    assert tsc.target_spine_enabled(_Stub("shadow")) is True


def test_ac_008_hook_runs_after_protection_and_only_when_enabled(monkeypatch):
    """ATDD-R4-B1-runtime-integration-dormant/AC-008: the hook is placed after the protection phase, invokes the composition only when shadow, and never raises."""
    source = RUN_CYCLE.read_text(encoding="utf-8")
    call_site = "    _run_target_spine_fail_open()"
    assert source.count(call_site) == 1
    hook_index = source.index(call_site)
    # The hook must run after the REAL protection phase, i.e. after the LAST
    # protection invocation in the file - not merely after the first
    # ``monitor_active_main()``, which is the degraded early-return branch.
    assert hook_index > source.rindex("_run_paper_v2_protection_fail_open()")
    assert hook_index > source.rindex("monitor_active_main()")

    import app.jobs.run_cycle as rc

    calls: list[dict] = []
    monkeypatch.setattr(rc, "get_settings", lambda: _Stub("shadow"))

    def _record(**kwargs):
        calls.append(kwargs)
        return tsc.TargetSpineSummary(mode="shadow", inert=True, reason="TEST")

    monkeypatch.setattr(tsc, "run_target_spine_cycle", _record)
    # Enabled -> invoked.
    monkeypatch.setattr(tsc, "target_spine_enabled", lambda settings: True)
    rc._run_target_spine_fail_open()
    assert len(calls) == 1

    # Disabled -> not invoked at all.
    calls.clear()
    monkeypatch.setattr(tsc, "target_spine_enabled", lambda settings: False)
    rc._run_target_spine_fail_open()
    assert calls == []

    # A failing composition never aborts the cycle.
    monkeypatch.setattr(tsc, "target_spine_enabled", lambda settings: True)

    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tsc, "run_target_spine_cycle", _boom)
    rc._run_target_spine_fail_open()  # must not raise

    # A malformed summary (raising attribute access) must not abort the cycle
    # either: reporting is inside the same fail-open guard.
    class _BadSummary:
        mode = "shadow"
        inert = True
        reason = None
        considered = 0
        selected = 0
        abstained = 0
        vetoed = 0
        cash_no_trade = 0
        handoffs_built = 0

        @property
        def errors(self):
            raise RuntimeError("malformed summary")

    monkeypatch.setattr(tsc, "run_target_spine_cycle", lambda **kwargs: _BadSummary())
    rc._run_target_spine_fail_open()  # must not raise


def test_ac_009_inert_without_source_and_writes_nothing():
    """ATDD-R4-B1-runtime-integration-dormant/AC-009: with no snapshot source the run is a recorded inert no-op, and the module imports and calls no funded, exchange, order, protection or Committee authority."""
    off = tsc.run_target_spine_cycle(settings=_Stub("off"), snapshots=())
    assert off.inert is True and off.reason == tsc.REASON_MODE_OFF

    no_source = tsc.run_target_spine_cycle(settings=_Stub("shadow"), snapshots=())
    assert no_source.inert is True
    assert no_source.reason == tsc.REASON_NO_SNAPSHOT_SOURCE
    assert no_source.considered == 0

    source = TARGET_SPINE_MODULE.read_text(encoding="utf-8")
    for token in FORBIDDEN_CALL_TOKENS:
        assert token not in source, token
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for name in modules:
        lowered = name.lower()
        for forbidden in FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in lowered, (name, forbidden)
        # Any application import must be allowlisted, so a write routed through
        # an unlisted canonical/storage import cannot evade the deny-list.
        if name.startswith("app."):
            assert name.startswith(ALLOWED_APP_IMPORT_PREFIXES), name


def test_ac_010_reachable_when_test_enabled_with_real_spine():
    """ATDD-R4-B1-runtime-integration-dormant/AC-010: when test-enabled, the runner composes the real R4-B0 spine, keeps the dispositions distinct and counts the handoff, without creating any admission."""
    from tests import test_opip_r4_b0_spine_composition as r4b0

    snapshot = r4b0._feature_snapshot()

    def _compose(one_snapshot) -> tsc.TargetSpineDisposition:
        result = r4b0._compose(direction="LONG")
        selected = result.decision.status is PortfolioStatus.SELECTED
        return tsc.TargetSpineDisposition(
            snapshot_id=one_snapshot.snapshot_id,
            disposition=(
                tsc.DISPOSITION_SELECTED if selected else tsc.DISPOSITION_ABSTAINED
            ),
            selected=selected,
            handoff_built=len(result.handoffs) == 1,
        )

    summary = tsc.run_target_spine_cycle(
        settings=_Stub("shadow"), snapshots=(snapshot,), compose=_compose
    )
    assert summary.inert is False
    assert summary.considered == 1
    assert summary.selected == 1
    assert summary.handoffs_built == 1

    # A zero-admissible-model environment abstains distinctly and builds no handoff.
    def _compose_zero_model(one_snapshot) -> tsc.TargetSpineDisposition:
        result = r4b0._compose(registry=TrustedForecastModelRegistry.empty())
        abstained = result.decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
        return tsc.TargetSpineDisposition(
            snapshot_id=one_snapshot.snapshot_id,
            disposition=(
                tsc.DISPOSITION_ABSTAINED if abstained else tsc.DISPOSITION_SELECTED
            ),
            selected=not abstained,
            handoff_built=len(result.handoffs) == 1,
        )

    zero = tsc.run_target_spine_cycle(
        settings=_Stub("shadow"), snapshots=(snapshot,), compose=_compose_zero_model
    )
    assert zero.abstained == 1
    assert zero.selected == 0
    assert zero.handoffs_built == 0

    # Every disposition counter is distinct and is never collapsed or mis-mapped.
    # Counts are deliberately asymmetric so swapping two counters is detectable.
    from types import SimpleNamespace

    plan = {
        "S1": "SELECTED",
        "S2": "SELECTED",
        "A1": "INSUFFICIENT_EVIDENCE",
        "A2": "INSUFFICIENT_EVIDENCE",
        "A3": "INSUFFICIENT_EVIDENCE",
        "V1": "VETO",
        "C1": "CASH_NO_TRADE",
        "C2": "CASH_NO_TRADE",
        "C3": "CASH_NO_TRADE",
        "C4": "CASH_NO_TRADE",
    }

    def _compose_fixed(one_snapshot) -> tsc.TargetSpineDisposition:
        return tsc.TargetSpineDisposition(
            snapshot_id=one_snapshot.snapshot_id,
            disposition=plan[one_snapshot.snapshot_id],
            handoff_built=plan[one_snapshot.snapshot_id] == tsc.DISPOSITION_SELECTED,
        )

    def _raise(one_snapshot) -> tsc.TargetSpineDisposition:
        raise RuntimeError("compose failed")

    error_snapshots = tuple(
        SimpleNamespace(snapshot_id=f"E{index}") for index in range(2)
    )
    snapshots = tuple(SimpleNamespace(snapshot_id=key) for key in plan)
    counted = tsc.run_target_spine_cycle(
        settings=_Stub("shadow"),
        snapshots=(*snapshots, *error_snapshots),
        compose=lambda one: (
            _raise(one) if one.snapshot_id.startswith("E") else _compose_fixed(one)
        ),
    )
    assert counted.considered == 12
    assert counted.selected == 2
    assert counted.abstained == 3
    assert counted.vetoed == 1
    assert counted.cash_no_trade == 4
    assert counted.errors == 2
    assert counted.handoffs_built == 2


def test_ac_011_posture_reconciled_and_no_new_authority():
    """ATDD-R4-B1-runtime-integration-dormant/AC-011: the unified cycle still references no target-spine internal module, the repository posture is unchanged, and the wiring adds no authority."""
    run_cycle = RUN_CYCLE.read_text(encoding="utf-8")
    scan = SCAN.read_text(encoding="utf-8")
    for forbidden in ("portfolio_selector", "portfolio_paper_handoff", "evaluate_forecast"):
        assert forbidden not in run_cycle, forbidden
        assert forbidden not in scan, forbidden

    settings = Settings(**_settings_kwargs())
    assert settings.opip_target_spine_mode == "off"
    assert settings.opip_feature_bus_mode == "off"
    assert settings.opip_paper_v2_mode == "off"
