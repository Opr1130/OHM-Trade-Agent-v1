"""B/C-4D read-only cockpit API tests.

The most important assertions here are structural rather than behavioural: the
cockpit surface must be **read-only by construction**, so a caller cannot reach a
mutation through it, and it must never present an unreadable store as an empty
healthy result.
"""

from __future__ import annotations

import json

import pytest

from app.api import cockpit
from app.opip.cockpit.ledger import PaperV2Ledger
from app.opip.cockpit.trust import Completeness, Freshness


class _ReadOnlyClient:
    """A canonical client stub exposing only the ledger read."""

    def __init__(self, ledger: PaperV2Ledger) -> None:
        self._ledger = ledger

    def get_paper_v2_ledger(self) -> PaperV2Ledger:
        return self._ledger


class _ExplodingClient:
    def get_paper_v2_ledger(self):  # noqa: ANN201 - deliberately fails
        raise RuntimeError("socket closed")


@pytest.fixture(autouse=True)
def _bypass_secret(monkeypatch):
    """Authenticate every call so the tests exercise behaviour, not auth.

    Authentication itself is shared with the existing analytics endpoints and is
    covered by their own tests; here the subject is read-only authority and trust
    reporting.
    """
    monkeypatch.setattr(cockpit, "_require_secret", lambda value: None)


# ---------------------------------------------------------------------------
# Read-only authority, enforced structurally
# ---------------------------------------------------------------------------


def test_every_cockpit_route_is_a_get():
    """No route on this surface may accept a mutating method."""
    methods = cockpit.route_methods()
    assert methods, "cockpit router exposes no routes"
    for path, allowed in methods.items():
        assert allowed == {"GET"}, f"{path} accepts {sorted(allowed)}"


def test_cockpit_module_imports_no_write_surface():
    """The API module must not import or call any write surface.

    Checked structurally via the module AST rather than by substring: a docstring
    that explains *why* the surface is read-only must not be mistaken for a write
    path, while a real import or call to a mutating API must be caught.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(cockpit.__file__).read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    for banned in (
        "app.opip.canonical.writer",
        "app.services.paper_v2_execution",
        "app.services.paper_v2_protection_runtime",
    ):
        assert banned not in imported_modules, f"cockpit API imports {banned}"

    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned_call in (
        "submit",
        "trigger_paper_protection_action",
        "admit_paper_opportunity",
        "place_order",
        "cancel_order",
        "withdraw",
    ):
        assert banned_call not in called_attributes, (
            f"cockpit API calls write surface {banned_call!r}"
        )


def test_cockpit_api_declares_no_post_or_put_routes():
    source = __import__("pathlib").Path(cockpit.__file__).read_text(encoding="utf-8")
    for decorator in ('@router.post', '@router.put', '@router.patch', '@router.delete'):
        assert decorator not in source, f"cockpit API declares {decorator}"


# ---------------------------------------------------------------------------
# Unreadable store is reported, never disguised
# ---------------------------------------------------------------------------


def test_overview_reports_unavailable_writer_rather_than_empty_success(monkeypatch):
    monkeypatch.setattr(cockpit, "_writer_client", lambda: None)

    payload = cockpit.cockpit_overview(x_webhook_secret=None)

    assert payload["portfolios"] == []
    assert payload["trust"]["is_healthy"] is False
    assert payload["details"]
    assert payload["as_of"]


def test_unavailable_overview_has_the_same_shape_as_a_healthy_one(monkeypatch):
    """A consumer must process the unavailable case through one code path.

    Review finding (valid): the unavailable payload used an ``entries`` key and
    omitted the overview's own keys, so callers would only discover the different
    schema when something was already wrong.
    """
    monkeypatch.setattr(cockpit, "_writer_client", lambda: None)
    unavailable = cockpit.cockpit_overview(x_webhook_secret=None)

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )
    healthy = cockpit.cockpit_overview(x_webhook_secret=None)

    assert set(unavailable) == set(healthy)
    assert isinstance(unavailable["portfolios"], list)
    assert isinstance(unavailable["attention"], list)


def test_unavailable_trade_list_has_the_same_shape_as_a_healthy_one(monkeypatch):
    monkeypatch.setattr(cockpit, "_writer_client", lambda: None)
    unavailable = cockpit.cockpit_trades(
        quote_currency=None, limit=25, x_webhook_secret=None
    )

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )
    healthy = cockpit.cockpit_trades(
        quote_currency=None, limit=25, x_webhook_secret=None
    )

    assert set(unavailable) == set(healthy)
    assert unavailable["count"] == 0
    assert unavailable["filters"] == healthy["filters"]


def test_overview_reports_an_unhealthy_ledger_trust_state(monkeypatch):
    monkeypatch.setattr(
        cockpit,
        "_writer_client",
        lambda: _ReadOnlyClient(
            PaperV2Ledger(status="RETRYABLE", error_code="WORKER_UNHEALTHY")
        ),
    )

    payload = cockpit.cockpit_overview(x_webhook_secret=None)

    assert payload["trust"]["is_healthy"] is False
    assert payload["trust"]["completeness"] == Completeness.UNKNOWN.value
    assert payload["valuation"]["status"] == "UNKNOWN"
    assert payload["valuation"]["is_known"] is False


def test_read_failure_does_not_raise_into_the_caller(monkeypatch):
    """A client that blows up surfaces as an unhealthy envelope, not a 500."""
    monkeypatch.setattr(cockpit, "_writer_client", lambda: _ExplodingClient())

    payload = cockpit.cockpit_overview(x_webhook_secret=None)

    assert payload["trust"]["is_healthy"] is False
    assert payload["portfolios"] == []
    assert payload["details"]


# ---------------------------------------------------------------------------
# Envelope metadata and filters
# ---------------------------------------------------------------------------


def test_overview_carries_the_full_semantic_and_trust_envelope(monkeypatch):
    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )

    payload = cockpit.cockpit_overview(x_webhook_secret=None)

    for field in (
        "as_of",
        "timezone",
        "trust",
        "projection_version",
        "ledger_projection_version",
        "valuation",
        "attention",
    ):
        assert field in payload, f"overview payload missing {field}"
    assert payload["timezone"] == "UTC"
    trust = payload["trust"]
    for dimension in (
        "freshness",
        "completeness",
        "fidelity",
        "uncertainty",
        "correction_state",
    ):
        assert dimension in trust, f"trust envelope missing {dimension}"
    # The dimensions must stay independent rather than collapsing into one score.
    assert "is_healthy" in trust


def test_trade_list_reports_its_population_and_filters(monkeypatch):
    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )

    payload = cockpit.cockpit_trades(
        quote_currency=None, limit=25, x_webhook_secret=None
    )

    assert payload["filters"] == {"quote_currency": None, "limit": 25}
    assert payload["population"]
    assert payload["count"] == 0
    assert payload["entries"] == []
    assert payload["timezone"] == "UTC"


def test_unsupported_currency_filter_fails_safely(monkeypatch):
    """An unknown currency must 400 rather than be silently ignored."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )

    with pytest.raises(HTTPException) as excinfo:
        cockpit.cockpit_trades(
            quote_currency="EUR", limit=10, x_webhook_secret=None
        )
    assert excinfo.value.status_code == 400


def test_currency_filter_is_normalised_but_validated(monkeypatch):
    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )
    payload = cockpit.cockpit_trades(
        quote_currency="usdt", limit=10, x_webhook_secret=None
    )
    assert payload["filters"]["quote_currency"] == "USDT"


@pytest.mark.parametrize("limit", [0, -1, cockpit.MAX_TRADES + 1])
def test_out_of_range_limit_fails_safely(monkeypatch, limit):
    from fastapi import HTTPException

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )

    with pytest.raises(HTTPException) as excinfo:
        cockpit.cockpit_trades(
            quote_currency=None, limit=limit, x_webhook_secret=None
        )
    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# Trade detail
# ---------------------------------------------------------------------------


def test_unknown_trade_is_404_when_the_ledger_is_healthy(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )

    with pytest.raises(HTTPException) as excinfo:
        cockpit.cockpit_trade_detail("PTV2:nope", x_webhook_secret=None)
    assert excinfo.value.status_code == 404


def test_missing_trade_in_an_unhealthy_ledger_is_not_claimed_as_absent(monkeypatch):
    """An unreadable ledger cannot prove a trade does not exist."""
    monkeypatch.setattr(
        cockpit,
        "_writer_client",
        lambda: _ReadOnlyClient(PaperV2Ledger(status="RETRYABLE")),
    )

    payload = cockpit.cockpit_trade_detail("PTV2:any", x_webhook_secret=None)

    assert payload["found"] is False
    assert payload["trust"]["is_healthy"] is False
    assert payload["details"]
    # Shape-consistent with the healthy detail payload.
    assert {"as_of", "timezone", "projection_version", "found"} <= set(payload)


def test_empty_trade_id_is_rejected(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )
    with pytest.raises(HTTPException) as excinfo:
        cockpit.cockpit_trade_detail("   ", x_webhook_secret=None)
    assert excinfo.value.status_code == 400


def test_trade_detail_returns_the_dossier_fields(monkeypatch):
    """Trade Detail must expose identity, lifecycle, economics and lineage."""
    from datetime import datetime, timedelta, timezone

    from app.opip.canonical.models import PaperV2LedgerEntry

    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    def _exact(offset: int) -> dict:
        return {
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": (now + timedelta(seconds=offset))
            .isoformat()
            .replace("+00:00", "Z"),
        }

    entry = PaperV2LedgerEntry(
        paper_trade_id="PTV2:" + "a" * 64,
        quote_currency="USD",
        native_symbol="BTC/USD",
        policy_version="gate-v1",
        policy_fingerprint="fp1",
        entry_quantity=5.0,
        exited_quantity=5.0,
        remaining_quantity=0.0,
        entry_fills=(
            {
                "side": "BUY",
                "quantity": 5.0,
                "price": 100.0,
                "fee_cost": 0.1,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "other_supported_cost": 0.0,
                "fill_time": _exact(0),
                "economic_model_version": "opip-paper-economics-v2",
            },
        ),
        exit_fills=(
            {
                "side": "SELL",
                "quantity": 5.0,
                "price": 110.0,
                "fee_cost": 0.1,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "other_supported_cost": 0.0,
                "fill_time": _exact(600),
                "economic_model_version": "opip-paper-economics-v2",
            },
        ),
        first_entry_fill_time=_exact(0),
        last_exit_fill_time=_exact(600),
        entry_order_intent={"requested_quantity": 5.0},
        protection_plan={
            "protection_plan_id": "PPLAN:1",
            "plan_seq": 0,
            "stop_price": 90.0,
            "max_hold_seconds": 3600,
            "targets": [{"target_id": "TP1", "price": 110.0, "fraction": 1.0}],
        },
        latest_reconciliation={
            "reconciliation_seq": 1,
            "terminal_reconciliation_state": "FINAL_VERIFIED",
            "position_state": "FLAT",
            "realized_gross_pnl": 50.0,
            "recorded_execution_costs": 0.2,
            "realized_net_pnl": 49.8,
        },
        final_verified=True,
        event_ids=("e1", "e2", "e3"),
    )
    monkeypatch.setattr(
        cockpit,
        "_writer_client",
        lambda: _ReadOnlyClient(PaperV2Ledger(status="OK", entries=(entry,))),
    )

    payload = cockpit.cockpit_trade_detail(entry.paper_trade_id, x_webhook_secret=None)

    assert payload["found"] is True
    trade = payload["trade"]
    assert trade["policy_version"] == "gate-v1"
    assert trade["native_symbol"] == "BTC/USD"
    assert trade["planned_stop_price"] == pytest.approx(90.0)
    assert trade["net_pnl_definitive"] is True
    assert trade["economic_result"] == "WIN"
    assert trade["lifecycle_status"] == "CLOSED"
    assert trade["event_ids"] == ["e1", "e2", "e3"]
    # No secrets or credential material may appear anywhere in the dossier.
    serialized = json.dumps(payload).lower()
    for forbidden in ("secret", "api_key", "token", "password", "private"):
        assert forbidden not in serialized, f"dossier leaks {forbidden!r}"


def test_responses_are_json_serialisable(monkeypatch):
    monkeypatch.setattr(
        cockpit, "_writer_client", lambda: _ReadOnlyClient(PaperV2Ledger(status="OK"))
    )
    for payload in (
        cockpit.cockpit_overview(x_webhook_secret=None),
        cockpit.cockpit_trades(quote_currency=None, limit=10, x_webhook_secret=None),
    ):
        assert isinstance(json.loads(json.dumps(payload)), dict)
