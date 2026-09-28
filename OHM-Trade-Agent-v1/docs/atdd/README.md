# ATDD scope control

Architecture defines what O'Pip is allowed to be. An OWNER-approved acceptance-criteria contract defines what the current increment is allowed to change. Pytest proves those criteria. Anything outside them stops and returns to the OWNER as a scope-change request.

This mechanism is subordinate to approved architecture. It does not replace architecture, replace pytest, authorize implementation, grant trading authority, or change runtime behavior.

## Precedence

```text
OWNER directives / OWNER-approved rulings
        ↓
Attached canonical architecture (v1.4.3 while that package is the OWNER source)
        ↓
Checked-in v1.2 architecture contracts and their invariant tests
        ↓
Approved ATDD scope contract for the current increment
        ↓
Implementation
```

Checked-in baseline: `docs/architecture/v1.2/`. The attached architecture package is `OPIP_Profit_Intelligence_Architecture_v1_4_3.docx` (22 September 2026). It is not copied into this repository by this increment.

## Where a contract lives

One markdown contract per increment:

`docs/atdd/scope-contracts/<increment-id>.md`

Required sections, in order:

```text
INCREMENT:
OWNER-APPROVED INTENT:
ARCHITECTURE REFERENCES:
APPROVED ACCEPTANCE CRITERIA:
EXPLICITLY OUT OF SCOPE:
FROZEN BOUNDARIES:
ACCEPTANCE TEST TRACEABILITY:
IMPLEMENTATION MAP:
DEFERRED DISCOVERIES:
UNAPPROVED SCOPE CHANGES:
```

`UNAPPROVED SCOPE CHANGES` must be exactly `NONE` for the increment to pass. Any other text means `SCOPE_CHANGE_REQUIRED`.

Each criterion uses `AC-NNN` plus `GIVEN:`, `WHEN:`, and `THEN:` behavior. Do not write criteria that only name a function and a return value.

`IMPLEMENTATION MAP` is the file half of traceability. A changed file that is not mapped to an approved criterion is not authorized.

## Pytest

Pytest remains the execution engine. No BDD framework is added.

Acceptance tests use the registered marker:

```python
@pytest.mark.acceptance
def test_example():
    """AC-001: behavior under test."""
```

The docstring must contain the `AC-NNN` id. The checker reads that docstring. Fixture text inside the test body is not a criterion.

From `OHM-Trade-Agent-v1/`:

```bash
python -m pytest -m acceptance
```

`pytest.yml` already runs the full suite, including these tests. This increment does not add a CI job.

## Scope-drift stop

If implementation discovers behavior that is necessary and is not an approved criterion:

```text
STOP
SCOPE_CHANGE_REQUIRED
```

Do not implement it. Do not silently broaden an existing criterion. Record the proposed criterion and wait for OWNER approval. Put that text in `UNAPPROVED SCOPE CHANGES` only as a request; it fails the check until the OWNER approves a new contract.

Adjacent bugs, refactors, observability, and future requirements go under `DEFERRED DISCOVERIES`. They are not acceptance criteria and do not enter the increment.

Check a diff from `OHM-Trade-Agent-v1/`:

```bash
python tests/atdd_scope.py path/to/changed_file.py
```

Paths may be app-relative or `OHM-Trade-Agent-v1/...` from the wrapper root. Exit `0` is `PASS`. Exit `2` is `SCOPE_CHANGE_REQUIRED`. Exit `1` is `TRACEABILITY_GAP` (an approved criterion has no matching acceptance test, or a trace does not match).

## Implementation agent

1. Read approved architecture.
2. Read the approved scope contract.
3. Use only the criteria in that contract.
4. Write or update the acceptance tests that cite those ids.
5. Implement only the behavior those criteria require.
6. Run `python -m pytest -m acceptance` and the targeted tests.
7. Run the existing quality and security workflows. Do not invent new ones here.
8. Map every changed file to an approved criterion.
9. Stop when a new behavior would require a new criterion.

Completion table:

```text
| AC | Requirement | Architecture Ref | Acceptance Test | Implementation | Result |
```

## Current increment

`docs/atdd/scope-contracts/ATDD-000-scope-control.md` is the scope contract for this mechanism. Its criteria describe the checker. They do not authorize product behavior.
