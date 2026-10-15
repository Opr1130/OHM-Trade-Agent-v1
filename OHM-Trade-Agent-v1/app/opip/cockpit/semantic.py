"""F10-A semantic read service: one versioned read model over canonical projections.

Increment: ``ATDD-R5-F10-semantic-read-service`` (mission step ``F10-A`` of the
owner-approved F10 mission). What this module *is* is governed by
``docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md``; what each page
and panel *is* is governed by ``docs/architecture/OPIP_F10_SOURCE_CENSUS.md``.

What this service is
--------------------

One read-only service that answers the semantic questions the eight frozen F10
pages ask, from projections that already exist on the read-only analytics plane:
the reconciled Paper-v2 ledger (``app.opip.cockpit.ledger``) and the
portfolio/overview projection (``app.opip.cockpit.portfolio``). It adds no second
source of truth, no projection, no store, no scheduler, no route and no metric
definition. It replaces no existing surface and retires nothing.

KEY LAW - ONE SEMANTIC MODEL, ONE METRIC REGISTRY.
Every numeric fact published here is either a field an existing projection already
publishes, or a count over a declared population of the reconciled ledger, or a
state the existing trust envelope already reports. No formula is restated:
``paper-metrics-v1`` (``app.opip.contracts.paper_metrics``) remains the only
metric-semantics authority, and a metric response carries a *reference* to its
registry entry - identifier, definition version and declared population - never the
formula text. Where this service can recount a published count from the ledger
rows, it does, and reports the comparison instead of trusting the projection.

KEY LAW - MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE.
A fact this plane cannot produce is returned as an explicit state with a reason code
and, where one is registered, the registered evidence-gap identifier. It is never
returned as ``0``, an empty collection, ``false`` or an omitted key. A page whose
required facts are not all producible serves no value at all, even when one of its
panels happens to be readable, because a partially rendered page is the failure mode
the F10 parity predicate exists to prevent: the same fact stays available from
:meth:`SemanticReadService.metric` and
:meth:`SemanticReadService.population`, but the page is not claimed.

KEY LAW - DECLARATION IS NOT ACTIVATION.
This module declares the read routes the service exposes and deliberately does not mount
them itself: ``MOUNTED`` is ``False``, ``MOUNTED_PATHS`` is empty, and this module
imports no web framework. The declared routes are mounted by the F10 read router in
``app/api/cockpit_semantic.py``, on the analytics plane, under the owner-approved
increment named by ``FOLLOWER_INCREMENT``, because the analytics plane's mounted path
set is pinned by an existing exposure contract: widening the externally reachable
surface is a deliberate, owner-visible change that must never be a side effect of
implementing the service. The mounting posture a read envelope publishes is therefore
the mounting component's declaration, and this module makes no mounting claim of its own.

Authority: none. Every method is a read over an injected projection. Nothing here
writes, admits, protects, exits, notifies, promotes, releases or deploys, and no
value this service publishes can override a deterministic risk or execution gate.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from app.opip.cockpit.ledger import (
    COCKPIT_LEDGER_PROJECTION_VERSION,
    EconomicResult,
    ExecutionResult,
    ExitMechanism,
    LifecycleStatus,
    PaperLedger,
    ReconciledPaperTrade,
)
from app.opip.cockpit.portfolio import (
    COCKPIT_PORTFOLIO_PROJECTION_VERSION,
    EXPECTANCY_REQUIRES_INTERVAL,
    NO_MARK_EVIDENCE,
    CockpitOverview,
    CurrencyPortfolio,
    build_overview,
    build_realized_equity_series,
)
from app.opip.contracts.paper_execution import TerminalReconciliationState
from app.opip.contracts.paper_metrics import PAPER_METRICS, metric_definition
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES
from app.opip.profit_intelligence.semantics import EVIDENCE_GAPS

# --------------------------------------------------------------------------- #
# Identity and declared boundaries
# --------------------------------------------------------------------------- #

SEMANTIC_SERVICE_VERSION = "cockpit-semantic-v1"


def _registry_definition_version() -> str:
    """The registry's definition version, read from the registry itself.

    Read here rather than imported as the registry's version constant, so this module
    cannot hold a second version string that drifts from the registry it describes,
    and so the application keeps exactly one module defining versioned metric
    semantics. A registry whose entries disagree about their definition version cannot
    be published under one semantic version, so that case fails closed at import.
    """
    versions = {
        definition.definition_version for definition in PAPER_METRICS.values()
    }
    if len(versions) != 1:
        raise RuntimeError(
            "metric registry publishes inconsistent definition versions: "
            + ", ".join(sorted(versions))
        )
    return versions.pop()


#: The metric-semantics authority. A reference, never a re-declaration.
SEMANTIC_VERSION = _registry_definition_version()
LEDGER_PROJECTION_VERSION = COCKPIT_LEDGER_PROJECTION_VERSION
PORTFOLIO_PROJECTION_VERSION = COCKPIT_PORTFOLIO_PROJECTION_VERSION

#: The instant a row is placed in time by. Published with every windowed response so
#: a consumer never has to guess which temporal field a window applied to.
TIME_BASIS = "last_exit_fill_at"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

#: Frozen by the census and the parity specification. This service decides no
#: per-page projection ownership and claims no parity.
AUTHORITY_UNDECIDED = "UNDECIDED"
PARITY_STATUS_NOT_VERIFIED = "NOT_VERIFIED"
COUNTS_AND_PNL_RECONCILIATION = "NOT_RECORDED"
PROOF_REQUIRED = (
    "fixture reconciliation of counts and P&L at one watermark against the "
    "declared canonical projection"
)
FOLLOWER_INCREMENT = "ATDD-R5-F10-dashboard-parity-implementation"
PARITY_VERIFIED_PAGES = 0

#: No threshold, tolerance, sample size or bound is ratified by this module.
THRESHOLD_POLICY = "OWNER_DECISION_REQUIRED"
RATIFIED_THRESHOLDS: tuple[str, ...] = ()

#: Declaration without activation. This module mounts nothing. The exposed read surface
#: is mounted by the read router in ``app/api/cockpit_semantic.py``, on the analytics
#: plane, and its path set is pinned by ``tests/test_bc4_cockpit_ui_v1.py`` and
#: ``tests/test_bc4_cockpit_exposure_v1.py``. ``FOLLOWER_INCREMENT`` names the
#: owner-approved increment that authorized that widening.
MOUNTED = False
EXPOSURE_WIDENING_REQUIRES = "OWNER_DECISION: analytics read-surface widening"

SOURCE = "CANONICAL_REPLICA"
UNSPECIFIED_IDENTITY = "UNSPECIFIED"

# --------------------------------------------------------------------------- #
# Reason codes. Every withheld, blocked or absent value names exactly one.
# --------------------------------------------------------------------------- #

REASON_UPSTREAM_NOT_ON_READ_PLANE = "UPSTREAM_EVIDENCE_NOT_ON_READ_PLANE"
REASON_NO_PRODUCER_ON_READ_PLANE = "NO_PRODUCER_ON_READ_PLANE"
REASON_NO_ORDER_INTENT_ENUMERATION = "NO_CANONICAL_ORDER_INTENT_ENUMERATION"
REASON_NO_POPULATION_AGGREGATION = "NO_REGISTERED_POPULATION_AGGREGATION"
REASON_NO_DECISION_REFERENCE_PRICE = "NO_DECISION_REFERENCE_PRICE_ON_PLANE"
REASON_NO_EXIT_REFERENCE_PRICE = "NO_EXIT_REFERENCE_PRICE_ON_PLANE"
REASON_NO_POLICY_REPLAY = "NO_VERSIONED_POLICY_REPLAY_ON_PLANE"
REASON_NO_CANONICAL_AI_COST = "NO_CANONICAL_AI_COST_EVIDENCE"
REASON_NO_CANONICAL_INCIDENT_STORE = "NO_CANONICAL_INCIDENT_STORE"
REASON_NO_CANONICAL_CASH_LEDGER = "NO_CANONICAL_CASH_LEDGER_ON_PLANE"
REASON_UNREADABLE = "CANONICAL_PROJECTION_UNREADABLE"
REASON_IDENTITY_UNSPECIFIED = "DEPLOYMENT_IDENTITY_UNSPECIFIED"
REASON_QUOTE_CURRENCY_REQUIRED = "QUOTE_CURRENCY_REQUIRED_FOR_THIS_METRIC"
REASON_NO_STARTING_EQUITY = "NO_DECLARED_STARTING_EQUITY"
REASON_BLOCKED_PAGE = "PAGE_REQUIRED_FACTS_NOT_PRODUCIBLE"
REASON_POPULATION_MISMATCH = "PROJECTION_COUNT_DOES_NOT_RECONCILE"
REASON_WITHHELD_INTERVAL = EXPECTANCY_REQUIRES_INTERVAL
REASON_NO_MARK_EVIDENCE = NO_MARK_EVIDENCE

#: A row with no provable close instant cannot be placed in a window. It is excluded
#: from the window and counted, never silently dropped.
EXCLUSION_WITHOUT_CLOSE_INSTANT = "ROW_WITHOUT_PROVABLE_CLOSE_INSTANT"

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #


class EvidenceState(str, Enum):
    """The census's closed five-value capability vocabulary.

    This classifies a *page*'s capability. It is deliberately a different axis from
    :class:`FactState`, which is the state of one fact in one response.
    """

    EXISTING_AND_VERIFIED = "EXISTING_AND_VERIFIED"
    EXISTING_NEEDS_EXTENSION = "EXISTING_NEEDS_EXTENSION"
    MISSING = "MISSING"
    BLOCKED_BY_UPSTREAM_EVIDENCE = "BLOCKED_BY_UPSTREAM_EVIDENCE"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"


class FactState(str, Enum):
    """The state of one fact, metric or panel in one response.

    ``SERVED`` and its two qualified forms may carry a value. ``BLOCKED``,
    ``ABSENT`` and ``UNAVAILABLE`` never do: they carry a reason code instead, so a
    consumer cannot mistake a non-answer for a measured zero.
    """

    SERVED = "SERVED"
    SERVED_WITHHELD = "SERVED_WITHHELD"
    SERVED_PARTIAL = "SERVED_PARTIAL"
    BLOCKED_BY_UPSTREAM_EVIDENCE = "BLOCKED_BY_UPSTREAM_EVIDENCE"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


#: States in which a value, or an explicit withholding, may be published.
SERVED_STATES: frozenset[FactState] = frozenset(
    {FactState.SERVED, FactState.SERVED_WITHHELD, FactState.SERVED_PARTIAL}
)

#: States that must never carry a value.
UNSERVED_STATES: frozenset[FactState] = frozenset(
    {
        FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        FactState.ABSENT,
        FactState.UNAVAILABLE,
    }
)

#: The outcomes a *metric* binding is answered from data in. ``SERVED_WITHHELD`` is not
#: one: the withheld served form declares that the metric registry's own estimator
#: requirement forbids a value for that metric, so its answer is a declaration like the
#: blocked states above and is returned without reading the projection at all.
_VALUE_ANSWERED_OUTCOMES: tuple[FactState, ...] = (
    FactState.SERVED,
    FactState.SERVED_PARTIAL,
)

#: Reasons the code refuses a request rather than answering it differently.
REFUSAL_KINDS: tuple[str, ...] = (
    "UNSUPPORTED_FILTER",
    "INVALID_FILTER_VALUE",
    "UNSUPPORTED_PAGE",
    "UNSUPPORTED_METRIC",
    "UNSUPPORTED_FACT",
)


class SemanticServiceError(Exception):
    """Base class for every refused request."""


class UnsupportedFilter(SemanticServiceError):
    """A filter the contract does not define.

    Refused rather than ignored: silently ignoring a filter would present a
    differently-scoped population under the caller's assumed scope.
    """


class InvalidFilterValue(SemanticServiceError):
    """A defined filter carrying a value outside its declared vocabulary."""


class UnsupportedPage(SemanticServiceError):
    """A page identifier outside the frozen eight-page inventory."""


class UnsupportedMetric(SemanticServiceError):
    """A metric identifier outside the versioned registry."""


class UnsupportedFact(SemanticServiceError):
    """A population fact or read-model fact this service does not declare."""


# --------------------------------------------------------------------------- #
# Registries. These are declarations, not data: they are identical for every
# request and for every ledger, so they are module-level and immutable.
# --------------------------------------------------------------------------- #


#: A fact family's ``AVAILABILITY_ON_THIS_PLANE`` (census section 5) is expressed in
#: the census's single closed five-value vocabulary, so it is typed as
#: :class:`EvidenceState` rather than as a second, near-identical enum.
#:
#: The canonical ``app.opip.profit_intelligence.semantics.FactAvailability`` is
#: deliberately **not** reused here: it is a different, four-value vocabulary
#: (``KNOWN``, ``DERIVED``, ``UNAVAILABLE``, ``NOT_APPLICABLE``), and classifying this
#: column with words the census does not use would be exactly the vocabulary drift the
#: census forbids.
_FACT_AVAILABLE = EvidenceState.EXISTING_AND_VERIFIED
_FACT_BLOCKED = EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE


@dataclass(frozen=True)
class FactGrain:
    """One logical fact family and the grain it may be joined at.

    Mirrors section 5 of the census. A read model may only join facts at these
    grains, and each aggregate must reconcile to its drill-down population at one
    watermark.
    """

    fact: str
    required_grain: str
    availability: EvidenceState

    @property
    def is_available(self) -> bool:
        return self.availability is _FACT_AVAILABLE

    def to_dict(self) -> dict[str, str]:
        return {
            "fact": self.fact,
            "required_grain": self.required_grain,
            "availability": self.availability.value,
        }


def _blocked(fact: str, grain: str) -> FactGrain:
    return FactGrain(fact, grain, _FACT_BLOCKED)


def _available(fact: str, grain: str) -> FactGrain:
    return FactGrain(fact, grain, _FACT_AVAILABLE)


FACT_GRAINS: tuple[FactGrain, ...] = (
    _blocked("Candidate lifecycle events", "one candidate lifecycle event"),
    _blocked("Detector evaluations", "one instrument evaluation including no-claim"),
    _blocked("Decisions", "one frozen baseline decision"),
    _blocked("Committee assessments", "one assessment and its bounded role attempts"),
    _available("Paper trades", "one position lifecycle"),
    _available("Paper fills", "one execution fill"),
    _blocked("Outcomes", "one entity, policy, horizon and revision"),
    _blocked("Learning events", "one experiment or learning transition"),
    _blocked("Incidents", "one incident transition"),
    _blocked("AI usage", "one provider invocation attempt"),
    _blocked("Portfolio marks", "one declared valuation observation"),
    _blocked("Attribution", "one registered comparison and evaluation window"),
)

GRAIN_BY_FACT: Mapping[str, FactGrain] = MappingProxyType(
    {grain.fact: grain for grain in FACT_GRAINS}
)

#: The grain every served count and metric population is expressed at.
SERVED_GRAIN = GRAIN_BY_FACT["Paper trades"].required_grain


@dataclass(frozen=True)
class PageDescriptor:
    """One frozen page of the eight-page inventory.

    ``primary_facts`` reproduces the census's page record and ``required_facts`` the
    parity specification's requirement, so neither document can drift from the code
    without a test noticing. Two producer citations are deliberately *not* copied
    verbatim, because existing application-level dormancy guards assert that no module
    outside an owning module names the shadow detector package or the plan record
    class; those rows cite the same producers by name and contract identifier instead,
    since naming them must never look like consuming them. See the citation note above
    ``PAGE_REGISTRY``. ``canonical_projection_authority`` is published as
    ``UNDECIDED``: this service serves facts, it does not declare the owner's per-page
    authority decision, and it claims no parity.
    """

    page_id: str
    adopted_name: str
    operator_question: str
    primary_facts: str
    required_facts: str
    evidence_state: EvidenceState
    panels: tuple[str, ...]
    page_reason: str | None = None

    @property
    def is_readable(self) -> bool:
        return self.evidence_state in (
            EvidenceState.EXISTING_AND_VERIFIED,
            EvidenceState.EXISTING_NEEDS_EXTENSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "adopted_name": self.adopted_name,
            "operator_question": self.operator_question,
            "primary_facts": self.primary_facts,
            "required_facts": self.required_facts,
            "evidence_state": self.evidence_state.value,
            "panels": list(self.panels),
            "canonical_projection_authority": AUTHORITY_UNDECIDED,
            "parity_status": PARITY_STATUS_NOT_VERIFIED,
            "counts_and_pnl_reconciliation": COUNTS_AND_PNL_RECONCILIATION,
            "proof_required": PROOF_REQUIRED,
            "follower_increment": FOLLOWER_INCREMENT,
            "page_reason": self.page_reason,
        }


#: The frozen eight-page inventory.
#:
#: Citation note. Row text is copied from the census and the parity specification so a
#: drift is visible, with two deliberate exceptions. Two existing application-level
#: dormancy guards assert that no module outside an owning module names the shadow
#: detector package or the plan record class, and this service is a consumer of
#: neither: it declares them as blocked producers. Those two citations therefore name
#: the same producers without the guarded symbols, because a citation that reads like
#: a consumption claim would be indistinguishable from an authority widening.
PAGE_REGISTRY: tuple[PageDescriptor, ...] = (
    PageDescriptor(
        page_id="P1_SYSTEM_PULSE",
        adopted_name="System Pulse",
        operator_question="Can I trust the system right now",
        primary_facts=(
            "freshness and stream status, maintenance state, reconciliation "
            "posture, attention codes"
        ),
        required_facts=(
            "pipeline liveness and freshness, coverage and gaps, consumed "
            "watermark, scheduler and lock health"
        ),
        evidence_state=EvidenceState.EXISTING_NEEDS_EXTENSION,
        panels=("P1-PNL-01", "P1-PNL-02", "P1-PNL-03", "P1-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P2_DISCOVERY_REGIME",
        adopted_name="Discovery and Regime",
        operator_question="What did O'Pip observe and what opportunities developed",
        primary_facts=(
            "screening coverage, detector evaluations, candidate funnel and "
            "dispositions"
        ),
        required_facts=(
            "continuous observation coverage, observed pairs and windows, regime "
            "context, explicit unknown coverage"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P2-PNL-01", "P2-PNL-02", "P2-PNL-03", "P2-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P3_DECISION_TRACE",
        adopted_name="Decision Trace",
        operator_question="What did O'Pip know when it decided",
        primary_facts=(
            "decision context identity, temporal fields, planned economics, "
            "execution lineage"
        ),
        required_facts=(
            "complete decision trace per decision, eligible evidence at its own "
            "cutoff and watermark, explicit refusal reasons"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P3-PNL-01", "P3-PNL-02", "P3-PNL-03", "P3-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P4_PAPER_PORTFOLIO",
        adopted_name="Paper Portfolio",
        operator_question="What is the isolated paper account's economics and capital usage",
        primary_facts=(
            "per-currency capital, realized net P&L, execution costs, open "
            "positions, equity series"
        ),
        required_facts=(
            "the frozen SC-08 plan record as a plan revision, positions "
            "and lifecycle, reconciled counts and net economics"
        ),
        evidence_state=EvidenceState.EXISTING_NEEDS_EXTENSION,
        panels=("P4-PNL-01", "P4-PNL-02", "P4-PNL-03", "P4-PNL-04", "P4-PNL-05"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P5_QUALITY_MISSED_OPPORTUNITY",
        adopted_name="Quality and Missed Opportunity",
        operator_question="Does O'Pip find useful opportunities in time",
        primary_facts=(
            "candidate population, missed and late opportunities, calibration, "
            "MFE and MAE, counterfactuals"
        ),
        required_facts=(
            "rejection and no-fill reasons, incomplete outcomes stated as "
            "incomplete, missed-opportunity facts"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P5-PNL-01", "P5-PNL-02", "P5-PNL-03", "P5-PNL-04", "P5-PNL-05"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P6_COMMITTEE_VALUE_AI_COST",
        adopted_name="Committee Value and AI Cost",
        operator_question="Did advisory AI create prospective value after cost",
        primary_facts=(
            "Committee trust stage, paired comparisons, incremental net, AI cost"
        ),
        required_facts=(
            "Committee analysis and its cost, advisory-only status, calibrated "
            "resolution, section 21 Committee trust panels"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P6-PNL-01", "P6-PNL-02", "P6-PNL-03", "P6-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P7_LEARNING_RELEASES",
        adopted_name="Learning and Releases",
        operator_question="Is O'Pip improving on evidence",
        primary_facts=(
            "frozen expectations, matured outcomes, weakness findings, experiments, "
            "release adoption"
        ),
        required_facts=(
            "expectations versus realised outcomes, capital-time and horizon "
            "evaluation, release identity and receipts"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P7-PNL-01", "P7-PNL-02", "P7-PNL-03", "P7-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
    PageDescriptor(
        page_id="P8_INCIDENTS_INFRASTRUCTURE",
        adopted_name="Incidents and Infrastructure",
        operator_question="What failed, what is affected and what recovery completed",
        primary_facts=(
            "incident lifecycle, coverage gaps, reconciliation failures, delivery "
            "failures, recovery state"
        ),
        required_facts=(
            "incidents and their resolution state, coverage-degradation scopes, "
            "protection and infrastructure health"
        ),
        evidence_state=EvidenceState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        panels=("P8-PNL-01", "P8-PNL-02", "P8-PNL-03", "P8-PNL-04"),
        page_reason=REASON_BLOCKED_PAGE,
    ),
)

PAGE_BY_ID: Mapping[str, PageDescriptor] = MappingProxyType(
    {page.page_id: page for page in PAGE_REGISTRY}
)
PAGE_IDS: tuple[str, ...] = tuple(page.page_id for page in PAGE_REGISTRY)

# --------------------------------------------------------------------------- #
# Served fact vocabulary. A panel may only claim facts drawn from these sets, so a
# typo cannot silently invent a served fact.
# --------------------------------------------------------------------------- #

POPULATION_FACTS: Mapping[str, str] = MappingProxyType(
    {
        "paper.population.rows": (
            "every reconciled Paper-v2 lifecycle row, counted at one position "
            "lifecycle per row"
        ),
        "paper.population.settled": (
            "rows whose terminal lifecycle is CLOSED with canonically verified "
            "economics, recounted from the rows"
        ),
        "paper.population.settled_in_equity_series": (
            "settled rows the realized equity series can place at a provable close "
            "instant"
        ),
        "paper.population.open_positions": (
            "rows whose lifecycle status is OPEN, recounted from the rows"
        ),
        "paper.population.flat_unverified": (
            "rows whose lifecycle status is UNRESOLVED, recounted from the rows"
        ),
        "paper.population.admitted": (
            "rows that were admitted to the isolated paper account, recounted from "
            "the rows"
        ),
        "paper.population.reserved_capital": (
            "reserved capital still held, summed from rows whose economics are not "
            "yet definitive"
        ),
    }
)

READ_MODEL_FACTS: Mapping[str, str] = MappingProxyType(
    {
        "read_model.trust": (
            "the independent trust dimensions of the projection this response was "
            "built from"
        ),
        "read_model.reconciliation_posture": (
            "the reconciliation and coverage state the projection reports; never a "
            "collapsed health score"
        ),
        "read_model.attention": (
            "the attention items the projection already emits, with their own codes "
            "and severities"
        ),
        "read_model.uncertainty_reasons": (
            "the machine-readable reasons the projection attaches to its "
            "uncertainty posture"
        ),
        "read_model.valuation_posture": (
            "whether a marked-equity valuation is defensible, and why not when it "
            "is not"
        ),
    }
)

ROW_FACT_GROUPS: Mapping[str, str] = MappingProxyType(
    {
        "ledger.row.identity": (
            "row identity: paper trade, decision context, candidate, episode and "
            "cohort identifiers"
        ),
        "ledger.row.temporal": (
            "row instants: evaluation, entry intent, entry attempt, first entry fill "
            "and last exit fill"
        ),
        "ledger.row.fills": (
            "row fills: requested, filled and exited quantities, entry and exit "
            "VWAPs, execution result"
        ),
        "ledger.row.exit": (
            "row exit: economic result, exit mechanism and every exit mechanism "
            "observed"
        ),
        "ledger.row.residual": (
            "row residual: remaining quantity and the still-open basis it represents"
        ),
    }
)

SERVED_FACT_NAMES: frozenset[str] = frozenset(
    set(POPULATION_FACTS) | set(READ_MODEL_FACTS) | set(ROW_FACT_GROUPS)
)

#: The filters the contract defines. A filter outside this set is refused, never
#: ignored, and a value outside a vocabulary is refused rather than coerced.
SUPPORTED_FILTERS: tuple[str, ...] = (
    "quote_currency",
    "instrument",
    "lifecycle_status",
    "economic_result",
    "execution_result",
    "exit_mechanism",
    "policy_version",
    "window_start",
    "window_end",
    "limit",
    "cursor",
)

#: The census's declared filter names that the readable plane cannot resolve. They
#: stay declared (honest) and are reported as unsupported rather than approximated.
UNSUPPORTED_DECLARED_FILTERS: Mapping[str, str] = MappingProxyType(
    {
        "environment": REASON_IDENTITY_UNSPECIFIED,
        "account": REASON_IDENTITY_UNSPECIFIED,
        "source": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "venue": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "detector phase": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "horizon class": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "reason": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "disposition": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "model version": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "regime": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "strategy": REASON_NO_POPULATION_AGGREGATION,
        "stage": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "role": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "model": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "provider": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "category": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "severity": REASON_NO_CANONICAL_INCIDENT_STORE,
        "cohort": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "release": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "system": REASON_NO_CANONICAL_INCIDENT_STORE,
        "component": REASON_NO_CANONICAL_INCIDENT_STORE,
        "state": REASON_NO_CANONICAL_INCIDENT_STORE,
        "candidate": REASON_UPSTREAM_NOT_ON_READ_PLANE,
        "episode": REASON_UPSTREAM_NOT_ON_READ_PLANE,
    }
)

#: Reason attached to a declared panel filter that is neither resolvable nor declared
#: rejected. Reaching it means the filter vocabulary and the census panel map disagree,
#: which is a contract defect: it is reported and never silently dropped.
UNDECLARED_FILTER_REASON = "FILTER_NOT_DECLARED"

#: Filter names as the census writes them, mapped onto the filters this contract
#: defines. A declared name with no alias and no definition is unsupported.
DECLARED_FILTER_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "instrument": ("instrument",),
        "quote currency": ("quote_currency",),
        "status": ("lifecycle_status",),
        "lifecycle status": ("lifecycle_status",),
        "economic result": ("economic_result",),
        "outcome status": ("economic_result",),
        "execution result": ("execution_result",),
        "exit mechanism": ("exit_mechanism",),
        "policy version": ("policy_version",),
        "time window": ("window_start", "window_end"),
    }
)


@dataclass(frozen=True)
class PanelBinding:
    """One panel of the census's panel map, with its value disposition.

    The eight descriptive fields reproduce the census. The four disposition fields
    are this contract's own: which facts the panel *could* produce on this read
    plane, and why it cannot produce the rest.
    """

    panel_id: str
    operator_question: str
    fact_declaration: str
    producer: str
    declared_filters: tuple[str, ...]
    trust_rule: str
    drill_target: str
    criterion: str
    state: FactState
    served: tuple[str, ...]
    unserved_reasons: tuple[str, ...]
    gap_id: str | None

    @property
    def carries_value(self) -> bool:
        """Whether this panel may carry a value in a page response."""
        return self.state in SERVED_STATES and bool(self.served)

    @property
    def resolved_filters(self) -> tuple[str, ...]:
        """The canonical filters this panel declares, through the shared allowlist."""
        resolved: list[str] = []
        for name in self.declared_filters:
            targets = (
                (name,)
                if name in SUPPORTED_FILTERS
                else DECLARED_FILTER_ALIASES.get(name, ())
            )
            for target in targets:
                if target not in resolved:
                    resolved.append(target)
        return tuple(resolved)

    @property
    def unresolved_filters(self) -> tuple[str, ...]:
        """Declared filters this plane cannot resolve, in declared order."""
        return tuple(
            name
            for name in self.declared_filters
            if name not in SUPPORTED_FILTERS and name not in DECLARED_FILTER_ALIASES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "operator_question": self.operator_question,
            "fact_declaration": self.fact_declaration,
            "producer": self.producer,
            "declared_filters": list(self.declared_filters),
            "resolved_filters": list(self.resolved_filters),
            # Every declared filter is reported: one with a declared reason where the
            # vocabulary rejects it, and an explicit contract-defect reason otherwise,
            # so a filter can never disappear from the rendering.
            "unresolved_filters": [
                {
                    "filter": name,
                    "reason": UNSUPPORTED_DECLARED_FILTERS.get(
                        name, UNDECLARED_FILTER_REASON
                    ),
                }
                for name in self.unresolved_filters
            ],
            "trust_rule": self.trust_rule,
            "drill_target": self.drill_target,
            "criterion": self.criterion,
            "state": self.state.value,
            "served_facts": list(self.served),
            "unserved_reasons": list(self.unserved_reasons),
            # A registered evidence-gap identifier where one covers the unserved
            # part; None where the block is an upstream plane rather than a gap.
            "evidence_gap": self.gap_id,
            "carries_value": self.carries_value,
        }


#: panel_id, question, fact, producer, declared filters, trust rule, drill target,
#: planned acceptance test. Reproduced from the census's panel map.
_PANEL_ROWS: tuple[tuple[Any, ...], ...] = (
    (
        "P1-PNL-01",
        "Is the system trustworthy now",
        "system state, release identity and profile alignment",
        "app.services.release_profiles",
        ("environment", "account"),
        "no aggregate green status while mandatory evidence is missing or contradictory",
        "release and profile evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-020",
    ),
    (
        "P1-PNL-02",
        "Are observations trustworthy",
        "observation coverage and feed freshness",
        "app.opip.data_platform.freshness",
        ("source", "venue", "instrument", "time window"),
        "stale evidence is never presented as current",
        "missing intervals and source history",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-021",
    ),
    (
        "P1-PNL-03",
        "Is the read path healthy",
        "projection lag, reconciliation state and read-model freshness",
        "app.opip.data_platform.reconcile",
        ("environment", "time window"),
        "an unevaluated trust dimension is not healthy",
        "reconciliation detail",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-023",
    ),
    (
        "P1-PNL-04",
        "Is protection operational",
        "F11 protection state and open incidents",
        "app.opip.risk (F11, independent)",
        ("severity", "state"),
        "protection never depends on this panel or Telegram",
        "incident and source history",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-024",
    ),
    (
        "P2-PNL-01",
        "What was observed",
        "eligible universe and coverage against eligibility",
        "app.opip.decision.store (file WAL)",
        ("instrument", "venue", "time window"),
        "exclusions and no-claims are retained, never dropped",
        "instrument evaluation",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-030",
    ),
    (
        "P2-PNL-02",
        "What did the detectors evaluate",
        "detector evaluations including explicit no-claim",
        "detector package (not live)",
        ("detector phase", "instrument", "time window"),
        "an absent evaluation is not a negative claim",
        "evaluation detail",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-031",
    ),
    (
        "P2-PNL-03",
        "How did candidates progress",
        "candidate lifecycle funnel and episode lineage",
        "app.opip.discovery",
        ("status", "horizon class", "time window"),
        "a candidate is never presented as an authorized trade",
        "candidate and episode",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-032",
    ),
    (
        "P2-PNL-04", "Why was a candidate rejected",
        "rejection, deferral and expiry reasons with latency",
        "app/opip/decision/summary.py (file WAL)",
        ("reason", "disposition", "horizon class", "time window"),
        "an unattributable reason is withheld rather than invented",
        "rejection record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-033",
    ),
    (
        "P3-PNL-01", "What did O'Pip know",
        "decision identity, evaluation time, evidence cutoff and watermark",
        "canonical writer",
        ("candidate", "episode", "policy version", "time window"),
        "later corrections never modify the original explanation",
        "frozen snapshot",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-040",
    ),
    (
        "P3-PNL-02", "Why did it pass the gates",
        "F5 feasibility, F6 forecast and F7 ranking evidence",
        "app.opip.risk, app.opip.ml and app.opip.decision (not live)",
        ("policy version", "horizon class"),
        "deterministic fact, statistical estimate and AI opinion stay distinct",
        "stage evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-041",
    ),
    (
        "P3-PNL-03", "What were the alternatives",
        "planned economics, capital plan and the cash alternative",
        "app.opip.contracts.portfolio",
        ("policy version", "horizon class"),
        "a plan is not an outcome",
        "portfolio plan record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-042",
    ),
    (
        "P3-PNL-04", "What happened afterwards",
        "fills, protections, exits and outcome lineage",
        "app.opip.profit_intelligence.lineage",
        ("economic result", "execution result", "exit mechanism"),
        "a missing Committee opinion never implies a favorable review",
        "execution lineage",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-044",
    ),
    (
        "P4-PNL-01", "How much capital is committed",
        "cash, reserved capital, deployed basis and available capital per currency",
        "app.opip.cockpit.portfolio",
        ("quote currency", "account", "time window"),
        "USD and USDT are never summed or joined",
        "paper trade",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-050",
    ),
    (
        "P4-PNL-02", "What did the paper account earn after costs",
        "paper.realized_net_pnl, paper.execution_cost",
        "app.opip.cockpit.portfolio",
        (
            "quote currency", "policy version", "outcome status", "time window",
        ),
        "a missing cost is never a zero cost, and an open mark is never realized",
        "paper trade and fills",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-051",
    ),
    (
        "P4-PNL-03", "What positions exist and how were they filled",
        "open positions, fills, partial fills, cancellations and unfilled intents",
        "app.opip.cockpit.ledger",
        ("instrument", "lifecycle status", "time window"),
        "a no-fill is not a losing trade",
        "fills and order intents",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-052",
    ),
    (
        "P4-PNL-04", "How did equity and drawdown move",
        "paper.realized_equity_drawdown_pct",
        "app.opip.cockpit.portfolio",
        ("quote currency", "time window"),
        "no marked-equity drawdown without a defensible executable mark",
        "equity series",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-053",
    ),
    (
        "P4-PNL-05", "Did planned rotations complete",
        "remaining quantity, exit mechanism and residual exposure",
        "app.opip.cockpit.ledger",
        ("instrument", "lifecycle status", "exit mechanism"),
        "a planned rotation is not a completed rotation",
        "exit and residual evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-055",
    ),
    (
        "P5-PNL-01", "What opportunity population existed",
        "candidate population with exclusions and no-claims",
        "app.opip.decision.store (file WAL)",
        ("instrument", "status", "time window"),
        "the eligible and excluded cohorts are both retained",
        "candidate",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-060",
    ),
    (
        "P5-PNL-02", "What did O'Pip miss or find late",
        "missed, late and expired opportunities",
        "app.services.opportunity_accountability",
        ("status", "horizon class", "time window"),
        "no best-hindsight price is presented as realizable profit",
        "accountability record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-061",
    ),
    (
        "P5-PNL-03", "Is the forecast calibrated",
        "calibration, reliability and expected-versus-actual outcomes",
        "learning worker",
        ("model version", "horizon class", "time window"),
        "insufficient support is not a favorable conclusion",
        "frozen expectation",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-062",
    ),
    (
        "P5-PNL-04", "Does trading net survive costs by strategy",
        (
            "paper.realized_net_pnl, paper.net_expectancy, "
            "paper.in_position_mfe_pct, paper.in_position_mae_pct"
        ),
        "app.opip.cockpit.portfolio",
        ("strategy", "policy version", "regime", "horizon class"),
        "a censored or incomplete outcome is shown as incomplete",
        "strategy contribution and trade",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-063",
    ),
    (
        "P5-PNL-05", "Was a better alternative available",
        "paper.capture_efficiency",
        "canonical actual outcome plus versioned policy replay",
        ("policy version", "time window"),
        "a counterfactual is never realized profit, and an ordinal score is not a probability",
        "comparison record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-065",
    ),
    (
        "P6-PNL-01", "What has the Committee contributed",
        "trust stage, measured cases and exclusions",
        "app.opip.committee (advisory only)",
        ("stage", "role", "model", "provider", "time window"),
        "trust state comes from the existing calculation, never from this page",
        "assessment record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-070",
    ),
    (
        "P6-PNL-02", "Did the Committee help or harm",
        "four-way correctness and false interventions",
        "learning worker",
        ("role", "model", "policy version", "time window"),
        "an AI opinion is not a statistical forecast",
        "paired comparison",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-071",
    ),
    (
        "P6-PNL-03", "What is the incremental net after AI cost",
        "baseline net, matched-policy net, incremental net and attributable AI cost",
        "learning worker",
        ("policy version", "model", "provider", "time window"),
        "unknown cost blocks any favorable economics claim",
        "matched comparison",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-072",
    ),
    (
        "P6-PNL-04", "Where is the spend going",
        "tokens, retries, latency and billed or estimated cost by provider and model",
        "none (no canonical AI cost evidence exists)",
        ("model", "provider", "role", "time window"),
        "estimated and billed cost are never conflated",
        "invocation attempt",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-073",
    ),
    (
        "P7-PNL-01", "Did the frozen expectation hold",
        "frozen expected outcomes against matured actual outcomes",
        "learning worker",
        ("policy version", "horizon class", "time window"),
        "an expectation is never rewritten after the fact",
        "frozen expectation",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-080",
    ),
    (
        "P7-PNL-02", "What weaknesses are durable",
        "weakness findings with severity, category and recurrence",
        "learning worker",
        ("category", "severity", "time window"),
        "a hypothesis is not a validated improvement",
        "weakness finding",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-081",
    ),
    (
        "P7-PNL-03", "What did experiments prove",
        "experiments, champion and challenger evaluations with approval identity",
        "learning worker",
        ("status", "cohort", "time window"),
        "the page never exposes promotion or release controls",
        "experiment record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-082",
    ),
    (
        "P7-PNL-04", "Did an approved change actually help",
        "release identity, compatibility, adoption and observed before-and-after economics",
        "app.services.release_profiles",
        ("release", "policy version", "time window"),
        "an approved artifact is not an adopted artifact, and a learning change has no trading authority",
        "release evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-084",
    ),
    (
        "P8-PNL-01", "What failed and when",
        "incident inventory, severity, lifecycle and affected systems",
        "trading-host incident records",
        ("severity", "state", "system", "time window"),
        "an unproven incident claim is not asserted",
        "incident record",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-090",
    ),
    (
        "P8-PNL-02", "Which evidence is affected",
        "coverage gaps, freshness state and reconciliation failures",
        "app.opip.data_platform.reconcile",
        ("component", "state", "time window"),
        "a failed projection never makes stale evidence look current",
        "affected evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-091",
    ),
    (
        "P8-PNL-03", "Is anything untrusted or degraded",
        "release identity mismatch, protection degradation and incomplete outcome labels",
        "app.services.release_runtime_verifier",
        ("release", "system", "state"),
        "incompatible release identities are exposed, never silently joined",
        "release and protection evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-093",
    ),
    (
        "P8-PNL-04", "What recovery completed",
        "delivery failures, retries, backup and restore state",
        "app.services.telegram_delivery",
        ("component", "state", "time window"),
        "operational recovery is not authorization to resume trading",
        "recovery evidence",
        "PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-095",
    ),
)

#: The value disposition of each panel on this read plane, in the same shape as the
#: panel map's columns: ``(state, served_facts, unserved_reasons, evidence_gap)``.
#: Keeping this separate from the census-derived rows lets the census's statement and
#: this service's actual capability be reviewed independently of each other.
_PANEL_DISPOSITION_PART_A: tuple[tuple[str, tuple[Any, ...]], ...] = (
    (
        "P1-PNL-01",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P1-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P1-PNL-03",
        (
            FactState.SERVED,
            (
                "read_model.trust",
                "read_model.reconciliation_posture",
                "read_model.uncertainty_reasons",
                "read_model.attention",
            ),
            (),
            None,
        ),
    ),
    (
        "P1-PNL-04",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P2-PNL-01",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "QUALIFICATION_FUNNEL_PROGRESSION",
        ),
    ),
    (
        "P2-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "QUALIFICATION_FUNNEL_PROGRESSION",
        ),
    ),
    (
        "P2-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "QUALIFICATION_FUNNEL_PROGRESSION",
        ),
    ),
    (
        "P2-PNL-04",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "REJECTION_REASON_AND_GATE_ATTRIBUTION",
        ),
    ),
    (
        "P3-PNL-01",
        (
            FactState.SERVED_PARTIAL,
            ("ledger.row.identity", "ledger.row.temporal"),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P3-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P3-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P3-PNL-04",
        (
            FactState.SERVED_PARTIAL,
            ("ledger.row.fills", "ledger.row.exit"),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "FORWARD_OUTCOME_HORIZON_EVIDENCE",
        ),
    ),
    (
        "P4-PNL-01",
        (
            FactState.SERVED_PARTIAL,
            ("paper.population.reserved_capital", "paper.population.open_positions"),
            (REASON_NO_CANONICAL_CASH_LEDGER,),
            None,
        ),
    ),
    (
        "P4-PNL-02",
        (
            FactState.SERVED,
            ("paper.realized_net_pnl", "paper.execution_cost"),
            (),
            None,
        ),
    ),
    (
        "P4-PNL-03",
        (
            FactState.SERVED,
            (
                "paper.population.rows",
                "paper.population.settled",
                "paper.population.open_positions",
                "ledger.row.fills",
            ),
            (),
            None,
        ),
    ),
)

_PANEL_DISPOSITION_PART_B: tuple[tuple[str, tuple[Any, ...]], ...] = (
    (
        "P4-PNL-04",
        (
            FactState.SERVED_PARTIAL,
            ("paper.realized_equity_drawdown_pct",),
            (REASON_NO_MARK_EVIDENCE,),
            "UNREALIZED_MARK_TO_MARKET_PNL",
        ),
    ),
    (
        "P4-PNL-05",
        (
            FactState.SERVED_PARTIAL,
            ("ledger.row.residual", "ledger.row.exit"),
            (REASON_NO_MARK_EVIDENCE,),
            "UNREALIZED_MARK_TO_MARKET_PNL",
        ),
    ),
    (
        "P5-PNL-01",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "QUALIFICATION_FUNNEL_PROGRESSION",
        ),
    ),
    (
        "P5-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "MISSED_PROFITABLE_OPPORTUNITY",
        ),
    ),
    (
        "P5-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "FORWARD_OUTCOME_HORIZON_EVIDENCE",
        ),
    ),
    (
        "P5-PNL-04",
        (
            FactState.SERVED_PARTIAL,
            ("paper.realized_net_pnl", "paper.unresolved_count"),
            (REASON_WITHHELD_INTERVAL, REASON_UPSTREAM_NOT_ON_READ_PLANE),
            "FORWARD_OUTCOME_HORIZON_EVIDENCE",
        ),
    ),
    (
        "P5-PNL-05",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "MISSED_PROFITABLE_OPPORTUNITY",
        ),
    ),
    (
        "P6-PNL-01",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P6-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P6-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P6-PNL-04",
        (
            FactState.ABSENT,
            (),
            (REASON_NO_CANONICAL_AI_COST,),
            None,
        ),
    ),
)

_PANEL_DISPOSITION_PART_C: tuple[tuple[str, tuple[Any, ...]], ...] = (
    (
        "P7-PNL-01",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            "FORWARD_OUTCOME_HORIZON_EVIDENCE",
        ),
    ),
    (
        "P7-PNL-02",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P7-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P7-PNL-04",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P8-PNL-01",
        (
            FactState.ABSENT,
            (),
            (REASON_NO_CANONICAL_INCIDENT_STORE,),
            None,
        ),
    ),
    (
        "P8-PNL-02",
        (
            FactState.SERVED_PARTIAL,
            ("read_model.trust", "read_model.reconciliation_posture"),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P8-PNL-03",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
    (
        "P8-PNL-04",
        (
            FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
            (),
            (REASON_UPSTREAM_NOT_ON_READ_PLANE,),
            None,
        ),
    ),
)

_PANEL_DISPOSITIONS: Mapping[str, tuple[Any, ...]] = MappingProxyType(
    dict(
        _PANEL_DISPOSITION_PART_A
        + _PANEL_DISPOSITION_PART_B
        + _PANEL_DISPOSITION_PART_C
    )
)

PANEL_BINDINGS: tuple[PanelBinding, ...] = tuple(
    PanelBinding(*row, *_PANEL_DISPOSITIONS[row[0]]) for row in _PANEL_ROWS
)

PANEL_BY_ID: Mapping[str, PanelBinding] = MappingProxyType(
    {panel.panel_id: panel for panel in PANEL_BINDINGS}
)

#: Registered evidence-gap facts (``app.opip.profit_intelligence.semantics``). A panel
#: may only cite these: an invented gap identifier would assert missing canonical
#: evidence that the canonical vocabulary does not name.
REGISTERED_GAPS: frozenset[str] = frozenset(gap.fact for gap in EVIDENCE_GAPS)

#: Every evidence gap this service cites, in panel order.
CITED_GAPS: tuple[str, ...] = tuple(
    sorted({panel.gap_id for panel in PANEL_BINDINGS if panel.gap_id})
)


# --------------------------------------------------------------------------- #
# The declared surface, and what is deliberately not mounted
# --------------------------------------------------------------------------- #

#: The routes the F10 read surface declares. Declared here so the surface this service
#: intends to expose is reviewable in one place. This module itself still mounts nothing:
#: ``MOUNTED`` is ``False`` and ``MOUNTED_PATHS`` is empty, and it holds no HTTP surface
#: of its own. The declared routes are mounted by the read router
#: ``app/api/cockpit_semantic.py`` under the owner-approved increment
#: ``ATDD-R5-F10-dashboard-parity-implementation``, on the analytics plane, and the
#: analytics plane's mounted path set is pinned by
#: ``tests/test_bc4_cockpit_exposure_v1.py``. Widening that set is an owner-visible
#: decision, never a side effect of declaring a route.
DECLARED_ROUTES: tuple[str, ...] = (
    "GET /api/cockpit/semantic/pages",
    "GET /api/cockpit/semantic/pages/{page_id}",
    "GET /api/cockpit/semantic/panels/{panel_id}",
    "GET /api/cockpit/semantic/metrics/{metric_id}",
    "GET /api/cockpit/semantic/facts/{fact_id}",
)

#: The reachable surface this module contributes. Empty: a declaration that mounts
#: nothing cannot widen reachability, whatever it declares.
MOUNTED_PATHS: tuple[str, ...] = ()

#: The ledger row fields each declared row fact group publishes. Every name must be a
#: real ``ReconciledPaperTrade`` field, so the declaration cannot promise a fact the
#: projection does not carry. The union is deliberately a subset of the row rather
#: than the whole row: publishing every field would expose columns no page declared.
ROW_FACT_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "ledger.row.identity": (
            "paper_trade_id",
            "decision_context_id",
            "candidate_id",
            "episode_id",
            "cohort_id",
            "quote_currency",
            "policy_version",
            "policy_fingerprint",
        ),
        "ledger.row.temporal": (
            "evaluation_at",
            "entry_intent_at",
            "entry_attempt_at",
            "first_entry_fill_at",
            "last_exit_fill_at",
            "holding_seconds",
        ),
        "ledger.row.fills": (
            "requested_entry_quantity",
            "entry_quantity",
            "exited_quantity",
            "entry_price_vwap",
            "exit_price_vwap",
            "execution_result",
        ),
        "ledger.row.exit": (
            "economic_result",
            "exit_mechanism",
            "exit_mechanisms",
        ),
        "ledger.row.residual": (
            "remaining_quantity",
            "entry_notional",
            "entry_price_vwap",
            "net_pnl_definitive",
            "reserved_capital",
            "lifecycle_status",
        ),
    }
)


# --------------------------------------------------------------------------- #
# Metric bindings: where a registered metric's value comes from, or why it cannot
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MetricBinding:
    """One registered metric bound to this plane's producer, or to its refusal.

    ``value_source`` is a *location* in an existing projection, never a restated
    formula: the definition itself stays in ``paper-metrics-v1`` and the response
    carries only a reference to it.
    """

    metric_id: str
    outcome: FactState
    producer: str
    value_source: str | None
    population_fact: str | None
    requires_quote_currency: bool
    reasons: tuple[str, ...]
    gap_id: str | None = None
    withheld_when: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "outcome": self.outcome.value,
            "producer": self.producer,
            "value_source": self.value_source,
            "population_fact": self.population_fact,
            "requires_quote_currency": self.requires_quote_currency,
            "reasons": list(self.reasons),
            "gap_id": self.gap_id,
            "withheld_when": self.withheld_when,
        }


_LEDGER = "app.opip.cockpit.ledger"
_PORTFOLIO = "app.opip.cockpit.portfolio"

#: Every registered metric, bound to this plane. A metric whose evidence is not here
#: is declared unavailable with its reason rather than approximated, and the module's
#: tests assert this covers exactly the versioned registry's identifier set.
_METRIC_ROWS: tuple[MetricBinding, ...] = (
    MetricBinding(
        metric_id="paper.realized_net_pnl",
        outcome=FactState.SERVED,
        producer=f"{_PORTFOLIO}.CurrencyPortfolio.realized_net_pnl",
        value_source="portfolios[quote_currency].realized_net_pnl",
        population_fact="paper.population.settled",
        requires_quote_currency=True,
        reasons=(),
    ),
    MetricBinding(
        metric_id="paper.execution_cost",
        outcome=FactState.SERVED,
        producer=f"{_PORTFOLIO}.CurrencyPortfolio.execution_costs",
        value_source="portfolios[quote_currency].execution_costs",
        population_fact="paper.population.settled",
        requires_quote_currency=True,
        reasons=(),
    ),
    MetricBinding(
        metric_id="paper.realized_equity_drawdown_pct",
        outcome=FactState.SERVED_PARTIAL,
        producer=f"{_PORTFOLIO}.CurrencyPortfolio.drawdown",
        value_source="portfolios[quote_currency].drawdown.realized_max_drawdown_pct",
        population_fact="paper.population.settled_in_equity_series",
        requires_quote_currency=True,
        # Realized basis only: no canonical mark exists for an open position, so the
        # marked basis is unavailable and saying so is part of the answer.
        reasons=(REASON_NO_MARK_EVIDENCE,),
        withheld_when=REASON_NO_STARTING_EQUITY,
    ),
    MetricBinding(
        metric_id="paper.unresolved_count",
        outcome=FactState.SERVED,
        producer=f"{_LEDGER}.PaperLedger.entries",
        value_source="recounted rows whose terminal reconciliation is unresolved",
        population_fact="paper.population.rows",
        requires_quote_currency=False,
        reasons=(),
    ),
    MetricBinding(
        metric_id="paper.net_expectancy",
        outcome=FactState.SERVED_WITHHELD,
        producer=f"{_PORTFOLIO}.CurrencyPortfolio.expectancy_quote_currency",
        value_source="portfolios[quote_currency].expectancy_quote_currency",
        population_fact="paper.population.settled",
        requires_quote_currency=True,
        # Registered as requiring an interval and no interval estimator is registered,
        # so a point estimate would invent precision the registry forbids.
        reasons=(REASON_WITHHELD_INTERVAL,),
        withheld_when=REASON_WITHHELD_INTERVAL,
    ),
    MetricBinding(
        metric_id="paper.entry_latency",
        outcome=FactState.SERVED_WITHHELD,
        producer=f"{_LEDGER}.ReconciledPaperTrade.entry_latency_seconds",
        value_source="ledger row entry_latency_seconds (per-row interval)",
        population_fact="paper.population.settled",
        requires_quote_currency=False,
        reasons=(REASON_WITHHELD_INTERVAL,),
        withheld_when=REASON_WITHHELD_INTERVAL,
    ),
    MetricBinding(
        metric_id="paper.exit_latency",
        outcome=FactState.SERVED_WITHHELD,
        producer=f"{_LEDGER}.ReconciledPaperTrade.exit_latency_seconds",
        value_source="ledger row exit_latency_seconds (per-row interval)",
        population_fact="paper.population.settled",
        requires_quote_currency=False,
        reasons=(REASON_WITHHELD_INTERVAL,),
        withheld_when=REASON_WITHHELD_INTERVAL,
    ),
    MetricBinding(
        metric_id="paper.entry_price_drift_bps",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (decision reference price)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        reasons=(REASON_NO_DECISION_REFERENCE_PRICE,),
    ),
    MetricBinding(
        metric_id="paper.exit_price_drift_bps",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (exit reference price)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        reasons=(REASON_NO_EXIT_REFERENCE_PRICE,),
    ),
    MetricBinding(
        metric_id="paper.fill_ratio",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (canonical order intents)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        # The registry's population is order intents and the replica carries no intent
        # enumeration, so a ratio computed over trades would answer a different
        # question under the registry's name.
        reasons=(REASON_NO_ORDER_INTENT_ENUMERATION,),
    ),
    MetricBinding(
        metric_id="paper.capture_efficiency",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (versioned policy replay)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        reasons=(REASON_NO_POLICY_REPLAY,),
        gap_id="MISSED_PROFITABLE_OPPORTUNITY",
    ),
    MetricBinding(
        metric_id="paper.in_position_mfe_pct",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (retained excursion series)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        reasons=(REASON_UPSTREAM_NOT_ON_READ_PLANE,),
        gap_id="FORWARD_OUTCOME_HORIZON_EVIDENCE",
    ),
    MetricBinding(
        metric_id="paper.in_position_mae_pct",
        outcome=FactState.BLOCKED_BY_UPSTREAM_EVIDENCE,
        producer="none on this plane (retained excursion series)",
        value_source=None,
        population_fact=None,
        requires_quote_currency=False,
        reasons=(REASON_UPSTREAM_NOT_ON_READ_PLANE,),
        gap_id="FORWARD_OUTCOME_HORIZON_EVIDENCE",
    ),
)

METRIC_BINDINGS: Mapping[str, MetricBinding] = MappingProxyType(
    {binding.metric_id: binding for binding in _METRIC_ROWS}
)


# --------------------------------------------------------------------------- #
# Page requirements: the declared, checkable reason a page is not claimable
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PageRequirement:
    """What a page needs before this service may claim it.

    ``required_fact_families`` names census section 5 families, so the page predicate
    is a check against the declared fact-grain registry rather than a private second
    opinion about evidence. ``unmet_requirements`` names the same shortfall in the
    operator's words. While either is unmet the page serves no value at all, whatever
    any single panel can show.
    """

    page_id: str
    required_fact_families: tuple[str, ...]
    unmet_requirements: tuple[str, ...]


PAGE_REQUIREMENTS: Mapping[str, PageRequirement] = MappingProxyType(
    {
        requirement.page_id: requirement
        for requirement in (
            PageRequirement(
                page_id="P1_SYSTEM_PULSE",
                required_fact_families=("Incidents",),
                unmet_requirements=(
                    "scheduler and lock health from the trading host",
                    "consumed watermark and observation coverage",
                ),
            ),
            PageRequirement(
                page_id="P2_DISCOVERY_REGIME",
                required_fact_families=(
                    "Candidate lifecycle events",
                    "Detector evaluations",
                ),
                unmet_requirements=(
                    "continuous observation coverage",
                    "regime context",
                ),
            ),
            PageRequirement(
                page_id="P3_DECISION_TRACE",
                required_fact_families=("Decisions", "Outcomes"),
                unmet_requirements=(
                    "eligible evidence at each decision's own cutoff",
                    "explicit refusal reasons",
                ),
            ),
            PageRequirement(
                page_id="P4_PAPER_PORTFOLIO",
                required_fact_families=("Portfolio marks",),
                unmet_requirements=(
                    "the frozen SC-08 plan record as a plan revision",
                ),
            ),
            PageRequirement(
                page_id="P5_QUALITY_MISSED_OPPORTUNITY",
                required_fact_families=("Outcomes", "Attribution"),
                unmet_requirements=(
                    "missed-opportunity facts",
                    "calibration against matured expected outcomes",
                ),
            ),
            PageRequirement(
                page_id="P6_COMMITTEE_VALUE_AI_COST",
                required_fact_families=("Committee assessments", "AI usage"),
                unmet_requirements=(
                    "Committee trust analysis and its cost",
                    "calibrated resolution",
                ),
            ),
            PageRequirement(
                page_id="P7_LEARNING_RELEASES",
                required_fact_families=("Learning events", "Outcomes"),
                unmet_requirements=(
                    "frozen expectations against matured outcomes",
                    "release identity and receipts",
                ),
            ),
            PageRequirement(
                page_id="P8_INCIDENTS_INFRASTRUCTURE",
                required_fact_families=("Incidents", "Attribution"),
                unmet_requirements=(
                    "incident lifecycle and resolution state",
                    "delivery, retry and recovery state",
                ),
            ),
        )
    }
)


# --------------------------------------------------------------------------- #
# The typed filter contract
# --------------------------------------------------------------------------- #

#: The ledger row field each supported filter constrains, so a filter's effect is
#: declared rather than discovered. The window filters both constrain the declared
#: time basis; ``limit`` and ``cursor`` are pagination, not population, controls.
FILTER_ROW_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "quote_currency": "quote_currency",
        "instrument": "native_symbol",
        "lifecycle_status": "lifecycle_status",
        "economic_result": "economic_result",
        "execution_result": "execution_result",
        "exit_mechanism": "exit_mechanism",
        "policy_version": "policy_version",
        "window_start": TIME_BASIS,
        "window_end": TIME_BASIS,
        "limit": "PAGINATION",
        "cursor": "PAGINATION",
    }
)

#: Closed vocabularies a filter value must belong to. A value outside its declared
#: vocabulary is refused rather than coerced: coercing it would silently answer a
#: differently-scoped question than the caller asked.
FILTER_VOCABULARIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "quote_currency": QUOTE_CURRENCIES,
        "lifecycle_status": frozenset(item.value for item in LifecycleStatus),
        "economic_result": frozenset(item.value for item in EconomicResult),
        "execution_result": frozenset(item.value for item in ExecutionResult),
        "exit_mechanism": frozenset(item.value for item in ExitMechanism),
    }
)

#: Filters whose value is a free-form identity rather than a vocabulary member.
IDENTITY_FILTERS: frozenset[str] = frozenset({"instrument", "policy_version"})

#: The dimensions a request may widen with more than one value. Everything else - the paging
#: controls and the two window edges - is single-valued by construction: two window starts or
#: two limits do not describe one coherent request, and silently keeping the last one would
#: answer a differently-scoped question than the caller asked.
OR_CAPABLE_FILTERS: frozenset[str] = frozenset(
    {
        "quote_currency",
        "instrument",
        "lifecycle_status",
        "economic_result",
        "execution_result",
        "exit_mechanism",
        "policy_version",
    }
)


@dataclass(frozen=True)
class OrValues:
    """Two or more validated values for one dimension: ``OR`` within the dimension.

    A single value is never represented by this type. A one-member union collapses to the
    scalar it equals, so a request that names one value keeps the exact shape it had before
    this type existed and no consumer has to handle two spellings of the same scope.
    """

    dimension: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.values) < 2:
            raise ValueError("OrValues needs at least two values; use the scalar instead")
        if len(set(self.values)) != len(self.values):
            raise ValueError("OrValues repeats a value")

    def __contains__(self, item: object) -> bool:
        return item in self.values

    def to_json(self) -> list[str]:
        """The values, in the order the request stated them."""
        return list(self.values)


def _merge_dimension(
    dimension: str, existing: Any, incoming: Any
) -> Any:
    """Combine two statements about one dimension.

    Repeating a dimension widens it, so the union is the answer and no stated value is ever
    dropped. A repeated dimension that cannot be widened is refused rather than resolved to
    its last value.
    """
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    if existing == incoming:
        return existing
    if dimension not in OR_CAPABLE_FILTERS:
        raise InvalidFilterValue(
            f"{dimension} was given more than once and is not a dimension that ORs; "
            "state it exactly once"
        )
    values: list[str] = []
    for item in (existing, incoming):
        if isinstance(item, OrValues):
            values.extend(item.values)
        else:
            values.append(str(item))
    if len(set(values)) == 1:
        return values[0]
    return OrValues(dimension, tuple(dict.fromkeys(values)))


def _normalize_dimension(dimension: str, value: Any) -> Any:
    """Normalize one dimension's value, whether it was stated once or several times.

    A sequence states the same dimension more than once: every member is validated on its
    own, and one member collapses to the scalar it equals.
    """
    if isinstance(value, (list, tuple, set, frozenset)) and not isinstance(value, str):
        members = list(value)
        if not members:
            raise InvalidFilterValue(f"{dimension} was given an empty value list")
        # The repeat itself is the error for a dimension that cannot widen, whatever the
        # members happen to be: reporting a member's own problem would hide why it was refused.
        if len(members) > 1 and dimension not in OR_CAPABLE_FILTERS:
            raise InvalidFilterValue(
                f"{dimension} was given more than once and is not a dimension that ORs; "
                "state it exactly once"
            )
        normalized = [_normalize_filter_value(dimension, member) for member in members]
        ordered = tuple(dict.fromkeys(normalized))
        if len(ordered) == 1:
            return ordered[0]
        return OrValues(dimension, ordered)
    return _normalize_filter_value(dimension, value)


@dataclass(frozen=True)
class FilterRequest:
    """One validated, typed request. A filter is absent or carries a valid value.

    There is deliberately no "any" value: "no filter" and "filter for the empty
    string" are different queries, and only the first is expressible.
    """

    quote_currency: str | None = None
    instrument: str | None = None
    lifecycle_status: str | None = None
    economic_result: str | None = None
    execution_result: str | None = None
    exit_mechanism: str | None = None
    policy_version: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    limit: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    @property
    def has_window(self) -> bool:
        """Whether an explicit window was requested."""
        return self.window_start is not None or self.window_end is not None

    @classmethod
    def parse(cls, **requested: Any) -> FilterRequest:
        """Validate one request, refusing anything outside the declared contract."""
        resolved: dict[str, Any] = {}
        for name, value in requested.items():
            if value is None:
                continue
            if name in UNSUPPORTED_DECLARED_FILTERS:
                raise UnsupportedFilter(
                    f"{name}: {UNSUPPORTED_DECLARED_FILTERS[name]}"
                )
            # A canonical filter name is usable as itself; an alias maps the census's
            # own vocabulary onto a canonical name. Anything else is undeclared.
            canonical = (
                (name,)
                if name in SUPPORTED_FILTERS
                else DECLARED_FILTER_ALIASES.get(name)
            )
            if canonical is None:
                raise UnsupportedFilter(name)
            if len(canonical) != 1 or canonical[0] not in SUPPORTED_FILTERS:
                raise UnsupportedFilter(
                    f"{name} is a grouped filter; use "
                    + " and ".join(sorted(canonical))
                )
            target = canonical[0]
            normalized = _normalize_dimension(target, value)
            existing = resolved.get(target)
            # Two *different names* for one dimension that disagree is an ambiguous request and
            # is refused. The same canonical name stated again is a deliberate widen, which
            # ``_merge_dimension`` unions.
            if existing is not None and existing != normalized and name != target:
                raise InvalidFilterValue(f"conflicting values for {target}")
            resolved[target] = _merge_dimension(target, existing, normalized)
        window_start = resolved.get("window_start")
        window_end = resolved.get("window_end")
        if (
            window_start is not None
            and window_end is not None
            and window_start >= window_end
        ):
            raise InvalidFilterValue("window_start must precede window_end")
        return cls(**resolved)

    def effective(self) -> tuple[dict[str, Any], ...]:
        """The effective typed filters, in declared order.

        Never empty when a filter was applied, so a consumer is never shown a
        population under a scope the request did not state.
        """
        entries: list[dict[str, Any]] = []
        for name in SUPPORTED_FILTERS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, datetime):
                value = value.isoformat().replace("+00:00", "Z")
            elif isinstance(value, OrValues):
                # A list value *is* the OR: the dimension was stated more than once, and the
                # shape carries that fact without adding a second spelling of the same scope.
                value = value.to_json()
            entries.append(
                {
                    "dimension": name,
                    "value": value,
                    "constrains": FILTER_ROW_FIELDS[name],
                }
            )
        return tuple(entries)


def _normalize_filter_value(name: str, value: Any) -> Any:
    """Validate and normalize one filter value, or refuse it.

    ``InvalidFilterValue`` is raised for a malformed value; ``UnsupportedFilter`` is
    never raised here, because reaching this function means the filter is declared.
    """
    if name in FILTER_VOCABULARIES:
        text = value.strip().upper() if isinstance(value, str) else ""
        if not text or text not in FILTER_VOCABULARIES[name]:
            raise InvalidFilterValue(
                f"{name}={value!r} is not one of "
                + ", ".join(sorted(FILTER_VOCABULARIES[name]))
            )
        return text
    if name in IDENTITY_FILTERS:
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise InvalidFilterValue(f"{name} must be a non-empty identifier")
        return text
    if name in ("window_start", "window_end"):
        parsed = _parse_instant(value)
        if parsed is None:
            raise InvalidFilterValue(
                f"{name} must be a timezone-aware ISO-8601 instant"
            )
        return parsed
    if name == "limit":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidFilterValue("limit must be an integer")
        if value < 1 or value > MAX_PAGE_SIZE:
            raise InvalidFilterValue(
                f"limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        return value
    if name == "cursor":
        if not isinstance(value, str) or not value.strip():
            raise InvalidFilterValue("cursor must be a non-empty token")
        return value.strip()
    raise UnsupportedFilter(name)


def _parse_instant(value: Any) -> datetime | None:
    """Parse a timezone-aware instant, or ``None``.

    A naive instant is refused rather than assumed to be UTC: guessing the zone would
    place rows in a window they may not belong to, which is precisely the
    point-in-time error the census forbids.
    """
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str):
        try:
            candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return None
    return candidate


# --------------------------------------------------------------------------- #
# Readings: what a response must carry, per census section 6
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FactReading:
    """One answer about a fact, metric or population.

    The field set is the census's semantic response contract: identity and version;
    unit and currency; grain and a *reference* to the metric definition; eligible
    population and exclusions; numerator and denominator where they apply; the
    effective typed filters; observation and as-of timestamps; source and projection
    watermarks; environment and account; completeness, freshness and reconciliation
    states; the withholding reason; lineage and evidence references; and a pagination
    cursor where the population is paged.
    """

    kind: str
    subject_id: str
    state: FactState
    trust: Mapping[str, Any]
    value: Any = None
    unit: str | None = None
    quote_currency: str | None = None
    grain: str = SERVED_GRAIN
    population_fact: str | None = None
    population_scope: str | None = None
    eligible_population: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    exclusions: tuple[str, ...] = ()
    definition_reference: Mapping[str, Any] | None = None
    declared_filters: tuple[str, ...] = SUPPORTED_FILTERS
    effective_filters: tuple[Mapping[str, Any], ...] = ()
    observations: int | None = None
    first_observation: str | None = None
    last_observation: str | None = None
    as_of: str | None = None
    source: str = SOURCE
    identity: str = UNSPECIFIED_IDENTITY
    environment: str = UNSPECIFIED_IDENTITY
    account: str = UNSPECIFIED_IDENTITY
    semantic_version: str = SEMANTIC_VERSION
    service_version: str = SEMANTIC_SERVICE_VERSION
    ledger_projection_version: str = LEDGER_PROJECTION_VERSION
    portfolio_projection_version: str = PORTFOLIO_PROJECTION_VERSION
    time_basis: str = TIME_BASIS
    watermark: str | None = None
    reconciliation: Mapping[str, Any] | None = None
    completeness: str | None = None
    freshness: str | None = None
    uncertainty: str | None = None
    reasons: tuple[str, ...] = ()
    gap_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    read_model: Mapping[str, Any] | None = None
    cursor: str | None = None
    next_cursor: str | None = None
    authority: str = AUTHORITY_UNDECIDED
    parity_status: str = PARITY_STATUS_NOT_VERIFIED
    parity_verified_pages: int = PARITY_VERIFIED_PAGES
    thresholds: str = THRESHOLD_POLICY
    mounted: bool = MOUNTED

    @property
    def carries_value(self) -> bool:
        """Whether this state is allowed to publish a value at all."""
        return self.state in SERVED_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject_id": self.subject_id,
            "state": self.state.value,
            "value": self.value,
            "unit": self.unit,
            "quote_currency": self.quote_currency,
            "grain": self.grain,
            "population_fact": self.population_fact,
            "population_scope": self.population_scope,
            "eligible_population": self.eligible_population,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "exclusions": list(self.exclusions),
            "definition_reference": dict(self.definition_reference or {}),
            "declared_filters": list(self.declared_filters),
            "effective_filters": [dict(item) for item in self.effective_filters],
            "observations": self.observations,
            "first_observation": self.first_observation,
            "last_observation": self.last_observation,
            "as_of": self.as_of,
            "source": self.source,
            "identity": self.identity,
            "environment": self.environment,
            "account": self.account,
            "semantic_version": self.semantic_version,
            "service_version": self.service_version,
            "ledger_projection_version": self.ledger_projection_version,
            "portfolio_projection_version": self.portfolio_projection_version,
            "time_basis": self.time_basis,
            "watermark": self.watermark,
            "reconciliation": dict(self.reconciliation or {}),
            "completeness": self.completeness,
            "freshness": self.freshness,
            "uncertainty": self.uncertainty,
            "reasons": list(self.reasons),
            "gap_id": self.gap_id,
            "evidence_refs": list(self.evidence_refs),
            "rows": [dict(row) for row in self.rows],
            "read_model": dict(self.read_model or {}),
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "trust": dict(self.trust),
            "authority": self.authority,
            "parity_status": self.parity_status,
            "parity_verified_pages": self.parity_verified_pages,
            "thresholds": self.thresholds,
            "mounted": self.mounted,
        }


@dataclass(frozen=True)
class PageReading:
    """One answer about a page or a panel.

    A page is never *claimed* here. The reading reports the frozen descriptor, the
    declared requirement that is unmet and every panel's state; it publishes no page
    value, because a partially rendered page is exactly the failure the F10 parity
    predicate exists to prevent. A panel reading names the page it belongs to, so a
    consumer can see that the same fact stays available from :meth:`metric` and
    :meth:`population` while the page itself is withheld.
    """

    kind: str
    subject_id: str
    state: FactState
    trust: Mapping[str, Any]
    descriptor: Mapping[str, Any]
    reason: str | None = None
    required_fact_families: tuple[Mapping[str, Any], ...] = ()
    unmet_requirements: tuple[str, ...] = ()
    served_facts: tuple[str, ...] = ()
    unserved_reasons: tuple[str, ...] = ()
    unserved_gap_id: str | None = None
    fact_declaration: str | None = None
    producer: str | None = None
    declared_filters: tuple[str, ...] = ()
    effective_filters: tuple[Mapping[str, Any], ...] = ()
    trust_rule: str | None = None
    drill_target: str | None = None
    criterion: str | None = None
    page_id: str | None = None
    panel_states: tuple[Mapping[str, Any], ...] = ()
    as_of: str | None = None
    source: str = SOURCE
    identity: str = UNSPECIFIED_IDENTITY
    environment: str = UNSPECIFIED_IDENTITY
    account: str = UNSPECIFIED_IDENTITY
    semantic_version: str = SEMANTIC_VERSION
    service_version: str = SEMANTIC_SERVICE_VERSION
    ledger_projection_version: str = LEDGER_PROJECTION_VERSION
    portfolio_projection_version: str = PORTFOLIO_PROJECTION_VERSION
    watermark: str | None = None
    completeness: str | None = None
    freshness: str | None = None
    uncertainty: str | None = None
    reasons: tuple[str, ...] = ()
    authority: str = AUTHORITY_UNDECIDED
    parity_status: str = PARITY_STATUS_NOT_VERIFIED
    parity_verified_pages: int = PARITY_VERIFIED_PAGES
    thresholds: str = THRESHOLD_POLICY
    mounted: bool = MOUNTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject_id": self.subject_id,
            "state": self.state.value,
            "value": None,
            "page_value_claimed": False,
            "reason": self.reason,
            "descriptor": dict(self.descriptor),
            "required_fact_families": [
                dict(item) for item in self.required_fact_families
            ],
            "unmet_requirements": list(self.unmet_requirements),
            "served_facts": list(self.served_facts),
            "unserved_reasons": list(self.unserved_reasons),
            "unserved_gap_id": self.unserved_gap_id,
            "fact_declaration": self.fact_declaration,
            "producer": self.producer,
            "declared_filters": list(self.declared_filters),
            "effective_filters": [dict(item) for item in self.effective_filters],
            "trust_rule": self.trust_rule,
            "drill_target": self.drill_target,
            "criterion": self.criterion,
            "page_id": self.page_id,
            "panel_states": [dict(item) for item in self.panel_states],
            "as_of": self.as_of,
            "source": self.source,
            "identity": self.identity,
            "environment": self.environment,
            "account": self.account,
            "semantic_version": self.semantic_version,
            "service_version": self.service_version,
            "ledger_projection_version": self.ledger_projection_version,
            "portfolio_projection_version": self.portfolio_projection_version,
            "watermark": self.watermark,
            "completeness": self.completeness,
            "freshness": self.freshness,
            "uncertainty": self.uncertainty,
            "reasons": list(self.reasons),
            "authority": self.authority,
            "parity_status": self.parity_status,
            "parity_verified_pages": self.parity_verified_pages,
            "thresholds": self.thresholds,
            "mounted": self.mounted,
        }






# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #

#: The terminal reconciliation state that leaves a row's economics unresolved.
_UNRESOLVED = str(TerminalReconciliationState.UNRESOLVED_EVIDENCE.value)

#: Cursor token format identifier. A cursor minted by a different format is refused
#: rather than reinterpreted, so a client cannot be served a different window under
#: the illusion of resuming one.
_CURSOR_KIND = "cockpit-semantic-cursor-v1"


def _iso(value: datetime | None) -> str | None:
    """An instant as a UTC ISO-8601 string, or ``None``."""
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


class SemanticReadService:
    """Read-only semantic answers over an injected canonical projection.

    The service owns no store, schedules nothing and writes nothing. Every answer is
    derived from the injected :class:`PaperLedger` and the portfolio projection built
    from it, so it cannot become a second source of truth: if the projection is wrong,
    the answer is wrong in the same way and for the same reason.

    Authority: none. Nothing here can admit, reject, protect, exit, notify, promote,
    release or deploy, and no value it publishes may override a deterministic risk or
    execution gate.
    """

    def __init__(
        self,
        ledger: PaperLedger,
        *,
        as_of: datetime,
        starting_equity: float | None = None,
        identity: str = UNSPECIFIED_IDENTITY,
        recent_limit: int = 10,
    ) -> None:
        if not isinstance(ledger, PaperLedger):
            raise SemanticServiceError("ledger must be a PaperLedger")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            # A naive clock cannot order rows against tz-aware instants; refusing it
            # stops a window from meaning something other than it says.
            raise SemanticServiceError("as_of must be timezone-aware")
        if starting_equity is not None and starting_equity < 0.0:
            raise SemanticServiceError("starting_equity must not be negative")
        if not str(identity).strip():
            raise SemanticServiceError("identity must not be empty")
        if recent_limit < 1:
            raise SemanticServiceError("recent_limit must be at least 1")
        self.ledger = ledger
        self.as_of = as_of
        self.starting_equity = starting_equity
        self.identity = identity
        self.recent_limit = recent_limit
        self._overview: CockpitOverview | None = None
        self._portfolios: dict[str, CurrencyPortfolio] | None = None

    # -- derived projections -------------------------------------------------

    @property
    def overview(self) -> CockpitOverview:
        """The portfolio projection, built once from the injected ledger.

        Built lazily, so a reading that only needs the ledger never pays for the
        portfolio aggregation, and never built twice for one reading.
        """
        if self._overview is None:
            self._overview = build_overview(
                self.ledger,
                now=self.as_of,
                starting_equity=self.starting_equity,
                recent_limit=self.recent_limit,
            )
        return self._overview

    @property
    def portfolios(self) -> Mapping[str, CurrencyPortfolio]:
        """The projection's per-currency portfolios, keyed by quote currency."""
        if self._portfolios is None:
            self._portfolios = dict(self.overview.by_quote_currency())
        return MappingProxyType(self._portfolios)

    @property
    def watermark(self) -> str:
        """A deterministic marker for the data this reading was built from.

        The projections publish projection *versions*, not watermarks, so the marker
        is derived from the rows themselves and published with every response.
        Pagination cursors are validated against it, so a cursor cannot be replayed
        against a different ledger and silently page a different population.
        """
        closes = [
            row.last_exit_fill_at.isoformat()
            for row in self.ledger.entries
            if row.last_exit_fill_at is not None
        ]
        return (
            f"{LEDGER_PROJECTION_VERSION}|{len(self.ledger.entries)}|"
            f"{max(closes) if closes else 'NO_CLOSE_INSTANT'}"
        )

    @property
    def is_readable(self) -> bool:
        """Whether the injected ledger is healthy enough to answer from."""
        return self.ledger.trust.is_healthy

    # -- declared contract ---------------------------------------------------

    def contract(self) -> dict[str, Any]:
        """The declared service contract, as data.

        This is what the F10 read router mounts against and what an exposure review
        reads: version pins, the declared routes (which this module does not mount
        itself), the typed filter allowlist, the page requirements, every metric
        binding, and the preserved parity and threshold state. It reports declarations
        only; it is not a health check.
        """
        return {
            "service_version": SEMANTIC_SERVICE_VERSION,
            "semantic_version": SEMANTIC_VERSION,
            "ledger_projection_version": LEDGER_PROJECTION_VERSION,
            "portfolio_projection_version": PORTFOLIO_PROJECTION_VERSION,
            "time_basis": TIME_BASIS,
            "source": SOURCE,
            "identity": self.identity,
            "environment": UNSPECIFIED_IDENTITY,
            "account": UNSPECIFIED_IDENTITY,
            "identity_reason": REASON_IDENTITY_UNSPECIFIED,
            "mounted": MOUNTED,
            "declared_routes": list(DECLARED_ROUTES),
            "mounted_paths": list(MOUNTED_PATHS),
            "exposure_widening_requires": EXPOSURE_WIDENING_REQUIRES,
            "supported_filters": list(SUPPORTED_FILTERS),
            "unsupported_declared_filters": {
                name: UNSUPPORTED_DECLARED_FILTERS[name]
                for name in sorted(UNSUPPORTED_DECLARED_FILTERS)
            },
            "declared_filter_aliases": {
                name: list(targets)
                for name, targets in sorted(DECLARED_FILTER_ALIASES.items())
            },
            "filter_row_fields": dict(sorted(FILTER_ROW_FIELDS.items())),
            "filter_vocabularies": {
                name: sorted(values)
                for name, values in sorted(FILTER_VOCABULARIES.items())
            },
            "pages": list(PAGE_IDS),
            "panels": [panel.panel_id for panel in PANEL_BINDINGS],
            "page_requirements": {
                page_id: {
                    "required_fact_families": list(
                        PAGE_REQUIREMENTS[page_id].required_fact_families
                    ),
                    "unmet_requirements": list(
                        PAGE_REQUIREMENTS[page_id].unmet_requirements
                    ),
                }
                for page_id in PAGE_IDS
            },
            "row_fact_fields": {
                name: list(fields)
                for name, fields in sorted(ROW_FACT_FIELDS.items())
            },
            "population_facts": {
                name: POPULATION_FACTS[name] for name in sorted(POPULATION_FACTS)
            },
            "read_model_facts": {
                name: READ_MODEL_FACTS[name] for name in sorted(READ_MODEL_FACTS)
            },
            "metric_bindings": {
                metric_id: METRIC_BINDINGS[metric_id].to_dict()
                for metric_id in sorted(METRIC_BINDINGS)
            },
            "registered_gaps": sorted(REGISTERED_GAPS),
            "cited_gaps": list(CITED_GAPS),
            "refusal_kinds": list(REFUSAL_KINDS),
            "authority": AUTHORITY_UNDECIDED,
            "parity_status": PARITY_STATUS_NOT_VERIFIED,
            "parity_verified_pages": PARITY_VERIFIED_PAGES,
            "counts_and_pnl_reconciliation": COUNTS_AND_PNL_RECONCILIATION,
            "proof_required": PROOF_REQUIRED,
            "follower_increment": FOLLOWER_INCREMENT,
            "thresholds": THRESHOLD_POLICY,
            "ratified_thresholds": list(RATIFIED_THRESHOLDS),
        }

    # -- request plumbing ----------------------------------------------------

    def _envelope(self, request: FilterRequest) -> dict[str, Any]:
        """The parts every reading carries, whatever its subject."""
        trust = self.ledger.trust
        return {
            "trust": trust.to_dict(),
            "effective_filters": request.effective(),
            "as_of": _iso(self.as_of),
            "identity": self.identity,
            "watermark": self.watermark,
            "completeness": trust.completeness.value,
            "freshness": trust.freshness.value,
            "uncertainty": trust.uncertainty.value,
        }

    def _unreadable(
        self, kind: str, subject_id: str, request: FilterRequest
    ) -> FactReading:
        """The only answer an unreadable projection may give: a state, never a value."""
        return FactReading(
            kind=kind,
            subject_id=subject_id,
            state=FactState.UNAVAILABLE,
            reasons=(REASON_UNREADABLE,),
            **self._envelope(request),
        )

    @staticmethod
    def _passes(candidate: str, criterion: Any) -> bool:
        """Whether one row's value passes one dimension's criterion.

        A dimension stated once is equality; a dimension stated more than once is membership,
        which is ``OR`` within that dimension. The two are different questions and the caller
        always said which one it asked.
        """
        if isinstance(criterion, OrValues):
            return candidate in criterion.values
        return candidate == criterion

    @classmethod
    def _matches(cls, row: ReconciledPaperTrade, request: FilterRequest) -> bool:
        """Whether a row passes the row-level filters. ``AND`` across dimensions."""
        if request.quote_currency is not None:
            if not cls._passes(row.quote_currency or "", request.quote_currency):
                return False
        if request.instrument is not None:
            if not cls._passes(row.native_symbol or "", request.instrument):
                return False
        if request.lifecycle_status is not None:
            if not cls._passes(row.lifecycle_status.value, request.lifecycle_status):
                return False
        if request.economic_result is not None:
            if not cls._passes(row.economic_result.value, request.economic_result):
                return False
        if request.execution_result is not None:
            if not cls._passes(row.execution_result.value, request.execution_result):
                return False
        if request.exit_mechanism is not None:
            if not cls._passes(row.exit_mechanism.value, request.exit_mechanism):
                return False
        if request.policy_version is not None:
            if not cls._passes(row.policy_version or "", request.policy_version):
                return False
        return True

    def _select(
        self, request: FilterRequest
    ) -> tuple[tuple[ReconciledPaperTrade, ...], int]:
        """Rows the request selects, plus how many were excluded because they have no
        provable close instant.

        A windowed request can only place a row whose close instant exists. Such a row
        is excluded *and counted*, never silently dropped and never given an invented
        timestamp.
        """
        matched: list[ReconciledPaperTrade] = []
        unplaceable = 0
        for row in self.ledger.entries:
            if not self._matches(row, request):
                continue
            if request.has_window:
                close = row.last_exit_fill_at
                if close is None:
                    unplaceable += 1
                    continue
                if request.window_start is not None and close < request.window_start:
                    continue
                if request.window_end is not None and close >= request.window_end:
                    continue
            matched.append(row)
        matched.sort(
            key=lambda row: (
                row.last_exit_fill_at is None,
                row.last_exit_fill_at or self.as_of,
                row.paper_trade_id,
            )
        )
        return tuple(matched), unplaceable

    # -- counts and reconciliation -------------------------------------------

    @staticmethod
    def _group(
        rows: Iterable[ReconciledPaperTrade],
    ) -> dict[str, tuple[ReconciledPaperTrade, ...]]:
        """Rows grouped by quote currency. The only supported aggregation axis.

        USD and USDT are distinct portfolios, so grouping is the only way this service
        ever aggregates above one row: there is no cross-currency total anywhere in
        this module, and a row with no recorded currency groups under ``UNKNOWN``
        rather than being attributed to one that was never recorded.
        """
        grouped: dict[str, list[ReconciledPaperTrade]] = {}
        for row in rows:
            grouped.setdefault(str(row.quote_currency or "UNKNOWN"), []).append(row)
        return {key: tuple(group) for key, group in grouped.items()}

    @staticmethod
    def _recounts(rows: Iterable[ReconciledPaperTrade]) -> dict[str, int]:
        """The declared populations, recounted from the rows themselves."""
        members = tuple(rows)
        return {
            "rows": len(members),
            "settled": sum(1 for row in members if row.is_settled),
            "settled_in_equity_series": len(build_realized_equity_series(members)),
            "open_positions": sum(
                1
                for row in members
                if row.lifecycle_status is LifecycleStatus.OPEN
            ),
            "flat_unverified": sum(
                1
                for row in members
                if row.lifecycle_status is LifecycleStatus.UNRESOLVED
            ),
            "unresolved_evidence": sum(
                1 for row in members if row.terminal_reconciliation_state == _UNRESOLVED
            ),
        }

    def _reconciliation(
        self, request: FilterRequest
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Recount the row populations and compare them with the projection's counts.

        A projection count is re-derived from the rows it was built from, and any
        difference is published: a displayed aggregate that does not reconcile with
        its own drill-down population is a defect, not a rounding detail.

        The comparison is per currency, because USD and USDT are separate portfolios
        and are never summed. It is only meaningful when the request does not narrow
        the population past what the projection counts (a lifecycle or instrument
        filter legitimately makes a count differ), so in that case the comparison is
        reported as ``NOT_COMPARABLE`` instead of inventing a mismatch.
        """
        scoped = FilterRequest(quote_currency=request.quote_currency)
        rows, _unplaceable = self._select(scoped)
        comparable = (
            request.window_start is None
            and request.window_end is None
            and request.instrument is None
            and request.lifecycle_status is None
            and request.economic_result is None
            and request.execution_result is None
            and request.exit_mechanism is None
            and request.policy_version is None
        )
        recounted: dict[str, dict[str, int]] = {}
        declared: dict[str, dict[str, int]] = {}
        mismatches: list[str] = []
        for currency, group in sorted(self._group(rows).items()):
            counts = self._recounts(group)
            recounted[currency] = counts
            portfolio = self.portfolios.get(currency)
            if portfolio is None:
                continue
            projection = {
                "rows": portfolio.accountability.admitted,
                "settled": portfolio.settled_trades,
                "settled_in_equity_series": portfolio.settled_trades_in_equity_series,
                "open_positions": portfolio.open_positions,
                "flat_unverified": portfolio.accountability.flat_unverified,
            }
            declared[currency] = projection
            if not comparable:
                continue
            for key, value in sorted(projection.items()):
                if counts[key] != value:
                    mismatches.append(f"{currency}:{key}")
        if not comparable:
            status = "NOT_COMPARABLE"
        elif mismatches:
            status = "MISMATCH"
        else:
            status = "RECONCILED"
        report = {
            "status": status,
            "basis": (
                "projection counts re-derived from the reconciled ledger rows at "
                "one watermark, per quote currency"
            ),
            "watermark": self.watermark,
            "recounted": recounted,
            "projection": declared,
            "mismatches": tuple(mismatches),
            # Per-page counts-and-P&L reconciliation is the F10 cutover gate and is
            # not recorded by this increment; recounting is not that proof.
            "counts_and_pnl_reconciliation": COUNTS_AND_PNL_RECONCILIATION,
            "counts_and_pnl_proof_required": PROOF_REQUIRED,
        }
        reasons = (REASON_POPULATION_MISMATCH,) if mismatches else ()
        return report, reasons

    @staticmethod
    def _population_scope(request: FilterRequest, populations: Iterable[str]) -> str:
        """A one-line statement of the population a value was computed over."""
        scope = " AND ".join(populations)
        if request.quote_currency is not None:
            return f"{scope} within quote_currency={request.quote_currency}"
        return f"{scope}, grouped by quote_currency and never summed across it"

    # -- metrics -------------------------------------------------------------

    def metric(self, metric_id: str, **filters: Any) -> FactReading:
        """Answer one registered metric from this plane, or refuse to.

        The definition is never restated: the reading carries a *reference* to the
        versioned registry entry (identifier, definition version, declared eligible
        population, declared exclusions, unit, uncertainty requirement and source),
        and the value comes from a field the projection already publishes. A metric
        whose evidence is not here is returned with its state and reason, never with a
        substitute number.
        """
        binding = METRIC_BINDINGS.get(metric_id)
        if binding is None:
            raise UnsupportedMetric(metric_id)
        request = FilterRequest.parse(**filters)
        definition = metric_definition(metric_id)
        if binding.requires_quote_currency and request.quote_currency is None:
            raise InvalidFilterValue(
                f"{metric_id} requires quote_currency "
                f"({REASON_QUOTE_CURRENCY_REQUIRED})"
            )
        common: dict[str, Any] = {
            "kind": "METRIC",
            "subject_id": metric_id,
            "unit": definition.unit.value,
            "quote_currency": request.quote_currency,
            "population_fact": binding.population_fact,
            "eligible_population": definition.eligible_population,
            "exclusions": definition.exclusions,
            "gap_id": binding.gap_id,
            "definition_reference": {
                "registry": SEMANTIC_VERSION,
                "metric_id": definition.metric_id,
                "definition_version": definition.definition_version,
                "eligible_population": definition.eligible_population,
                "exclusions": list(definition.exclusions),
                "unit": definition.unit.value,
                "uncertainty_requirement": definition.uncertainty_requirement.value,
                "window": definition.window,
                "authoritative_source": definition.authoritative_source,
                "formula_restated_in_read_model": False,
            },
            **self._envelope(request),
        }
        if not self.is_readable:
            return FactReading(
                state=FactState.UNAVAILABLE,
                reasons=(REASON_UNREADABLE,),
                **common,
            )
        if binding.outcome not in _VALUE_ANSWERED_OUTCOMES:
            # A withheld, blocked, absent or unavailable answer is a declaration:
            # identical for every request and every ledger, so it depends on no data at
            # all. The qualified *served* form is not a declaration - ``SERVED_PARTIAL``
            # declares a location *and* a qualification - so it is answered from the
            # projection below with that qualification attached, because a served state
            # that carried no value would read as a measured nothing.
            return FactReading(
                state=binding.outcome,
                reasons=binding.reasons,
                **common,
            )
        rows, unplaceable = self._select(request)
        counts = self._recounts(rows)
        reconciliation, mismatch_reasons = self._reconciliation(request)
        state = binding.outcome
        reasons = list(binding.reasons) + list(mismatch_reasons)
        if mismatch_reasons:
            state = FactState.SERVED_PARTIAL
        value: Any
        observations: int
        population = binding.population_fact
        if metric_id == "paper.unresolved_count":
            # Recounted from the ledger rows. This binding declares no quote-currency
            # requirement, so it is answered without one: gating it on a portfolio
            # existing would withhold a value the ledger already carries, under a
            # requirement the contract does not declare.
            value = counts["unresolved_evidence"]
            observations = counts["rows"]
        else:
            portfolio = self.portfolios.get(request.quote_currency or "")
            if portfolio is None:
                # The currency is one this contract supports, but this projection shows
                # no such portfolio. Withheld rather than zero: a zero would assert a
                # measured result for a portfolio nothing shows.
                return FactReading(
                    state=FactState.SERVED_WITHHELD,
                    reasons=(REASON_QUOTE_CURRENCY_REQUIRED,),
                    population_scope=(
                        f"no {request.quote_currency} portfolio exists in this projection"
                    ),
                    **common,
                )
            if population == "paper.population.settled_in_equity_series":
                observations = counts["settled_in_equity_series"]
            elif population == "paper.population.settled":
                observations = counts["settled"]
            else:
                observations = counts["rows"]
            if metric_id == "paper.realized_net_pnl":
                value = portfolio.realized_net_pnl
            elif metric_id == "paper.execution_cost":
                value = portfolio.execution_costs
            elif metric_id == "paper.realized_equity_drawdown_pct":
                value = portfolio.drawdown.realized_max_drawdown_pct
                if value is None:
                    # The percentage form needs a declared starting equity. Without one
                    # only the absolute drawdown exists, and a percentage would rest on
                    # an invented base.
                    state = FactState.SERVED_WITHHELD
                    if binding.withheld_when:
                        reasons.append(binding.withheld_when)
            else:  # pragma: no cover - every served binding has a branch above
                raise SemanticServiceError(f"no value source declared for {metric_id}")
        exclusions = list(definition.exclusions)
        if unplaceable:
            exclusions.append(EXCLUSION_WITHOUT_CLOSE_INSTANT)
            common["exclusions"] = tuple(exclusions)
        return FactReading(
            state=state,
            value=value,
            reasons=tuple(reasons),
            observations=observations,
            first_observation=_iso(_bound_instant(rows, earliest=True)),
            last_observation=_iso(_bound_instant(rows, earliest=False)),
            population_scope=self._population_scope(
                request, (population or "recounted population",)
            ),
            reconciliation=reconciliation,
            **common,
        )

    # -- populations, row facts and read-model facts -------------------------

    def population(self, fact_id: str, **filters: Any) -> FactReading:
        """Answer one declared fact.

        Three kinds are declared: population counts and sums over the reconciled
        ledger, row fact groups (a declared field subset of the rows themselves), and
        read-model facts the projection already emits. A fact identifier this service
        does not declare is refused rather than approximated, and a declared fact that
        cannot be produced is returned with its state and reason - never as an empty
        collection, which would read as "nothing happened".
        """
        if fact_id not in SERVED_FACT_NAMES:
            raise UnsupportedFact(fact_id)
        request = FilterRequest.parse(**filters)
        if not self.is_readable:
            return self._unreadable(kind="FACT", subject_id=fact_id, request=request)
        if fact_id in POPULATION_FACTS:
            return self._population_fact(fact_id, request)
        if fact_id in ROW_FACT_GROUPS:
            return self._row_fact(fact_id, request)
        return self._read_model_fact(fact_id, request)

    def _population_fact(self, fact_id: str, request: FilterRequest) -> FactReading:
        """One declared population, recounted from the rows and reconciled.

        Counts are single populations, so no numerator or denominator applies; the
        declared population statement is carried in ``eligible_population`` and
        ``population_scope`` instead. A monetary population requires an explicit quote
        currency, because summing across currencies would join two separate portfolios.
        """
        rows, unplaceable = self._select(request)
        counts = self._recounts(rows)
        reconciliation, mismatch_reasons = self._reconciliation(request)
        reasons = list(mismatch_reasons)
        state = FactState.SERVED_PARTIAL if mismatch_reasons else FactState.SERVED
        exclusions: list[str] = []
        if unplaceable:
            exclusions.append(EXCLUSION_WITHOUT_CLOSE_INSTANT)
        unit: str
        if fact_id == "paper.population.reserved_capital":
            if request.quote_currency is None:
                raise InvalidFilterValue(
                    "paper.population.reserved_capital requires quote_currency "
                    f"({REASON_QUOTE_CURRENCY_REQUIRED})"
                )
            value: Any = sum(
                row.reserved_capital for row in rows if not row.net_pnl_definitive
            )
            unit = "QUOTE_CURRENCY"
        elif fact_id == "paper.population.settled":
            value, unit = counts["settled"], "COUNT"
        elif fact_id == "paper.population.settled_in_equity_series":
            value, unit = counts["settled_in_equity_series"], "COUNT"
        elif fact_id == "paper.population.open_positions":
            value, unit = counts["open_positions"], "COUNT"
        elif fact_id == "paper.population.flat_unverified":
            value, unit = counts["flat_unverified"], "COUNT"
        elif fact_id == "paper.population.admitted":
            value, unit = counts["rows"], "COUNT"
        else:  # pragma: no cover - every declared population has a branch above
            value, unit = counts["rows"], "COUNT"
        return FactReading(
            kind="FACT",
            subject_id=fact_id,
            state=state,
            value=value,
            unit=unit,
            quote_currency=request.quote_currency,
            population_fact=fact_id,
            eligible_population=POPULATION_FACTS[fact_id],
            population_scope=self._population_scope(
                request, (POPULATION_FACTS[fact_id],)
            ),
            exclusions=tuple(exclusions),
            observations=counts["rows"],
            first_observation=_iso(_bound_instant(rows, earliest=True)),
            last_observation=_iso(_bound_instant(rows, earliest=False)),
            reasons=tuple(reasons),
            reconciliation=reconciliation,
            **self._envelope(request),
        )

    def _row_fact(self, fact_id: str, request: FilterRequest) -> FactReading:
        """One declared row fact group, paged.

        Only the declared field subset of each row is published - never the whole row,
        which would expose fields no page declared. ``value`` is the number of rows in
        *this page* while ``observations`` is the size of the matching population, so a
        paged answer cannot be mistaken for a complete one; ``next_cursor`` resumes
        exactly where this page stopped, and a cursor minted against a different ledger
        is refused rather than reinterpreted.
        """
        rows, unplaceable = self._select(request)
        offset = 0
        if request.cursor is not None:
            offset = self._offset_from(request.cursor, subject=fact_id)
        page = rows[offset : offset + request.limit]
        next_cursor = None
        if offset + request.limit < len(rows):
            next_cursor = self._cursor_for(
                subject=fact_id, offset=offset + request.limit
            )
        exclusions = (EXCLUSION_WITHOUT_CLOSE_INSTANT,) if unplaceable else ()
        return FactReading(
            kind="FACT",
            subject_id=fact_id,
            state=FactState.SERVED,
            value=len(page),
            unit="ROWS",
            quote_currency=request.quote_currency,
            eligible_population=ROW_FACT_GROUPS[fact_id],
            population_scope=self._population_scope(
                request, (ROW_FACT_GROUPS[fact_id],)
            ),
            exclusions=exclusions,
            observations=len(rows),
            first_observation=_iso(_bound_instant(page, earliest=True)),
            last_observation=_iso(_bound_instant(page, earliest=False)),
            evidence_refs=tuple(ref for row in page for ref in row.event_ids),
            rows=self._row_payload(fact_id, page),
            cursor=request.cursor,
            next_cursor=next_cursor,
            reconciliation={
                "status": "NOT_APPLICABLE",
                "basis": "row facts are rows, not aggregates, so there is no count",
                "watermark": self.watermark,
            },
            **self._envelope(request),
        )

    @staticmethod
    def _row_payload(
        fact_id: str, rows: Iterable[ReconciledPaperTrade]
    ) -> tuple[dict[str, Any], ...]:
        """The declared field subset of each row, JSON-shaped and unabridged."""
        fields = ROW_FACT_FIELDS[fact_id]
        return tuple(
            {name: _json_value(getattr(row, name)) for name in fields} for row in rows
        )

    def _read_model_fact(self, fact_id: str, request: FilterRequest) -> FactReading:
        """One read-model fact the projection already emits.

        These facts carry no value this service computes: they publish a posture the
        projection itself produced (trust dimensions, reconciliation detail, attention
        items, uncertainty reasons, valuation posture), carried verbatim in
        ``read_model`` so no health score is recomputed or collapsed here.
        """
        return FactReading(
            kind="FACT",
            subject_id=fact_id,
            state=FactState.SERVED,
            unit="READ_MODEL",
            eligible_population=READ_MODEL_FACTS[fact_id],
            population_scope=READ_MODEL_FACTS[fact_id],
            read_model=self._read_model_payload(fact_id, request),
            reconciliation={
                "status": "NOT_APPLICABLE",
                "basis": "a read-model posture is reported, not reconciled",
                "watermark": self.watermark,
            },
            **self._envelope(request),
        )

    def _read_model_payload(
        self, fact_id: str, request: FilterRequest
    ) -> dict[str, Any]:
        """The payload of one read-model fact, taken from the projection as-is."""
        if fact_id == "read_model.trust":
            return {"trust": self.ledger.trust.to_dict()}
        if fact_id == "read_model.attention":
            return {"items": [item.to_dict() for item in self.overview.attention]}
        if fact_id == "read_model.uncertainty_reasons":
            return {
                "ledger_trust_reasons": list(self.ledger.trust.reasons),
                "by_quote_currency": {
                    currency: {
                        "uncertainty": portfolio.uncertainty.value,
                        "reasons": list(portfolio.uncertainty_reasons),
                    }
                    for currency, portfolio in sorted(self.portfolios.items())
                },
            }
        if fact_id == "read_model.valuation_posture":
            return {"valuation": self.overview.valuation.to_dict()}
        report, _ = self._reconciliation(request)
        return {"reconciliation": report}

    # -- pages and panels ----------------------------------------------------

    def pages(self) -> tuple[PageReading, ...]:
        """Every frozen page, in inventory order."""
        return tuple(self.page(page_id) for page_id in PAGE_IDS)

    def page(self, page_id: str) -> PageReading:
        """One frozen page: its descriptor, its requirement and its panels' states.

        This service claims no page. A page is claimable only when every fact family it
        requires is available on this plane and no declared requirement is unmet; while
        that is false the reading carries ``BLOCKED_BY_UPSTREAM_EVIDENCE`` with
        ``PAGE_REQUIRED_FACTS_NOT_PRODUCIBLE`` instead of a partly filled page. The
        facts themselves stay reachable through :meth:`metric` and
        :meth:`population`, and the panel states say which of them those are.
        """
        descriptor = PAGE_BY_ID.get(page_id)
        if descriptor is None:
            raise UnsupportedPage(page_id)
        requirement = PAGE_REQUIREMENTS[page_id]
        claimable = self._page_claimable(page_id)
        served = tuple(
            fact
            for panel_id in descriptor.panels
            for fact in PANEL_BY_ID[panel_id].served
        )
        return PageReading(
            kind="PAGE",
            subject_id=page_id,
            state=(
                FactState.SERVED
                if claimable
                else FactState.BLOCKED_BY_UPSTREAM_EVIDENCE
            ),
            reason=None if claimable else REASON_BLOCKED_PAGE,
            descriptor=descriptor.to_dict(),
            required_fact_families=tuple(
                {
                    "fact": name,
                    "required_grain": GRAIN_BY_FACT[name].required_grain,
                    "availability": GRAIN_BY_FACT[name].availability.value,
                }
                for name in requirement.required_fact_families
            ),
            unmet_requirements=requirement.unmet_requirements,
            served_facts=served,
            reasons=(descriptor.page_reason,) if descriptor.page_reason else (),
            panel_states=tuple(
                self._panel_state(panel_id) for panel_id in descriptor.panels
            ),
            **self._page_envelope(),
        )

    @staticmethod
    def _panel_state(panel_id: str) -> dict[str, Any]:
        """One panel's declaration and state, without any value."""
        panel = PANEL_BY_ID[panel_id]
        return {
            "panel_id": panel.panel_id,
            "state": panel.state.value,
            "served": list(panel.served),
            "unserved_reasons": list(panel.unserved_reasons),
            "gap_id": panel.gap_id,
            "criterion": panel.criterion,
        }

    @staticmethod
    def _page_claimable(page_id: str) -> bool:
        """Whether every fact family a page requires is available on this plane.

        The predicate is declared data, not a judgement: it reads the page's declared
        requirement and the census's fact-grain registry. A page becomes claimable by
        an increment that makes its evidence producible here, never by relaxing this
        check.
        """
        requirement = PAGE_REQUIREMENTS[page_id]
        if requirement.unmet_requirements:
            return False
        return all(
            GRAIN_BY_FACT[name].is_available
            for name in requirement.required_fact_families
        )

    def _page_envelope(self) -> dict[str, Any]:
        """The envelope for a page or panel reading.

        A page is a declaration rather than a query, so its effective filter set is
        explicitly empty: nothing was silently applied, and nothing was silently
        ignored either.
        """
        envelope = self._envelope(FilterRequest())
        envelope["effective_filters"] = ()
        return envelope

    def panel(self, panel_id: str) -> PageReading:
        """One panel: its declaration, its state and the page it belongs to.

        A panel's state is data-independent - it is the same before and after any
        ledger is read - because it reports what this plane can produce for that panel,
        not what one ledger happens to contain.
        """
        panel = PANEL_BY_ID.get(panel_id)
        if panel is None:
            raise UnsupportedFact(f"unknown panel: {panel_id}")
        return PageReading(
            kind="PANEL",
            subject_id=panel_id,
            state=panel.state,
            reason=panel.unserved_reasons[0] if panel.unserved_reasons else None,
            descriptor={
                "panel_id": panel.panel_id,
                "page_id": PANEL_PAGE.get(panel_id),
                "operator_question": panel.operator_question,
                "fact_declaration": panel.fact_declaration,
                "producer": panel.producer,
                "trust_rule": panel.trust_rule,
                "drill_target": panel.drill_target,
                "criterion": panel.criterion,
            },
            page_id=PANEL_PAGE.get(panel_id),
            fact_declaration=panel.fact_declaration,
            producer=panel.producer,
            declared_filters=panel.declared_filters,
            trust_rule=panel.trust_rule,
            drill_target=panel.drill_target,
            criterion=panel.criterion,
            served_facts=panel.served,
            unserved_reasons=panel.unserved_reasons,
            unserved_gap_id=panel.gap_id,
            reasons=panel.unserved_reasons,
            **self._page_envelope(),
        )

    # -- listings ------------------------------------------------------------

    def metrics(self) -> tuple[dict[str, Any], ...]:
        """Every registered metric's binding, in registry order."""
        return tuple(
            METRIC_BINDINGS[metric_id].to_dict()
            for metric_id in sorted(METRIC_BINDINGS)
        )

    def facts(self) -> tuple[dict[str, Any], ...]:
        """Every declared fact this service answers for, in stable order."""
        return tuple(
            {"fact_id": fact_id, "declaration": self._fact_declaration(fact_id)}
            for fact_id in sorted(SERVED_FACT_NAMES)
        )

    @staticmethod
    def _fact_declaration(fact_id: str) -> str:
        """The declared meaning of one fact, from its registry."""
        if fact_id in POPULATION_FACTS:
            return POPULATION_FACTS[fact_id]
        if fact_id in ROW_FACT_GROUPS:
            return ROW_FACT_GROUPS[fact_id]
        return READ_MODEL_FACTS[fact_id]

    # -- pagination helpers --------------------------------------------------

    def _cursor_for(self, *, subject: str, offset: int) -> str:
        """A resumable cursor over the rows this watermark and subject select."""
        payload = {
            "kind": _CURSOR_KIND,
            "subject": subject,
            "watermark": self.watermark,
            "offset": offset,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    def _offset_from(self, cursor: str, *, subject: str) -> int:
        """The offset a cursor resumes at, or a refusal.

        A cursor is refused when it was minted by another format, for another subject,
        or against another watermark: all three would resume somewhere other than where
        the client believes it is.
        """
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            )
            if not isinstance(payload, Mapping):
                raise ValueError("cursor payload is not an object")
            if payload.get("kind") != _CURSOR_KIND:
                raise ValueError("cursor format is not recognised")
            if payload.get("subject") != subject:
                raise ValueError("cursor belongs to another subject")
            if payload.get("watermark") != self.watermark:
                raise ValueError("cursor predates the current watermark")
            offset = payload.get("offset")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("cursor offset is not a valid position")
        except Exception as exc:  # noqa: BLE001 - any malformed cursor is one refusal
            raise InvalidFilterValue(
                f"cursor is not resumable here: {exc}"
            ) from exc
        return offset


# --------------------------------------------------------------------------- #
# Module-level helpers and the declared export surface
# --------------------------------------------------------------------------- #

#: Owning page of every declared panel. Derived from the frozen page registry, so a
#: panel cannot be declared without a page and a page cannot own an undeclared panel.
PANEL_PAGE: Mapping[str, str] = MappingProxyType(
    {
        panel_id: page.page_id
        for page in PAGE_REGISTRY
        for panel_id in page.panels
    }
)


def _bound_instant(
    rows: Iterable[ReconciledPaperTrade], *, earliest: bool
) -> datetime | None:
    """The earliest or latest provable close instant in a row set, or ``None``.

    Rows without a provable close instant simply do not contribute to the span; none
    of them is given a synthetic time to make the span look complete.
    """
    instants = [
        row.last_exit_fill_at for row in rows if row.last_exit_fill_at is not None
    ]
    if not instants:
        return None
    return min(instants) if earliest else max(instants)


def _json_value(value: Any) -> Any:
    """One row field as a JSON-shaped value, without reinterpreting it.

    Enums become their declared member names and instants become UTC ISO-8601 strings;
    nothing is rounded, rescaled or dropped.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


__all__ = [
    "AUTHORITY_UNDECIDED",
    "CITED_GAPS",
    "COUNTS_AND_PNL_RECONCILIATION",
    "DECLARED_FILTER_ALIASES",
    "DECLARED_ROUTES",
    "DEFAULT_PAGE_SIZE",
    "EXCLUSION_WITHOUT_CLOSE_INSTANT",
    "EXPOSURE_WIDENING_REQUIRES",
    "EvidenceState",
    "FACT_GRAINS",
    "FILTER_ROW_FIELDS",
    "FILTER_VOCABULARIES",
    "FOLLOWER_INCREMENT",
    "FactGrain",
    "FactReading",
    "FactState",
    "FilterRequest",
    "GRAIN_BY_FACT",
    "InvalidFilterValue",
    "LEDGER_PROJECTION_VERSION",
    "MAX_PAGE_SIZE",
    "METRIC_BINDINGS",
    "MOUNTED",
    "MOUNTED_PATHS",
    "MetricBinding",
    "PAGE_BY_ID",
    "PAGE_IDS",
    "PAGE_REGISTRY",
    "PAGE_REQUIREMENTS",
    "PANEL_BINDINGS",
    "PANEL_BY_ID",
    "PANEL_PAGE",
    "PARITY_STATUS_NOT_VERIFIED",
    "PARITY_VERIFIED_PAGES",
    "POPULATION_FACTS",
    "PROOF_REQUIRED",
    "PageDescriptor",
    "PageReading",
    "PageRequirement",
    "PanelBinding",
    "READ_MODEL_FACTS",
    "REGISTERED_GAPS",
    "ROW_FACT_FIELDS",
    "ROW_FACT_GROUPS",
    "RATIFIED_THRESHOLDS",
    "REASON_BLOCKED_PAGE",
    "REASON_IDENTITY_UNSPECIFIED",
    "REASON_NO_CANONICAL_AI_COST",
    "REASON_NO_CANONICAL_CASH_LEDGER",
    "REASON_NO_CANONICAL_INCIDENT_STORE",
    "REASON_NO_DECISION_REFERENCE_PRICE",
    "REASON_NO_EXIT_REFERENCE_PRICE",
    "REASON_NO_MARK_EVIDENCE",
    "REASON_NO_ORDER_INTENT_ENUMERATION",
    "REASON_NO_POLICY_REPLAY",
    "REASON_NO_POPULATION_AGGREGATION",
    "REASON_NO_PRODUCER_ON_READ_PLANE",
    "REASON_NO_STARTING_EQUITY",
    "REASON_POPULATION_MISMATCH",
    "REASON_QUOTE_CURRENCY_REQUIRED",
    "REASON_UNREADABLE",
    "REASON_UPSTREAM_NOT_ON_READ_PLANE",
    "REASON_WITHHELD_INTERVAL",
    "REFUSAL_KINDS",
    "SEMANTIC_SERVICE_VERSION",
    "SEMANTIC_VERSION",
    "SERVED_FACT_NAMES",
    "SERVED_GRAIN",
    "SERVED_STATES",
    "SOURCE",
    "SUPPORTED_FILTERS",
    "SemanticReadService",
    "SemanticServiceError",
    "THRESHOLD_POLICY",
    "TIME_BASIS",
    "UNDECLARED_FILTER_REASON",
    "UNSERVED_STATES",
    "UNSPECIFIED_IDENTITY",
    "UNSUPPORTED_DECLARED_FILTERS",
    "UnsupportedFact",
    "UnsupportedFilter",
    "UnsupportedMetric",
    "UnsupportedPage",
]

