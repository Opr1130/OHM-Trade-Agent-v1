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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "opip-learning-recovery-attestation/v1"
MARKER = "<!-- opip-recovery-attestation:v1"
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
        age_ok = (now or datetime.now(timezone.utc)) - approved_at <= timedelta(
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


def select_from_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    compatibility: Mapping[str, Any],
    owner_login: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    candidates: list[tuple[str, Mapping[str, Any], dict[str, Any]]] = []
    for comment in comments:
        if str((comment.get("user") or {}).get("login") or "") != owner_login:
            continue
        payload = _attestation_from_comment(str(comment.get("body") or ""))
        if payload is None:
            continue
        candidates.append(
            (str(comment.get("created_at") or ""), comment, payload)
        )
    if not candidates:
        raise AttestationError("no owner-authored attestation comment found")
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, comment, payload in candidates:
        payload = dict(payload)
        # The authenticated comment URL is authoritative; ignore a URL embedded in the body.
        payload["record_identity"] = str(comment.get("html_url") or "")
        try:
            return attest_manifest(
                compatibility,
                payload,
                owner_login=owner_login,
                now=now,
                max_age_seconds=max_age_seconds,
            )
        except AttestationError:
            continue
    raise AttestationError("no valid owner attestation for this exact pair")


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

    args = parser.parse_args(argv)
    compatibility = json.loads(Path(args.compatibility).read_text(encoding="utf-8"))
    try:
        if args.command == "validate":
            attestation = json.loads(Path(args.attestation).read_text(encoding="utf-8"))
            manifest = attest_manifest(
                compatibility,
                attestation,
                owner_login=args.owner_login,
                max_age_seconds=args.max_age_seconds,
            )
        elif args.command == "select":
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
