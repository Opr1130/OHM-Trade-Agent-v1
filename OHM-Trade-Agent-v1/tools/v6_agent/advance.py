"""Pick the next v6 slice to build after a v6 PR merges (one slice at a time)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

QUEUE_LABEL = "v6-queue"
BUILD_LABEL = "build"
DONE_LABEL = "built"
_DEPENDS_RE = re.compile(r"###\s*Depends on\s*\n+(.*?)(?:\n###|\Z)", re.S | re.I)


def dependencies(body: str | None) -> list[int]:
    match = _DEPENDS_RE.search(body or "")
    return [int(n) for n in re.findall(r"#(\d+)", match.group(1))] if match else []


def pick_next(
    issues: list[dict[str, Any]],
    open_v6_prs: int,
    closed_numbers: set[int],
) -> int | None:
    """Lowest-numbered queued issue whose dependencies are closed; none while a v6 PR is open."""
    if open_v6_prs:
        return None
    for issue in sorted(issues, key=lambda i: i["number"]):
        if "pull_request" in issue:
            continue
        labels = {str(label["name"]) for label in issue.get("labels", [])}
        if QUEUE_LABEL not in labels or labels & {BUILD_LABEL, DONE_LABEL}:
            continue
        if all(dep in closed_numbers for dep in dependencies(issue.get("body"))):
            return int(issue["number"])
    return None


def _gh(*args: str) -> Any:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def main() -> int:
    repo = os.environ["GH_REPOSITORY"]
    open_issues = _gh("api", f"repos/{repo}/issues?state=open&labels={QUEUE_LABEL}&per_page=100")
    closed = _gh("api", f"repos/{repo}/issues?state=closed&per_page=100") or []
    prs = _gh("api", f"repos/{repo}/pulls?state=open&per_page=100") or []
    merged = os.environ.get("MERGED_PR", "")
    v6_prs = [
        p
        for p in prs
        if str(p["head"]["ref"]).startswith("v6/") and str(p["number"]) != merged
    ]
    print(
        f"queued={len(open_issues or [])} closed={len(closed)} "
        f"open_v6_prs={[p['number'] for p in v6_prs]} merged={merged!r}"
    )
    number = pick_next(open_issues or [], len(v6_prs), {int(i["number"]) for i in closed})
    if number is None:
        print("no slice ready")
        return 0
    subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{number}/labels", "-f", f"labels[]={BUILD_LABEL}"],
        check=True,
    )
    print(f"queued slice #{number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
