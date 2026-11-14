# O'Pip PostgreSQL Phase 1 — fail-closed contract

## Contract and the remaining FILES-writer gap

- `OPIP_BUSINESS_STORE_AUTHORITY` unset, empty or `FILES` is the only legitimate pre-cutover state.
  Any other value that is not `POSTGRES` (for example a typo) makes `new_admissions_allowed` refuse
  (`INVALID_AUTHORITY_CONFIG`); it never falls back to the file admission path.
- `ops.append_business_event` treats a replay as a duplicate only when the aggregate, event type,
  occurrence time and payload hash are all identical. `occurred_at` must be the business fact's own
  time, never the retry time.
- A typed row is accepted only in the same transaction that appended its ledger event, for the matching
  aggregate and event type, under the current epoch.
- **Not yet true:** FILES writers are not fenced by the database. Until each production writer calls
  `new_admissions_allowed` (Phase 2 call-site integration), a node still running the file path can write
  after the authority flips. That is why the cutover remains OFF and requires separate owner approval.
