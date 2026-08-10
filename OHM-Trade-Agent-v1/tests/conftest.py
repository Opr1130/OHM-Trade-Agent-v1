import pytest

from app.services import chief_analyst, trade_outcome_registry


@pytest.fixture(autouse=True)
def preserve_legacy_chief_payload_test_scope(request, monkeypatch):
    """Keep the pre-existing dedup/payload test focused on its original contract."""
    if request.node.name != "test_one_underlying_asset_is_one_chief_payload_and_one_api_call":
        return

    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, account_equity: ({}, True),
    )


@pytest.fixture(autouse=True)
def isolate_outcome_registry_with_existing_trade_fixtures(request, monkeypatch):
    """Keep outcome writes inside the temp directory used by lifecycle tests."""
    if "registry_files" not in request.fixturenames:
        return

    tmp_path = request.getfixturevalue("tmp_path")
    monkeypatch.setattr(
        trade_outcome_registry,
        "OUTCOME_FILE",
        tmp_path / "trade_outcomes.json",
    )
