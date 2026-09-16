import os

import pytest

from app.jobs import scan_opportunities
from app.services import (
    attention_budget,
    chief_alert_notifier,
    chief_analyst,
    notification_policy,
    trade_outcome_registry,
)


@pytest.fixture(autouse=True)
def preserve_legacy_candidate_selector_test_scope(monkeypatch):
    """Let legacy tests patch select_candidates while production uses directional selection."""
    scan_opportunities.select_candidates = scan_opportunities.select_directional_candidates
    monkeypatch.setattr(
        scan_opportunities,
        "select_directional_candidates",
        lambda items: scan_opportunities.select_candidates(items),
    )


@pytest.fixture(autouse=True)
def preserve_legacy_chief_payload_test_scope(request, monkeypatch):
    """Keep the asset-dedup payload test focused on payload/API-call behavior."""
    if request.node.name != "test_one_underlying_asset_is_one_chief_payload_and_one_api_call":
        return
    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, account_equity: ({}, True),
    )


@pytest.fixture(autouse=True)
def isolate_outcome_registry_with_existing_trade_fixtures(request, monkeypatch):
    """Keep lifecycle/notification writes inside the test temp directory."""
    if "registry_files" not in request.fixturenames:
        return

    tmp_path = request.getfixturevalue("tmp_path")
    monkeypatch.setattr(
        trade_outcome_registry,
        "OUTCOME_FILE",
        tmp_path / "trade_outcomes.json",
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "STATE_LOCK_FILE",
        tmp_path / ".alert_state.lock",
    )
    monkeypatch.setattr(
        notification_policy,
        "STATE_FILE",
        tmp_path / "notification_state.json",
    )
    monkeypatch.setattr(
        notification_policy,
        "LOCK_FILE",
        tmp_path / ".notification_state.lock",
    )
    monkeypatch.setattr(
        attention_budget,
        "STATE_FILE",
        tmp_path / "attention_budget_state.json",
    )


@pytest.fixture(autouse=True)
def stub_authoritative_directory_durability_on_windows(request, monkeypatch):
    """Keep Windows logic tests from falsely claiming namespace durability.

    PR-A0 fails closed for authoritative directory durability on Windows, because
    no documented Windows primitive proves that a directory entry reached durable
    storage (see ``app.opip.canonical.schema._flush_directory_windows``). Linux CI
    exercises the real ``os.fsync(dirfd)`` path and is unaffected by this fixture.

    Local Windows runs still need to exercise publication/recovery *sequencing*
    and ownership/cleanup logic, so on Windows the authoritative primitive is
    stubbed at its call sites. This is deliberately narrow:

    * only ``backup`` and ``recovery`` call-site references are replaced;
    * ``app.opip.canonical.schema.fsync_directory_required`` itself is untouched,
      so tests that call it directly still observe the real fail-closed contract;
    * tests named ``test_windows_real_*`` opt out entirely to observe real
      behaviour end-to-end.

    Production code is not weakened by this: on Windows it raises
    ``CanonicalDurabilityError`` and publication cannot report success.
    """
    if os.name != "nt" or request.node.name.startswith("test_windows_real_"):
        return

    from app.opip.canonical import backup as canonical_backup
    from app.opip.canonical import recovery as canonical_recovery

    monkeypatch.setattr(
        canonical_backup, "fsync_directory_required", lambda _directory: None
    )
    monkeypatch.setattr(
        canonical_recovery, "fsync_directory_required", lambda _directory: None
    )
