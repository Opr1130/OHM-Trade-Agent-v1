# O'Pip owner decisions register

Purpose: the auditable record of decisions the human operator has **actually taken**. It
exists because a decision and a register row are different artefacts: the frozen
requirements register records what the adoption decided *before* an owner acted, and it
pins that state in its own conformance contract.

**The frozen requirements register is deliberately NOT edited by this register.** It keeps
its `UNASSIGNED` owner cells and its `OWNER_DECISION_REQUIRED` statuses, and its
conformance test continues to prove it is unchanged. Where a decision changes a
requirement's posture, this register records the decision; the register keeps its frozen
value and its history, following the same principle as the conformance and retirement
ledgers ("a corrected value never erases what was previously recorded").

## Identifier and evidence policy

- Decision identifiers are `OD-001` upward.
- `DECIDED_BY` names a **role**, never a person.
- `AUTHORITY_SOURCE` names the authorization actually received.
- **No personal identifier is recorded**, and **no independent human approval is claimed**
  beyond the authorization actually given.
- An assignment of accountability is **not** a numeric policy ratification; each decision
  states separately whether it ratified any number.
- A decision this register does not contain has not been taken.

## OD-001 — OQ-01 owner assignment

| Field | Value |
| --- | --- |
| DECISION | `OQ-01` owner assignment for the R5 requirements and gates |
| STATUS | `OWNER_DECISION_CONFIRMED` |
| DECIDED_BY | `BUSINESS_DECISION_OWNER` and `TECHNICAL_RELEASE_OWNER` — the same human operator, confirmed by explicit owner authorization to serve both roles |
| AUTHORITY_SOURCE | explicit owner authorization directing that the operator serves as both Business Decision Owner and Technical Release Owner, and that the two assignments be recorded with F1–F12 and G1–G6 accountability mapped to that owner |
| SCOPE | accountability assignment only |
| NUMERIC_POLICY_RATIFIED_BY_THIS_DECISION | `NONE` |
| IMPLEMENTATION_STATE | recorded in this register; the frozen requirements register is intentionally unmodified |
| SEPARATE_APPROVER_REQUIRED | `G6` and `PM-02` — see below |

### Why G6 / PM-02 still needs a separate approval

Architecture v1.5.0 §19 requires that all inherited and new readiness gates pass
**"with separate exact-release owner approval, legacy drain and rollback"**. That is a
*different act* from naming an accountable owner. OD-001 therefore assigns accountability
for `G6` while explicitly **not** supplying the exact-release approval, the legacy drain
authorization or the rollback authorization that `G6` and `PM-02` require.

### Accountability mapping — F1–F12

```text
MAPPING: F1  | feature=Observation            | gate=G2 | owner=OWNER | separate_approver=NO
MAPPING: F2  | feature=FeatureBus              | gate=G2 | owner=OWNER | separate_approver=NO
MAPPING: F3  | feature=Detector                | gate=G1 | owner=OWNER | separate_approver=NO
MAPPING: F4  | feature=Lifecycle               | gate=G4 | owner=OWNER | separate_approver=NO
MAPPING: F5  | feature=Feasibility             | gate=G3 | owner=OWNER | separate_approver=NO
MAPPING: F6  | feature=Forecast                | gate=G3 | owner=OWNER | separate_approver=NO
MAPPING: F7  | feature=Selector                | gate=G3 | owner=OWNER | separate_approver=NO
MAPPING: F8  | feature=PaperExecution          | gate=G5 | owner=OWNER | separate_approver=NO
MAPPING: F9  | feature=OutcomesAndLearning     | gate=G3 | owner=OWNER | separate_approver=NO
MAPPING: F10 | feature=Dashboard               | gate=G4 | owner=OWNER | separate_approver=NO
MAPPING: F11 | feature=Safety                  | gate=G5 | owner=OWNER | separate_approver=NO
MAPPING: F12 | feature=AIResearch              | gate=G6 | owner=OWNER | separate_approver=NO
```

### Accountability mapping — G1–G6

```text
MAPPING: G1 | gate=DiscoveryAndTruth      | owner=OWNER | separate_approver=NO
MAPPING: G2 | gate=CoverageAndContinuity  | owner=OWNER | separate_approver=NO
MAPPING: G3 | gate=HorizonAndCapital      | owner=OWNER | separate_approver=NO
MAPPING: G4 | gate=OperatorUsefulness     | owner=OWNER | separate_approver=NO
MAPPING: G5 | gate=SafetyAndIsolation     | owner=OWNER | separate_approver=NO
MAPPING: G6 | gate=GovernanceAndCutover   | owner=OWNER | separate_approver=YES
```

### Explicitly NOT ratified by OD-001

```text
NOT_RATIFIED: OQ-02
NOT_RATIFIED: OQ-03
NOT_RATIFIED: OQ-04
NOT_RATIFIED: OQ-05
NOT_RATIFIED: OQ-06
NOT_RATIFIED: OQ-07
NOT_RATIFIED: OQ-08
NOT_RATIFIED: OQ-09
NOT_RATIFIED: OQ-10
NOT_RATIFIED: SC-13
NOT_RATIFIED: C17
NOT_RATIFIED: PM-02
```

`OQ-07` (gate thresholds and measurement populations) is the most consequential of these:
until it is decided, **no gate in the register can pass**, because PASS requires approved
thresholds. OD-001 assigns *who* decides; it does not decide anything those rows hold.

### What OD-001 does not claim

- It does not claim a second, independent human approval. One operator holds both roles.
- It does not claim the requirements register now reads `OWNER`; it deliberately still
  reads `UNASSIGNED`, and its conformance test proves that.
- It does not ratify any threshold, duration, budget, cap or calibration value.
- It does not authorize a merge, a deployment, an activation, a retirement or a cutover.
