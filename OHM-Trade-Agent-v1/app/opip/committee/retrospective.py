"""Phase-B retrospective corpus infrastructure.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Phase B compares roles and models on a **frozen retrospective corpus**. Three
things must be true for that comparison to mean anything, and all three are
enforced here rather than described:

* **The corpus is frozen.** A manifest carries a content-derived identity over
  every case, so a corpus cannot be edited after results are seen without the
  change becoming visible. A comparison names the corpus identity it ran on.

* **Breadth is checked, not hoped for.** A corpus made only of easy wins produces
  a flattering comparison, so the required classes (baseline accepts, baseline
  rejects, wins, losses, late extensions, no-fills, missing evidence, grade B/C
  evidence, incidents) must all be present before a corpus may be used.

* **Outcomes are hidden from the prompt.** The point of a retrospective fixture
  is that the model does not see the answer. A case whose model-bound payload
  contains its own resolved outcome is refused, because the comparison would then
  measure recall of the fixture rather than analysis.

**Retrospective results are not portfolio evidence.** They are research evidence
about role and model behaviour on a case-control corpus. A portfolio profit claim
requires matched economics from prospective, matured outcomes. The explicit
disposition returned here states that, so a downstream reader cannot mistake a
retrospective win rate for realised profitability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from app.opip.committee.contracts import CaseType, EvaluationPhase
from app.opip.committee.roles import CommitteeRole
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

RETROSPECTIVE_CASE_SCHEMA_VERSION = 1
RETROSPECTIVE_CORPUS_SCHEMA_VERSION = 1

CORPUS_CASE_IDENTITY_DOMAIN = "COMMITTEE-CORPUS-CASE"
CORPUS_IDENTITY_DOMAIN = "COMMITTEE-CORPUS"

#: Breadth the architecture requires of a retrospective diagnostic corpus.
MINIMUM_CORPUS_CASES = 120

#: The disposition every retrospective result carries. A reader is told, in the
#: record itself, what this evidence may and may not be used for.
RETROSPECTIVE_EVIDENCE_DISPOSITION = "RESEARCH_ONLY_NOT_PORTFOLIO_EVIDENCE"


class CaseClass(str, Enum):
    """The diagnostic classes a corpus must span to be informative."""

    BASELINE_ACCEPT = "BASELINE_ACCEPT"
    BASELINE_REJECT = "BASELINE_REJECT"
    WIN = "WIN"
    LOSS = "LOSS"
    LATE_EXTENSION = "LATE_EXTENSION"
    NO_FILL = "NO_FILL"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    GRADE_B_EVIDENCE = "GRADE_B_EVIDENCE"
    GRADE_C_EVIDENCE = "GRADE_C_EVIDENCE"
    INCIDENT = "INCIDENT"


class CorpusError(ValueError):
    """A corpus contract was violated."""


@dataclass(frozen=True)
class RetrospectiveCorpusCase:
    """One frozen retrospective fixture.

    ``model_bound_payload`` is what a model would receive. It must not contain the
    resolved outcome, which is the whole reason the fixture is retrospective.
    """

    case_id: str
    case_class: CaseClass
    case_type: CaseType
    evidence_cutoff_at: datetime
    evidence_refs: tuple[str, ...]
    model_bound_payload: Mapping[str, Any]
    resolved_positive: bool
    schema_version: int = RETROSPECTIVE_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RETROSPECTIVE_CASE_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported RetrospectiveCorpusCase schema_version")
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id is required")
        if not isinstance(self.case_class, CaseClass):
            raise ValueError("invalid case_class")
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case_type")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("evidence_refs must be a non-empty tuple")
        if type(self.resolved_positive) is not bool:
            raise ValueError("resolved_positive must be a boolean")
        object.__setattr__(
            self,
            "evidence_cutoff_at",
            require_utc(self.evidence_cutoff_at, field_name="evidence_cutoff_at"),
        )
        leaked = _outcome_keys_in(self.model_bound_payload)
        if leaked:
            raise CorpusError(
                f"case {self.case_id!r} exposes its resolved outcome to the model "
                f"via {sorted(leaked)}; a retrospective fixture must hide the answer"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "case_class": self.case_class,
            "case_type": self.case_type,
            "evidence_cutoff_at": self.evidence_cutoff_at,
            "evidence_refs": self.evidence_refs,
            "model_bound_payload": dict(self.model_bound_payload),
            "resolved_positive": self.resolved_positive,
        }

    @property
    def case_hash(self) -> str:
        return stable_hash(CORPUS_CASE_IDENTITY_DOMAIN, self.identity_payload())


#: Keys that would reveal the answer if they reached a model.
_OUTCOME_KEYS = frozenset(
    {
        "resolved_positive",
        "outcome",
        "resolved_outcome",
        "label",
        "target",
        "realised_return",
        "realised_return_microunits",
        "pnl",
        "profit",
        "future_return",
        "result",
    }
)


def _outcome_keys_in(payload: Mapping[str, Any], *, depth: int = 0) -> set[str]:
    """Find answer-revealing keys anywhere in a nested payload."""
    if depth > 6:
        return set()
    found: set[str] = set()
    for key, value in payload.items():
        if str(key).strip().lower() in _OUTCOME_KEYS:
            found.add(str(key))
        if isinstance(value, Mapping):
            found |= _outcome_keys_in(value, depth=depth + 1)
    return found


@dataclass(frozen=True)
class RetrospectiveCorpus:
    """A frozen manifest of retrospective fixtures.

    ``frozen`` is a content-derived identity over every case, so the manifest a
    comparison names is the manifest it actually ran on.
    """

    corpus_version: str
    cases: tuple[RetrospectiveCorpusCase, ...]
    schema_version: int = RETROSPECTIVE_CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RETROSPECTIVE_CORPUS_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported RetrospectiveCorpus schema_version")
        if not isinstance(self.corpus_version, str) or not self.corpus_version.strip():
            raise ValueError("corpus_version is required")
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("a corpus requires at least one case")
        seen: set[str] = set()
        for case in self.cases:
            if not isinstance(case, RetrospectiveCorpusCase):
                raise ValueError("cases must be RetrospectiveCorpusCase values")
            if case.case_id in seen:
                raise CorpusError(f"duplicate corpus case id {case.case_id!r}")
            seen.add(case.case_id)

    def class_counts(self) -> Mapping[CaseClass, int]:
        counts = {case_class: 0 for case_class in CaseClass}
        for case in self.cases:
            counts[case.case_class] += 1
        return counts

    def missing_classes(self) -> tuple[CaseClass, ...]:
        """Required diagnostic classes this corpus does not cover."""
        counts = self.class_counts()
        return tuple(
            case_class for case_class in CaseClass if counts[case_class] == 0
        )

    def validate_usable(self) -> None:
        """Fail closed unless the corpus is large enough and spans every class.

        A corpus that only contains easy wins measures nothing about failure
        behaviour, so an incomplete corpus is refused rather than used with a
        caveat nobody reads.
        """
        if len(self.cases) < MINIMUM_CORPUS_CASES:
            raise CorpusError(
                f"a retrospective corpus requires at least {MINIMUM_CORPUS_CASES} "
                f"cases; got {len(self.cases)}"
            )
        missing = self.missing_classes()
        if missing:
            raise CorpusError(
                "a retrospective corpus must span every diagnostic class; missing "
                f"{[case_class.value for case_class in missing]}"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus_version": self.corpus_version,
            "cases": tuple(case.case_hash for case in self.cases),
        }

    @property
    def corpus_hash(self) -> str:
        """The frozen identity of this corpus."""
        return stable_hash(CORPUS_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class RoleComparison:
    """A Phase-B comparison of roles and models over one frozen corpus.

    The phase is asserted retrospective and the evidence disposition is stated, so
    the result cannot be read as portfolio profit evidence.
    """

    corpus_version: str
    corpus_hash: str
    roles: tuple[CommitteeRole, ...]
    experiment_id: str
    generated_at: datetime
    phase: EvaluationPhase = EvaluationPhase.RETROSPECTIVE
    evidence_disposition: str = RETROSPECTIVE_EVIDENCE_DISPOSITION
    measurement_only: bool = True
    automatic_promotion: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        if self.phase is not EvaluationPhase.RETROSPECTIVE:
            raise CorpusError(
                "a Phase-B comparison is retrospective evidence; prospective "
                "evidence belongs to the sealed prospective experiment"
            )
        if self.evidence_disposition != RETROSPECTIVE_EVIDENCE_DISPOSITION:
            raise CorpusError(
                "a retrospective comparison must carry the research-only disposition"
            )
        if self.measurement_only is not True:
            raise CorpusError("a retrospective comparison is measurement only")
        if self.automatic_promotion or self.trade_authority_changed:
            raise CorpusError(
                "a retrospective comparison cannot promote a model or change "
                "trading authority"
            )
        if not self.roles:
            raise CorpusError("a role comparison names at least one role")
        for role in self.roles:
            if not isinstance(role, CommitteeRole):
                raise CorpusError("invalid role in comparison")
        object.__setattr__(
            self,
            "generated_at",
            require_utc(self.generated_at, field_name="generated_at"),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "corpus_version": self.corpus_version,
            "corpus_hash": self.corpus_hash,
            "roles": self.roles,
            "experiment_id": self.experiment_id,
            "generated_at": self.generated_at,
            "phase": self.phase,
            "evidence_disposition": self.evidence_disposition,
        }

    @property
    def comparison_id(self) -> str:
        return stable_hash(CORPUS_IDENTITY_DOMAIN, self.identity_payload())


def build_role_comparison(
    *,
    corpus: RetrospectiveCorpus,
    roles: Sequence[CommitteeRole],
    experiment_id: str,
    generated_at: datetime,
) -> RoleComparison:
    """Compare roles over a frozen corpus, refusing an unusable corpus.

    The corpus is validated before use, so a comparison cannot be produced from a
    corpus that is too small or that omits the failure classes.
    """
    if not isinstance(corpus, RetrospectiveCorpus):
        raise CorpusError("a role comparison requires a RetrospectiveCorpus")
    corpus.validate_usable()
    return RoleComparison(
        corpus_version=corpus.corpus_version,
        corpus_hash=corpus.corpus_hash,
        roles=tuple(roles),
        experiment_id=experiment_id,
        generated_at=generated_at,
    )


def build_diagnostic_corpus(
    *,
    corpus_version: str,
    cases: Iterable[RetrospectiveCorpusCase],
) -> RetrospectiveCorpus:
    """Construct a corpus from cases supplied by a caller.

    Supplied rather than generated: real retrospective fixtures carry real market
    evidence, and this module must not invent evidence it does not have.
    """
    return RetrospectiveCorpus(corpus_version=corpus_version, cases=tuple(cases))


__all__ = [
    "CORPUS_CASE_IDENTITY_DOMAIN",
    "CORPUS_IDENTITY_DOMAIN",
    "CaseClass",
    "CorpusError",
    "MINIMUM_CORPUS_CASES",
    "RETROSPECTIVE_CASE_SCHEMA_VERSION",
    "RETROSPECTIVE_CORPUS_SCHEMA_VERSION",
    "RETROSPECTIVE_EVIDENCE_DISPOSITION",
    "RetrospectiveCorpus",
    "RetrospectiveCorpusCase",
    "RoleComparison",
    "build_diagnostic_corpus",
    "build_role_comparison",
]
