"""Regression tests for the five defects found in the merged engineering relay (#570).

Each test cites its criterion in ATDD-OPIP-RELAY-REMEDIATION-V1. The relay stays
communication-only: nothing here widens what it may do, each fix makes it refuse or
dedupe more.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools import opip_relay as relay  # noqa: E402
from tests.test_opip_engineering_relay_v1 import ACTION, HEAD, OWNER, TS, Wire, comment  # noqa: E402

pytestmark = pytest.mark.acceptance
ME = "claude-engineering"
TO = "claude-cline-engineering"


def _pass(comments, state_path, wire, *, head=None, recipients=frozenset({TO})):
    state = relay.State(state_path)
    out = relay.process(comments, allowed_actor_ids=frozenset({OWNER}), state=state, post=wire, me=ME,
                        current_head=head, runtime_summary="s", recipients=recipients)
    return out, state


def _as_comment(body: str, cid: int) -> dict:
    return {"id": cid, "user": {"id": OWNER}, "created_at": TS, "updated_at": TS, "body": body}


def test_ac_001_a_crash_between_post_and_record_does_not_send_a_second_ack(tmp_path):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-001: the published ACK is remote evidence; a lost local record must not duplicate it."""
    request = comment()
    wire1 = Wire()
    _pass([request], tmp_path / "lost-on-crash.json", wire1)
    assert len(wire1.posted) == 1
    published = _as_comment(wire1.posted[0], 99)
    # Restart with the state file gone (the crash happened before record()).
    wire2 = Wire()
    out, state = _pass([request, published], tmp_path / "fresh-state.json", wire2)
    assert wire2.posted == []
    assert [o.status for o in out if o.message_id == "opip-v6-test-0001"] == ["DUPLICATE"]
    assert state.seen["opip-v6-test-0001"] == "REPLIED"


class _Pages:
    """Fake `gh api` serving a long thread by page and recording posts."""

    def __init__(self, comments):
        self.comments, self.posts, self.gets = comments, [], []

    def __call__(self, args, stdin=None):
        if stdin is not None:
            self.posts.append(stdin)
            return "{}"
        url = args[0]
        self.gets.append(url)
        page = int(url.split("&page=")[1])
        return json.dumps(self.comments[(page - 1) * 100 : page * 100])


def test_ac_002_a_request_beyond_the_first_100_comments_is_found(tmp_path, monkeypatch):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-002: every page is read, so a request after comment 100 is answered."""
    filler = [comment(cid=i, mid=f"opip-v6-fill-{i:05d}", kind="REVIEW_RESULT") for i in range(1, 231)]
    request = comment(cid=500, mid="opip-v6-late-request-0001")
    gh = _Pages(filler + [request])
    monkeypatch.setattr(relay, "_gh", gh)
    code = relay.main(["--issue", "569", "--owner-id", str(OWNER), "--state", str(tmp_path / "s.json"),
                       "--me", ME, "--recipients", TO])
    assert code == 0
    assert len(gh.gets) == 3  # 100 + 100 + 31
    assert len(gh.posts) == 1 and "reply_to: opip-v6-late-request-0001" in gh.posts[0]


def test_ac_002_a_thread_longer_than_the_page_bound_fails_closed(tmp_path, monkeypatch):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-002: never act on a partial thread."""
    full = [comment(cid=i, mid=f"opip-v6-fill-{i:05d}", kind="REVIEW_RESULT") for i in range(1, 101)]
    monkeypatch.setattr(relay, "_gh", lambda args, stdin=None: json.dumps(full))
    assert relay.main(["--issue", "1", "--owner-id", str(OWNER), "--state", str(tmp_path / "s.json"), "--me", ME]) == 3
    assert not (tmp_path / "s.json").exists()
    with pytest.raises(RuntimeError):
        relay.fetch_comments("o/r", 1, gh=lambda args: json.dumps(full), max_pages=3)


def test_ac_003_a_message_for_another_recipient_is_not_answered_or_consumed(tmp_path):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-003: recipient is enforced, and the message stays available to its real recipient."""
    wire = Wire()
    out, state = _pass([comment()], tmp_path / "s.json", wire, recipients=frozenset({"someone-else"}))
    assert wire.posted == []
    assert [o.status for o in out] == ["NOT_FOR_US"]
    assert "opip-v6-test-0001" not in state.seen
    # Default recipients is this relay's own name only.
    wire2 = Wire()
    out2, _ = _pass([comment()], tmp_path / "s2.json", wire2, recipients=None)
    assert wire2.posted == [] and [o.status for o in out2] == ["NOT_FOR_US"]


@pytest.mark.parametrize("head,current,expected", [
    (HEAD, None, "STALE"),            # named head, nothing to verify against: fail closed
    (HEAD, "b" * 40, "STALE"),        # differs
    (HEAD, HEAD, "REPLIED"),          # verified
    ("", None, "REPLIED"),            # no head named: nothing to verify
])
def test_ac_004_a_named_head_must_be_verified_or_the_request_is_stale(tmp_path, head, current, expected):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-004: an unverifiable head is treated as stale, never accepted."""
    extra = f"\n`head_sha: {head}`" if head else ""
    wire = Wire()
    out, _ = _pass([comment(extra=extra)], tmp_path / "s.json", wire, head=current)
    assert [o.status for o in out] == [expected]
    assert len(wire.posted) == (1 if expected == "REPLIED" else 0)


def test_ac_005_the_longest_accepted_id_still_gets_an_ack_and_nothing_crashes(tmp_path):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-005: inbound ids are capped so '<id>-ack' always fits."""
    longest = "a" + "b" * 91  # 92 characters
    wire = Wire()
    out, _ = _pass([comment(mid=longest)], tmp_path / "s.json", wire)
    assert [o.status for o in out] == ["REPLIED"]
    assert f"message_id: {longest}-ack" in wire.posted[0] and len(longest) + 4 <= 96


def test_ac_005_an_id_too_long_to_acknowledge_is_rejected_at_parse(tmp_path):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-005: a 93-character id is refused inbound instead of crashing the reply."""
    too_long = "a" + "b" * 92
    wire = Wire()
    out, _ = _pass([comment(mid=too_long)], tmp_path / "s.json", wire)
    assert [o.detail for o in out] == ["BAD_MESSAGE_ID"] and wire.posted == []


def test_ac_005_a_reply_that_cannot_be_built_is_recorded_not_raised(tmp_path, monkeypatch):
    """ATDD-OPIP-RELAY-REMEDIATION-V1/AC-005: defence in depth, a build failure becomes a REJECTED outcome."""
    monkeypatch.setattr(relay, "build_envelope", lambda **kw: (_ for _ in ()).throw(ValueError("x")))
    wire = Wire()
    out, state = _pass([comment()], tmp_path / "s.json", wire)
    assert [o.status for o in out] == ["REJECTED"] and wire.posted == []
    assert state.seen["opip-v6-test-0001"] == "REJECTED"
