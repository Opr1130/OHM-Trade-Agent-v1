INCREMENT:
ATDD-SQLITE-LINEAGE-IDENTIFIER-M01

OWNER-APPROVED INTENT:
Safely escape double quotes when counting SQLite sidecar tables enumerated from sqlite_master, preventing a crafted local schema identifier from concealing populated legacy state. This draft requires separate merge approval and cannot activate learning.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0: immutable canonical evidence, EVIDENCE_SHADOW and F11 independence
- Existing learning lineage strict fail-closed sidecar binding
- Active scanner ATDD pointer remains unchanged
- Security debt issue #474: B608 learning_derived_state.py SQL identifier robustness

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
an unbound legacy sidecar with a populated table whose name includes SQL syntax and a double quote
WHEN:
the lineage binder inventories the table through sqlite_master and counts its rows
THEN:
it queries the exact quoted identifier, rejects the populated legacy state, and does not bind a new lineage identity

AC-002:
GIVEN:
a legitimately empty fresh sidecar with a Unicode and double-quote table name
WHEN:
the binder checks whether its tables contain any legacy state
THEN:
it accurately counts the quoted table and binds the current lineage without altering its schema

AC-003:
GIVEN:
the repository's authoritative ATDD scope pointer remains unchanged
WHEN:
an isolated mapped SQLite-identifier security change or an unrelated mixed authority change is reviewed
THEN:
the isolated security scope passes while mixed or unmapped runtime edits are rejected

EXPLICITLY OUT OF SCOPE:
- activating learning, changing recovery/retirement semantics or lineage identity
- canonical writer SQL, schema migrations, new tables, deploy or production activation
- broad SQL rewrites, Bandit rule suppression or risk-acceptance declarations
- enabling Committee or Paper-v2 trading authority

FROZEN BOUNDARIES:
- canonical writer remains sole writable authority
- existing sidecar identity and fail-closed unbound-state behavior are preserved
- F11 remains independent, EVIDENCE_SHADOW, learning/Paper-v2/Committee OFF
- no merge, production deployment or activation without separate owner approval

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_learning_sql_identifier_security_v1.py::test_populated_quoted_table_cannot_masquerade_as_empty_sidecar
AC-002 -> tests/test_learning_sql_identifier_security_v1.py::test_empty_quoted_unicode_table_binds_to_current_lineage
AC-003 -> tests/test_learning_sql_identifier_security_v1.py::test_sqlite_lineage_security_scope_rejects_mixed_authority_edits

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/learning_derived_state.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_learning_sql_identifier_security_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/learning_derived_state.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_learning_sql_identifier_security_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SQLITE-LINEAGE-IDENTIFIER-M01.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_learning_sql_identifier_security_v1.py

DEFERRED DISCOVERIES:
- exact-head mandatory Linux CI, full pytest and Bandit baseline after source changes
- production sidecar schema/ownership verification if later deployment is approved

UNAPPROVED SCOPE CHANGES:
NONE
