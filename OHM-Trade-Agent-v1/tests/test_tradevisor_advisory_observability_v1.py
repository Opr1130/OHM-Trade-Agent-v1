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
    decision = evaluate(btc_context_provider=lambda _market: ("ok", _btc(), None))

    context = decision.evidence["native_context"]
    btc = context["btc_regime"]
    assert btc["status"] == "AVAILABLE"
    assert btc["value"] == "BULLISH"
    assert btc["source"] == "app.scanner.market_scanner.analyze_symbol"
    # The stamp is the source candle's time, never the evaluation time.
    assert btc["observed_at"] == (
        datetime.fromtimestamp(int(NOW.timestamp()) - 300, tz=timezone.utc).isoformat()
    )
    assert btc["observed_at"] != NOW.isoformat()
    assert btc["freshness_seconds"] == 300.0
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
    """``count`` distinct observation rows (each with its own durable identity)."""
    from app.services import tradevisor_observation as observation

    rows = []
    for index in range(count):
        candidate = observation.observation_candidate(evaluate(), now=NOW)
        # These represent distinct durable rows, so they must carry distinct
        # dedup identities; reusing one id would model a single row ``count``
        # times and hide per-row acknowledgement behaviour.
        candidate["dedup_id"] = f"digest-candidate-{index}"
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
    # The same content is recognised as already delivered: no second send, and
    # the acknowledgement is confirmed rather than re-emitted.
    assert second.status == observation.STATUS_ALREADY_DELIVERED
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


# ---------------------------------------------------------------------------
# AC-027 - no internal instruction may reach user-facing WATCH text
# ---------------------------------------------------------------------------

#: Tokens that describe an internal decision, never an operator instruction.
_INTERNAL_INSTRUCTION_TOKENS = (
    "ENTER_NOW",
    "SET_LIMIT_ENTRY",
    "ACTIONABLE",
    "ENTER NOW",
    "SET LIMIT ENTRY",
    "Action:",
)


def _limit_entry_decision():
    """A real withheld candidate whose internal proposal is SET_LIMIT_ENTRY."""
    extended = market(last_price=104.0, ticker_last=104.0, ema20=100.0)
    return te.evaluate_signal(
        signal(signal_price=104.0),
        now=NOW,
        settings=settings(),
        snapshot_provider=lambda _market: ("ok", extended, None),
        account_equity=250_000.0,
        active_trades=[],
    )


def test_ac_027_watch_observation_carries_no_internal_instruction_tokens():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-027: WATCH text is never an instruction."""
    from app.services import tradevisor_observation as observation

    enter_now = evaluate(equity=10_000.0)
    limit_entry = _limit_entry_decision()
    # The internal proposals really do carry instruction tokens ...
    assert enter_now.evidence["shadow_proposed_action"] == "ENTER_NOW"
    assert limit_entry.evidence["shadow_proposed_action"] == "SET_LIMIT_ENTRY"
    assert "ENTER_NOW" in enter_now.reason
    assert "SET_LIMIT_ENTRY" in limit_entry.reason

    candidates = [
        observation.observation_candidate(enter_now, now=NOW),
        observation.observation_candidate(limit_entry, now=NOW),
    ]
    assert all(candidate is not None for candidate in candidates)

    rendered = [observation.format_observation(c) for c in candidates]
    rendered.append(
        observation.format_observation_digest(candidates, now=NOW, suppressed=0)
    )
    for text in rendered:
        for token in _INTERNAL_INSTRUCTION_TOKENS:
            assert token not in text, f"{token} leaked into: {text}"
        assert "NO TRADE RECOMMENDATION" in text
        assert "TRADEVISOR OBSERVATION" in text

    # The assessment still explains the abstention in operator language.
    single = candidates[0]
    assert single["missing_evidence"] == [
        "F6_TRUSTED_FORECAST",
        "SWING_4H_CLOSED_CONTEXT",
    ]
    assert "hypothesis" in single["abstention_reason"].lower()
    for token in _INTERNAL_INSTRUCTION_TOKENS:
        assert token not in single["abstention_reason"]


def test_ac_027_internal_shadow_evidence_is_preserved_unchanged():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-027: presentation must not touch evidence."""
    from app.services import tradevisor_observation as observation

    decision = evaluate(equity=10_000.0)
    before = decision.as_record()
    candidate = observation.observation_candidate(decision, now=NOW)
    observation.format_observation(candidate)
    observation.format_observation_digest([candidate], now=NOW)

    assert decision.as_record() == before
    assert decision.evidence["shadow_proposed_action"] == "ENTER_NOW"
    assert decision.evidence["advisory_qualification"]["status"] == "SHADOW_ONLY"
    assert decision.evidence["advisory_qualification"]["policy"] == (
        "OWNER_APPROVED_G02_G04_2026-10-23"
    )
    # Deterministic replay is unaffected by having rendered a report.
    assert evaluate(equity=10_000.0).as_record() == before


def test_ac_027_contradictory_instruction_bearing_observation_is_refused():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-027: the guard refuses contradictions."""
    from app.services import tradevisor_observation as observation

    base = observation.observation_candidate(evaluate(), now=NOW)
    assert base is not None

    for injected in (
        "ENTER NOW at market",
        "internal proposal SET_LIMIT_ENTRY",
        "O'Pip QUALIFIED entry",
        "Action: buy 0.5 BTC",
    ):
        contradictory = {**base, "abstention_reason": injected}
        with pytest.raises(ValueError):
            observation.format_observation(contradictory)


# ---------------------------------------------------------------------------
# AC-028 - the final F5 call can never exceed any permitted limit
# ---------------------------------------------------------------------------


class _FakeAllocation:
    """Stand-in for an F7 result, used only to probe the composition guard."""

    def __init__(self, capital, *, risk_dollars=1.0, position_pct=None):
        self.recommended_capital = capital
        self.risk_dollars = risk_dollars
        # Mirror the real owner: position_pct is capital as a % of equity.
        self.position_pct = (
            100.0 * float(capital) / 10_000.0 if position_pct is None else position_pct
        )
        self.reason = "synthetic allocation for a composition guard test"

    def as_dict(self) -> dict:
        """Mirror the real owner's serialization so as_record() can persist it."""
        return {
            "recommended_capital": self.recommended_capital,
            "risk_dollars": self.risk_dollars,
            "position_pct": self.position_pct,
            "reason": self.reason,
        }


def _fake_owner(allocation):
    """Return a ``recommend_capital`` replacement that yields one fixed result."""

    def owner(**_kwargs):
        return allocation

    return owner


def test_ac_028_allocation_never_exceeds_position_or_account_risk_limits():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: F7 output is bounded, end to end."""
    risk_pct = settings().risk_per_trade_pct
    for equity in (10_000.0, 25_000.0, 100_000.0):
        for atr in (2.0, 3.0):
            decision = evaluate(
                equity=equity,
                snapshot_provider=provider(atr=atr, atr_pct=atr),
            )
            allocation = decision.allocation
            assert allocation is not None
            # Position / strategy cap and fixed account-risk budget, unchanged.
            assert allocation.recommended_capital <= (
                equity * te.PRODUCTION_MAX_CAPITAL_FRACTION + 1e-6
            )
            assert allocation.risk_dollars <= equity * risk_pct / 100.0 + 1e-6

            if decision.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE":
                envelope = decision.evidence["economics_at_envelope"]
                final = decision.evidence["final_allocation_basis"]
                assert final["position_notional"] <= envelope["position_notional"]
                assert decision.economics.position_notional <= (
                    equity * te.PRODUCTION_MAX_CAPITAL_FRACTION + 1e-6
                )
                assert decision.economics.recommended_capital == pytest.approx(
                    allocation.recommended_capital
                )


def test_ac_028_oversized_allocation_is_refused_as_inconsistent(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: a broken owner cannot size a trade."""
    monkeypatch.setattr(
        te, "recommend_capital", _fake_owner(_FakeAllocation(50_000.0))
    )
    decision = evaluate(equity=10_000.0)

    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "CAPITAL_INCONSISTENT"
    assert decision.actionable is False
    assert decision.economics is not None
    assert decision.economics.qualified is False
    assert "CAPITAL_INCONSISTENT" == decision.evidence["capital_consistency"]["status"]
    assert decision.evidence["capital_consistency"]["limit"] == pytest.approx(
        10_000.0 * te.PRODUCTION_MAX_CAPITAL_FRACTION
    )
    assert decision.evidence["capital_consistency"]["risk_budget"] == pytest.approx(
        10_000.0 * settings().risk_per_trade_pct / 100.0
    )

    # The independent risk-budget tripwire: inside the position cap but far over
    # the fixed account-risk budget must also be refused.
    monkeypatch.setattr(
        te,
        "recommend_capital",
        _fake_owner(_FakeAllocation(1_000.0, risk_dollars=5_000.0)),
    )
    over_budget = evaluate(equity=10_000.0)
    assert over_budget.rejection_code == "CAPITAL_INCONSISTENT"
    assert over_budget.evidence["capital_consistency"]["status"] == "CAPITAL_INCONSISTENT"
    assert over_budget.evidence["capital_consistency"]["risk_dollars"] == pytest.approx(
        5_000.0
    )
    assert over_budget.economics.qualified is False


def test_ac_028_non_finite_or_negative_allocation_fails_closed_without_raising(
    monkeypatch,
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: malformed capital never escapes."""
    cases = {
        "nan": (_FakeAllocation(float("nan")), "CAPITAL_INCONSISTENT"),
        "inf": (_FakeAllocation(float("inf")), "CAPITAL_INCONSISTENT"),
        "negative": (_FakeAllocation(-5.0), "CAPITAL_REJECT"),
        "zero": (_FakeAllocation(0.0), "CAPITAL_REJECT"),
    }
    for label, (fake, expected) in cases.items():
        monkeypatch.setattr(te, "recommend_capital", _fake_owner(fake))
        decision = evaluate(equity=10_000.0)  # must not raise
        assert decision.rejection_code == expected, label
        assert decision.decision == te.DECISION_WATCH, label
        assert decision.actionable is False, label
        # No economic PASS may be reported at a size the operator cannot deploy.
        assert decision.economics is not None
        assert decision.economics.qualified is False, label


def test_ac_028_unusable_allocation_never_reports_an_economic_pass(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: no PASS without a usable allocation."""
    monkeypatch.setattr(
        te, "recommend_capital", lambda **_kwargs: _FakeAllocation(10.0)
    )
    decision = evaluate(equity=10_000.0)

    assert decision.rejection_code == "CAPITAL_BELOW_EXECUTABLE_MINIMUM"
    assert decision.economics is not None
    assert decision.economics.qualified is False
    assert decision.evidence["f5_final"]["qualified"] is False


# ---------------------------------------------------------------------------
# AC-029 - discovery coverage separates the four coin populations
# ---------------------------------------------------------------------------


def _coverage_config():
    return TradevisorConfig(
        instruments={
            ("COINBASE", "BTCUSD"): ("BTC/USD", "XBTUSD"),
            ("COINBASE", "ETHUSD"): ("ETH/USD", "ETHUSD"),
            ("COINBASE", "SOLUSD"): ("SOL/USD", "SOLUSD"),
            # Configured for an external alert, but absent from the native
            # universe used by these fixtures.
            ("COINBASE", "DOGEUSD"): ("DOGE/USD", "XDGUSD"),
        }
    )


def test_ac_029_discovery_coverage_distinguishes_the_four_coin_sets():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-029: coverage is four explicit sets."""
    from app.services.tradevisor_native_context import discovery_coverage

    coverage = discovery_coverage(
        native_markets=["XBTUSD", "ETHUSD", "SOLUSD", "MANAUSD", "ADAUSD"],
        config=_coverage_config(),
        observed_markets=["XBTUSD"],
    )

    rows = {row["market"]: row for row in coverage["rows"]}
    # 1. discovered by O'Pip and configured for an external alert
    assert rows["XBTUSD"]["native_discovered"] is True
    assert rows["XBTUSD"]["tradingview_configured"] is True
    # 3. actually observed (the only market with durable evidence)
    assert rows["XBTUSD"]["tradevisor_observed"] is True
    # A configured pair with no observation never claims one.
    assert rows["ETHUSD"]["tradingview_configured"] is True
    assert rows["ETHUSD"]["tradevisor_observed"] is False
    # 2. configured for an external alert but NOT in the native universe
    assert rows["XDGUSD"]["tradingview_configured"] is True
    assert rows["XDGUSD"]["native_discovered"] is False
    # 4. discovered by O'Pip with NO TradingView coverage
    assert rows["MANAUSD"]["tradingview_configured"] is False
    assert rows["MANAUSD"]["tradingview_coverage_gap"] is True
    assert rows["MANAUSD"]["tradevisor_observed"] is False

    counts = coverage["counts"]
    assert counts["native_discovered"] == 5
    assert counts["tradingview_configured"] == 4
    assert counts["tradevisor_observed"] == 1
    assert counts["configured_not_native"] == 1
    assert counts["native_without_tradingview_coverage"] == 2
    assert coverage["authority"] == "READ_ONLY_DIAGNOSTIC"
    # No external signal is ever invented for an uncovered coin.
    assert rows["MANAUSD"]["tradevisor_observed"] is False


def test_ac_029_missing_native_universe_is_never_assumed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-029: unknown universe stays unknown."""
    from app.services.tradevisor_native_context import discovery_coverage

    coverage = discovery_coverage(
        native_markets=None, config=_coverage_config(), observed_markets=[]
    )
    assert coverage["native_universe"] == "UNAVAILABLE"
    assert coverage["native_universe_size"] is None
    assert all(row["native_discovered"] is None for row in coverage["rows"])
    assert coverage["counts"]["native_discovered"] is None
    assert coverage["counts"]["native_without_tradingview_coverage"] is None


def test_ac_029_observed_markets_come_from_durable_records_only():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-029: observations are read, never invented."""
    from app.services.tradevisor_native_context import observed_markets_from_records

    records = [
        {"dedup_id": "a", "signal": {"market": "XBTUSD", "signal": "BUY"}},
        {"dedup_id": "b", "signal": {"market": "ethusd"}},
        {"dedup_id": "c", "signal": {}},
        {"dedup_id": "d"},
        "not-a-row",
    ]
    assert observed_markets_from_records(records) == {"XBTUSD", "ETHUSD"}
    assert observed_markets_from_records([]) == set()
    assert observed_markets_from_records(None) == set()


def test_ac_029_opportunity_ranking_reuse_is_declared_unavailable():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-029: ranking reuse needs an architecture decision."""
    from app.services.tradevisor_native_context import discovery_coverage

    coverage = discovery_coverage(
        native_markets=["XBTUSD"], config=_coverage_config(), observed_markets=["XBTUSD"]
    )
    ranking = coverage["ranking"]
    assert ranking["status"] == "UNAVAILABLE"
    assert "REQUIRES_SCAN_CANDIDATE_SET" in ranking["reason"]
    # The diagnostic never ranks, scores or admits anything itself.
    assert coverage["authority"] == "READ_ONLY_DIAGNOSTIC"
    assert "rank" not in {key.lower() for key in coverage["counts"]}


# ===========================================================================
# TV2-FR-01 independent-review remediation (AC-030 .. AC-035)
# ===========================================================================


def _sender(sink: list[str], *, delivered: bool = True):
    """A fake transport that records only successfully delivered messages."""

    def send(**kwargs):
        sink.extend([kwargs["message"]] if delivered else [])
        return SimpleNamespace(delivered=delivered, message_id=len(sink))

    return send


@pytest.fixture()
def durable_inbox(tmp_path, monkeypatch):
    """Real inbox, isolated on disk, with deterministic clock injection."""
    from app.services import tradevisor_inbox as inbox

    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: settings())
    return inbox


_MARKETS = ("BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XRPUSD", "LINKUSD", "AVAXUSD")


def pipeline_result(sig: TradevisorSignal, *, equity: float = 10_000.0) -> dict:
    """The real pipeline result dict the inbox evaluator contract expects."""
    from app.services.tradevisor_pipeline import evaluate_and_notify

    return evaluate_and_notify(
        sig,
        settings=settings(),
        now=NOW,
        snapshot_provider=provider(),
        account_equity=equity,
        active_trades=[],
    )


def settle_observations(inbox, count: int, *, equity: float = 10_000.0, when=NOW) -> dict:
    """Drive the real inbox so each row is terminal with a frozen decision.

    The drain is batch-bounded by configuration, so this loops until the queue is
    empty exactly as the production drain does.
    """
    for index in range(count):
        market_name = _MARKETS[index % len(_MARKETS)]
        sig = signal(
            dedup_id=f"durable-{index}-{market_name}",
            market=market_name,
            instrument=f"{market_name[:-3]}/USD",
            source_symbol=market_name,
        )
        inbox.enqueue(sig, now=when)
    totals = {"checked": 0, "processed": 0, "rejected": 0}
    for _ in range(10):
        result = inbox.process_queued_events(
            evaluator=lambda queued: pipeline_result(queued, equity=equity), now=when
        )
        for key in totals:
            totals[key] += int(result.get(key, 0) or 0)
        if int(result.get("checked", 0) or 0) == 0:
            break
    return totals


# ---------------------------------------------------------------------------
# AC-030 - advisory delivery is recoverable from durable records
# ---------------------------------------------------------------------------


def test_ac_030_crash_after_settlement_is_recovered_from_durable_rows(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: settlement cannot lose the report."""
    from app.services import tradevisor_observation as observation

    summary = settle_observations(durable_inbox, 2)
    assert summary["processed"] == 2
    # Simulate a crash immediately after settlement: nothing was sent.
    pending = observation.pending_observations(now=NOW, settings=settings())
    assert len(pending) == 2

    sent: list[str] = []
    outcome = observation.send_observation_digest(
        pending,
        settings=_delivery_settings(),
        now=NOW,
        sender=_sender(sent),
    )
    assert outcome.status == observation.STATUS_DELIVERED
    assert len(sent) == 1
    # Next cycle: already reported, so nothing is resent.
    assert observation.pending_observations(now=NOW, settings=settings()) == []
    # Acknowledgements are bookkeeping: one digest consumes exactly one
    # noncritical attention slot, however many rows it acknowledged.
    import json

    from app.services import notification_policy

    budget_file = notification_policy.STATE_FILE.parent / "attention_budget_state.json"
    budget = json.loads(budget_file.read_text(encoding="utf-8"))
    assert len(budget["history"]) == 1
    # ``allow_new_noncritical`` is re-exported from attention_budget, so its
    # default path is the production registry, not the patched policy file.
    assert (
        notification_policy.allow_new_noncritical(now=NOW, state_file=budget_file) is True
    )


def test_ac_030_transport_refusal_or_exception_keeps_rows_pending(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: a failed send stays recoverable."""
    from app.services import tradevisor_observation as observation

    settle_observations(durable_inbox, 1)
    pending = observation.pending_observations(now=NOW, settings=settings())
    assert len(pending) == 1

    refused = observation.send_observation_digest(
        pending,
        settings=_delivery_settings(),
        now=NOW,
        sender=lambda **_kwargs: SimpleNamespace(delivered=False, message_id=None),
    )
    assert refused.status == observation.STATUS_SEND_FAILED
    assert len(observation.pending_observations(now=NOW, settings=settings())) == 1

    def exploding(**_kwargs):
        raise RuntimeError("transport unreachable")

    crashed = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=exploding
    )
    assert crashed.status == observation.STATUS_SEND_FAILED
    assert "RuntimeError" in (crashed.reason or "")
    assert len(observation.pending_observations(now=NOW, settings=settings())) == 1


def test_ac_030_bounded_digest_leaves_the_remainder_recoverable(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: over-cap evidence rolls forward."""
    from app.services import tradevisor_observation as observation

    settle_observations(durable_inbox, 7)
    pending = observation.pending_observations(now=NOW, settings=settings())
    assert len(pending) == 7

    sent: list[str] = []
    first = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert first.observed == observation.MAX_OBSERVATIONS_PER_DIGEST
    assert first.suppressed_bounded == 2
    assert len(sent) == 1

    remainder = observation.pending_observations(now=NOW, settings=settings())
    assert len(remainder) == 2
    # Inside the cooldown the digest is suppressed, and the remainder survives.
    suppressed = observation.send_observation_digest(
        remainder, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert suppressed.status == observation.STATUS_SUPPRESSED_POLICY
    assert len(sent) == 1
    assert len(observation.pending_observations(now=NOW, settings=settings())) == 2

    # After the reconciled cooldown the remainder is delivered, once.
    later = NOW + timedelta(hours=7)
    second = observation.send_observation_digest(
        observation.pending_observations(now=later, settings=settings()),
        settings=_delivery_settings(),
        now=later,
        sender=_sender(sent),
    )
    assert second.status == observation.STATUS_DELIVERED
    assert second.observed == 2
    assert len(sent) == 2
    assert observation.pending_observations(now=later, settings=settings()) == []


def test_ac_030_consecutive_cycles_and_concurrent_workers_send_once(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: no duplicate across cycles/workers."""
    from app.services import notification_policy
    from app.services import tradevisor_observation as observation

    settle_observations(durable_inbox, 1)
    pending = observation.pending_observations(now=NOW, settings=settings())
    sent: list[str] = []

    # Concurrent workers: the second cannot take the reservation the first holds.
    fingerprint = observation._fingerprint(pending[: observation.MAX_OBSERVATIONS_PER_DIGEST])
    held = notification_policy.reserve_emit(
        identity=observation.OBSERVATION_IDENTITY,
        event_type=observation.OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
        now=NOW,
    )
    assert held is not None
    blocked = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert blocked.status == observation.STATUS_SUPPRESSED_POLICY
    assert sent == []
    notification_policy.release_emit(
        identity=observation.OBSERVATION_IDENTITY,
        event_type=observation.OBSERVATION_EVENT_TYPE,
        reservation_token=held,
    )

    # Consecutive cycles: the first delivers, the next finds nothing to do.
    first = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert first.status == observation.STATUS_DELIVERED
    assert len(sent) == 1
    assert observation.pending_observations(now=NOW, settings=settings()) == []
    empty = observation.send_observation_digest(
        [], settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert empty.status == observation.STATUS_EMPTY
    assert len(sent) == 1


def test_ac_030_successful_retry_delivers_exactly_once(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: retry succeeds once, never twice."""
    from app.services import tradevisor_observation as observation

    settle_observations(durable_inbox, 1)
    pending = observation.pending_observations(now=NOW, settings=settings())
    sent: list[str] = []
    failed = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent, delivered=False)
    )
    assert failed.status == observation.STATUS_SEND_FAILED
    assert sent == []

    recovered = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert recovered.status == observation.STATUS_DELIVERED
    assert len(sent) == 1

    # A third attempt with the same list cannot double-send.
    repeat = observation.send_observation_digest(
        pending, settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert repeat.status == observation.STATUS_ALREADY_DELIVERED
    assert len(sent) == 1
    assert observation.pending_observations(now=NOW, settings=settings()) == []


# ---------------------------------------------------------------------------
# AC-030 - the digest identity must name the exact observation set
# ---------------------------------------------------------------------------


def _settle_rows_that_differ_only_by_identity(inbox, count: int, *, market: str = "BTCUSD"):
    """Settle durable rows whose only distinguishing fact is their dedup identity.

    Distinct ``dedup_id`` values are distinct inbox rows even when market,
    rejection code and source bar close are identical, so this builds the exact
    collision the digest identity has to survive.
    """
    from app.services import tradevisor_observation as observation

    for index in range(count):
        inbox.enqueue(
            signal(
                dedup_id=f"identity-only-{index}-{market}",
                market=market,
                instrument=f"{market[:-3]}/USD",
                source_symbol=market,
            ),
            now=NOW,
        )
    for _ in range(10):
        result = inbox.process_queued_events(
            evaluator=lambda queued: pipeline_result(queued, equity=10_000.0), now=NOW
        )
        if int(result.get("checked", 0) or 0) == 0:
            break
    return observation.pending_observations(now=NOW, settings=settings())


def test_ac_030_fingerprint_separates_rows_that_differ_only_by_identity(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: a digest names the rows it carries."""
    from app.services import tradevisor_observation as observation

    pending = _settle_rows_that_differ_only_by_identity(durable_inbox, 3)
    assert len(pending) == 3
    # Market, rejection code and bar close are identical across all three rows:
    # only the durable identity distinguishes them.
    assert (
        len(
            {
                (row["market"], row["rejection_code"], row["source_bar_closed_at"])
                for row in pending
            }
        )
        == 1
    )
    first, second, third = pending
    assert len({observation._fingerprint([row]) for row in (first, second, third)}) == 3
    # Exact-set identity, and still independent of discovery order.
    assert observation._fingerprint([first, second]) != observation._fingerprint(
        [first, third]
    )
    assert observation._fingerprint([first, second]) != observation._fingerprint([first])
    assert observation._fingerprint([first, second]) == observation._fingerprint(
        [second, first]
    )


def test_ac_030_delivery_never_acknowledges_a_different_pending_row(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: an acknowledgement covers only sent rows."""
    from app.services import notification_policy
    from app.services import tradevisor_observation as observation
    from app.services.registry_io import load_json

    pending = _settle_rows_that_differ_only_by_identity(durable_inbox, 2)
    assert len(pending) == 2
    delivered_row, withheld_row = pending
    sent: list[str] = []
    calls: list[dict] = []

    def sender(**kwargs):
        calls.append(kwargs)
        sent.append(kwargs["message"])
        return SimpleNamespace(delivered=True, message_id=len(sent))

    first = observation.send_observation_digest(
        [delivered_row], settings=_delivery_settings(), now=NOW, sender=sender
    )
    assert first.status == observation.STATUS_DELIVERED
    assert len(sent) == 1
    # One fingerprint governs the transport call, the policy record and the
    # confirmation, so all three agree on what was delivered.
    assert calls[0]["fingerprint"] == observation._fingerprint([delivered_row])
    key = f"{observation.OBSERVATION_IDENTITY}:{observation.OBSERVATION_EVENT_TYPE}"
    recorded = load_json(notification_policy.STATE_FILE).get(key) or {}
    assert recorded.get("fingerprint") == calls[0]["fingerprint"]

    assert observation.observation_already_reported(delivered_row["dedup_id"]) is True
    # The sibling was never transmitted, so it must keep both its evidence and
    # its unreported status.
    assert observation.observation_already_reported(withheld_row["dedup_id"]) is False
    assert [
        row["dedup_id"]
        for row in observation.pending_observations(now=NOW, settings=settings())
    ] == [withheld_row["dedup_id"]]

    # Inside the cooldown the sibling is deferred, never falsely acknowledged.
    blocked = observation.send_observation_digest(
        [withheld_row], settings=_delivery_settings(), now=NOW, sender=sender
    )
    assert blocked.status == observation.STATUS_SUPPRESSED_POLICY
    assert blocked.delivered is False
    assert (blocked.retry_after_seconds or 0) > 0
    assert len(sent) == 1
    assert observation.observation_already_reported(withheld_row["dedup_id"]) is False

    # After the reconciled cooldown the deferred row becomes deliverable, once.
    later = NOW + timedelta(hours=7)
    recovered = observation.send_observation_digest(
        observation.pending_observations(now=later, settings=settings()),
        settings=_delivery_settings(),
        now=later,
        sender=sender,
    )
    assert recovered.status == observation.STATUS_DELIVERED
    assert len(sent) == 2
    assert observation.pending_observations(now=later, settings=settings()) == []


def test_ac_030_retry_repairs_the_missing_acknowledgement_of_the_same_identity(
    durable_inbox, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: a stable identity repairs a lost ack."""
    from app.services import tradevisor_observation as observation

    pending = _settle_rows_that_differ_only_by_identity(durable_inbox, 1)
    assert len(pending) == 1
    row = pending[0]
    fingerprint = observation._fingerprint([row])
    sent: list[str] = []

    original_marker = observation._mark_reported
    monkeypatch.setattr(observation, "_mark_reported", lambda *_args, **_kwargs: 0)
    first = observation.send_observation_digest(
        [row], settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert first.status == observation.STATUS_DELIVERED
    monkeypatch.setattr(observation, "_mark_reported", original_marker)
    assert len(sent) == 1

    # The same observation keeps the same identity across the retry ...
    assert observation._fingerprint([row]) == fingerprint
    assert observation.observation_already_reported(row["dedup_id"]) is False

    repaired = observation.send_observation_digest(
        [row], settings=_delivery_settings(), now=NOW, sender=_sender(sent)
    )
    assert repaired.status == observation.STATUS_ALREADY_DELIVERED
    assert repaired.delivered is False
    assert len(sent) == 1, "an already-confirmed digest must never be resent"
    # ... so the missing acknowledgement is repaired instead of lost.
    assert observation.observation_already_reported(row["dedup_id"]) is True
    assert observation.pending_observations(now=NOW, settings=settings()) == []


def test_ac_030_missing_or_malformed_identity_fails_closed(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-030: an unidentifiable row is never delivered."""
    from app.services import tradevisor_observation as observation

    pending = _settle_rows_that_differ_only_by_identity(durable_inbox, 1)
    assert len(pending) == 1
    identifiable = pending[0]
    sent: list[str] = []
    malformed: tuple[object, ...] = (
        None,
        "",
        "   ",
        0,
        12345,
        {"id": "x"},
        ["x"],
        "bad\x00identity",
        "broken\nidentity",
    )

    for value in malformed:
        broken = dict(identifiable)
        broken["dedup_id"] = value
        outcome = observation.send_observation_digest(
            [identifiable, broken],
            settings=_delivery_settings(),
            now=NOW,
            sender=_sender(sent),
        )
        assert outcome.status == observation.STATUS_SEND_FAILED, value
        assert outcome.delivered is False, value
        assert "identity" in (outcome.reason or "").lower(), value

    assert sent == []
    # A refused digest must not acknowledge anything, so the row stays pending.
    assert observation.observation_already_reported(identifiable["dedup_id"]) is False
    assert len(observation.pending_observations(now=NOW, settings=settings())) == 1


# ---------------------------------------------------------------------------
# AC-031 - provenance is durable end to end
# ---------------------------------------------------------------------------


def test_ac_031_provenance_survives_enqueue_reload_and_evaluation(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-031: provenance is persisted, not memory."""
    from app.services.tradevisor_ingress import signal_from_record

    admitted = admit(
        payload(indicator_version="2.4.1"),
        config=config_from_settings(settings()),
        received_at=NOW,
    )
    durable_inbox.enqueue(admitted, now=NOW)

    stored = durable_inbox.iter_records()
    assert len(stored) == 1
    persisted = stored[0]["signal"]["provenance"]
    assert persisted["declared_indicator_version"] == "2.4.1"
    assert persisted["algorithm_verification"] == "UNVERIFIED_EXTERNAL_SCRIPT"
    assert persisted["declared_version_verified"] is False

    reloaded = signal_from_record(stored[0]["signal"])
    assert reloaded.provenance == persisted
    assert reloaded.dedup_id == admitted.dedup_id

    durable_inbox.process_queued_events(
        evaluator=lambda queued: pipeline_result(queued), now=NOW
    )
    record = durable_inbox.iter_records()[0]["decision_record"]
    assert record["evidence"]["provenance"]["declared_indicator_version"] == "2.4.1"
    assert record["evidence"]["source"]["provenance"]["algorithm_verification"] == (
        "UNVERIFIED_EXTERNAL_SCRIPT"
    )
    assert record["execution_authority"] is False


def test_ac_031_legacy_and_reclaimed_rows_stay_honest(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-031: missing provenance is unknown."""
    import json

    legacy_signal = {
        "dedup_id": "legacy-row",
        "signal": "BUY",
        "instrument": "BTC/USD",
        "market": "BTCUSD",
        "source_exchange": "COINBASE",
        "source_symbol": "BTCUSD",
        "timeframe_minutes": 60,
        "bar_opened_at": BAR_OPEN.isoformat(),
        "bar_closed_at": (BAR_OPEN + timedelta(minutes=60)).isoformat(),
        "triggered_at": NOW.isoformat(),
        "received_at": NOW.isoformat(),
        "signal_price": 100.0,
        "alert_config_version": "tradevisor-1h-buy",
    }
    durable_inbox.INBOX_FILE.write_text(
        json.dumps(
            {
                "events": {
                    "legacy-row": {
                        "schema_version": 1,
                        "dedup_id": "legacy-row",
                        "status": "QUEUED",
                        "attempts": 0,
                        "received_at": NOW.isoformat(),
                        "enqueued_at": NOW.isoformat(),
                        "signal": legacy_signal,
                    }
                },
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )
    durable_inbox.process_queued_events(
        evaluator=lambda queued: pipeline_result(queued), now=NOW
    )
    legacy_record = durable_inbox.iter_records()[0]["decision_record"]
    # Absent provenance must read as unknown, never as a verified vendor claim.
    assert legacy_record["evidence"]["provenance"] == {}

    # Crash reclamation: a row stranded in PROCESSING is recovered with its
    # provenance intact and evaluated, not dropped.
    admitted = admit(
        payload(indicator_version="9.9.9"),
        config=config_from_settings(settings()),
        received_at=NOW,
    )
    durable_inbox.enqueue(admitted, now=NOW)
    registry = json.loads(durable_inbox.INBOX_FILE.read_text(encoding="utf-8"))
    row = registry["events"][admitted.dedup_id]
    row["status"] = "PROCESSING"
    row["processing_started_at"] = (NOW - timedelta(hours=2)).isoformat()
    row["claim_token"] = "abandoned-worker"
    durable_inbox.INBOX_FILE.write_text(json.dumps(registry), encoding="utf-8")

    summary = durable_inbox.process_queued_events(
        evaluator=lambda queued: pipeline_result(queued), now=NOW
    )
    assert summary["reclaimed"] == 1
    reclaimed = [
        item for item in durable_inbox.iter_records()
        if item.get("dedup_id") == admitted.dedup_id
    ][0]
    assert reclaimed["status"] == "PROCESSED"
    assert reclaimed["decision_record"]["evidence"]["provenance"][
        "declared_indicator_version"
    ] == "9.9.9"


# ---------------------------------------------------------------------------
# AC-032 - BTC context needs market identity and verified freshness
# ---------------------------------------------------------------------------


def _validated(**over):
    """A market-data validation block the scanner would produce."""
    from app.scanner.market_data_validation import MarketDataValidation

    values = dict(
        status="OK",
        qualified=True,
        warnings=[],
        rejection_reasons=[],
        candle_count=300,
        latest_candle_timestamp=int(NOW.timestamp()) - 300,
        latest_candle_age_seconds=300.0,
        duplicate_timestamp_count=0,
        gap_count=0,
        largest_gap_seconds=3600.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=100.0,
        latest_ohlc_close=100.0,
        ticker_vs_ohlc_difference_pct=0.0,
        suspicious_spike_detected=False,
    )
    values.update(over)
    return MarketDataValidation(**values)


def _btc(**over):
    values = dict(symbol="XBTUSD", market_data_validation=_validated())
    values.update(over)
    return market(**values)


def test_ac_032_btc_context_requires_identity_and_verified_freshness():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-032: a trend alone is not evidence."""
    from app.services.tradevisor_native_context import btc_regime_context

    good = btc_regime_context(
        provider=lambda _m: ("ok", _btc(), None), market="XBTUSD", now=NOW
    )
    assert good["status"] == "AVAILABLE"
    assert good["value"] == "BULLISH"
    # Source observation time comes from the candle, never from evaluation time.
    assert good["observed_at"] == (
        datetime.fromtimestamp(int(NOW.timestamp()) - 300, tz=timezone.utc).isoformat()
    )
    assert good["observed_at"] != NOW.isoformat()
    assert good["freshness_seconds"] == 300.0

    other_market = btc_regime_context(
        provider=lambda _m: ("ok", _btc(symbol="ETHUSD"), None),
        market="XBTUSD",
        now=NOW,
    )
    assert other_market["status"] == "UNAVAILABLE"
    assert other_market["reason"].startswith("MARKET_IDENTITY_MISMATCH")
    assert other_market["observed_at"] is None

    unvalidated = btc_regime_context(
        provider=lambda _m: ("ok", _btc(market_data_validation=None), None),
        market="XBTUSD",
        now=NOW,
    )
    assert unvalidated["status"] == "UNAVAILABLE"
    assert unvalidated["reason"].startswith("NO_MARKET_DATA_VALIDATION")

    stale = btc_regime_context(
        provider=lambda _m: (
            "ok",
            _btc(market_data_validation=_validated(qualified=False, status="REJECT")),
            None,
        ),
        market="XBTUSD",
        now=NOW,
    )
    assert stale["status"] == "UNAVAILABLE"
    assert stale["reason"].startswith("MARKET_DATA_UNQUALIFIED")

    no_timestamp = btc_regime_context(
        provider=lambda _m: (
            "ok",
            _btc(market_data_validation=_validated(latest_candle_timestamp=None)),
            None,
        ),
        market="XBTUSD",
        now=NOW,
    )
    assert no_timestamp["status"] == "UNAVAILABLE"
    assert no_timestamp["reason"].startswith("NO_SOURCE_TIMESTAMP")


# ---------------------------------------------------------------------------
# AC-033 - partial chase-risk inputs are disclosed, never a full clearance
# ---------------------------------------------------------------------------


def test_ac_033_chase_risk_never_presents_partial_inputs_as_complete():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-033: partial inputs are labelled."""
    from app.services import tradevisor_observation as observation

    decision = evaluate()
    chase = decision.evidence["native_context"]["chase_risk"]
    assert chase["status"] == "AVAILABLE"
    assert chase["source"] == "app.services.chase_risk.assess_chase_risk"

    value = chase["value"]
    completeness = value["input_completeness"]
    assert completeness["provided"] < completeness["total"]
    assert completeness["ratio"] < 1.0
    assert completeness["missing"]
    assert value["conclusion"] == "INDETERMINATE_PARTIAL_INPUTS"
    assert value["band_is_indicative_only"] is True
    # Completeness is exposed next to the band, not hidden behind it.
    assert any(term.startswith("INPUT_NOT_SUPPLIED") for term in chase["limitations"])

    text = observation.format_observation(
        observation.observation_candidate(decision, now=NOW)
    )
    chase_line = [line for line in text.splitlines() if "Chase risk" in line][0]
    assert "partial" in chase_line.lower()
    assert "indicative" in chase_line.lower()


# ---------------------------------------------------------------------------
# AC-034 - the configured observation interval governs and never discards
# ---------------------------------------------------------------------------


def _variant(market_name: str, bar_hour: int) -> list[dict]:
    """One candidate with distinct content, so only a cooldown can suppress it."""
    rows = _candidates(1)
    rows[0]["market"] = market_name
    rows[0]["instrument"] = f"{market_name[:-3]}/USD"
    rows[0]["source_bar_closed_at"] = datetime(
        2026, 10, 23, bar_hour, 0, 0, tzinfo=timezone.utc
    ).isoformat()
    return rows


def test_ac_034_configured_interval_governs_the_cooldown():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-034: config and policy are reconciled."""
    from app.services import tradevisor_observation as observation

    sent: list[str] = []
    long_interval = _delivery_settings(
        opip_tradevisor_observation_min_interval_minutes=720
    )

    first = observation.send_observation_digest(
        _variant("TESTAUSD", 12), settings=long_interval, now=NOW, sender=_sender(sent)
    )
    assert first.status == observation.STATUS_DELIVERED

    # 12h configured interval is stricter than the 6h policy default, so new
    # evidence is still suppressed seven hours later.
    within = observation.send_observation_digest(
        _variant("TESTBUSD", 13),
        settings=long_interval,
        now=NOW + timedelta(hours=7),
        sender=_sender(sent),
    )
    assert within.status == observation.STATUS_SUPPRESSED_POLICY
    assert within.retry_after_seconds is not None
    assert within.retry_after_seconds > 0
    assert len(sent) == 1

    later = observation.send_observation_digest(
        _variant("TESTCUSD", 14),
        settings=long_interval,
        now=NOW + timedelta(hours=13),
        sender=_sender(sent),
    )
    assert later.status == observation.STATUS_DELIVERED
    assert len(sent) == 2


def test_ac_034_configuration_never_shortens_the_shared_cooldown():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-034: the stricter limit always wins."""
    from app.services import tradevisor_observation as observation

    sent: list[str] = []
    short = _delivery_settings(opip_tradevisor_observation_min_interval_minutes=30)

    assert observation.send_observation_digest(
        _variant("TESTDUSD", 12), settings=short, now=NOW, sender=_sender(sent)
    ).status == observation.STATUS_DELIVERED
    # 30 minutes is weaker than the 6h policy default, so 7h later is allowed.
    assert observation.send_observation_digest(
        _variant("TESTEUSD", 13),
        settings=short,
        now=NOW + timedelta(hours=7),
        sender=_sender(sent),
    ).status == observation.STATUS_DELIVERED
    assert len(sent) == 2


def test_ac_034_suppression_never_discards_durable_observations(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-034: suppressed evidence stays pending."""
    from app.services import notification_policy
    from app.services import tradevisor_observation as observation

    settle_observations(durable_inbox, 1)
    pending = observation.pending_observations(now=NOW, settings=settings())
    assert len(pending) == 1

    # A cooldown already in force for this identity, from a *different* set of
    # evidence: the policy suppresses on the cooldown, and this content has
    # never been confirmed as delivered.
    notification_policy.record_emitted(
        identity=observation.OBSERVATION_IDENTITY,
        event_type=observation.OBSERVATION_EVENT_TYPE,
        fingerprint="earlier-unrelated-digest",
        now=NOW,
    )
    sent: list[str] = []
    suppressed = observation.send_observation_digest(
        pending,
        settings=_delivery_settings(
            opip_tradevisor_observation_min_interval_minutes=720
        ),
        now=NOW + timedelta(hours=2),
        sender=_sender(sent),
    )
    assert suppressed.status == observation.STATUS_SUPPRESSED_POLICY
    assert sent == []
    # The evidence is still eligible: nothing was marked as reported.
    assert len(observation.pending_observations(now=NOW + timedelta(hours=2), settings=settings(opip_tradevisor_observation_min_interval_minutes=720))) == 1


# ---------------------------------------------------------------------------
# AC-035 - every unusable F7 result fails closed
# ---------------------------------------------------------------------------


def _assert_inconsistent(decision, *, reason: str) -> None:
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "CAPITAL_INCONSISTENT"
    assert decision.actionable is False
    assert decision.economics is not None
    assert decision.economics.qualified is False
    assert decision.evidence["capital_consistency"]["reason_code"] == reason


def test_ac_035_negative_risk_and_inconsistent_position_percentage_fail_closed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-035: bad risk/pct never passes."""
    from app.services import tradevisor_evaluation as evaluation

    original = evaluation.recommend_capital
    try:
        evaluation.recommend_capital = _fake_owner(
            _FakeAllocation(1_000.0, risk_dollars=-5.0, position_pct=10.0)
        )
        _assert_inconsistent(evaluate(equity=10_000.0), reason="NEGATIVE_RISK")

        evaluation.recommend_capital = _fake_owner(
            _FakeAllocation(1_000.0, risk_dollars=1.0, position_pct=50.0)
        )
        _assert_inconsistent(
            evaluate(equity=10_000.0), reason="POSITION_PCT_INCONSISTENT"
        )
    finally:
        evaluation.recommend_capital = original


def test_ac_035_missing_or_nonnumeric_allocation_fields_fail_closed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-035: malformed owner output is refused."""
    from app.services import tradevisor_evaluation as evaluation

    class _Missing:
        pass

    class _NonNumeric:
        recommended_capital = "not-a-number"
        risk_dollars = 1.0
        position_pct = 10.0
        reason = "nonnumeric fixture"

    original = evaluation.recommend_capital
    try:
        for result, reason in (
            (_Missing(), "MISSING_ALLOCATION_FIELDS"),
            (_NonNumeric(), "NON_NUMERIC_ALLOCATION"),
        ):
            evaluation.recommend_capital = _fake_owner(result)
            decision = evaluate(equity=10_000.0)  # must not raise
            _assert_inconsistent(decision, reason=reason)
    finally:
        evaluation.recommend_capital = original


def test_ac_035_non_finite_risk_and_unexpected_owner_results_fail_closed():
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-035: NaN risk and None fail closed."""
    from app.services import tradevisor_evaluation as evaluation

    original = evaluation.recommend_capital
    try:
        for result, reason in (
            (_FakeAllocation(1_000.0, risk_dollars=float("nan"), position_pct=10.0), "NON_FINITE_ALLOCATION"),
            (_FakeAllocation(float("inf")), "NON_FINITE_ALLOCATION"),
            (None, "MISSING_ALLOCATION_FIELDS"),
        ):
            evaluation.recommend_capital = _fake_owner(result)
            decision = evaluate(equity=10_000.0)  # must not raise
            _assert_inconsistent(decision, reason=reason)
    finally:
        evaluation.recommend_capital = original


# ---------------------------------------------------------------------------
# AC-028 - account risk is measured against total equity, not the allocation
# ---------------------------------------------------------------------------


def test_ac_028_account_risk_uses_total_equity_denominator(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: $10k equity / $2k allocation."""
    monkeypatch.setattr(te, "recommend_capital", _fake_owner(_FakeAllocation(2_000.0)))

    decision = evaluate(equity=10_000.0)

    economics = decision.economics
    assert economics is not None
    assert economics.recommended_capital == pytest.approx(2_000.0)
    assert economics.position_notional > 0
    fraction = 2_000.0 / 10_000.0
    # The gate rounds its published percentages to 2dp, so compare the
    # relationship rather than the last bit of the rounding.
    assert economics.account_risk_at_stop_pct == pytest.approx(
        economics.stop_pct * fraction, abs=0.01
    )
    # The defect made this equal to stop_pct; it must now be the equity share.
    assert economics.account_risk_at_stop_pct < economics.stop_pct
    # Persisted evidence uses the same denominator.
    recorded = decision.as_record()["economics"]
    assert recorded["account_risk_at_stop_pct"] == pytest.approx(
        economics.account_risk_at_stop_pct
    )


def test_ac_028_account_risk_scales_with_the_allocation_fraction(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: small through maximum fractions."""
    risks: dict[float, float] = {}
    for capital in (100.0, 500.0, 2_000.0):
        monkeypatch.setattr(
            te, "recommend_capital", _fake_owner(_FakeAllocation(capital))
        )
        decision = evaluate(equity=10_000.0)
        economics = decision.economics
        assert economics is not None
        assert economics.recommended_capital == pytest.approx(capital)
        risks[capital] = economics.account_risk_at_stop_pct

    assert risks[500.0] == pytest.approx(5 * risks[100.0], abs=0.01)
    assert risks[2_000.0] == pytest.approx(4 * risks[500.0], abs=0.01)


def test_ac_028_economic_decision_is_unchanged_by_the_denominator(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: only the risk basis moves."""
    capital = 2_000.0
    monkeypatch.setattr(te, "recommend_capital", _fake_owner(_FakeAllocation(capital)))

    decision = evaluate(equity=10_000.0)

    assert decision.plan is not None
    assert decision.economics is not None
    legacy = te.evaluate_economic_quality(
        decision.plan, capital, max_capital_fraction=1.0
    )
    current = decision.economics
    assert current.qualified == legacy.qualified
    assert current.position_notional == pytest.approx(legacy.position_notional)
    assert current.target_1_net_profit == pytest.approx(legacy.target_1_net_profit)
    assert current.target_2_net_profit == pytest.approx(legacy.target_2_net_profit)
    assert current.estimated_costs == pytest.approx(legacy.estimated_costs)
    assert current.stop_pct == pytest.approx(legacy.stop_pct)
    assert current.target_1_move_pct == pytest.approx(legacy.target_1_move_pct)
    # Exactly the account-risk percentage is rescaled to total equity.
    assert current.account_risk_at_stop_pct == pytest.approx(
        legacy.account_risk_at_stop_pct * capital / 10_000.0, abs=0.01
    )


def test_ac_028_zero_or_non_finite_equity_fails_closed(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: an unusable denominator is refused."""
    monkeypatch.setattr(te, "recommend_capital", _fake_owner(_FakeAllocation(2_000.0)))
    # Keep the envelope gate healthy so the capital block itself is exercised
    # with the invalid denominator (a zero-equity envelope would reject earlier).
    real_gate = te.evaluate_economic_quality

    def healthy_envelope(plan, _available_capital, **kwargs):
        return real_gate(plan, 10_000.0, **kwargs)

    monkeypatch.setattr(te, "evaluate_economic_quality", healthy_envelope)

    for equity in (0.0, -10_000.0, float("nan"), float("inf")):
        decision = evaluate(equity=equity)  # must not raise
        assert decision.actionable is False
        assert decision.rejection_code == "CAPITAL_INCONSISTENT"
        assert (
            decision.evidence["capital_consistency"]["reason_code"]
            == "INVALID_EQUITY_DENOMINATOR"
        )
        assert decision.economics is not None
        assert decision.economics.qualified is False


def test_ac_028_oversized_allocation_still_refused_with_equity_basis(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-028: the limits are unchanged."""
    monkeypatch.setattr(
        te, "recommend_capital", _fake_owner(_FakeAllocation(50_000.0))
    )

    decision = evaluate(equity=10_000.0)

    assert decision.rejection_code == "CAPITAL_INCONSISTENT"
    assert (
        decision.evidence["capital_consistency"]["reason_code"] == "OVERSIZED_ALLOCATION"
    )
    assert decision.economics is not None
    assert decision.economics.qualified is False
    assert decision.actionable is False
