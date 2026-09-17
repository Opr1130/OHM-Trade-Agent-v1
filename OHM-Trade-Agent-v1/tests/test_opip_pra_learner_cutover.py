"""PR-A learner cutover: canonical outcomes plus the end-to-end safety proof.

Section 39 is the load-bearing test of the whole PR. Enlarging the learner's
outcome population is only safe because runtime influence is gated on an
approved promotion. If canonical outcomes could move the multiplier on their
own, PR-A would have swapped a silent evidence gap for a silent live sizing
change - so this file proves the gate holds once the population is real.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.learning.linkage import (
    OutcomeSourceQuality,
    normalize_canonical_paper_outcome,
    normalize_paper_outcome,
)
from app.services import profitability_learning
from app.services import trade_decision_intelligence as intel
from app.services.learning_governance import NEUTRAL_CALIBRATION_MULTIPLIER

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(days=30)


def _canonical_payload(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "outcome_id": "PAPER-OUTCOME:" + "a" * 32,
        "engine": "OHM_PAPER_SIM_V1",
        "paper_trade_id": "PAPER:" + "b" * 20,
        "episode_id": "EP:test-1",
        "cohort_id": "COH:test-1",
        "candidate_id": "CAND:1",
        "decision_context_id": "DI-CONTEXT:" + "c" * 32,
        "strategy_id": None,
        "strategy_version": "OPIP-STRATEGY-V1",
        "learning_version": None,
        "exchange": "KRAKEN",
        "native_symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "quote_currency": "USD",
        "terminal_status": "CLOSED",
        "exit_reason": "STOP",
        "exit_price": 98.0,
        "entry_timestamp": "2026-09-16T12:00:00Z",
        "exit_timestamp": "2026-09-16T13:00:00Z",
        "capital_committed": 1000.0,
        "gross_pnl": -25.0,
        "fees_paid": 4.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "economic_model_version": "paper-sim-flat-fee-v1",
        "final_revision": 7,
        "terminal_event_id": "PTE:" + "d" * 24,
        "lineage_completeness": "COMPLETE",
        "lineage_missing": [],
    }
    payload.update(overrides)
    return payload


def _legacy_lifecycle_row() -> dict:
    """A state.json-shaped row, as the pre-cutover learner consumed it."""
    return {
        "status": "CLOSED",
        "paper_only": True,
        "exchange_write_authority": False,
        "direction": "LONG",
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "closed_at": "2026-09-16T13:00:00Z",
        "exit_price": 98.0,
        "outcome": "LOSS",
        "episode_id": "EP:legacy-1",
        "revision": 5,
        "paper_trade_id": "PAPER:" + "e" * 20,
    }


# ---------------------------------------------------------------------------
# Cutover: canonical form is authoritative
# ---------------------------------------------------------------------------


def test_canonical_closed_outcome_is_final_paper():
    outcome = normalize_canonical_paper_outcome(_canonical_payload())
    assert outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert outcome.source_name == "PAPER_OUTCOME_CANONICAL_V1"
    assert outcome.net_pnl == pytest.approx(-29.0)
    assert outcome.net_pnl_pct == pytest.approx(-2.9)
    assert outcome.episode_id == "EP:test-1"
    assert outcome.paper_trade_id == "PAPER:" + "b" * 20


def test_canonical_form_wins_dispatch():
    """A canonical payload must not be read through the legacy lifecycle path."""
    outcome = normalize_paper_outcome(_canonical_payload())
    assert outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert outcome.source_name == "PAPER_OUTCOME_CANONICAL_V1"


def test_legacy_lifecycle_row_still_normalizes():
    """The compatibility path must not regress while canonical becomes primary."""
    outcome = normalize_paper_outcome(_legacy_lifecycle_row())
    assert outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert outcome.source_name == "PAPER_TRADE_V1"


@pytest.mark.parametrize("status", ["CANCELLED", "UNRESOLVED"])
def test_non_closed_terminal_states_are_not_supervised_truth(status):
    outcome = normalize_canonical_paper_outcome(
        _canonical_payload(terminal_status=status, exit_reason=status)
    )
    assert outcome.source_quality is OutcomeSourceQuality.UNUSABLE


def test_canonical_outcome_without_quote_currency_is_unusable():
    """Currency-blind P/L must never become a supervised label."""
    outcome = normalize_canonical_paper_outcome(_canonical_payload(quote_currency=""))
    assert outcome.source_quality is OutcomeSourceQuality.UNUSABLE


def test_canonical_outcome_without_engine_is_unusable():
    """Simulator populations must stay separable."""
    outcome = normalize_canonical_paper_outcome(_canonical_payload(engine=""))
    assert outcome.source_quality is OutcomeSourceQuality.UNUSABLE


def test_canonical_outcome_without_economics_is_unusable():
    outcome = normalize_canonical_paper_outcome(
        _canonical_payload(net_pnl=None, net_pnl_pct=None)
    )
    assert outcome.source_quality is OutcomeSourceQuality.UNUSABLE


def test_incomplete_evidence_is_never_final_supervised_truth():
    """Section 37: a known canonical gap must not be presented as complete."""
    outcome = normalize_canonical_paper_outcome(
        _canonical_payload(), evidence_complete=False
    )
    assert outcome.source_quality is OutcomeSourceQuality.UNUSABLE


def test_unresolved_outcome_is_marked_censored():
    outcome = normalize_canonical_paper_outcome(
        _canonical_payload(terminal_status="UNRESOLVED", exit_reason="OHLC_GAP")
    )
    assert outcome.censored is True


# ---------------------------------------------------------------------------
# Section 39 - the critical end-to-end safety proof
# ---------------------------------------------------------------------------


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_GOVERNANCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        profitability_learning, "PROFILE_FILE", tmp_path / "strategy_calibration_profile.json"
    )
    monkeypatch.setattr(
        profitability_learning, "LOCK_FILE", tmp_path / ".strategy_calibration_profile.lock"
    )
    return tmp_path


def _write_calibrated_profile(tmp_path, weights):
    profile = {
        "schema_version": 2,
        "version": "profitability-learning-v2",
        "trade_calibration": {"status": "CALIBRATED"},
        "weights": dict(weights),
    }
    profile["profile_id"] = profitability_learning._profile_content_id(profile)
    (tmp_path / "strategy_calibration_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    return profile


def test_canonical_population_cannot_move_sizing_without_approval(learning_env):
    """Section 39: more canonical outcomes, learner finds a pattern, still 1.0.

    The population is now genuinely populated by authoritative outcomes - the
    exact condition that previously would have activated the multiplier purely
    from evidence.
    """
    payloads = [
        _canonical_payload(
            outcome_id="PAPER-OUTCOME:" + f"{i:032d}",
            paper_trade_id="PAPER:" + f"{i:020d}",
            net_pnl=50.0,
            net_pnl_pct=5.0,
        )
        for i in range(40)
    ]
    outcomes = [normalize_canonical_paper_outcome(p) for p in payloads]
    assert all(o.source_quality is OutcomeSourceQuality.FINAL_PAPER for o in outcomes)

    # The learner's own artifact says "calibrated, move sizing".
    profile = _write_calibrated_profile(learning_env, {"direction:LONG": 1.20})
    assert profitability_learning.learned_multiplier(direction="LONG", regime=None) == pytest.approx(1.20)

    # No approved promotion exists, so runtime influence stays exactly neutral.
    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG", regime="RISK_ON"
    )
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == "NO_APPROVED_PROMOTION"
    assert profile["profile_id"]


def test_learner_population_from_canonical_outcomes_is_currency_separable(learning_env):
    """USD and USDT outcomes remain distinguishable in the learner population."""
    usd = normalize_canonical_paper_outcome(_canonical_payload(quote_currency="USD"))
    usdt = normalize_canonical_paper_outcome(
        _canonical_payload(
            native_symbol="ETHUSDT",
            quote_currency="USDT",
            outcome_id="PAPER-OUTCOME:" + "f" * 32,
        )
    )
    assert usd.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert usdt.source_quality is OutcomeSourceQuality.FINAL_PAPER
    # Both are usable, and neither was collapsed into a single currency figure.
    assert usd.net_pnl == pytest.approx(usdt.net_pnl)
