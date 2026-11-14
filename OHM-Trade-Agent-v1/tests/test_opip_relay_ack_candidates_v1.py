"""Regression tests for the Codex P2 finding on merged PR #578 (same-id ACK candidates).

Each test cites its criterion in ATDD-OPIP-RELAY-ACK-CANDIDATES-V1. The relay stays
communication-only and is not activated by this change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.test_opip_engineering_relay_v1 import OWNER, Wire, comment  # noqa: E402
from tests.test_opip_relay_ack_followup_v1 import (  # noqa: E402
    REQUESTER, _ack_comment, _pass, _published)

pytestmark = pytest.mark.acceptance
REQUEST_ID = "req-candidates-1"


def _decoys(base_id: int) -> list[dict]:
    """Comments that carry the ACK id but are not a genuine ACK for this requester."""
    return [
        _ack_comment(REQUEST_ID, base_id, sender="someone-else"),
        _ack_comment(REQUEST_ID, base_id + 1, recipient="not-the-requester"),
        _ack_comment(REQUEST_ID, base_id + 2, reply_to="another-request"),
    ]


@pytest.mark.parametrize("decoys_first", [True, False])
def test_ac_001_a_genuine_ack_survives_same_id_decoys_in_either_order(tmp_path, decoys_first):
    """ATDD-OPIP-RELAY-ACK-CANDIDATES-V1/AC-001: a genuine ACK is not shadowed by same-id decoys listed before or after it."""
    request = comment(mid=REQUEST_ID, sender=REQUESTER)
    wire1 = Wire()
    _pass([request], tmp_path / "lost.json", wire1)
    assert len(wire1.posted) == 1
    genuine = _published(wire1.posted[0], 50)
    decoys = _decoys(60)
    listed = decoys + [genuine] if decoys_first else [genuine] + decoys
    wire2 = Wire()
    out, state = _pass([request, *listed], tmp_path / "fresh.json", wire2)
    assert wire2.posted == []
    assert state.seen[REQUEST_ID] == "REPLIED"
    assert [o.status for o in out if o.message_id == REQUEST_ID] == ["DUPLICATE"]


def test_ac_002_only_decoys_with_the_ack_id_never_suppress_the_real_ack(tmp_path):
    """ATDD-OPIP-RELAY-ACK-CANDIDATES-V1/AC-002: several same-id comments, none genuine, leave the request unanswered."""
    request = comment(mid=REQUEST_ID, sender=REQUESTER)
    wire = Wire()
    out, state = _pass([request, *_decoys(70)], tmp_path / "s.json", wire)
    assert len(wire.posted) == 1
    assert state.seen[REQUEST_ID] == "REPLIED"
    assert [o.status for o in out if o.message_id == REQUEST_ID] == ["REPLIED"]
