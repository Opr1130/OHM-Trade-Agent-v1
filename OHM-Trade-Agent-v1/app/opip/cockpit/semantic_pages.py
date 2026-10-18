"""F10-B first slice: the page read model for System Pulse, Discovery and Regime, and
Decision Trace.

Increment: ``ATDD-R5-F10-dashboard-parity-implementation`` (mission step ``F10-B`` of the
owner-approved F10 mission). What each page, panel and fact *is* stays governed by
``docs/architecture/OPIP_F10_SOURCE_CENSUS.md`` and
``docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md``; what this module *is* is governed
by ``docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-implementation.md``.

What this module is
-------------------

One read-only presentation read model over the F10-A semantic read service
(``app.opip.cockpit.semantic``). It answers a page request by asking the service, for
every panel the frozen page registry declares, what that panel may publish - and by
publishing a value only where the service's own panel disposition allows one.

It adds no metric, no formula, no projection, no store, no scheduler and no writer. A
panel value is either a reading the service already produced for a fact family that panel
declares as served, or the lineage projection the profit intelligence plane already builds
(``app.opip.profit_intelligence.lineage``) over the same canonical rows.

KEY LAW - A VALUE IS ONLY EVER PUBLISHED WHERE THE SERVICE SAYS ONE MAY BE.
The value disposition of every panel is read from ``semantic.PANEL_BY_ID``, never
restated here. A panel the service declares blocked, absent or unavailable carries no
value from this module, whatever the caller asks for: this read model cannot upgrade a
panel's honesty, and a defect that would attach a value to a panel the service did not
declare served fails closed instead of rendering a number.

KEY LAW - MISSING EVIDENCE IS NEVER FAVORABLE EVIDENCE.
Every withheld panel and page section keeps the producer's own reason code and, where one
is registered, the registered evidence-gap identifier. An unreadable projection produces
an unavailable panel - never a zero, never an empty collection, never an omitted key. A
page whose facts are unavailable is still rendered: a page that silently disappeared
would be indistinguishable from a healthy one.

KEY LAW - THE PAGE IS NOT A CLAIM OF PARITY.
No page is claimed here. The eight-page parity predicate is unchanged and its verdict
stays ``NOT_VERIFIED``; every page carries the reconciliation it can prove and the
counts-and-P&L proof it cannot, distinctly.

Authority: none. Every method is a read over an injected service. Nothing here can admit,
reject, protect, exit, notify, promote, release or deploy, and no value this read model
publishes may override a deterministic risk or execution gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.cockpit import semantic
from app.opip.cockpit.semantic import (
    AUTHORITY_UNDECIDED,
    COUNTS_AND_PNL_RECONCILIATION,
    DECLARED_ROUTES,
    FactReading,
    FactState,
    FOLLOWER_INCREMENT,
    InvalidFilterValue,
    METRIC_BINDINGS,
    PAGE_BY_ID,
    PAGE_IDS,
    PANEL_BY_ID,
    PANEL_PAGE,
    PARITY_STATUS_NOT_VERIFIED,
    PARITY_VERIFIED_PAGES,
    PROOF_REQUIRED,
    REASON_NO_CANONICAL_CASH_LEDGER,
    REASON_NO_MARK_EVIDENCE,
    REASON_QUOTE_CURRENCY_REQUIRED,
    REASON_UNREADABLE,
    REASON_UPSTREAM_NOT_ON_READ_PLANE,
    ROW_FACT_FIELDS,
    SEMANTIC_SERVICE_VERSION,
    SERVED_STATES,
    SemanticReadService,
    THRESHOLD_POLICY,
    UNSERVED_STATES,
)
from app.opip.profit_intelligence.lineage import (
    LINEAGE_PROJECTION_VERSION,
    build_trade_lineage,
)

PAGE_READ_MODEL_VERSION = "cockpit-semantic-pages-v1"

#: The shape of a census criterion identifier. A withheld section names the criterion it
#: belongs to, and an unshaped identifier is a defect rather than a section.
_CRITERION_ID = re.compile(r"AC-\d{3}")

#: The three pages of the F10-B first slice, in inventory order. A page outside this
#: tuple is refused rather than half-rendered.
SLICE_PAGES: tuple[str, ...] = (
    "P1_SYSTEM_PULSE",
    "P2_DISCOVERY_REGIME",
    "P3_DECISION_TRACE",
)

#: The pages this read model serves once the succeeding increment adds the two whose facts
#: the analytics plane can reach: the F10-B slice plus Paper Portfolio and Quality and
#: Missed Opportunity. P6, P7 and P8 stay out because their canonically required facts are
#: not on this plane, and a page whose every panel is unproducible is an absence rather
#: than a page. This tuple is the only place the served set is widened; the default
#: construction still serves the slice exactly as delivered.
DELIVERED_PAGES: tuple[str, ...] = SLICE_PAGES + (
    "P4_PAPER_PORTFOLIO",
    "P5_QUALITY_MISSED_OPPORTUNITY",
)

#: The row identity a decision-grain join keys on. It is a declared field of the ledger
#: row itself, so the join introduces no new identifier.
JOIN_KEY = "paper_trade_id"

#: The row field that makes a row a *decision*. Without it a row is a reconciled trade
#: whose decision context was not retained: it is excluded with a reason rather than
#: presented as a decision with missing context.
DECISION_KEY = "decision_context_id"

EXCLUSION_ROW_NOT_A_DECISION = "ROW_WITHOUT_DECISION_CONTEXT"
EXCLUSION_JOIN_KEY_MISSING = "ROW_NOT_IDENTIFIABLE_FOR_DECISION_JOIN"
EXCLUSION_LINEAGE_ROW_ABSENT = "LINEAGE_ROW_NOT_PRESENT_IN_LEDGER"
EXCLUSION_ALIGNMENT_UNPROVEN = "ROW_GROUPS_NOT_ALIGNED_FOR_DECISION_JOIN"

#: The one declared row fact group that carries each row's own identifiers, including the
#: join key. Every other group publishes only the measured fields its panel declared, so
#: the identity group is the only place a group payload can be keyed from.
KEY_FACT = "ledger.row.identity"

#: Refusal reasons this read model adds on top of the service's own. They name what this
#: plane could not prove; they never stand in for a value.
REASON_ALIGNMENT_UNPROVEN = "ROW_GROUP_ALIGNMENT_UNPROVEN"
REASON_NO_IDENTIFIABLE_DECISION = "NO_DECISION_IDENTIFIABLE_IN_SELECTED_POPULATION"

#: What the index's served/withheld panel counts are. The index materializes no panel
#: value, so its counts are the service's dispositions and say so.
INDEX_COUNTS_BASIS = "SERVICE_PANEL_DISPOSITION"

#: The mounting posture of this read model's surface, declared rather than inferred.
#: The F10-A service module still mounts nothing of its own; the F10-B increment mounts
#: the read routes that read *through* it, on the analytics plane, and that mounting is an
#: owner-visible exposure change this read model reports instead of leaving a stale
#: "unmounted" claim in every page envelope. The reachable path set itself is not asserted
#: here - it is pinned by the increment's exposure tests, which read the mounted app.
MOUNTED_SURFACE: Mapping[str, Any] = MappingProxyType(
    {
        "service_module_mounts": False,
        "mounting_module": "app.api.cockpit_semantic",
        "mounted_on": "analytics plane (app.api.cockpit_service)",
        "mounted_routes": list(DECLARED_ROUTES),
        "authorized_by_increment": "ATDD-R5-F10-dashboard-parity-implementation",
    }
)

#: Sections a page declares but this plane cannot produce, with the census requirement
#: identifier each belongs to and the producer's own reason. Kept declared: a section
#: that vanishes is indistinguishable from a healthy one.
WITHHELD_SECTIONS: Mapping[str, tuple[tuple[str, str, str], ...]] = MappingProxyType(
    {
        "P1_SYSTEM_PULSE": (
            ("release identity and profile alignment", "AC-021", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("writer health and queue pressure", "AC-022", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("open incidents and protection state", "AC-024", REASON_UPSTREAM_NOT_ON_READ_PLANE),
        ),
        "P2_DISCOVERY_REGIME": (
            ("discovery-to-decision latency", "AC-034", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("regime, phase and horizon classification", "AC-035", REASON_UPSTREAM_NOT_ON_READ_PLANE),
        ),
        "P3_DECISION_TRACE": (
            ("feasibility, forecast and ranking evidence", "AC-041", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("frozen plan record and cash alternative", "AC-042", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("Committee review and role disagreement", "AC-043", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("expected-versus-actual attribution", "AC-045", REASON_UPSTREAM_NOT_ON_READ_PLANE),
        ),
        "P4_PAPER_PORTFOLIO": (
            ("cash and available capital per currency", "AC-050", REASON_NO_CANONICAL_CASH_LEDGER),
            ("marked equity and marked drawdown beyond the realized basis", "AC-053", REASON_NO_MARK_EVIDENCE),
            ("unrealized mark-to-market P&L on an open position", "AC-054", REASON_NO_MARK_EVIDENCE),
            ("valuation of the residual exposure a rotation has not yet realized", "AC-055", REASON_NO_MARK_EVIDENCE),
            ("capital-occupancy, capital-hours and efficiency diagnostics", "AC-056", REASON_UPSTREAM_NOT_ON_READ_PLANE),
        ),
        "P5_QUALITY_MISSED_OPPORTUNITY": (
            ("candidate population with its exclusions and no-claims", "AC-060", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("missed, late and expired opportunities", "AC-061", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("calibration and matured expected-versus-actual outcomes", "AC-062", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("trading net after cost by strategy with complete cost evidence", "AC-063", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("MFE and MAE retained path evidence", "AC-064", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("versioned feasible counterfactual comparison", "AC-065", REASON_UPSTREAM_NOT_ON_READ_PLANE),
            ("pending, censored and incomplete-outcome labels", "AC-066", REASON_UPSTREAM_NOT_ON_READ_PLANE),
        ),
    }
)

#: Row fact groups each decision-grain panel is assembled from, in declared order. Every
#: name must be a declared served fact of that panel; the module fails closed at import
#: if one is not, so a value can never be assembled from an undeclared fact.
_DECISION_FACT_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "P3-PNL-01": ("ledger.row.identity", "ledger.row.temporal"),
        "P3-PNL-04": ("ledger.row.fills", "ledger.row.exit"),
    }
)


class PageReadModelError(Exception):
    """A refusal by the page read model, never a substituted value."""


class UnsupportedSlicePage(PageReadModelError):
    """The requested page or panel is outside the F10-B first slice."""


class PanelDispositionConflict(PageReadModelError):
    """A value would have been published for a panel the service did not declare served."""


@dataclass(frozen=True)
class PanelReading:
    """One panel: its census declaration, its disposition and the value it may carry.

    ``value`` is present only when the service's panel disposition allows one, and it is
    always a reading of a fact family that panel declares as served. A withheld panel
    carries no value at all and states the reason instead.
    """

    panel_id: str
    page_id: str
    state: FactState
    declaration: Mapping[str, Any]
    value: Mapping[str, Any] | None = None
    reasons: tuple[str, ...] = ()
    gap_id: str | None = None
    facts: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    serving: bool = False

    @property
    def carries_value(self) -> bool:
        """Whether this reading publishes a value, on the same rule as the service."""
        return self.state in SERVED_STATES and self.value is not None

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.declaration)
        payload.update(
            {
                "page_id": self.page_id,
                "state": self.state.value,
                "carries_value": self.carries_value,
                "serving": self.serving,
                "value": dict(self.value) if self.value is not None else None,
                "panel_reasons": list(self.reasons),
                "evidence_gap": self.gap_id,
                "facts": list(self.facts),
                "exclusions": list(self.exclusions),
                "evidence_refs": list(self.evidence_refs),
            }
        )
        return payload


@dataclass(frozen=True)
class PageReading:
    """One page: frozen declaration, panels, withheld sections, annexes, reconciliation."""

    page_id: str
    descriptor: Mapping[str, Any]
    state: FactState
    reasons: tuple[str, ...]
    claimable: bool
    required_fact_families: tuple[Mapping[str, Any], ...]
    unmet_requirements: tuple[str, ...]
    panels: tuple[PanelReading, ...]
    withheld_sections: tuple[Mapping[str, str], ...]
    annexes: Mapping[str, Any] = field(default_factory=dict)
    reconciliation: Mapping[str, Any] = field(default_factory=dict)
    envelope: Mapping[str, Any] = field(default_factory=dict)

    @property
    def served_panels(self) -> int:
        """How many of this page's panels published a value."""
        return sum(1 for panel in self.panels if panel.carries_value)

    @property
    def serving_panels(self) -> int:
        """How many of this page's panels the service declares served on this plane.

        Distinct from :attr:`served_panels`, which counts values this response actually
        published: an index that materializes no values must still be able to say how many
        panels the service declares served, and both counts are always reported together so
        a disposition count can never be mistaken for a value count.
        """
        return sum(1 for panel in self.panels if panel.serving)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "PAGE",
            "page_id": self.page_id,
            "state": self.state.value,
            "claimed": self.claimable,
            "reasons": list(self.reasons),
            "descriptor": dict(self.descriptor),
            "required_fact_families": [dict(item) for item in self.required_fact_families],
            "unmet_requirements": list(self.unmet_requirements),
            "panels": [panel.to_dict() for panel in self.panels],
            "panel_counts": {
                "total": len(self.panels),
                "service_serving": self.serving_panels,
                "served": self.served_panels,
                "withheld": len(self.panels) - self.serving_panels,
            },
            "withheld_sections": [dict(item) for item in self.withheld_sections],
            "annexes": dict(self.annexes),
            "reconciliation": dict(self.reconciliation),
            "read_model_version": PAGE_READ_MODEL_VERSION,
            **dict(self.envelope),
        }


@dataclass(frozen=True)
class PageIndexEntry:
    """One page of the slice, as the index route publishes it."""

    page_id: str
    adopted_name: str
    state: FactState
    panel_count: int
    served_panels: int
    withheld_panels: int
    counts_basis: str = INDEX_COUNTS_BASIS

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "adopted_name": self.adopted_name,
            "state": self.state.value,
            "panel_count": self.panel_count,
            "served_panels": self.served_panels,
            "withheld_panels": self.withheld_panels,
            "counts_basis": self.counts_basis,
        }


def _json_ready(value: Any) -> Any:
    """One value as a JSON-shaped value, without reinterpreting it."""
    if isinstance(value, FactState):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


class PageReadModel:
    """The F10 page read model over an injected semantic read service.

    The service is the single fact authority this read model reads through: every panel
    value is a reading the service produced, every panel disposition is the service's
    declaration, and every withheld section keeps the service's own reason. This module's
    own decisions are presentation decisions only: which pages it serves, how a page's
    panels and annexes are grouped, and which declared read entry point a fact is read
    through.

    The served page set is a constructor argument so the increment that widens it does not
    silently narrow or widen the delivered slice's behaviour: the default is the F10-B
    three-page slice, and :data:`DELIVERED_PAGES` is what the read router serves.
    """

    def __init__(
        self,
        service: SemanticReadService,
        *,
        pages: tuple[str, ...] = SLICE_PAGES,
    ) -> None:
        if not isinstance(service, SemanticReadService):
            raise PageReadModelError("service must be a SemanticReadService")
        unknown = tuple(page for page in pages if page not in PAGE_BY_ID)
        if unknown:
            raise PageReadModelError(f"declared pages are not in the registry: {unknown}")
        if len(set(pages)) != len(pages):
            raise PageReadModelError(f"declared pages name a page twice: {pages}")
        self.service = service
        self.pages = tuple(pages)

    # -- index ---------------------------------------------------------------

    def index(self) -> tuple[PageIndexEntry, ...]:
        """Every served page, in inventory order, without any panel value.

        Because this route materializes no value, it reports the service's panel
        dispositions and labels that basis, so its counts can never be read as a count of
        published values.
        """
        entries: list[PageIndexEntry] = []
        for page_id in self.pages:
            reading = self.page(page_id, with_values=False)
            entries.append(
                PageIndexEntry(
                    page_id=reading.page_id,
                    adopted_name=str(reading.descriptor.get("adopted_name", "")),
                    state=reading.state,
                    panel_count=len(reading.panels),
                    served_panels=reading.serving_panels,
                    withheld_panels=len(reading.panels) - reading.serving_panels,
                )
            )
        return tuple(entries)

    # -- pages ---------------------------------------------------------------

    def page(
        self,
        page_id: str,
        *,
        with_values: bool = True,
        limit: int | None = None,
        **filters: Any,
    ) -> PageReading:
        """One served page: declaration, panels, annexes and reconciliation.

        The page state is the service's own page state and is never recomputed here: a
        page is claimable only when the service says every required fact family is
        producible on this plane. Panels are published in the frozen registry's order, and
        a panel the service declares withheld keeps that state even though other panels on
        the same page carry values - the same page, not the same verdict.
        """
        if page_id not in self.pages:
            raise UnsupportedSlicePage(page_id)
        reading = self.service.page(page_id)
        descriptor = reading.descriptor
        panels = tuple(
            self.panel(panel_id, with_value=with_values, limit=limit, **filters)
            for panel_id in descriptor["panels"]
        )
        return PageReading(
            page_id=page_id,
            descriptor=descriptor,
            state=reading.state,
            reasons=tuple(reading.reasons),
            claimable=reading.state in SERVED_STATES,
            required_fact_families=tuple(reading.required_fact_families),
            unmet_requirements=tuple(reading.unmet_requirements),
            panels=panels,
            withheld_sections=tuple(
                {"section": section, "requirement_id": requirement, "reason": reason}
                for section, requirement, reason in WITHHELD_SECTIONS.get(page_id, ())
            ),
            annexes=self._annexes(page_id, with_values=with_values, **filters),
            reconciliation=self._reconciliation(page_id, **filters),
            envelope=self._page_envelope(reading),
        )

    # -- panels --------------------------------------------------------------

    def panel(
        self,
        panel_id: str,
        *,
        with_value: bool = True,
        limit: int | None = None,
        **filters: Any,
    ) -> PanelReading:
        """One slice panel, with a value only where the service's disposition allows one.

        A panel outside the slice is refused. A panel inside it whose disposition is
        blocked, absent or unavailable is returned withheld with its declared reason and
        gap: the request cannot widen a panel's disposition. Where the disposition is
        served, the value is assembled from the fact families that panel declares as
        served, and from nothing else.
        """
        binding = PANEL_BY_ID.get(panel_id)
        if binding is None:
            raise UnsupportedSlicePage(panel_id)
        page_id = PANEL_PAGE[panel_id]
        if page_id not in self.pages:
            raise UnsupportedSlicePage(panel_id)
        declaration = binding.to_dict()
        if not binding.carries_value:
            if binding.state not in UNSERVED_STATES:
                raise PanelDispositionConflict(
                    f"{panel_id} declares {binding.state.value} with no served fact"
                )
            return PanelReading(
                panel_id=panel_id,
                page_id=page_id,
                state=binding.state,
                declaration=declaration,
                reasons=tuple(binding.unserved_reasons),
                gap_id=binding.gap_id,
            )
        if not with_value:
            return PanelReading(
                panel_id=panel_id,
                page_id=page_id,
                state=binding.state,
                declaration=declaration,
                gap_id=binding.gap_id,
                facts=tuple(binding.served),
                serving=True,
            )
        value, state, reasons, exclusions, refs = self._panel_value(
            panel_id, tuple(binding.served), limit=limit, **filters
        )
        if value is not None and state not in SERVED_STATES:
            raise PanelDispositionConflict(
                f"{panel_id} would publish a value in the non-serving state {state.value}"
            )
        if value is not None and state is FactState.SERVED and binding.state is FactState.SERVED_PARTIAL:
            # A qualified declaration stays qualified in the answer: the qualification is
            # part of what the panel means, so a served form cannot silently drop it.
            state = FactState.SERVED_PARTIAL
        return PanelReading(
            panel_id=panel_id,
            page_id=page_id,
            state=state,
            declaration=declaration,
            value=value,
            # The declaration's own qualification reasons are kept beside the reading's, so
            # a served form of a qualified panel cannot drop the qualification that says
            # what the panel does not prove.
            reasons=tuple(dict.fromkeys((*binding.unserved_reasons, *reasons))),
            gap_id=binding.gap_id,
            facts=tuple(binding.served),
            exclusions=exclusions,
            evidence_refs=refs,
            serving=True,
        )

    # -- value assembly ------------------------------------------------------

    def _panel_value(
        self,
        panel_id: str,
        facts: tuple[str, ...],
        *,
        limit: int | None = None,
        **filters: Any,
    ) -> tuple[
        Mapping[str, Any] | None,
        FactState,
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        """Assemble one served panel's value from its declared served facts.

        A panel whose readings are not all available is unavailable with the readings' own
        reasons and no value: a partially assembled panel would present the readable half
        as the whole panel. A decision-grain panel additionally reads the identity group,
        which carries the row identifiers every declared group is aligned by; where the
        panel declares that group it is one of its facts, and where it does not it is read
        privately to key the join and is never published as a panel fact.
        """
        read_facts = (
            tuple(dict.fromkeys((KEY_FACT, *facts)))
            if panel_id in _DECISION_FACT_GROUPS
            else facts
        )
        try:
            readings = self._readings(read_facts, limit=limit, **filters)
        except InvalidFilterValue as exc:
            # A fact family that is scoped to one quote currency refuses without one, and
            # the refusal is this panel's answer rather than the whole page's: assuming a
            # currency would answer a different question, and failing the page would hide
            # the panels that are readable. Any other invalid filter value is a genuine
            # client error and is still raised.
            if REASON_QUOTE_CURRENCY_REQUIRED not in str(exc):
                raise
            return None, FactState.UNAVAILABLE, (REASON_QUOTE_CURRENCY_REQUIRED,), (), ()
        unavailable = tuple(
            reading for reading in readings.values() if reading.state not in SERVED_STATES
        )
        if unavailable:
            reasons = [
                reason
                for reading in unavailable
                for reason in (reading.reasons or (REASON_UNREADABLE,))
            ]
            return None, FactState.UNAVAILABLE, tuple(dict.fromkeys(reasons)), (), ()
        if panel_id in _DECISION_FACT_GROUPS:
            entries, exclusions, refs, reasons = self._decision_entries(panel_id, readings)
            if not entries:
                # No decision row this plane can prove. That is a refusal with a reason,
                # never an empty population carrying a favourable zero: the exclusions
                # record how much selected evidence the refusal withheld.
                if not exclusions:
                    reasons = (REASON_NO_IDENTIFIABLE_DECISION,)
                return (
                    None,
                    FactState.UNAVAILABLE,
                    reasons or (REASON_UNREADABLE,),
                    exclusions,
                    refs,
                )
            value = self._decision_value(panel_id, entries, readings)
            return value, FactState.SERVED_PARTIAL, (), exclusions, refs
        value = {fact: self._reading_payload(reading) for fact, reading in readings.items()}
        reasons = tuple(
            dict.fromkeys(
                reason for reading in readings.values() for reason in reading.reasons
            )
        )
        state = FactState.SERVED_PARTIAL if reasons else FactState.SERVED
        return (
            value,
            state,
            reasons,
            tuple(
                dict.fromkeys(
                    item for reading in readings.values() for item in reading.exclusions
                )
            ),
            tuple(
                dict.fromkeys(
                    ref for reading in readings.values() for ref in reading.evidence_refs
                )
            ),
        )

    def _readings(
        self, facts: tuple[str, ...], *, limit: int | None = None, **filters: Any
    ) -> dict[str, FactReading]:
        """One fact reading per declared fact family, from the service.

        Each fact is read through the entry point the service declares for it: a fact the
        versioned metric registry binds is read through the service's metric read, and
        every other declared fact through its population read. The read model keeps no
        registry of its own and restates no formula, population, unit or exclusion - it
        only chooses which declared door to knock on.
        """
        requested = dict(filters)
        if limit is not None and facts and all(
            fact.startswith("ledger.row.") for fact in facts
        ):
            requested["limit"] = limit
        return {fact: self._read_fact(fact, requested) for fact in facts}

    def _read_fact(self, fact: str, requested: Mapping[str, Any]) -> FactReading:
        """One declared fact, read through the service entry point it belongs to.

        A registered metric is never read through the population read and a declared
        population is never read through the metric read: a fact that the declared entry
        point refuses stays refused rather than being answered by the other one under the
        registry's name.
        """
        if fact in METRIC_BINDINGS:
            return self.service.metric(fact, **requested)
        return self.service.population(fact, **requested)

    @staticmethod
    def _reading_payload(reading: FactReading) -> Mapping[str, Any]:
        """One reading as a JSON-shaped value, without duplicating its envelope."""
        return {
            "state": reading.state.value,
            "value": _json_ready(reading.value),
            "unit": reading.unit,
            "observations": reading.observations,
            "first_observation": reading.first_observation,
            "last_observation": reading.last_observation,
            "exclusions": list(reading.exclusions),
            "reasons": list(reading.reasons),
            "read_model": _json_ready(reading.read_model),
            "watermark": reading.watermark,
        }

    def _decision_entries(
        self, panel_id: str, readings: Mapping[str, FactReading]
    ) -> tuple[
        tuple[Mapping[str, Any], ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        """Join this panel's row groups into one entry per identified decision row.

        The join key is the row's own ``paper_trade_id`` and the decision key is the row's
        own ``decision_context_id``, both read from the group that carries row identity.
        The other declared groups publish only the measured fields their panel declared and
        carry no identifier, so they are aligned positionally with the identity group - and
        that alignment is *verified* before a single row is joined: every group must be
        served and must report the same selected population size. An alignment this read
        model cannot prove yields no entry plus a reason, never a joined row.
        """
        groups = _DECISION_FACT_GROUPS[panel_id]
        declared = KEY_FACT in groups
        # A panel that does not declare the identity group may publish only the measured
        # fields of the groups it does declare: the oracle's identifiers key the entry, they
        # are never added to it.
        publishable: frozenset[str] | None = (
            None
            if declared
            else frozenset(
                field
                for fact in groups
                if fact != KEY_FACT
                for field in ROW_FACT_FIELDS[fact]
            )
        )
        refs: list[str] = []
        for fact in groups:
            refs.extend(readings[fact].evidence_refs)
        unique_refs = tuple(dict.fromkeys(refs))
        if any(reading.state not in SERVED_STATES for reading in readings.values()):
            return (), (), unique_refs, (REASON_UNREADABLE,)
        sizes = {
            (reading.observations, len(reading.rows))
            for reading in readings.values()
        }
        if len(sizes) != 1:
            return (
                (),
                (EXCLUSION_ALIGNMENT_UNPROVEN,),
                unique_refs,
                (REASON_ALIGNMENT_UNPROVEN,),
            )
        identity_rows = tuple(readings[KEY_FACT].rows)
        others = {
            fact: tuple(readings[fact].rows) for fact in groups if fact != KEY_FACT
        }
        exclusions: list[str] = []
        entries: list[Mapping[str, Any]] = []
        for position, identity in enumerate(identity_rows):
            key = identity.get(JOIN_KEY)
            if not isinstance(key, str) or not key:
                exclusions.append(EXCLUSION_JOIN_KEY_MISSING)
                continue
            if not identity.get(DECISION_KEY):
                exclusions.append(EXCLUSION_ROW_NOT_A_DECISION)
                continue
            entry: dict[str, Any] = {JOIN_KEY: key, "facts": {}}
            if declared:
                entry["facts"][KEY_FACT] = identity
            for row in (identity, *(rows[position] for rows in others.values())):
                for name, value in row.items():
                    if name == JOIN_KEY or name in entry:
                        continue
                    if publishable is not None and name not in publishable:
                        continue
                    entry[name] = value
            for fact, rows in others.items():
                entry["facts"][fact] = rows[position]
            entries.append(entry)
        return (
            tuple(entries),
            tuple(dict.fromkeys(exclusions)),
            unique_refs,
            (),
        )

    def _decision_value(
        self,
        panel_id: str,
        entries: tuple[Mapping[str, Any], ...],
        readings: Mapping[str, FactReading],
    ) -> Mapping[str, Any]:
        """The value of one decision-grain panel, with its lineage where it has one."""
        watermark = next(
            (reading.watermark for reading in readings.values() if reading.watermark),
            None,
        )
        declared = _DECISION_FACT_GROUPS[panel_id]
        payload: dict[str, Any] = {
            "grain": "ONE ENTRY PER IDENTIFIED ROW, KEYED BY DECISION CONTEXT",
            "count": len(entries),
            "entries": [dict(entry) for entry in entries],
            "watermark": watermark,
            "join_key": JOIN_KEY,
            "decision_key": DECISION_KEY,
            "alignment": {
                "oracle": KEY_FACT,
                "declared_fact_groups": list(declared),
                "oracle_declared_by_panel": KEY_FACT in declared,
                "basis": (
                    "groups are aligned by the selected population size and ordered by the "
                    "service's own deterministic row order, keyed from the identity group"
                ),
            },
        }
        if panel_id == "P3-PNL-04":
            stages, lineages, lineage_exclusions = self._lineage(entries)
            payload["lineage"] = {"stages": list(stages), "entries": lineages}
            payload["lineage_exclusions"] = list(lineage_exclusions)
            payload["lineage_projection_version"] = LINEAGE_PROJECTION_VERSION
        return payload

    def _lineage(
        self, entries: tuple[Mapping[str, Any], ...]
    ) -> tuple[tuple[str, ...], dict[str, Any], tuple[str, ...]]:
        """The existing execution-lineage projection, per selected decision.

        The projection is not restated here: ``build_trade_lineage`` is the profit
        intelligence plane's own per-trade lineage builder, applied to the row each entry
        was assembled from. A selected decision whose row is not in the ledger is excluded
        with a reason instead of being given a lineage-shaped placeholder.
        """
        rows = {row.paper_trade_id: row for row in self.service.ledger.entries}
        stages: tuple[str, ...] = ()
        built: dict[str, Any] = {}
        exclusions: list[str] = []
        for entry in entries:
            key = str(entry.get(JOIN_KEY))
            row = rows.get(key)
            if row is None:
                exclusions.append(EXCLUSION_LINEAGE_ROW_ABSENT)
                continue
            payload = build_trade_lineage(row).to_dict()
            if not stages:
                stages = tuple(
                    str(stage.get("stage")) for stage in payload.get("stages", ())
                )
            built[key] = payload
        return stages, built, tuple(dict.fromkeys(exclusions))

    # -- annexes and page envelope ------------------------------------------

    def _annexes(
        self, page_id: str, *, with_values: bool = True, **filters: Any
    ) -> Mapping[str, Any]:
        """Facts a page may show beside its panels, under their own labels.

        Discovery and Regime declares no panel it can serve, while the reconciled rows do
        carry candidate, episode and cohort identities at row grain. That evidence is
        published here as row-grain evidence and explicitly *not* as a funnel, progression
        or disposition: a row-grain identity is not a lifecycle stage.
        """
        if page_id != "P2_DISCOVERY_REGIME" or not with_values:
            return {}
        requested = dict(filters)
        requested["limit"] = requested.get("limit") or 50
        reading = self.service.population("ledger.row.identity", **requested)
        return {
            "row_grain_evidence": {
                "presented_as": "ROW_GRAIN_IDENTITIES_NOT_A_FUNNEL",
                "not_a_funnel_reason": (
                    "the ledger carries candidate, episode and cohort identities on "
                    "reconciled rows; it carries no screening, detector or disposition "
                    "population, so a progression cannot be derived from it"
                ),
                "state": reading.state.value,
                "carries_value": reading.state in SERVED_STATES,
                "value": _json_ready(reading.value),
                "unit": reading.unit,
                "observations": reading.observations,
                "rows": _json_ready(reading.rows),
                "exclusions": list(reading.exclusions),
                "reasons": list(reading.reasons),
                "next_cursor": reading.next_cursor,
            }
        }

    def _reconciliation(self, page_id: str, **filters: Any) -> Mapping[str, Any]:
        """This page's reconciliation, and the proof it is not.

        The recount the service performs is published per currency at one watermark, and
        is explicitly distinguished from the cutover gate's per-page counts-and-P&L
        reconciliation, which needs a declared page authority and fixtures and is not
        recorded here.
        """
        reading = self.service.population("read_model.reconciliation_posture", **filters)
        if reading.state not in SERVED_STATES or reading.read_model is None:
            return {
                "page_id": page_id,
                "status": reading.state.value,
                "reasons": list(reading.reasons) or [REASON_UNREADABLE],
                "counts_and_pnl_reconciliation": COUNTS_AND_PNL_RECONCILIATION,
                "counts_and_pnl_proof_required": PROOF_REQUIRED,
            }
        report = dict(reading.read_model.get("reconciliation", {}))
        report["page_id"] = page_id
        return report

    @staticmethod
    def _page_envelope(reading: Any) -> Mapping[str, Any]:
        """The version and boundary envelope every page response carries."""
        return {
            "watermark": reading.watermark,
            "as_of": reading.as_of,
            "source": reading.source,
            "identity": reading.identity,
            "environment": reading.environment,
            "account": reading.account,
            "service_version": SEMANTIC_SERVICE_VERSION,
            "semantic_version": reading.semantic_version,
            "ledger_projection_version": reading.ledger_projection_version,
            "portfolio_projection_version": reading.portfolio_projection_version,
            "authority": AUTHORITY_UNDECIDED,
            "parity_status": PARITY_STATUS_NOT_VERIFIED,
            "parity_verified_pages": PARITY_VERIFIED_PAGES,
            "follower_increment": FOLLOWER_INCREMENT,
            "thresholds": THRESHOLD_POLICY,
            "counts_and_pnl_reconciliation": COUNTS_AND_PNL_RECONCILIATION,
            "counts_and_pnl_proof_required": PROOF_REQUIRED,
            "service_module_mounted": False,
            "read_surface": {
                **MOUNTED_SURFACE,
                "mounted_routes": list(MOUNTED_SURFACE["mounted_routes"]),
            },
            "declared_routes": list(DECLARED_ROUTES),
            "slice_pages": list(SLICE_PAGES),
            "all_pages": list(PAGE_IDS),
        }


def _validate_declarations() -> None:
    """Fail closed at import on a declaration this read model would misread.

    Every served page set must name registry pages; every fact group a decision panel is
    assembled from must be a fact that panel declares as served, so a value can never be
    assembled from an undeclared fact; and every withheld section must name a registry page,
    a criterion identifier and a reason the service itself registers, so a section cannot be
    withdrawn under an invented reason or lost to a typo.
    """
    for pages, label in ((SLICE_PAGES, "slice"), (DELIVERED_PAGES, "delivered")):
        for page_id in pages:
            if page_id not in PAGE_BY_ID:
                raise PageReadModelError(
                    f"{label} page {page_id} is not in the page registry"
                )
        if len(set(pages)) != len(pages):
            raise PageReadModelError(f"{label} pages name a page twice: {pages}")
    if not set(SLICE_PAGES) <= set(DELIVERED_PAGES):
        raise PageReadModelError("the delivered page set narrowed the slice")
    registered_reasons = frozenset(
        value
        for name, value in vars(semantic).items()
        if name.startswith("REASON_") and isinstance(value, str)
    )
    for page_id, sections in WITHHELD_SECTIONS.items():
        if page_id not in PAGE_BY_ID:
            raise PageReadModelError(f"withheld section for unknown page {page_id}")
        for section, criterion, reason in sections:
            if not section.strip():
                raise PageReadModelError(f"{page_id} declares an empty withheld section")
            if not _CRITERION_ID.fullmatch(criterion):
                raise PageReadModelError(
                    f"{page_id} cites an unshaped criterion: {criterion}"
                )
            if reason not in registered_reasons:
                raise PageReadModelError(
                    f"{page_id} withholds {section!r} for an unregistered reason: {reason}"
                )
    for panel_id, groups in _DECISION_FACT_GROUPS.items():
        binding = PANEL_BY_ID.get(panel_id)
        if binding is None:
            raise PageReadModelError(f"decision panel {panel_id} is not declared")
        undeclared = tuple(group for group in groups if group not in binding.served)
        if undeclared:
            raise PageReadModelError(
                f"{panel_id} would be assembled from undeclared facts: {undeclared}"
            )
        if KEY_FACT not in groups:
            if not groups:
                raise PageReadModelError(f"{panel_id} declares no fact group to join")
            unmeasurable = tuple(
                group for group in groups if not ROW_FACT_FIELDS.get(group)
            )
            if unmeasurable:
                raise PageReadModelError(
                    f"{panel_id} joins groups with no declared fields: {unmeasurable}"
                )
        if len(set(groups)) != len(groups):
            raise PageReadModelError(f"{panel_id} names a fact group twice: {groups}")


_validate_declarations()

__all__ = [
    "DELIVERED_PAGES",
    "EXCLUSION_ALIGNMENT_UNPROVEN",
    "EXCLUSION_JOIN_KEY_MISSING",
    "EXCLUSION_LINEAGE_ROW_ABSENT",
    "EXCLUSION_ROW_NOT_A_DECISION",
    "KEY_FACT",
    "INDEX_COUNTS_BASIS",
    "MOUNTED_SURFACE",
    "PAGE_READ_MODEL_VERSION",
    "PanelDispositionConflict",
    "PanelReading",
    "PageIndexEntry",
    "PageReadModel",
    "PageReadModelError",
    "PageReading",
    "REASON_ALIGNMENT_UNPROVEN",
    "REASON_NO_IDENTIFIABLE_DECISION",
    "SLICE_PAGES",
    "UnsupportedSlicePage",
    "WITHHELD_SECTIONS",
]
