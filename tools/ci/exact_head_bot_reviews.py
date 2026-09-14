#!/usr/bin/env python3
"""Exact-HEAD Codex / CodeRabbit review presence gate (governance CI).

Fail closed when either bot has not left a completed review whose ``commit_id``
exactly matches the current PR HEAD SHA, or when identity/format evidence is
ambiguous.

This gate does **not** certify that substantive findings are resolved or that
the PR is merge-clean. Human judgment remains required for finding triage.
Review *request* issue comments (e.g. ``@codex review``) never count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

CODEX_LOGINS = frozenset({"chatgpt-codex-connector[bot]"})
CODERABBIT_LOGINS = frozenset({"coderabbitai[bot]"})

# Completed review states that count as evidence. PENDING / DISMISSED do not.
COMPLETED_REVIEW_STATES = frozenset({"COMMENTED", "APPROVED", "CHANGES_REQUESTED"})

CODEX_REVIEWED_COMMIT_RE = re.compile(
    r"\*\*Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BotReviewEvidence:
    login: str
    state: str
    commit_id: str
    submitted_at: str | None
    body: str


@dataclass
class GateResult:
    ok: bool
    head_sha: str
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    codex: BotReviewEvidence | None = None
    coderabbit: BotReviewEvidence | None = None

    @property
    def findings_disposition(self) -> str:
        """Presence pass never implies findings are clean."""
        return "HUMAN_TRIAGE_REQUIRED"


def _normalize_sha(value: str) -> str:
    sha = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"head SHA must be a full 40-char hex digest, got {value!r}")
    return sha


def _as_review(raw: Mapping[str, Any]) -> BotReviewEvidence | None:
    user = raw.get("user") or {}
    login = str(user.get("login") or "").strip()
    state = str(raw.get("state") or "").strip().upper()
    commit_id = str(raw.get("commit_id") or "").strip().lower()
    body = str(raw.get("body") or "")
    if not login or not commit_id:
        return None
    return BotReviewEvidence(
        login=login,
        state=state,
        commit_id=commit_id,
        submitted_at=(str(raw["submitted_at"]) if raw.get("submitted_at") else None),
        body=body,
    )


def _matches_login(login: str, allowed: frozenset[str]) -> bool:
    return login in allowed


def _codex_format_ok(evidence: BotReviewEvidence, head_sha: str) -> list[str]:
    errors: list[str] = []
    if not evidence.body.strip():
        errors.append("Codex review body is empty (ambiguous format)")
        return errors
    match = CODEX_REVIEWED_COMMIT_RE.search(evidence.body)
    if match is None:
        errors.append(
            "Codex review body missing '**Reviewed commit:** `<sha>`' marker"
        )
        return errors
    claimed = match.group(1).lower()
    if not head_sha.startswith(claimed):
        errors.append(
            f"Codex Reviewed commit `{claimed}` does not match HEAD `{head_sha}`"
        )
    return errors


def _coderabbit_format_ok(evidence: BotReviewEvidence) -> list[str]:
    errors: list[str] = []
    body = evidence.body.strip()
    if not body:
        errors.append("CodeRabbit review body is empty (ambiguous format)")
        return errors
    # Observed formats include actionable / duplicate / nitpick summaries.
    markers = (
        "actionable comments",
        "duplicate comments",
        "nitpick comments",
        "review details",
        "walkthrough",
    )
    lower = body.lower()
    if not any(marker in lower for marker in markers):
        errors.append(
            "CodeRabbit review body lacks a recognized review-summary marker "
            f"(expected one of: {', '.join(markers)})"
        )
    return errors


def select_exact_head_reviews(
    reviews: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
) -> GateResult:
    """Evaluate PR review payloads for exact-HEAD Codex and CodeRabbit evidence."""
    head = _normalize_sha(head_sha)
    result = GateResult(ok=True, head_sha=head)
    result.notices.append(
        "PASS/FAIL here only concerns exact-HEAD review *presence* and "
        "identity/format. It does NOT certify that substantive findings are "
        "resolved or that the PR is merge-clean. "
        f"Findings disposition: {result.findings_disposition}."
    )

    codex_hits: list[BotReviewEvidence] = []
    rabbit_hits: list[BotReviewEvidence] = []
    ambiguous_logins: list[str] = []

    for raw in reviews:
        evidence = _as_review(raw)
        if evidence is None:
            continue
        if evidence.state not in COMPLETED_REVIEW_STATES:
            continue
        if evidence.commit_id != head:
            continue
        login = evidence.login
        if _matches_login(login, CODEX_LOGINS):
            codex_hits.append(evidence)
        elif _matches_login(login, CODERABBIT_LOGINS):
            rabbit_hits.append(evidence)
        elif "codex" in login.lower() or "coderabbit" in login.lower():
            ambiguous_logins.append(login)

    if ambiguous_logins:
        result.ok = False
        result.errors.append(
            "Ambiguous bot login(s) on exact HEAD (refusing lookalikes): "
            + ", ".join(sorted(set(ambiguous_logins)))
        )

    if not codex_hits:
        result.ok = False
        result.errors.append(
            "Missing completed Codex review on exact HEAD "
            f"(expected login in {sorted(CODEX_LOGINS)}, state in "
            f"{sorted(COMPLETED_REVIEW_STATES)}, commit_id={head})"
        )
    else:
        # Prefer the latest submission if multiple completed reviews exist.
        codex_hits.sort(key=lambda item: item.submitted_at or "")
        result.codex = codex_hits[-1]
        result.errors.extend(_codex_format_ok(result.codex, head))

    if not rabbit_hits:
        result.ok = False
        result.errors.append(
            "Missing completed CodeRabbit review on exact HEAD "
            f"(expected login in {sorted(CODERABBIT_LOGINS)}, state in "
            f"{sorted(COMPLETED_REVIEW_STATES)}, commit_id={head})"
        )
    else:
        rabbit_hits.sort(key=lambda item: item.submitted_at or "")
        result.coderabbit = rabbit_hits[-1]
        result.errors.extend(_coderabbit_format_ok(result.coderabbit))

    if result.errors:
        result.ok = False
    return result


def fetch_pull_request_reviews(
    *,
    repo: str,
    pull_number: int,
    token: str,
    api_url: str = "https://api.github.com",
) -> list[dict[str, Any]]:
    """Fetch all PR reviews (paginated). Fail closed on HTTP/API errors."""
    if not repo or "/" not in repo:
        raise ValueError("repo must be 'owner/name'")
    if pull_number < 1:
        raise ValueError("pull_number must be >= 1")
    if not token.strip():
        raise ValueError("GitHub token is required")

    reviews: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"{api_url.rstrip('/')}/repos/{repo}/pulls/{pull_number}/reviews"
            f"?per_page=100&page={page}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "opip-exact-head-bot-reviews",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                chunk = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub reviews API failed ({exc.code}): {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub reviews API unreachable: {exc}") from exc
        if not isinstance(chunk, list):
            raise RuntimeError("GitHub reviews API returned a non-list payload")
        reviews.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        if page > 50:
            raise RuntimeError("GitHub reviews pagination exceeded safety limit")
    return reviews


def evaluate_from_env() -> GateResult:
    head = os.environ.get("PR_HEAD_SHA") or os.environ.get("GITHUB_SHA")
    if not head:
        raise SystemExit("PR_HEAD_SHA (or GITHUB_SHA) is required")
    repo = os.environ.get("GITHUB_REPOSITORY")
    pull = os.environ.get("PR_NUMBER")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo or not pull or not token:
        raise SystemExit(
            "GITHUB_REPOSITORY, PR_NUMBER, and GITHUB_TOKEN are required"
        )
    reviews = fetch_pull_request_reviews(
        repo=repo,
        pull_number=int(pull),
        token=token,
    )
    return select_exact_head_reviews(reviews, head_sha=head)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reviews-json",
        help="Optional path to a reviews API JSON fixture (skips network)",
    )
    parser.add_argument("--head-sha", help="Exact PR HEAD SHA (40-char)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.reviews_json:
        if not args.head_sha:
            raise SystemExit("--head-sha is required with --reviews-json")
        path = Path(args.reviews_json)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("reviews JSON must be a list")
        result = select_exact_head_reviews(payload, head_sha=args.head_sha)
    else:
        result = evaluate_from_env()

    for notice in result.notices:
        print(f"NOTICE: {notice}")
    if result.codex:
        print(
            f"CODEX: login={result.codex.login} state={result.codex.state} "
            f"commit={result.codex.commit_id} at={result.codex.submitted_at}"
        )
    if result.coderabbit:
        print(
            f"CODERABBIT: login={result.coderabbit.login} "
            f"state={result.coderabbit.state} commit={result.coderabbit.commit_id} "
            f"at={result.coderabbit.submitted_at}"
        )
    print(f"FINDINGS_DISPOSITION: {result.findings_disposition}")
    if result.ok:
        print("RESULT: exact-head bot reviews present")
        return 0
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print("RESULT: exact-head bot reviews FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
