"""Acceptance tests for the R5-0 architecture source adoption.

These tests prove adoption of the owner-supplied v1.4.4 and v1.5.0 architecture
sources and the documentary truth re-baseline. They import no application runtime,
touch no trading, exchange or deployment module, and change no runtime behavior.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import normalize_repo_path, parse_scope_contract  # noqa: E402

INCREMENT = "ATDD-R5-0-architecture-source-adoption"
ARCH = APP_ROOT / "docs" / "architecture"
ATDD = APP_ROOT / "docs" / "atdd"
CONTRACT_PATH = ATDD / "scope-contracts" / f"{INCREMENT}.md"
COMPOSE = APP_ROOT / "docker-compose.yml"
CORE_SERVICE = "ohm-trade-agent"

V143 = ARCH / "v1.4.3"
V143_DOCX = V143 / "OPIP_Profit_Intelligence_Architecture_v1_4_3.docx"
V143_SHA256 = "ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83"

V144 = ARCH / "v1.4.4"
V144_DOCX = V144 / "OPIP_Profit_Intelligence_Architecture_v1_4_4.docx"
V144_SHA256 = "9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768"
V144_BYTES = 70601

V150 = ARCH / "v1.5.0"
V150_DOCX = V150 / "OPIP_Profit_Intelligence_Architecture_v1_5_0.docx"
V150_SHA256 = "69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2"
V150_BYTES = 63023
TRACKER_MD = V150 / "OPIP_Feature_Priorities_v1_5_0.md"
TRACKER_MD_SHA256 = "22f66a737f7a49fec8c2a7652d0324e3f942d8a488746d5a1e581c1921d98b68"
TRACKER_CSV = V150 / "OPIP_Feature_Priorities_v1_5_0.csv"
TRACKER_CSV_SHA256 = "2d7ca0ddfc8164a924333e4ff02fddfca82f4109312089631baa4b7bc9da7354"

ADOPTION_BASELINE = "8b3cc2712432ca21007be4db0667301d48b89d97"
PRIOR_RECONCILED_BASELINE = "a808e84ffc2fea4912cfa5592927d1afe2956568"
HISTORICAL_FEATURE_BUS_LITERAL = "OPIP_FEATURE_BUS_MODE=off"
TRUTH_DOCUMENTS = (
    ARCH / "CURRENT_ARCHITECTURE_STATUS.md",
    ARCH / "OPIP_CONFORMANCE_LEDGER.md",
    ARCH / "OPIP_RECOVERY_ROADMAP.md",
)

#: Every path this increment adds or corrects, as a repository-root path.
ADOPTED_PATHS = (
    "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    "OHM-Trade-Agent-v1/docs/atdd/README.md",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-architecture-source-adoption.md",
    "OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md",
    "OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md",
    "OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md",
    "OHM-Trade-Agent-v1/docs/architecture/v1.4.4/ARCHITECTURE.md",
    "OHM-Trade-Agent-v1/docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx",
    "OHM-Trade-Agent-v1/docs/architecture/v1.4.4/SOURCE.md",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/.gitattributes",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/ARCHITECTURE.md",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.csv",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx",
    "OHM-Trade-Agent-v1/docs/architecture/v1.5.0/SOURCE.md",
    "OHM-Trade-Agent-v1/tests/test_opip_r5_0_architecture_source_adoption.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[start:end]


def _core_environment() -> dict:
    import yaml

    document = yaml.safe_load(_read(COMPOSE))
    environment = document["services"][CORE_SERVICE]["environment"]
    assert isinstance(environment, dict), "core service environment must be a mapping"
    return environment


@pytest.mark.acceptance
def test_ac_001_v1_4_4_source_bytes_and_hash_are_adopted() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-001: the v1.4.4 amendment is adopted byte-for-byte with a recorded identity."""
    assert V144_DOCX.is_file(), V144_DOCX
    assert _digest(V144_DOCX) == V144_SHA256
    assert V144_DOCX.stat().st_size == V144_BYTES
    assert (V144 / "ARCHITECTURE.md").is_file()
    source = _read(V144 / "SOURCE.md")
    assert V144_SHA256 in source
    assert str(V144_BYTES) in source
    assert "docs/architecture/v1.4.4/ARCHITECTURE.md" in source


@pytest.mark.acceptance
def test_ac_002_v1_5_0_source_and_trackers_are_adopted() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-002: the v1.5.0 body and its owner tracker are adopted byte-for-byte."""
    assert V150_DOCX.is_file(), V150_DOCX
    assert _digest(V150_DOCX) == V150_SHA256
    assert V150_DOCX.stat().st_size == V150_BYTES
    assert _digest(TRACKER_MD) == TRACKER_MD_SHA256
    assert _digest(TRACKER_CSV) == TRACKER_CSV_SHA256
    source = _read(V150 / "SOURCE.md")
    for recorded in (V150_SHA256, TRACKER_MD_SHA256, TRACKER_CSV_SHA256):
        assert recorded in source, recorded
    # The tracker copies are owner text bytes, so the v1.5.0 package marks them
    # non-text and git stores those line endings verbatim on every platform.
    attributes = _read(V150 / ".gitattributes")
    for tracker in (TRACKER_MD.name, TRACKER_CSV.name):
        assert f"{tracker} -text" in attributes, tracker
    assert ".gitattributes" in source
    # The tracker is stored as planning input that conveys no authority.
    assert "UNASSIGNED" in source
    assert "NOT_ASSESSED" in source
    assert "No deployment or activation" in _read(TRACKER_MD)


@pytest.mark.acceptance
def test_ac_003_extracted_text_carries_the_source_identity() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-003: each extraction carries its source identity and the DOCX stays the authority."""
    v144 = _read(V144 / "ARCHITECTURE.md")
    assert "Version 1.4.4" in v144
    assert "Focused amendment to v1.4.3 • 4 October 2026" in v144
    assert "Appendix C Source register and adoption record" in v144
    v150 = _read(V150 / "ARCHITECTURE.md")
    assert "Version 1.5.0" in v150
    assert "Continuous Multi-Horizon Capital Intelligence" in v150
    assert "14 Delivery sequence and roadmap continuity" in v150
    assert "19 Sources clause mapping and open inputs" in v150
    for text in (v144, v150):
        assert "The DOCX is the architecture authority" in text
        assert "This extraction does not redesign the architecture." in text


@pytest.mark.acceptance
def test_ac_004_v1_4_3_baseline_is_retained_not_replaced() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-004: the baselined v1.4.3 authority and its pin are unchanged and nothing replaced them."""
    assert V143_DOCX.is_file(), V143_DOCX
    assert _digest(V143_DOCX) == V143_SHA256
    assert V143_SHA256 in _read(V143 / "SOURCE.md")
    assert "Version 1.4.3" in _read(V143 / "ARCHITECTURE.md")
    # Every adopted package is a separate directory beside the retained baseline.
    assert V144_DOCX.is_file() and V150_DOCX.is_file()


@pytest.mark.acceptance
def test_ac_005_truth_documents_record_the_adoption_and_keep_history() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-005: the truth documents name the adopted sources and keep their earlier baselines as history."""
    ledger = _read(ARCH / "OPIP_CONFORMANCE_LEDGER.md")
    status = _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")
    roadmap = _read(ARCH / "OPIP_RECOVERY_ROADMAP.md")
    for text in (ledger, status, roadmap):
        assert "v1.5.0" in text
        assert ADOPTION_BASELINE in text
        assert "preserved as history" in text
    assert "Architecture source: v1.5.0 DOCX pinned in `docs/architecture/v1.5.0/SOURCE.md`" in ledger
    assert "docs/architecture/v1.4.4/SOURCE.md" in ledger
    assert "docs/architecture/v1.5.0/SOURCE.md" in roadmap
    # Earlier baselines, reconciliation dates and superseded statements stay identifiable.
    assert "Reconciled code baseline (R4-B2 status reconciliation, 2026-10-03)" in ledger
    assert "Reconciled code baseline (R4-B2 status reconciliation, 2026-10-03)" in roadmap
    assert PRIOR_RECONCILED_BASELINE in status
    assert "Historical audit base" in status


@pytest.mark.acceptance
def test_ac_006_feature_bus_shadow_posture_is_recorded_with_its_history() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-006: the Feature Bus rows record the shadow posture and keep the off record as history."""
    ledger = _read(ARCH / "OPIP_CONFORMANCE_LEDGER.md")
    f1 = _section(ledger, "## F1", "## F2")
    f2 = _section(ledger, "## F2", "## F3")
    f3 = _section(ledger, "## F3", "## F4")
    for section in (f1, f2, f3):
        assert "`shadow`" in section
        assert "preserved as history" in section
    # The historical literal that the older increments froze is still recorded.
    assert HISTORICAL_FEATURE_BUS_LITERAL in f2
    assert "does not call" in f2
    # The repository-controlled literal behind the corrected wording.
    assert _core_environment()["OPIP_FEATURE_BUS_MODE"] == "shadow"
    status = _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")
    assert "FEATURE BUS EVIDENCE ACTIVATED" in status
    assert "run_cycle` still does not call the Feature Bus" in status


@pytest.mark.acceptance
def test_ac_007_target_spine_is_invoked_shadow_not_a_disabled_gate() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-007: the spine description matches a shadow invocation without authority and is distinct from a disabled gate."""
    status = _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")
    assert "TARGET_SPINE_NO_SNAPSHOT_SOURCE" in status
    assert "rather than a disabled gate" in status
    assert "on every unified cycle" in status
    ledger = _read(ARCH / "OPIP_CONFORMANCE_LEDGER.md")
    assert "rather than a disabled gate" in _section(ledger, "## F3", "## F4")

    # The description matches the runtime, which this increment does not change.
    run_cycle = _read(APP_ROOT / "app" / "jobs" / "run_cycle.py")
    assert '_cycle_phase("TARGET_SPINE")' in run_cycle
    assert "_run_target_spine_fail_open()" in run_cycle
    spine = _read(APP_ROOT / "app" / "services" / "target_spine_cycle.py")
    assert 'TARGET_SPINE_MODES = frozenset({"off", "shadow"})' in spine
    assert 'REASON_NO_SNAPSHOT_SOURCE = "TARGET_SPINE_NO_SNAPSHOT_SOURCE"' in spine
    assert "def target_spine_enabled" in spine


@pytest.mark.acceptance
def test_ac_008_legacy_r5_milestone_is_preserved_under_its_alias() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-008: the earlier R5 milestone keeps its alias and the new R5 program stays unapproved direction."""
    roadmap = _read(ARCH / "OPIP_RECOVERY_ROADMAP.md")
    assert "### R5-LEGACY-OUTCOMES-COCKPIT — Outcome consolidation and the v1.4.3 dashboard" in roadmap
    assert "Preserved tracking alias" in roadmap
    assert "R5-A Market Eye" in roadmap
    assert "is approved by the architecture source adoption" in roadmap
    adopted = _read(V150 / "ARCHITECTURE.md")
    assert "R5-LEGACY-OUTCOMES-COCKPIT" in adopted
    assert "R5-0 contracts" in adopted
    assert "R5-LEGACY-OUTCOMES-COCKPIT" in _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")


@pytest.mark.acceptance
def test_ac_009_adoption_inventory_is_fully_mapped_and_documentary() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-009: every adopted path is mapped, the map is documentation and tests only, and no scope change is requested."""
    pointer = _read(ATDD / "ACTIVE_INCREMENT").strip()
    # The pointer is expected to move later, so it is not pinned here; the scope
    # checker itself enforces that the active pointer matches the checked increment.
    assert (ATDD / "scope-contracts" / f"{pointer}.md").is_file(), pointer

    contract = parse_scope_contract(_read(CONTRACT_PATH))
    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"

    allowed = {
        normalize_repo_path(path)
        for paths in contract.implementation_map.values()
        for path in paths
    }
    retained = {
        "OHM-Trade-Agent-v1/docs/architecture/v1.4.3/ARCHITECTURE.md",
        "OHM-Trade-Agent-v1/docs/architecture/v1.4.3/SOURCE.md",
    }
    assert set(ADOPTED_PATHS) <= allowed, sorted(set(ADOPTED_PATHS) - allowed)
    assert retained <= allowed, sorted(retained - allowed)
    assert allowed == set(ADOPTED_PATHS) | retained
    for path in allowed:
        assert path.startswith(("OHM-Trade-Agent-v1/docs/", "OHM-Trade-Agent-v1/tests/")), path
    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.implementation_map, ac_id
        assert ac_id in contract.traceability, ac_id


@pytest.mark.acceptance
def test_ac_010_adoption_grants_no_runtime_or_trading_authority() -> None:
    """ATDD-R5-0-architecture-source-adoption/AC-010: no posture literal changes and the adoption claims no authority."""
    environment = _core_environment()
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert environment["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert environment["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_COMMITTEE_MODE"] == "off"

    status = _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")
    for marker in (
        "PRODUCTION TRADE AUTHORITY CHANGED BY THIS ADOPTION = NO",
        "FUNDED TRADING ENABLED BY THIS ADOPTION = NO",
        "FEATURE BUS EVIDENCE ACTIVATED BY THIS ADOPTION = NO",
        "PAPER V2 ACTIVATED BY THIS ADOPTION = NO",
        "COMMITTEE ACTIVATED BY THIS ADOPTION = NO",
    ):
        assert marker in status, marker
    for forbidden in ("PAPER V2 ACTIVATED = YES", "FEATURE BUS ACTIVATED = YES"):
        assert forbidden not in status, forbidden

    contract = parse_scope_contract(_read(CONTRACT_PATH))
    allowed = {
        normalize_repo_path(path)
        for paths in contract.implementation_map.values()
        for path in paths
    }
    for path in allowed:
        assert not path.startswith(
            (
                "OHM-Trade-Agent-v1/app/",
                "OHM-Trade-Agent-v1/deploy/",
                "OHM-Trade-Agent-v1/.github/",
                ".github/",
            )
        ), path
        assert not path.endswith(("docker-compose.yml", ".env")), path



