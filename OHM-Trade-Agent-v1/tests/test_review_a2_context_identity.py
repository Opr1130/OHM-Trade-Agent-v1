"""Red-first regression for the A2 review finding (lost-ack idempotent recovery)."""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.services.capital_admission_integration import (  # noqa: E402
    CAPITAL_ADMISSION_REFUSED,
    CAPITAL_ADMISSION_INPUT_INVALID,
    CapitalAdmissionDisposition,
)


@pytest.mark.acceptance
def test_a2_disposition_exposes_the_committed_context_identity() -> None:
    """ATDD-R5-E-capital-admission-integration/AC-012: the durable artifact must carry the context identity a retry must reuse."""

    disposition = CapitalAdmissionDisposition(
        status=CAPITAL_ADMISSION_REFUSED,
        reason=CAPITAL_ADMISSION_INPUT_INVALID,
        detail="probe",
        disposition_id="disp-1",
        decision_context_id="ctx-1",
    )
    assert disposition.decision_context_id == "ctx-1"
    assert disposition.to_dict()["decision_context_id"] == "ctx-1"
    # A refusal without a commit honestly reports no context identity.
    bare = CapitalAdmissionDisposition(
        status=CAPITAL_ADMISSION_REFUSED,
        reason=CAPITAL_ADMISSION_INPUT_INVALID,
        detail="probe",
        disposition_id="disp-2",
    )
    assert bare.decision_context_id is None
    assert bare.to_dict()["decision_context_id"] is None
