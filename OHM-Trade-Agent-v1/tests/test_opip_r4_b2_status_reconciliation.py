"""Architecture status reconciliation (R4-B2 Slice 3B).

Proves the stale status/recovery documents are reconciled to current accepted code
without claiming runtime activation, that historical statements remain identifiable
as history, and that the architecture authority bytes are untouched.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_ARCH = _ROOT / "docs" / "architecture"
_LEDGER = _ARCH / "OPIP_CONFORMANCE_LEDGER.md"
_ROADMAP = _ARCH / "OPIP_RECOVERY_ROADMAP.md"
_STATUS = _ARCH / "CURRENT_ARCHITECTURE_STATUS.md"
_DOCX = _ARCH / "v1.4.3" / "OPIP_Profit_Intelligence_Architecture_v1_4_3.docx"

#: The recorded authority SHA256 of the v1.4.3 DOCX.
_AUTHORITY_SHA256 = "ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83"


@pytest.mark.acceptance
def test_ac_029_status_docs_are_reconciled_without_activation() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-029: the status docs are reconciled to current code, preserve history, use existing vocabulary, and claim no activation."""
    ledger = _LEDGER.read_text(encoding="utf-8")
    roadmap = _ROADMAP.read_text(encoding="utf-8")
    status = _STATUS.read_text(encoding="utf-8")

    # Reconciled to the current code baseline and to the shadow target modules.
    assert "Reconciled code baseline (R4-B2 status reconciliation, 2026-10-03)" in ledger
    assert "app/opip/detectors/ignition.py" in ledger
    assert "app/opip/forecast.py" in ledger
    # SHADOW is the vocabulary for an implemented non-authoritative module.
    assert "`SHADOW`" in ledger

    # The stale status is corrected (the old wording survives only as quoted history).
    assert "| R3 — F3 through F7 on the R2 evidence | implemented (shadow / dormant" in status
    assert "### R3 — F3 through F7 on that evidence (implemented; shadow / dormant)" in roadmap
    assert "| IMPLEMENTATION_STATUS | `SHADOW` |" in ledger

    # Historical statements remain identifiable as history.
    for text in (ledger, roadmap, status):
        assert "preserved as history" in text

    # No document claims activation or authority from code implementation.
    for text in (ledger, roadmap, status):
        assert "PAPER V2 ACTIVATED = YES" not in text
        assert "FEATURE BUS ACTIVATED = YES" not in text
        assert "PRODUCTION TRADE AUTHORITY CHANGED = YES" not in text
        assert "FUNDED TRADING ENABLED = YES" not in text
    assert "PAPER V2 ACTIVATED BY THIS RECONCILIATION = NO" in status
    assert "FUNDED TRADING ENABLED = NO" in status


@pytest.mark.acceptance
def test_ac_029_architecture_authority_is_untouched() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-029: the reconciliation modifies no architecture authority; the v1.4.3 DOCX bytes still hash to the recorded value."""
    assert _DOCX.is_file()
    digest = hashlib.sha256(_DOCX.read_bytes()).hexdigest()
    assert digest == _AUTHORITY_SHA256
