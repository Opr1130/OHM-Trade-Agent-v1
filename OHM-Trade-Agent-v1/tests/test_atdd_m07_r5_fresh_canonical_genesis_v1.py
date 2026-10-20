"""Acceptance criteria for the bounded M07 R5 fresh-canonical-genesis increment.

Scope contract: ``docs/atdd/scope-contracts/ATDD-M07-R5-fresh-canonical-genesis-v1.md``.

These are contract-level checks that bind the new capability to its approved
criteria and to the certified controls it must not weaken. Behavioural proof
lives in ``deploy/m07/tests/scenarios/test_r5_fresh_genesis.py`` and in the Linux
qualification job; these checks keep that evidence honest and cheap.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
M07 = APP / "deploy" / "m07"
CONTRACTS = APP / "docs" / "atdd" / "scope-contracts"

GENESIS = M07 / "scripts" / "opip_genesis.py"
GUARD = M07 / "scripts" / "opip_rollback_guard.py"
LAYOUT = M07 / "scripts" / "opip_layout.py"
BOOTSTRAP = M07 / "scripts" / "v2" / "bootstrap-v2.sh"
SCENARIO = M07 / "tests" / "scenarios" / "test_r5_fresh_genesis.py"
RUNBOOK = M07 / "16-FRESH-GENESIS-RUNBOOK.md"
READINESS = M07 / "17-FRESH-GENESIS-READINESS.csv"
CUTOVER = M07 / "06-SINGLE-CUTOVER-RUNBOOK.md"
WORKFLOW = REPO / ".github" / "workflows" / "m07-cutover-toolkit.yml"

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
INCREMENT = "ATDD-M07-R5-fresh-canonical-genesis-v1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.acceptance
def test_ac_001_one_bounded_capability_reuses_the_certified_initialization():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-001: certified reuse, no second writer."""
    source = _read(GENESIS)
    assert CERTIFIED_SHA in source
    # The certified primitives own schema initialization and store ownership.
    assert "app.opip.canonical.schema.initialize_schema" in source
    assert "app.opip.canonical.writer.CanonicalWriter" in source
    assert "CanonicalStoreLock" in source
    assert "normalize_to_rollback_journal" in source
    assert "validate_canonical_sqlite" in source
    # No competing DDL or a second database writer inside the toolkit.
    assert "CREATE TABLE" not in source
    assert "executescript" not in source
    assert "INSERT INTO" not in source
    assert "def initialize_schema" in _read(APP / "app" / "opip" / "canonical" / "schema.py")
    # The capability is one new toolkit module plus its evidence.
    assert GENESIS.is_file() and SCENARIO.is_file()

    contract = _read(CONTRACTS / f"{INCREMENT}.md")
    # The certified application tree and the core Compose contract stay untouched:
    # no implementation-map entry may authorize them.
    map_lines = [
        line.strip()
        for line in contract.splitlines()
        if line.strip().startswith("AC-") and "->" in line
    ]
    assert map_lines
    assert not any(
        "OHM-Trade-Agent-v1/app/" in line or "docker-compose.yml" in line
        for line in map_lines
    )



@pytest.mark.acceptance
def test_ac_002_owner_identity_and_destination_are_proven_before_any_mutation():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-002: refusals precede mutation."""
    source = _read(GENESIS)
    for check_id in (
        "AUTHORIZATION",
        "RECEIPT_REQUIRED",
        "CERTIFIED_SHA",
        "HOST_IDENTITY_ENDPOINT",
        "HOST_IDENTITY",
        "DESTINATION_LAYOUT",
        "DESTINATION_CONFIRMED",
        "RECEIPT_LOCATION",
        "RECEIPT_PRESENT",
        "EXISTING_CANONICAL_STORE",
        "SQLITE_SIDECARS",
        "EXISTING_WRITER",
        "UNKNOWN_DATABASE_FILE",
    ):
        assert f'"{check_id}"' in source, check_id
    mutation = source.index("canonical_dir.mkdir")
    for check_id in (
        "AUTHORIZATION",
        "RECEIPT_REQUIRED",
        "HOST_IDENTITY_ENDPOINT",
        "DESTINATION_CONFIRMED",
        "RECEIPT_PRESENT",
        "UNKNOWN_DATABASE_FILE",
    ):
        assert source.index(f'"{check_id}"') < mutation, check_id
    # A missing destination confirmation or receipt path is a refusal, never a
    # PASS with a note.
    assert "no --confirm-destination supplied" not in source
    assert "an explicit destination confirmation is required" in source
    assert "an authoritative receipt path is required" in source
    # A missing or unreachable identity is never replaced by a self-declared one.
    assert "platform.node()" in source
    assert 'result["host_identity_proven"] = True' in source
    assert "No fallback (hostname," in source
    assert "environment) is ever used as an identity substitute" in source
    # Production identity may only be read from the pinned endpoint: the endpoint
    # is a constant, not a CLI option, and an in-process override must be the
    # pinned URL or a literal loopback test stub.
    assert "def imds_endpoint_trust" in source
    assert "DEFAULT_IMDS_URL = f\"http://169.254.169.254{METADATA_PATH}\"" in source
    assert 'p_fresh.add_argument("--imds-url"' not in source
    assert "imds_url=args.imds_url" not in source
    assert "address.is_loopback" in source
    assert (
        'result["host_identity_production_endpoint"] = imds_url == DEFAULT_IMDS_URL'
        in source
    )

    scenario = _read(SCENARIO)
    for name in (
        "test_a_missing_destination_confirmation_is_refused_before_any_mutation",
        "test_a_missing_destination_confirmation_refuses_even_at_the_certified_path",
        "test_a_missing_receipt_path_is_refused_before_any_store_is_created",
        "test_a_missing_receipt_path_never_publishes_a_store_even_when_authorized",
        "test_an_untrusted_metadata_endpoint_cannot_substitute_the_host_identity",
        "test_only_the_pinned_endpoint_or_a_loopback_stub_may_name_the_host",
        "test_the_pinned_and_loopback_endpoints_remain_accepted",
        "test_the_production_cli_cannot_select_a_metadata_endpoint",
        "test_a_repeated_invocation_preserves_the_store_and_receipt_and_adds_only_evidence",
        "test_each_repeated_invocation_adds_its_own_evidence_and_nothing_else",
    ):
        assert f"def {name}" in scenario, name
    # The retired name claimed more than the contract promises.
    assert "changes_nothing" not in scenario



@pytest.mark.acceptance
def test_ac_003_an_existing_database_is_never_overwritten_adopted_or_discarded():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-003: no-replace publication."""
    source = _read(GENESIS)
    assert "os.link" in source
    assert "os.replace" not in source
    assert "shutil" not in source
    # Cleanup is scoped to this tool's own private staging marker.
    assert "if STAGING_MARKER not in Path(staged).name:" in source
    scenario = _read(SCENARIO)
    for name in (
        "test_an_existing_nonempty_canonical_history_is_never_overwritten",
        "test_an_existing_empty_file_is_never_adopted",
        "test_a_corrupted_database_is_refused_and_left_untouched",
        "test_conflicting_sqlite_sidecars_are_refused",
        "test_an_unknown_database_file_blocks_a_fresh_genesis",
        "test_interrupted_genesis_artifacts_block_retry_until_removed",
        "test_a_competing_store_lock_refuses_the_genesis",
    ):
        assert f"def {name}" in scenario, name


@pytest.mark.acceptance
def test_ac_004_the_receipt_records_the_full_genesis_identity():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-004: verifiable genesis receipt."""
    source = _read(GENESIS)
    for field in (
        "destination_db_sha256",
        "certified_modules",
        '"schema"',
        "initial_epoch",
        "historical_events",
        "events_table_rows",
        "integrity_check",
        "foreign_key_violations",
        "generation_provenance",
        "receipt_sha256",
    ):
        assert field in source, field
    assert "def receipt_digest" in source
    assert "def _write_json_create_only" in source
    # A failed run writes diagnostic evidence beside, never on top of, the receipt.
    assert "def _failure_evidence_path" in source
    assert ".failed.json" in source


@pytest.mark.acceptance
def test_ac_005_writer_activation_accepts_either_receipt_and_refuses_forgeries():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-005: two receipt kinds, one gate."""
    guard = _read(GUARD)
    assert "import opip_genesis" in guard
    assert "NATIVE_RECOVERY_OPERATION = " in guard
    assert "opip_genesis.GENESIS_OPERATION" in guard
    assert "verify_genesis_receipt" in guard
    assert "is not a supported writer-gate" in guard
    assert "requires --app-root" in guard

    # The gate re-derives the facts it needs from the store, not from claims.
    genesis = _read(GENESIS)
    assert "def verify_genesis_receipt" in genesis
    assert "live database bytes do not match the generated genesis store" in genesis
    assert "receipt self-digest does not match its body" in genesis
    assert "receipt is stale" in genesis
    assert "certified source module does not match the receipt provenance" in genesis

    scenario = _read(SCENARIO)
    for name in (
        "test_a_tampered_receipt_is_refused",
        "test_a_forged_but_self_consistent_receipt_is_refused",
        "test_a_stale_receipt_is_refused",
        "test_a_future_dated_receipt_is_refused",
        "test_a_receipt_for_another_destination_is_refused",
        "test_a_store_written_after_genesis_can_no_longer_activate",
        "test_an_unsupported_receipt_operation_is_refused",
        "test_a_self_declared_genesis_pass_cannot_admit_a_store_with_history",
        "test_writer_gate_also_accepts_a_valid_native_recovery_receipt",
        "test_a_genesis_receipt_without_the_certified_source_is_refused",
    ):
        assert f"def {name}" in scenario, name


@pytest.mark.acceptance
def test_ac_006_one_writer_duplicate_activation_refused_scheduler_still_gated():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-006: single writer and scheduler gate."""
    bootstrap = _read(BOOTSTRAP)
    activate = bootstrap[
        bootstrap.index("mode_activate_writer()") : bootstrap.index("mode_activate_scheduler()")
    ]
    assert '[[ ! -e "$WRITER_RECEIPT" ]]' in activate
    assert "duplicate activation is refused" in activate
    assert "already running" in activate
    assert "grep -cx 'opip-canonical-writer'" in activate
    assert "--genesis-receipt" in activate
    assert '"$RECOVERY_RECEIPT"' in activate
    assert '--app-root "$APP_ROOT"' in activate
    assert '--expected-host "$DROPLET_ID"' in activate

    scheduler = bootstrap[bootstrap.index("mode_activate_scheduler()") :]
    assert '[[ -s "$WRITER_RECEIPT" ]]' in scheduler
    assert '[[ -s "$LIVE_ACCEPTANCE" ]]' in scheduler
    assert '"https_ok", "telegram_ok"' in scheduler
    assert "scheduler stays OFF" in scheduler

    # The fresh-start route passes the same gate, never a shortcut.
    assert "opip_genesis.py" in _read(BOOTSTRAP)
    assert "opip_genesis.py" in _read(BOOTSTRAP)[
        _read(BOOTSTRAP).index("toolkit_required_helpers()") : _read(BOOTSTRAP).index(
            "toolkit_manifest_verify()"
        )
    ]


@pytest.mark.acceptance
def test_ac_007_no_historical_state_can_enter_the_new_v2_history():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-007: no implicit history import."""
    genesis = _read(GENESIS)
    assert '"history_imported": False' in genesis
    assert "historical_sources_read" in genesis
    assert "v1_archives_touched" in genesis
    for forbidden in ("--bundle", "--restore", "--import", "--source-db", "--learning"):
        assert forbidden not in genesis, forbidden
    scenario = _read(SCENARIO)
    for name in (
        "test_v1_history_learning_and_calibration_cannot_enter_the_new_store",
        "test_the_genesis_surface_cannot_name_a_historical_source",
        "test_a_self_declared_genesis_pass_cannot_admit_a_store_with_history",
    ):
        assert f"def {name}" in scenario, name


@pytest.mark.acceptance
def test_ac_008_fresh_start_route_is_separate_and_prerequisites_are_honest():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-008: documented, inactive migration route."""
    assert RUNBOOK.is_file() and READINESS.is_file()
    runbook = _read(RUNBOOK)
    for marker in (
        "--genesis-receipt",
        "fresh-genesis",
        "--mode activate-writer",
        "HOLD",
        "historical",
        "OPIP_PAPER_V2_MODE=off",
        "recoverable backup",
        "exact-SHA",
    ):
        assert marker in runbook, marker

    cutover = _read(CUTOVER)
    assert "FRESH-START ROUTE" in cutover
    assert "historical migration route" in cutover.lower()
    assert "inactive" in cutover.lower()

    readiness = _read(READINESS)
    assert ",PASS," not in readiness and not readiness.rstrip().endswith(",PASS")
    for row in ("SOURCE_IMAGE", "CONFIG_PROTECTION", "RECOVERABLE_BACKUP", "RESOURCE_LIMITS",
                "HEALTH_EVIDENCE", "HTTPS_TELEGRAM", "LEARNING_EXACT_SHA"):
        assert row in readiness, row
    # The readiness table must stay machine-readable: six columns, real rows,
    # and never a status that claims a prerequisite already passed.
    rows = list(csv.DictReader(io.StringIO(readiness)))
    assert len(rows) == 7, rows
    assert {row["status"] for row in rows} <= {"UNPROVEN", "BLOCKED", "OWNER_GATE"}
    for row in rows:
        assert set(row) == {
            "id",
            "fresh_genesis_prerequisite",
            "why_it_blocks",
            "verification_command",
            "status",
            "owner_action",
        }, row
        assert all(row.values()), row

    workflow = _read(WORKFLOW)
    assert "tests/scenarios/test_r5_fresh_genesis.py" in workflow


@pytest.mark.acceptance
def test_ac_009_governance_handoff_is_lifecycle_safe():
    """ATDD-M07-R5-fresh-canonical-genesis-v1/AC-009: movable pointer handoff."""
    # The pointer is deliberately movable; it must merely resolve to a contract.
    active = _read(APP / "docs" / "atdd" / "ACTIVE_INCREMENT").strip()
    assert (CONTRACTS / f"{active}.md").is_file(), active
    # This increment proves its own identity from its own frozen contract.
    contract = _read(CONTRACTS / f"{INCREMENT}.md")
    assert contract.splitlines()[:2] == ["INCREMENT:", INCREMENT]
    # The completed R4 increment no longer owns the global pointer, and it
    # records the handoff instead.
    r4_test = _read(APP / "tests" / "test_atdd_m07_r4_readiness_recovery_v1.py")
    assert 'assert active == "ATDD-M07-R4-readiness-recovery-v1"' not in r4_test
    assert "scope-contracts" in r4_test and "is_file()" in r4_test
    r4_contract = _read(CONTRACTS / "ATDD-M07-R4-readiness-recovery-v1.md")
    assert "R5 ACTIVE_INCREMENT HANDOFF" in r4_contract
    assert INCREMENT in r4_contract
    # The structural guard now covers the corrected module.
    scope_test = _read(APP / "tests" / "test_atdd_scope_control.py")
    assert '"test_atdd_m07_r4_readiness_recovery_v1.py"' in scope_test


