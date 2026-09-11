"""Fixture-only validation for PR 1 architecture contracts.

Does not import app runtime, trading, or exchange modules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "architecture" / "v1.2"
FIXTURES = DOCS / "fixtures"
PIN = "808a308cd274d30b55fef47b382c229b761e07df"

CONTRACT_FILES = (
    "README.md",
    "BASELINE.md",
    "SOURCE_PIN.md",
    "AUTHORITY_MAP.md",
    "CONSUMER_CENSUS.md",
    "PR2_CAPTURE_BOUNDARY.md",
    "A_PAPER_MANDATE.md",
    "B_MARKET_DATA_CONTRACT.md",
    "C_CANONICAL_WRITER_CONTRACT.md",
    "D_DETECTOR_CONTRACT.md",
    "E_FORECAST_OUTCOME_CONTRACT.md",
    "F_ECONOMIC_PORTFOLIO_CONTRACT.md",
    "G_STATISTICAL_PROTOCOL.md",
    "H_LEARNING_PROMOTION_CONTRACT.md",
    "I_SAFETY_MONITORING_CONTRACT.md",
    "J_EXPORT_BACKUP_RECOVERY_CONTRACT.md",
    "K_LEGACY_DISPOSITION_MIGRATION.md",
    "CODING_BOUNDARY_CONTRACT.md",
    "CLEANUP_MATRIX.md",
    "NUMERIC_DECISIONS.md",
    "MIGRATION_CALENDAR.md",
    "ACCEPTANCE_CHECKLIST.md",
)

FIXTURE_FILES = (
    "observation.example.json",
    "feature_snapshot.example.json",
    "feature_state_checkpoint.example.json",
    "detector_evaluate.example.json",
    "paper_outcomes.example.json",
    "coverage_incident.example.json",
    "incident_lifecycle.example.json",
    "export_manifest.example.json",
)

AUTHORITY_STATEMENTS = (
    "PRODUCTION TRADE AUTHORITY CHANGED = NO",
    "SIGNAL QUALITY SQ-01 STARTED = NO",
    "FUNDED TRADING ENABLED = NO",
)


def _docs_text() -> str:
    parts = [(DOCS / name).read_text(encoding="utf-8") for name in CONTRACT_FILES]
    return "\n".join(parts)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_contract_files_exist() -> None:
    missing = [name for name in CONTRACT_FILES if not (DOCS / name).is_file()]
    assert missing == []


def test_fixture_files_are_valid_json() -> None:
    for name in FIXTURE_FILES:
        payload = _load_fixture(name)
        assert isinstance(payload, dict)


def test_source_pin_and_branch_rule() -> None:
    text = (DOCS / "SOURCE_PIN.md").read_text(encoding="utf-8")
    assert PIN in text
    assert "origin/main" in text
    assert "fix/discovery-rotation-aware-checkpoint" in text
    assert "MUST NOT" in text


def test_no_forbidden_pr233_alias() -> None:
    text = _docs_text()
    assert "Astra" not in text
    assert re.search(r"Astra\s+PR\s*#?233", text) is None
    assert "superseded proposed Profit Intelligence implementation path" in text


def test_github_pr_233_is_not_dispositioned() -> None:
    text = (DOCS / "K_LEGACY_DISPOSITION_MIGRATION.md").read_text(encoding="utf-8")
    assert "MUST NOT merge, close, supersede, modify, or otherwise disposition GitHub PR #233" in text


def test_owners_and_dates() -> None:
    text = _docs_text()
    assert text.count("Ohm Prakash") >= 4
    assert "2026-09-11" in text
    assert "2026-10-11" in text


def test_numeric_decisions_ratified() -> None:
    text = (DOCS / "NUMERIC_DECISIONS.md").read_text(encoding="utf-8")
    assert "$10,000" in text
    assert "Research = 3" in text
    assert "approved allocation = 2" in text
    assert "1%" in text
    assert "Soft 5%" in text
    assert "hard 8%" in text
    assert "0.35%" in text
    assert "2.5" in text
    assert "80 / 8" in text
    assert "≤ 50 ms" in text or "<= 50 ms" in text
    assert "1 second" in text
    assert "1-minute aggregate/watermark and 1-minute IGNITION" in text
    assert "Not** a permanent PI invariant" in text or "Not** a permanent" in text or "not a permanent PI invariant" in text.lower()


def test_ignition_grid_is_one_minute() -> None:
    market = (DOCS / "B_MARKET_DATA_CONTRACT.md").read_text(encoding="utf-8")
    detector = (DOCS / "D_DETECTOR_CONTRACT.md").read_text(encoding="utf-8")
    snapshot = _load_fixture("feature_snapshot.example.json")
    evaluate = _load_fixture("detector_evaluate.example.json")
    assert "1 minute" in market
    assert "must not** dictate early detection cadence" in market or "must not dictate early detection cadence" in market
    assert "1-minute" in detector
    assert snapshot["evaluation_grid_seconds"] == 60
    assert evaluate["evaluation_grid_seconds"] == 60
    assert evaluate["detector_family"] == "IGNITION"


def test_pr2_capture_boundary() -> None:
    text = (DOCS / "PR2_CAPTURE_BOUNDARY.md").read_text(encoding="utf-8")
    assert "app/services/alert_governor.py" in text
    assert "evaluate_opportunity_alert()" in text
    assert "record_opportunity_alert()" in text
    assert "release_opportunity_alert_reservation()" in text
    assert "canonical durable ACK" in text
    assert "CONFIRM_OPS_APPLIED" in text
    assert "capture_gap_spool.json" in text
    assert "OPIP_CANONICAL_WRITER_MODE=off" in text
    incident = _load_fixture("incident_lifecycle.example.json")
    boundary = incident["bounded_reminder_policy"]["pr2_boundary"]
    assert boundary["file"] == "app/services/alert_governor.py"
    assert boundary["evaluate"] == "evaluate_opportunity_alert"
    assert boundary["commit"] == "record_opportunity_alert"
    assert boundary["rollback"] == "release_opportunity_alert_reservation"


def test_authority_statements_present() -> None:
    text = _docs_text()
    for statement in AUTHORITY_STATEMENTS:
        assert statement in text


def test_observation_fixture_ordering_fields() -> None:
    observation = _load_fixture("observation.example.json")
    for key in (
        "source_event_time",
        "receipt_time",
        "ingestion_order",
        "source_sequence",
        "history_epoch",
        "local_sequence",
        "schema_version",
        "aggregate_interval_seconds",
    ):
        assert key in observation
    assert observation["aggregate_interval_seconds"] == 60


def test_paper_accounts_are_logically_separate() -> None:
    paper = _load_fixture("paper_outcomes.example.json")
    research = paper["accounts"]["research_simulation"]
    approved = paper["accounts"]["approved_allocation"]
    assert research["starting_equity"] == 10000
    assert approved["starting_equity"] == 10000
    assert research["max_positions"] == 3
    assert approved["max_positions"] == 2
    assert approved["ledger"] == "logically_separate"
    assert set(paper["entry_outcomes"]) == {"NO_FILL", "PARTIAL_FILL", "FULL_FILL"}
    assert set(paper["path_outcomes"]) == {"TARGET", "STOP", "TIMEOUT", "RISK_EXIT"}
    assert paper["horizon"]["anchor"] == "first_fill"
    assert paper["horizon"]["additional_fills_restart_horizon"] is False


def test_export_manifest_verify_before_commit() -> None:
    manifest = _load_fixture("export_manifest.example.json")
    assert manifest["published_and_verified_before_commit"] is True
    assert manifest["consumers_read_committed_manifest_only"] is True
    assert manifest["source_release_sha"] == PIN
    assert manifest["rpo_seconds_provisional"] == 300
    assert manifest["rto_seconds_provisional"] == 1800


def test_this_module_does_not_import_app_runtime() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert import_lines == [
        "from __future__ import annotations",
        "import json",
        "import re",
        "from pathlib import Path",
    ]
