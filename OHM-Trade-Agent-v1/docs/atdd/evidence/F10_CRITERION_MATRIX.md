# O'Pip F10 criterion matrix (traceability binding)

Status: `TRACEABILITY_RECORDED_NOT_CERTIFIED`

Increment: `ATDD-R5-F10-traceability-matrix`

Authority granted by this document: `NONE`

Parity verdict carried, not changed: `NOT_VERIFIED` (0 of 8 pages verified)

This document binds each of the 54 criteria the frozen source census plans for the F10 page
increment to the census requirement that defines it, the frozen page that presents it, the
registered evidence gap that explains it where one applies, a governance status, the functional
disposition of the fact family today, the increment the census assigns it to, and an evidence
reference that exists in the repository. It certifies nothing: it claims no page, ratifies no
threshold, decides no projection authority and transfers no criterion to any increment.

It is validated by `tests/test_opip_r5_f10_criterion_matrix.py`, which reads the census and the
semantic read service's registries and fails closed if any binding here stops being true.

## 1. Why the rows are references, not restatements

A row names its census requirement by identifier. The criterion's meaning, its canonical
producer and the census's own classification stay in the census and are read from there by the
test. This document deliberately does not copy that wording: a copied sentence can drift into a
re-wording that looks like a citation, and the increment's test fails closed if any census
meaning text appears here.

## 2. The two axes

Each row carries two governed values that are never collapsed into a score.

`GOVERNANCE` records who owns the criterion:

- `DEFERRED_UNCLAIMED` - the criterion is still deferred in the increment the census assigns it
  to. No delivered contract approved it, no increment has claimed it, and this document does not
  claim it either. 52 of the 54 are in this state.
- `OWNER_RESERVED` - the criterion cannot be finished by any increment in this mission because
  the census reserves the decision to the owner. Two are in this state: the per-page
  counts-and-P&L reconciliation, and the saved-view contract.

`DISPOSITION` records what the delivered read-only stack does today for the fact family the
criterion asks about. Exactly one value is used per row:

- `IMPLEMENTED_AND_VERIFIED` - the capability's invariant is asserted by a delivered increment's
  own acceptance test. It is not a parity verdict.
- `IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE` - a panel publishes a real reading, and the
  counts-and-P&L reconciliation the parity predicate requires is not recorded, so parity stays
  `NOT_VERIFIED` for it.
- `BLOCKED_BY_UPSTREAM_EVIDENCE` - the canonical fact is not on the plane this read service may
  read. The row's gap column names the registered gap where the census records one.
- `BLOCKED_BY_OWNER_DECISION` - the census reserves the decision to the owner, and the criterion
  cannot be finished by any increment in this mission.
- `NOT_IMPLEMENTED_NEXT_INCREMENT` - the fact may be reachable but no panel presents it, and no
  increment is declared for it. The owner column still names the increment the census assigns the
  criterion to; this document does not invent a successor.

## 3. Row format

```text
AC | REQUIREMENT | PAGE | GAP | GOVERNANCE | DISPOSITION | OWNER_INCREMENT | EVIDENCE
```

`PAGE` is the frozen page whose panel declares that criterion, or `NO_PANEL`. `GAP` is the panel's
own registered gap identifier, or, for a criterion no panel declares, the census evidence cell
when it names a registered gap, or `-`. `OWNER_INCREMENT` is the increment the census's own ATDD
cell assigns the criterion to. `EVIDENCE` is a repository path that exists, or `NONE` where no
evidence exists because nothing exists.

## 4. The matrix

```text
AC-010 | F10-SVC-07 | NO_PANEL | - | OWNER_RESERVED | BLOCKED_BY_OWNER_DECISION | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-020 | F10-P1-01 | P1_SYSTEM_PULSE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-021 | F10-P1-02 | P1_SYSTEM_PULSE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-022 | F10-P1-03 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-023 | F10-P1-04 | P1_SYSTEM_PULSE | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_implementation.py
AC-024 | F10-P1-05 | P1_SYSTEM_PULSE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-030 | F10-P2-01 | P2_DISCOVERY_REGIME | QUALIFICATION_FUNNEL_PROGRESSION | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-031 | F10-P2-02 | P2_DISCOVERY_REGIME | QUALIFICATION_FUNNEL_PROGRESSION | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-032 | F10-P2-03 | P2_DISCOVERY_REGIME | QUALIFICATION_FUNNEL_PROGRESSION | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-033 | F10-P2-04 | P2_DISCOVERY_REGIME | REJECTION_REASON_AND_GATE_ATTRIBUTION | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-034 | F10-P2-05 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-035 | F10-P2-06 | NO_PANEL | REGIME_AND_CAPITAL_UTILIZATION_GROUPING | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-040 | F10-P3-01 | P3_DECISION_TRACE | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_implementation.py
AC-041 | F10-P3-02 | P3_DECISION_TRACE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-042 | F10-P3-03 | P3_DECISION_TRACE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-043 | F10-P3-04 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-044 | F10-P3-05 | P3_DECISION_TRACE | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_implementation.py
AC-045 | F10-P3-06 | NO_PANEL | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-050 | F10-P4-01 | P4_PAPER_PORTFOLIO | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-051 | F10-P4-02 | P4_PAPER_PORTFOLIO | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-052 | F10-P4-03 | P4_PAPER_PORTFOLIO | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-053 | F10-P4-04 | P4_PAPER_PORTFOLIO | UNREALIZED_MARK_TO_MARKET_PNL | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-054 | F10-P4-05 | NO_PANEL | UNREALIZED_MARK_TO_MARKET_PNL | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-055 | F10-P4-06 | P4_PAPER_PORTFOLIO | UNREALIZED_MARK_TO_MARKET_PNL | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-056 | F10-P4-07 | NO_PANEL | REGIME_AND_CAPITAL_UTILIZATION_GROUPING | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-060 | F10-P5-01 | P5_QUALITY_MISSED_OPPORTUNITY | QUALIFICATION_FUNNEL_PROGRESSION | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-061 | F10-P5-02 | P5_QUALITY_MISSED_OPPORTUNITY | MISSED_PROFITABLE_OPPORTUNITY | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-062 | F10-P5-03 | P5_QUALITY_MISSED_OPPORTUNITY | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-063 | F10-P5-04 | P5_QUALITY_MISSED_OPPORTUNITY | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
AC-064 | F10-P5-05 | NO_PANEL | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-065 | F10-P5-06 | P5_QUALITY_MISSED_OPPORTUNITY | MISSED_PROFITABLE_OPPORTUNITY | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-066 | F10-P5-07 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-070 | F10-P6-01 | P6_COMMITTEE_VALUE_AI_COST | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-071 | F10-P6-02 | P6_COMMITTEE_VALUE_AI_COST | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-072 | F10-P6-03 | P6_COMMITTEE_VALUE_AI_COST | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-073 | F10-P6-04 | P6_COMMITTEE_VALUE_AI_COST | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-074 | F10-P6-05 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-080 | F10-P7-01 | P7_LEARNING_RELEASES | FORWARD_OUTCOME_HORIZON_EVIDENCE | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-081 | F10-P7-02 | P7_LEARNING_RELEASES | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-082 | F10-P7-03 | P7_LEARNING_RELEASES | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-083 | F10-P7-04 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-084 | F10-P7-05 | P7_LEARNING_RELEASES | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-085 | F10-P7-06 | NO_PANEL | - | DEFERRED_UNCLAIMED | IMPLEMENTED_AND_VERIFIED | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-090 | F10-P8-01 | P8_INCIDENTS_INFRASTRUCTURE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-091 | F10-P8-02 | P8_INCIDENTS_INFRASTRUCTURE | - | DEFERRED_UNCLAIMED | IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_d_trust_learning_incident_pages.py
AC-092 | F10-P8-03 | NO_PANEL | - | DEFERRED_UNCLAIMED | NOT_IMPLEMENTED_NEXT_INCREMENT | ATDD-R5-F10-dashboard-parity-implementation | NONE
AC-093 | F10-P8-04 | P8_INCIDENTS_INFRASTRUCTURE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-094 | F10-P8-05 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-095 | F10-P8-06 | P8_INCIDENTS_INFRASTRUCTURE | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-096 | F10-P8-07 | NO_PANEL | - | DEFERRED_UNCLAIMED | BLOCKED_BY_UPSTREAM_EVIDENCE | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-100 | F10-FLT-02 | NO_PANEL | - | DEFERRED_UNCLAIMED | NOT_IMPLEMENTED_NEXT_INCREMENT | ATDD-R5-F10-dashboard-parity-implementation | NONE
AC-101 | F10-FLT-03 | NO_PANEL | - | OWNER_RESERVED | BLOCKED_BY_OWNER_DECISION | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
AC-102 | F10-FLT-04 | NO_PANEL | - | DEFERRED_UNCLAIMED | NOT_IMPLEMENTED_NEXT_INCREMENT | ATDD-R5-F10-dashboard-parity-implementation | NONE
AC-105 | F10-SEC-04 | NO_PANEL | - | DEFERRED_UNCLAIMED | IMPLEMENTED_AND_VERIFIED | ATDD-R5-F10-dashboard-parity-implementation | OHM-Trade-Agent-v1/tests/test_opip_r5_f10_c_paper_portfolio_quality.py
```

## 5. Totals

```text
CRITERIA_RECORDED: 54
GOVERNANCE_DEFERRED_UNCLAIMED: 52
GOVERNANCE_OWNER_RESERVED: 2
DISPOSITION_IMPLEMENTED_AND_VERIFIED: 2
DISPOSITION_IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE: 10
DISPOSITION_BLOCKED_BY_UPSTREAM_EVIDENCE: 37
DISPOSITION_BLOCKED_BY_OWNER_DECISION: 2
DISPOSITION_NOT_IMPLEMENTED_NEXT_INCREMENT: 3
PARITY_VERIFIED_PAGES: 0 of 8
```

## 6. What this document does not do

- It does not claim a page, a panel or a criterion. `IMPLEMENTED_AND_VERIFIED` describes an
  invariant a delivered test asserts; it is not a parity certificate and it does not transfer the
  criterion out of the increment that owns it.
- It does not decide which canonical projection owns which page fact. That stays `UNDECIDED` and
  owner-gated, and no row implies otherwise.
- It does not record a counts-and-P&L reconciliation. That stays `NOT_RECORDED` for all eight
  pages.
- It does not ratify a threshold, a tolerance, a sample size, a freshness window or a cost bound.
  The threshold policy stays `OWNER_DECISION_REQUIRED`.
- It does not carry the criteria the census plans for `ATDD-R5-F10-F` or `ATDD-R5-F10-G`,
  including the accessibility and operator-usefulness criterion and the Telegram criteria. Those
  increments' own contracts own them, and recording them here would transfer them by accident.
- It changes no runtime behavior, decides no policy and conveys no trading, deployment,
  promotion or release authority.
