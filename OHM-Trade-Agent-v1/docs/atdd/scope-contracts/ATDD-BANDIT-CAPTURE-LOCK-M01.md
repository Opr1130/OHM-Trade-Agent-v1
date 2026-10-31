INCREMENT:
ATDD-BANDIT-CAPTURE-LOCK-M01

OWNER-APPROVED INTENT:
Harden the existing process-level lock used by both independent shadow evidence producers. Refuse attacker-supplied symlinks and unsafe inodes without changing lock identities, unlinking active files, or weakening non-overlap. This is a proposal for separate owner merge approval.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0: EVIDENCE_SHADOW, canonical writer ownership and F11 independence
- R4-B2 capture producers share CaptureProcessLock, but not lock identity
- ATDD-SCANNER-RELIABILITY-M01 remains active and unchanged
- Bandit issue 474: B108 shared temporary lock risk

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
an existing safe producer lock or a new capture lock path
WHEN:
a capture process attempts lock acquisition and later releases
THEN:
a new lock is owner-private, an existing safe inode retains its identity, and overlapping acquisition is refused

AC-002:
GIVEN:
a preexisting symlink, hardlink, FIFO or group-or-world-writable lock inode
WHEN:
the capture process attempts to acquire the untrusted path
THEN:
the capture fails closed without modifying any target and without unlinking a lock inode

AC-003:
GIVEN:
the active ATDD increment remains the scanner reliability contract
WHEN:
this narrow security change set or a mixed unapproved runtime change is validated
THEN:
the mapped lock security change is allowed and every mixed unapproved path is rejected

EXPLICITLY OUT OF SCOPE:
- default lock path changes or silent cleanup of existing lock files
- new lock authority, scheduler changes, deploy, container activation or funded trading
- canonical writer, learning, paper trading and Committee runtime changes
- ignoring or suppressing Bandit B108 warnings

FROZEN BOUNDARIES:
- feature and feasibility producers remain independent and non-overlapping
- EVIDENCE_SHADOW, canonical writer ownership and F11 independence
- Paper-v2 OFF, Committee OFF, learning frozen
- no merge or deploy without explicit separate owner approval

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_capture_lock_security_v1.py::test_private_creation_and_existing_lock_identity
AC-001 -> tests/test_capture_lock_security_v1.py::test_legacy_safe_owner_readable_lock_is_accepted
AC-002 -> tests/test_capture_lock_security_v1.py::test_symlink_fifo_hardlink_and_writable_lock_rejected
AC-003 -> tests/test_capture_lock_security_v1.py::test_maintenance_scope_stays_fail_closed

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_capture_lock_security_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_capture_lock_security_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-BANDIT-CAPTURE-LOCK-M01.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_capture_lock_security_v1.py

DEFERRED DISCOVERIES:
- exact-head Linux CI proof of symlink/non-overlap, full suite and runner compatibility
- verification of alternate configured lock locations and operator file ownership
- runtime container image behavior after any separate deployment approval

UNAPPROVED SCOPE CHANGES:
NONE
