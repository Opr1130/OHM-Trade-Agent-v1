"""External owner attestation for exact cross-SHA recovery compatibility.

The in-repo exception file cannot key an approval by the commit that contains
it: writing the approval changes the tooling SHA. This module accepts an
external, owner-authored attestation created after the tooling commit exists.
It binds the exact production/tooling pair, changed critical paths, per-path
hashes, the canonical diff digest, owner identity, evidence record, CI metadata
and a bounded approval timestamp. It never authorizes a wildcard or another
commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "opip-learning-recovery-attestation/v1"
MARKER = "<!-- opip-recovery-attestation:v1"
REVOCATION_SCHEMA = "opip-learning-recovery-revocation/v1"
REVOCATION_MARKER = "<!-- opip-recovery-revocation:v1"
PLAINTEXT_REVOCATION = re.compile(r"(?im)^\s*REVOKED:\s*do not proceed\s*\.?\s*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60



class AttestationError(RuntimeError):
    """The external attestation is missing, stale or not exact."""


def _canonical_digest(paths: Sequence[str], path_digests: Mapping[str, str]) -> str:
    lines = [f"{path}:{path_digests[path]}" for path in sorted(paths)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationError(f"invalid approved_at_utc: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate(
    *,
    attestation: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    owner_login: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    production = str(compatibility.get("production_sha") or "")
    tooling = str(compatibility.get("tooling_sha") or "")
    changed = sorted(str(path) for path in compatibility.get("changed_critical_paths") or [])
    hashes = compatibility.get("tooling_sha256") or {}
    add("attestation_schema", attestation.get("schema") == SCHEMA, str(attestation.get("schema")))
    add("attestation_production_sha", attestation.get("production_sha") == production, f"production={production}")
    add("attestation_tooling_sha", attestation.get("tooling_sha") == tooling, f"tooling={tooling}")
    add("attestation_owner", str(attestation.get("owner_login") or "") == owner_login, f"owner={owner_login}")
    add("attestation_rationale", len(str(attestation.get("rationale") or "").strip()) >= 20, "rationale")
    record = str(attestation.get("record_identity") or "")
    add(
        "attestation_record",
        record.startswith("https://github.com/") and "#issuecomment-" in record,
        record,
    )
    approved_at = None
    try:
        approved_at = _parse_utc(str(attestation.get("approved_at_utc") or ""))
        current = now or datetime.now(timezone.utc)
        age_ok = approved_at <= current + timedelta(minutes=5) and current - approved_at <= timedelta(
            seconds=max_age_seconds
        )
        add("attestation_fresh", age_ok, approved_at.isoformat())
    except AttestationError as exc:
        add("attestation_fresh", False, str(exc))
    ci = attestation.get("ci") or {}
    add(
        "attestation_ci",
        ci.get("commit_sha") == tooling
        and all(
            str(ci.get(key) or "") == "success"
            for key in ("pytest", "quality_security", "semgrep")
        ),
        json.dumps(ci, sort_keys=True),
    )
    claimed_paths = sorted(str(path) for path in attestation.get("changed_paths") or [])
    add("attestation_changed_paths", claimed_paths == changed, f"paths={claimed_paths}")
    claimed_hashes = attestation.get("path_digests") or {}
    add(
        "attestation_path_digests",
        isinstance(claimed_hashes, dict)
        and set(claimed_hashes) == set(changed)
        and all(claimed_hashes[path] == hashes.get(path) for path in changed),
        "per-path hashes",
    )
    expected_digest = _canonical_digest(changed, hashes) if changed else ""
    add(
        "attestation_diff_digest",
        attestation.get("diff_sha256") == expected_digest,
        f"digest={expected_digest}",
    )
    return checks


def attest_manifest(
    compatibility: Mapping[str, Any],
    attestation: Mapping[str, Any],
    *,
    owner_login: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    checks = validate(
        attestation=attestation,
        compatibility=compatibility,
        owner_login=owner_login,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    failures = [check["name"] for check in checks if not check["passed"]]
    if failures:
        raise AttestationError(f"attestation refused: {failures}")
    manifest = dict(compatibility)
    manifest["status"] = "PASS"
    manifest["attestation_required"] = True
    manifest["approved_paths"] = list(compatibility.get("changed_critical_paths") or [])
    manifest["unreviewed_changes"] = []
    manifest["exception_rationale"] = str(attestation.get("rationale") or "")
    manifest["attestation"] = dict(attestation)
    manifest["attestation_checks"] = checks
    manifest["attestation_sha256"] = hashlib.sha256(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


def fetch_comments(
    *,
    repo: str,
    issue: int,
    token: str,
    api_url: str = "https://api.github.com",
    per_page: int = 100,
    max_pages: int = 100,
    urlopen=urllib.request.urlopen,
) -> list[dict[str, Any]]:
    """Fetch every issue-comment page into one array.

    Any failed page aborts the entire retrieval; a partial list is never
    returned for admission. Author login, timestamps and comment URLs are
    preserved exactly as GitHub returned them.
    """
    if not token:
        raise AttestationError("GitHub token is required for pagination")
    if not 1 <= per_page <= 100:
        raise AttestationError("per_page must be between 1 and 100")
    if max_pages < 1:
        raise AttestationError("max_pages must be positive")
    comments: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = (
            f"{api_url.rstrip('/')}/repos/{repo}/issues/{issue}/comments"
            f"?per_page={per_page}&page={page}"
        )
        request = urllib.request.Request(url)
        request.add_header("Authorization", f"bearer {token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", "opip-recovery-attestation")
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AttestationError(
                f"GitHub comment pagination failed on page {page}: HTTP {exc.code}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise AttestationError(
                f"GitHub comment pagination failed on page {page}: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise AttestationError(
                f"GitHub comment page {page} is not a JSON array"
            )
        if not all(isinstance(item, dict) for item in payload):
            raise AttestationError(
                f"GitHub comment page {page} contains a non-object item"
            )
        comments.extend(payload)
        if len(payload) < per_page:
            return comments
    raise AttestationError(
        f"comment pagination exceeded {max_pages} pages; refusing partial results"
    )


def _attestation_from_comment(body: str) -> dict[str, Any] | None:
    if MARKER not in body:
        return None
    start = body.index(MARKER) + len(MARKER)
    end = body.find("-->", start)
    if end < 0:
        return None
    try:
        payload = json.loads(body[start:end].strip())
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class _AuthEvent:
    """One owner-authored authorization event, already scoped to the pair."""

    kind: str  # approval | approval_invalid | revocation | revocation_invalid
    when: datetime
    order: int | None
    payload: Mapping[str, Any] | None
    comment: Mapping[str, Any]
    reason: str


def _payload_between(body: str, marker: str) -> tuple[bool, dict[str, Any] | None]:
    """Return (marker_present, parsed_object_or_None)."""
    if marker not in body:
        return False, None
    start = body.index(marker) + len(marker)
    end = body.find("-->", start)
    if end < 0:
        return True, None
    try:
        value = json.loads(body[start:end].strip())
    except ValueError:
        return True, None
    return True, value if isinstance(value, dict) else None


def _scope(payload: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(payload, Mapping):
        return "", ""
    return (
        str(payload.get("production_sha") or ""),
        str(payload.get("tooling_sha") or ""),
    )


def _applies(production: str, tooling: str, pair_production: str, pair_tooling: str) -> bool:
    """An event with no explicit scope applies to the pair under evaluation."""
    if production and production != pair_production:
        return False
    if tooling and tooling != pair_tooling:
        return False
    return True


def _event_order(comment: Mapping[str, Any]) -> tuple[datetime, int | None]:
    when = _parse_utc(str(comment.get("created_at") or ""))
    raw = comment.get("id")
    if isinstance(raw, bool):
        return when, None
    if isinstance(raw, int):
        return when, raw
    if isinstance(raw, str) and raw.isdigit():
        return when, int(raw)
    return when, None


def authorization_events(
    comments: Sequence[Mapping[str, Any]],
    *,
    compatibility: Mapping[str, Any],
    owner_login: str,
) -> list[_AuthEvent]:
    """Classify owner-authored authorization events applicable to this exact pair.

    Non-owner comments can never authorize or revoke. Events explicitly scoped to
    another SHA pair are ignored. Anything relevant whose scope cannot be read is
    treated as applicable so the caller fails closed.
    """
    pair_production = str(compatibility.get("production_sha") or "")
    pair_tooling = str(compatibility.get("tooling_sha") or "")
    events: list[_AuthEvent] = []
    for comment in comments:
        if str((comment.get("user") or {}).get("login") or "") != owner_login:
            continue
        body = str(comment.get("body") or "")
        has_approval, approval = _payload_between(body, MARKER)
        has_revocation, revocation = _payload_between(body, REVOCATION_MARKER)
        if not has_approval and not has_revocation:
            if PLAINTEXT_REVOCATION.search(body):
                when, order = _event_order(comment)
                events.append(
                    _AuthEvent(
                        "revocation", when, order, None, comment, "plaintext owner revocation"
                    )
                )
            continue
        if has_revocation:
            production, tooling = _scope(revocation)
            if not _applies(production, tooling, pair_production, pair_tooling):
                continue
            when, order = _event_order(comment)
            if revocation is None:
                events.append(
                    _AuthEvent(
                        "revocation_invalid",
                        when,
                        order,
                        None,
                        comment,
                        "malformed revocation event",
                    )
                )
            else:
                events.append(
                    _AuthEvent(
                        "revocation",
                        when,
                        order,
                        revocation,
                        comment,
                        str(revocation.get("reason") or "owner revocation"),
                    )
                )
            continue
        production, tooling = _scope(approval)
        if approval is not None and not _applies(
            production, tooling, pair_production, pair_tooling
        ):
            continue
        when, order = _event_order(comment)
        if approval is None:
            events.append(
                _AuthEvent(
                    "approval_invalid",
                    when,
                    order,
                    None,
                    comment,
                    "malformed attestation event",
                )
            )
        else:
            events.append(_AuthEvent("approval", when, order, approval, comment, ""))
    return events


def _reject_ambiguous_ordering(events: Sequence[_AuthEvent]) -> None:
    for previous, current in zip(events, events[1:]):
        if previous.when != current.when:
            continue
        if (
            previous.order is None
            or current.order is None
            or previous.order == current.order
        ):
            raise AttestationError(
                "ambiguous authorization event ordering; refusing to resolve"
            )


def select_from_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    compatibility: Mapping[str, Any],
    owner_login: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Resolve the exact-pair authorization state from authenticated owner events.

    Events are applied chronologically with last-applicable-wins semantics: a later
    revocation always supersedes an earlier approval, a later invalid applicable
    event never silently falls back to an older approval, and a fresh valid
    re-approval after revocation restores authorization.
    """
    events = authorization_events(
        comments, compatibility=compatibility, owner_login=owner_login
    )
    if not events:
        marker_seen = any(
            str((comment.get("user") or {}).get("login") or "") == owner_login
            and (
                MARKER in str(comment.get("body") or "")
                or REVOCATION_MARKER in str(comment.get("body") or "")
            )
            for comment in comments
        )
        if marker_seen:
            raise AttestationError("no valid owner attestation for this exact pair")
        raise AttestationError("no owner-authored attestation comment found")
    events.sort(
        key=lambda event: (event.when, event.order if event.order is not None else -1)
    )
    _reject_ambiguous_ordering(events)

    state: tuple[str, Mapping[str, Any] | None, str] | None = None
    for event in events:
        if event.kind == "approval":
            payload = dict(event.payload or {})
            # The authenticated comment URL is authoritative; ignore any URL in the body.
            payload["record_identity"] = str(event.comment.get("html_url") or "")
            try:
                checks = validate(
                    attestation=payload,
                    compatibility=compatibility,
                    owner_login=owner_login,
                    now=now,
                    max_age_seconds=max_age_seconds,
                )
            except AttestationError as exc:
                state = ("invalid", None, str(exc))
                continue
            failures = [check["name"] for check in checks if not check["passed"]]
            if failures:
                state = ("invalid", None, f"failed checks {failures}")
            else:
                state = ("approved", payload, "")
        elif event.kind == "revocation":
            state = ("revoked", event.payload, event.reason)
        else:
            state = ("invalid", None, event.reason)

    if state is None:
        raise AttestationError("no valid owner attestation for this exact pair")
    kind, payload, detail = state
    if kind == "revoked":
        raise AttestationError(
            f"no valid owner attestation: authorization revoked ({detail})"
        )
    if kind == "invalid" or payload is None:
        raise AttestationError(
            "no valid owner attestation: latest applicable authorization event "
            f"is invalid ({detail})"
        )
    return attest_manifest(
        compatibility,
        payload,
        owner_login=owner_login,
        now=now,
        max_age_seconds=max_age_seconds,
    )


def validate_manifest_attestation(
    manifest: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> list[dict[str, Any]]:
    changed = list(manifest.get("changed_critical_paths") or [])
    if not changed:
        return []
    attestation = manifest.get("attestation")
    if not isinstance(attestation, dict):
        return [
            {
                "name": "attestation_present",
                "passed": False,
                "detail": "critical changes require external attestation",
            }
        ]
    checks = validate(
        attestation=attestation,
        compatibility=manifest,
        owner_login=str(attestation.get("owner_login") or ""),
        now=now,
        max_age_seconds=max_age_seconds,
    )
    expected = hashlib.sha256(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    checks.append(
        {
            "name": "attestation_digest",
            "passed": manifest.get("attestation_sha256") == expected,
            "detail": expected,
        }
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recovery compatibility attestation")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--attestation", required=True)
    validate_parser.add_argument("--compatibility", required=True)
    validate_parser.add_argument("--owner-login", required=True)
    validate_parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    validate_parser.add_argument("--out", required=True)

    select_parser = sub.add_parser("select")
    select_parser.add_argument("--comments", required=True)
    select_parser.add_argument("--compatibility", required=True)
    select_parser.add_argument("--owner-login", required=True)
    select_parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    select_parser.add_argument("--out", required=True)

    verify_parser = sub.add_parser("verify-manifest")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)

    fetch_parser = sub.add_parser("fetch-comments")
    fetch_parser.add_argument("--repo", required=True)
    fetch_parser.add_argument("--issue", type=int, required=True)
    fetch_parser.add_argument("--out", required=True)
    fetch_parser.add_argument("--api-url", default="https://api.github.com")
    fetch_parser.add_argument("--per-page", type=int, default=100)

    args = parser.parse_args(argv)
    if args.command == "fetch-comments":
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
        try:
            comments = fetch_comments(
                repo=args.repo,
                issue=args.issue,
                token=token,
                api_url=args.api_url,
                per_page=args.per_page,
            )
        except AttestationError as exc:
            print(f"attestation retrieval refused: {exc}", file=sys.stderr)
            return 2
        Path(args.out).write_text(
            json.dumps(comments, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"comments": len(comments), "out": args.out}))
        return 0
    try:
        if args.command == "validate":
            compatibility = json.loads(
                Path(args.compatibility).read_text(encoding="utf-8")
            )
            attestation = json.loads(Path(args.attestation).read_text(encoding="utf-8"))
            manifest = attest_manifest(
                compatibility,
                attestation,
                owner_login=args.owner_login,
                max_age_seconds=args.max_age_seconds,
            )
        elif args.command == "select":
            compatibility = json.loads(
                Path(args.compatibility).read_text(encoding="utf-8")
            )
            comments = json.loads(Path(args.comments).read_text(encoding="utf-8"))
            manifest = select_from_comments(
                comments,
                compatibility=compatibility,
                owner_login=args.owner_login,
                max_age_seconds=args.max_age_seconds,
            )
        else:
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            checks = validate_manifest_attestation(
                manifest, max_age_seconds=args.max_age_seconds
            )
            failures = [check for check in checks if not check["passed"]]
            print(json.dumps({"checks": checks, "failures": failures}, indent=2))
            return 0 if not failures else 2
    except AttestationError as exc:
        print(f"attestation refused: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "PASS", "out": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
