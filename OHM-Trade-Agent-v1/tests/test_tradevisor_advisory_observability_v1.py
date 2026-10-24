"""ATDD: Tradevisor V2 advisory observability and native-context integration.

Scope: external-observation provenance, native intelligence reuse, final
allocation economic qualification, and the separately classified advisory
observation digest.

Every market input is synthetic. The gates exercised are the real production
owners (R4 geometry, F5 attainability/economics, F7 capital, exposure). This
suite never asserts that an actionable recommendation is reachable while the
F6/4H authority gates are closed, and it never claims the external indicator's
internal algorithm was verified.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import notification_policy
from app.services import tradevisor_evaluation as te
from app.services.tradevisor_ingress import (
    TradevisorConfig,
    TradevisorSignal,
    admit,
    config_from_settings,
)

NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)
#: Hour-aligned source bar: the ingress refuses a bar that is not on the
#: configured interval grid, so every fixture uses a closed 12:00-13:00 bar.
BAR_OPEN = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def settings(**over) -> Settings:
    values = dict(_env_file=None, webhook_secret="x" * 12)
    values.update(over)
    return Settings(**values)


@pytest.fixture(autouse=True)
def isolated_notification_state(tmp_path, monkeypatch):
    """Keep policy state and locks inside tmp_path (no /app/data writes)."""
    monkeypatch.setattr(
        notification_policy, "STATE_FILE", tmp_path / "notification_state.json"
    )
    monkeypatch.setattr(
        notification_policy, "LOCK_FILE", tmp_path / ".notification_state.lock"
    )
    return tmp_path


def execution_evidence(**over) -> ExecutionValidation:
    values = dict(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        best_bid=99.95,
        best_ask=100.05,
        mid_price=100.0,
        spread_pct=0.1,
        spread_bps=5.0,
        validation_notional_usd=250.0,
        buy_vwap=100.02,
        sell_vwap=99.98,
        buy_fully_covered=True,
        sell_fully_covered=True,
        estimated_visible_round_trip_market_drag_pct=0.10,
        recent_trade_status="FRESH",
        latest_trade_price=100.0,
        latest_trade_age_seconds=10.0,
        recent_trade_count=20,
    )
    values.update(over)
    return ExecutionValidation(**values)


def market(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=112.0,
        recent_24h_low=94.0,
        recent_72h_high=120.0,
        recent_72h_low=88.0,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0,
        rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0,
        ticker_last=100.0,
        execution_validation=execution_evidence(),
    )
    values.update(over)
    return MarketSnapshot(**values)


def signal(**over) -> TradevisorSignal:
    values = dict(
        dedup_id="dedup-buy-BTCUSD",
        signal="BUY",
        instrument="BTC/USD",
        market="BTCUSD",
        source_exchange="COINBASE",
        source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=BAR_OPEN,
        bar_closed_at=BAR_OPEN + timedelta(minutes=60),
        triggered_at=NOW,
        received_at=NOW,
        signal_price=100.0,
        alert_config_version="tradevisor-1h-buy",
    )
    values.update(over)
    return TradevisorSignal(**values)


def provider(**over):
    resolved = market(**over)
    return lambda _market_name: ("ok", resolved, None)


def evaluate(equity: float = 10_000.0, **kwargs):
    options = dict(
        now=NOW,
        settings=settings(),
        snapshot_provider=provider(),
        account_equity=equity,
        active_trades=[],
    )
    options.update(kwargs)
    return te.evaluate_signal(options.pop("sig", None) or signal(), **options)


def payload(**over) -> dict:
    values = dict(
        schema_version="tradevisor-ingress-v1",
        source="TRADEVISOR",
        indicator="TRADEVISOR_V2",
        signal="BUY",
        symbol="BTCUSD",
        exchange="COINBASE",
        timeframe="60",
        signal_price=100.0,
        bar_opened_at=BAR_OPEN.isoformat(),
        triggered_at=NOW.isoformat(),
        alert_config_version="tradevisor-1h-buy",
    )
    values.update(over)
    return values


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# AC-016 / AC-017 - external-observation provenance
# ---------------------------------------------------------------------------


def test_ac_016_provenance_records_external_identity_and_unverified_algorithm():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-016: provenance is recorded honestly."""
    config = config_from_settings(settings())
    admitted = admit(payload(indicator_version="2.4.1"), config=config, received_at=NOW)

    provenance = admitted.provenance
    assert provenance["external_indicator"] == "TRADEVISOR_V2"
    assert provenance["declared_indicator_version"] == "2.4.1"
    # Ours, operator-declared: the integration contract version.
    assert provenance["integration_version"] == "tradevisor-ingress-v1"
    # The external script's contents are never verifiable from a webhook body.
    assert provenance["algorithm_verification"] == "UNVERIFIED_EXTERNAL_SCRIPT"
    assert provenance["source_exchange"] == "COINBASE"
    assert provenance["source_symbol"] == "BTCUSD"
    assert provenance["timeframe_minutes"] == 60
    assert provenance["signal"] == "BUY"
    assert provenance["source_bar_opened_at"] == admitted.bar_opened_at.isoformat()

    # The same record travels with the evidence handed to evaluation.
    assert (
        admitted.as_evidence()["provenance"]["algorithm_verification"]
        == "UNVERIFIED_EXTERNAL_SCRIPT"
    )

    decision = evaluate(sig=admitted)
    assert decision.evidence["provenance"]["declared_indicator_version"] == "2.4.1"
    assert decision.actionable is False


def test_ac_017_provenance_never_changes_replay_identity_and_bad_values_fail_closed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-017: provenance is not replay identity."""
    from app.services.tradevisor_ingress import TradevisorIngressError

    config = config_from_settings(settings())
    plain = admit(payload(), config=config, received_at=NOW)
    versioned = admit(payload(indicator_version="2.4.1"), config=config, received_at=NOW)
    replayed = admit(
        payload(indicator_version="2.4.1"),
        config=config,
        received_at=NOW + timedelta(minutes=3),
    )

    # A declared version is provenance, not identity: the same source bar is the
    # same observation, so a re-delivery can never duplicate durable evidence.
    assert plain.dedup_id == versioned.dedup_id == replayed.dedup_id
    assert plain.provenance["declared_indicator_version"] is None

    for bad in ("", "2.4.1; DROP", "x" * 40, "v2/../3"):
        with pytest.raises(TradevisorIngressError):
            admit(payload(indicator_version=bad), config=config, received_at=NOW)


# ---------------------------------------------------------------------------
# AC-018 - native BTC / broad-market context
# ---------------------------------------------------------------------------


def test_ac_018_native_btc_context_carries_source_timestamp_and_availability():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-018: BTC context is sourced and stamped."""
    decision = evaluate()

    context = decision.evidence["native_context"]
    btc = context["btc_regime"]
    assert btc["status"] == "AVAILABLE"
    assert btc["value"] == "BULLISH"
    assert btc["source"] == "app.scanner.market_scanner.analyze_symbol"
    assert btc["observed_at"] is not None
    assert btc["market"] == "XBTUSD"
    # Breadth needs a >=30 snapshot universe, so it is incompatible with a
    # single-symbol evaluation: declared, never faked.
    assert context["market_breadth"]["status"] == "UNAVAILABLE"
    assert "SNAPSHOT" in context["market_breadth"]["reason"]
    assert context["opportunity_ranking"]["status"] == "UNAVAILABLE"
    assert decision.actionable is False


def test_ac_018_unavailable_btc_context_is_never_reported_as_a_pass():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-018: unavailable is not corroboration."""

    def failing_provider(_market: str):
        raise RuntimeError("kraken public data unavailable")

    decision = evaluate(
        snapshot_provider=provider(), btc_context_provider=failing_provider
    )

    btc = decision.evidence["native_context"]["btc_regime"]
    assert btc["status"] == "UNAVAILABLE"
    assert btc["value"] is None
    assert "RuntimeError" in btc["reason"]
    assert decision.decision == te.DECISION_WATCH
    assert decision.actionable is False


# ---------------------------------------------------------------------------
# AC-019 - chase-risk evidence from the existing native owner
# ---------------------------------------------------------------------------


def test_ac_019_chase_risk_is_reused_from_the_native_owner_as_context_only():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-019: chase risk is advisory context."""
    extended = market(last_price=114.0, ticker_last=114.0)
    decision = evaluate(
        sig=signal(signal_price=114.0),
        snapshot_provider=lambda _market: ("ok", extended, None),
    )

    chase = decision.evidence["native_context"]["chase_risk"]
    assert chase["source"] == "app.services.chase_risk.assess_chase_risk"
    assert chase["status"] == "AVAILABLE"
    assert chase["value"]["advisory_only"] is True
    assert chase["value"]["band"] in {"LOW", "MODERATE", "HIGH", "EXTREME"}
    # The native scorer owns the band; this path only supplies the inputs it
    # has, and every unsupplied input is disclosed instead of implied.
    assert "INPUT_NOT_SUPPLIED:lift_from_24h_low_pct" in chase["limitations"]
    assert "INPUT_NOT_SUPPLIED:retest_state" in chase["limitations"]
    # Context only: it never grants authority by itself.
    assert decision.actionable is False


# ---------------------------------------------------------------------------
# AC-020 - instrument coverage diagnostics
# ---------------------------------------------------------------------------


def test_ac_020_instrument_coverage_distinguishes_supported_unsupported_unknown():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-020: coverage is explicit, never assumed."""
    from app.services.tradevisor_native_context import instrument_coverage

    config = TradevisorConfig(
        instruments={
            ("COINBASE", "BTCUSD"): ("BTC/USD", "XBTUSD"),
            ("COINBASE", "SOLUSD"): ("SOL/USD", "SOLUSD"),
        }
    )

    unknown = instrument_coverage(config)
    assert {row["status"] for row in unknown["instruments"]} == {
        "UNVERIFIED_NO_VENUE_CATALOGUE"
    }
    assert unknown["status"] == "UNVERIFIED"

    verified = instrument_coverage(config, known_markets={"XBTUSD"})
    statuses = {row["market"]: row["status"] for row in verified["instruments"]}
    assert statuses == {"XBTUSD": "SUPPORTED", "SOLUSD": "UNSUPPORTED"}
    assert verified["status"] == "PARTIAL"


# ---------------------------------------------------------------------------
# AC-021 - final-allocation economic qualification
# ---------------------------------------------------------------------------


def test_ac_021_final_economics_rejects_when_f7_allocation_falls_below_minimum():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-021: the final allocation decides."""
    decision = evaluate(equity=8_000.0)

    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "ECONOMIC_REJECT_AT_FINAL_ALLOCATION"
    assert decision.actionable is False
    # No economic PASS may be reported for an allocation below the bar.
    assert decision.economics is not None
    assert decision.economics.qualified is False
    assert decision.economics.target_2_net_profit < 75.0

    envelope = decision.evidence["economics_at_envelope"]
    assert envelope["qualified"] is True
    assert envelope["target_2_net_profit"] >= 75.0
    assert decision.evidence["final_allocation_basis"]["parity"] is True


def test_ac_021_final_economics_pass_uses_the_same_basis_for_display():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-021: one basis for gate and display."""
    decision = evaluate(equity=10_000.0)

    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert decision.economics.qualified is True
    assert decision.allocation is not None
    # The gate envelope, the qualified notional and the F7 allocation agree.
    assert decision.economics.recommended_capital == pytest.approx(
        decision.allocation.recommended_capital
    )
    assert decision.economics.position_notional == pytest.approx(
        decision.allocation.recommended_capital
    )
    assert decision.economics.target_2_net_profit >= 75.0


def test_ac_021_repeated_evaluation_is_byte_identical():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-021: composition stays deterministic."""
    assert evaluate(equity=10_000.0).as_record() == evaluate(equity=10_000.0).as_record()
    assert evaluate(equity=8_000.0).as_record() == evaluate(equity=8_000.0).as_record()


def test_ac_021_missing_execution_economics_still_fails_closed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-021: no book means no economics."""
    decision = evaluate(
        snapshot_provider=lambda _market: ("ok", market(execution_validation=None), None),
        execution_provider=lambda *_args, **_kwargs: None,
    )

    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "EXECUTION_EVIDENCE_UNAVAILABLE"
    assert decision.economics is None
    assert decision.actionable is False


# ---------------------------------------------------------------------------
# AC-022 / AC-026 - advisory observation cohort and formatting
# ---------------------------------------------------------------------------


def test_ac_022_authority_blocked_candidate_yields_only_an_observation():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-022: observations are not instructions."""
    from app.services import tradevisor_observation as observation

    decision = evaluate(equity=10_000.0)
    assert decision.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"

    candidate = observation.observation_candidate(decision, now=NOW)
    assert candidate is not None
    assert candidate["classification"] == "WATCH / NO TRADE"

    text = observation.format_observation(candidate)
    assert "TRADEVISOR OBSERVATION" in text
    assert "NO TRADE RECOMMENDATION" in text
    assert "BTC/USD" in text
    assert "COINBASE" in text and "Kraken" in text
    assert "F6_TRUSTED_FORECAST" in text
    assert "SWING_4H_CLOSED_CONTEXT" in text
    # Absolute prohibitions for an unqualified observation.
    for forbidden in (
        "ENTER NOW",
        "BUY NOW",
        "SET LIMIT ENTRY",
        "Action:",
        "Preferred entry",
        "Stop:",
        "Target 1",
        "QUALIFIED",
    ):
        assert forbidden not in text


def test_ac_022_non_substantive_rejections_are_not_observed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-022: low-quality events stay silent."""
    from app.services import tradevisor_observation as observation

    unavailable = evaluate(
        snapshot_provider=lambda _market: ("skip", None, "BTCUSD: insufficient history")
    )
    assert unavailable.rejection_code == "MARKET_DATA_UNAVAILABLE"
    assert observation.observation_candidate(unavailable, now=NOW) is None

    sell = evaluate(
        sig=signal(signal="SELL", alert_config_version="tradevisor-1h-sell")
    )
    assert sell.rejection_code in {
        "SELL_CORROBORATED_BEARISH",
        "SELL_UNCONFIRMED_BY_NATIVE",
        "SELL_EVIDENCE_ONLY_NO_NATIVE_DATA",
    }
    assert observation.observation_candidate(sell, now=NOW) is None


def test_ac_026_observation_and_actionable_delivery_are_mutually_exclusive():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-026: one decision, one delivery class."""
    from app.services import tradevisor_observation as observation
    from app.services.tradevisor_pipeline import evaluate_and_notify

    result = evaluate_and_notify(
        signal(),
        settings=settings(),
        now=NOW,
        snapshot_provider=provider(),
        account_equity=10_000.0,
        active_trades=[],
    )
    assert result["notification"] is None
    assert result["observation"] is not None
    assert result["observation"]["classification"] == "WATCH / NO TRADE"

    actionable = replace(
        evaluate(),
        decision=te.DECISION_ENTER_NOW,
        rejection_code=None,
        expires_at=NOW + timedelta(minutes=90),
    )
    # An actionable decision is never downgraded into an observation report.
    assert observation.observation_candidate(actionable, now=NOW) is None


# ---------------------------------------------------------------------------
# AC-023 / AC-024 - bounded, deduplicated, honest delivery
# ---------------------------------------------------------------------------


def _candidates(count: int):
    from app.services import tradevisor_observation as observation

    rows = []
    for index in range(count):
        candidate = observation.observation_candidate(evaluate(), now=NOW)
        candidate["market"] = f"TEST{index}USD"
        candidate["instrument"] = f"TEST{index}/USD"
        rows.append(candidate)
    return rows


def _delivery_settings(**over) -> Settings:
    """Settings that permit the observation digest to reach a (fake) transport."""
    values = dict(
        opip_tradevisor_observation_enabled=True,
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )
    values.update(over)
    return settings(**values)


def test_ac_023_digest_is_bounded_and_deduplicated_by_policy():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-023: no flooding, no duplicate send."""
    from app.services import tradevisor_observation as observation

    sent: list[str] = []

    def sender(**kwargs):
        sent.append(kwargs["message"])
        return SimpleNamespace(delivered=True, message_id=1)

    rows = _candidates(7)
    first = observation.send_observation_digest(
        rows,
        settings=_delivery_settings(),
        now=NOW,
        sender=sender,
    )
    assert first.status == observation.STATUS_DELIVERED
    assert first.observed == observation.MAX_OBSERVATIONS_PER_DIGEST
    assert first.suppressed_bounded == 7 - observation.MAX_OBSERVATIONS_PER_DIGEST
    assert len(sent) == 1
    # One digest, not one message per event.
    assert sent[0].count("TRADEVISOR OBSERVATION") == 1
    assert "TEST0/USD" in sent[0]
    assert "TEST6/USD" not in sent[0]

    # Same evidence, same digest identity: the policy suppresses the repeat.
    second = observation.send_observation_digest(
        rows,
        settings=_delivery_settings(),
        now=NOW + timedelta(minutes=1),
        sender=sender,
    )
    assert second.status == observation.STATUS_SUPPRESSED_POLICY
    assert len(sent) == 1

    # Protection alerts bypass the noncritical attention budget; observations
    # never do, so a digest can be deferred but can never starve F11.
    assert observation.OBSERVATION_EVENT_TYPE not in notification_policy.CRITICAL_EVENTS


def test_ac_023_transport_failure_is_reported_and_never_claimed_delivered():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-023: delivery is never fabricated."""
    from app.services import tradevisor_observation as observation

    outcome = observation.send_observation_digest(
        _candidates(2),
        settings=_delivery_settings(),
        now=NOW,
        sender=lambda **_kwargs: SimpleNamespace(delivered=False, message_id=None),
    )
    assert outcome.status == observation.STATUS_SEND_FAILED
    assert outcome.delivered is False


def test_ac_024_observation_digest_is_disabled_by_default():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-024: the digest is opt-in."""
    from app.services import tradevisor_observation as observation

    calls: list[str] = []
    outcome = observation.send_observation_digest(
        _candidates(2),
        settings=_delivery_settings(opip_tradevisor_observation_enabled=False),
        now=NOW,
        sender=lambda **kwargs: calls.append(kwargs["message"]),
    )
    assert outcome.status == observation.STATUS_DISABLED
    assert calls == []
    assert settings().opip_tradevisor_observation_enabled is False
    assert settings().opip_tradevisor_enabled is False


# ---------------------------------------------------------------------------
# AC-025 - approved-format presentation
# ---------------------------------------------------------------------------


def test_ac_025_actionable_alert_states_timing_venue_and_capital_basis():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-025: presentation is explicit."""
    from app.services import tradevisor_notifier as notifier

    decision = replace(
        evaluate(equity=10_000.0),
        decision=te.DECISION_ENTER_NOW,
        rejection_code=None,
        expires_at=NOW + timedelta(minutes=90),
    )
    text = notifier.format_tradevisor_alert(decision)

    assert "TRADEVISOR ALERTS" in text
    assert "Evidence as of:" in text
    assert "Evaluated:" in text
    assert "Source bar closed:" in text
    assert "Execution venue: Kraken" in text
    assert "Capital basis:" in text
    assert "Horizon: SWING" in text
    # No fabricated holding duration may be presented.
    assert "hold for" not in text.lower()
    assert "expected duration" not in text.lower()
    assert "Execution: Human approval required" in text
    assert "Expires:" in text
