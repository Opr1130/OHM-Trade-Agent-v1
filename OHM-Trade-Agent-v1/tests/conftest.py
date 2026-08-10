import pytest

from app.services import trade_outcome_registry


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
