"""ATDD: owner authorization state machine (approval, revocation, re-approval).

Covers the P1 fail-open defect where a later owner revocation was ignored and an
older still-valid attestation was returned as PASS.
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.services.learning_recovery_attestation import (
    AttestationError,
    select_from_comments,
)

NOW = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
PRODUCTION = "a" * 40
TOOLING = "b" * 40
OTHER_TOOLING = "c" * 40
OWNER = "Opr1130"
PATHS = [
    "app/jobs/build_phase3c_forward_outcomes.py",
    "app/opip/discovery/maturation.py",
    "app/services/opportunity_accountability.py",
]
HASHES = {path: hashlib.sha256(path.encode()).hexdigest() for path in PATHS}
DIGEST = hashlib.sha256(
    "\n".join(f"{path}:{HASHES[path]}" for path in sorted(PATHS)).encode()
).hexdigest()


def _compat() -> dict:
    return {
        "schema": "opip-learning-recovery-compatibility/v1",
        "status": "REFUSED",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_critical_paths": PATHS,
        "unreviewed_changes": PATHS,
        "tooling_sha256": HASHES,
    }


def _attestation(**overrides) -> dict:
    payload = {
        "schema": "opip-learning-recovery-attestation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_paths": PATHS,
        "path_digests": HASHES,
        "diff_sha256": DIGEST,
        "owner_login": OWNER,
        "approved_at_utc": "2026-10-22T00:59:00+00:00",
        "record_identity": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-1",
        "rationale": "Reviewed lineage-bound consumer guard; no trading authority change.",
        "ci": {
            "commit_sha": TOOLING,
            "pytest": "success",
            "quality_security": "success",
            "semgrep": "success",
        },
    }
    payload.update(overrides)
    return payload


def _approval_body(**overrides) -> str:
    return "<!-- opip-recovery-attestation:v1 " + json.dumps(_attestation(**overrides)) + " -->"


def _revocation_body(**overrides) -> str:
    payload = {
        "schema": "opip-learning-recovery-revocation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "reason": "owner withdrew approval",
    }
    payload.update(overrides)
    return "<!-- opip-recovery-revocation:v1 " + json.dumps(payload) + " -->"


def _comment(login, body, created_at, number=1, ident=None):
    comment = {
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
        "html_url": f"https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-{number}",
    }
    if ident is not None:
        comment["id"] = ident
    return comment


def _select(comments):
    return select_from_comments(
        comments, compatibility=_compat(), owner_login=OWNER, now=NOW
    )


# ---------------------------------------------------------------- approvals


@pytest.mark.acceptance
def test_t01_valid_exact_owner_attestation_is_accepted():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T01: a well-formed owner attestation for the exact pair is admitted."""
    manifest = _select([_comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z")])
    assert manifest["status"] == "PASS"
    assert manifest["approved_paths"] == sorted(PATHS)
    assert manifest["unreviewed_changes"] == []


@pytest.mark.acceptance
def test_t02_wrong_actor_is_rejected():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T02: non-owner and bot comments can never authorize."""
    for login in ("attacker", "github-actions[bot]", "dependabot[bot]", ""):
        with pytest.raises(AttestationError, match="no owner-authored attestation"):
            _select([_comment(login, _approval_body(), "2026-10-22T00:59:00Z")])


@pytest.mark.acceptance
def test_t02b_owner_identity_inside_body_is_not_trusted():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T02b: a self-declared owner_login in the body cannot substitute for authorship."""
    with pytest.raises(AttestationError, match="no owner-authored attestation"):
        _select(
            [
                _comment(
                    "attacker", _approval_body(owner_login=OWNER), "2026-10-22T00:59:00Z"
                )
            ]
        )


# --------------------------------------------------------------- revocation


@pytest.mark.acceptance
def test_t12_original_revocation_fail_open_reproduction_now_holds():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T12: the original defect - a later owner revocation must HOLD, never PASS."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(OWNER, "REVOKED: do not proceed", "2026-10-22T00:59:30Z", 2),
    ]
    with pytest.raises(AttestationError, match="revoked"):
        _select(comments)


@pytest.mark.acceptance
def test_t03_structured_revocation_supersedes_approval():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T03: a structured owner revocation supersedes an earlier approval."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(OWNER, _revocation_body(), "2026-10-22T00:59:30Z", 2),
    ]
    with pytest.raises(AttestationError, match="revoked"):
        _select(comments)


@pytest.mark.acceptance
def test_t03b_plaintext_revocation_is_honoured():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T03b: the documented plaintext revocation form must be honoured."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(OWNER, "REVOKED: do not proceed", "2026-10-22T00:59:30Z", 2),
    ]
    with pytest.raises(AttestationError, match="plaintext owner revocation"):
        _select(comments)


@pytest.mark.acceptance
def test_t04_non_owner_revocation_cannot_modify_authorization():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T04: an attacker's revocation comment has no authority."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment("attacker", "REVOKED: do not proceed", "2026-10-22T00:59:30Z", 2),
        _comment("attacker", _revocation_body(), "2026-10-22T00:59:40Z", 3),
    ]
    assert _select(comments)["status"] == "PASS"


@pytest.mark.acceptance
def test_t05_unrelated_owner_comment_does_not_revoke():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T05: ordinary owner discussion is not a revocation."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(
            OWNER,
            "Thanks, I reviewed the diff and it looks good.",
            "2026-10-22T00:59:30Z",
            2,
        ),
        _comment(OWNER, "REVOKED", "2026-10-22T00:59:40Z", 3),
    ]
    assert _select(comments)["status"] == "PASS"


@pytest.mark.acceptance
def test_t06_revocation_for_another_pair_does_not_affect_this_pair():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T06: scoped revocations must not leak across SHA pairs."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(
            OWNER, _revocation_body(tooling_sha=OTHER_TOOLING), "2026-10-22T00:59:30Z", 2
        ),
    ]
    assert _select(comments)["status"] == "PASS"


@pytest.mark.acceptance
def test_t07_malformed_newer_applicable_event_fails_closed_without_fallback():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T07: an unreadable later applicable event must not fall back to an older approval."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(
            OWNER,
            "<!-- opip-recovery-attestation:v1 {not json -->",
            "2026-10-22T00:59:30Z",
            2,
        ),
    ]
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select(comments)


@pytest.mark.acceptance
def test_t07b_malformed_newer_revocation_fails_closed():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T07b: a malformed revocation is still a refusal, not an admission."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(
            OWNER,
            "<!-- opip-recovery-revocation:v1 not-json -->",
            "2026-10-22T00:59:30Z",
            2,
        ),
    ]
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select(comments)


@pytest.mark.acceptance
def test_t07c_invalid_newer_approval_does_not_fall_back_to_valid_older():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T07c: a newer invalid applicable approval supersedes the older valid one."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(OWNER, _approval_body(diff_sha256="f" * 64), "2026-10-22T00:59:30Z", 2),
    ]
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select(comments)


@pytest.mark.acceptance
def test_t08_fresh_reapproval_after_revocation_restores_authorization():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T08: an authenticated fresh re-approval after revocation is valid again."""
    comments = [
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1),
        _comment(OWNER, "REVOKED: do not proceed", "2026-10-22T00:59:30Z", 2),
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:50Z", 3),
    ]
    manifest = _select(comments)
    assert manifest["status"] == "PASS"
    assert manifest["attestation"]["record_identity"].endswith("issuecomment-3")


# ---------------------------------------------------------------- ambiguity


@pytest.mark.acceptance
def test_t09_timestamp_ambiguity_refuses_authorization():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T09: unorderable events must fail closed."""
    same = "2026-10-22T00:59:00Z"
    comments = [
        _comment(OWNER, _approval_body(), same, 1),
        _comment(OWNER, "REVOKED: do not proceed", same, 2),
    ]
    with pytest.raises(AttestationError, match="ambiguous authorization event ordering"):
        _select(comments)


@pytest.mark.acceptance
def test_t09b_identical_timestamps_are_ordered_by_comment_id():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T09b: a deterministic GitHub comment id resolves the tie."""
    same = "2026-10-22T00:59:00Z"
    comments = [
        _comment(OWNER, _approval_body(), same, 1, ident=900),
        _comment(OWNER, "REVOKED: do not proceed", same, 2, ident=901),
    ]
    with pytest.raises(AttestationError, match="revoked"):
        _select(comments)


@pytest.mark.acceptance
def test_t09c_missing_timestamp_fails_closed():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T09c: an applicable event without a timestamp cannot be ordered safely."""
    comment = _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z")
    comment["created_at"] = ""
    with pytest.raises(AttestationError):
        _select([comment])


@pytest.mark.acceptance
def test_t09d_missing_authenticated_record_identity_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T09d: record identity must come from authenticated comment metadata."""
    comment = _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z")
    del comment["html_url"]
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select([comment])


# ------------------------------------------------------- exactness coverage


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "override",
    [
        {"production_sha": "e" * 40},
        {"tooling_sha": OTHER_TOOLING},
        {"diff_sha256": "f" * 64},
        {"approved_at_utc": "2026-01-01T00:00:00+00:00"},
    ],
    ids=["wrong-production-sha", "wrong-tooling-sha", "wrong-digest", "stale-approval"],
)
def test_t23_exactness_overrides_are_refused(override):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T23-T26: pair, digest and freshness are all enforced."""
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select([_comment(OWNER, _approval_body(**override), "2026-10-22T00:59:00Z")])


@pytest.mark.acceptance
def test_t26_ci_failure_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T26: missing or failed CI evidence blocks admission."""
    payload = _attestation()
    payload["ci"]["semgrep"] = "failure"
    body = "<!-- opip-recovery-attestation:v1 " + json.dumps(payload) + " -->"
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select([_comment(OWNER, body, "2026-10-22T00:59:00Z")])


@pytest.mark.acceptance
def test_wrong_path_hash_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: A single mismatched per-path hash refuses the whole attestation."""
    digests = dict(HASHES)
    digests[PATHS[0]] = "0" * 64
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select(
            [
                _comment(
                    OWNER,
                    _approval_body(path_digests=digests),
                    "2026-10-22T00:59:00Z",
                )
            ]
        )


@pytest.mark.acceptance
def test_broader_path_scope_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: A wildcard or additional path is never approved."""
    with pytest.raises(AttestationError, match="no valid owner attestation"):
        _select(
            [
                _comment(
                    OWNER,
                    _approval_body(
                        changed_paths=PATHS + ["app/services/learning_derived_state.py"]
                    ),
                    "2026-10-22T00:59:00Z",
                )
            ]
        )


@pytest.mark.acceptance
def test_backward_compatible_approval_only_history_still_passes():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: Existing approval-only histories keep their original semantics."""
    comments = [
        _comment(OWNER, _approval_body(diff_sha256="e" * 64), "2026-10-22T00:58:00Z", 1),
        _comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 2),
        _comment("attacker", _approval_body(), "2026-10-22T00:59:30Z", 3),
    ]
    manifest = _select(comments)
    assert manifest["status"] == "PASS"
    assert manifest["attestation"]["record_identity"].endswith("issuecomment-2")



