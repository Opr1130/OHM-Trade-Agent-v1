"""Acceptance checks for the v6 root design-note archive (v6 slice B).

`docs/V6_AUDIT.md` section 10 (retirement theme R1) found about 28 design notes
sitting at the application root and proposed moving them into `docs/archive/`
with an index, keeping the README, the safety contracts and anything that code or
tests actually read. These checks pin that move: every archived note lives in the
archive and is listed in the index, and no document that code or tests read was
moved out from under them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = APP_ROOT / "docs" / "archive"
INDEX = ARCHIVE / "INDEX.md"

#: Root design notes this increment moved into ``docs/archive/``.
ARCHIVED_NOTES = (
    "OHM_P1_PHASE3C_IMPLEMENTATION.md",
    "OHM_P1_TARGET_ARCHITECTURE.md",
    "OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md",
    "OPIP_BUILD0_VERIFICATION.md",
    "OPIP_DECISION_ENGINE_5_2A.md",
    "OPIP_DECISION_ENGINE_V1.md",
    "OPIP_DECISION_ENGINE_V2.md",
    "OPIP_EVENT_INTELLIGENCE_V1.md",
    "OPIP_REALTIME_MARKET_INTELLIGENCE_4_2.md",
    "OPIP_REALTIME_MARKET_INTELLIGENCE_4_3_4_5.md",
    "OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md",
    "OPIP_SEQUENCE5_WAVE_A1.md",
    "OPIP_SEQUENCE5_WAVE_A2.md",
    "OPIP_SEQUENCE5_WAVE_A3.md",
    "OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md",
    "OPIP_TWO_STAGE_ADVISORY_AI_V1.md",
    "PRICE_MOVEMENT_RADAR.md",
    "SIGNAL_QUALITY_PHASE2.md",
    "SIGNAL_QUALITY_PHASE3B.md",
    "SIGNAL_QUALITY_V1.md",
)

#: Root notes kept in place: the README, the safety contracts and every note
#: that application code or a test still reads from the application root.
RETAINED_NOTES = (
    "README.md",
    "AI_EXECUTION_GATEWAY.md",
    "OHM_P1_PROMOTION_GATES.md",
    "AUTONOMOUS_BUILD_REPORT.md",
    "SIGNAL_QUALITY_PHASE3A.md",
    "TRADEVISOR_V2.md",
    "TRADINGVIEW_WAVE8_2.md",
    "WAVES_3_7_COMPLETION.md",
    "WAVE_CHECKPOINTS.md",
)

#: Notes read by name from the application root (`Path("NOTE.md")`).
ROOT_READ_NOTES = (
    "AUTONOMOUS_BUILD_REPORT.md",
    "SIGNAL_QUALITY_PHASE3A.md",
    "TRADEVISOR_V2.md",
    "TRADINGVIEW_WAVE8_2.md",
    "WAVES_3_7_COMPLETION.md",
    "WAVE_CHECKPOINTS.md",
)

_CITED_NOTE = re.compile(r"(?<![\w/.\-])([A-Za-z0-9_]+\.md)")
_INDEX_ENTRY = re.compile(r"\]\(([A-Za-z0-9_]+\.md)\)")
_SKIPPED_DIRS = frozenset({"__pycache__", ".git", "node_modules"})
#: This module is the archive manifest itself, so it names every archived note on
#: purpose. It is the one source excluded from the "no old-path citation" scan.
_MANIFEST_MODULE = "tests/test_v6_archive_root_notes_v1.py"


def _python_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for directory in ("app", "tests"):
        for path in sorted((APP_ROOT / directory).rglob("*.py")):
            if _SKIPPED_DIRS.intersection(path.parts):
                continue
            relative = path.relative_to(APP_ROOT).as_posix()
            if relative == _MANIFEST_MODULE:
                continue
            sources[relative] = path.read_text(encoding="utf-8")
    return sources


def _indexed_notes() -> set[str]:
    return set(_INDEX_ENTRY.findall(INDEX.read_text(encoding="utf-8")))


@pytest.mark.acceptance
def test_archived_notes_live_under_docs_archive_and_are_indexed() -> None:
    """ATDD-OPIP-V6-ARCHIVE-NOTES-v1/AC-001"""
    assert INDEX.is_file(), "docs/archive/INDEX.md is missing"
    indexed = _indexed_notes()

    for name in ARCHIVED_NOTES:
        assert (ARCHIVE / name).is_file(), f"docs/archive/{name} is missing"
        assert not (APP_ROOT / name).exists(), f"{name} was not moved"
        assert name in indexed, f"{name} is not listed in docs/archive/INDEX.md"

    # The index is complete: nothing in the archive is an unlisted loose note.
    present = {path.name for path in ARCHIVE.glob("*.md")} - {INDEX.name}
    assert set(ARCHIVED_NOTES) <= present
    for name in sorted(present):
        assert name in indexed, f"{name} is not listed in docs/archive/INDEX.md"


@pytest.mark.acceptance
def test_no_cited_document_is_broken_by_the_move() -> None:
    """ATDD-OPIP-V6-ARCHIVE-NOTES-v1/AC-002"""
    sources = _python_sources()
    assert sources, "no application or test sources were found"

    # 1. No application module or test still cites an archived note at its old
    #    root path; a citation has to name the archive location instead.
    for relative, text in sources.items():
        for number, line in enumerate(text.splitlines(), start=1):
            for name in ARCHIVED_NOTES:
                if name not in line:
                    continue
                assert "docs/archive/" in line, (
                    f"{relative}:{number} cites {name} at the moved root path"
                )

    # 2. Every note that code or tests read by name still resolves at the
    #    application root, which is what `Path("NOTE.md")` reads depend on.
    cited = {match for text in sources.values() for match in _CITED_NOTE.findall(text)}
    for name in ROOT_READ_NOTES:
        assert name in cited, f"{name} is no longer cited by code or tests"
        assert (APP_ROOT / name).is_file(), f"{name} no longer resolves at the root"

    # 3. The retained set as a whole stays at the root, including the README and
    #    the safety contracts that the repository guardrails name.
    for name in RETAINED_NOTES:
        assert (APP_ROOT / name).is_file(), f"{name} no longer resolves at the root"

    # 4. Documentation links that point at archived or retained notes resolve, so
    #    the move leaves no dangling reference behind.
    for relative in ("README.md", "docs/archive/INDEX.md"):
        document = APP_ROOT / relative
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (document.parent / target).resolve().exists(), (
                f"{relative} links to a missing document: {target}"
            )
