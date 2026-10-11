"""R5-B Horizon Intelligence thesis-lifecycle acceptance tests.

This module proves the `ATDD-R5-B-horizon-intelligence` increment: the thesis
lifecycle contract (`app/opip/contracts/horizon_lifecycle.py`) that closes the
structural gap R5-A left open — the first-fill anchor and the three lifecycle
clocks of one thesis revision.

It is deliberately adversarial about the boundaries the adopted clauses set:

* the first fill is recorded once and a later fill never restarts the holding
  clock (`ForecastHorizonAnchor.FIRST_FILL`, `HZ-02`);
* a clock deadline is arithmetic on a preserved instant plus an owner-gated
  duration, never a clock read, and expiry is terminal;
* a lifecycle observation is either the complete declared clock set or a
  recorded abstention: a partially observed clock set is not admissible;
* a missing owner number abstains visibly instead of being defaulted, guessed or
  borrowed, and `OQ-03`/`OQ-08` stay owner decisions;
* point-in-time violations fail closed: no anchor before classification, no
  observation before a clock started, no evidence received before the instant it
  describes, and no anchor at or after the declared time invalidation;
* every record is research-only evidence: `authoritative` is hard-coded `False`
  and the increment adds no order, sizing, reservation, allocation, store or
  environment surface and wires nothing into a runtime root.

The tests are deterministic and hermetic: no clock, no network, no database and
no environment read. Every instant is explicit, so an anchor, a clock and a
lifecycle replay identically.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts import horizon_lifecycle as lifecycle_module  # noqa: E402
from app.opip.contracts.horizon_lifecycle import (  # noqa: E402
    CLOCK_ORDER,
    DECLARED_LIFECYCLE_POLICY_VERSIONS,
    FIRST_FILL_ANCHOR_ID_PREFIX,
    FIRST_FILL_ANCHOR_SCHEMA_VERSION,
    HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    HORIZON_LIFECYCLE_ID_PREFIX,
    HORIZON_LIFECYCLE_POLICY_VERSION,
    HORIZON_LIFECYCLE_SCHEMA_VERSION,
    OWNER_INPUT_STATUS,
    THESIS_CLOCK_ID_PREFIX,
    THESIS_CLOCK_SCHEMA_VERSION,
    FirstFillAnchor,
    HorizonLifecycleContractError,
    HorizonThesisLifecycle,
    LifecycleAbstentionReason,
    ThesisClockName,
    ThesisClockState,
    ThesisLifecycleClock,
    ThesisLifecyclePolicy,
    advance_clock,
    attach_first_fill,
    build_first_fill_anchor,
    evaluate_thesis_lifecycle,
    first_fill_anchor_identity,
    horizon_lifecycle_identity,
    record_additional_fill,
    remaining_lifecycle_seconds,
    require_clock_name,
    start_clock,
    thesis_clock_identity,
)
from app.opip.contracts.thesis import (  # noqa: E402
    HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
    HorizonClass,
    HorizonEvidence,
    HorizonPolicy,
    HorizonRequirements,
    HorizonThesis,
    ThesisLifecycleState,
    classify_horizon,
    expire_thesis,
)
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    parse_scope_contract,
)
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-B-horizon-intelligence"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
MODULE = APP_ROOT / "app" / "opip" / "contracts" / "horizon_lifecycle.py"

#: Runtime roots that own trading, discovery, decision and risk paths. None may
#: import the lifecycle contract of this increment.
RUNTIME_ROOTS = (
    APP_ROOT / "app" / "services",
    APP_ROOT / "app" / "jobs",
    APP_ROOT / "app" / "api",
    APP_ROOT / "app" / "opip" / "decision",
    APP_ROOT / "app" / "opip" / "discovery",
    APP_ROOT / "app" / "opip" / "risk",
)

#: The single module this increment adds. It is a contract, not a runtime.
R5B_MODULES = (MODULE,)

FORBIDDEN_AUTHORITY_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "position_size",
    "reserve_capital",
    "allocate(",
    "kraken",
    "api_secret",
    "api_key",
)

FORBIDDEN_PRIMITIVES = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "sqlite3",
    "psycopg",
    "psycopg2",
    "subprocess",
    "os",
    "time",
    "random",
    "uuid",
    "pathlib",
    "asyncio",
    "pickle",
}

#: Storage and writer surfaces no R5-B module may own. A durable lifecycle or
#: anchor ledger is an open owner decision, not something this slice may add.
FORBIDDEN_PERSISTENCE_TOKENS = (
    "writer_client",
    "submit_decision_context",
    "submit_decision_snapshot",
    "submit_canonical_event",
    "INSERT INTO",
    "parquet",
    "pickle",
    "json.dump",
    "write_text",
    "write_bytes",
    "open(",
    "commit(",
)

TACTICAL_BAND = (60, 86_400)
SWING_BAND = (86_401, 1_209_600)
POSITION_BAND = (1_209_601, 7_776_000)

T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)



def at(minutes: float) -> datetime:
    """One explicit UTC instant on the fixture grid."""

    return T0 + timedelta(minutes=minutes)


def horizon_policy(**overrides) -> HorizonPolicy:
    """The labelled engineering-scenario horizon policy of the frozen F4 owner."""

    payload = {
        "policy_version": HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
        "holding_bounds_seconds": {
            HorizonClass.TACTICAL: TACTICAL_BAND,
            HorizonClass.SWING: SWING_BAND,
            HorizonClass.POSITION: POSITION_BAND,
        },
        "requirements": HorizonRequirements(position_min_structural_milestones=2),
    }
    payload.update(overrides)
    return HorizonPolicy(**payload)


def lifecycle_policy(**overrides) -> ThesisLifecyclePolicy:
    """The labelled engineering-scenario lifecycle policy (`OQ-03` unratified).

    The bands mirror the horizon policy the thesis was classified with, because
    the lifecycle re-derives the band the revision declares.
    """

    payload = {
        "policy_version": HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        "qualification_budget_seconds": 3_600,
        "holding_budget_seconds": {
            HorizonClass.TACTICAL: TACTICAL_BAND,
            HorizonClass.SWING: SWING_BAND,
            HorizonClass.POSITION: POSITION_BAND,
        },
    }
    payload.update(overrides)
    return ThesisLifecyclePolicy(**payload)


def thesis_evidence(**overrides) -> HorizonEvidence:
    """Evidence that classifies to a supported TACTICAL revision."""

    payload = {
        "episode_id": "OPEP:r5b",
        "claim_id": "IGN:r5b",
        "decision_id": "DEC:r5b",
        "evidence_cutoff": at(10),
        "classified_at": at(10),
        "supporting_evidence": ("structural:r5b",),
        "detector_support": True,
        "expected_holding_seconds": 3_600,
        "entry_window_start": at(10),
        "entry_window_end": at(70),
        "time_invalidation": at(600),
    }
    payload.update(overrides)
    return HorizonEvidence(**payload)


def classified_thesis(
    *,
    evidence_overrides: dict | None = None,
    policy: HorizonPolicy | None = None,
) -> HorizonThesis:
    return classify_horizon(
        thesis_evidence(**(evidence_overrides or {})),
        policy if policy is not None else horizon_policy(),
    )


def supported_thesis() -> HorizonThesis:
    """A TACTICAL revision with a declared entry window and a time invalidation."""

    thesis = classified_thesis()
    assert thesis.horizon_class is HorizonClass.TACTICAL
    assert thesis.abstention_reason.value == "NONE"
    return thesis


def unknown_thesis() -> HorizonThesis:
    """A research-only `UNKNOWN` revision: the classifier abstained."""

    thesis = classified_thesis(evidence_overrides={"contradicting_evidence": ("x:1",)})
    assert thesis.horizon_class is HorizonClass.UNKNOWN
    return thesis


def unregulated_window_thesis() -> HorizonThesis:
    """A supported revision whose owner did not require a time invalidation."""

    thesis = classified_thesis(
        evidence_overrides={"time_invalidation": None},
        policy=horizon_policy(
            requirements=HorizonRequirements(
                position_min_structural_milestones=2,
                requires_time_invalidation=False,
            )
        ),
    )
    assert thesis.horizon_class is HorizonClass.TACTICAL
    assert thesis.time_invalidation is None
    return thesis


def position_thesis() -> HorizonThesis:
    """A supported POSITION revision that the labelled lifecycle policy bands."""

    thesis = classified_thesis(
        evidence_overrides={
            "expected_holding_seconds": POSITION_BAND[0],
            "structural_support": True,
            "liquidity_support": True,
            "structural_milestones": 2,
        }
    )
    assert thesis.horizon_class is HorizonClass.POSITION
    return thesis


def anchor_for(
    thesis: HorizonThesis,
    *,
    first_fill_minutes: float = 20,
    receipt_minutes: float | None = None,
    filled_quantity: float = 0.5,
    average_fill_price: float = 100.0,
    source_reference: str = "FILL:1",
) -> FirstFillAnchor:
    first_fill = at(first_fill_minutes)
    receipt = at(first_fill_minutes if receipt_minutes is None else receipt_minutes)
    return build_first_fill_anchor(
        thesis,
        first_fill_at=first_fill,
        receipt_at=receipt,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        source_reference=source_reference,
    )


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def observe(
    thesis: HorizonThesis,
    *,
    anchor: FirstFillAnchor | None = None,
    now: float = 30,
    receipt: float = 30,
    policy: ThesisLifecyclePolicy | None = None,
) -> HorizonThesisLifecycle:
    """Observe one revision's lifecycle at explicit instants on the fixture grid."""

    return evaluate_thesis_lifecycle(
        thesis,
        policy=lifecycle_policy() if policy is None else policy,
        now=at(now),
        receipt_at=at(receipt),
        anchor=anchor,
    )


# ---------------------------------------------------------------------------
# AC-001 The first-fill anchor is recorded once and identity-bound (`HZ-02`)
# ---------------------------------------------------------------------------


def test_ac_001_the_anchor_identity_is_deterministic_and_lineage_bound() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-001: identity is evidence, not a clock read.

    GIVEN one classified thesis revision and the first fill that opened it
    WHEN the anchor is recorded twice and for a different lineage
    THEN both recordings are equal and identity-bound, a different decision
    lineage or first fill instant is a different anchor, and the identity is
    exactly the deterministic `FFAN:` function of the revision and the instant
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    assert anchor_for(thesis) == anchor
    assert anchor.anchor_id.startswith(f"{FIRST_FILL_ANCHOR_ID_PREFIX}:")
    assert anchor.anchor_id == first_fill_anchor_identity(
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        episode_id=thesis.episode_id,
        decision_id=thesis.decision_id,
        first_fill_at=anchor.first_fill_at,
    )
    assert anchor.anchor_schema_version == FIRST_FILL_ANCHOR_SCHEMA_VERSION
    assert (anchor.thesis_id, anchor.thesis_version) == (
        thesis.thesis_id,
        thesis.thesis_version,
    )
    assert anchor.episode_id == thesis.episode_id
    assert anchor.decision_id == thesis.decision_id
    assert (anchor.first_fill_at, anchor.receipt_at) == (at(20), at(20))
    assert anchor.fill_count == 1
    assert anchor.source_references == ("FILL:1",)
    other = classified_thesis(evidence_overrides={"decision_id": "DEC:other"})
    assert other.thesis_id != thesis.thesis_id
    assert anchor_for(other).anchor_id != anchor.anchor_id
    assert anchor_for(thesis, first_fill_minutes=21).anchor_id != anchor.anchor_id


def test_ac_001_the_anchor_record_carries_every_declared_key_without_authority() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-001: evidence only, no authority surface.

    GIVEN a recorded first-fill anchor
    WHEN its serialized record and its flags are inspected
    THEN it carries exactly the declared keys with ISO-8601 UTC instants, it
    declares itself non-authoritative, and it names no order, sizing,
    reservation, allocation or credential field
    """

    anchor = anchor_for(
        supported_thesis(), filled_quantity=0.25, average_fill_price=101.5
    )
    record = anchor.to_dict()
    assert sorted(record) == [
        "anchor_id",
        "anchor_schema_version",
        "average_fill_price",
        "decision_id",
        "episode_id",
        "fill_count",
        "filled_quantity",
        "first_fill_at",
        "receipt_at",
        "record_type",
        "source_references",
        "thesis_id",
        "thesis_version",
    ]
    assert record["record_type"] == "FirstFillAnchor"
    assert record["first_fill_at"] == "2026-10-10T12:20:00Z"
    assert record["receipt_at"] == "2026-10-10T12:20:00Z"
    assert record["filled_quantity"] == 0.25
    assert record["average_fill_price"] == 101.5
    assert record["source_references"] == ["FILL:1"]
    assert anchor.authoritative is False
    for forbidden in ("authority", "order", "position_size", "reserve", "allocate"):
        assert forbidden not in record, forbidden


def test_ac_001_a_forged_or_drifted_anchor_identity_is_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-001: a rewritten record fails closed.

    GIVEN a recorded anchor
    WHEN its identity, declared schema version or declared quantities are
    rewritten
    THEN the constructor refuses the record instead of accepting drifted or
    unfalsifiable evidence
    """

    anchor = anchor_for(supported_thesis())
    with pytest.raises(HorizonLifecycleContractError, match="identity of the anchor"):
        replace(anchor, anchor_id="FFAN:forged")
    with pytest.raises(HorizonLifecycleContractError, match="not the ratified"):
        replace(anchor, anchor_schema_version="first-fill-anchor-v2")
    with pytest.raises(HorizonLifecycleContractError, match="must be >= 1"):
        replace(anchor, thesis_version=0)
    with pytest.raises(HorizonLifecycleContractError, match="whitespace-free token"):
        replace(anchor, source_references=(" ",))
    with pytest.raises(HorizonLifecycleContractError, match="at least the anchor"):
        replace(anchor, source_references=())
    with pytest.raises(HorizonLifecycleContractError, match="fill_count must be positive"):
        replace(anchor, fill_count=0)
    with pytest.raises(HorizonLifecycleContractError, match="must be a number"):
        replace(anchor, filled_quantity="0.5")


def test_ac_001_an_anchor_cannot_precede_the_revision_or_its_own_fill() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-001: no back-filled anchor.

    GIVEN an ACTIVE revision and an already expired one
    WHEN an anchor would precede classification, arrive before its own fill or
    attach to an expired revision
    THEN each is refused, so an anchor can neither be back-filled nor
    time-travelled
    """

    thesis = supported_thesis()
    with pytest.raises(
        HorizonLifecycleContractError, match="precede the thesis classification"
    ):
        anchor_for(thesis, first_fill_minutes=5, receipt_minutes=20)
    with pytest.raises(
        HorizonLifecycleContractError, match="received before its first fill"
    ):
        build_first_fill_anchor(
            thesis,
            first_fill_at=at(20),
            receipt_at=at(10),
            filled_quantity=0.5,
            average_fill_price=100.0,
            source_reference="FILL:1",
        )
    expired = expire_thesis(thesis, now=at(800))
    assert expired.lifecycle_state is ThesisLifecycleState.EXPIRED
    with pytest.raises(HorizonLifecycleContractError, match="never anchored"):
        anchor_for(expired)


# ---------------------------------------------------------------------------
# AC-002 A later fill accumulates without restarting the holding clock
# ---------------------------------------------------------------------------


def test_ac_002_a_later_fill_keeps_the_anchor_identity_and_the_first_fill_instant() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-002: the anchor is recorded once.

    GIVEN a recorded first-fill anchor
    WHEN a later fill of the same revision is accumulated
    THEN the new record preserves the anchor identity and the original first-fill
    instant exactly, so the holding period is still anchored to the first fill
    """

    anchor = anchor_for(supported_thesis())
    more = record_additional_fill(
        anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    assert more.anchor_id == anchor.anchor_id
    assert more.first_fill_at == anchor.first_fill_at == at(20)
    assert more.receipt_at == at(45)
    assert more.fill_count == anchor.fill_count + 1 == 2
    assert more.source_references == ("FILL:1", "FILL:2")
    assert more.filled_quantity == anchor.filled_quantity + 0.5
    assert more.anchor_schema_version == anchor.anchor_schema_version
    assert (more.thesis_id, more.episode_id, more.decision_id) == (
        anchor.thesis_id,
        anchor.episode_id,
        anchor.decision_id,
    )
    assert more.authoritative is False


def test_ac_002_a_later_fill_accumulates_quantity_and_weighted_price() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-002: accumulation is arithmetic.

    GIVEN an anchor of one half unit at one hundred and a later half unit at one
    hundred and twenty
    WHEN the later fill is accumulated twice
    THEN the quantity is the sum, the average price is the quantity-weighted
    average, and the accumulation is deterministic
    """

    anchor = anchor_for(supported_thesis(), filled_quantity=0.5, average_fill_price=100.0)
    first = record_additional_fill(
        anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    second = record_additional_fill(
        anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    assert first == second
    assert first.filled_quantity == 1.0
    assert first.average_fill_price == 110.0
    assert first.fill_count == 2


def test_ac_002_a_back_filled_or_out_of_order_fill_is_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-002: no merged hindsight.

    GIVEN a recorded anchor and a later fill
    WHEN the fill instant precedes the anchor or the receipt precedes the
    previous receipt, or the declared quantity is not positive
    THEN each is refused rather than merged into the anchor
    """

    anchor = anchor_for(supported_thesis())
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot precede the anchor instant"
    ):
        record_additional_fill(
            anchor,
            filled_quantity_added=0.5,
            average_fill_price=120.0,
            fill_at=at(15),
            receipt_at=at(45),
            source_reference="FILL:2",
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="received out of order"
    ):
        record_additional_fill(
            anchor,
            filled_quantity_added=0.5,
            average_fill_price=120.0,
            fill_at=at(40),
            receipt_at=at(19),
            source_reference="FILL:2",
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="filled_quantity_added must be positive"
    ):
        record_additional_fill(
            anchor,
            filled_quantity_added=0,
            average_fill_price=120.0,
            fill_at=at(40),
            receipt_at=at(45),
            source_reference="FILL:2",
        )
    with pytest.raises(HorizonLifecycleContractError, match="must be a FirstFillAnchor"):
        record_additional_fill(
            "anchor",  # type: ignore[arg-type]
            filled_quantity_added=0.5,
            average_fill_price=120.0,
            fill_at=at(40),
            receipt_at=at(45),
            source_reference="FILL:2",
        )


def test_ac_002_the_holding_deadline_is_unchanged_by_a_later_fill() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-002: a later fill never restarts time.

    GIVEN a lifecycle anchored to the first fill
    WHEN another fill is accumulated and the lifecycle is observed again
    THEN the holding clock still starts at the first fill and its deadline and
    remaining seconds are identical to the un-accumulated observation
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    baseline = observe(thesis, anchor=anchor, now=45, receipt=45)
    accumulated = record_additional_fill(
        anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    later = observe(thesis, anchor=accumulated, now=45, receipt=45)
    assert later.lifecycle_id == baseline.lifecycle_id
    assert [clock.clock_id for clock in later.clocks] == [
        clock.clock_id for clock in baseline.clocks
    ]
    assert later.clock(ThesisClockName.HOLDING).started_at == at(20)
    assert later.clock(ThesisClockName.HOLDING).expires_at == baseline.clock(
        ThesisClockName.HOLDING
    ).expires_at
    assert remaining_lifecycle_seconds(later, now=at(45)) == remaining_lifecycle_seconds(
        baseline, now=at(45)
    )
    assert later.clock(ThesisClockName.HOLDING).started_from_anchor_id == (
        accumulated.anchor_id
    )


# ---------------------------------------------------------------------------
# AC-003 The declared clock set is closed and every deadline is derived
# ---------------------------------------------------------------------------


def test_ac_003_the_declared_clock_set_and_order_is_closed() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-003: one declared clock vocabulary.

    GIVEN the declared clock vocabulary and a supported lifecycle observation
    WHEN the recorded clocks are inspected
    THEN the vocabulary is exactly `QUALIFICATION`, `HOLDING`, `INVALIDATION` in
    that order, the observation carries exactly those three clocks, and the
    running-clock view is the declared order
    """

    assert [name.value for name in CLOCK_ORDER] == [
        "QUALIFICATION",
        "HOLDING",
        "INVALIDATION",
    ]
    assert [name.value for name in ThesisClockName] == [
        "QUALIFICATION",
        "HOLDING",
        "INVALIDATION",
    ]
    life = observe(supported_thesis(), anchor=anchor_for(supported_thesis()))
    assert [clock.clock_name for clock in life.clocks] == list(CLOCK_ORDER)
    assert life.running_clock_names == CLOCK_ORDER
    assert life.clock("HOLDING") is life.clocks[1]
    assert life.clock(ThesisClockName.INVALIDATION) is life.clocks[2]
    assert life.horizon_class is HorizonClass.TACTICAL
    assert life.abstention_reason is LifecycleAbstentionReason.NONE
    assert life.authoritative is False


def test_ac_003_every_deadline_is_the_start_instant_plus_the_owner_gated_duration() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-003: one authority per deadline.

    GIVEN a supported revision with a declared entry window, a declared holding
    band and a declared time invalidation
    WHEN each clock is started from preserved evidence
    THEN each start instant is evidence rather than a clock read, each deadline
    is exactly `started_at + expected_seconds`, and only the two anchored clocks
    carry the anchor identity
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    life = observe(thesis, anchor=anchor)
    qualification, holding, invalidation = life.clocks
    assert qualification.started_at == thesis.classified_at == at(10)
    assert qualification.expected_seconds == 3_600
    assert qualification.expires_at == at(70)
    assert qualification.started_from_anchor_id is None
    assert holding.started_at == anchor.first_fill_at == at(20)
    assert holding.expected_seconds == TACTICAL_BAND[1] == 86_400
    assert holding.expires_at == at(1_460)
    assert holding.started_from_anchor_id == anchor.anchor_id
    assert invalidation.started_at == at(20)
    assert invalidation.expected_seconds == 34_800
    assert invalidation.expires_at == thesis.time_invalidation == at(600)
    assert invalidation.started_from_anchor_id == anchor.anchor_id
    for clock in life.clocks:
        assert clock.expires_at - clock.started_at == timedelta(
            seconds=clock.expected_seconds
        )
        assert clock.receipt_at == at(30)
        assert clock.state is ThesisClockState.RUNNING
        assert clock.expired_at is None
        assert clock.clock_schema_version == THESIS_CLOCK_SCHEMA_VERSION
        assert clock.policy_version == (
            HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION
        )
        assert clock.thesis_id == thesis.thesis_id
        assert clock.clock_id.startswith(f"{THESIS_CLOCK_ID_PREFIX}:")
        assert clock.authoritative is False


def test_ac_003_a_clock_identity_excludes_the_observation_and_survives_advancement() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-003: advancement is one clock.

    GIVEN a running holding clock
    WHEN it is advanced, then advanced to its deadline
    THEN the clock identity is unchanged by the observation instant and by the
    state, and it is exactly the deterministic `TCLK:` function of the thesis,
    the clock name, the applied policy and the derived duration
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    holding = observe(thesis, anchor=anchor).clock(ThesisClockName.HOLDING)
    advanced = advance_clock(holding, now=at(45), receipt_at=at(45))
    assert advanced.clock_id == holding.clock_id
    assert advanced.state is ThesisClockState.RUNNING
    assert advanced.receipt_at == at(45)
    assert holding.clock_id == thesis_clock_identity(
        thesis_id=thesis.thesis_id,
        clock_name=ThesisClockName.HOLDING,
        policy_version=HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        started_at=holding.started_at,
        expected_seconds=holding.expected_seconds,
        started_from_anchor_id=anchor.anchor_id,
    )
    expired = advance_clock(holding, now=at(1_460), receipt_at=at(1_460))
    assert expired.clock_id == holding.clock_id
    assert expired.state is ThesisClockState.EXPIRED
    assert expired.expired_at == at(1_460)


def test_ac_003_the_qualification_clock_requires_the_declared_window_and_budget() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-003: qualification derives from evidence.

    GIVEN a declared entry window and the owner-gated qualification budget
    WHEN the qualification clock is started or the budget disagrees with the
    window, or the window ends before the revision was classified
    THEN the derived duration is the declared window, a budget mismatch abstains
    and is refused at the strict seam, and a window that ends before
    classification fails closed as a point-in-time violation
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    clock = start_clock(
        thesis, "QUALIFICATION", policy=lifecycle_policy(), receipt_at=at(30)
    )
    assert clock.started_at == thesis.classified_at == at(10)
    assert clock.expected_seconds == 3_600 == int(
        (thesis.entry_window_end - thesis.classified_at).total_seconds()
    )
    assert clock.expires_at == thesis.entry_window_end == at(70)
    assert clock.started_from_anchor_id is None
    mismatch = observe(
        thesis,
        anchor=anchor,
        policy=lifecycle_policy(qualification_budget_seconds=3_000),
    )
    assert mismatch.abstention_reason is LifecycleAbstentionReason.POLICY_BAND_MISMATCH
    assert mismatch.clocks == ()
    with pytest.raises(HorizonLifecycleContractError, match="POLICY_BAND_MISMATCH"):
        start_clock(
            thesis,
            "QUALIFICATION",
            policy=lifecycle_policy(qualification_budget_seconds=3_000),
            receipt_at=at(30),
        )
    late_window = classified_thesis(
        evidence_overrides={"entry_window_start": at(-60), "entry_window_end": at(0)}
    )
    with pytest.raises(
        HorizonLifecycleContractError, match="at or before the thesis classification"
    ):
        observe(late_window, anchor=anchor_for(late_window))


def test_ac_003_a_clock_the_revision_cannot_support_is_refused_rather_than_defaulted() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-003: no invented duration.

    GIVEN a revision without an entry window, an anchor-less observation and a
    receipt instant that precedes the clock
    WHEN the strict clock seam is called
    THEN it refuses with the same reason the abstaining seam records, a wrong
    argument type is refused, and an undeclared clock name is refused
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    no_window = classified_thesis(
        evidence_overrides={"entry_window_start": None, "entry_window_end": None}
    )
    with pytest.raises(
        HorizonLifecycleContractError,
        match="QUALIFICATION clock cannot be observed: MISSING_ENTRY_WINDOW",
    ):
        start_clock(no_window, "QUALIFICATION", policy=lifecycle_policy(), receipt_at=at(30))
    with pytest.raises(
        HorizonLifecycleContractError,
        match="HOLDING clock cannot be observed: MISSING_FIRST_FILL_ANCHOR",
    ):
        start_clock(thesis, "HOLDING", policy=lifecycle_policy(), receipt_at=at(30))
    with pytest.raises(
        HorizonLifecycleContractError, match="received before the clock starts"
    ):
        start_clock(
            thesis,
            "HOLDING",
            policy=lifecycle_policy(),
            receipt_at=at(5),
            anchor=anchor,
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="policy must be a ThesisLifecyclePolicy"
    ):
        start_clock(
            thesis,
            "HOLDING",
            policy="policy",  # type: ignore[arg-type]
            receipt_at=at(30),
            anchor=anchor,
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="thesis must be a HorizonThesis"
    ):
        start_clock(
            "thesis",  # type: ignore[arg-type]
            "HOLDING",
            policy=lifecycle_policy(),
            receipt_at=at(30),
        )
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        start_clock(thesis, "nope", policy=lifecycle_policy(), receipt_at=at(30))
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a ThesisClockName token"
    ):
        require_clock_name(3, field_name="clock_name")


# ---------------------------------------------------------------------------
# AC-004 Expiry is terminal and advancement observes one clock
# ---------------------------------------------------------------------------


def test_ac_004_advancing_to_the_deadline_expires_the_clock_exactly_once() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-004: the deadline is the only expiry.

    GIVEN a running holding clock with a derived deadline
    WHEN it is observed one interval before and exactly at the deadline
    THEN it stays running before the deadline and expires at it, recording the
    observed instant while keeping the identical clock identity
    """

    thesis = supported_thesis()
    holding = observe(thesis, anchor=anchor_for(thesis)).clock("HOLDING")
    assert holding.state is ThesisClockState.RUNNING
    before = advance_clock(holding, now=at(1_459), receipt_at=at(1_459))
    assert before.state is ThesisClockState.RUNNING
    assert before.expired_at is None
    at_deadline = advance_clock(holding, now=at(1_460), receipt_at=at(1_460))
    assert at_deadline.state is ThesisClockState.EXPIRED
    assert at_deadline.expired_at == at(1_460) == at_deadline.expires_at
    assert at_deadline.clock_id == holding.clock_id


def test_ac_004_an_expired_clock_is_terminal_and_never_resumes() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-004: expiry never resumes.

    GIVEN an expired clock and a lifecycle whose every deadline has passed
    WHEN the clock is advanced again
    THEN the advancement is refused, and the lifecycle reports the terminal
    `EXPIRED` state with no remaining time
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    holding = observe(thesis, anchor=anchor).clock("HOLDING")
    expired = advance_clock(holding, now=at(1_460), receipt_at=at(1_460))
    with pytest.raises(
        HorizonLifecycleContractError, match="terminal and never resumes"
    ):
        advance_clock(expired, now=at(1_500), receipt_at=at(1_500))
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a ThesisLifecycleClock"
    ):
        advance_clock("clock", now=at(1_500), receipt_at=at(1_500))  # type: ignore[arg-type]
    terminal = observe(thesis, anchor=anchor, now=2_000, receipt=2_000)
    assert terminal.thesis_lifecycle_state is ThesisLifecycleState.EXPIRED
    assert [clock.state for clock in terminal.clocks] == [
        ThesisClockState.EXPIRED,
        ThesisClockState.EXPIRED,
        ThesisClockState.EXPIRED,
    ]
    assert terminal.running_clock_names == ()
    assert remaining_lifecycle_seconds(terminal, now=at(2_000)) == 0


def test_ac_004_a_clock_cannot_be_observed_before_it_started_or_received_out_of_order() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-004: point-in-time observation.

    GIVEN a holding clock that starts at the first fill
    WHEN it is observed before that instant, or the evidence is received before
    the observation instant, or the instant is naive
    THEN each is refused instead of producing an observation
    """

    thesis = supported_thesis()
    holding = observe(thesis, anchor=anchor_for(thesis)).clock("HOLDING")
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot be observed before it started"
    ):
        advance_clock(holding, now=at(15), receipt_at=at(30))
    with pytest.raises(
        HorizonLifecycleContractError, match="received before the observation instant"
    ):
        advance_clock(holding, now=at(45), receipt_at=at(44))
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        advance_clock(holding, now=at(45).replace(tzinfo=None), receipt_at=at(45))


def test_ac_004_a_clock_rejects_a_forged_deadline_or_expiry_instant() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-004: the record is self-consistent.

    GIVEN a running clock
    WHEN its deadline, state, expiry instant, identity, schema or policy token is
    rewritten
    THEN the constructor refuses the record, while a legitimate later expiry
    observation is accepted
    """

    thesis = supported_thesis()
    holding = observe(thesis, anchor=anchor_for(thesis)).clock("HOLDING")
    with pytest.raises(
        HorizonLifecycleContractError, match="expires_at must be exactly"
    ):
        replace(holding, expires_at=at(2_000))
    with pytest.raises(HorizonLifecycleContractError, match="expire after it starts"):
        replace(holding, expires_at=holding.started_at)
    with pytest.raises(
        HorizonLifecycleContractError, match="expired clock must record the instant"
    ):
        replace(holding, state=ThesisClockState.EXPIRED)
    with pytest.raises(
        HorizonLifecycleContractError, match="running clock cannot record an expiry"
    ):
        replace(holding, expired_at=at(1_470))
    with pytest.raises(
        HorizonLifecycleContractError,
        match="expiry cannot be observed before the deadline",
    ):
        replace(holding, state=ThesisClockState.EXPIRED, expired_at=at(1_440))
    with pytest.raises(
        HorizonLifecycleContractError, match="received before the clock starts"
    ):
        replace(holding, receipt_at=at(11))
    with pytest.raises(
        HorizonLifecycleContractError, match="clock_id is not the identity"
    ):
        replace(holding, clock_id="TCLK:forged")
    with pytest.raises(HorizonLifecycleContractError, match="not the ratified"):
        replace(holding, clock_schema_version="thesis-clock-v2")
    with pytest.raises(HorizonLifecycleContractError, match="not a declared token"):
        replace(holding, policy_version="nope")
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        replace(holding, state="BOGUS")
    late = replace(holding, state=ThesisClockState.EXPIRED, expired_at=at(1_470))
    assert late.state is ThesisClockState.EXPIRED
    assert late.expired_at == at(1_470)


def test_ac_004_the_clock_state_vocabulary_is_closed() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-004: two states, no pause.

    GIVEN the declared clock state vocabulary
    WHEN it is enumerated and an undeclared token is requested
    THEN the vocabulary is exactly `RUNNING` and `EXPIRED` and no pause, hold or
    resume state exists
    """

    assert [state.value for state in ThesisClockState] == ["RUNNING", "EXPIRED"]
    assert set(ThesisClockState) == {
        ThesisClockState.RUNNING,
        ThesisClockState.EXPIRED,
    }
    assert ThesisClockState("RUNNING") is ThesisClockState.RUNNING
    with pytest.raises(ValueError):
        ThesisClockState("PAUSED")
    for undeclared in ("PAUSED", "HELD", "RESUMED", "CANCELLED"):
        assert undeclared not in {state.value for state in ThesisClockState}


# ---------------------------------------------------------------------------
# AC-005 The lifecycle observation is complete evidence or a recorded abstention
# ---------------------------------------------------------------------------


def test_ac_005_a_supported_lifecycle_carries_exactly_the_declared_clocks() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-005: complete evidence only.

    GIVEN a supported revision observed at an explicit instant
    WHEN the lifecycle record and its serialized form are inspected
    THEN it carries exactly the declared clock set bound to the revision and the
    anchor, with ISO-8601 UTC instants and no authority
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    life = observe(thesis, anchor=anchor)
    assert life.lifecycle_schema_version == HORIZON_LIFECYCLE_SCHEMA_VERSION
    assert life.policy_version == (
        HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION
    )
    assert (life.thesis_id, life.thesis_version) == (
        thesis.thesis_id,
        thesis.thesis_version,
    )
    assert life.horizon_class is HorizonClass.TACTICAL
    assert life.lifecycle_id.startswith(f"{HORIZON_LIFECYCLE_ID_PREFIX}:")
    assert life.anchor == anchor
    assert (life.evaluated_at, life.receipt_at) == (at(30), at(30))
    assert life.thesis_lifecycle_state is ThesisLifecycleState.ACTIVE
    assert tuple(clock.clock_name for clock in life.clocks) == CLOCK_ORDER
    assert all(clock.thesis_id == thesis.thesis_id for clock in life.clocks)
    record = life.to_dict()
    assert sorted(record) == [
        "abstention_reason",
        "anchor",
        "clocks",
        "evaluated_at",
        "horizon_class",
        "lifecycle_id",
        "lifecycle_schema_version",
        "policy_version",
        "receipt_at",
        "record_type",
        "thesis_id",
        "thesis_lifecycle_state",
        "thesis_version",
    ]
    assert record["record_type"] == "HorizonThesisLifecycle"
    assert record["evaluated_at"] == "2026-10-10T12:30:00Z"
    assert record["receipt_at"] == "2026-10-10T12:30:00Z"
    assert record["abstention_reason"] == "NONE"
    assert record["thesis_lifecycle_state"] == "ACTIVE"
    assert [item["clock_name"] for item in record["clocks"]] == [
        "QUALIFICATION",
        "HOLDING",
        "INVALIDATION",
    ]
    assert record["anchor"]["anchor_id"] == anchor.anchor_id
    assert life.authoritative is False


def test_ac_005_a_partial_misordered_or_foreign_clock_set_is_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-005: no partial clock set.

    GIVEN a supported lifecycle observation
    WHEN its clock set is truncated, reordered, duplicated, carries a non-clock
    record or a clock of another revision
    THEN every form is refused, so a partially observed clock set is not
    admissible evidence
    """

    thesis = supported_thesis()
    life = observe(thesis, anchor=anchor_for(thesis))
    with pytest.raises(
        HorizonLifecycleContractError, match="exactly the declared clock set"
    ):
        replace(life, clocks=(life.clocks[0],))
    with pytest.raises(
        HorizonLifecycleContractError, match="unique and in the declared clock order"
    ):
        replace(life, clocks=tuple(reversed(life.clocks)))
    with pytest.raises(
        HorizonLifecycleContractError, match="unique and in the declared clock order"
    ):
        replace(life, clocks=(life.clocks[0], life.clocks[0], life.clocks[2]))
    with pytest.raises(
        HorizonLifecycleContractError, match="may only contain ThesisLifecycleClock"
    ):
        replace(life, clocks=("clock",))
    foreign = classified_thesis(evidence_overrides={"decision_id": "DEC:other"})
    foreign_anchor = anchor_for(foreign)
    foreign_clock = start_clock(
        foreign,
        "HOLDING",
        policy=lifecycle_policy(),
        receipt_at=at(30),
        anchor=foreign_anchor,
    )
    assert foreign.thesis_id != thesis.thesis_id
    with pytest.raises(
        HorizonLifecycleContractError,
        match="must belong to the lifecycle's thesis revision",
    ):
        replace(life, clocks=(foreign_clock, *life.clocks[1:]))


def test_ac_005_an_abstained_lifecycle_carries_no_clocks_and_stays_identity_bound() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-005: abstention is recorded evidence.

    GIVEN an observation without a first-fill anchor
    WHEN the lifecycle is inspected
    THEN the abstention is recorded with its declared reason, the clock set is
    empty, the anchor is absent and the observation keeps its deterministic
    identity, while smuggled clocks or a missing anchor on supported evidence are
    refused
    """

    thesis = supported_thesis()
    abstained = observe(thesis)
    assert abstained.abstention_reason is (
        LifecycleAbstentionReason.MISSING_FIRST_FILL_ANCHOR
    )
    assert abstained.clocks == ()
    assert abstained.anchor is None
    assert abstained.clock("HOLDING") is None
    assert abstained.to_dict()["anchor"] is None
    assert abstained.to_dict()["clocks"] == []
    assert abstained.lifecycle_id.startswith(f"{HORIZON_LIFECYCLE_ID_PREFIX}:")
    assert abstained.lifecycle_id == horizon_lifecycle_identity(
        lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
        policy_version=HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        horizon_class=thesis.horizon_class,
        anchor_id=None,
    )
    supported = observe(thesis, anchor=anchor_for(thesis))
    assert supported.lifecycle_id != abstained.lifecycle_id
    with pytest.raises(
        HorizonLifecycleContractError, match="abstained lifecycle observation carries no clocks"
    ):
        replace(abstained, clocks=supported.clocks)
    with pytest.raises(
        HorizonLifecycleContractError, match="must be bound to a first-fill anchor"
    ):
        replace(supported, anchor=None)


def test_ac_005_lifecycle_instants_must_follow_the_instants_they_describe() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-005: no future evidence.

    GIVEN a lifecycle observation
    WHEN its receipt precedes its evaluation instant or a declared field carries
    an undeclared, naive or impossible value
    THEN the record is refused instead of being accepted as evidence
    """

    life = observe(supported_thesis(), anchor=anchor_for(supported_thesis()))
    with pytest.raises(
        HorizonLifecycleContractError, match="received before it was evaluated"
    ):
        replace(life, receipt_at=at(25))
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        replace(life, evaluated_at=at(30).replace(tzinfo=None))
    with pytest.raises(HorizonLifecycleContractError, match="must be an integer"):
        replace(life, thesis_version=True)
    with pytest.raises(HorizonLifecycleContractError, match="must be >= 1"):
        replace(life, thesis_version=0)
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        replace(life, thesis_lifecycle_state="BOGUS")
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        replace(life, abstention_reason="BOGUS")
    with pytest.raises(HorizonLifecycleContractError, match="not the ratified"):
        replace(life, lifecycle_schema_version="horizon-lifecycle-v2")
    with pytest.raises(HorizonLifecycleContractError, match="not a declared token"):
        replace(life, policy_version="nope")


def test_ac_005_a_forged_lifecycle_identity_is_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-005: the identity is re-derived.

    GIVEN a supported lifecycle observation
    WHEN its identity is rewritten, it is bound to another revision's anchor or
    to a different second anchor of the same revision
    THEN each is refused, and a different revision reproduces a different
    lifecycle identity
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    life = observe(thesis, anchor=anchor)
    assert life.lifecycle_id == horizon_lifecycle_identity(
        lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
        policy_version=HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        horizon_class=thesis.horizon_class,
        anchor_id=anchor.anchor_id,
    )
    with pytest.raises(
        HorizonLifecycleContractError, match="lifecycle_id is not the identity"
    ):
        replace(life, lifecycle_id="HLFC:forged")
    foreign = classified_thesis(evidence_overrides={"decision_id": "DEC:other"})
    with pytest.raises(
        HorizonLifecycleContractError, match="anchor must belong to the thesis revision"
    ):
        replace(life, anchor=anchor_for(foreign))
    with pytest.raises(
        HorizonLifecycleContractError, match="lifecycle_id is not the identity"
    ):
        replace(life, anchor=anchor_for(thesis, first_fill_minutes=21))
    other_revision = observe(foreign, anchor=anchor_for(foreign))
    assert other_revision.lifecycle_id != life.lifecycle_id
    assert other_revision.thesis_id == foreign.thesis_id


# ---------------------------------------------------------------------------
# AC-006 Owner numbers are never invented and missing evidence abstains visibly
# ---------------------------------------------------------------------------


def test_ac_006_an_unconfigured_policy_abstains_and_starts_no_clock() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-006: unconfigured means inert.

    GIVEN a policy with no owner-gated numbers and a policy with a budget but no
    bands
    WHEN the lifecycle is evaluated
    THEN both abstain with `POLICY_NOT_CONFIGURED` and start no clock, and
    declared-token, positivity and band-shape violations are refused
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    unconfigured = ThesisLifecyclePolicy()
    assert unconfigured.configured is False
    assert unconfigured.qualification_budget_seconds is None
    assert unconfigured.holding_budget_seconds is None
    assert unconfigured.policy_version == HORIZON_LIFECYCLE_POLICY_VERSION
    assert unconfigured.band_for(HorizonClass.TACTICAL) is None
    life = observe(thesis, anchor=anchor, policy=unconfigured)
    assert life.abstention_reason is LifecycleAbstentionReason.POLICY_NOT_CONFIGURED
    assert life.clocks == ()
    assert life.anchor == anchor
    assert life.policy_version == HORIZON_LIFECYCLE_POLICY_VERSION
    half = ThesisLifecyclePolicy(qualification_budget_seconds=3_600)
    assert half.configured is False
    assert observe(thesis, anchor=anchor, policy=half).abstention_reason is (
        LifecycleAbstentionReason.POLICY_NOT_CONFIGURED
    )
    with pytest.raises(HorizonLifecycleContractError, match="not a declared token"):
        ThesisLifecyclePolicy(policy_version="nope")
    with pytest.raises(
        HorizonLifecycleContractError, match="qualification_budget_seconds must be positive"
    ):
        lifecycle_policy(qualification_budget_seconds=0)
    with pytest.raises(HorizonLifecycleContractError, match="must not overlap"):
        lifecycle_policy(
            holding_budget_seconds={
                HorizonClass.TACTICAL: (60, 100_000),
                HorizonClass.SWING: (50_000, 1_209_600),
            }
        )
    with pytest.raises(
        HorizonLifecycleContractError,
        match="may only bound TACTICAL, SWING or POSITION",
    ):
        lifecycle_policy(
            holding_budget_seconds={HorizonClass.UNKNOWN: (1, 2)}
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="must not exceed its maximum"
    ):
        lifecycle_policy(
            holding_budget_seconds={HorizonClass.TACTICAL: (100, 60)}
        )


def test_ac_006_a_missing_anchor_or_window_abstains_without_guessing_a_clock() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-006: missing evidence is visible.

    GIVEN a supported revision without an anchor, a revision without an entry
    window and a research-only `UNKNOWN` revision
    WHEN each lifecycle is evaluated
    THEN each records its declared abstention reason with no clock started, and
    the strict clock seam refuses the same missing evidence
    """

    thesis = supported_thesis()
    no_anchor = observe(thesis)
    assert no_anchor.abstention_reason is (
        LifecycleAbstentionReason.MISSING_FIRST_FILL_ANCHOR
    )
    assert no_anchor.clocks == ()
    no_window_thesis = classified_thesis(
        evidence_overrides={"entry_window_start": None, "entry_window_end": None}
    )
    assert no_window_thesis.horizon_class is HorizonClass.TACTICAL
    assert no_window_thesis.abstention_reason.value == "NONE"
    no_window = observe(no_window_thesis, anchor=anchor_for(no_window_thesis))
    assert no_window.abstention_reason is LifecycleAbstentionReason.MISSING_ENTRY_WINDOW
    assert no_window.clocks == ()
    assert no_window.anchor is not None
    with pytest.raises(HorizonLifecycleContractError, match="MISSING_ENTRY_WINDOW"):
        start_clock(
            no_window_thesis,
            "QUALIFICATION",
            policy=lifecycle_policy(),
            receipt_at=at(30),
        )
    research_only = unknown_thesis()
    assert research_only.horizon_class is HorizonClass.UNKNOWN
    unknown = observe(research_only)
    assert unknown.abstention_reason is LifecycleAbstentionReason.UNKNOWN_HORIZON_CLASS
    assert unknown.clocks == ()
    assert unknown.anchor is None


def test_ac_006_a_missing_time_invalidation_abstains() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-006: no invented invalidation.

    GIVEN a supported revision whose owner did not require a time invalidation
    WHEN the lifecycle is evaluated
    THEN it abstains with `MISSING_TIME_INVALIDATION`, starts no clock, keeps the
    recorded anchor, and the strict clock seam refuses the same absence
    """

    thesis = unregulated_window_thesis()
    assert thesis.time_invalidation is None
    anchor = anchor_for(thesis)
    life = observe(thesis, anchor=anchor)
    assert life.abstention_reason is LifecycleAbstentionReason.MISSING_TIME_INVALIDATION
    assert life.clocks == ()
    assert life.anchor == anchor
    with pytest.raises(
        HorizonLifecycleContractError, match="MISSING_TIME_INVALIDATION"
    ):
        start_clock(
            thesis,
            "INVALIDATION",
            policy=lifecycle_policy(),
            receipt_at=at(30),
            anchor=anchor,
        )


def test_ac_006_an_unbanded_class_or_a_band_mismatch_abstains() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-006: bands are owner-gated.

    GIVEN a `POSITION` revision and a policy that bands only `TACTICAL`, then a
    policy whose `TACTICAL` band differs from the declared band
    WHEN each lifecycle is evaluated
    THEN the unbanded class abstains with `UNSUPPORTED_LIFECYCLE_BAND`, the
    mismatched band abstains with `POLICY_BAND_MISMATCH`, and the fully
    configured policy supports the same revision
    """

    position = position_thesis()
    position_anchor = anchor_for(position)
    tactical_only = lifecycle_policy(
        holding_budget_seconds={HorizonClass.TACTICAL: TACTICAL_BAND}
    )
    unbanded = observe(position, anchor=position_anchor, policy=tactical_only)
    assert unbanded.abstention_reason is (
        LifecycleAbstentionReason.UNSUPPORTED_LIFECYCLE_BAND
    )
    assert unbanded.clocks == ()
    with pytest.raises(
        HorizonLifecycleContractError, match="UNSUPPORTED_LIFECYCLE_BAND"
    ):
        start_clock(
            position,
            "HOLDING",
            policy=tactical_only,
            receipt_at=at(30),
            anchor=position_anchor,
        )
    supported_position = observe(position, anchor=position_anchor)
    assert supported_position.abstention_reason is LifecycleAbstentionReason.NONE
    assert supported_position.clock("HOLDING").expected_seconds == POSITION_BAND[1]
    mismatch = observe(
        supported_thesis(),
        anchor=anchor_for(supported_thesis()),
        policy=lifecycle_policy(
            holding_budget_seconds={HorizonClass.TACTICAL: (120, 86_400)}
        ),
    )
    assert mismatch.abstention_reason is LifecycleAbstentionReason.POLICY_BAND_MISMATCH
    assert mismatch.clocks == ()
    assert mismatch.anchor is not None


def test_ac_006_the_owner_input_and_abstention_vocabulary_is_declared() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-006: the vocabulary is closed.

    GIVEN the declared owner-input status and abstention vocabulary
    WHEN they are enumerated and an undeclared reason is requested
    THEN the status stays `OWNER_DECISION_REQUIRED`, the vocabulary is exactly
    the eight declared reasons, and every abstention is recorded with no clock
    """

    assert OWNER_INPUT_STATUS == "OWNER_DECISION_REQUIRED"
    assert [reason.value for reason in LifecycleAbstentionReason] == [
        "NONE",
        "POLICY_NOT_CONFIGURED",
        "UNKNOWN_HORIZON_CLASS",
        "MISSING_FIRST_FILL_ANCHOR",
        "UNSUPPORTED_LIFECYCLE_BAND",
        "POLICY_BAND_MISMATCH",
        "MISSING_ENTRY_WINDOW",
        "MISSING_TIME_INVALIDATION",
    ]
    assert LifecycleAbstentionReason("NONE") is LifecycleAbstentionReason.NONE
    with pytest.raises(ValueError):
        LifecycleAbstentionReason("GUESSED")
    abstentions = (
        observe(supported_thesis(), policy=ThesisLifecyclePolicy()),
        observe(supported_thesis()),
        observe(unknown_thesis()),
        observe(
            classified_thesis(
                evidence_overrides={
                    "entry_window_start": None,
                    "entry_window_end": None,
                }
            ),
            anchor=anchor_for(
                classified_thesis(
                    evidence_overrides={
                        "entry_window_start": None,
                        "entry_window_end": None,
                    }
                )
            ),
        ),
        observe(unregulated_window_thesis(), anchor=anchor_for(unregulated_window_thesis())),
        observe(
            position_thesis(),
            anchor=anchor_for(position_thesis()),
            policy=lifecycle_policy(
                holding_budget_seconds={HorizonClass.TACTICAL: TACTICAL_BAND}
            ),
        ),
        observe(
            supported_thesis(),
            anchor=anchor_for(supported_thesis()),
            policy=lifecycle_policy(
                holding_budget_seconds={HorizonClass.TACTICAL: (120, 86_400)}
            ),
        ),
    )
    for life in abstentions:
        assert life.abstention_reason is not LifecycleAbstentionReason.NONE
        assert life.clocks == ()
    assert sorted({life.abstention_reason for life in abstentions}) == sorted(
        reason
        for reason in LifecycleAbstentionReason
        if reason is not LifecycleAbstentionReason.NONE
    )


# ---------------------------------------------------------------------------
# AC-007 Point-in-time violations fail closed
# ---------------------------------------------------------------------------


def test_ac_007_no_anchor_before_classification_or_at_the_declared_invalidation() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-007: the anchor is inside its window.

    GIVEN a supported revision with a declared classification instant and a
    declared time invalidation
    WHEN an anchor would precede classification or fall at or after the
    invalidation
    THEN each is refused instead of being recorded as favourable evidence
    """

    thesis = supported_thesis()
    with pytest.raises(
        HorizonLifecycleContractError, match="precede the thesis classification"
    ):
        build_first_fill_anchor(
            thesis,
            first_fill_at=at(5),
            receipt_at=at(20),
            filled_quantity=0.5,
            average_fill_price=100.0,
            source_reference="FILL:1",
        )
    late = anchor_for(thesis, first_fill_minutes=600, receipt_minutes=600)
    assert late.first_fill_at == thesis.time_invalidation == at(600)
    with pytest.raises(
        HorizonLifecycleContractError,
        match="at or after the revision's declared time invalidation",
    ):
        observe(thesis, anchor=late, now=610, receipt=610)
    beyond = anchor_for(thesis, first_fill_minutes=700, receipt_minutes=700)
    with pytest.raises(
        HorizonLifecycleContractError,
        match="at or after the revision's declared time invalidation",
    ):
        observe(thesis, anchor=beyond, now=710, receipt=710)
    with pytest.raises(
        HorizonLifecycleContractError, match="at or before the thesis classification"
    ):
        late_window = classified_thesis(
            evidence_overrides={
                "entry_window_start": at(-60),
                "entry_window_end": at(0),
            }
        )
        observe(late_window, anchor=anchor_for(late_window))


def test_ac_007_no_observation_before_a_clock_could_start() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-007: no observation before evidence.

    GIVEN a lifecycle anchored at the first fill
    WHEN it is observed before classification, between classification and the
    first fill, and exactly at the first fill
    THEN the first two are refused and the third is the earliest admissible
    observation
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot be observed before it started"
    ):
        observe(thesis, anchor=anchor, now=5, receipt=30)
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot be observed before it started"
    ):
        observe(thesis, anchor=anchor, now=15, receipt=30)
    earliest = observe(thesis, anchor=anchor, now=20, receipt=30)
    assert earliest.evaluated_at == at(20)
    assert earliest.clock("HOLDING").started_at == at(20)
    assert earliest.clock("HOLDING").state is ThesisClockState.RUNNING


def test_ac_007_no_evidence_received_before_the_instant_it_describes() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-007: receipt never precedes observation.

    GIVEN a lifecycle observed at an explicit instant
    WHEN the receipt instant precedes the observation instant
    THEN the observation is refused, while an identical receipt is admitted
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    with pytest.raises(
        HorizonLifecycleContractError, match="received before the observation instant"
    ):
        observe(thesis, anchor=anchor, now=30, receipt=25)
    with pytest.raises(
        HorizonLifecycleContractError, match="received before the observation instant"
    ):
        observe(thesis, anchor=anchor, now=30, receipt=29)
    admitted = observe(thesis, anchor=anchor, now=30, receipt=30)
    assert admitted.receipt_at == at(30) == admitted.evaluated_at
    later = observe(thesis, anchor=anchor, now=30, receipt=45)
    assert later.receipt_at == at(45)
    assert later.evaluated_at == at(30)


def test_ac_007_naive_instants_and_foreign_anchors_are_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-007: explicit UTC or nothing.

    GIVEN instants without a timezone, an anchor recorded by another revision and
    arguments of the wrong type
    WHEN the lifecycle is evaluated or the anchor is built
    THEN each is refused, so no implicit local time and no foreign evidence can
    enter a lifecycle observation
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    policy = lifecycle_policy()
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        evaluate_thesis_lifecycle(
            thesis,
            policy=policy,
            now=at(30).replace(tzinfo=None),
            receipt_at=at(30),
            anchor=anchor,
        )
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        evaluate_thesis_lifecycle(
            thesis,
            policy=policy,
            now=at(30),
            receipt_at=at(30).replace(tzinfo=None),
            anchor=anchor,
        )
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        build_first_fill_anchor(
            thesis,
            first_fill_at=at(20).replace(tzinfo=None),
            receipt_at=at(20),
            filled_quantity=0.5,
            average_fill_price=100.0,
            source_reference="FILL:1",
        )
    foreign = classified_thesis(evidence_overrides={"decision_id": "DEC:other"})
    foreign_anchor = anchor_for(foreign)
    assert foreign_anchor.thesis_id != thesis.thesis_id
    with pytest.raises(
        HorizonLifecycleContractError,
        match="belongs to the thesis revision that recorded it",
    ):
        observe(thesis, anchor=foreign_anchor)
    with pytest.raises(HorizonLifecycleContractError, match="must be a HorizonThesis"):
        evaluate_thesis_lifecycle(
            "thesis",  # type: ignore[arg-type]
            policy=policy,
            now=at(30),
            receipt_at=at(30),
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a ThesisLifecyclePolicy"
    ):
        evaluate_thesis_lifecycle(
            thesis,
            policy="policy",  # type: ignore[arg-type]
            now=at(30),
            receipt_at=at(30),
        )
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a FirstFillAnchor"
    ):
        evaluate_thesis_lifecycle(
            thesis,
            policy=policy,
            now=at(30),
            receipt_at=at(30),
            anchor="anchor",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# AC-008 One identity per revision, replayable and policy bound
# ---------------------------------------------------------------------------


def test_ac_008_re_observing_reproduces_the_lifecycle_and_clock_identities() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-008: re-observation advances clocks only.

    GIVEN a supported revision and its anchor
    WHEN the lifecycle is observed again at a later instant
    THEN the lifecycle identity and every clock identity, start instant and
    deadline are unchanged while the observation and receipt instants advance
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    first = observe(thesis, anchor=anchor, now=30, receipt=30)
    later = observe(thesis, anchor=anchor, now=45, receipt=45)
    assert later.lifecycle_id == first.lifecycle_id
    assert [clock.clock_id for clock in later.clocks] == [
        clock.clock_id for clock in first.clocks
    ]
    assert [clock.started_at for clock in later.clocks] == [
        clock.started_at for clock in first.clocks
    ]
    assert [clock.expires_at for clock in later.clocks] == [
        clock.expires_at for clock in first.clocks
    ]
    assert first.evaluated_at == at(30)
    assert later.evaluated_at == at(45)
    assert later.to_dict()["clocks"][1]["receipt_at"] == "2026-10-10T12:45:00Z"
    assert later.clock("HOLDING").state is ThesisClockState.RUNNING


def test_ac_008_an_identical_replay_is_identical_key_for_key() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-008: identical inputs replay identically.

    GIVEN the same revision, anchor and instants
    WHEN the lifecycle is evaluated twice, and once more with an anchor that has
    accumulated a later fill
    THEN the two identical evaluations are equal key for key, and the accumulated
    anchor changes the recorded anchor only, never a clock
    """

    def replay() -> dict:
        thesis = supported_thesis()
        return observe(thesis, anchor=anchor_for(thesis), now=30, receipt=30).to_dict()

    assert replay() == replay()
    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    accumulated = record_additional_fill(
        anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    plain = observe(thesis, anchor=anchor)
    with_fill = observe(thesis, anchor=accumulated)
    assert plain.lifecycle_id == with_fill.lifecycle_id
    assert [clock.to_dict() for clock in with_fill.clocks] == [
        clock.to_dict() for clock in plain.clocks
    ]
    assert plain.anchor == anchor
    assert with_fill.anchor == accumulated
    assert with_fill.anchor.fill_count == 2


def test_ac_008_the_applied_policy_token_is_part_of_the_identity() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-008: policy binds identity, not instants.

    GIVEN the same revision, anchor and numbers under two declared policy tokens
    WHEN both lifecycles are observed
    THEN the policy token changes the lifecycle and clock identities while the
    preserved start instants and derived deadlines stay identical
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    engineering = observe(thesis, anchor=anchor)
    shadow = observe(
        thesis,
        anchor=anchor,
        policy=lifecycle_policy(policy_version=HORIZON_LIFECYCLE_POLICY_VERSION),
    )
    assert engineering.policy_version == (
        HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION
    )
    assert shadow.policy_version == HORIZON_LIFECYCLE_POLICY_VERSION
    assert shadow.lifecycle_id != engineering.lifecycle_id
    assert [clock.policy_version for clock in shadow.clocks] == [
        HORIZON_LIFECYCLE_POLICY_VERSION
    ] * len(CLOCK_ORDER)
    assert [clock.started_at for clock in shadow.clocks] == [
        clock.started_at for clock in engineering.clocks
    ]
    assert [clock.expires_at for clock in shadow.clocks] == [
        clock.expires_at for clock in engineering.clocks
    ]
    assert shadow.clock("HOLDING").clock_id != engineering.clock("HOLDING").clock_id
    assert set(DECLARED_LIFECYCLE_POLICY_VERSIONS) == {
        HORIZON_LIFECYCLE_POLICY_VERSION,
        HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    }


def test_ac_008_an_abstention_is_a_distinct_observation() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-008: abstention is its own identity.

    GIVEN the same revision observed with and without an anchor
    WHEN both observations are compared and replayed
    THEN the abstention carries a different identity and no clocks, replays
    identically at a later instant, and never equals supported evidence
    """

    thesis = supported_thesis()
    supported = observe(thesis, anchor=anchor_for(thesis))
    abstained = observe(thesis)
    assert abstained.lifecycle_id != supported.lifecycle_id
    assert abstained.abstention_reason is (
        LifecycleAbstentionReason.MISSING_FIRST_FILL_ANCHOR
    )
    assert abstained.clocks == ()
    assert supported.clocks != abstained.clocks
    assert abstained.to_dict() != supported.to_dict()
    again = observe(thesis, now=45, receipt=45)
    assert again.lifecycle_id == abstained.lifecycle_id
    assert again.evaluated_at == at(45)
    assert abstained.evaluated_at == at(30)


# ---------------------------------------------------------------------------
# AC-009 Remaining lifecycle time is a point-in-time read from preserved instants
# ---------------------------------------------------------------------------


def test_ac_009_remaining_seconds_derive_from_preserved_instants() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-009: remaining time is arithmetic.

    GIVEN a supported lifecycle observed at the thirtieth minute
    WHEN the remaining time of each clock is read, then re-read later
    THEN each read is the whole seconds between the preserved deadline and the
    supplied instant
    """

    thesis = supported_thesis()
    life = observe(thesis, anchor=anchor_for(thesis))
    assert remaining_lifecycle_seconds(life, now=at(30)) == 85_800
    assert remaining_lifecycle_seconds(life, now=at(30), clock_name="HOLDING") == 85_800
    assert remaining_lifecycle_seconds(
        life, now=at(30), clock_name=ThesisClockName.HOLDING
    ) == 85_800
    assert remaining_lifecycle_seconds(life, now=at(30), clock_name="INVALIDATION") == 34_200
    assert remaining_lifecycle_seconds(life, now=at(30), clock_name="QUALIFICATION") == 2_400
    holding = life.clock("HOLDING")
    assert remaining_lifecycle_seconds(life, now=at(30)) == int(
        (holding.expires_at - at(30)).total_seconds()
    )
    assert remaining_lifecycle_seconds(life, now=at(45)) == 84_900
    assert remaining_lifecycle_seconds(life, now=at(20)) == 86_400


def test_ac_009_remaining_seconds_is_floored_at_zero() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-009: never negative.

    GIVEN a supported lifecycle
    WHEN the remaining time is read at the deadline, past it and long past it
    THEN the value is exactly zero and never negative, and the reads decrease
    monotonically until they floor
    """

    life = observe(supported_thesis(), anchor=anchor_for(supported_thesis()))
    assert remaining_lifecycle_seconds(life, now=at(1_459)) == 60
    assert remaining_lifecycle_seconds(life, now=at(1_460)) == 0
    assert remaining_lifecycle_seconds(life, now=at(1_470)) == 0
    assert remaining_lifecycle_seconds(life, now=at(5_000)) == 0
    assert (
        remaining_lifecycle_seconds(life, now=at(200), clock_name="QUALIFICATION") == 0
    )
    readings = [
        remaining_lifecycle_seconds(life, now=at(minutes))
        for minutes in (30, 600, 1_000, 1_459, 1_460, 4_000)
    ]
    assert readings[0] == 85_800
    assert readings[-1] == 0
    assert readings == sorted(readings, reverse=True)
    assert all(value >= 0 for value in readings)


def test_ac_009_an_abstention_or_unknown_clock_reports_unknown() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-009: unknown is not zero.

    GIVEN an abstained lifecycle and a supported one
    WHEN remaining time is read from the abstained observation or with an
    undeclared clock name
    THEN the abstention reports unknown rather than zero and the undeclared name
    is refused
    """

    thesis = supported_thesis()
    abstained = observe(thesis)
    assert remaining_lifecycle_seconds(abstained, now=at(30)) is None
    assert remaining_lifecycle_seconds(abstained, now=at(30), clock_name="QUALIFICATION") is None
    assert abstained.clock("QUALIFICATION") is None
    assert abstained.clock("HOLDING") is None
    supported = observe(thesis, anchor=anchor_for(thesis))
    assert remaining_lifecycle_seconds(supported, now=at(30)) is not None
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        remaining_lifecycle_seconds(supported, now=at(30), clock_name="NOPE")
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        remaining_lifecycle_seconds(supported, now=at(30), clock_name="holding")


def test_ac_009_reading_before_a_clock_started_fails_closed() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-009: no read before the clock exists.

    GIVEN clocks that start at classification and at the first fill
    WHEN remaining time is read before those instants, with a naive instant or
    from a non-lifecycle record
    THEN each read fails closed instead of reporting invented remaining time
    """

    thesis = supported_thesis()
    life = observe(thesis, anchor=anchor_for(thesis))
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot be observed before it started"
    ):
        remaining_lifecycle_seconds(life, now=at(15))
    with pytest.raises(
        HorizonLifecycleContractError, match="cannot be observed before it started"
    ):
        remaining_lifecycle_seconds(life, now=at(5), clock_name="QUALIFICATION")
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a HorizonThesisLifecycle"
    ):
        remaining_lifecycle_seconds("life", now=at(30))  # type: ignore[arg-type]
    with pytest.raises(HorizonLifecycleContractError, match="timezone-aware"):
        remaining_lifecycle_seconds(life, now=at(30).replace(tzinfo=None))


# ---------------------------------------------------------------------------
# AC-010 Attaching a first fill records the anchor once
# ---------------------------------------------------------------------------


def attach(
    thesis: HorizonThesis,
    *,
    first_fill_minutes: float = 20,
    receipt_minutes: float = 30,
    now: float = 30,
    filled_quantity: float = 0.5,
    average_fill_price: float = 100.0,
    source_reference: str = "FILL:1",
    previous: HorizonThesisLifecycle | None = None,
) -> HorizonThesisLifecycle:
    """Attach the first fill and observe the lifecycle in one step."""

    return attach_first_fill(
        thesis,
        policy=lifecycle_policy(),
        first_fill_at=at(first_fill_minutes),
        receipt_at=at(receipt_minutes),
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        source_reference=source_reference,
        now=at(now),
        previous=previous,
    )


def test_ac_010_attach_records_the_anchor_and_the_observation_together() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-010: one receipt for both records.

    GIVEN a first fill observed at the thirtieth minute
    WHEN the anchor is attached and the lifecycle observed in one step
    THEN the anchor and the observation share the receipt instant, the anchor
    identity is the same as a separately recorded anchor, and the observation
    equals the direct two step evaluation key for key
    """

    thesis = supported_thesis()
    attached = attach(thesis)
    assert attached.receipt_at == at(30) == attached.anchor.receipt_at
    assert attached.evaluated_at == at(30)
    assert attached.anchor.first_fill_at == at(20)
    assert attached.anchor.source_references == ("FILL:1",)
    assert attached.anchor.anchor_id == anchor_for(thesis, receipt_minutes=30).anchor_id
    direct = observe(
        thesis, anchor=anchor_for(thesis, receipt_minutes=30), now=30, receipt=30
    )
    assert attached.to_dict() == direct.to_dict()
    assert attached.lifecycle_id == direct.lifecycle_id
    assert attached.abstention_reason is LifecycleAbstentionReason.NONE


def test_ac_010_re_attaching_the_same_first_fill_reuses_the_recorded_anchor() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-010: re-attachment is idempotent.

    GIVEN a lifecycle whose anchor has accumulated a later fill
    WHEN the same first fill is re-attached
    THEN the already recorded anchor is reused, the accumulated fills are not
    discarded, and the lifecycle identity is unchanged
    """

    thesis = supported_thesis()
    first = attach(thesis)
    accumulated = record_additional_fill(
        first.anchor,
        filled_quantity_added=0.5,
        average_fill_price=120.0,
        fill_at=at(40),
        receipt_at=at(45),
        source_reference="FILL:2",
    )
    with_fill = observe(thesis, anchor=accumulated, now=45, receipt=45)
    assert with_fill.anchor == accumulated
    again = attach(thesis, now=45, receipt_minutes=45, previous=with_fill)
    assert again.anchor == accumulated
    assert again.anchor.fill_count == 2
    assert again.lifecycle_id == with_fill.lifecycle_id
    assert again.receipt_at == at(45)
    assert again.lifecycle_id == first.lifecycle_id
    assert again.clock("HOLDING").started_at == first.clock("HOLDING").started_at


def test_ac_010_a_different_first_fill_or_a_foreign_previous_is_refused() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-010: the anchor is recorded once.

    GIVEN a recorded anchor
    WHEN a different first fill is attached, a previous lifecycle of another
    revision or without an anchor is supplied, or the previous record is of the
    wrong type
    THEN each is refused, so the anchor of a revision is never rewritten
    """

    thesis = supported_thesis()
    first = attach(thesis)
    with pytest.raises(
        HorizonLifecycleContractError, match="recorded once and never rewritten"
    ):
        attach(thesis, first_fill_minutes=21, previous=first)
    foreign = classified_thesis(evidence_overrides={"decision_id": "DEC:other"})
    with pytest.raises(
        HorizonLifecycleContractError, match="same thesis revision"
    ):
        attach(foreign, previous=first)
    abstained = observe(thesis)
    with pytest.raises(
        HorizonLifecycleContractError, match="must carry the anchor being re-attached"
    ):
        attach(thesis, previous=abstained)
    with pytest.raises(
        HorizonLifecycleContractError, match="must be a HorizonThesisLifecycle"
    ):
        attach(thesis, previous="previous")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-011 Zero trading authority, storage, environment and runtime wiring
# ---------------------------------------------------------------------------


def test_ac_011_the_module_carries_no_authority_storage_or_environment_surface() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-011: zero authority by construction.

    GIVEN the R5-B contract module
    WHEN its imports, source and exports are inspected
    THEN it imports no network, database, filesystem, subprocess, clock,
    randomness or persistence facility, names no order, sizing, reservation,
    credential or store surface, reads no environment variable and exports no
    authority-shaped name
    """

    assert MODULE.is_file()
    source = module_source(MODULE)
    lowered = source.lower()
    assert module_imports(MODULE) & FORBIDDEN_PRIMITIVES == set()
    for token in FORBIDDEN_AUTHORITY_TOKENS:
        assert token not in lowered, token
    for token in FORBIDDEN_PERSISTENCE_TOKENS:
        assert token not in source, token
    for pattern in ("OPIP_", "os.environ", "os.getenv", "getenv(", "environ["):
        assert pattern not in source, pattern
    assert "app.opip.contracts.thesis" in source
    for name in lifecycle_module.__all__:
        asserted = name.lower()
        for token in FORBIDDEN_AUTHORITY_TOKENS:
            assert token not in asserted, (name, token)
        for token in ("store", "writer", "persist", "allocate", "execute"):
            assert token not in asserted, (name, token)


def test_ac_011_no_runtime_root_or_application_module_consumes_the_contract() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-011: not wired into runtime.

    GIVEN the runtime roots that own trading, discovery, decision and risk paths
    WHEN every application module is inspected
    THEN no runtime root imports the contract and no application module outside
    the contract itself references it, so it cannot act on production state
    """

    targets = (
        "app.opip.contracts.horizon_lifecycle",
        "contracts.horizon_lifecycle",
        "horizon_lifecycle",
    )
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            hits = sorted(name for name in module_imports(path) if name in targets)
            if hits:
                offenders.append(f"{path.name}: {hits}")
    assert offenders == []
    consumers = [
        path.relative_to(APP_ROOT).as_posix()
        for path in sorted((APP_ROOT / "app").rglob("*.py"))
        if path != MODULE and "horizon_lifecycle" in module_source(path)
    ]
    assert consumers == []
    for module in R5B_MODULES:
        assert module == MODULE


def test_ac_011_the_frozen_r3_isolation_scans_need_no_widening() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-011: weaken nothing, widen nothing.

    GIVEN the two frozen R3 isolation suites that scan every module under `app/`
    WHEN their sources and this increment's module are compared
    THEN every substantive isolation assertion of those suites is still present
    and this module contains none of the tokens they forbid in other modules, so
    no allowlist needed widening and no check was relaxed
    """

    f3_source = module_source(
        APP_ROOT / "tests" / "test_opip_r3_f3_ignition_detector.py"
    )
    f4_source = module_source(
        APP_ROOT / "tests" / "test_opip_r3_f4_opportunity_lifecycle.py"
    )
    for source in (f3_source, f4_source):
        assert 'rglob("*.py")' in source
        assert "FORBIDDEN_MODULE_PREFIXES" in source
        assert "AUTHORITY_TOKENS" in source
        assert 'OPIP_FEATURE_BUS_MODE: "shadow"' in source
    assert "run_cycle" in f4_source
    assert '"opip.detectors" not in text' in f3_source
    assert '"detectors.ignition" not in text' in f3_source
    assert '"contracts.detector" not in text' in f3_source
    assert '"contracts.opportunity" not in text' in f4_source
    assert '"opportunity_lifecycle" not in text' in f4_source
    assert '"apply_claim" not in text' in f4_source
    source = module_source(MODULE)
    for token in (
        "opip.detectors",
        "detectors.ignition",
        "contracts.detector",
        "contracts.opportunity",
        "opportunity_lifecycle",
        "apply_claim",
    ):
        assert token not in source, token


def test_ac_011_release_profile_and_paper_posture_are_unchanged() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-011: profile posture preserved.

    GIVEN the repository release-profile contract and Compose literals
    WHEN they are inspected
    THEN `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER`
    blocked, paper-v2 stays off, and this increment adds no mode key
    """

    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert validate_profile_contract("EVIDENCE_SHADOW") == (True, [])
    valid, reasons = validate_profile_contract("TARGET_PAPER")
    assert valid is False
    assert reasons and "blocked" in reasons[0]
    allowed = profiles["EVIDENCE_SHADOW"]["allowed_modes"]
    assert allowed["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert allowed["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert allowed["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert allowed["OPIP_PAPER_V2_MODE"] == "off"
    assert not any(key.startswith("OPIP_R5") for key in allowed)
    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"


def test_ac_011_every_record_declares_itself_non_authoritative() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-011: research-only evidence.

    GIVEN an anchor, three clocks, a supported lifecycle and an abstention
    WHEN each record's flag and serialized keys are inspected
    THEN every record declares itself non-authoritative and no serialized record
    carries an authority, order, allocation or sizing key
    """

    thesis = supported_thesis()
    anchor = anchor_for(thesis)
    life = observe(thesis, anchor=anchor)
    records = (anchor, *life.clocks, life, observe(thesis))
    assert len(records) == len(CLOCK_ORDER) + 3
    for record in records:
        assert record.authoritative is False
        serialized = record.to_dict()
        for key in (
            "authoritative",
            "authority",
            "allocation",
            "order",
            "position_size",
            "reserve_capital",
        ):
            assert key not in serialized, (serialized["record_type"], key)
    source = module_source(MODULE)
    assert "authoritative" in source
    assert source.count("return False") >= len(CLOCK_ORDER)


# ---------------------------------------------------------------------------
# AC-012 Scope conformance and exact declared vocabulary
# ---------------------------------------------------------------------------


def test_ac_012_the_increment_scope_contract_is_self_consistent() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-012: contract matches its tests.

    GIVEN this increment's scope contract and the repository acceptance index
    WHEN the contract is parsed and checked
    THEN its increment matches, its declared traceability equals the acceptance
    tests that cite it, every criterion has an implementation-map entry, the
    scope check reports no reason, and the movable increment pointer resolves
    without being pinned by this module
    """

    assert CONTRACT.is_file()
    contract = parse_scope_contract(module_source(CONTRACT))
    assert contract.increment == INCREMENT
    assert set(contract.acceptance_criteria) == {
        f"AC-{index:03d}" for index in range(1, 13)
    }
    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.reasons == ()
    assert result.status == "PASS"
    for ac_id in contract.acceptance_criteria:
        assert contract.traceability.get(ac_id), ac_id
        assert contract.implementation_map.get(ac_id), ac_id
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    increments = [item.increment for item in contracts]
    assert len(increments) == len(set(increments))
    assert INCREMENT in increments
    # The pointer is movable, so it is not pinned here; it must merely resolve.
    atdd_root = APP_ROOT / "docs" / "atdd"
    active = (atdd_root / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    assert (atdd_root / "scope-contracts" / f"{active}.md").is_file(), active
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []


def test_ac_012_the_declared_schema_versions_tokens_and_prefixes_are_exact() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-012: the declared vocabulary is exact.

    GIVEN the module's declared schema versions, policy tokens, identity prefixes
    and vocabularies
    WHEN each is compared with its declared value
    THEN every token is the exact declared one, the clock vocabulary is exact and
    case sensitive, the owner-input status is `OWNER_DECISION_REQUIRED`, and the
    policy ships no owner number
    """

    assert HORIZON_LIFECYCLE_SCHEMA_VERSION == "horizon-lifecycle-v1"
    assert FIRST_FILL_ANCHOR_SCHEMA_VERSION == "first-fill-anchor-v1"
    assert THESIS_CLOCK_SCHEMA_VERSION == "thesis-clock-v1"
    assert HORIZON_LIFECYCLE_POLICY_VERSION == "horizon-lifecycle-shadow-policy-v1"
    assert HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION == (
        "horizon-lifecycle-engineering-scenario-v1"
    )
    assert DECLARED_LIFECYCLE_POLICY_VERSIONS == (
        HORIZON_LIFECYCLE_POLICY_VERSION,
        HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    )
    assert HORIZON_LIFECYCLE_ID_PREFIX == "HLFC"
    assert FIRST_FILL_ANCHOR_ID_PREFIX == "FFAN"
    assert THESIS_CLOCK_ID_PREFIX == "TCLK"
    assert CLOCK_ORDER == (
        ThesisClockName.QUALIFICATION,
        ThesisClockName.HOLDING,
        ThesisClockName.INVALIDATION,
    )
    assert require_clock_name("HOLDING", field_name="clock_name") is (
        ThesisClockName.HOLDING
    )
    assert require_clock_name(ThesisClockName.INVALIDATION, field_name="clock_name") is (
        ThesisClockName.INVALIDATION
    )
    with pytest.raises(HorizonLifecycleContractError, match="unsupported token"):
        require_clock_name("holding", field_name="clock_name")
    assert OWNER_INPUT_STATUS == "OWNER_DECISION_REQUIRED"
    policy = ThesisLifecyclePolicy()
    assert policy.qualification_budget_seconds is None
    assert policy.holding_budget_seconds is None
    assert policy.band_for(HorizonClass.TACTICAL) is None
    source = module_source(MODULE)
    assert "qualification_budget_seconds: int | None = None" in source
    assert (
        "holding_budget_seconds: Mapping[HorizonClass, tuple[int, int]] | None = None"
        in source
    )


def test_ac_012_the_module_holds_no_hidden_clock_randomness_or_environment_read() -> None:
    """ATDD-R5-B-horizon-intelligence/AC-012: explicit instants only.

    GIVEN the R5-B contract module
    WHEN its source and imports are inspected
    THEN it reads no wall clock, no randomness, no environment, no filesystem and
    no network, and it validates and serializes instants through the frozen
    temporal and serialization owners
    """

    source = module_source(MODULE)
    for pattern in (
        "datetime.now",
        "utcnow",
        "time.time",
        "time.monotonic",
        "random.",
        "uuid",
        "os.environ",
        "getenv(",
        "Path(",
        "open(",
        "read_text",
        "requests",
        "socket",
        "subprocess",
        "sqlite3",
        "pickle",
        "asyncio",
    ):
        assert pattern not in source, pattern
    imports = module_imports(MODULE)
    assert imports & FORBIDDEN_PRIMITIVES == set()
    assert "datetime" in imports
    assert "from datetime import datetime, timedelta" in source
    assert "app.opip.contracts.temporal" in source
    assert "require_utc" in source
    assert "iso_z" in source
    assert "stable_hash" in source
    assert "authoritative" in source



















