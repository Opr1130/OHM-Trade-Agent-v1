INCREMENT:
ATDD-R5-M02-source-of-truth-audit

OWNER-APPROVED INTENT:
This is the audit increment of the owner-directed Mission 02 intelligence-integration work, inside frozen architecture v1.5.0. It answers one question with evidence and implements nothing: along the continuous advisor pipeline - F1 observation, F2 Feature Bus, F3 IGNITION, F4 candidate and horizon lifecycle, F5 feasibility, F6 calibrated forecast, F7 capital-aware selection, canonical decision evidence and F10 read-model consumption - what exists, what is wired, what runs, and what has production proof.

What changes where. No application module changes. This increment adds two evidence documents, one acceptance-test module and the records that accompany them. It adds no route, no fact, no panel, no producer, no schema, no writer, no scheduler entry and no configuration key. Its whole output is a machine-asserted statement of what is already true.

Four axes, never collapsed. Repository existence is not target wiring; wiring is not runtime activity; runtime activity is not production proof. Every row of the matrix records all four, and the proof axis is NOT_PROVEN on every row without exception. The acceptance tests fail if any row claims otherwise.

The finding that required care. The dashboard census records most pipeline families as BLOCKED_BY_UPSTREAM_EVIDENCE. That single phrase hides two materially different situations, and this increment separates them because the difference decides who can act. Gap type 1: the family is durably produced by the canonical writer and is point-in-time capable, and the analytics read plane simply declares no fact for it - the seven canonical event types for observations, instrument versions, feature snapshots, feature checkpoints, coverage gaps, feature restarts and F5 feasibility evidence. That is an exposure decision. Gap type 2: no canonical record of the family exists at all - candidates, detector evaluations, decisions, Committee assessments, learning events, incidents, AI usage and portfolio marks. That is a producer decision, and a new canonical writer responsibility. Neither is implemented here; the second is proposed explicitly in the owner-decision document.

The seam this increment found, and did not paper over. The continuous engine (F1 to F4) and the F5 to F7 spine are each verified on their own, and nothing has ever driven the first into the second. Two consequences are recorded: the engine's committed outcome exposes the claim identity but not the claim object, and the two F4 episode-opening entry points derive the episode identity differently on their face, agreeing today only because the ratified lifecycle policy happens to equal the module constants. The first needs an owner decision. The second is a latent lineage fork, not a live defect, and is recorded as such with the evidence that establishes the distinction.

KEY LAW - AN AUDIT MAY NOT PROMOTE ITSELF. Recording that a module exists is not evidence that it runs, and recording that it runs is not evidence that it is right. Every row keeps its four axes distinct, no row claims production proof, and the tests recompute the document's own totals from its own rows so the file cannot quietly miscount itself.

KEY LAW - PROPOSING IS NOT DOING. Where a new producer, publication mechanism, schema, job or trust decision is required, this increment records the requirement and implements none of it. The acceptance tests assert that the census's declared absences are still absent, so a proposal cannot smuggle an implementation.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md sections 2, 3 and 5: the unified F1-F7 path, the single canonical writer, and the rule that a timestamp alone is not an idempotency key.
- docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md: the F1-F7 rows whose status this audit reports and does not alter. The register is referenced, never restated.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md: the recorded conformance state the F1-F7 rows rely on.
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 5, 9, 10, 12 and 13: the availability-on-this-plane table, the registered evidence gaps, the declared absences, the unratified threshold policy and the owner-gated open items this audit reads and leaves exactly as they are.
- docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md: the approved dormancy posture that makes IMPLEMENTED_NOT_INTEGRATED a record rather than a defect, and the deferred discovery that no committed-feature-snapshot read seam exists.
- app/opip/events/opportunity_engine.py, app/opip/contracts/events.py, app/opip/fev_evidence_event.py, app/opip/decision/store.py, app/opip/canonical/writer.py, app/opip/cockpit/semantic.py, app/jobs/scan_opportunities.py: the authority paths the matrix cites and the acceptance tests probe.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-200:
GIVEN:
the recorded F1-F7 integration matrix
WHEN:
every row is parsed and its class, repository, wiring, runtime, proof and stage values are compared with the vocabulary the document itself declares, and the declared totals are recomputed from the rows
THEN:
every row carries exactly the declared keys, every value is one the document declares, every stage of the adopted pipeline is represented, every one of the six classes is used by at least one row, and the row and class totals equal the recomputed values

AC-201:
GIVEN:
the owner path, evidence path and dotted entry point each row cites
WHEN:
each path is resolved against the repository root and each entry point is imported and traversed attribute by attribute
THEN:
every cited path exists and every cited entry point resolves to a live symbol, so no row can cite a renamed, moved or invented authority

AC-202:
GIVEN:
the application package, the scheduled scan job, the canonical event contracts and the analytics read plane's declared facts
WHEN:
the engine's compiled mode, authority, environment reads and default detector evaluator are inspected, the importers of each back-half stage are listed, the scheduled job's call sites are read, the canonical event inventory is compared with the contract, and the declared read-plane facts are searched for a pipeline family
THEN:
the engine is the literal off mode with NONE authority reading no environment variable, its default evaluator is the real IGNITION detector, the feasibility, forecast and portfolio-selector stages have no application importer, the legacy service selector is the one the scheduled job calls, the canonical inventory is exactly the seven declared event types, and the read plane declares no observation, coverage, feasibility, detector or feature fact

AC-203:
GIVEN:
every recorded row and the census the audit reads
WHEN:
the proof axis is inspected and the census's parity state, availability table, threshold policy and absence declarations are read
THEN:
every row's proof is NOT_PROVEN and the document says so, parity is still zero of eight, the paper families are still the only ones marked verified, the candidate and decision families are still blocked, and the threshold policy is still unratified

AC-204:
GIVEN:
the owner-decision document and the census's declared absences
WHEN:
each decision line is parsed for its named producer, consumer, schema, failure mode, security implication and migration implication, and each declared-absent path and symbol is probed
THEN:
every decision names all six implications and covers both the exposure gaps and the producer gaps, every declared-absent path is still absent, every declared-absent symbol still appears nowhere in the application package, and no new exposure was added

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, route, panel, fact, schema, store, writer, job, cron unit or configuration key.
- Any canonical producer, publication mechanism, read-model fact or page panel. These are proposed in the owner-decision document and implemented nowhere.
- Any activation of the Feature Bus, Paper-v2, the target spine, the continuous engine mode or any release profile.
- Any change to F5 veto precedence, F6 calibrated authority, F7 selection authority, admission or capital reservation.
- Restating, editing or superseding the census, the requirements register, the conformance ledger or the criterion matrix.
- Any threshold, bound, tolerance or capacity limit.
- Any claim of production proof, parity, operator usefulness or G4.

FROZEN BOUNDARIES:
- The F1-F7 ownership recorded in docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md is unchanged.
- The single canonical writer remains the only authority that commits operational domain events and projections, and this increment writes none.
- The dormant posture of ATDD-R4-B1-runtime-integration-dormant is unchanged: the target spine stays non-authoritative and the legacy paper engines stay authoritative.
- The analytics read surface stays exactly the pinned mounted path set with no added route.
- The learning, shadow and analytics planes stay separated from production admission and exchange authority.
- No new detector family, statistical forecast authority, selector authority or scheduler topology is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-200 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_200_matrix_rows_use_only_the_declared_vocabulary
AC-200 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_200_declared_totals_recompute_from_the_rows
AC-201 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_201_every_cited_authority_path_exists
AC-201 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_201_every_cited_entry_point_resolves
AC-202 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_202_the_dormant_engine_is_still_dormant
AC-202 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_202_the_back_half_still_has_no_application_caller
AC-202 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_202_the_legacy_selector_is_still_the_admission_path
AC-202 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_202_the_canonical_event_inventory_is_exact
AC-202 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_202_the_read_plane_declares_no_pipeline_fact
AC-203 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_203_no_row_claims_production_proof
AC-203 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_203_the_census_state_this_audit_reads_is_unchanged
AC-204 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_204_every_decision_names_its_six_implications
AC-204 -> tests/test_opip_r5_m02_integration_matrix.py::test_ac_204_the_proposal_added_no_exposure_and_no_absent_capability

IMPLEMENTATION MAP:
AC-200 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M02-source-of-truth-audit.md
AC-200 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-200 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-201 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-201 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-202 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-202 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-203 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-203 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-204 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_READ_PLANE_OWNER_PROPOSAL.md
AC-204 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-200 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-200 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- Candidate, detector-evaluation and frozen-decision evidence exists only in the trading-host append-only JSONL file WAL under `/app/data/opip/qualification`, and the census already records that it is not published to the canonical replica. Publishing it needs a new canonical producer, which is a new writer responsibility and therefore an owner decision, not an audit outcome.
- The canonical plane already holds seven event types (observations, instrument versions, feature snapshots, feature checkpoints, coverage gaps, feature restarts and F5 feasibility evidence) with their own idempotency keys and watermark streams, and the analytics read plane declares no fact for any of them. This is an exposure decision and is proposed, not implemented.
- The continuous engine's committed outcome carries `claim_id`, `episode`, `thesis` and `snapshot_id` but not the `DetectorClaim` object. A consumer needing the claim must re-evaluate or be handed it separately; re-evaluating would be a second evaluation of the same input. Recorded as an owner decision, matching the deferred discovery already in ATDD-R4-B1.
- The two F4 episode-opening entry points derive the episode identity on their face differently: `app/opip/opportunity_lifecycle._create_episode` passes the caller's `policy.episode_schema_version` into `opportunity_episode_identity` while `app/opip/contracts/opportunity_phases.episode_from_claim` omits it and takes the module default. This was first recorded as a latent fork and that recording was wrong: `OpportunityLifecyclePolicy.__post_init__` refuses any unratified `episode_schema_version`, `lifecycle_version` or `policy_version`, so both paths resolve to the same ratified token by construction. A probe on one admissible IGNITION claim produced the identical `OPEP:084327d4571400a96c1bbe79fd0de0e5` from both paths with all twelve identity fields equal, and constructing a policy with `opportunity-episode-v2` raises `OpportunityContractError`. The corrected finding is the stronger one - consistency is guaranteed by the policy's fail-closed validation - and no owner decision is requested. `ATDD-R5-M02-pipeline-seam-integration` asserts the agreement, the refusal and the identity so the correction cannot silently reverse.
- The engine classifies a horizon thesis under the engineering scenario policy as `UNKNOWN` with `research_only` set, and F6 nonetheless evaluates its own forecast from the episode because F6's abstention is governed by its own policy and trusted-model registry rather than by the thesis horizon class. This is recorded as an observation for the seam increment to assert, not as a defect.
- No headless browser harness exists in this repository, so no browser end-to-end claim is made anywhere in this increment.

UNAPPROVED SCOPE CHANGES:
NONE


