"""F10-F acceptance tests: decision-first notifications, eligibility, delivery and independence.

Increment: ``ATDD-R5-F10-F``.

These tests prove the census's Telegram criteria **at their original meanings**: that an
actionable notification is bound to a committed decision and to the existing eligibility
authority, that missing or uncalibrated evidence fails closed instead of being rendered
optimistically, that delivery records distinguish a failed send from a delivered one, and that
nothing here can override deterministic risk or depend on Telegram for protection.

No production Telegram endpoint is contacted and no production credential is used: the only send
path exercised is driven through an injected transport that fails on purpose.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.services import alert_decision_binding as binding
from app.services import notification_policy as policy
from app.services import telegram_delivery as delivery

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)

INCREMENT = "ATDD-R5-F10-F"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
AUTHORIZED = binding.PolicyAuthorization(authorized=True)

pytestmark = pytest.mark.acceptance


def _card(**overrides: object) -> dict:
    """A complete, eligible, funded card. Each test removes exactly one thing from it."""
    card = {
        "decision_id": "DEC:1",
        "mode": binding.MODE_FUNDED,
        "horizon": "SWING",
        "disposition": "ADMITTED",
        "entry_evidence": "committed limit 100.00 USD",
        "invalidation": "close below 95.00",
        "target_evidence": "committed target 110.00",
        "capital_context": "reserved 500 USD",
        "valid_until": (NOW + timedelta(hours=4)).isoformat(),
        "confidence_marker": binding.CALIBRATED,
        "confidence": 0.61,
        "trace_link": "/api/cockpit/semantic/pages/P3_DECISION_TRACE",
        "instrument": "BTC/USD",
    }
    card.update(overrides)
    return card


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: pathlib.Path) -> set[str]:
    reached: set[str] = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)
    return reached


# --------------------------------------------------------------------------- #
# AC-120: an actionable label is bound to a committed, eligible decision
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_120_an_actionable_label_is_bound_to_a_committed_eligible_decision():
    """ATDD-R5-F10-F/AC-120: no actionable label without a committed decision and a verdict.

    GIVEN the decision-first binder and the existing notification eligibility authority
    WHEN a card is bound without a committed identity, without the policy's verdict, and after
    the policy has refused it
    THEN the card is refused rather than labelled actionable, the policy's own reason is carried
    through, and the binder itself decides no eligibility
    """
    no_identity = binding.bind_decision_card(
        _card(decision_id=""), authorization=AUTHORIZED, now=NOW
    )
    assert no_identity.disposition == binding.REFUSED
    assert no_identity.actionable is False
    assert binding.REASON_NO_COMMITTED_IDENTITY in no_identity.reasons
    assert no_identity.decision_id is None

    no_verdict = binding.bind_decision_card(_card(), authorization=None, now=NOW)
    assert no_verdict.disposition == binding.REFUSED
    assert binding.REASON_NO_POLICY_VERDICT in no_verdict.reasons

    # The real policy refuses a card whose quality is unavailable and whose window is spent.
    quality = policy.notification_quality_suppression_reason(
        "not-a-score", min_quality_score=0.5
    )
    assert quality is not None and quality.startswith("NOTIFICATION_QUALITY_UNAVAILABLE")
    authorized, reason = policy.authorize_new_trade_notification(
        quality_reason=quality, delivery_attempts=0, max_per_window=5
    )
    assert authorized is False and reason == quality

    refused = binding.bind_decision_card(
        _card(),
        authorization=binding.PolicyAuthorization(authorized=False, reason=reason),
        now=NOW,
    )
    assert refused.disposition == binding.REFUSED
    assert binding.REASON_NOT_AUTHORIZED in refused.reasons
    assert reason in refused.reasons
    assert refused.actionable is False

    # The binder consumes a verdict; it cannot produce one. It must not reach the policy.
    reached = _imports(APP_ROOT / "app" / "services" / "alert_decision_binding.py")
    assert "app.services.notification_policy" not in reached
    assert not any(name.startswith("app.services.telegram") for name in reached)
    assert "app.services.alert_v2_format" in reached


# --------------------------------------------------------------------------- #
# AC-121: fail-closed actionability -- no guessed price, no uncalibrated confidence
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_121_missing_or_uncalibrated_evidence_fails_closed():
    """ATDD-R5-F10-F/AC-121: a value appears only when the evidence supports one.

    GIVEN the binder and cards that each miss one thing
    WHEN a card has no committed price evidence, an expired validity, an unstated validity, an
    ordinal score, a paper mode, and advisory Committee text
    THEN no price or confidence is invented, expired and unstated evidence is never actionable,
    an ordinal score is withheld as uncalibrated, a paper card is advisory-only, and Committee
    text stays out of the deterministic facts
    """
    # No committed price: refused, and nothing is rendered in its place.
    unpriced = binding.bind_decision_card(
        _card(entry_evidence=None), authorization=AUTHORIZED, now=NOW
    )
    assert unpriced.disposition == binding.REFUSED
    assert binding.REASON_NO_PRICE in unpriced.reasons
    assert unpriced.price_evidence is None
    assert "none supplied" in binding.render_decision_card(unpriced)

    # Expired evidence is refused, never presented as current.
    expired = binding.bind_decision_card(
        _card(valid_until=(NOW - timedelta(minutes=1)).isoformat()),
        authorization=AUTHORIZED,
        now=NOW,
    )
    assert expired.disposition == binding.REFUSED
    assert binding.REASON_EXPIRED in expired.reasons
    assert expired.actionable is False

    # An unstated validity cannot be promised as current, so it is withheld.
    unstated = binding.bind_decision_card(
        _card(valid_until=None), authorization=AUTHORIZED, now=NOW
    )
    assert unstated.disposition == binding.WITHHELD
    assert binding.REASON_VALIDITY_UNSTATED in unstated.reasons
    assert unstated.actionable is False

    # An ordinal score is not a probability: no calibrated marker means no confidence.
    ordinal = binding.bind_decision_card(
        _card(confidence_marker=None, confidence=None, score=4),
        authorization=AUTHORIZED,
        now=NOW,
    )
    assert ordinal.disposition == binding.ACTIONABLE
    assert ordinal.confidence is None
    assert ordinal.confidence_reason == binding.REASON_UNCALIBRATED
    rendered = binding.render_decision_card(ordinal)
    assert "Confidence: WITHHELD (UNCALIBRATED_SCORE_IS_NOT_CONFIDENCE)" in rendered
    assert "score" not in rendered

    # A calibrated marker without a number still yields no number.
    unnumbered = binding.bind_decision_card(
        _card(confidence=None), authorization=AUTHORIZED, now=NOW
    )
    assert unnumbered.confidence is None
    assert unnumbered.confidence_reason == binding.REASON_UNCALIBRATED

    # Paper, shadow and advisory cards are advisory-only, never funded instructions.
    for mode in (binding.MODE_PAPER, binding.MODE_SHADOW, binding.MODE_ADVISORY):
        paper = binding.bind_decision_card(
            _card(mode=mode), authorization=AUTHORIZED, now=NOW
        )
        assert paper.disposition == binding.ADVISORY_ONLY, mode
        assert paper.actionable is False, mode
        assert binding.REASON_PAPER_ONLY in paper.reasons, mode
        text = binding.render_decision_card(paper)
        assert "Paper/read-only mode" in text, mode
        assert "ACTIONABLE" not in text, mode

    # Committee material is advisory, and never becomes a deterministic fact.
    advised = binding.bind_decision_card(
        _card(committee_note="the Committee leans supportive"),
        authorization=AUTHORIZED,
        now=NOW,
    )
    assert advised.actionable is True
    assert advised.advisory["committee_note"] == "the Committee leans supportive"
    assert "committee_note" not in advised.facts
    text = binding.render_decision_card(advised)
    assert "Advisory (not deterministic evidence, no authority):" in text
    assert "No order was placed or changed." in text


# --------------------------------------------------------------------------- #
# AC-122: the existing notification policy remains the eligibility authority
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_122_revalidation_stays_with_the_notification_policy():
    """ATDD-R5-F10-F/AC-122: eligibility is revalidated by the policy, not by a card.

    GIVEN the notification policy's authorize, slot, quality and dedupe entry points
    WHEN a card is revalidated at emission time on quality, volume and slot grounds
    THEN the policy's verdict governs, a suppressed card cannot become actionable, and the
    binder consumes that verdict rather than computing one
    """
    assert policy.NEW_TRADE_EVENT == "ACTIONABLE_TRADE"
    assert policy.NOTIFICATION_POLICY_VERSION
    assert policy.DEFAULT_COOLDOWN_SECONDS > 0

    allowed, reason = policy.authorize_new_trade_notification(
        quality_reason=None, delivery_attempts=0, max_per_window=2
    )
    assert allowed is True and reason is None
    assert policy.new_trade_delivery_slot_open(1, 2) is True
    assert policy.new_trade_delivery_slot_open(2, 2) is False

    spent, spent_reason = policy.authorize_new_trade_notification(
        quality_reason=None, delivery_attempts=2, max_per_window=2
    )
    assert spent is False
    assert spent_reason is not None and spent_reason.startswith("NEW_TRADE_VOLUME_BUDGET")

    low = policy.notification_quality_suppression_reason(0.4, min_quality_score=0.5)
    assert low is not None and low.startswith("NOTIFICATION_QUALITY_THRESHOLD")

    # A suppressed verdict keeps the card non-actionable, whatever else the card carries.
    suppressed = binding.bind_decision_card(
        _card(),
        authorization=binding.PolicyAuthorization(authorized=False, reason=spent_reason),
        now=NOW,
    )
    assert suppressed.actionable is False

    # And the verdict the policy allows feeds the binder to an actionable card.
    permitted = binding.bind_decision_card(
        _card(),
        authorization=binding.PolicyAuthorization(authorized=allowed, reason=reason),
        now=NOW,
    )
    assert permitted.actionable is True


# --------------------------------------------------------------------------- #
# AC-123: emission identity is derived, and no second dedupe authority exists
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_123_emission_identity_is_derived_and_unique():
    """ATDD-R5-F10-F/AC-123: one dedupe authority, one derived emission identity.

    GIVEN the delivery ledger's alert identity, the policy's confirmation predicate and the
    application package
    WHEN the identity is derived twice from the same decision and from a changed fingerprint, and
    the package is searched for a second eligibility or identity authority
    THEN the identity is stable for one decision, changes with the fingerprint, and no module
    outside the policy and the ledger defines eligibility or emission identity
    """
    first = delivery._alert_id(
        identity="IDENT:1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-1",
    )
    again = delivery._alert_id(
        identity="IDENT:1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-1",
    )
    changed = delivery._alert_id(
        identity="IDENT:1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-2",
    )
    assert first == again
    assert first != changed
    assert first.startswith("TGALERT:")

    assert callable(policy.is_confirmed_emission)

    # One authority each: no other module in the package defines an eligibility predicate or an
    # alert identity of its own.
    definitions: dict[str, list[str]] = {}
    for path in (APP_ROOT / "app").rglob("*.py"):
        text = _source(path)
        for name in ("def should_emit", "def reserve_emit", "def _alert_id"):
            if name in text:
                definitions.setdefault(name, []).append(path.name)
    assert definitions.get("def should_emit") == ["notification_policy.py"]
    assert definitions.get("def reserve_emit") == ["notification_policy.py"]
    assert definitions.get("def _alert_id") == ["telegram_delivery.py"]


# --------------------------------------------------------------------------- #
# AC-124: delivery states, failed-versus-delivered, and the audit trail
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_124_a_failed_send_is_recorded_distinctly_from_a_delivery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-R5-F10-F/AC-124: a failure is an outcome, not a silence.

    GIVEN the delivery ledger and a transport that fails
    WHEN a send raises, a suppression is recorded, and the ledger is read back
    THEN the failed attempt is recorded as SEND_FAILED with its reason and is not delivered, the
    suppression is a distinct status, and no message identity is claimed for a send that never
    landed
    """
    state = tmp_path / "delivery_state.json"
    events = tmp_path / "delivery_events.jsonl"

    def _explode(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("transport down")

    monkeypatch.setattr(delivery, "send_telegram_message_with_id", _explode)

    failed = delivery.send_tracked_telegram(
        bot_token="not-a-real-token",
        chat_id="0",
        message="binding text",
        identity="IDENT:1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-1",
        state_file=state,
        event_file=events,
    )
    assert failed.status == "SEND_FAILED"
    assert failed.delivered is False
    assert failed.message_id is None
    assert failed.alert_id.startswith("TGALERT:")

    rows = delivery.read_delivery_events(path=events)
    assert rows, "the failed attempt must be recorded"
    assert rows[-1]["status"] == "SEND_FAILED"
    assert rows[-1]["failure_reason"] == "RuntimeError"
    assert rows[-1]["alert_id"] == failed.alert_id

    # Nothing landed, so the ledger claims no canonical message identity for this decision.
    assert delivery.canonical_message_id("IDENT:1", state_file=state) is None

    # A suppression is a distinct outcome from a failed send.
    delivery.record_telegram_suppression(
        identity="IDENT:2",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp-2",
        reason="NEW_TRADE_VOLUME_BUDGET:r4-alert-quality-v1:1",
        state_file=state,
        event_file=events,
    )
    statuses = {row["status"] for row in delivery.read_delivery_events(path=events)}
    assert statuses == {"SEND_FAILED", "SUPPRESSED"}

    summary = delivery.build_delivery_summary(path=events)
    assert summary["suppressed"] >= 1


# --------------------------------------------------------------------------- #
# AC-125: Telegram failure cannot touch protection or trading authority
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_125_telegram_failure_cannot_reach_protection_or_execution():
    """ATDD-R5-F10-F/AC-125: the notification plane is not on the protection path.

    GIVEN the binding module, the delivery module and the release architecture gate
    WHEN their imports, their failure behaviour and the gate's own statements are inspected
    THEN no notification module reaches risk, protection or execution, a transport failure is
    returned as a result rather than raised into the caller, and the gate still asserts
    independent protection
    """
    binding_imports = _imports(APP_ROOT / "app" / "services" / "alert_decision_binding.py")
    for banned in ("app.opip.risk", "app.exchange", "app.opip.exchange", "app.api.telegram"):
        assert not any(
            name == banned or name.startswith(banned + ".") for name in binding_imports
        ), banned

    delivery_text = _source(APP_ROOT / "app" / "services" / "telegram_delivery.py")
    # The send path captures the failure instead of propagating it, so a Telegram outage is a
    # delivery fact and never an exception on a lifecycle path.
    assert "except Exception as exc:" in delivery_text
    assert "failure_reason = type(exc).__name__" in delivery_text
    assert '"SEND_FAILED"' in delivery_text
    for banned in ("app.opip.risk", "place_order", "cancel_order", "record_exit"):
        assert banned not in delivery_text, banned

    workflow = (APP_ROOT.parent / ".github" / "workflows" / "pytest.yml").read_text(
        encoding="utf-8"
    )
    assert "PROTECTION=INDEPENDENT" in workflow


# --------------------------------------------------------------------------- #
# AC-126: notification limits remain owner-set
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_126_notification_limits_stay_owner_set():
    """ATDD-R5-F10-F/AC-126: the binder ratifies no notification limit.

    GIVEN the owner-set limits in the notification policy and the binding module
    WHEN the defaults, the binder's own content and the census's classification are inspected
    THEN every limit stays a policy value the binder cannot read or write, the binder holds no
    cooldown or budget value of its own, and the criterion stays owner-gated
    """
    assert isinstance(policy.DEFAULT_COOLDOWN_SECONDS, int)
    assert isinstance(policy.DEFAULT_RESERVATION_LEASE_SECONDS, int)
    assert policy.NEW_TRADE_EVENT

    text = _source(APP_ROOT / "app" / "services" / "alert_decision_binding.py")
    for token in ("cooldown", "budget", "max_per_window", "DEFAULT_COOLDOWN_SECONDS"):
        assert token not in text, token

    census = (
        APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
    ).read_text(encoding="utf-8")
    row = next(line for line in census.splitlines() if line.startswith("| F10-TG-07"))
    assert "OWNER_DECISION_REQUIRED" in row


# --------------------------------------------------------------------------- #
# AC-127: the delivery record, the pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_127_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-F/AC-127: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer and
    the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records this increment with the exact base head it stacks on, its own
    contract and its own test count, the pointer resolves to a declared contract, every approved
    criterion is traced to the test that cites it and mapped to a path, the Telegram criteria
    this increment owns are exactly the census's, and the parity row stays OUTSTANDING
    """
    contracts = load_scope_contracts(CONTRACT_DIR)
    declared = {item.increment for item in contracts}
    assert INCREMENT in declared
    assert read_increment_pointer(POINTER) in declared
    assert (CONTRACT_DIR / f"{read_increment_pointer(POINTER)}.md").is_file()

    ours = [item for item in contracts if item.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id
        for path in contract.implementation_map[ac_id]:
            assert path.startswith("OHM-Trade-Agent-v1/"), path

    # The criteria this increment owns are exactly the census's Telegram criteria, plus this
    # increment's own delivery-record criterion.
    census = (
        APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
    ).read_text(encoding="utf-8")
    census_owned = set(
        re.findall(r"PLANNED:" + re.escape(INCREMENT) + r"/(AC-\d{3})", census)
    )
    assert census_owned == {f"AC-{n}" for n in range(120, 127)}
    assert set(contract.acceptance_criteria) == census_owned | {"AC-127"}

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons

    source = _source(pathlib.Path(__file__))
    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", source))
    assert cited == set(contract.acceptance_criteria)

    report = _source(BUILD_REPORT)
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    assert "pull/376" in report and "pull/377" in report and "pull/378" in report
    cases = len(re.findall(r"@pytest\.mark\.acceptance", source))
    assert f"{cases} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report
