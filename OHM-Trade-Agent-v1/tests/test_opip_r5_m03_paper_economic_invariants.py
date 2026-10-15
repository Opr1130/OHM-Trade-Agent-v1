"""Mission 03 paper-lifecycle economic invariants.

Increment: ``ATDD-R5-M03-paper-lifecycle-economic-invariants``.

The audit recorded one genuinely unproven item: `B16`, that the economic identity -
cash, fees, quantity, remaining obligation, fills and realized net economics - is
validated after every lifecycle transition. The existing B/C-3 suite proves the
transitions and the states; it does not recompute the economics from the frozen model
at each step.

This increment closes that, and only that. It drives the real production protection
runtime through entry fill, activation, a crossed stop, the exit fill, the flat
reconciliation and final verification, and after every step recomputes the canonical
economic identity from the frozen model in `app.opip.contracts.paper_economics`.

It changes no application module, activates no runtime, and invents no coefficient:
the fee rate and the slippage bound are read from the frozen registry, not restated.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:  # pragma: no cover - the suite already does this
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.paper_economics import (  # noqa: E402
    paper_economics_for_version,
)
from app.opip.contracts.paper_execution import (  # noqa: E402
    PAPER_ECONOMIC_MODEL_VERSION,
    ProtectionState,
)
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_FILL_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)

from tests.test_opip_paper_v2_protection_runtime_bc3 import (  # noqa: E402
    QUALIFICATION_TIME,
    _Clock,
    _Env,
    _item,
    _next_scan,
    _open_entry,
    _rows,
    _state_of,
    _sweep,
    _transport,
)

pytestmark = pytest.mark.acceptance

TOLERANCE = 1e-9


@pytest.fixture()
def env(tmp_path):
    holder = _Env(tmp_path=tmp_path)
    try:
        yield holder
    finally:
        holder.server.stop()


def _model():
    return paper_economics_for_version(PAPER_ECONOMIC_MODEL_VERSION)


def _assert_every_fill_matches_the_frozen_model(env: _Env, trade_id: str) -> int:
    """Recompute each committed fill's costs from the frozen model, exactly."""

    model = _model()
    fills = _rows(env, PAPER_FILL_RECORDED, trade_id)
    assert fills, "a transition with no committed fill is not an economic transition"

    for fill in fills:
        quantity = float(fill["quantity"])
        price = float(fill["price"])
        expected = model.cost_components(quantity, price)
        # The fill is bound to the frozen model, not to whatever settings were live.
        assert fill["economic_model_version"] == PAPER_ECONOMIC_MODEL_VERSION, fill
        assert abs(float(fill["fee_cost"]) - expected["fee_cost"]) < TOLERANCE, fill
        assert (
            abs(float(fill["slippage_cost"]) - expected["slippage_cost"]) < TOLERANCE
        ), fill
        assert abs(float(fill["spread_cost"]) - expected["spread_cost"]) < TOLERANCE, fill
        assert (
            abs(float(fill["other_supported_cost"]) - expected["other_supported_cost"])
            < TOLERANCE
        ), fill
        # Notional is the quantity at the executed price and nothing else.
        assert quantity > 0.0 and price > 0.0, fill
    return len(fills)


def _assert_quantity_identity(env: _Env, trade_id: str) -> None:
    """Entry minus exit equals the remaining obligation, at every step."""

    item = _item(env, trade_id)
    entry = sum(
        float(fill["quantity"])
        for fill in _rows(env, PAPER_FILL_RECORDED, trade_id)
        if str(fill["side"]) == "BUY"
    )
    exit_ = sum(
        float(fill["quantity"])
        for fill in _rows(env, PAPER_FILL_RECORDED, trade_id)
        if str(fill["side"]) == "SELL"
    )
    assert abs(entry - exit_ - float(item.remaining_quantity)) < TOLERANCE, (
        entry,
        exit_,
        item.remaining_quantity,
    )
    assert entry - exit_ >= -TOLERANCE, (entry, exit_)


def _assert_reconciliation_identity(env: _Env, trade_id: str) -> None:
    """Every committed reconciliation states exactly the committed totals."""

    fills = _rows(env, PAPER_FILL_RECORDED, trade_id)
    gross = sum(
        float(fill["quantity"])
        * float(fill["price"])
        * (1.0 if str(fill["side"]) == "SELL" else -1.0)
        for fill in fills
    )
    costs = sum(
        float(fill["fee_cost"])
        + float(fill["spread_cost"])
        + float(fill["slippage_cost"])
        + float(fill["other_supported_cost"])
        for fill in fills
    )
    entry = sum(
        float(fill["quantity"]) for fill in fills if str(fill["side"]) == "BUY"
    )
    exit_ = sum(
        float(fill["quantity"]) for fill in fills if str(fill["side"]) == "SELL"
    )

    for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id):
        assert abs(float(row["realized_gross_pnl"]) - gross) < TOLERANCE, row
        assert abs(float(row["recorded_execution_costs"]) - costs) < TOLERANCE, row
        assert (
            abs(float(row["realized_net_pnl"]) - (gross - costs)) < TOLERANCE
        ), row
        assert abs(float(row["filled_entry_quantity"]) - entry) < TOLERANCE, row
        assert abs(float(row["filled_exit_quantity"]) - exit_) < TOLERANCE, row
        assert abs(float(row["remaining_quantity"])) < TOLERANCE, row
        assert row["economic_model_version"] == PAPER_ECONOMIC_MODEL_VERSION, row


def _lifecycle(env: _Env):
    """Drive the real runtime through the whole lifecycle, checking at every step.

    Returns the trade identity and the per-step fill counts, so a test can assert
    what happened rather than only that nothing raised.
    """

    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]

    steps: list[tuple[str, int]] = []

    # 1. entry fill -> activation
    armed = _sweep(env, clock=clock)
    assert armed.activated == 1, armed.details
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    steps.append(
        ("activated", _assert_every_fill_matches_the_frozen_model(env, trade_id))
    )
    _assert_quantity_identity(env, trade_id)

    # 2. stop crossed -> atomic trigger and exit intent
    _next_scan(clock)
    triggered = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )
    assert triggered.triggered == 1, triggered.details
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    _assert_quantity_identity(env, trade_id)

    # 3. exit fill -> flat, not yet verified
    _next_scan(clock)
    exited = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )
    assert exited.exit_attempted == 1, exited.details
    steps.append(("exited", _assert_every_fill_matches_the_frozen_model(env, trade_id)))
    _assert_quantity_identity(env, trade_id)

    # 4. flat awaiting reconciliation
    flat = _sweep(env, clock=clock)
    assert flat.terminalized == 1, flat.details
    _assert_quantity_identity(env, trade_id)
    _assert_reconciliation_identity(env, trade_id)

    # 5. final verification
    final = _sweep(env, clock=clock)
    assert final.terminalized == 1, final.details
    steps.append(
        ("verified", _assert_every_fill_matches_the_frozen_model(env, trade_id))
    )
    _assert_quantity_identity(env, trade_id)
    _assert_reconciliation_identity(env, trade_id)

    # 6. a further sweep is a no-op and invents nothing
    again = _sweep(env, clock=clock)
    assert again.triggered == 0 and again.exit_attempted == 0
    _assert_every_fill_matches_the_frozen_model(env, trade_id)
    _assert_reconciliation_identity(env, trade_id)

    return trade_id, steps


# ---------------------------------------------------------------------------
# AC-320 the per-fill economic identity holds at every transition
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_320_every_fill_after_every_transition_matches_the_frozen_model(env):
    """ATDD-R5-M03-paper-lifecycle-economic-invariants/AC-320: no invented cost, ever.

    GIVEN the real protection runtime driving the whole lifecycle
    WHEN the committed fills are recomputed from the frozen economic registry after
    the entry fill and after the exit fill
    THEN every fill's fee, slippage, spread and other cost equal the frozen model
    applied to that fill's own quantity and price, and every fill names the frozen
    economic model version rather than the live settings
    """

    trade_id, steps = _lifecycle(env)

    assert [name for name, _ in steps] == ["activated", "exited", "verified"]
    assert [count for _, count in steps] == [1, 2, 2]

    fills = _rows(env, PAPER_FILL_RECORDED, trade_id)
    assert [str(fill["side"]) for fill in fills[:2]] == ["BUY", "SELL"]


# ---------------------------------------------------------------------------
# AC-321 the quantity and remaining-obligation identity holds at every step
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_321_entry_minus_exit_equals_the_remaining_obligation(env):
    """ATDD-R5-M03-paper-lifecycle-economic-invariants/AC-321: the obligation is conserved.

    GIVEN the same traversal
    WHEN the committed fill quantities are compared with the item's remaining
    quantity after every step
    THEN entry minus exit equals the remaining obligation at every point, never goes
    negative, and reaches exactly zero only when the position is flat
    """

    trade_id, _steps = _lifecycle(env)

    item = _item(env, trade_id)
    assert float(item.remaining_quantity) == 0.0
    fills = _rows(env, PAPER_FILL_RECORDED, trade_id)
    entry = sum(float(fill["quantity"]) for fill in fills if str(fill["side"]) == "BUY")
    exit_ = sum(
        float(fill["quantity"]) for fill in fills if str(fill["side"]) == "SELL"
    )
    assert entry == exit_ == 5.0


# ---------------------------------------------------------------------------
# AC-322 the reconciliation states exactly the committed totals
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_322_the_reconciliation_states_exactly_the_committed_totals(env):
    """ATDD-R5-M03-paper-lifecycle-economic-invariants/AC-322: net is gross minus cost.

    GIVEN a finalised position with a committed exit
    WHEN each committed reconciliation is recomputed from the committed fills
    THEN the gross is the signed notional sum, the recorded cost is the sum of the
    four cost components, the net is gross minus cost, and the filled and remaining
    quantities state the committed population rather than a derived one
    """

    trade_id, _steps = _lifecycle(env)

    rows = _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    assert [row["terminal_reconciliation_state"] for row in rows] == [
        "FLAT_AWAITING_RECONCILIATION",
        "FINAL_VERIFIED",
    ]
    final = rows[-1]
    # A long entry at the ask exiting into the bid loses on both legs, and fees are
    # charged on both, so the net is strictly worse than the gross.
    assert float(final["realized_gross_pnl"]) < 0.0
    assert float(final["recorded_execution_costs"]) > 0.0
    assert float(final["realized_net_pnl"]) < float(final["realized_gross_pnl"])
    assert float(final["reserved_capital"]) >= 0.0


# ---------------------------------------------------------------------------
# AC-323 the frozen model cannot drift and cannot be bypassed
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_323_the_frozen_economic_model_refuses_an_unknown_version():
    """ATDD-R5-M03-paper-lifecycle-economic-invariants/AC-323: an unknown model is not the current one.

    GIVEN the frozen economic registry
    WHEN an unknown economic model version is requested
    THEN the registry refuses rather than falling back to the live settings, so a
    historical execution can never be reinterpreted under today's coefficients
    """

    assert _model().economic_model_version == PAPER_ECONOMIC_MODEL_VERSION
    with pytest.raises(ValueError):
        paper_economics_for_version("economic-model-does-not-exist-v9")
    with pytest.raises(ValueError):
        paper_economics_for_version("")


