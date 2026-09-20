"""B/C-3 protection plan builder tests (increment 4b).

Proves the qualified opportunity's exit geometry is translated into the frozen
B/C-2 plan deterministically, long-only, and without inventing levels.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_PROTECTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_PROTECTION_PLAN_RECORDED,
    validate_paper_evidence_payload,
)
from app.services.paper_v2_protection_plan import (
    FIRST_TARGET_ID,
    RESIDUAL_TARGET_ID,
    ProtectionPlanSource,
    build_protection_plan_id,
    build_protection_plan_payload,
    protection_plan_idempotency_key,
)

PLAN_TIME = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
TRADE_ID = "PTV2:trade-1"


def _source(**overrides) -> ProtectionPlanSource:
    fields = {
        "paper_trade_id": TRADE_ID,
        "plan_seq": 0,
        "stop_price": 90.0,
        "target_prices": (110.0, 120.0),
        "plan_time": PLAN_TIME,
    }
    fields.update(overrides)
    return ProtectionPlanSource(**fields)


def _build(source=None, *, tp1_fraction=0.5, max_hold_seconds=86_400) -> dict:
    return build_protection_plan_payload(
        source or _source(),
        tp1_fraction=tp1_fraction,
        max_hold_seconds=max_hold_seconds,
    )


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def test_plan_is_accepted_by_the_frozen_contract():
    payload = _build()
    assert validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload) == payload


def test_plan_uses_the_opportunity_prices_verbatim():
    """Stop and target levels come from qualification, never from config."""
    payload = _build(_source(stop_price=88.5, target_prices=(111.5, 133.0)))
    assert payload["stop_price"] == 88.5
    assert [t["price"] for t in payload["targets"]] == [111.5, 133.0]


def test_targets_are_staged_and_conserve_the_whole_position():
    payload = _build(tp1_fraction=0.4)
    assert [t["target_id"] for t in payload["targets"]] == [
        FIRST_TARGET_ID,
        RESIDUAL_TARGET_ID,
    ]
    assert [t["fraction"] for t in payload["targets"]] == [0.4, pytest.approx(0.6)]
    assert sum(t["fraction"] for t in payload["targets"]) == pytest.approx(1.0)


def test_plan_carries_frozen_versions_and_engine():
    payload = _build()
    assert payload["engine"] == ENGINE_OPIP_PAPER_V2
    assert payload["protection_model_version"] == PAPER_PROTECTION_MODEL_VERSION
    assert payload["schema_version"] == 1


def test_plan_time_is_exact_source_reported():
    payload = _build()
    assert payload["plan_time"] == {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": "2026-09-19T12:00:00Z",
    }


def test_max_hold_comes_from_policy():
    payload = _build(max_hold_seconds=3_600)
    assert payload["max_hold_seconds"] == 3_600


# ---------------------------------------------------------------------------
# Deterministic identity
# ---------------------------------------------------------------------------


def test_plan_id_is_deterministic_for_the_same_trade_and_sequence():
    first = _build()
    second = _build()
    assert first["protection_plan_id"] == second["protection_plan_id"]


def test_plan_id_changes_with_the_plan_sequence():
    assert _build()["protection_plan_id"] != _build(_source(plan_seq=1))[
        "protection_plan_id"
    ]


def test_plan_id_changes_with_the_trade():
    other = build_protection_plan_id(paper_trade_id="PTV2:other", plan_seq=0)
    assert _build()["protection_plan_id"] != other


def test_plan_id_is_stable_across_policy_changes():
    """Identity names the plan, not the policy values that filled it."""
    assert (
        _build(tp1_fraction=0.5)["protection_plan_id"]
        == _build(tp1_fraction=0.25)["protection_plan_id"]
    )


def test_idempotency_key_is_derived_from_the_plan_identity():
    payload = _build()
    key = protection_plan_idempotency_key(payload)
    assert key == f"{PAPER_PROTECTION_PLAN_RECORDED}:{payload['protection_plan_id']}"


# ---------------------------------------------------------------------------
# Rejected geometry and policy
# ---------------------------------------------------------------------------


def test_stop_at_or_above_the_first_target_is_rejected():
    with pytest.raises(ValueError, match="stop_price must sit below"):
        _build(_source(stop_price=110.0))


def test_unsorted_targets_are_rejected():
    with pytest.raises(ValueError, match="ascend"):
        _build(_source(target_prices=(120.0, 110.0)))


def test_duplicate_target_prices_are_rejected():
    with pytest.raises(ValueError, match="distinct"):
        _build(_source(target_prices=(110.0, 110.0)))


def test_single_target_is_rejected():
    """A staged plan is required; a single level is not a staged plan."""
    with pytest.raises(ValueError, match="at least two qualified targets"):
        _build(_source(target_prices=(110.0,)))


@pytest.mark.parametrize("stop", [0.0, -1.0])
def test_non_positive_stop_is_rejected(stop):
    with pytest.raises(ValueError, match="stop_price"):
        _build(_source(stop_price=stop))


@pytest.mark.parametrize("price", [0.0, -5.0])
def test_non_positive_target_is_rejected(price):
    with pytest.raises(ValueError, match="target price"):
        _build(_source(target_prices=(price, 120.0)))


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_out_of_range_tp1_fraction_is_rejected(fraction):
    with pytest.raises(ValueError, match="tp1_fraction"):
        _build(tp1_fraction=fraction)


@pytest.mark.parametrize("hold", [0, -1])
def test_non_positive_max_hold_is_rejected(hold):
    with pytest.raises(ValueError, match="max_hold_seconds"):
        _build(max_hold_seconds=hold)


def test_naive_plan_time_is_rejected():
    with pytest.raises(ValueError):
        _build(_source(plan_time=datetime(2026, 9, 19, 12, 0, 0)))


def test_blank_trade_id_is_rejected():
    with pytest.raises(ValueError, match="paper_trade_id"):
        _build(_source(paper_trade_id=""))


def test_non_canonical_trade_id_is_rejected():
    with pytest.raises(ValueError, match="paper_trade_id"):
        _build(_source(paper_trade_id=" PTV2:trade-1 "))


def test_negative_plan_seq_is_rejected():
    with pytest.raises(ValueError, match="plan_seq"):
        _build(_source(plan_seq=-1))


def test_invalid_source_type_is_rejected():
    with pytest.raises(ValueError, match="ProtectionPlanSource"):
        build_protection_plan_payload({}, tp1_fraction=0.5, max_hold_seconds=100)


def test_builder_has_no_strategy_optimisation_surface():
    """Translation only: no trailing stop, no training, no model, no orders.

    Scans referenced identifiers and module imports rather than raw text, so the
    module's documentation of what it deliberately does *not* do is not mistaken
    for capability.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_protection_plan.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            referenced.add(node.name.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            referenced.add(node.arg.lower())

    for token in (
        "trailing",
        "optimiz",
        "train",
        "fit",
        "place_order",
        "create_order",
        "kraken_private",
        "openai",
    ):
        assert not any(token in name for name in referenced), (
            f"plan builder must not reference {token}"
        )

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not any("decision_intelligence" in name for name in modules)
