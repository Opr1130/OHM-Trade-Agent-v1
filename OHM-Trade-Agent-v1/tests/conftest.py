import pytest

from app.services import chief_analyst


@pytest.fixture(autouse=True)
def preserve_legacy_chief_payload_test_scope(request, monkeypatch):
    """Keep the pre-existing dedup/payload test focused on its original contract.

    API-cost-control viability semantics have dedicated tests. The legacy test
    named below verifies one-underlying-asset deduplication and payload shape,
    so deterministic viability is stubbed only for that single test.
    """
    if request.node.name != "test_one_underlying_asset_is_one_chief_payload_and_one_api_call":
        return

    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, account_equity: ({}, True),
    )
