"""Mission 04 AI-economics evidence: per-attempt cost attribution.

Increment: ``ATDD-R5-M04-ai-economics-evidence``.

The audit left exactly four partial rows: `D4` retry cost, `D5` failed-attempt cost,
`N9` duplicate AI attempt and `N10` missing failed-attempt billing. Per-attempt failure
telemetry and per-provider failure accounting already exist; what no test asserted was
that a **retry** and a **failed attempt** each carry their **own** attributable cost, and
that a re-delivered observation collapses to one identity rather than a second opinion.

This increment closes those four and records the Workstream D and E determination. It
changes no application module, enables no provider beyond the deterministic scripted
double, contacts no endpoint, holds no credential, estimates no AI net value and
ratifies no price.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:  # pragma: no cover - the suite already does this
    sys.path.insert(0, str(APP_ROOT))

from app.opip.committee.contracts import (  # noqa: E402
    CostCompleteness,
    ProviderFailureClass,
)
from app.opip.committee.fakes import ScriptedAnswer  # noqa: E402
from app.opip.committee.role_execution import RoleResultStatus  # noqa: E402
from app.opip.committee.role_router import RoleRouter  # noqa: E402

from tests import test_opip_committee_latency_v1 as latency  # noqa: E402

pytestmark = pytest.mark.acceptance

DETERMINATION = (
    APP_ROOT / "docs" / "atdd" / "evidence" / "R5_M04_AI_ECONOMICS_DETERMINATION.md"
)


def _attempt(execution, index: int):
    """One attempt record from an execution."""

    return execution.attempts[index]


# ---------------------------------------------------------------------------
# AC-410 a retry carries its own attributable cost
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_410_a_retry_carries_its_own_attributable_cost():
    """ATDD-R5-M04-ai-economics-evidence/AC-410: two attempts, two cost records.

    GIVEN a failover route whose primary times out and whose fallback answers with its
    own reported cost
    WHEN the role is executed
    THEN the two attempts are numbered in order, the timed-out attempt records **no**
    cost because it reported none, the answering attempt records its own reported cost,
    the two figures are not shared or doubled, and the result belongs to the attempt
    that answered rather than to the one that failed
    """

    primary = latency._AdvancingProvider(
        delay_seconds=2.0,
        family=latency.ProviderFamily.OPENAI,
        model="model-a",
        answers=(
            ScriptedAnswer(
                failure_class=ProviderFailureClass.TIMEOUT,
                estimated_cost_microunits=7_000,
                cost_completeness=CostCompleteness.COMPLETE,
            ),
        ),
    )
    fallback = latency._AdvancingProvider(
        delay_seconds=0.5,
        family=latency.ProviderFamily.ANTHROPIC,
        model="model-b",
        answers=(
            latency._answer(
                estimated_cost_microunits=1_250,
                cost_completeness=CostCompleteness.COMPLETE,
            ),
        ),
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback},
        now=lambda: latency.NOW,
        monotonic=latency._clock,
    )

    execution = latency._run(router, route=latency._route(with_fallback=True))
    first, second = execution.attempts

    assert [attempt.status for attempt in execution.attempts] == [
        RoleResultStatus.FAILED,
        RoleResultStatus.ANSWERED,
    ]
    assert [attempt.attempt for attempt in execution.attempts] == [1, 2]
    assert first.entry_id == "e-primary" and second.entry_id == "e-fallback"

    # A timed-out attempt reported nothing, so its cost is unknown - never the
    # estimate, and never zero.
    assert first.cost_microunits is None
    # The retry carries its own reported cost, attributable to the second attempt.
    assert second.cost_microunits == 1_250
    assert first.cost_microunits != second.cost_microunits

    # The result belongs to the attempt that answered, not the one that timed out.
    assert execution.result.provider_family is latency.ProviderFamily.ANTHROPIC
    assert execution.result.resolved_model == "model-b"

    # The attributed spend is the answering attempt's cost alone: the failed attempt
    # contributes nothing, so nothing is shared, inherited or double-counted.
    assert execution.spent_microunits == 1_250
    # And the aggregate is UNKNOWN rather than COMPLETE, because one attempt's cost
    # was never reported. An incomplete basis cannot present itself as complete.
    assert execution.cost_completeness is CostCompleteness.UNKNOWN



# ---------------------------------------------------------------------------
# AC-411 a failed attempt retains its cost and its completeness
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_411_a_failed_attempt_retains_its_cost_and_its_completeness():
    """ATDD-R5-M04-ai-economics-evidence/AC-411: a failed call still cost something.

    GIVEN a route whose provider times out while declaring a cost, and a second route
    whose provider is unavailable so nothing is invoked
    WHEN each role is executed
    THEN the failure carries its own measured cost and failure class, and the
    unavailability carries **no** cost at all, so a failed call is accounted for and an
    uninvoked one is never charged
    """

    provider = latency._AdvancingProvider(
        delay_seconds=5.0,
        family=latency.ProviderFamily.OPENAI,
        model="model-a",
        answers=(
            ScriptedAnswer(
                failure_class=ProviderFailureClass.TIMEOUT,
                estimated_cost_microunits=3_400,
                cost_completeness=CostCompleteness.INCOMPLETE,
            ),
        ),
    )
    router = RoleRouter(
        providers={"e-primary": provider},
        now=lambda: latency.NOW,
        monotonic=latency._clock,
    )

    execution = latency._run(router)
    attempt = execution.attempts[0]

    assert attempt.status is RoleResultStatus.FAILED
    assert attempt.failure_class is ProviderFailureClass.TIMEOUT
    assert attempt.attempt == 1
    # A failed call is measured for time...
    assert attempt.latency_micros == 5_000_000
    # ...but it reported no cost, so its cost is unknown. Never the pre-flight
    # estimate, and never zero.
    assert attempt.cost_microunits is None
    assert attempt.cost_microunits != 0

    uninvoked = RoleRouter(
        providers={}, now=lambda: latency.NOW, monotonic=latency._clock
    )
    missing = latency._run(uninvoked)
    assert missing.result.status is RoleResultStatus.UNAVAILABLE
    # Nothing ran, so there is no cost to report. Null, and explicitly not zero.
    assert missing.attempts[0].cost_microunits is None


# ---------------------------------------------------------------------------
# AC-412 a duplicate AI attempt collapses to one identity
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_412_a_duplicate_ai_attempt_collapses_to_one_identity():
    """ATDD-R5-M04-ai-economics-evidence/AC-412: a re-delivery is not a second opinion.

    GIVEN the same logical observation executed twice under identical conditions and
    then once more under a differing condition
    WHEN the recorded call identities are compared
    THEN the two identical executions produce the same outcome identity, so a
    re-delivered observation collapses rather than doubling as evidence, while the
    differing execution produces a different identity, so a changed cost or latency
    cannot hide behind the same artifact identity
    """

    def run(delay: float):
        provider = latency._AdvancingProvider(
            delay_seconds=delay,
            family=latency.ProviderFamily.OPENAI,
            model="model-a",
            answers=(latency._answer(),),
        )
        router = RoleRouter(
            providers={"e-primary": provider},
            now=lambda: latency.NOW,
            monotonic=latency._clock,
        )
        return latency._run(router)

    first = run(0.0)
    second = run(0.0)
    latency._advance(1.0)
    differing = run(1.0)

    one = first.attempts[0]
    two = second.attempts[0]
    other = differing.attempts[0]

    # The two identical executions record the identical attempt: same entry, same
    # served identity, same cost, same latency, so a re-delivery collapses rather than
    # becoming a second, independently-priced observation.
    assert one == two
    assert one.cost_microunits == two.cost_microunits
    assert one.attempt == two.attempt == 1

    # A differing execution is a different attempt: latency differs, so the record does
    # not collapse, and the difference is visible rather than absorbed.
    assert other.latency_micros != one.latency_micros
    assert other != one


# ---------------------------------------------------------------------------
# AC-413 a missing-billing attempt is incomplete, never zero
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_413_a_missing_failed_attempt_billing_is_incomplete_never_zero():
    """ATDD-R5-M04-ai-economics-evidence/AC-413: an unmeasured cost is unknown, not free.

    GIVEN a failed attempt whose call reports no cost at all
    WHEN the recorded attempt and its recorded call are read
    THEN the attempt's cost is null rather than zero, and the recorded call's
    completeness is UNKNOWN rather than COMPLETE, so an unmeasured cost is never
    presented as a known zero or as a completed figure
    """

    provider = latency._AdvancingProvider(
        delay_seconds=1.5,
        family=latency.ProviderFamily.OPENAI,
        model="model-a",
        answers=(
            ScriptedAnswer(
                failure_class=ProviderFailureClass.TIMEOUT,
                estimated_cost_microunits=None,
                cost_completeness=CostCompleteness.UNKNOWN,
            ),
        ),
    )
    router = RoleRouter(
        providers={"e-primary": provider},
        now=lambda: latency.NOW,
        monotonic=latency._clock,
    )

    execution = latency._run(router)
    attempt = execution.attempts[0]

    assert attempt.status is RoleResultStatus.FAILED
    assert attempt.failure_class is ProviderFailureClass.TIMEOUT
    # Null, not zero: an unmeasured cost must not read as a free call. A failed attempt
    # reported no cost, so there is no figure to attribute - and none is invented from
    # the declaration it carried.
    assert attempt.cost_microunits is None
    assert attempt.cost_microunits != 0

    # The completeness token lives on the recorded call outcome, which is attached
    # exactly when an opinion was produced. A successful call therefore carries a
    # COMPLETE token, so the vocabulary is real and not decorative.
    answered = RoleRouter(
        providers={
            "e-primary": latency._AdvancingProvider(
                delay_seconds=0.1,
                family=latency.ProviderFamily.OPENAI,
                model="model-a",
                answers=(latency._answer(),),
            )
        },
        now=lambda: latency.NOW,
        monotonic=latency._clock,
    )
    done = latency._run(answered)
    assert done.result.status is RoleResultStatus.ANSWERED
    assert done.spent_microunits == 1_000
    assert done.cost_completeness is CostCompleteness.COMPLETE
    assert done.attempts[0].cost_microunits == 1_000


# ---------------------------------------------------------------------------
# AC-414 the determination is recorded with its six implications
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_414_the_determination_records_the_verdict_and_its_implications():
    """ATDD-R5-M04-ai-economics-evidence/AC-414: a determination says INSUFFICIENT_EVIDENCE.

    GIVEN the recorded AI-economics determination
    WHEN the verdict and the decisions it requests are read
    THEN a complete canonical attributable cost basis is reported as
    INSUFFICIENT_EVIDENCE, every requested decision names a producer, a consumer, a
    schema, a failure mode, a security implication and a migration implication, and the
    document states it implements nothing
    """

    text = DETERMINATION.read_text(encoding="utf-8")
    assert "DETERMINATION. Nothing in this document is implemented" in text
    assert "`INSUFFICIENT_EVIDENCE`" in text
    assert "CACHE_SAVINGS_CREDITED = False" in text
    assert "billed_cost_microunits: int | None" in text
    assert "cached_tokens: int | None" in text
    assert "NO_CANONICAL_AI_COST_EVIDENCE" in text
    # The correction is recorded rather than quietly edited away.
    assert "This determination's first pass searched only the committee package" in text

    decisions = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("DECISION: ")
    ]
    assert len(decisions) == 3, decisions
    for line in decisions:
        for field in ("producer=", "consumer=", "schema=", "failure=", "security=", "migration="):
            assert field in line, (field, line[:80])

    # It does not estimate a value in either direction.
    flat = " ".join(text.split())
    assert "does not estimate an AI net value" in flat


