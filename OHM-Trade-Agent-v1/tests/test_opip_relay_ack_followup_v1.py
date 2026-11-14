"""Regression tests for the two Codex P2 findings on merged PR #577.

Each test cites its criterion in ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1. The relay stays
communication-only and is not activated by this change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools import opip_relay as relay  # noqa: E402
from tests.test_opip_engineering_relay_v1 import OWNER, TS, Wire, comment  # noqa: E402

pytestmark = pytest.mark.acceptance
ME = "claude-engineering"
TO = "claude-cline-engineering"
REQUESTER = "chatgpt-github-review"


def _pass(comments, state_path, wire):
    state = relay.State(state_path)
    out = relay.process(comments, allowed_actor_ids=frozenset({OWNER}), state=state, post=wire, me=ME,
                        current_head=None, runtime_summary="s", recipients=frozenset({TO}))
    return out, state


def _published(body: str, cid: int) -> dict:
    return {"id": cid, "user": {"id": OWNER}, "created_at": TS, "updated_at": TS, "body": body}


def _ack_comment(request_id: str, cid: int, **overrides) -> dict:
    """A hand-built comment shaped like an ACK for ``request_id`` (fields overridable)."""
    fields = {"message_id": f"{request_id}-ack", "sender": ME, "recipient": REQUESTER, "kind": "ACK",
              "reply_to": request_id, "authority": "COMMUNICATION_ONLY", "requested_action": "none"}
    fields.update(overrides)
    lines = [f"`{k}: {v}`" for k, v in fields.items() if v != ""]
    return _published("### OPIP-V6 / ACK\n" + "\n".join(lines) + "\n", cid)


@pytest.mark.parametrize("length", [89, 90, 91, 92])
def test_ac_001_a_full_length_ack_can_be_read_back_so_a_restart_does_not_duplicate_it(tmp_path, length):
    """ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1/AC-001: request ids of 89-92 chars yield 93-96 char ACK ids that must still parse."""
    request_id = "a" + "b" * (length - 1)
    request = comment(mid=request_id)
    wire1 = Wire()
    _pass([request], tmp_path / "lost.json", wire1)
    assert len(wire1.posted) == 1
    ack_id = f"{request_id}-ack"
    assert 93 <= len(ack_id) <= 96
    wire2 = Wire()
    out, state = _pass([request, _published(wire1.posted[0], 99)], tmp_path / "fresh.json", wire2)
    assert wire2.posted == []  # no second ACK after the restart
    assert state.seen[request_id] == "REPLIED"
    assert [o.status for o in out if o.message_id == request_id] == ["DUPLICATE"]


def test_ac_001_the_request_limit_is_unchanged_and_acks_may_use_the_full_limit(tmp_path):
    """ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1/AC-001: a 93-char REQUEST is still refused; a 96-char ACK id is accepted."""
    wire = Wire()
    out, _ = _pass([comment(mid="a" + "b" * 92)], tmp_path / "s.json", wire)
    assert [o.detail for o in out] == ["BAD_MESSAGE_ID"] and wire.posted == []
    env = relay.parse_comment(_ack_comment("a" + "b" * 91, 5), allowed_actor_ids=frozenset({OWNER}))
    assert len(env.message_id) == 96


@pytest.mark.parametrize("label,overrides", [
    ("REVIEW_RESULT with the ack id", {"kind": "REVIEW_RESULT"}),
    ("DECISION_REQUEST with the ack id", {"kind": "DECISION_REQUEST"}),
    ("ACK from someone else", {"sender": "not-the-relay"}),
    ("ACK addressed to a different requester", {"recipient": "someone-else"}),
    ("ACK naming a different request", {"reply_to": "opip-v6-other-request-9999"}),
    ("ACK with no reply_to", {"reply_to": ""}),
])
def test_ac_002_an_unverified_comment_with_the_ack_id_does_not_suppress_the_real_ack(tmp_path, label, overrides):
    """ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1/AC-002: only a genuine ACK from this relay, back to the requester, naming this request, counts as answered."""
    request = comment()
    impostor = _ack_comment("opip-v6-test-0001", 7, **overrides)
    wire = Wire()
    out, state = _pass([request, impostor], tmp_path / "s.json", wire)
    assert len(wire.posted) == 1, label  # the legitimate acknowledgement is still sent
    assert [o.status for o in out if o.message_id == "opip-v6-test-0001"] == ["REPLIED"]
    assert state.seen["opip-v6-test-0001"] == "REPLIED"


def test_ac_002_a_genuine_published_ack_still_suppresses_a_duplicate(tmp_path):
    """ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1/AC-002: the crash-recovery guarantee from #577 is kept."""
    request = comment()
    genuine = _ack_comment("opip-v6-test-0001", 8)
    wire = Wire()
    out, _ = _pass([request, genuine], tmp_path / "s.json", wire)
    assert wire.posted == []
    assert [o.status for o in out if o.message_id == "opip-v6-test-0001"] == ["DUPLICATE"]
