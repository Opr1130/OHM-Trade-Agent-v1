# ATDD scope control

Architecture defines what O'Pip is allowed to be. An OWNER-approved acceptance-criteria contract defines what the current increment is allowed to change. Pytest proves those criteria. Anything outside them stops and returns to the OWNER as a scope-change request.

This mechanism is subordinate to approved architecture. It does not replace architecture, replace pytest, authorize implementation, grant trading authority, or change runtime behavior.

## Precedence

```text
OWNER directives / OWNER-approved rulings
        ↓
Attached canonical architecture (v1.5.0 while that package is the OWNER source)
        ↓
Checked-in v1.2 architecture contracts and their invariant tests
        ↓
Approved ATDD scope contract for the current increment
        ↓
Implementation
```

Checked-in baseline: `docs/architecture/v1.2/` for clause-level history. The current attached architecture package is `OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (9 October 2026), copied into this repository at `docs/architecture/v1.5.0/` with its paragraph extraction, `SOURCE.md` identity pin and the owner feature-priority tracker. The v1.4.4 amendment (4 October 2026) and the baselined v1.4.3 body (22 September 2026) are retained at `docs/architecture/v1.4.4/` and `docs/architecture/v1.4.3/`. The earlier statement that the package "is not copied into this repository by this increment" described the R0/R1 increment at that time; it is preserved as history, and adoption of the source bytes remains documentary only.

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
    """ATDD-000-scope-control/AC-001: behavior under test."""
```

The docstring must contain `INCREMENT/AC-NNN`. The checker reads that pair. A bare `AC-NNN` does not attach the test to a contract. Fixture text inside the test body is not a criterion.

Supported acceptance marks are `@pytest.mark.acceptance` on a module-level test, an `async def` test, or a test method; the same decorator on a test class; and `pytestmark = pytest.mark.acceptance` or a list containing that mark on the module or class. Node ids keep the class, for example `tests/test_x.py::TestSomething::test_behavior`. A marker alias such as `mark = pytest.mark.acceptance` is rejected. The checker does not silently skip a form it cannot classify.

`GIVEN:`, `WHEN:`, and `THEN:` must each include non-whitespace text.

From `OHM-Trade-Agent-v1/`:

```bash
python -m pytest -m acceptance
```

`.github/workflows/pytest.yml` runs that suite and an `atdd-scope` job. The job runs `tests/atdd_scope.py --pr-scoped`: it selects the one scope contract the pull request adds or modifies and compares the diff with that increment only. `docs/atdd/ACTIVE_INCREMENT` is read only as the legacy fallback when the pull request changes no contract.

## Scope-drift stop

If implementation discovers behavior that is necessary and is not an approved criterion:

```text
STOP
SCOPE_CHANGE_REQUIRED
```

Do not implement it. Do not silently broaden an existing criterion. Record the proposed criterion and wait for OWNER approval. Put that text in `UNAPPROVED SCOPE CHANGES` only as a request; it fails the check until the OWNER approves a new contract.

Adjacent bugs, refactors, observability, and future requirements go under `DEFERRED DISCOVERIES`. They are not acceptance criteria and do not enter the increment.

Per pull request, exactly one increment may authorize files: the contract that PR changes (see PR-scoped selection below), or, for a PR that changes no contract, the legacy `docs/atdd/ACTIVE_INCREMENT` fallback. An older contract's implementation map does not authorize a later increment. A missing, ambiguous, or unknown active increment fails closed.

### PR-scoped selection (collision-free)

A single global pointer cannot serve concurrent pull requests: each PR had to rewrite it, so the last to merge invalidated the others. CI now runs `python tests/atdd_scope.py --pr-scoped --git-base <base>`. The increment is the **one scope contract the change set adds or modifies** (`docs/atdd/scope-contracts/<INCREMENT>.md`); the pointer on the base is ignored. Rules, all fail-closed:

- Two or more changed contracts: ambiguous.
- A contract whose file name differs from its `INCREMENT`: rejected.
- A PR that edits `ACTIVE_INCREMENT` to a value different from its contract: rejected.
- No changed contract: legacy fallback to `ACTIVE_INCREMENT` (missing or unknown still fails).
- A contract must map its own file, and every changed file must be in that contract's implementation map.

New PRs should add their own contract and **not** touch `ACTIVE_INCREMENT`. The legacy `--increment` mode is unchanged. Why not a per-PR pointer file or a label: the contract already exists, is reviewed, and is in the diff, so deriving selection from it adds no new state to collide. Authorization remains reviewer-gated, as before: a PR cannot be green without a contract that maps all its files, but the reviewer must still read that contract.

### The active increment pointer is movable

`docs/atdd/ACTIVE_INCREMENT` holds a single increment identifier. It is **mutable orchestration state**, not a permanent record: it is **no longer authoritative for PRs that carry their own contract** and is only the migration fallback for PRs that change none (the fallback is intentionally supported and still fails closed when missing, ambiguous or unknown). **New work should carry its own contract and must not edit this file.** Enforcement belongs to `tests/atdd_scope.py` and the `atdd scope` CI job.

Consequences for acceptance tests:

- A completed product increment's acceptance tests must **not** permanently require the global pointer to equal that increment. Pinning it blocks every later approved increment, because only one increment can be active at a time.
- Increment identity is proven from that increment's **own** contract and test identity — its scope contract exists and declares its increment — not by requiring the global pointer to stay there forever.
- A historical test may verify that the current pointer **resolves to an existing scope contract**. That is the R0/R1 pattern:

  ```python
  pointer = _read(ATDD / "ACTIVE_INCREMENT").strip()
  # The pointer is expected to move later, so it is not pinned here; the scope
  # checker enforces that the active pointer matches the checked increment.
  assert (ATDD / "scope-contracts" / f"{pointer}.md").is_file(), pointer
  ```

- A structural regression test in `tests/test_atdd_scope_control.py` scans every increment acceptance module (including F3, F4, F5 and F6) and fails closed if any increment test reads the global pointer and compares it to its own increment identity. It catches pointer-derived locals (including annotated and walrus bindings), pointer-returning helpers, direct pointer expressions, and `read_increment_pointer()` calls, in assertions or conditional guards, and it deliberately ignores generic checks such as `startswith("ATDD-")`. This prevents a future completed increment from re-introducing a global-pointer ownership pin.

The pointer must always name an existing scope contract, and the checker still fails closed when it is missing, ambiguous, or unknown.

Check the branch diff from `OHM-Trade-Agent-v1/`:

```bash
python tests/atdd_scope.py --increment ATDD-000-scope-control --git-base origin/main
```

`--git-base` includes additions, modifications, renames, and deletions. Omitting both `--git-base` and explicit paths fails closed. Exit `0` is `PASS`. Exit `2` is `SCOPE_CHANGE_REQUIRED`. Exit `1` is `TRACEABILITY_GAP`.

Every authorized path is relative to the repository root. `OHM-Trade-Agent-v1/pyproject.toml` and `pyproject.toml` are different files. `.github/workflows/pytest.yml` and `OHM-Trade-Agent-v1/.github/workflows/pytest.yml` are different files. The checker keeps the `OHM-Trade-Agent-v1/` prefix from the Git diff. A leading `./` and backslashes are normalized before that comparison. A `..` segment is rejected. Positional paths must already be repository-root paths; the checker does not treat a shorter suffix as the same file.

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
