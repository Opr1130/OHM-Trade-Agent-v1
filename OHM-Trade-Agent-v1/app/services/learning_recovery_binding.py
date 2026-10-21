"""Recovery-only binding between production release identity and worker tooling.

Normal learning deployment requires ``target SHA == current main``. That is the
correct contract for ordinary operation, but it cannot express a recovery where
the healthy production core legitimately runs an earlier SHA than the merged
recovery tooling.

This module does not weaken that workflow. It defines a separate, owner-gated
binding with four distinct identities:

* ``production_sha``  - the authenticated SHA published by the production
  exporter (never a caller-supplied claim);
* ``tooling_sha``     - the exact merged commit whose worker image is built;
* ``current_main_sha``- the repository main at dispatch, which tooling must equal;
* ``manifest_sha``    - the SHA read from the authenticated export manifest.

It fails closed unless production is an ancestor of the tooling (``ahead`` or
``identical`` in the GitHub compare API), the manifest matches production,
tooling is current main with successful exact-SHA CI, and learning timers are
already disabled.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_COMPARE = {"ahead", "identical"}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def evaluate(
    *,
    production_sha: str,
    tooling_sha: str,
    current_main_sha: str,
    manifest_sha: str,
    compare_status: str,
    tooling_ci_success: bool,
    timers_enabled: bool,
) -> tuple[Check, ...]:
    checks: list[Check] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(Check(name=name, passed=bool(passed), detail=detail))

    add(
        "production_sha_well_formed",
        bool(SHA_RE.match(production_sha or "")),
        f"production={production_sha}",
    )
    add(
        "tooling_sha_well_formed",
        bool(SHA_RE.match(tooling_sha or "")),
        f"tooling={tooling_sha}",
    )
    add(
        "identities_are_distinct",
        bool(production_sha) and production_sha != tooling_sha,
        "normal deploy applies when equal",
    )
    add(
        "tooling_equals_current_main",
        bool(tooling_sha) and tooling_sha == current_main_sha,
        f"tooling={tooling_sha} main={current_main_sha}",
    )
    add(
        "tooling_exact_sha_ci_success",
        tooling_ci_success is True,
        f"ci_success={tooling_ci_success}",
    )
    add(
        "production_is_ancestor_of_tooling",
        compare_status in ALLOWED_COMPARE,
        f"compare={compare_status}",
    )
    add(
        "manifest_binds_production",
        bool(manifest_sha) and manifest_sha == production_sha,
        f"manifest={manifest_sha} production={production_sha}",
    )
    add(
        "timers_were_disabled_before_recovery",
        timers_enabled is False,
        f"timers_enabled={timers_enabled}",
    )
    return tuple(checks)


def failed(checks: tuple[Check, ...]) -> list[Check]:
    return [check for check in checks if not check.passed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bind recovery tooling to production")
    parser.add_argument("--production-sha", required=True)
    parser.add_argument("--tooling-sha", required=True)
    parser.add_argument("--current-main-sha", required=True)
    parser.add_argument("--manifest-sha", required=True)
    parser.add_argument("--compare-status", required=True)
    parser.add_argument("--tooling-ci-success", required=True)
    parser.add_argument("--timers-enabled", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    checks = evaluate(
        production_sha=args.production_sha.strip(),
        tooling_sha=args.tooling_sha.strip(),
        current_main_sha=args.current_main_sha.strip(),
        manifest_sha=args.manifest_sha.strip(),
        compare_status=args.compare_status.strip().lower(),
        tooling_ci_success=args.tooling_ci_success.strip().lower() == "true",
        timers_enabled=args.timers_enabled.strip().lower() == "true",
    )
    failures = failed(checks)
    report = {
        "status": "PASS" if not failures else "REFUSED",
        "checks": [asdict(check) for check in checks],
        "failures": [check.name for check in failures],
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} {check.name} {check.detail}")
        print(f"RESULT={report['status']}")
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
