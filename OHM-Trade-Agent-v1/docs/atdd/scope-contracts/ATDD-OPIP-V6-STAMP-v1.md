INCREMENT:
ATDD-OPIP-V6-STAMP-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 slice A
names one version stamp that labels O'Pip evidence: the strategy version, the
model version (an explicit null while no ML build is declared) and the live gate
policy fingerprint. `app/opip/decision/versioning.py::version_stamp` already
produces this block; this increment reuses that module instead of adding a second
versioning source, and it adds the acceptance proof plus the written purity
guarantee. Runtime behavior, evidence schemas and thresholds are unchanged, and
nothing is wired into the scanner path.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 5 slice A (version stamp module, pure and tested) and section 7 (safety boundary).
- OHM-Trade-Agent-v1/app/opip/decision/versioning.py: the existing stamp, named versions and `gate_policy_fingerprint()`.
- OHM-Trade-Agent-v1/app/opip/decision/thresholds.py: `gate_policy_constants()`, the live thresholds the fingerprint is derived from.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the versioning module
WHEN: version_stamp() is called
THEN: it returns a dict with strategy_version, model_version (None if unversioned) and gate_policy_fingerprint matching gate_policy_fingerprint()

AC-002:
GIVEN: two calls with unchanged thresholds
WHEN: the two stamps are compared
THEN: the stamps are equal

EXPLICITLY OUT OF SCOPE:
- Scanner wiring, record schemas, configuration, deploy scripts and trading code.
- Defining, changing or loosening any threshold or gate policy value.
- Kraken order placement, modification, cancellation or confirmation of any kind.
- Teaching the scorecard or qualification streams to consume the stamp (later slices).

FROZEN BOUNDARIES:
- Advisory-only: coding automation has no trading authority and no exchange permissions.
- Deterministic gates keep failing closed; external AI stays advisory evidence.
- Existing version identifiers, schema version and fingerprint derivation are preserved.
- Other increments' contracts, tests and behavior are unchanged.
- No secret, credential or `.env` value is read, printed or written.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_version_stamp_v1.py::test_version_stamp_carries_strategy_model_and_gate_fingerprint
AC-002 -> tests/test_v6_version_stamp_v1.py::test_two_stamps_with_unchanged_thresholds_are_equal

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/decision/versioning.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_version_stamp_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-STAMP-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/versioning.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_version_stamp_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-STAMP-v1.md

DEFERRED DISCOVERIES:
- Stamping qualification records with this block is v6 slice D and needs its own owner-approved contract.
- Scorecard consumption of the stamp is v6 slice E and needs its own owner-approved contract.

UNAPPROVED SCOPE CHANGES:
NONE
