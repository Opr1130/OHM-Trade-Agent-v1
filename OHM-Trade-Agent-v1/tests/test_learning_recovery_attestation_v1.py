import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from app.services.learning_recovery_attestation import (
    AttestationError,
    attest_manifest,
    fetch_comments,
    select_from_comments,
    validate,
    validate_manifest_attestation,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
PRODUCTION = "a" * 40
TOOLING = "b" * 40
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


def _attestation() -> dict:
    return {
        "schema": "opip-learning-recovery-attestation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_paths": PATHS,
        "path_digests": HASHES,
        "diff_sha256": DIGEST,
        "owner_login": "Opr1130",
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


@pytest.mark.acceptance
def test_exact_owner_attestation_is_accepted():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: external exact-pair approval."""
    checks = validate(
        attestation=_attestation(),
        compatibility=_compat(),
        owner_login="Opr1130",
        now=NOW,
    )
    assert [check for check in checks if not check["passed"]] == []
    manifest = attest_manifest(
        _compat(), _attestation(), owner_login="Opr1130", now=NOW
    )
    assert manifest["status"] == "PASS"
    assert manifest["attestation_sha256"]
    assert validate_manifest_attestation(manifest, now=NOW)


@pytest.mark.acceptance
def test_attestation_rejects_wrong_pair_path_digest_and_owner():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: no wildcard or stale approval."""
    wrong = _attestation()
    wrong["tooling_sha"] = "c" * 40
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    wrong = _attestation()
    wrong["changed_paths"] = PATHS[:1]
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    wrong = _attestation()
    wrong["diff_sha256"] = "d" * 64
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    assert any(
        not check["passed"]
        for check in validate(
            attestation=_attestation(),
            compatibility=_compat(),
            owner_login="someone-else",
            now=NOW,
        )
    )

@pytest.mark.acceptance
def test_attestation_rejects_stale_and_missing_ci():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: freshness and exact CI."""
    stale = _attestation()
    stale["approved_at_utc"] = "2026-01-01T00:00:00+00:00"
    assert any(
        not check["passed"]
        for check in validate(
            attestation=stale, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    no_ci = _attestation()
    no_ci["ci"]["pytest"] = "failure"
    assert any(
        not check["passed"]
        for check in validate(
            attestation=no_ci, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    for key in ("quality_security", "semgrep"):
        failed = _attestation()
        failed["ci"][key] = "failure"
        assert any(
            not check["passed"]
            for check in validate(
                attestation=failed, compatibility=_compat(), owner_login="Opr1130", now=NOW
            )
        )
        body = "<!-- opip-recovery-attestation:v1 " + json.dumps(failed) + " -->"
        with pytest.raises(AttestationError, match="no valid owner attestation"):
            select_from_comments(
                [
                    {
                        "user": {"login": "Opr1130"},
                        "created_at": "2026-10-22T00:59:00Z",
                        "body": body,
                        "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-3",
                    }
                ],
                compatibility=_compat(),
                owner_login="Opr1130",
                now=NOW,
            )


@pytest.mark.acceptance
def test_attested_manifest_without_attestation_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: PASS alone is insufficient."""
    manifest = _compat()
    manifest["status"] = "PASS"
    checks = validate_manifest_attestation(manifest, now=NOW)
    assert any(not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_comment_selection_uses_owner_and_latest_valid_attestation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: authenticated record source."""
    body_old = (
        "<!-- opip-recovery-attestation:v1 "
        + json.dumps({**_attestation(), "diff_sha256": "e" * 64})
        + " -->"
    )
    body_new = "<!-- opip-recovery-attestation:v1 " + json.dumps(_attestation()) + " -->"
    comments = [
        {
            "user": {"login": "Opr1130"},
            "created_at": "2026-10-22T00:58:00Z",
            "body": body_old,
            "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-1",
        },
        {
            "user": {"login": "Opr1130"},
            "created_at": "2026-10-22T00:59:00Z",
            "body": body_new,
            "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-2",
        },
        {
            "user": {"login": "attacker"},
            "created_at": "2026-10-22T00:59:30Z",
            "body": body_new,
            "html_url": "https://github.com/attacker/x",
        },
    ]
    manifest = select_from_comments(
        comments, compatibility=_compat(), owner_login="Opr1130", now=NOW
    )
    assert manifest["status"] == "PASS"
    assert manifest["attestation"]["record_identity"].endswith("issuecomment-2")


@pytest.mark.acceptance
def test_recovery_workflow_authenticates_issue64_attestation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: no in-commit self-reference."""
    workflow = (
        ROOT.parent / ".github/workflows/deploy-learning-recovery.yml"
    ).read_text(encoding="utf-8")
    assert "fetch-comments" in workflow
    assert "--issue 64" in workflow
    assert "learning_recovery_attestation select" in workflow
    assert "learning_recovery_compatibility verify" in workflow
    for workflow_name in ("pytest.yml", "quality-security.yml", "semgrep.yml"):
        assert workflow_name in workflow
    assert workflow.index("learning_recovery_attestation select") < workflow.index(
        "Upload exact tooling tree"
    )


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _paged_urlopen(pages, fail_page=None):
    calls = []

    def opener(request, timeout=45):
        calls.append(request.full_url)
        page = int(parse_qs(urlparse(request.full_url).query)["page"][0])
        if fail_page == page:
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)
        return _Response(pages[page - 1])

    return opener, calls


def _comment(login, body, created_at, number):
    return {
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
        "html_url": f"https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-{number}",
    }


@pytest.mark.acceptance
def test_paginates_more_than_600_comments_and_accepts_final_owner_attestation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: every page is admission-relevant."""
    valid_body = "<!-- opip-recovery-attestation:v1 " + json.dumps(_attestation()) + " -->"
    old_body = (
        "<!-- opip-recovery-attestation:v1 "
        + json.dumps({**_attestation(), "tooling_sha": "c" * 40})
        + " -->"
    )
    pages = []
    for page_number in range(1, 7):
        pages.append(
            [
                _comment(
                    "attacker" if index % 3 == 0 else "Opr1130",
                    "noise" if index % 4 else old_body,
                    "2026-10-22T00:58:00Z",
                    page_number * 100 + index,
                )
                for index in range(100)
            ]
        )
    pages.append(
        [
            _comment("attacker", "noise", "2026-10-22T00:58:30Z", 700 + index)
            for index in range(64)
        ]
        + [_comment("Opr1130", valid_body, "2026-10-22T00:59:00Z", 999)]
    )
    opener, calls = _paged_urlopen(pages)
    comments = fetch_comments(
        repo="Opr1130/OHM-Trade-Agent-v1",
        issue=64,
        token="token",
        urlopen=opener,
        per_page=100,
    )
    assert len(comments) == 665
    assert len(calls) == 7
    manifest = select_from_comments(
        comments, compatibility=_compat(), owner_login="Opr1130", now=NOW
    )
    assert manifest["status"] == "PASS"
    assert manifest["attestation"]["tooling_sha"] == TOOLING
    assert manifest["attestation"]["record_identity"].endswith("issuecomment-999")


@pytest.mark.acceptance
def test_pagination_failure_returns_no_partial_admission():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: API failure fails closed."""
    pages = [[_comment("Opr1130", "noise", "2026-10-22T00:58:00Z", index) for index in range(100)] for _ in range(4)]
    opener, _ = _paged_urlopen(pages, fail_page=3)
    with pytest.raises(AttestationError, match="pagination failed on page 3"):
        fetch_comments(
            repo="Opr1130/OHM-Trade-Agent-v1",
            issue=64,
            token="token",
            urlopen=opener,
            per_page=100,
        )


@pytest.mark.acceptance
def test_incomplete_pagination_refuses_partial_results():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: page cap is not silently partial."""
    pages = [[_comment("Opr1130", "noise", "2026-10-22T00:58:00Z", index) for index in range(100)] for _ in range(3)]
    opener, _ = _paged_urlopen(pages)
    with pytest.raises(AttestationError, match="exceeded 2 pages"):
        fetch_comments(
            repo="Opr1130/OHM-Trade-Agent-v1",
            issue=64,
            token="token",
            urlopen=opener,
            per_page=100,
            max_pages=2,
        )


@pytest.mark.acceptance
def test_pagination_rejects_malformed_pages_and_missing_token():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: malformed retrieval fails closed."""
    opener, _ = _paged_urlopen([{"message": "not a list"}])
    with pytest.raises(AttestationError, match="not a JSON array"):
        fetch_comments(
            repo="Opr1130/OHM-Trade-Agent-v1",
            issue=64,
            token="token",
            urlopen=opener,
            per_page=100,
        )
    with pytest.raises(AttestationError, match="token is required"):
        fetch_comments(
            repo="Opr1130/OHM-Trade-Agent-v1",
            issue=64,
            token="",
            urlopen=opener,
            per_page=100,
        )


@pytest.mark.acceptance
@pytest.mark.skipif(shutil.which("bash") is None, reason="workflow shell test requires bash")
def test_recovery_workflow_refuses_exact_sha_ci_failure(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: exact-SHA CI is enforced."""
    workflow_path = ROOT.parent / ".github/workflows/deploy-learning-recovery.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step = next(
        item
        for item in workflow["jobs"]["deploy"]["steps"]
        if item.get("name") == "Resolve tooling main and require exact-SHA CI"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "api" && "$2" == *"/commits/main" ]]; then
  printf '%s\\n' "$FAKE_MAIN_SHA"
  exit 0
fi
if [[ "$1" == "api" && "$2" == *"/compare/"* ]]; then
  printf 'ahead\\n'
  exit 0
fi
if [[ "$1" == "api" && "$2" == *"/actions/workflows/"* ]]; then
  path="${2%%\\?*}"
  workflow="${path##*/}"
  if [[ -n "${FAKE_FAIL_WORKFLOW:-}" && "$workflow" == "$FAKE_FAIL_WORKFLOW" ]]; then
    printf 'completed\\tfailure\\n'
  else
    printf 'completed\\tsuccess\\n'
  fi
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "GITHUB_REPOSITORY": "Opr1130/OHM-Trade-Agent-v1",
            "GITHUB_OUTPUT": str(output),
            "PRODUCTION_SHA": "a" * 40,
            "FAKE_MAIN_SHA": "b" * 40,
        }
    )
    success = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=ROOT.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    assert success.returncode == 0, success.stderr
    assert f"sha={'b' * 40}" in output.read_text(encoding="utf-8")
    for failed_workflow in ("pytest.yml", "quality-security.yml", "semgrep.yml"):
        env["FAKE_FAIL_WORKFLOW"] = failed_workflow
        failure = subprocess.run(
            ["bash", "-c", step["run"]],
            cwd=ROOT.parent,
            env=env,
            capture_output=True,
            text=True,
        )
        assert failure.returncode != 0
        assert f"{failed_workflow} is not successful" in failure.stderr
