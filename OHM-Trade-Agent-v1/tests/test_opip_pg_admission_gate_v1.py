"""ATDD-OPIP-PG-ADMISSION-GATE-V1: the scan's NEW-admission decision consults the business-store authority."""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.opip.data_platform import admission_gate as gate
from app.opip.data_platform import business_store as bs
from app.services.notification_policy import authorize_new_trade_notification

pytestmark = pytest.mark.acceptance

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH = "OPIP_BUSINESS_STORE_AUTHORITY"


def _no_database(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the FILES authority must not touch the database layer")

    monkeypatch.setattr(bs, "connection_factory_from_config", boom)
    monkeypatch.setattr(bs.BusinessStore, "__init__", boom)


@pytest.mark.parametrize("env", [{}, {AUTH: ""}, {AUTH: "files"}, {AUTH: " FILES "}])
def test_ac_001_files_authority_is_inert_and_touches_no_database(monkeypatch, env):
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-001: unset, empty and FILES admit without any database layer."""
    _no_database(monkeypatch)
    assert gate.business_store_refusal_reason(env) is None


@pytest.mark.parametrize("quality", [None, "QUALITY_LOW"])
@pytest.mark.parametrize("attempts", [0, 3])
def test_ac_001_notification_decision_is_unchanged_without_a_store_reason(quality, attempts):
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-001: omitting or passing None for the new argument changes nothing."""
    base = authorize_new_trade_notification(quality_reason=quality, delivery_attempts=attempts, max_per_window=3)
    explicit = authorize_new_trade_notification(
        quality_reason=quality, delivery_attempts=attempts, max_per_window=3, business_store_reason=None
    )
    assert base == explicit
    assert authorize_new_trade_notification(
        quality_reason=quality, delivery_attempts=attempts, max_per_window=3, business_store_reason=""
    ) == base


class _Store:
    def __init__(self, authority="POSTGRES", epoch=3, raises=False):
        self.authority_epoch, self._a, self._e, self._r = 3, authority, epoch, raises

    def read_authority(self):
        if self._r:
            raise bs.StoreUnavailable("down")
        return self._a, self._e


PG = {AUTH: "POSTGRES", "OPIP_BUSINESS_STORE_DATABASE_URL": "postgresql://x", "OPIP_BUSINESS_STORE_EPOCH": "3"}


@pytest.mark.parametrize(
    "env, store, reason",
    [
        ({AUTH: "POSTGRESS"}, None, "BUSINESS_STORE_INVALID_AUTHORITY_CONFIG"),
        ({AUTH: "POSTGRES"}, None, "BUSINESS_STORE_POSTGRES_STORE_NOT_CONFIGURED"),
        ({AUTH: "POSTGRES", "OPIP_BUSINESS_STORE_DATABASE_URL": "postgresql://x"}, None,
         "BUSINESS_STORE_POSTGRES_STORE_NOT_CONFIGURED"),
        (PG, _Store(raises=True), "BUSINESS_STORE_POSTGRES_UNAVAILABLE"),
        (PG, _Store(authority="FILES"), "BUSINESS_STORE_AUTHORITY_NOT_POSTGRES"),
        (PG, _Store(epoch=4), "BUSINESS_STORE_AUTHORITY_EPOCH_MISMATCH"),
        (PG, _Store(), None),
    ],
)
def test_ac_002_every_unproven_postgres_state_refuses_with_a_stable_reason(monkeypatch, env, store, reason):
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-002: only a proven authoritative database at the configured epoch admits."""
    monkeypatch.setattr(bs, "connection_factory_from_config", lambda config: (lambda: None))
    monkeypatch.setattr(bs, "BusinessStore", lambda factory, authority_epoch: store)
    assert gate.business_store_refusal_reason(env) == reason


def test_ac_002_unexpected_errors_refuse_and_never_raise(monkeypatch):
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-002: any surprise while PostgreSQL is configured is a refusal."""
    def explode(*_a, **_k):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(bs, "connection_factory_from_config", explode)
    assert gate.business_store_refusal_reason(PG) == "BUSINESS_STORE_GATE_ERROR:RuntimeError"
    monkeypatch.undo()
    monkeypatch.setattr(bs, "new_admissions_allowed", explode)
    assert gate.business_store_refusal_reason(PG) == "BUSINESS_STORE_GATE_ERROR:RuntimeError"


def test_ac_002_store_reason_suppresses_the_notification_but_never_hides_a_quality_reason():
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-002: the store refusal suppresses; an earlier quality refusal keeps priority."""
    reason = "BUSINESS_STORE_POSTGRES_UNAVAILABLE"
    assert authorize_new_trade_notification(
        quality_reason=None, delivery_attempts=0, max_per_window=3, business_store_reason=reason
    ) == (False, reason)
    assert authorize_new_trade_notification(
        quality_reason="QUALITY_LOW", delivery_attempts=0, max_per_window=3, business_store_reason=reason
    ) == (False, "QUALITY_LOW")


def test_ac_003_scan_evaluates_the_gate_once_after_the_protection_sweep():
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-003: protection runs first; the gate is evaluated once and passed on."""
    source = (APP_ROOT / "app" / "jobs" / "scan_opportunities.py").read_text(encoding="utf-8")
    assert source.count("business_store_refusal_reason()") == 1
    assert source.count("business_store_reason=business_store_reason") == 1
    assert source.index("_run_paper_v2_protection_sweep(") < source.index("business_store_refusal_reason()")


def test_ac_003_only_the_scan_imports_the_adapter():
    """ATDD-OPIP-PG-ADMISSION-GATE-V1/AC-003: F11, protection, exits and emergency alerts cannot depend on PostgreSQL."""
    importers: set[str] = set()
    for path in (APP_ROOT / "app").rglob("*.py"):
        if path.name == "admission_gate.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            if any(n.endswith("admission_gate") for n in names):
                importers.add(path.relative_to(APP_ROOT).as_posix())
    assert importers == {"app/jobs/scan_opportunities.py"}
