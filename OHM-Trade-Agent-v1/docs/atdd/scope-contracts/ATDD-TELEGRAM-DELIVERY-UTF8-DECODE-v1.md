INCREMENT:
ATDD-TELEGRAM-DELIVERY-UTF8-DECODE-v1

OWNER-APPROVED INTENT:
Make text decoding explicit in the telegram-delivery-closure test module only. `tests/test_telegram_delivery_closure_v2.py` read UTF-8 source and JSON fixtures through `Path.read_text()` with no encoding, so decoding fell back to the host locale codec. On a cp1252 host the module raised `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f` while reading a module source file (line 449), producing `1 failed, 10 passed` on the merged baseline `46a6c7e624a6…` (frozen `main`). The repair adds `encoding="utf-8"` to the module's fifteen `read_text()` sites. Assertions, test names, test intent and all application behaviour are unchanged. No application, dependency, workflow, release-profile or architecture change.

ARCHITECTURE REFERENCES:
- `AGENTS.md` — rule 12 (run the relevant validation before proposing a change) and rule 13 (never modify tests merely to make failures disappear; fix the product bug or record an explicit, reviewed behaviour change). This increment changes neither assertions nor behaviour.
- `CLAUDE.md` — quality contract: tests must be deterministic and must not depend on host environment.
- `OHM-Trade-Agent-v1/tests/test_telegram_delivery_closure_v2.py` — the module under repair; its obligations (canonical delivery ledger, terminal outbox truth, atomic registry IO) are preserved exactly.
- Architecture v1.5.0 (frozen) is untouched: this increment is test-determinism only and creates no runtime, ownership or authority change.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the telegram delivery closure module on a host whose default text encoding is not UTF-8 (cp1252)
WHEN:
the module is executed
THEN:
every file it reads is decoded as UTF-8 explicitly, so no `UnicodeDecodeError` is raised by implicit locale decoding

AC-002:
GIVEN:
the module's existing assertions and test names
WHEN:
the decoding repair is applied
THEN:
no assertion is weakened, skipped, removed or re-scoped, and no test intent changes

AC-003:
GIVEN:
the delivery-outbox and registry-IO behaviour exercised by the module
WHEN:
the repair is applied
THEN:
application behaviour is unchanged and the module's atomic-registry-IO and terminal-outbox obligations are still exercised

EXPLICITLY OUT OF SCOPE:
- Any change to `app/**`: notifiers, delivery ledger, retry/outbox behaviour, registry IO and every runtime path stay exactly as shipped.
- Any change to any other test module, any application or test dependency, any workflow, release profile, architecture document or policy threshold.
- Any change to the module's assertions, test names or test intent; any skipped test; any mocking of delivery results.
- Any widening of trading authority, canonical ownership or capital accounting.

FROZEN BOUNDARIES:
- Assertions and test intent in the module remain exactly as shipped; the only textual change is the explicit `encoding="utf-8"` argument.
- The modules under test remain the real modules; nothing is stubbed to make the suite pass.
- Determinism: no assertion may depend on the host locale or filesystem encoding.
- No new runtime behaviour, store, event, timer, scheduler or authority is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_telegram_delivery_closure_v2.py::test_telegram_alert_state_writers_use_atomic_registry_io
AC-002 -> tests/test_telegram_delivery_closure_v2.py::test_canonical_delivery_ledger_tracks_message_id_retry_and_latency
AC-002 -> tests/test_telegram_delivery_closure_v2.py::test_delivery_summary_distinguishes_suppressed_not_eligible_and_failed
AC-003 -> tests/test_telegram_delivery_closure_v2.py::test_terminal_outbox_claim_prevents_concurrent_duplicate_workers
AC-003 -> tests/test_telegram_delivery_closure_v2.py::test_terminal_retry_does_not_duplicate_already_recorded_delivery
AC-003 -> tests/test_telegram_delivery_closure_v2.py::test_terminal_alert_outbox_retires_when_exchange_fill_supersedes_market_alert

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_telegram_delivery_closure_v2.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_telegram_delivery_closure_v2.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_telegram_delivery_closure_v2.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TELEGRAM-DELIVERY-UTF8-DECODE-v1.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- Whether any other test module still relies on locale-default decoding is NOT decided here; only this module is in scope. A repository-wide encoding audit would be a separate increment.
- Whether the repository should standardise on an explicit encoding helper is an owner decision and is out of scope.
- The wider suite run in CI is the authority for the module's behaviour under Linux; this increment's local evidence was produced on a Windows/cp1252 host, where the failure was reproducible.

UNAPPROVED SCOPE CHANGES:
NONE
